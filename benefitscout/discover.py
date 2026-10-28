"""Stage 2: find candidate schemes.

Super writes the search plan (official domains, portal to crawl, queries).
Tavily search (preferring official domains) and Tavily crawl (portal, natural-language instructions)
collect pages. Nano triages the pile down to distinct, plausibly relevant schemes.
"""
from __future__ import annotations

from urllib.parse import urlparse

from . import llm, profile, web

# Seed knowledge: the model adds to these; search uses them in "prefer" mode so NGO and university
# pages are still found, official pages just rank first.
SEED_DOMAINS = {
    "India": ["myscheme.gov.in", "scholarships.gov.in", "gov.in", "nic.in", "india.gov.in"],
    "United States": ["benefits.gov", "usa.gov", "studentaid.gov", ".gov"],
    "United Kingdom": ["gov.uk"],
    "Canada": ["canada.ca", "gc.ca"],
    "Australia": ["servicesaustralia.gov.au", "gov.au"],
    "Germany": ["bund.de", "arbeitsagentur.de", "bafoeg.de"],
    "France": ["service-public.fr", "gouv.fr"],
    "Nigeria": ["gov.ng"],
    "Kenya": ["go.ke"],
    "Brazil": ["gov.br"],
}
SEED_PORTAL = {
    "India": "https://www.myscheme.gov.in",
    "United States": "https://www.benefits.gov",
    "United Kingdom": "https://www.gov.uk/browse/benefits",
    "Canada": "https://www.canada.ca/en/services/benefits.html",
    "Australia": "https://www.servicesaustralia.gov.au",
}

PLAN_PROMPT = """You plan web research to find government benefits, scholarships, grants, subsidies, pensions,
insurance schemes or support programmes that this person could qualify for.

Person:
{profile}

Return a JSON object with these keys:
- "official_domains": list of 3-8 official government or public-scheme domains for this country AND this region
  (for example the state government site). Bare domains only, no scheme prefix.
- "portal": object with "url" (the single best official scheme-finder portal to crawl, or null) and "instructions"
  (one sentence telling a crawler which pages to collect).
- "queries": list of exactly 8 diverse web search queries. Cover central AND regional schemes and every major angle
  that fits the person (education, income support, housing, health, business, category-based, gender-based).
  Specific beats generic. If the country's main official language is not English, write 2 of the 8 in that language.
- "scheme_types": list of the kinds of scheme most relevant to this person."""

TRIAGE_PROMPT = """A person is looking for government schemes, scholarships, benefits or grants they qualify for.

Person:
{profile}

The research plan specifically looked for these things:
{targets}

Below are web search / crawl results. Pick the distinct schemes (programmes) that are plausibly relevant to THIS
person. If a result describes one of the schemes the plan targeted, include it. Always include the main national and regional programmes for this person's situation when they appear in
the results (for example the national student grant, the main food or income support, the flagship farmer or
small-business scheme). One entry per scheme: if several results describe the same scheme, pick the most official
and detailed URL (the page that states eligibility rules). For a flagship national scheme whose official rules page is
not among the results, an encyclopedic page (Wikipedia, a bank's or insurer's scheme page, an official FAQ) is acceptable.
Skip news articles, generic listicles, and schemes that clearly cannot apply (wrong country, wrong age group, wrong category). Skip industrial or sector policies, incentives for
companies and investors, and programmes for organisations unless the person runs such a business. Prefer official
pages but keep a strong NGO/university scholarship if it fits.

Results:
{results}

Return JSON: {{"candidates": [{{"name": string, "url": string, "why": string, "relevance": number}}]}}
ordered by relevance (0-1, how likely this person qualifies AND would want it). At most {k} entries."""


def plan(prof: dict, ledger: llm.Ledger) -> dict:
    p = llm.ask_json(PLAN_PROMPT.format(profile=profile.one_line(prof)), "query_writer", ledger, max_tokens=1500)
    country = prof.get("country") or ""
    doms = list(dict.fromkeys((p.get("official_domains") or []) + SEED_DOMAINS.get(country, [])))
    p["official_domains"] = [d.replace("https://", "").replace("http://", "").strip("/") for d in doms][:10]
    portal = p.get("portal") or {}
    if not portal.get("url"):
        portal["url"] = SEED_PORTAL.get(country)
    portal.setdefault("instructions", "Collect pages describing individual schemes with their eligibility rules")
    p["portal"] = portal
    p["queries"] = [q for q in (p.get("queries") or []) if isinstance(q, str) and q.strip()][:8]
    return p


def gather(plan_: dict, wled: web.WebLedger, emit, deep: bool = True) -> tuple[list[dict], dict[str, str]]:
    """Run searches (and the portal crawl). Returns (results, url->full content from crawl)."""
    results: list[dict] = []
    page_text: dict[str, str] = {}
    doms = plan_["official_domains"]

    def do_search(q):
        return web.search(q, wled, include_domains=doms, prefer=True, max_results=8)

    for q, res in zip(plan_["queries"], llm.pmap(do_search, plan_["queries"], workers=4)):
        if isinstance(res, dict) and res.get("_error"):
            emit("log", f"search failed: {q} ({res['_error']})")
            continue
        emit("search", {"query": q, "n": len(res), "urls": [r["url"] for r in res]})
        results.extend(res)

    portal = plan_.get("portal") or {}
    if deep and portal.get("url"):
        try:
            emit("log", f"Tavily crawl: {portal['url']}  ->  \"{portal['instructions']}\"")
            pages = web.crawl(portal["url"], portal["instructions"], wled, limit=15, depth=2)
            emit("crawl", {"url": portal["url"], "n": len(pages), "urls": [p["url"] for p in pages]})
            if not pages:
                emit("log", "portal is a JavaScript app or blocks crawlers; relying on search results")
            for pg in pages:
                page_text[pg["url"]] = pg["content"]
                results.append({"url": pg["url"], "title": pg["title"], "content": pg["content"][:1500], "score": 0.5,
                                "from": "crawl"})
        except Exception as e:  # noqa: BLE001
            emit("log", f"crawl skipped: {type(e).__name__}: {str(e)[:120]}")

    # de-duplicate by URL; pages from the official portal crawl go first so triage always sees them
    results.sort(key=lambda r: 0 if r.get("from") == "crawl" else 1)
    seen, uniq = set(), []
    for r in results:
        u = r["url"].rstrip("/")
        if u not in seen and urlparse(u).scheme in ("http", "https"):
            seen.add(u)
            uniq.append(r)
    return uniq, page_text


def triage(prof: dict, results: list[dict], ledger: llm.Ledger, k: int = 12, plan_: dict | None = None) -> list[dict]:
    if not results:
        return []
    block = "\n\n".join(f"[{i}] {r['title']}\n{r['url']}\n{r['content'][:600]}" for i, r in enumerate(results[:90]))
    targets = "\n".join("- " + q for q in (plan_ or {}).get("queries", [])) or "- (none)"
    out = llm.ask_json(TRIAGE_PROMPT.format(profile=profile.one_line(prof), results=block, k=k, targets=targets), "triage",
                       ledger, max_tokens=2500)
    cands = []
    urls = {r["url"] for r in results}
    for c in (out.get("candidates") or []):
        if not isinstance(c, dict) or not c.get("url"):
            continue
        if c["url"] not in urls:  # the model must pick from the list, not invent
            continue
        cands.append({"name": str(c.get("name", "")).strip()[:120], "url": c["url"], "why": str(c.get("why", ""))[:300],
                      "relevance": float(c.get("relevance") or 0)})
    cands.sort(key=lambda c: -c["relevance"])
    return cands[:k]
