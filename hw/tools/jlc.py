"""Tiny client for JLCPCB's public parts search (stock, library type, price tiers, datasheet)."""
from __future__ import annotations

import json
import sys
import urllib.request
from functools import lru_cache

URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"


def _search(keyword: str, n: int = 10) -> list[dict]:
    body = json.dumps({"keyword": keyword, "currentPage": 1, "pageSize": n}).encode()
    req = urllib.request.Request(
        URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    return ((d.get("data") or {}).get("componentPageInfo") or {}).get("list") or []


def _norm(c: dict) -> dict:
    prices = sorted((p["startNumber"], float(p["productPrice"])) for p in (c.get("componentPrices") or []))
    return {
        "lcsc": (c.get("urlSuffix") or "").split("/")[-1],
        "stock": int(c.get("stockCount") or 0),
        "lib": c.get("componentLibraryType") or "",  # "base" = Basic, "expand" = Extended
        "preferred": bool(c.get("preferredComponentFlag")),
        "prices": prices,
        "datasheet": c.get("dataManualUrl") or c.get("dataManualOfficialLink") or "",
        "describe": c.get("describe") or c.get("componentSpecificationEn") or "",
        "model": c.get("componentModelEn") or "",
        "package": c.get("componentSpecificationEn") or "",
    }


@lru_cache(maxsize=None)
def lookup(lcsc: str) -> dict:
    """Exact LCSC number -> {stock, lib, prices[(qty, price)], datasheet, describe, model, package}."""
    for c in _search(lcsc, 10):
        if (c.get("urlSuffix") or "").endswith("/" + lcsc):
            return _norm(c)
    raise KeyError(f"{lcsc} not found at JLC")


def search(keyword: str, n: int = 10) -> list[dict]:
    return [_norm(c) for c in _search(keyword, n)]


def price_at(prices: list[tuple[int, float]], qty: int) -> float:
    """Unit price for buying `qty`: the highest tier whose start quantity is <= qty."""
    best = prices[0][1]
    for start, price in sorted(prices):
        if start <= qty:
            best = price
    return best


if __name__ == "__main__":
    for kw in sys.argv[1:]:
        for r in search(kw, 8):
            print(
                f"{r['lcsc']:10} {r['lib']:6} {r['stock']:>8} {r['prices'][:1]} "
                f"{r['model'][:28]:28} {r['package'][:18]:18} {r['describe'][:70]}"
            )
