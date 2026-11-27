#!/usr/bin/env bash
# Builds sim/site/probepen.wasm: the firmware's user interface (firmware/src, all but main.c) plus the
# simulated board (sim/sim.c), for the web simulator. Needs clang, lld, wasi-libc and the wasm32
# compiler-rt (Ubuntu: apt install clang lld wasi-libc libclang-rt-dev-wasm32).
# Also writes sim/site/build.txt, a hash of the sources, so tests/test_sim.py notices a stale build.
set -euo pipefail
cd "$(dirname "$0")/.."

CC=${CC:-clang}
# WASI_SYSROOT: a sysroot holding include/wasm32-wasi and lib/wasm32-wasi, when they are not in /usr
SYSROOT=()
[ -n "${WASI_SYSROOT:-}" ] && SYSROOT=(--sysroot="$WASI_SYSROOT")
srcs=(sim/sim.c)
for f in firmware/src/*.c; do
    [ "$(basename "$f")" = main.c ] || srcs+=("$f")
done

"$CC" --target=wasm32-wasi "${SYSROOT[@]}" -std=c11 -O2 -D_DEFAULT_SOURCE -fvisibility=hidden -Ifirmware/include \
    -nostartfiles -Wl,--no-entry -Wl,--export-dynamic -Wl,--strip-all \
    -o sim/site/probepen.wasm "${srcs[@]}" -lm

python3 - "${srcs[@]}" firmware/include/*.h <<'EOF'
import hashlib, sys
h = hashlib.sha256()
for p in sorted(sys.argv[1:]):
    h.update(p.encode() + b"\0" + open(p, "rb").read())
open("sim/site/build.txt", "w").write(f"sources {h.hexdigest()}\n")
EOF
ls -l sim/site/probepen.wasm
