"""Stage 1 (Nano): turn whatever the person typed, in any language, into a structured profile."""
from __future__ import annotations

from . import llm

SYSTEM = """You turn a person's free-text description of their situation into a structured JSON profile that
will be matched against eligibility rules of government benefits, scholarships and grants.
Never invent facts. Use null for anything not stated. Keep the person's own wording in "facts"."""

PROMPT = """Description (may be in any language):
\"\"\"{text}\"\"\"

Return a JSON object with exactly these keys (use null when not stated):
"country" (full English country name), "region" (state, province or canton, English name), "city",
"age" (number), "gender", "citizenship", "residency_years" (number),
"education" (object with "status" one of student / graduate / not studying / null, "level", "year" (number), "field",
"institution_type", "last_result"),
"household_income_annual" (object with "amount" (number, per year) and "currency"),
"social_category" (for India one of SC, ST, OBC, EWS, general; elsewhere only if stated),
"minority_or_religion", "disability",
"occupation" (student, farmer, unemployed, self-employed, salaried, retired, ...), "employment_status",
"family" (object with "parent_occupation", "single_parent" (boolean), "orphan" (boolean), "dependants" (number),
"marital_status"),
"housing" (owns, rents, homeless, rural, urban), "veteran_or_armed_forces_family" (boolean),
"business" (if they run or want to start one),
"goals" (list of strings: what they want help with, in English),
"facts" (list of strings: every concrete fact from the text, one per item, in English),
"language" (ISO 639-1 code of the input text, for example "en" or "hi"),
"missing_important" (list of 2-4 facts that would most change which schemes apply and are NOT stated)."""


def normalise(text: str, ledger: llm.Ledger) -> dict:
    prof = llm.ask_json(PROMPT.format(text=text.strip()[:4000]), "profile", ledger, system=SYSTEM, max_tokens=1800)
    if not isinstance(prof, dict):
        raise ValueError("profile parse failed")
    prof.setdefault("facts", [])
    prof.setdefault("goals", [])
    prof.setdefault("missing_important", [])
    prof.setdefault("language", "en")
    prof["raw_text"] = text.strip()
    return prof


def one_line(prof: dict) -> str:
    """Compact profile for prompts."""
    parts = []
    for k in ("country", "region", "age", "gender", "citizenship", "social_category", "occupation",
              "disability", "minority_or_religion", "housing"):
        v = prof.get(k)
        if v not in (None, "", [], False):
            parts.append(f"{k}={v}")
    ed = prof.get("education") or {}
    if ed.get("status"):
        parts.append("education=" + ", ".join(f"{k}:{v}" for k, v in ed.items() if v))
    inc = prof.get("household_income_annual") or {}
    if inc.get("amount") is not None:
        parts.append(f"household_income_annual={inc.get('amount')} {inc.get('currency') or ''}".strip())
    fam = prof.get("family") or {}
    fam_s = ", ".join(f"{k}:{v}" for k, v in fam.items() if v not in (None, False, ""))
    if fam_s:
        parts.append("family=" + fam_s)
    if prof.get("business"):
        parts.append(f"business={prof['business']}")
    if prof.get("goals"):
        parts.append("goals=" + "; ".join(prof["goals"]))
    s = "\n".join("- " + p for p in parts)
    if prof.get("facts"):
        s += "\nStated facts:\n" + "\n".join("- " + f for f in prof["facts"])
    return s
