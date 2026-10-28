"""BenefitScout web app: FastAPI + server-sent events, static front end in ./web."""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import __version__, llm, pipeline, store, web  # noqa: E402

app = FastAPI(title="BenefitScout", version=__version__)
WEB = Path(__file__).resolve().parent.parent / "web"


def _sse(work) -> StreamingResponse:
    """Run `work(emit)` in a thread and stream its events as SSE."""
    q: queue.Queue = queue.Queue()

    def emit(kind, payload):
        q.put((kind, payload))

    def runner():
        try:
            res = work(emit)
            store.save(res)
        except Exception as e:  # noqa: BLE001
            q.put(("error", f"{type(e).__name__}: {str(e)[:300]}"))
        finally:
            q.put(None)

    threading.Thread(target=runner, daemon=True).start()

    def gen():
        yield "retry: 10000\n\n"
        while True:
            try:
                item = q.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                yield "event: close\ndata: {}\n\n"
                break
            kind, payload = item
            yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/run/stream")
def run_stream(text: str, deep: int = 1, max_schemes: int = 10):
    text = (text or "").strip()
    if len(text) < 15:
        raise HTTPException(400, "Please describe your situation in a sentence or two.")
    return _sse(lambda emit: pipeline.run(text[:4000], emit, deep=bool(deep), max_schemes=min(max(max_schemes, 3), 14)))


@app.get("/api/recheck/{run_id}/stream")
def recheck_stream(run_id: str):
    old = store.load(run_id)
    if not old:
        raise HTTPException(404, "run not found")
    return _sse(lambda emit: pipeline.recheck(old, emit))


@app.get("/api/runs")
def runs():
    return store.list_runs()


@app.get("/api/run/{run_id}")
def run_get(run_id: str):
    r = store.load(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return r


@app.post("/api/watch/{run_id}")
def watch(run_id: str, on: int = 1):
    if not store.load(run_id):
        raise HTTPException(404, "run not found")
    return {"watched": store.set_watch(run_id, bool(on))}


@app.get("/api/status")
def status():
    return {"version": __version__, "models": {k: llm.SHORT[v] for k, v in llm.ROLES.items()},
            "prices_usd_per_1m": {llm.SHORT[k]: v for k, v in llm.PRICES.items()}, "tavily": web.account_usage(),
            "watched": store.watched()}


@app.get("/api/export/{run_id}.md", response_class=PlainTextResponse)
def export_md(run_id: str):
    r = store.load(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return to_markdown(r)


def to_markdown(r: dict) -> str:
    s = r.get("summary") or {}
    out = [f"# BenefitScout report ({r['created']})", "", f"**You said:** {r['text']}", ""]
    if s.get("headline"):
        out += [f"## {s['headline']}", "", s.get("summary", ""), "", f"**Do this first:** {s.get('first_action','')}", ""]
    labels = {"eligible": "Eligible", "likely": "Likely eligible", "need_info": "Need more information", "not_eligible": "Not eligible"}
    for sch in r.get("schemes", []):
        out += [f"## {sch['name']}  -  {labels.get(sch['verdict'], sch['verdict'])} ({round(100*float(sch.get('confidence') or 0))}%)",
                "", f"{sch.get('benefit','')}" + (f" ({sch['benefit_amount']})" if sch.get("benefit_amount") else ""), "",
                f"Source: {sch['url']}" + (f"  (secondary source; official page: {sch['official_url']})" if sch.get("official_url") else ""),
                f"Deadline: {sch.get('deadline') or 'not stated'}", "", sch.get("why", ""), "", "| Rule | Verdict | Why |", "|---|---|---|"]
        for c in sch.get("criteria", []):
            out.append(f"| {c['text']} | {c.get('verdict','')} | {c.get('reason','')} |")
        if sch.get("documents"):
            out += ["", "Documents: " + "; ".join(sch["documents"])]
        if sch.get("next_steps"):
            out += ["", "Next steps:"] + [f"{i}. {x}" for i, x in enumerate(sch["next_steps"], 1)]
        if sch.get("missing_info"):
            out += ["", "Still needed: " + "; ".join(sch["missing_info"])]
        out.append("")
    c = r.get("costs", {})
    n, t = c.get("nemotron", {}), c.get("tavily", {})
    out += ["---", f"Nemotron on Nebius Token Factory: ${n.get('total_usd')} ({n.get('calls')} calls; the same run on Ultra alone: "
            f"${n.get('all_ultra_usd')}, so {n.get('saved_pct')}% saved). Tavily: {t.get('credits')} credits over {t.get('calls')} calls. "
            f"{c.get('seconds')} s."]
    return "\n".join(out)


app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
