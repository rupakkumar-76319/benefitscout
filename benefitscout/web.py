"""Tavily, with a disk cache and exact credit accounting.

BenefitScout uses four Tavily endpoints at runtime:
  search  - find candidate schemes, preferring official (.gov) domains
  extract - pull the full text of each scheme page so rules are quoted, not guessed
  crawl   - walk the main official portal for the user's country with natural-language instructions
  map     - list the pages behind a portal when the user asks for a deep scan
Every call asks Tavily for its own usage so the UI can show the real credits spent.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import requests
from tavily import TavilyClient

CACHE_DIR = Path(os.environ.get("BS_DATA_DIR", "data")) / "tavily_cache"
CACHE_ON = os.environ.get("BS_WEB_CACHE", "1") == "1"


@dataclass
class WebLedger:
    calls: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, kind: str, detail: str, credits: float, cached: bool, n: int):
        with self.lock:
            self.calls.append({"kind": kind, "detail": detail[:160], "credits": credits, "cached": cached, "results": n})

    def summary(self) -> dict:
        by: dict = {}
        for c in self.calls:
            b = by.setdefault(c["kind"], {"calls": 0, "credits": 0.0, "results": 0})
            b["calls"] += 1
            b["credits"] += c["credits"]
            b["results"] += c["results"]
        return {"by_endpoint": by, "credits": round(sum(c["credits"] for c in self.calls), 2), "calls": len(self.calls)}


_tv: TavilyClient | None = None


def tv() -> TavilyClient:
    global _tv
    if _tv is None:
        _tv = TavilyClient(os.environ["TAVILY_API_KEY"])
    return _tv


def _cached(kind: str, payload: dict):
    key = hashlib.sha256(json.dumps([kind, payload], sort_keys=True).encode()).hexdigest()[:32]
    p = CACHE_DIR / f"{kind}_{key}.json"
    return p, (json.loads(p.read_text(encoding="utf-8")) if CACHE_ON and p.exists() else None)


def _store(p: Path, data: dict):
    if CACHE_ON:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")


def _credits(resp: dict, fallback: float) -> float:
    """Credits as reported by Tavily; estimated when the response carries no usable figure."""
    u = resp.get("usage") or {}
    c = u.get("credits")
    if c is None or (float(c) == 0 and resp.get("results")):
        return fallback if resp.get("results") else 0.0
    return float(c)


def search(query: str, ledger: WebLedger, include_domains: list[str] | None = None, prefer: bool = True,
           max_results: int = 8, depth: str = "basic") -> list[dict]:
    payload = {"query": query, "include_domains": include_domains or [], "prefer": prefer,
               "max_results": max_results, "depth": depth}
    p, hit = _cached("search", payload)
    if hit is None:
        kw: dict = {"query": query, "max_results": max_results, "search_depth": depth, "include_usage": True}
        if include_domains:
            kw["include_domains"] = include_domains
            kw["include_domains_mode"] = "prefer" if prefer else "restrict"
        hit = tv().search(**kw)
        _store(p, hit)
        cached = False
    else:
        cached = True
    res = [{"url": r["url"], "title": r.get("title", ""), "content": (r.get("content") or "")[:1500],
            "score": r.get("score", 0)} for r in hit.get("results", [])]
    ledger.add("search", query, _credits(hit, 1 if depth == "basic" else 2), cached, len(res))
    return res


def extract(urls: list[str], ledger: WebLedger) -> dict[str, str]:
    """url -> markdown text. Batches of 5 (one basic extract credit each)."""
    out: dict[str, str] = {}
    for i in range(0, len(urls), 5):
        batch = urls[i:i + 5]
        p, hit = _cached("extract", {"urls": batch})
        if hit is None:
            hit = tv().extract(urls=batch, format="markdown", include_usage=True, timeout=45)
            _store(p, hit)
            cached = False
        else:
            cached = True
        for r in hit.get("results", []):
            out[r["url"]] = r.get("raw_content") or ""
        ledger.add("extract", ", ".join(batch), _credits(hit, 1), cached, len(hit.get("results", [])))
    return out


def crawl(url: str, instructions: str, ledger: WebLedger, limit: int = 15, depth: int = 2) -> list[dict]:
    payload = {"url": url, "instructions": instructions, "limit": limit, "depth": depth}
    p, hit = _cached("crawl", payload)
    if hit is None:
        hit = tv().crawl(url=url, instructions=instructions, limit=limit, max_depth=depth, max_breadth=20,
                         format="markdown", include_usage=True, timeout=150)
        _store(p, hit)
        cached = False
    else:
        cached = True
    res = []
    for r in hit.get("results", []):
        raw = r.get("raw_content") or ""
        res.append({"url": r["url"], "title": raw[:80].strip().split("\n")[0], "content": raw})
    ledger.add("crawl", f"{url} :: {instructions}", _credits(hit, max(1, len(res) // 5 + 1)), cached, len(res))
    return res


def site_map(url: str, instructions: str, ledger: WebLedger, limit: int = 40) -> list[str]:
    payload = {"url": url, "instructions": instructions, "limit": limit}
    p, hit = _cached("map", payload)
    if hit is None:
        hit = tv().map(url=url, instructions=instructions, limit=limit, max_depth=2, include_usage=True, timeout=120)
        _store(p, hit)
        cached = False
    else:
        cached = True
    urls = list(hit.get("results", []))
    ledger.add("map", f"{url} :: {instructions}", _credits(hit, 1), cached, len(urls))
    return urls


def account_usage() -> dict:
    """Live plan usage from Tavily (credits used / limit this month)."""
    try:
        r = requests.get("https://api.tavily.com/usage",
                         headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"}, timeout=10)
        a = r.json().get("account", {})
        return {"used": a.get("plan_usage"), "limit": a.get("plan_limit"), "plan": a.get("current_plan")}
    except Exception:  # noqa: BLE001
        return {}
