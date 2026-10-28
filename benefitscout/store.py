"""Saved runs and watched profiles: plain JSON files, no database to set up."""
from __future__ import annotations

import json
import os
from pathlib import Path

RUNS = Path(os.environ.get("BS_DATA_DIR", "data")) / "runs"
WATCH = Path(os.environ.get("BS_DATA_DIR", "data")) / "watch.json"


def save(run: dict) -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{run['id']}.json").write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")


def load(run_id: str) -> dict | None:
    p = RUNS / f"{run_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def list_runs(limit: int = 50) -> list[dict]:
    if not RUNS.exists():
        return []
    files = sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for p in files:
        r = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": r["id"], "created": r["created"], "text": r["text"][:140], "counts": r.get("counts", {}),
                    "country": (r.get("profile") or {}).get("country"), "watched": r["id"] in watched(),
                    "has_changes": bool(r.get("changes"))})
    return out


def watched() -> list[str]:
    return json.loads(WATCH.read_text(encoding="utf-8")) if WATCH.exists() else []


def set_watch(run_id: str, on: bool) -> list[str]:
    w = set(watched())
    (w.add if on else w.discard)(run_id)
    WATCH.parent.mkdir(parents=True, exist_ok=True)
    WATCH.write_text(json.dumps(sorted(w)), encoding="utf-8")
    return sorted(w)
