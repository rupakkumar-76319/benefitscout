"""Stage 4 (Ultra, thinking on): does THIS person meet EACH rule? Then a short plan (Super)."""
from __future__ import annotations

import json

from . import llm, profile

SYSTEM = """You are a careful caseworker. You decide whether a specific person meets each eligibility criterion of a
scheme, using only the person's stated facts and the quoted rules. You never invent facts, but you do draw the
obvious inferences a caseworker would (someone who says they own 1.5 acres has land in their name; a 71-year-old
who has lived in Canada for 40 years meets a 10-year residence rule; a person described as a student is enrolled).
You convert units and currencies sensibly (lakh = 100,000; monthly vs yearly) and reason about thresholds explicitly.

Calibration:
- "unknown" is for a substantive fact about the person that the rule turns on and that they did not state
  (their exact income, whether they already receive another benefit, their caste certificate).
- Procedural requirements are NOT unknowns: having to submit documents, prove ownership, pass a lender's credit
  check, enrol before a seasonal date, or have crops that are notified locally. Treat those as met for the verdict and
  list them under next_steps instead.
- A prerequisite that is itself another scheme (e.g. "must receive Old Age Security") is judged from the person's
  facts, not marked unknown.
- Exclusions the person would normally have mentioned (already receiving the same kind of benefit, being a
  government employee or income-tax payer, owning a car or a second house, being a loan defaulter) count as "unknown"
  but do not by themselves pull the verdict below "likely": say "likely" and ask the question in missing_info.
- Consistency: if any criterion "fails", the verdict is "not_eligible", unless the criteria are alternatives
  (only one of several routes must be met); then mark the unmet alternatives "unknown" and say so in the reason.
Answer with a single JSON object and nothing else."""


PROMPT = """Person:
{profile}

Scheme: {name}
Authority: {authority}   Scope: {scope}   Country: {country}
Benefit: {benefit}
Deadline: {deadline}

Criteria (verbatim quotes from the official page {url}):
{criteria}

Return a JSON object with these keys:
- "verdict": one of "eligible" (every criterion met on stated facts or obvious inference), "likely" (all known
  criteria met; what remains is procedural or a minor unknown that is probably fine), "need_info" (a substantive,
  decisive criterion is unknown and could realistically go either way), "not_eligible" (at least one criterion clearly failed).
- "confidence": number between 0 and 1.
- "criteria": list with one object per criterion id above, each with "id", "verdict" (one of "meets", "fails",
  "unknown") and "reason" (one sentence, citing the person's fact and the rule).
- "blocking": list of ids of failed criteria (empty if none).
- "missing_info": list of concrete questions to ask the person (empty if none).
- "why": one or two plain-language sentences for the person.
- "next_steps": list of 2-4 concrete actions, in order.
- "value_to_person": one of "high", "medium", "low" (how much this scheme matters for their stated goals)."""

SUMMARY_PROMPT = """You are writing the top of a results page for a person who asked which government schemes,
scholarships or benefits they qualify for. Be warm, specific and short. No marketing language.

Person:
{profile}

Results (JSON):
{results}

Return a JSON object with these keys:
"headline" (one sentence: how many schemes look open to them and the single best one),
"summary" (3-5 sentences: what to apply for first and why, key deadlines, what information is still missing and
would unlock more), "first_action" (the one thing to do today),
"questions" (list of up to 4 questions whose answers would most change the results)."""


def _conf(v) -> float:
    """Confidence as a number in [0, 1]; smaller models sometimes answer with a word."""
    words = {"very high": 0.95, "high": 0.85, "medium": 0.6, "moderate": 0.6, "low": 0.3, "very low": 0.15}
    if isinstance(v, str):
        if v.strip().lower() in words:
            return words[v.strip().lower()]
        try:
            v = float(v.strip().rstrip("%")) / (100 if "%" in v else 1)
        except ValueError:
            return 0.5
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def judge_one(prof: dict, rules: dict, ledger: llm.Ledger) -> dict:
    crit = "\n".join(f"{c['id']} [{c.get('type','other')}] {c['text']}\n    quote: \"{(c.get('quote') or '')[:400]}\""
                     for c in rules["criteria"])
    prompt = PROMPT.format(profile=profile.one_line(prof), name=rules.get("name"), authority=rules.get("authority"),
                           scope=rules.get("region_scope"), country=rules.get("country"), benefit=rules.get("benefit"),
                           deadline=rules.get("deadline") or "not stated", url=rules.get("url"), criteria=crit)
    out = llm.ask_json(prompt, "judge", ledger, system=SYSTEM, thinking=True, max_tokens=5000)
    if not isinstance(out, dict) or out.get("verdict") not in ("eligible", "likely", "not_eligible", "need_info"):
        raise ValueError("judge returned no verdict")
    by_id = {c.get("id"): c for c in (out.get("criteria") or []) if isinstance(c, dict)}
    merged = []
    for c in rules["criteria"]:
        j = by_id.get(c["id"], {})
        merged.append({**c, "verdict": j.get("verdict", "unknown"), "reason": j.get("reason", "")})
    out["criteria"] = merged
    out["confidence"] = _conf(out.get("confidence"))
    out.setdefault("missing_info", [])
    out.setdefault("next_steps", [])
    out.setdefault("blocking", [])
    return out


