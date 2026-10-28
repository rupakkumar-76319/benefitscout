"""The whole BenefitScout run, as a stream of events.

  describe yourself  ->  Nano: profile  ->  Super: search plan  ->  Tavily search + crawl
  ->  Super: triage  ->  Tavily extract  ->  Super: rules with quotes  ->  Ultra: verdict per criterion
  ->  Super: summary (+ translation)  ->  result + cost ledger
"""
from __future__ import annotations

import time
import uuid

from . import discover, judge, llm, profile, rules, web


def run(text: str, emit, deep: bool = True, max_schemes: int = 10, translate: bool = True) -> dict:
    ledger, wled = llm.Ledger(), web.WebLedger()
    t0 = time.time()
    run_id = uuid.uuid4().hex[:10]

    emit("stage", {"stage": "profile", "model": llm.SHORT[llm.ROLES["profile"]]})
    prof = profile.normalise(text, ledger)
    emit("profile", prof)

    emit("stage", {"stage": "plan", "model": llm.SHORT[llm.ROLES["query_writer"]]})
    plan = discover.plan(prof, ledger)
    emit("plan", plan)

    emit("stage", {"stage": "search", "model": "Tavily search + crawl"})
    results, page_text = discover.gather(plan, wled, emit, deep=deep)
    emit("log", f"{len(results)} unique pages collected")

    emit("stage", {"stage": "triage", "model": llm.SHORT[llm.ROLES["triage"]]})
    cands = discover.triage(prof, results, ledger, k=max_schemes + 2, plan_=plan)
    emit("candidates", cands)
    if not cands:
        res = _finish(run_id, text, prof, plan, [], [], {}, ledger, wled, t0)
        emit("done", res)
        return res

    emit("stage", {"stage": "rules", "model": llm.SHORT[llm.ROLES["extract"]]})
    good, bad = rules.extract_all(cands, page_text, ledger, wled, emit)
    good = good[:max_schemes]
    emit("log", f"{len(good)} scheme pages with rules, {len(bad)} pages dropped (not a scheme page / blocked)")

    emit("stage", {"stage": "judge", "model": llm.SHORT[llm.ROLES["judge"]]})
    items = judge.judge_all(prof, good, ledger, emit)

    emit("stage", {"stage": "summary", "model": llm.SHORT[llm.ROLES["summary"]]})
    summary = judge.summarise(prof, items, ledger) if items else {}
    if translate and items and prof.get("language") not in (None, "", "en"):
        try:
            summary["translated"] = judge.translate(
                {k: summary.get(k) for k in ("headline", "summary", "first_action")}, prof["language"], ledger)
            summary["translated"]["language"] = prof["language"]
        except Exception:  # noqa: BLE001
            pass

    dropped = [{"name": b.get("name"), "url": (b.get("_candidate") or {}).get("url"), "reason": b.get("_reason", "not a scheme page")}
               for b in bad]
    res = _finish(run_id, text, prof, plan, items, dropped, summary, ledger, wled, t0)
    emit("done", res)
    return res


def _finish(run_id, text, prof, plan, items, dropped, summary, ledger, wled, t0) -> dict:
    return {"id": run_id, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text, "profile": prof, "plan": plan,
            "schemes": items, "dropped": dropped, "summary": summary,
            "costs": {"nemotron": ledger.summary(), "tavily": wled.summary(), "seconds": round(time.time() - t0, 1)},
            "counts": {k: sum(1 for i in items if i["verdict"] == k) for k in ("eligible", "likely", "need_info", "not_eligible")}}


# ---------------------------------------------------------------- re-check (the workflow that runs itself)

DIFF_PROMPT = """Two runs of the same eligibility search, days apart. Summarise for the person in 2-4 short bullet
points what changed and whether they need to act: new schemes, schemes that disappeared, changed verdicts, changed
deadlines. If nothing material changed, say so in one line. Return JSON {{"bullets": [string], "action_needed": boolean}}.

Before: {before}
After: {after}"""


def recheck(old: dict, emit) -> dict:
    """Re-run the search for a saved profile and diff against the previous result."""
    new = run(old["text"], emit, deep=True)
    slim = lambda r: [{k: s.get(k) for k in ("name", "verdict", "deadline", "benefit_amount")} for s in r["schemes"]]  # noqa: E731
    ledger = llm.Ledger()
    import json
    diff = llm.ask_json(DIFF_PROMPT.format(before=json.dumps(slim(old), ensure_ascii=False),
                                           after=json.dumps(slim(new), ensure_ascii=False)), "diff", ledger, max_tokens=800)
    before, after = {s["name"] for s in old["schemes"]}, {s["name"] for s in new["schemes"]}
    new["changes"] = {"previous_run": old["id"], "previous_created": old["created"],
                      "new_schemes": sorted(after - before), "gone_schemes": sorted(before - after),
                      "verdict_changes": [{"name": s["name"], "from": o["verdict"], "to": s["verdict"]}
                                          for s in new["schemes"] for o in old["schemes"]
                                          if o["name"] == s["name"] and o["verdict"] != s["verdict"]],
                      "bullets": (diff or {}).get("bullets", []), "action_needed": bool((diff or {}).get("action_needed"))}
    new["costs"]["nemotron_diff"] = ledger.summary()
    return new
