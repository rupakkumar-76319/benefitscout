/* BenefitScout front end: streams the agent's run over SSE and renders the result. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const esc = (s) => String(s ?? "");
  const LABEL = { eligible: "Eligible", likely: "Likely eligible", need_info: "Need more info", not_eligible: "Not eligible" };
  const VLABEL = { meets: "meets", fails: "fails", unknown: "unknown" };
  const EXAMPLES = {
    bihar: "I am a 20 year old girl from Gaya, Bihar, India. Second year BSc student at a government college. My father is a farmer, family income about 2 lakh per year. We are from the SC category. I got 82% in class 12. I need help paying fees and want to know any scholarship or government scheme I can apply for.",
    us: "I'm a 34 year old single mom in Dayton, Ohio with two kids aged 4 and 7. I work part time at a grocery store making about $1,900 a month. I rent an apartment. I'd like to go back to community college for nursing but can't afford childcare or tuition.",
    uk: "I'm 52, live in Manchester, UK, and stopped working two years ago to look after my mother who has dementia. I care for her about 50 hours a week. My savings are nearly gone and I rent privately. I'm a British citizen.",
    hindi: "मैं 24 साल का हूँ, उत्तर प्रदेश के गोरखपुर का रहने वाला हूँ। मैंने B.Com किया है और अभी बेरोज़गार हूँ। मैं अपना छोटा किराना स्टोर खोलना चाहता हूँ। मेरे पिता किसान हैं, परिवार की सालाना आय लगभग 1.5 लाख है। हम OBC वर्ग से हैं।",
  };

  const text = $("#text"), go = $("#go"), deep = $("#deep"), stages = $("#stages"), activity = $("#activity"), log = $("#log"),
    timer = $("#timer"), results = $("#results"), empty = $("#empty"), drawer = $("#drawer"), runsEl = $("#runs");
  let es = null, t0 = 0, tick = null, current = null;

  $("#examples").addEventListener("click", (e) => { const k = e.target.dataset.ex; if (k) { text.value = EXAMPLES[k]; text.focus(); } });
  go.addEventListener("click", () => start());
  text.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") start(); });
  $("#historyBtn").addEventListener("click", () => { drawer.hidden = false; loadRuns(); });
  $("#closeDrawer").addEventListener("click", () => drawer.hidden = true);

  fetch("/api/status").then(r => r.json()).then(s => {
    if (s.tavily && s.tavily.used != null) $("#tavilyUsage").textContent = `Tavily plan: ${s.tavily.used}/${s.tavily.limit} credits used this month`;
  }).catch(() => {});

  const hash = location.hash.slice(1);
  if (hash) loadRun(hash);

  function addLog(tag, msg, cls) {
    const li = el("li", cls || ""); li.append(el("span", "tag", tag), el("span", "", msg)); log.append(li); log.scrollTop = log.scrollHeight;
  }
  function setStage(name) {
    [...stages.children].forEach(d => { if (d.classList.contains("active")) { d.classList.remove("active"); d.classList.add("done"); } });
    const d = stages.querySelector(`[data-stage="${name}"]`); if (d) { d.classList.add("active"); d.classList.remove("done"); }
  }
  function resetUI() {
    [...stages.children].forEach(d => d.classList.remove("active", "done"));
    stages.hidden = false; activity.hidden = false; log.innerHTML = ""; results.innerHTML = ""; empty.hidden = true;
    t0 = performance.now(); clearInterval(tick); tick = setInterval(() => timer.textContent = ((performance.now() - t0) / 1000).toFixed(1) + " s", 100);
  }
  function stop() { clearInterval(tick); go.disabled = false; go.textContent = "Find what I qualify for"; if (es) { es.close(); es = null; } }

  function start(url) {
    const t = text.value.trim();
    if (!url && t.length < 15) { text.focus(); return; }
    if (es) es.close();
    resetUI(); go.disabled = true; go.textContent = "Working...";
    es = new EventSource(url || `/api/run/stream?text=${encodeURIComponent(t)}&deep=${deep.checked ? 1 : 0}`);
    es.addEventListener("stage", (e) => { const d = JSON.parse(e.data); setStage(d.stage); addLog(d.stage, d.model, "model"); });
    es.addEventListener("log", (e) => addLog("log", JSON.parse(e.data)));
    es.addEventListener("profile", (e) => { const p = JSON.parse(e.data); addLog("profile", `${p.country || "?"}${p.region ? ", " + p.region : ""} · language ${p.language}` + (p.missing_important?.length ? ` · missing: ${p.missing_important.slice(0, 3).join("; ")}` : "")); });
    es.addEventListener("plan", (e) => { const p = JSON.parse(e.data); addLog("plan", `${p.queries.length} queries · prefer ${p.official_domains.slice(0, 4).join(", ")}` + (p.portal?.url ? ` · crawl ${p.portal.url}` : "")); });
    es.addEventListener("search", (e) => { const d = JSON.parse(e.data); const li = el("li", "search"); li.append(el("span", "tag", "search"), el("span", "", `${d.n} results · `)); li.lastChild.append(el("span", "q", d.query)); log.append(li); log.scrollTop = log.scrollHeight; });
    es.addEventListener("crawl", (e) => { const d = JSON.parse(e.data); addLog("crawl", `${d.n} pages from ${d.url}`, "search"); });
    es.addEventListener("candidates", (e) => { const c = JSON.parse(e.data); addLog("triage", `${c.length} candidate schemes: ` + c.slice(0, 6).map(x => x.name).join(" · ") + (c.length > 6 ? " ..." : "")); });
    es.addEventListener("rules", (e) => { const d = JSON.parse(e.data); addLog("rules", d.ok ? `${d.n_criteria} rules · ${d.name}${d.secondary ? " (secondary source)" : ""}` : `skipped · ${d.name || d.url} · ${d.reason || ""}`); });
    es.addEventListener("verdict", (e) => { const d = JSON.parse(e.data); addLog("judge", `${LABEL[d.verdict] || d.verdict} (${Math.round((d.confidence || 0) * 100)}%) · ${d.name}`, "verdict"); });
    es.addEventListener("done", (e) => { current = JSON.parse(e.data); setStage("done"); render(current); location.hash = current.id; });
    es.addEventListener("error", (e) => { if (e.data) { results.append(el("div", "error", "Something failed: " + JSON.parse(e.data))); } stop(); });
    es.addEventListener("close", () => stop());
    es.onerror = () => { if (!current) addLog("conn", "connection lost; retrying"); };
  }

  function loadRun(id) {
    fetch(`/api/run/${id}`).then(r => r.ok ? r.json() : null).then(r => { if (r) { current = r; text.value = r.text; empty.hidden = true; stages.hidden = true; activity.hidden = true; render(r); drawer.hidden = true; } });
  }
  function loadRuns() {
    fetch("/api/runs").then(r => r.json()).then(list => {
      runsEl.innerHTML = "";
      if (!list.length) runsEl.append(el("li", "muted", "No searches yet."));
      list.forEach(r => {
        const li = el("li"); const when = el("div", "when"); when.append(el("span", "", `${r.created} · ${r.country || ""}`));
        if (r.watched) when.append(el("span", "watch", "watching"));
        const c = r.counts || {}; li.append(when, el("div", "", r.text), el("div", "muted", `${c.eligible || 0} eligible · ${c.likely || 0} likely · ${c.need_info || 0} need info`));
        li.onclick = () => loadRun(r.id); runsEl.append(li);
      });
    });
  }

  function render(r) {
    results.innerHTML = "";
    const s = r.summary || {};
    const sum = el("section", "card summary");
    sum.append(el("h2", "", s.headline || (r.schemes.length ? "Here is what we found" : "We could not find schemes for this description")));
    const counts = el("div", "counts");
    ["eligible", "likely", "need_info", "not_eligible"].forEach(k => { const n = r.counts?.[k] || 0; if (n) counts.append(el("span", "c-" + k, `${n} ${LABEL[k].toLowerCase()}`)); });
    sum.append(counts);
    if (s.summary) sum.append(el("p", "", s.summary));
    if (s.first_action) { const f = el("div", "first"); f.append(el("b", "", "Do this first: "), document.createTextNode(s.first_action)); sum.append(f); }
    if (s.questions?.length) { const q = el("ul", "questions"); s.questions.forEach(x => q.append(el("li", "", x))); sum.append(el("p", "muted", "Answering these would sharpen the results:"), q); }
    if (s.translated) { const t = el("div", "translated"); t.append(el("b", "", s.translated.headline || ""), el("p", "", s.translated.summary || ""), el("p", "", s.translated.first_action || "")); sum.append(t); }
    const actions = el("div", "actions");
    const md = el("a", "ghost", "Download report (Markdown)"); md.href = `/api/export/${r.id}.md`; md.download = `benefitscout-${r.id}.md`; md.style.textDecoration = "none"; md.style.display = "inline-block";
    const watch = el("button", "ghost", "Watch this profile"); watch.type = "button";
    fetch("/api/status").then(x => x.json()).then(st => { if (st.watched?.includes(r.id)) watch.textContent = "Watching (click to stop)"; }).catch(() => {});
    watch.onclick = () => { const on = !watch.textContent.startsWith("Watching"); fetch(`/api/watch/${r.id}?on=${on ? 1 : 0}`, { method: "POST" }).then(() => watch.textContent = on ? "Watching (click to stop)" : "Watch this profile"); };
    const re = el("button", "ghost", "Re-check now"); re.type = "button"; re.title = "Run the search again and show what changed";
    re.onclick = () => start(`/api/recheck/${r.id}/stream`);
    const share = el("button", "ghost", "Copy link"); share.type = "button"; share.onclick = () => navigator.clipboard?.writeText(location.origin + "/#" + r.id).then(() => share.textContent = "Copied");
    actions.append(md, watch, re, share); sum.append(actions);
    results.append(sum);

    if (r.changes) {
      const ch = el("section", "card changes"); ch.append(el("h3", "", r.changes.action_needed ? "Something changed since " + r.changes.previous_created : "Re-checked: no material change since " + r.changes.previous_created));
      const ul = el("ul"); (r.changes.bullets || []).forEach(b => ul.append(el("li", "", b)));
      r.changes.new_schemes?.forEach(n => ul.append(el("li", "", "New scheme found: " + n)));
      r.changes.gone_schemes?.forEach(n => ul.append(el("li", "", "No longer found: " + n)));
      r.changes.verdict_changes?.forEach(v => ul.append(el("li", "", `${v.name}: ${LABEL[v.from]} -> ${LABEL[v.to]}`)));
      ch.append(ul); results.append(ch);
    }

    const tpl = $("#schemeTpl");
    r.schemes.forEach(sch => {
      const n = tpl.content.firstElementChild.cloneNode(true);
      $("h3", n).textContent = sch.name;
      $(".meta", n).textContent = [sch.authority, sch.region_scope, sch.deadline ? "deadline " + sch.deadline : null].filter(Boolean).join(" · ");
      const b = $(".badge", n); b.textContent = LABEL[sch.verdict] || sch.verdict; b.classList.add("c-" + sch.verdict);
      $(".conf", n).textContent = `${Math.round((sch.confidence || 0) * 100)}% confident` + (sch.value_to_person ? ` · ${sch.value_to_person} value` : "");
      $(".benefit", n).textContent = esc(sch.benefit) + (sch.benefit_amount ? ` (${sch.benefit_amount})` : "");
      $(".why", n).textContent = sch.why || "";
      const crit = sch.criteria || [], meets = crit.filter(c => c.verdict === "meets").length, fails = crit.filter(c => c.verdict === "fails").length, unk = crit.filter(c => c.verdict === "unknown").length;
      $("summary", n).textContent = `${crit.length} rules checked: ${meets} met${fails ? `, ${fails} failed` : ""}${unk ? `, ${unk} unknown` : ""}`;
      const tb = $("tbody", n);
      crit.forEach(c => {
        const tr = el("tr"); const td1 = el("td", "", c.text); if (c.quote) td1.append(el("q", "quote", c.quote));
        const td2 = el("td", "v v-" + (c.verdict || "unknown"), VLABEL[c.verdict] || c.verdict || "unknown");
        tr.append(td1, td2, el("td", "", c.reason || "")); tb.append(tr);
      });
      if (sch.verdict !== "not_eligible") $("details", n).open = true;
      const ex = $(".extras", n);
      if (sch.next_steps?.length) { const d = el("div"); d.append(el("h4", "", "Next steps")); const ol = el("ol"); sch.next_steps.forEach(x => ol.append(el("li", "", x))); d.append(ol); ex.append(d); }
      if (sch.documents?.length) { const d = el("div"); d.append(el("h4", "", "Documents")); const ul = el("ul"); sch.documents.slice(0, 10).forEach(x => ul.append(el("li", "", x))); d.append(ul); ex.append(d); }
      if (sch.missing_info?.length) { const d = el("div"); d.append(el("h4", "", "We still need to know")); const ul = el("ul"); sch.missing_info.forEach(x => ul.append(el("li", "", x))); d.append(ul); ex.append(d); }
      const src = $(".src", n);
      const a = el("a", "", sch.source_kind === "secondary" ? "Rules read from a secondary source" : "Rules read from the official page"); a.href = sch.url; a.target = "_blank"; a.rel = "noopener"; src.append(a);
      if (sch.source_kind === "secondary") { src.append(el("span", "sec", "verify on official page")); if (sch.official_url) { const o = el("a", "", "official page"); o.href = sch.official_url; o.target = "_blank"; o.rel = "noopener"; src.append(o); } }
      if (sch.apply_url && sch.apply_url !== sch.url) { const ap = el("a", "", "apply here"); ap.href = sch.apply_url; ap.target = "_blank"; ap.rel = "noopener"; src.append(ap); }
      if (sch.last_updated_hint) src.append(el("span", "", "page dated " + sch.last_updated_hint));
      results.append(n);
    });

    if (r.dropped?.length) {
      const d = el("details", "card dropped"); d.append(el("summary", "", `${r.dropped.length} pages were found but not used (not a single-scheme page, or unreadable)`));
      const ul = el("ul"); r.dropped.forEach(x => { const li = el("li"); const a = el("a", "", x.name || x.url); a.href = x.url; a.target = "_blank"; a.rel = "noopener"; li.append(a, document.createTextNode(` · ${x.reason}`)); ul.append(li); }); d.append(ul); results.append(d);
    }

    const c = r.costs || {}, nm = c.nemotron || {}, tv = c.tavily || {};
    const led = el("section", "card ledger"); led.append(el("h3", "", "Who did the work, and what it cost"));
    const big = el("div", "big");
    [[`$${(nm.total_usd ?? 0).toFixed(4)}`, "Nemotron on Token Factory, this run"], [`$${(nm.all_ultra_usd ?? 0).toFixed(4)}`, "same run on Ultra alone"], [`${nm.saved_pct ?? 0}%`, "saved by the Nano / Super / Ultra split"], [`${tv.credits ?? 0}`, `Tavily credits over ${tv.calls ?? 0} calls`], [`${c.seconds ?? 0} s`, "wall clock"]]
      .forEach(([v, l]) => { const d = el("div"); d.append(el("b", "", v), el("span", "", l)); big.append(d); });
    led.append(big);
    const tbl = el("table"); tbl.innerHTML = "<thead><tr><th>Role</th><th>Model</th><th>Calls</th><th>Tokens</th><th>Cost</th></tr></thead>"; const tb = el("tbody");
    Object.entries(nm.roles || {}).forEach(([role, v]) => { const tr = el("tr"); tr.append(el("td", "", role), el("td", "", v.model), el("td", "n", v.calls), el("td", "n", v.tokens.toLocaleString()), el("td", "n", "$" + v.cost.toFixed(4))); tb.append(tr); });
    Object.entries(tv.by_endpoint || {}).forEach(([k, v]) => { const tr = el("tr"); tr.append(el("td", "", "web " + k), el("td", "", "Tavily " + k), el("td", "n", v.calls), el("td", "n", v.results + " results"), el("td", "n", v.credits + " cr")); tb.append(tr); });
    tbl.append(tb); led.append(tbl); results.append(led);
    results.scrollIntoView({ behavior: "smooth", block: "start" });
  }
})();
