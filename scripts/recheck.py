"""The workflow that runs itself: re-check every watched profile and record what changed.

Run it on a schedule (cron, a Nebius Serverless Job, or `--loop` to poll in-process):

    python scripts/recheck.py            # one pass over all watched profiles
    python scripts/recheck.py --loop 24  # every 24 hours, forever

Each pass re-runs the search for a watched profile, diffs it against the last saved run
(new schemes, schemes gone, changed verdicts, changed deadlines) and saves the new run with a
"changes" block, which the web app shows at the top of the results. The new run inherits the watch.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from benefitscout import pipeline, store  # noqa: E402


def one_pass() -> None:
    ids = store.watched()
    if not ids:
        print("nothing watched")
        return
    for rid in ids:
        old = store.load(rid)
        if not old:
            store.set_watch(rid, False)
            continue
        print(f"re-checking {rid}: {old['text'][:80]}")
        new = pipeline.recheck(old, lambda k, v: None)
        store.save(new)
        store.set_watch(rid, False)
        store.set_watch(new["id"], True)
        ch = new["changes"]
        flag = "ACTION" if ch["action_needed"] else "quiet"
        print(f"  -> {new['id']} [{flag}] new={ch['new_schemes']} gone={ch['gone_schemes']} verdicts={ch['verdict_changes']}")
        for b in ch["bullets"]:
            print("     -", b)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, default=0, help="hours between passes; 0 = run once")
    a = ap.parse_args()
    while True:
        one_pass()
        if not a.loop:
            break
        time.sleep(a.loop * 3600)
