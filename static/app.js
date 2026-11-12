/* ClearAP: vanilla JS single-page app. No build step. */
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (x) => (x == null ? "—" : "$" + Number(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const kmoney = (x) => (x >= 100000 ? "$" + (x / 1000).toFixed(0) + "k" : money(x));
const when = (iso) => (iso ? new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
const secs = (ms) => (ms == null ? "" : ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " s");
const api = async (path, opts = {}) => {
  const r = await fetch(path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r.json();
};
const toast = (msg) => { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 2600); };
const DEC_LABEL = { APPROVE: "Approved", HOLD: "On hold", REJECT: "Rejected" };
const STATUS_LABEL = { pass: "Pass", warn: "Note", fail: "Fail", info: "Info", skip: "Skipped" };
const QUEUE_KEYS = { fraud: "Treasury & Fraud", controller: "Controller approval", procurement: "Procurement / Buyer", vendor_mgmt: "Vendor Management", ap: "AP Specialist" };
const decBadge = (d, review) => {
  if (!d) return `<span class="badge running">Running…</span>`;
  let b = `<span class="badge ${d}">${DEC_LABEL[d] || d}</span>`;
  if (d === "HOLD" && review && review !== "pending") b += ` <span class="badge ${review === "approved" ? "APPROVE" : "REJECT"}">${review === "approved" ? "Released" : "Rejected"} by reviewer</span>`;
  return b;
};

let live = null; // active EventSource
function stopLive() { if (live) { live.close(); live = null; } }

/* ------------------------------------------------------------------ router */
const routes = [
  [/^#?\/?$/, viewDashboard, "dashboard"],
  [/^#\/inbox$/, viewInbox, "inbox"],
  [/^#\/runs\/([\w-]+)(?:\?(.*))?$/, viewRun, null],
  [/^#\/queue$/, viewQueue, "queue"],
  [/^#\/data$/, viewData, "data"],
  [/^#\/rules$/, viewRules, "rules"],
];
async function router() {
  stopLive();
  const h = location.hash || "#/";
  for (const [re, fn, key] of routes) {
    const m = h.match(re);
    if (m) {
      document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === key));
      try { await fn(...m.slice(1)); } catch (e) { app.innerHTML = `<div class="card pad">Error: ${esc(e.message)}</div>`; }
      window.scrollTo(0, 0);
      return;
    }
  }
  location.hash = "#/";
}
window.addEventListener("hashchange", router);

async function refreshChrome() {
  try {
    const [h, s] = await Promise.all([api("/api/health"), api("/api/stats")]);
    const m = $("#mode");
    m.className = "mode" + (h.llm ? "" : " off");
    m.innerHTML = `<span class="dot"></span>${h.llm ? `Gemini · ${esc(h.model)}` : "Heuristic mode (no API key)"}`;
    const q = $("#queue-count");
    q.hidden = !s.pending_review; q.textContent = s.pending_review;
  } catch {}
}

/* ------------------------------------------------------------------ dashboard */
async function viewDashboard() {
  const [s, runs] = await Promise.all([api("/api/stats"), api("/api/runs")]);
  refreshChrome();
  const pct = (n) => (s.processed ? (100 * n) / s.processed : 0);
  app.innerHTML = `
  <div class="page-head">
    <div><h1>Accounts payable · overview</h1><p>Every invoice the process has handled: what it decided, why, and where human attention is needed.</p></div>
    <div style="display:flex;gap:8px"><a class="btn primary" href="#/inbox">+ Process an invoice</a></div>
  </div>
  <div class="grid kpis">
    ${kpi("Invoices processed", s.processed, `${money(s.value_processed)} total value`)}
    ${kpi("Touchless rate", s.processed ? s.touchless_rate + "%" : "—", `${s.approved} approved with no human touch`, "accent")}
    ${kpi("Waiting for review", s.pending_review, Object.keys(s.queues).length ? Object.entries(s.queues).map(([k, v]) => `${v} ${k}`).join(" · ") : "Queue is clear")}
    ${kpi("Rejected", s.rejected, "duplicates, missing info, blocked vendors")}
    ${kpi("Exposure stopped", money(s.value_protected), "duplicate + fraud-risk invoices held back")}
    ${kpi("Avg. processing time", s.avg_duration_ms ? secs(s.avg_duration_ms) : "—", "per invoice, end to end")}
  </div>
  <div class="grid dash-cols" style="margin-top:16px">
    <div class="card panel">
      <div class="panel-head"><span class="t">Recent runs</span><span class="faint">${runs.length} run(s)</span></div>
      ${runs.length ? `<div class="table-wrap"><table>
        <thead><tr><th>When</th><th>Vendor · invoice</th><th class="num">Amount</th><th>PO</th><th>Decision</th><th>Routed to</th><th class="num">Time</th></tr></thead>
        <tbody>${runs.map((r) => `<tr class="click" onclick="location.hash='#/runs/${r.id}'">
          <td class="faint" style="white-space:nowrap">${when(r.created_at)}</td>
          <td><b>${esc(r.vendor_name || r.filename)}</b><div class="faint mono">${esc(r.invoice_number || "no invoice #")}</div></td>
          <td class="num">${money(r.total)}</td>
          <td class="mono">${esc(r.po_number || "—")}</td>
          <td>${decBadge(r.decision, r.review_status)}</td>
          <td class="muted">${esc(r.route_to || "")}</td>
          <td class="num faint nowrap">${secs(r.duration_ms)}</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty">No invoices processed yet.<br><br><a class="btn primary" href="#/inbox">Process your first invoice</a></div>`}
    </div>
    <div class="grid">
      <div class="card pad">
        <h3>Decision mix</h3>
        <div class="mixbar"><span style="width:${pct(s.approved)}%;background:var(--ok)"></span><span style="width:${pct(s.held)}%;background:var(--warn)"></span><span style="width:${pct(s.rejected)}%;background:var(--bad)"></span></div>
        <div class="legend"><span><i style="background:var(--ok)"></i>Approved ${s.approved}</span><span><i style="background:var(--warn)"></i>On hold ${s.held}</span><span><i style="background:var(--bad)"></i>Rejected ${s.rejected}</span></div>
      </div>
      <div class="card pad">
        <h3>Review queues</h3>
        ${Object.values(QUEUE_KEYS).map((q) => `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)"><span>${q}</span><b>${s.queues[q] || 0}</b></div>`).join("")}
        <a class="btn sm" style="margin-top:12px" href="#/queue">Open review queue →</a>
      </div>
      <div class="card pad">
        <h3>Demo controls</h3>
        <p class="muted" style="margin:0 0 10px;font-size:13px">Resets the ledger to its seeded state and clears run history.</p>
        <button class="btn sm bad" id="reset">Reset demo data</button>
      </div>
    </div>
  </div>`;
  $("#reset").onclick = async () => {
    if (!confirm("Reset ledger and clear all runs?")) return;
    await api("/api/reset", { method: "POST" }); toast("Demo data reset"); viewDashboard();
  };
}
const kpi = (label, value, sub, cls = "") => `<div class="card kpi ${cls}"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;

/* ------------------------------------------------------------------ inbox */
async function viewInbox() {
  const samples = await api("/api/samples");
  refreshChrome();
  const kindCls = (k) => (k.startsWith("Edge") ? "edge" : k.startsWith("Extra") ? "extra" : "");
  app.innerHTML = `
  <div class="page-head">
    <div><h1>Process an invoice</h1><p>Drop any invoice PDF (digital or scanned), or pick one from the test inbox. Each run streams live, stage by stage.</p></div>
    <button class="btn" id="runall">Run entire inbox</button>
  </div>
  <label class="drop" id="drop"><input type="file" id="file" accept="application/pdf,.pdf" hidden>
    <b>Drop an invoice PDF here</b><span class="muted">or click to choose a file (max 15 MB)</span></label>
  <h2 style="margin-top:26px">Test inbox</h2>
  <div class="grid samples">${samples.map((s) => `
    <div class="card sample">
      <div class="row"><span class="badge kind ${kindCls(s.kind)}">${esc(s.kind)}</span>${s.scanned ? `<span class="badge info">Scanned image</span>` : ""}</div>
      <div><div class="t">${esc(s.title)}</div><div class="v">${esc(s.vendor)}</div></div>
      <div class="x">${esc(s.what_to_expect)}</div>
      <div class="row"><button class="btn primary sm" data-run="${esc(s.file)}">Run ▸</button><a class="btn sm" target="_blank" href="/api/samples/${encodeURIComponent(s.file)}">View PDF</a></div>
    </div>`).join("")}</div>`;
  app.querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => runSample(b.dataset.run)));
  const drop = $("#drop"), file = $("#file");
  file.onchange = () => file.files[0] && upload(file.files[0]);
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (ev) => ev.dataTransfer.files[0] && upload(ev.dataTransfer.files[0]));
  $("#runall").onclick = async () => {
    $("#runall").disabled = true;
    for (const s of samples) {
      const fd = new FormData(); fd.append("sample", s.file);
      await api("/api/runs", { method: "POST", body: fd });
      await new Promise((r) => setTimeout(r, 400));
    }
    toast(`${samples.length} invoices queued`); location.hash = "#/";
  };
}
async function runSample(name) {
  const fd = new FormData(); fd.append("sample", name);
  const { run_id } = await api("/api/runs", { method: "POST", body: fd });
  location.hash = `#/runs/${run_id}`;
}
async function upload(f) {
  const fd = new FormData(); fd.append("file", f);
  try { const { run_id } = await api("/api/runs", { method: "POST", body: fd }); location.hash = `#/runs/${run_id}`; }
  catch (e) { toast(e.message); }
}

/* ------------------------------------------------------------------ run view */
async function viewRun(id, query) {
  const run = await api(`/api/runs/${id}`);
  const q = new URLSearchParams(query || "");
  const st = { run, stages: {}, order: [], sel: q.get("stage"), userSel: !!q.get("stage"), tab: q.get("tab") || "checks", decision: null, running: run.status === "running" };
  app.innerHTML = `
  <div class="run-head">
    <div><div class="faint" style="font-size:12.5px"><a href="#/">Dashboard</a> / Run <span class="mono">${esc(id)}</span></div>
      <h1 id="rh-title">${esc(run.filename)}</h1>
      <div class="meta" id="rh-meta">Received ${when(run.created_at)} · source: ${esc(run.source)}</div></div>
    <div style="display:flex;gap:8px">
      <button class="btn sm" id="replay" title="Re-animate this run from its event log">↻ Replay</button>
      <a class="btn sm" href="/api/runs/${id}" target="_blank">Raw JSON</a>
      <a class="btn sm" href="#/inbox">Process another</a></div>
  </div>
  <div class="run-layout">
    <div class="card timeline" id="timeline"></div>
    <div><div id="decision"></div><div class="card panel" id="detail"></div><div id="review"></div></div>
  </div>`;
  const STAGES = [["intake", "Intake"], ["analyze", "Document analysis"], ["extract", "AI extraction"], ["validate", "Validation"], ["vendor", "Vendor verification"], ["duplicates", "Duplicate detection"], ["po_match", "PO & receipt matching"], ["decision", "Decision"]];
  STAGES.forEach(([k, l]) => { st.stages[k] = { key: k, label: l, state: "pending" }; st.order.push(k); });

  const apply = (e) => {
    if (e.type === "stage_start") { st.stages[e.stage].state = "running"; if (!st.userSel) st.sel = e.stage; }
    if (e.type === "stage_end") {
      Object.assign(st.stages[e.stage], e, { state: e.skipped ? "skipped" : "done" });
      if (e.stage === "decision") { st.decision = e.data; if (!st.userSel) st.sel = "decision"; }
    }
    if (e.type === "run_error") st.error = e.message;
  };
  const render = () => { renderTimeline(st); renderDecision(st); renderDetail(st); };
  $("#replay").onclick = async () => {
    const r = await api(`/api/runs/${id}`);
    STAGES.forEach(([k]) => (st.stages[k] = { key: k, label: st.stages[k].label, state: "pending" }));
    st.decision = null; st.userSel = false; render();
    for (const e of r.events) { apply(e); render(); if (e.type === "stage_end") await new Promise((res) => setTimeout(res, 380)); }
    finish(r);
  };
  const finish = async (r) => {
    r = r || (await api(`/api/runs/${id}`));
    st.run = r; st.running = false;
    $("#rh-title").textContent = r.vendor_name ? `${r.vendor_name} · ${r.invoice_number || "no invoice #"}` : r.filename;
    $("#rh-meta").innerHTML = `${esc(r.filename)} · received ${when(r.created_at)} · ${secs(r.duration_ms)} end to end · extractor: <b>${esc(r.extractor || "n/a")}</b>`;
    render(); renderReview(st); refreshChrome();
  };

  if (st.running) {
    render();
    live = new EventSource(`/api/runs/${id}/events`);
    live.onmessage = (m) => {
      const e = JSON.parse(m.data);
      apply(e); render();
      if (e.type === "run_end" || e.type === "run_error") { stopLive(); finish(); }
    };
    live.onerror = () => { stopLive(); setTimeout(finish, 800); };
  } else {
    run.events.forEach(apply);
    if (!st.userSel) st.sel = "decision";
    finish(run);
  }
  st.onSelect = (k) => { st.sel = k; st.userSel = true; st.tab = "checks"; render(); };
  st.onTab = (t) => { st.tab = t; renderDetail(st); };
  window._st = st;
}

function stageFlags(s) {
  const cs = s.checks || [];
  return { fail: cs.some((c) => c.status === "fail"), warn: cs.some((c) => c.status === "warn") };
}
function renderTimeline(st) {
  $("#timeline").innerHTML = st.order.map((k, i) => {
    const s = st.stages[k], f = stageFlags(s);
    const cls = [s.state, f.fail ? "has-fail" : f.warn ? "has-warn" : "", st.sel === k ? "sel" : ""].join(" ");
    const icon = s.state === "done" ? (f.fail ? "!" : f.warn ? "~" : "✓") : s.state === "skipped" ? "–" : i + 1;
    return `<div class="stage ${cls}" onclick="_st.onSelect('${k}')">
      <div class="ic"><span>${icon}</span></div>
      <div><div class="nm">${esc(s.label)}<small>${s.duration_ms != null ? secs(s.duration_ms) : s.state === "running" ? "running" : ""}</small></div>
      ${s.summary ? `<div class="sm fade">${esc(s.summary)}</div>` : s.state === "running" ? `<div class="sm">Working…</div>` : ""}</div></div>`;
  }).join("");
}

function renderDecision(st) {
  const el = $("#decision"), d = st.decision;
  if (st.error) { el.innerHTML = `<div class="decision HOLD"><div class="verdict"><span class="word">ERROR</span></div><div class="headline">${esc(st.error)}</div><div class="muted">The run failed safely and was routed to an AP specialist.</div></div>`; return; }
  if (!d) { el.innerHTML = `<div class="decision pending"><div class="verdict"><span class="badge running">Processing</span><span class="muted">Decision appears when all stages complete…</span></div></div>`; return; }
  const p = d.payment;
  el.innerHTML = `<div class="decision ${d.decision} fade">
    <div class="verdict"><span class="word">${DEC_LABEL[d.decision].toUpperCase()}</span><span class="route">${esc(d.route_name || "")}</span>
      ${d.sla ? `<span class="sla">· SLA ${esc(d.sla)}</span>` : ""}
      ${d.money_protected ? `<span class="protected">🛡 ${money(d.money_protected)} held back</span>` : ""}
      ${(d.also_notify || []).length ? `<span class="sla">· also notify: ${d.also_notify.map(esc).join(", ")}</span>` : ""}</div>
    <div class="headline">${esc(d.headline)}</div>
    <div class="faint" style="font-size:12.5px">${d.check_counts.pass} passed · ${d.check_counts.warn} notes · ${d.check_counts.fail} failed · ${d.check_counts.info + d.check_counts.skip} info/skipped</div>
    <div class="cols">
      <div><h3>Why</h3><ul>${d.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>
      <div><h3>Next</h3><ul>${d.next_actions.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
        ${d.partial_approval_amount != null ? `<p style="margin:10px 0 0"><b>Suggested partial approval:</b> ${money(d.partial_approval_amount)}</p>` : ""}</div>
    </div>
    ${p ? `<div class="sect"><h3>Payment plan</h3><dl class="kv">
        <dt>Pay</dt><dd><b>${money(p.amount)}</b> on <b>${esc(p.pay_on)}</b>${p.discount_note ? ` (${esc(p.discount_note)})` : ""}</dd>
        <dt>To</dt><dd>${esc(p.pay_to)}</dd><dt>Terms</dt><dd>${esc(p.terms)} · due ${esc(p.due_date)}</dd>
        <dt>GL coding</dt><dd>${p.gl_coding.map((g) => `${esc(g.account)}: ${money(g.amount)}`).join(" · ")}${p.cost_center ? ` · ${esc(p.cost_center)}` : ""}</dd></dl></div>` : ""}
    ${d.internal_task ? `<div class="sect"><h3>Internal task (do not contact the invoice sender)</h3><div class="task">${esc(d.internal_task)}</div></div>` : ""}
    ${d.vendor_email ? `<div class="sect"><h3>Drafted vendor email <button class="btn sm" style="margin-left:8px" onclick="navigator.clipboard.writeText(_st.decision.vendor_email.body);toast('Email copied')">Copy</button></h3>
      <div class="email"><div class="hdr"><b>To:</b> ${esc(d.vendor_email.to)}<br><b>Subject:</b> ${esc(d.vendor_email.subject)}</div>${esc(d.vendor_email.body)}</div></div>` : ""}
  </div>`;
}

function renderDetail(st) {
  const el = $("#detail"), s = st.stages[st.sel];
  if (!s) { el.innerHTML = ""; return; }
  const tabs = [["checks", "Checks"], ["data", stageDataLabel(s.key)], ["pdf", "Invoice PDF"], ["json", "Trace JSON"]].filter(([t]) => t !== "data" || stageDataLabel(s.key));
  let body = "";
  if (st.tab === "pdf") body = `<iframe class="pdf" src="/api/runs/${st.run.id}/file#view=FitH"></iframe>`;
  else if (st.tab === "json") body = `<div class="panel-body"><pre class="json">${esc(JSON.stringify(s, null, 2))}</pre></div>`;
  else if (s.state === "pending" || s.state === "running") body = `<div class="empty">${s.state === "running" ? "This stage is running…" : "Not started yet."}</div>`;
  else if (st.tab === "data") body = `<div class="panel-body">${stageData(s, st)}</div>`;
  else if (s.key === "decision") {
    const fired = st.order.filter((k) => k !== "decision").flatMap((k) => (st.stages[k].checks || []).filter((c) => c.status === "fail" || c.status === "warn").map((c) => ({ ...c, label: `${st.stages[k].label} · ${c.label}` })));
    body = `<div class="panel-body"><p style="margin:0 0 12px">${fired.length ? `The ${fired.length} check(s) that shaped this decision:` : "Every check passed. Nothing needed a human."}</p>${checksHtml(fired)}`;
  }
  else body = `<div class="panel-body">${s.summary ? `<p style="margin:0 0 12px">${esc(s.summary)}</p>` : ""}${checksHtml(s.checks || []) || `<div class="faint">No checks in this stage.</div>`}
    ${s.key === "decision" ? `<p class="faint" style="margin-top:12px">The decision is a pure function of the checks: any reject → REJECT; otherwise any hold → HOLD (routed to the most specialised owner); otherwise APPROVE.</p>` : ""}</div>`;
  el.innerHTML = `<div class="panel-head"><span class="t">${esc(s.label)}</span>
    <div class="tabs">${tabs.map(([t, l]) => `<button class="${st.tab === t ? "on" : ""}" onclick="_st.onTab('${t}')">${l}</button>`).join("")}</div></div>${body}`;
}
const stageDataLabel = (k) => ({ extract: "Extracted fields", po_match: "Line matching", vendor: "Vendor", analyze: "Text layer" }[k]);
const checksHtml = (cs) => cs.length ? `<div class="checks">${cs.map((c) => `<div class="check ${c.status}">
  <div><span class="badge ${c.status}">${STATUS_LABEL[c.status]}</span></div>
  <div><div class="lbl">${esc(c.label)}</div><div class="det">${esc(c.detail)}</div>
  ${c.outcome ? `<div class="route-tag">→ ${c.outcome === "reject" ? "rejects the invoice" : `holds for ${esc(QUEUE_KEYS[c.route] || "review")}`}</div>` : ""}</div></div>`).join("")}</div>` : "";

function stageData(s, st) {
  const d = s.data || {};
  if (s.key === "analyze") return `<dl class="kv"><dt>Characters</dt><dd>${d.text_chars}</dd><dt>Scanned</dt><dd>${d.scanned ? "Yes, image only" : "No"}</dd></dl>
    ${d.text_preview ? `<div class="sect"><h3>Text layer (first 1,500 chars)</h3><pre class="json">${esc(d.text_preview)}</pre></div>` : ""}`;
  if (s.key === "vendor" && d.vendor) return `<dl class="kv">${Object.entries(d.vendor).map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    <div class="sect"><h3>Name-match candidates</h3><table><tbody>${(d.candidates || []).map((c) => `<tr><td class="mono">${esc(c.vendor_id)}</td><td>${esc(c.name)}</td><td class="num">${c.score.toFixed(2)}</td></tr>`).join("")}</tbody></table></div>`;
  if (s.key === "extract") {
    const f = d.fields || {}, conf = f.field_confidence || {}, m = d.meta || {};
    const show = [["vendor_name", "Vendor"], ["invoice_number", "Invoice #"], ["invoice_date", "Invoice date"], ["due_date", "Due date"], ["po_reference", "PO reference"], ["payment_terms", "Terms"], ["currency", "Currency"], ["bill_to_name", "Bill to"], ["vendor_email", "Sender email"], ["vendor_tax_id", "Tax ID"], ["subtotal", "Subtotal", 1], ["tax_amount", "Tax", 1], ["shipping_amount", "Shipping", 1], ["total_amount", "Total", 1]];
    const bank = f.bank_details || {};
    return `<div class="faint" style="margin-bottom:10px;font-size:12.5px">Extractor <b>${esc(m.extractor)}</b>${m.model ? ` · ${esc(m.model)}` : ""}${m.latency_ms ? ` · ${secs(m.latency_ms)}` : ""}${m.input_tokens ? ` · ${m.input_tokens} in / ${m.output_tokens} out tokens` : ""}
      ${(d.notes || []).length ? `<br>${d.notes.map(esc).join(" ")}` : ""}</div>
      <div class="fields">${show.map(([k, l, isM]) => { const v = f[k], c = conf[k];
        return `<div class="field"><div class="k">${l}</div><div class="v ${v == null ? "null" : ""}">${v == null ? "not on document" : esc(isM ? money(v) : v)}</div>
        ${c != null ? `<div class="conf ${c < 0.7 ? "low" : ""}" title="confidence ${c}"><span style="width:${c * 100}%"></span></div>` : ""}</div>`; }).join("")}
        <div class="field" style="grid-column:span 2"><div class="k">Remit-to bank</div><div class="v">${esc([bank.bank_name, bank.routing_number && "routing " + bank.routing_number, bank.account_number && "acct " + bank.account_number].filter(Boolean).join(" · ") || "—")}</div>
        ${f.payment_instruction_notes ? `<div class="faint" style="font-size:12.5px;margin-top:4px">“${esc(f.payment_instruction_notes)}”</div>` : ""}</div></div>
      <div class="sect"><h3>Line items</h3><div class="table-wrap"><table><thead><tr><th>Description</th><th class="num">Qty</th><th class="num">Unit price</th><th class="num">Amount</th></tr></thead>
      <tbody>${(f.line_items || []).map((l) => `<tr><td>${esc(l.description)}</td><td class="num">${l.quantity ?? "—"}</td><td class="num">${money(l.unit_price)}</td><td class="num">${money(l.amount)}</td></tr>`).join("")}</tbody></table></div></div>`;
  }
  if (s.key === "po_match") {
    if (!d.lines) return `<div class="faint">No PO match performed.</div>`;
    const b = d.po_state || {};
    const cap = b.po_total || 1, after = (b.prior_billed || 0) + (d.net_amount || 0), scale = Math.max(cap, after) * 1.04;
    const w = (x) => ((100 * x) / scale).toFixed(2) + "%";
    const over = after > cap + 0.01;
    return `<dl class="kv"><dt>PO</dt><dd class="mono">${esc(d.po_number)} (${esc(d.how)})</dd><dt>Invoice net</dt><dd>${money(d.net_amount)}</dd><dt>PO value of billed lines</dt><dd>${money(d.expected)}</dd><dt>Variance</dt><dd>${money(d.variance)} (tolerance ${money(d.allowed)})</dd></dl>
      <div class="sect"><h3>PO balance</h3><div class="balance"><div class="bar">
        <span style="width:${w(b.prior_billed || 0)};background:#98A2B3" title="Previously billed"></span>
        <span style="width:${w(d.net_amount || 0)};background:${over ? "var(--bad)" : "var(--brand)"}" title="This invoice"></span>
        <div class="cap" style="left:${w(cap)}" title="PO value"></div></div>
        <div class="legend" style="margin-top:6px"><span><i style="background:#98A2B3"></i>Previously billed ${money(b.prior_billed)}</span><span><i style="background:${over ? "var(--bad)" : "var(--brand)"}"></i>This invoice ${money(d.net_amount)}</span><span><i style="background:var(--text)"></i>PO value ${money(b.po_total)}</span></div></div></div>
      ${d.inference ? `<div class="sect"><h3>PO inference: candidate scoring</h3><div class="table-wrap"><table><thead><tr><th>PO</th><th>Description</th><th class="num">Lines</th><th class="num">Amount fit</th><th class="num">Receipts</th><th class="num">Confidence</th></tr></thead><tbody>
        ${d.inference.candidates.map((c, i) => `<tr${i === 0 ? ' style="font-weight:600"' : ""}><td class="mono">${esc(c.po_number)}</td><td>${esc(c.description)}</td><td class="num">${c.line_score.toFixed(2)}</td><td class="num">${c.amount_fit.toFixed(2)}</td><td class="num">${c.receipt_fit.toFixed(2)}</td><td class="num">${c.confidence.toFixed(2)}</td></tr>`).join("")}</tbody></table></div>
        <p class="faint" style="font-size:12.5px">confidence = 0.55 × line similarity + 0.25 × amount fit vs remaining balance + 0.20 × goods-receipt support</p></div>` : ""}
      <div class="sect"><h3>Line-level match</h3><div class="table-wrap"><table><thead><tr><th>Invoice line</th><th>Matched PO line</th><th class="num">Qty (PO)</th><th class="num">Price (PO)</th><th class="num">Variance</th><th>GL</th></tr></thead><tbody>
        ${d.lines.map((l) => `<tr><td>${esc(l.invoice)}</td><td>${l.po_line ? esc(l.po_line) : `<span class="badge warn">not on PO</span>`}<div class="faint" style="font-size:11.5px">match score ${l.score}</div></td>
          <td class="num">${l.qty ?? "—"}${l.po_qty != null ? ` <span class="faint">(${l.po_qty})</span>` : ""}</td><td class="num">${money(l.inv_price)}${l.po_price != null ? ` <span class="faint">(${money(l.po_price)})</span>` : ""}</td>
          <td class="num" style="color:${Math.abs(l.variance) > 0.005 ? "var(--warn)" : "inherit"}">${money(l.variance)}</td><td class="faint">${esc(l.gl || "")}</td></tr>`).join("")}</tbody></table></div></div>`;
  }
  return "";
}

function renderReview(st) {
  const el = $("#review"), r = st.run;
  const audit = (r.audit || []).length ? `<div class="card pad" style="margin-top:16px"><h3>Audit trail</h3><ul class="audit">${r.audit.map((a) => `<li><span class="mono">${when(a.ts)}</span> · <b>${esc(a.actor)}</b> · ${esc(a.action.replace(/_/g, " "))}${a.note ? `: ${esc(a.note)}` : ""}</li>`).join("")}</ul></div>` : "";
  const pending = r.decision === "HOLD" && (!r.review_status || r.review_status === "pending");
  el.innerHTML = (pending ? `<div class="card pad" style="margin-top:16px"><h3>Reviewer decision · ${esc(r.route_to)}</h3><div class="review">
      <textarea id="note" placeholder="Required for the audit trail: what did you verify, and why are you releasing or rejecting it?"></textarea>
      <div style="display:flex;gap:8px"><button class="btn ok" id="ap">Approve &amp; post</button><button class="btn bad" id="rj">Reject</button></div></div></div>` : "") + audit;
  if (pending) {
    const act = async (action) => {
      try { st.run = await api(`/api/runs/${r.id}/action`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, note: $("#note").value }) });
        toast(action === "approve" ? "Approved and posted to ledger" : "Rejected"); renderReview(st); refreshChrome(); }
      catch (e) { toast(e.message); }
    };
    $("#ap").onclick = () => act("approve"); $("#rj").onclick = () => act("reject");
  }
}

/* ------------------------------------------------------------------ queue */
async function viewQueue() {
  const runs = (await api("/api/runs")).filter((r) => r.decision === "HOLD" && (!r.review_status || r.review_status === "pending"));
  refreshChrome();
  const groups = {};
  runs.forEach((r) => (groups[r.route_to] = groups[r.route_to] || []).push(r));
  app.innerHTML = `<div class="page-head"><div><h1>Review queue</h1><p>Only exceptions reach a human, each routed to the team that owns that kind of problem, with the evidence attached.</p></div></div>
  ${runs.length ? Object.values(QUEUE_KEYS).filter((q) => groups[q]).map((q) => `<div class="card panel" style="margin-bottom:16px">
    <div class="panel-head"><span class="t">${esc(q)}</span><span class="badge HOLD">${groups[q].length} waiting</span></div>
    <table><tbody>${groups[q].map((r) => `<tr class="click" onclick="location.hash='#/runs/${r.id}'"><td style="width:28%"><b>${esc(r.vendor_name || r.filename)}</b><div class="faint mono">${esc(r.invoice_number || "")}</div></td><td>${esc(r.headline)}</td><td class="num">${money(r.total)}</td><td class="faint">${when(r.created_at)}</td></tr>`).join("")}</tbody></table></div>`).join("")
    : `<div class="card empty">Nothing waiting for review. 🎉</div>`}`;
}

/* ------------------------------------------------------------------ data */
async function viewData() {
  const d = await api("/api/reference");
  const vname = Object.fromEntries(d.vendors.map((v) => [v.vendor_id, v.legal_name]));
  app.innerHTML = `<div class="page-head"><div><h1>Purchase orders, vendors &amp; ledger</h1><p>The procurement-side system of record the process matches against. The ledger updates as invoices are approved, which is how split billing and duplicates are caught across runs.</p></div></div>
  <div class="card panel"><div class="panel-head"><span class="t">Purchase orders</span></div><div class="table-wrap"><table>
    <thead><tr><th>PO</th><th>Vendor</th><th>Description</th><th>Status</th><th class="num">Value</th><th class="num">Billed</th><th>Consumed</th><th>Receipt</th></tr></thead>
    <tbody>${d.purchase_orders.map((p) => { const pc = Math.min(100, (100 * p.prior_billed) / p.po_total);
      return `<tr><td class="mono">${esc(p.po_number)}</td><td>${esc(p.vendor)}</td><td class="muted">${esc(p.description)}<div class="faint" style="font-size:12px">${esc(p.buyer)}</div></td>
      <td><span class="badge ${p.status === "open" ? "pass" : "info"}">${esc(p.status)}</span></td><td class="num">${money(p.po_total)}</td><td class="num">${money(p.prior_billed)}</td>
      <td><div class="progress ${pc >= 99.9 ? "full" : ""}"><span style="width:${pc}%"></span></div><div class="faint" style="font-size:11.5px">${pc.toFixed(0)}% · ${money(p.remaining)} left</div></td>
      <td class="faint">${p.receipt_required ? "3-way" : "2-way"}</td></tr>`; }).join("")}</tbody></table></div></div>
  <div class="card panel" style="margin-top:16px"><div class="panel-head"><span class="t">Invoice ledger</span><span class="faint">${d.ledger.length} entries</span></div><div class="table-wrap"><table>
    <thead><tr><th>Invoice #</th><th>Vendor</th><th>PO</th><th class="num">Net</th><th class="num">Total</th><th>Status</th><th>Posted</th><th>Payment ref</th><th>Source</th></tr></thead>
    <tbody>${d.ledger.slice().reverse().map((l) => `<tr><td class="mono">${esc(l.invoice_number)}</td><td>${esc(vname[l.vendor_id] || l.vendor_id)}</td><td class="mono">${esc(l.po_number || "")}</td>
      <td class="num">${money(l.net_amount)}</td><td class="num">${money(l.total_amount)}</td><td><span class="badge ${l.status === "paid" ? "pass" : "info"}">${esc(l.status)}</span></td>
      <td>${esc(l.posted_at)}</td><td class="mono">${esc(l.payment_ref || "")}</td><td>${l.source_run_id === "seed" ? `<span class="faint">history</span>` : `<a href="#/runs/${l.source_run_id}">run</a>`}</td></tr>`).join("")}</tbody></table></div></div>
  <div class="card panel" style="margin-top:16px"><div class="panel-head"><span class="t">Vendor master</span></div><div class="table-wrap"><table>
    <thead><tr><th>ID</th><th>Legal name</th><th>Tax ID</th><th>Status</th><th>Terms</th><th>Bank on file</th><th>Domain</th><th>Phone on file</th></tr></thead>
    <tbody>${d.vendors.map((v) => `<tr><td class="mono">${esc(v.vendor_id)}</td><td>${esc(v.legal_name)}</td><td class="mono">${esc(v.tax_id)}</td><td><span class="badge ${v.status === "active" ? "pass" : "fail"}">${esc(v.status)}</span></td><td>${esc(v.payment_terms)}</td><td>${esc(v.bank)}</td><td class="mono">${esc(v.email_domain)}</td><td class="mono">${esc(v.phone_on_file)}</td></tr>`).join("")}</tbody></table></div></div>`;
}

/* ------------------------------------------------------------------ rules */
async function viewRules() {
  const p = await api("/api/policy");
  const fields = [
    ["price_tolerance_pct", "Price tolerance (%)", "Max invoice-vs-PO variance, as a percentage"],
    ["price_tolerance_abs", "Price tolerance ($)", "…and in dollars. The tighter of the two applies."],
    ["high_value_threshold", "Controller sign-off above ($)", "Matched invoices above this still need a controller"],
    ["inferred_po_min_confidence", "Inferred-PO confidence floor", "0–1. Below this, an inferred PO goes to the buyer"],
    ["min_field_confidence", "Extraction confidence floor", "0–1. Critical fields below this go to a human"],
    ["fuzzy_duplicate_window_days", "Near-duplicate window (days)", "Same vendor + same amount within N days"],
    ["max_invoice_age_days", "Stale invoice after (days)", "Older invoices are held"],
  ];
  app.innerHTML = `<div class="page-head"><div><h1>Rules &amp; process</h1><p>How the process decides, in plain language. The AI reads the document; deterministic rules make every decision that touches money.</p></div></div>
  <div class="card pad"><h2>The process, step by step</h2><div class="flow">
    ${[["Intake", "Fingerprint the file (SHA-256) so exact resubmissions are caught.", "System"],
       ["Document analysis", "Detect digital vs scanned. Scans are read visually, and the text cross-check is switched off.", "System"],
       ["AI extraction", "Gemini reads the invoice into a strict schema and scores its confidence per field. Blanks stay blank; nothing is guessed.", "AI"],
       ["Validation", "Arithmetic, tax plausibility, dates, required fields, and grounding: every key value must appear verbatim in the PDF text.", "Rules"],
       ["Vendor verification", "Match to the vendor master. Check status, tax ID, and remit-to bank against the account on file. Look for change-of-bank language and look-alike domains.", "Rules"],
       ["Duplicate detection", "Exact file, normalised invoice number (INV 2026/0417 = INV-2026-0417), items already in the review queue, and same-amount near-duplicates.", "Rules"],
       ["PO & receipt matching", "Find the PO, quoted or inferred, then match line by line on price and quantity, check goods received (3-way), and check cumulative billing against the PO cap.", "Rules"],
       ["Decision", "Any reject → Reject. Any hold → Hold, routed to the owning team with an SLA. Otherwise → Approve, post, and schedule payment.", "Rules"]]
      .map(([t, d, w]) => `<div class="step ${w === "AI" ? "ai" : ""}"><b>${t}</b><p>${d}</p><div class="who">${w}</div></div>`).join("")}
  </div></div>
  <div class="grid outcomes" style="margin-top:16px">
    <div class="card pad"><span class="badge APPROVE">Approve</span><p class="muted">Every check passed, or minor variances fell within tolerance. Posted to the ledger and scheduled for payment, with early-payment discounts captured where the terms allow.</p></div>
    <div class="card pad"><span class="badge HOLD">Hold</span><p class="muted">Something needs a human, routed to the team that owns it: <b>Treasury &amp; Fraud</b> (bank changes), <b>Controller</b> (high value), <b>Procurement</b> (PO issues), <b>Vendor Mgmt</b> (unknown vendor), <b>AP</b> (data quality).</p></div>
    <div class="card pad"><span class="badge REJECT">Reject</span><p class="muted">Must not be paid as submitted: a duplicate, a blocked vendor, not an invoice, or missing an invoice number or date. Returns to the vendor come with a drafted email.</p></div>
  </div>
  <div class="card pad" style="margin-top:16px"><h2>Tolerances &amp; thresholds</h2><p class="muted" style="margin-top:-6px">Changes apply to the next run. Try tightening the price tolerance to 0.5% and re-running the Pinnacle invoice.</p>
    <form class="policy-form" id="pf">${fields.map(([k, l, h]) => `<label>${l}<input type="number" step="any" name="${k}" value="${p[k]}"><small>${h}</small></label>`).join("")}
      <label>Auto-approve inferred POs<select name="inferred_po_auto_approve" style="padding:8px;border-radius:8px;border:1px solid var(--border-strong)"><option value="true" ${p.inferred_po_auto_approve ? "selected" : ""}>Yes, if above the confidence floor</option><option value="false" ${!p.inferred_po_auto_approve ? "selected" : ""}>No, always ask the buyer</option></select><small>A "no PO, no pay" policy would set this to No</small></label>
    </form><button class="btn primary" style="margin-top:14px" id="save">Save rules</button></div>
  <div class="card pad" style="margin-top:16px"><h2>Design principles</h2><ul class="principles muted">
    <li><b>The AI reads, rules decide.</b> The model only transcribes. It can't approve a payment, and every decision traces back to named checks with evidence.</li>
    <li><b>Never trust the invoice for payment details.</b> Payments only go to the bank account in the vendor master. Changes arrive through vendor management with a callback, never from an invoice.</li>
    <li><b>Look across invoices, not just at one.</b> Split billing and resubmitted duplicates are invisible on a single document, so the ledger is part of every decision.</li>
    <li><b>Degrade, don't fail.</b> If the AI is unavailable, the process falls back to a text parser. If nothing can read the document, it goes to a human. It never auto-approves on weak data.</li>
    <li><b>Humans handle exceptions, with context.</b> Each hold names an owner, an SLA, the evidence, and a suggested action. Every human action is audited.</li></ul></div>`;
  $("#save").onclick = async () => {
    const fd = new FormData($("#pf")), body = {};
    for (const [k, v] of fd.entries()) body[k] = k === "inferred_po_auto_approve" ? v === "true" : Number(v);
    await api("/api/policy", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    toast("Rules saved. They apply to the next run.");
  };
}

refreshChrome();
router();
