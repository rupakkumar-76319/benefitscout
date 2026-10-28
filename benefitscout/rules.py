"""Stage 3 (Super): read each scheme page and write its rules down as structured, quoted criteria.

Official portals are often JavaScript apps that serve an empty shell to crawlers (myscheme.gov.in does).
When the official page is unreadable, BenefitScout searches for the same scheme on a secondary source,
extracts the rules from there, and labels the result "secondary" so the person knows to verify.
"""
from __future__ import annotations

import re

from . import llm, web

PROMPT = """Below is the text of a web page that may describe a government scheme, scholarship, benefit or grant.
Extract its rules as JSON. Quote the page verbatim for every criterion so the reader can verify it; do not
paraphrase into the "quote" field and do not add criteria the page does not state. Criteria are conditions ABOUT THE
PERSON (age, income, residence, citizenship, study, category, occupation, disability, family, housing). Which office
handles the application, how to apply, documents to submit and deadlines are NOT criteria; put them in the other fields. If the page is not actually about
one specific scheme (it is a news story, a list of many schemes, a login page, an error page), set
"page_is_scheme" to false.

Page URL: {url}
Page text:
\"\"\"
{text}
\"\"\"

Return a JSON object with these keys:
"page_is_scheme" (boolean), "name", "authority" (who runs it: ministry, department, state, foundation, or null),
"country", "region_scope" ("national" or the state/region it is limited to),
"benefit" (what the person gets, one or two sentences), "benefit_amount" (amount or value if stated, else null),
"criteria" (list of objects, each with "id" like "c1", "type" one of age / income / residence / citizenship /
education / category / gender / occupation / disability / family / housing / other, "text" (the rule in plain
words) and "quote" (the verbatim sentence from the page that states it)),
"documents" (list of strings), "deadline" (application deadline or window as stated, null if not stated),
"how_to_apply", "apply_url", "last_updated_hint" (any date on the page showing how current it is)."""

SHELL_MARKERS = ("Something went wrong", "Please enable JavaScript", "Access Denied", "404 Not Found")