ORDER = {"eligible": 0, "likely": 1, "need_info": 2, "not_eligible": 3}


def judge_all(prof: dict, schemes: list[dict], ledger: llm.Ledger, emit, workers: int = 6) -> list[dict]:
    def one(rules):
        j = judge_one(prof, rules, ledger)
        item = {"name": rules.get("name"), "url": rules.get("url"), "authority": rules.get("authority"),
                "region_scope": rules.get("region_scope"), "benefit": rules.get("benefit"),
                "benefit_amount": rules.get("benefit_amount"), "deadline": rules.get("deadline"),
                "documents": rules.get("documents", []), "how_to_apply": rules.get("how_to_apply"),
                "apply_url": rules.get("apply_url") or rules.get("official_url") or rules.get("url"),
                "last_updated_hint": rules.get("last_updated_hint"),
                "source_kind": rules.get("source_kind", "official"), "official_url": rules.get("official_url"),
                **j}
        emit("verdict", {"name": item["name"], "verdict": item["verdict"], "confidence": item.get("confidence")})
        return item

    out = llm.pmap(one, schemes, workers=workers)
    items = []
    for r, s in zip(out, schemes):
        if isinstance(r, dict) and r.get("_error"):
            emit("log", f"judge failed for {s.get('name')}: {r['_error'][:120]}")
            continue
        items.append(r)
    items.sort(key=lambda x: (ORDER.get(x["verdict"], 9), -float(x.get("confidence") or 0)))
    return items


def summarise(prof: dict, items: list[dict], ledger: llm.Ledger) -> dict:
    slim = [{k: it.get(k) for k in ("name", "verdict", "confidence", "benefit", "benefit_amount", "deadline",
                                     "missing_info", "why", "value_to_person")} for it in items]
    out = llm.ask_json(SUMMARY_PROMPT.format(profile=profile.one_line(prof), results=json.dumps(slim, ensure_ascii=False)),
                       "summary", ledger, max_tokens=1200)
    return out if isinstance(out, dict) else {"headline": "", "summary": "", "first_action": "", "questions": []}


LANG_NAMES = {"hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi", "gu": "Gujarati", "kn": "Kannada",
              "ml": "Malayalam", "pa": "Punjabi", "ur": "Urdu", "or": "Odia", "as": "Assamese", "ne": "Nepali", "es": "Spanish",
              "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian", "ar": "Arabic", "sw": "Swahili", "id": "Indonesian",
              "vi": "Vietnamese", "tr": "Turkish", "ru": "Russian", "uk": "Ukrainian", "pl": "Polish", "nl": "Dutch", "zh": "Chinese",
              "ja": "Japanese", "ko": "Korean", "fa": "Persian", "ha": "Hausa", "yo": "Yoruba", "am": "Amharic", "tl": "Filipino"}


def translate(text_obj: dict, language: str, ledger: llm.Ledger) -> dict:
    """Translate the summary block into the person's own language (Super: reliable in non-Latin scripts)."""
    name = LANG_NAMES.get((language or "").lower()[:2], language)
    out = llm.ask_json(f"Translate every string value in this JSON into {name}. Keep the keys and structure identical, "
                       "keep scheme names, amounts and acronyms as they are, and write naturally for a non-expert reader.\n\n"
                       + json.dumps(text_obj, ensure_ascii=False), "translate", ledger, max_tokens=1500)
    return out if isinstance(out, dict) and out.get("summary") else text_obj
