"""Nemotron on Nebius Token Factory, with a per-role ledger.

Three models, three jobs:
  Nano  - cheap and fast: normalise the profile, diff two runs, repair malformed JSON.
  Super - structure: plan the research, triage dozens of search hits, turn a messy government page into rules.
  Ultra - reasoning: decide, criterion by criterion, whether THIS person qualifies.
Every call is tagged with a role so the UI can show who did what and what it cost,
and what the same run would have cost on Ultra alone.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI

NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
SUPER = "nvidia/nemotron-3-super-120b-a12b"
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
SHORT = {NANO: "Nemotron 3 Nano 30B", SUPER: "Nemotron 3 Super 120B", ULTRA: "Nemotron 3 Ultra 550B"}

# USD per 1M tokens (input, output) on Nebius Token Factory, list prices Oct 2026.
PRICES = {NANO: (0.06, 0.24), SUPER: (0.30, 0.90), ULTRA: (1.00, 3.00)}

# Role -> model. The eval script overrides this to measure "all Nano" / "all Ultra" etc.
ROLES = {"profile": NANO, "query_writer": SUPER, "triage": SUPER, "extract": SUPER,
         "judge": ULTRA, "summary": SUPER, "translate": SUPER, "diff": NANO}

CACHE_DIR = Path(os.environ.get("BS_DATA_DIR", "data")) / "llm_cache"
CACHE_ON = os.environ.get("BS_LLM_CACHE", "1") == "1"


@dataclass
class Call:
    role: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float
    cached: bool = False

    def cost_on(self, model: str) -> float:
        pi, po = PRICES[model]
        return (self.prompt_tokens * pi + self.completion_tokens * po) / 1e6

    @property
    def cost(self) -> float:
        return self.cost_on(self.model)


@dataclass
class Ledger:
    calls: list[Call] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, c: Call):
        with self.lock:
            self.calls.append(c)

    def summary(self) -> dict:
        roles: dict = {}
        for c in self.calls:
            r = roles.setdefault(c.role, {"model": SHORT[c.model], "calls": 0, "tokens": 0, "cost": 0.0, "seconds": 0.0})
            r["calls"] += 1
            r["tokens"] += c.prompt_tokens + c.completion_tokens
            r["cost"] += c.cost
            r["seconds"] += c.seconds
        total = sum(c.cost for c in self.calls)
        all_ultra = sum(c.cost_on(ULTRA) for c in self.calls)
        return {"roles": roles, "total_usd": round(total, 4), "all_ultra_usd": round(all_ultra, 4),
                "saved_pct": round(100 * (1 - total / all_ultra), 1) if all_ultra else 0.0,
                "calls": len(self.calls), "tokens": sum(c.prompt_tokens + c.completion_tokens for c in self.calls)}


_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1"),
                         api_key=os.environ["NEBIUS_API_KEY"], timeout=240, max_retries=3)
    return _client


def _cache_key(model: str, messages: list[dict], kwargs: dict) -> Path:
    h = hashlib.sha256(json.dumps([model, messages, kwargs], sort_keys=True).encode()).hexdigest()[:32]
    return CACHE_DIR / f"{h}.json"


def chat(messages: list[dict], role: str, ledger: Ledger, model: str | None = None, thinking: bool = False,
         max_tokens: int = 4096, temperature: float = 0.0, json_mode: bool = False) -> str:
    model = model or ROLES[role]
    kwargs: dict = {"max_tokens": max_tokens, "temperature": temperature, "thinking": thinking, "json": json_mode}
    key = _cache_key(model, messages, kwargs)
    if CACHE_ON and key.exists():
        d = json.loads(key.read_text(encoding="utf-8"))
        ledger.add(Call(role, model, d["pt"], d["ct"], 0.0, cached=True))
        return d["text"]
    req: dict = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                 "extra_body": {"chat_template_kwargs": {"enable_thinking": thinking}}}
    if json_mode:
        req["response_format"] = {"type": "json_object"}
    t = time.time()
    r = client().chat.completions.create(**req)
    dt = time.time() - t
    text = r.choices[0].message.content or ""
    u = r.usage
    ledger.add(Call(role, model, u.prompt_tokens, u.completion_tokens, dt))
    if CACHE_ON:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key.write_text(json.dumps({"text": text, "pt": u.prompt_tokens, "ct": u.completion_tokens}), encoding="utf-8")
    return text


def parse_json(text: str):
    """First JSON object or array in a reply; tolerates fences, prose and <think> blocks."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                continue
    raise ValueError("no JSON in reply: " + text[:200])


def ask_json(prompt: str, role: str, ledger: Ledger, system: str | None = None, model: str | None = None,
             thinking: bool = False, max_tokens: int = 4096):
    # Constrained JSON mode is unreliable on Super/Ultra for nested schemas (it can emit "{}"), so every
    # structured call runs in plain mode with an explicit instruction and a tolerant parser.
    prompt = prompt.rstrip() + "\n\nAnswer with a single JSON object only. No prose before or after it."
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    text = chat(msgs, role, ledger, model=model, thinking=thinking, max_tokens=max_tokens, json_mode=False)
    try:
        out = parse_json(text)
        if out in ({}, []):
            raise ValueError("empty JSON")
        return out
    except ValueError:
        # one repair attempt: ask Nano to fix the JSON
        fixed = chat([{"role": "user", "content": "Return ONLY the valid JSON contained in this text, nothing else:\n\n" + text[:12000]}],
                     "triage", ledger, max_tokens=max_tokens, json_mode=True)
        return parse_json(fixed)


def pmap(fn, items, workers: int = 6):
    """Parallel map that preserves order and turns exceptions into an error dict."""
    def safe(x):
        try:
            return fn(x)
        except Exception as e:  # noqa: BLE001
            return {"_error": f"{type(e).__name__}: {e}"}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(safe, items))