def readable(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 600 and not any(m in t[:1500] for m in SHELL_MARKERS)


def extract_rules(cand: dict, page_text: str, ledger: llm.Ledger) -> dict:
    if not readable(page_text):
        return {"page_is_scheme": False, "name": cand["name"], "_reason": "page unreadable (JavaScript app or blocked)"}
    rules = llm.ask_json(PROMPT.format(url=cand["url"], text=page_text.strip()[:24000]), "extract", ledger, max_tokens=2500)
    if not isinstance(rules, dict):
        return {"page_is_scheme": False, "name": cand["name"], "_reason": "unparseable"}
    rules["url"] = cand["url"]
    if not rules.get("name"):
        rules["name"] = cand["name"]
    rules["criteria"] = [c for c in (rules.get("criteria") or []) if isinstance(c, dict) and c.get("text")]
    for i, c in enumerate(rules["criteria"], 1):
        c["id"] = f"c{i}"
    rules["documents"] = [str(d) for d in (rules.get("documents") or [])][:15]
    if not rules.get("_reason") and not rules.get("page_is_scheme"):
        rules["_reason"] = "not a single-scheme page"
    return rules


def _fetch(urls: list[str], page_text: dict[str, str], wled: web.WebLedger, emit) -> None:
    need = [u for u in urls if u not in page_text]
    if not need:
        return
    emit("log", f"Tavily extract: {len(need)} pages")
    try:
        fetched = web.extract(need, wled)
    except Exception as e:  # noqa: BLE001
        emit("log", f"extract failed: {type(e).__name__}: {str(e)[:120]}")
        return
    norm = {k.rstrip("/"): v for k, v in fetched.items()}
    for u in need:
        page_text[u] = fetched.get(u) or norm.get(u.rstrip("/")) or ""


def _norm_name(n: str) -> str:
    n = re.sub(r"\(.*?\)", " ", (n or "").lower())
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    stop = {"scheme", "yojana", "the", "of", "for", "and", "programme", "program", "component", "sector", "central", "a"}
    return " ".join(w for w in n.split() if w not in stop)[:60]


def dedupe(good: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for r in good:
        key = _norm_name(r.get("name", ""))
        prev = seen.get(key)
        if prev is None or (len(r["criteria"]) > len(prev["criteria"]) and r.get("source_kind") == prev.get("source_kind")) \
                or (prev.get("source_kind") == "secondary" and r.get("source_kind") != "secondary"):
            seen[key] = r
    return list(seen.values())


def extract_all(cands: list[dict], page_text: dict[str, str], ledger: llm.Ledger, wled: web.WebLedger, emit,
                fallback: bool = True, max_fallback: int = 4) -> tuple[list[dict], list[dict]]:
    _fetch([c["url"] for c in cands], page_text, wled, emit)

    def one(c):
        r = extract_rules(c, page_text.get(c["url"], ""), ledger)
        r["_candidate"] = c
        r["source_kind"] = "official"
        emit("rules", {"name": r.get("name"), "url": c["url"], "ok": bool(r.get("page_is_scheme") and r.get("criteria")),
                       "n_criteria": len(r.get("criteria") or []), "reason": r.get("_reason")})
        return r

    out = llm.pmap(one, cands, workers=6)
    ok = lambda r: isinstance(r, dict) and r.get("page_is_scheme") and r.get("criteria")  # noqa: E731
    good = [r for r in out if ok(r)]
    bad = [r for r in out if not ok(r)]

    # Second chance: the official page could not be read, so find the same scheme described elsewhere.
    retry = [b for b in bad if isinstance(b, dict) and (b.get("_candidate") or {}).get("name")
             and float((b.get("_candidate") or {}).get("relevance") or 0) >= 0.5][:max_fallback]
    if fallback and retry:
        emit("log", f"{len(retry)} official pages unreadable; searching secondary sources for the same schemes")

        def find_alt(b):
            c = b["_candidate"]
            res = web.search(f"{c['name']} eligibility criteria documents how to apply", wled, max_results=5)
            alts = [r for r in res if r["url"].rstrip("/") != c["url"].rstrip("/")
                    and "myscheme.gov.in" not in r["url"] and not r["url"].lower().endswith(".pdf")]
            return alts[:2]

        alts = llm.pmap(find_alt, retry, workers=4)
        alt_cands = []
        for b, a in zip(retry, alts):
            if isinstance(a, list) and a:
                c = b["_candidate"]
                alt_cands.append({"name": c["name"], "url": a[0]["url"], "why": c.get("why", ""), "relevance": c.get("relevance", 0),
                                  "official_url": c["url"], "backup_url": a[1]["url"] if len(a) > 1 else None})
        _fetch([c["url"] for c in alt_cands], page_text, wled, emit)

        def one_alt(c):
            r = extract_rules(c, page_text.get(c["url"], ""), ledger)
            if not ok(r) and c.get("backup_url"):
                _fetch([c["backup_url"]], page_text, wled, emit)
                c2 = {**c, "url": c["backup_url"]}
                r = extract_rules(c2, page_text.get(c2["url"], ""), ledger)
                c = c2
            r["_candidate"] = c
            r["source_kind"] = "secondary"
            r["official_url"] = c["official_url"]
            emit("rules", {"name": r.get("name"), "url": c["url"], "ok": bool(ok(r)), "secondary": True,
                           "n_criteria": len(r.get("criteria") or []), "reason": r.get("_reason")})
            return r

        out2 = llm.pmap(one_alt, alt_cands, workers=6)
        recovered = [r for r in out2 if ok(r)]
        recovered_names = {_norm_name(r.get("name", "")) for r in recovered}
        good += recovered
        bad = [b for b in bad if _norm_name((b.get("_candidate") or {}).get("name", "")) not in recovered_names]
        bad += [r for r in out2 if not ok(r)]

    good.sort(key=lambda r: -float((r.get("_candidate") or {}).get("relevance") or 0))
    return dedupe(good), bad
