"""The web simulator with AI mode, on this computer only.

    python3 host/ai_server.py [--port 8765] [--provider gemini|anthropic|rules] [--model NAME] [--key-file FILE]

Serves sim/site at http://127.0.0.1:PORT/ and answers the page's AI questions at /ai/plan with
host/ai_view.py. The API key comes from GEMINI_API_KEY / ANTHROPIC_API_KEY or from --key-file (a JSON file
with {"keys": {"gemini": "...", "anthropic": "..."}}); it stays in this process and is never sent to the
page. The server listens on 127.0.0.1 only. The published simulator (GitHub Pages) has no /ai: there, AI mode
says it needs this local server.
"""
from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_view  # noqa: E402

SITE = Path(__file__).resolve().parents[1] / "sim" / "site"


class Handler(SimpleHTTPRequestHandler):
    cfg: dict = {}

    def log_message(self, fmt, *args):   # quieter: only the AI questions
        if "/ai/" in (str(args[0]) if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    def _json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        self.send_header("cache-control", "no-store")   # always the freshly built wasm
        super().end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/ai/status":
            c = self.cfg
            self._json(200, {"ready": True, "provider": c["provider"] if c["key"] else "rules",
                             "model": c["model"] if c["key"] else "", "has_key": bool(c["key"])})
            return
        super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] != "/ai/plan":
            self._json(404, {"error": "not here"})
            return
        try:
            n = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(min(n, 4_000_000)) or b"{}")
            snaps = body.get("snapshots") or []
            if not snaps:
                raise ValueError("no snapshots")
            c = self.cfg
            with open("/tmp/probepen-ai-last.json", "w") as f:   # the last question, to replay when tuning
                json.dump(snaps, f)
            out = ai_view.ai_plan(snaps, c["provider"], c["key"], c["model"])
            out.pop("analysis", None)
            sys.stderr.write(f"AI: {out['source']} -> {out['plan'].get('view')}: {out['plan'].get('title')}"
                             f"{'  (' + out['note'] + ')' if out['note'] else ''}\n")
            self._json(200, out)
        except Exception as e:
            self._json(400, {"error": str(e)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--provider", default="gemini", choices=["gemini", "anthropic", "rules"])
    ap.add_argument("--model", default="")
    ap.add_argument("--key-file", default=None)
    a = ap.parse_args()
    key = "" if a.provider == "rules" else ai_view.load_key(a.provider, a.key_file)
    Handler.cfg = {"provider": a.provider, "model": a.model or ai_view.DEFAULT_MODELS.get(a.provider, ""), "key": key}
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), partial(Handler, directory=str(SITE)))
    who = f"{a.provider} {Handler.cfg['model']}" if key else "the rules (no API key)"
    print(f"ProbePen-AI simulator with AI mode: http://127.0.0.1:{a.port}/  (AI: {who})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
