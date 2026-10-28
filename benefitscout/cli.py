"""Command line: python -m benefitscout "describe yourself"  (prints events, saves the run)."""
from __future__ import annotations

import json
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from . import pipeline, store  # noqa: E402


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    text = " ".join(argv)
    t0 = time.time()

    def emit(kind, payload):
        if kind in ("log", "stage"):
            print(f"[{time.time()-t0:5.1f}s] {kind}: {payload}")
        elif kind == "search":
            print(f"[{time.time()-t0:5.1f}s] search: {payload['query']!r} -> {payload['n']}")
        elif kind == "crawl":
            print(f"[{time.time()-t0:5.1f}s] crawl: {payload['url']} -> {payload['n']} pages")
        elif kind == "candidates":
            for c in payload:
                print(f"    cand {c['relevance']:.2f} {c['name']}  {c['url']}")
        elif kind == "rules":
            print(f"[{time.time()-t0:5.1f}s] rules: {'ok ' if payload['ok'] else 'DROP'} {payload['n_criteria']:2d} crit  {payload['name']}")
        elif kind == "verdict":
            print(f"[{time.time()-t0:5.1f}s] verdict: {payload['verdict']:13s} {payload['confidence']}  {payload['name']}")
        elif kind == "profile":
            print(f"[{time.time()-t0:5.1f}s] profile: {payload.get('country')}, {payload.get('region')}, lang={payload.get('language')}")
        elif kind == "plan":
            print(f"[{time.time()-t0:5.1f}s] plan: domains={payload['official_domains']} portal={payload['portal'].get('url')}")
            for q in payload["queries"]:
                print(f"    q: {q}")

    res = pipeline.run(text, emit)
    store.save(res)
    print("\n=== RESULT", res["id"])
    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
    for s in res["schemes"]:
        print(f"- {s['verdict']:13s} {s.get('confidence')}  {s['name']}  [{s.get('deadline')}]")
        print(f"      {s.get('why')}")
    print("costs:", json.dumps(res["costs"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
