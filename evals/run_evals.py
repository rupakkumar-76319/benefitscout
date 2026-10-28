"""Measure the Nano / Super / Ultra split.

For each labelled profile in profiles.json we run the whole pipeline and check two things:
  recall   - was each expected scheme found at all?
  accuracy - for the expected schemes that were found, did the judge give an allowed verdict?
Profile normalisation and planning stay on their default models so every configuration
sees the same web pages (Tavily results are served from cache after the first pass);
three roles change: "triage" (results -> candidates), "extract" (page -> rules) and "judge" (rules -> verdict).

  python evals/run_evals.py            # all configurations
  python evals/run_evals.py split      # one configuration

Writes evals/results.json and docs/EVALS.md (scores, cost, and every failure case with the model's own words).
"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from benefitscout import llm, pipeline  # noqa: E402

CONFIGS = {
    "split":        {"triage": llm.SUPER, "extract": llm.SUPER, "judge": llm.ULTRA},   # what the app ships
    "judge_super":  {"triage": llm.SUPER, "extract": llm.SUPER, "judge": llm.SUPER},
    "judge_nano":   {"triage": llm.SUPER, "extract": llm.SUPER, "judge": llm.NANO},
    "triage_nano":  {"triage": llm.NANO,  "extract": llm.SUPER, "judge": llm.ULTRA},   # our first version
    "extract_nano": {"triage": llm.SUPER, "extract": llm.NANO,  "judge": llm.ULTRA},
    "all_ultra":    {"triage": llm.ULTRA, "extract": llm.ULTRA, "judge": llm.ULTRA},
}


def score(run: dict, expects: list[dict]) -> list[dict]:
    out = []
    for e in expects:
        rx = re.compile(e["match"], re.I)
        hits = [s for s in run["schemes"] if rx.search(s.get("name") or "")]
        if not hits:
            # A scheme the person clearly cannot get may be dropped at triage instead of judged: that is correct too.
            neg = e["verdicts"] == ["not_eligible"]
            out.append({**e, "found": False, "ok": neg, "got": "not surfaced"})
            continue
        best = sorted(hits, key=lambda s: 0 if s["verdict"] in e["verdicts"] else 1)[0]
        out.append({**e, "found": True, "ok": best["verdict"] in e["verdicts"], "got": best["verdict"],
                    "scheme": best["name"], "why": best.get("why"), "confidence": best.get("confidence")})
    return out


def run_config(name: str, cfg: dict, profiles: list[dict]) -> dict:
    llm.ROLES.update(cfg)
    print(f"\n=== {name}: triage={llm.SHORT[cfg['triage']]}, extract={llm.SHORT[cfg['extract']]}, judge={llm.SHORT[cfg['judge']]}")
    t0 = time.time()

    def one(p):
        quiet = lambda k, v: None  # noqa: E731
        r = pipeline.run(p["text"], quiet, deep=True, translate=False)
        sc = score(r, p["expect"])
        print(f"  {p['id']:28s} found {sum(s['found'] for s in sc)}/{len(sc)}  correct {sum(s['ok'] for s in sc)}/{len(sc)}"
              f"  ${r['costs']['nemotron']['total_usd']:.3f}  {r['costs']['seconds']}s")
        return {"id": p["id"], "scores": sc, "n_schemes": len(r["schemes"]),
                "cost": r["costs"]["nemotron"]["total_usd"], "seconds": r["costs"]["seconds"],
                "judge_cost": r["costs"]["nemotron"]["roles"].get("judge", {}).get("cost", 0),
                "extract_cost": r["costs"]["nemotron"]["roles"].get("extract", {}).get("cost", 0),
                "tavily_credits": r["costs"]["tavily"]["credits"]}

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(one, profiles))
    flat = [s for r in rows for s in r["scores"]]
    found = [s for s in flat if s["found"]]
    pos = [s for s in flat if s["verdicts"] != ["not_eligible"]]
    return {"name": name, "triage": llm.SHORT[cfg["triage"]], "extract": llm.SHORT[cfg["extract"]], "judge": llm.SHORT[cfg["judge"]], "rows": rows,
            "n_expect": len(flat), "recall": round(sum(s["found"] for s in pos) / len(pos), 3) if pos else 0,
            "accuracy": round(sum(s["ok"] for s in found) / len(found), 3) if found else 0,
            "correct_total": sum(s["ok"] for s in flat),
            "cost": round(sum(r["cost"] for r in rows), 4), "judge_cost": round(sum(r["judge_cost"] for r in rows), 4),
            "extract_cost": round(sum(r["extract_cost"] for r in rows), 4),
            "seconds": round(time.time() - t0, 1)}


def write_report(results: list[dict], profiles: list[dict]) -> str:
    L = ["# BenefitScout evals: does the Nano / Super / Ultra split earn its keep?", "",
         f"{len(profiles)} labelled profiles across India, the US, the UK, Germany and Canada, "
         f"{sum(len(p['expect']) for p in profiles)} expected (scheme, verdict) pairs. Profile, plan and triage run on the "
         "default models in every configuration and Tavily results are cached, so every configuration starts from the "
         "same web pages; the triage, rule-extraction and judging models change. Labels are in `evals/profiles.json`; rerun with `python evals/run_evals.py`.", "",
         "| Configuration | Triage by | Rules by | Verdicts by | Expected schemes found | Verdict accuracy on found | Correct / expected | Nemotron cost (8 runs) | of which judge | of which extract |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        L.append(f"| {r['name']} | {r['triage']} | {r['extract']} | {r['judge']} | {r['recall']:.0%} | {r['accuracy']:.0%} | "
                 f"{r['correct_total']}/{r['n_expect']} | ${r['cost']:.3f} | ${r['judge_cost']:.3f} | ${r['extract_cost']:.3f} |")
    L += ["", "Accuracy counts a verdict as correct when it is in the allowed set for that scheme "
          "(for example a UK Carer's Allowance claim may be `likely` or `need_info`, because it depends on the parent's "
          "benefit, which the person did not state).", "", "## Failure cases", ""]
    for r in results:
        fails = [(row["id"], s) for row in r["rows"] for s in row["scores"] if not s["ok"]]
        L.append(f"### {r['name']} ({len(fails)} failures)")
        if not fails:
            L.append("none")
        for pid, s in fails:
            if not s["found"]:
                L.append(f"- **{pid}** - expected `{s['match']}` was never surfaced ({s['note']}).")
            else:
                L.append(f"- **{pid}** - {s['scheme']}: got `{s['got']}`, expected one of `{'/'.join(s['verdicts'])}` ({s['note']}). "
                         f"Model said: \"{(s.get('why') or '')[:300]}\"")
        L.append("")
    return "\n".join(L)


def rescore(results: list[dict], profiles: list[dict]) -> None:
    """Re-apply the labels in profiles.json to stored results (after a label fix, no model calls)."""
    by_id = {p["id"]: p for p in profiles}
    for r in results:
        for row in r["rows"]:
            exp = by_id[row["id"]]["expect"]
            for s, e in zip(row["scores"], exp):
                s["match"], s["verdicts"], s["note"] = e["match"], e["verdicts"], e["note"]
                s["ok"] = (s["got"] in e["verdicts"]) if s["found"] else (e["verdicts"] == ["not_eligible"])
        flat = [s for row in r["rows"] for s in row["scores"]]
        found = [s for s in flat if s["found"]]
        pos = [s for s in flat if s["verdicts"] != ["not_eligible"]]
        r["recall"] = round(sum(s["found"] for s in pos) / len(pos), 3) if pos else 0
        r["accuracy"] = round(sum(s["ok"] for s in found) / len(found), 3) if found else 0
        r["correct_total"] = sum(s["ok"] for s in flat)


def main():
    profiles = json.loads((ROOT / "evals" / "profiles.json").read_text(encoding="utf-8"))
    out_json = ROOT / "evals" / "results.json"
    if sys.argv[1:] == ["--rescore"]:
        results = json.loads(out_json.read_text(encoding="utf-8"))
        rescore(results, profiles)
        out_json.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        (ROOT / "docs" / "EVALS.md").write_text(write_report(results, profiles), encoding="utf-8")
        for r in results:
            print(f"{r['name']:14s} recall {r['recall']:.0%}  accuracy {r['accuracy']:.0%}  correct {r['correct_total']}/{r['n_expect']}  ${r['cost']:.3f}")
        return
    names = sys.argv[1:] or list(CONFIGS)
    results = []
    if out_json.exists():
        results = [r for r in json.loads(out_json.read_text(encoding="utf-8")) if r["name"] not in names]
    for n in names:
        results.append(run_config(n, CONFIGS[n], profiles))
        out_json.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    results.sort(key=lambda r: list(CONFIGS).index(r["name"]) if r["name"] in CONFIGS else 99)
    (ROOT / "docs" / "EVALS.md").write_text(write_report(results, profiles), encoding="utf-8")
    print("\nwrote docs/EVALS.md")
    for r in results:
        print(f"{r['name']:14s} recall {r['recall']:.0%}  accuracy {r['accuracy']:.0%}  correct {r['correct_total']}/{r['n_expect']}  ${r['cost']:.3f}")


if __name__ == "__main__":
    main()
