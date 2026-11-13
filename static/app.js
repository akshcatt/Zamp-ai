/* ClearAP: vanilla JS single-page app. No build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const app = $("#app");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (x) => (x == null ? "—" : "$" + Number(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const kmoney = (x) => (x == null ? "—" : x >= 1e6 ? "$" + (x / 1e6).toFixed(1) + "M" : x >= 1e4 ? "$" + (x / 1000).toFixed(1) + "k" : money(x));
const toDate = (iso) => new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
const when = (iso) => (iso ? toDate(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
const ago = (iso) => {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - toDate(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  return Math.floor(s / 86400) + " d ago";
};
const secs = (ms) => (ms == null ? "" : ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " s");
const api = async (path, opts = {}) => {
  const r = await fetch(path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r.json();
};
const toast = (msg) => { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 2600); };
const tip = (text) => `<span class="tip" tabindex="0" data-tip="${esc(text)}">?</span>`;
const icon = {
  upload: `<svg viewBox="0 0 24 24"><path d="M12 16V4m0 0-4 4m4-4 4 4"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></svg>`,
  shield: `<svg viewBox="0 0 24 24"><path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6l-8-3Z"/></svg>`,
  search: `<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`,
  download: `<svg viewBox="0 0 24 24"><path d="M12 4v12m0 0-4-4m4 4 4-4"/><path d="M4 20h16"/></svg>`,
  replay: `<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>`,
  play: `<svg viewBox="0 0 24 24"><path d="M7 5v14l11-7z"/></svg>`,
  sun: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`,
  moon: `<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>`,
};

/* ------------------------------------------------------------------ plain-language vocabulary */
const DEC_LABEL = { APPROVE: "Approved", HOLD: "Needs review", REJECT: "Rejected" };
const STATUS_LABEL = { pass: "OK", warn: "Heads-up", fail: "Problem", info: "FYI", skip: "Skipped" };
// Backend queue/route names → the words a non-specialist would use.
const FRIENDLY = {
  "Treasury & Fraud": "Fraud check team", "Controller approval": "Finance manager", "Procurement / Buyer": "Purchasing team",
  "Vendor Management": "Vendor setup team", "AP Specialist": "Accounts payable team",
  "Auto-approved → payment run": "Scheduled for payment", "Rejected: duplicate": "Blocked: already billed",
  "Rejected: blocked vendor": "Blocked: vendor is on hold", "Returned to vendor": "Sent back to vendor", Rejected: "Rejected",
};
const friendly = (n) => FRIENDLY[n] || n || "";
const QUEUE_KEYS = { fraud: "Treasury & Fraud", controller: "Controller approval", procurement: "Procurement / Buyer", vendor_mgmt: "Vendor Management", ap: "AP Specialist" };
const TEAM_WHY = {
  "Treasury & Fraud": "Bank details changed or look suspicious. They phone the vendor on a known number before any money moves.",
  "Controller approval": "Large amounts need a manager's sign-off even when everything matches.",
  "Procurement / Buyer": "The invoice doesn't line up with the purchase order. The person who placed the order confirms it.",
  "Vendor Management": "We don't recognise the vendor. They set it up properly first.",
  "AP Specialist": "The document was hard to read or data is unclear. A person types in or confirms the details.",
};
const SLA_HOURS = { "Treasury & Fraud": 8, "Controller approval": 24, "Procurement / Buyer": 48, "Vendor Management": 48, "AP Specialist": 24 };
const STAGES = [
  ["intake", "File received", "Fingerprint the file so the same one can't be paid twice."],
  ["analyze", "File check", "Is it a digital PDF, a scan, or a phone photo?"],
  ["extract", "AI reads the invoice", "Copies out vendor, amounts and lines. It never makes the decision."],
  ["validate", "Math & dates", "Do the totals add up? Are the dates sensible?"],
  ["vendor", "Vendor check", "A known vendor? Bank account matches the one on file?"],
  ["duplicates", "Repeat check", "Have we already seen or paid this invoice?"],
  ["po_match", "Order match", "Does it match what we ordered and received?"],
  ["decision", "Decision", "Pay it, send it to a person, or reject it."],
];
const MANUAL_MIN = { APPROVE: 12, REJECT: 10, HOLD: 5 }; // assumed minutes of manual work saved per outcome

const decBadge = (d, review, status) => {
  if (!d || status === "running") return `<span class="badge running">Working…</span>`;
  if (d === "HOLD" && review && review !== "pending")
    return `<span class="badge ${review === "approved" ? "APPROVE" : "REJECT"}">${review === "approved" ? "Approved by reviewer" : "Rejected by reviewer"}</span>`;
  return `<span class="badge ${d}">${DEC_LABEL[d] || d}</span>`;
};
const outcome = (r) => (r.decision === "HOLD" && r.review_status === "approved" ? "APPROVE" : r.decision === "HOLD" && r.review_status === "rejected" ? "REJECT" : r.decision);
const slaState = (r) => {
  const h = SLA_HOURS[r.route_to] || 24, waited = (Date.now() - toDate(r.created_at)) / 3.6e6, left = h - waited;
  const fmt = (x) => (x < 1 ? Math.max(1, Math.round(x * 60)) + " min" : Math.round(x) + " h");
  if (left <= 0) return { cls: "late", text: `Overdue by ${fmt(-left)}` };
  if (left < h * 0.25) return { cls: "soon", text: `Due in ${fmt(left)}` };
  return { cls: "", text: `Due in ${fmt(left)}` };
};

let live = null, pollTimer = null;
function stopLive() { if (live) { live.close(); live = null; } clearTimeout(pollTimer); }

/* ------------------------------------------------------------------ theme */
function currentTheme() {
  const t = document.documentElement.dataset.theme;
  return t || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function paintThemeIcon() { $("#theme").innerHTML = currentTheme() === "dark" ? icon.sun : icon.moon; }
$("#theme").onclick = () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("clearap-theme", next); } catch {}
  paintThemeIcon();
};
paintThemeIcon();

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
      $$("nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === key));
      try { await fn(...m.slice(1)); } catch (e) { app.innerHTML = `<div class="card pad">Something went wrong: ${esc(e.message)}</div>`; }
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
    m.innerHTML = `<span class="dot"></span>${h.llm ? `AI on · ${esc(h.model)}` : "AI off · basic reader"}`;
    m.title = h.llm ? `Invoices are read by Google Gemini (${h.model}). Rules still make every decision.` : "No GEMINI_API_KEY set. A simple text parser reads digital PDFs; scans and photos go to a person.";
    const q = $("#queue-count");
    q.hidden = !s.pending_review; q.textContent = s.pending_review;
  } catch {}
}

/* ------------------------------------------------------------------ dashboard */
const dash = { filter: "all", q: "" };
async function viewDashboard() {
  const [s, runs] = await Promise.all([api("/api/stats"), api("/api/runs")]);
  refreshChrome();
  const pending = runs.filter((r) => r.decision === "HOLD" && (!r.review_status || r.review_status === "pending") && r.status !== "running");
  const done = runs.filter((r) => r.status !== "running");
  const savedMin = done.reduce((a, r) => a + (MANUAL_MIN[r.decision] || 0), 0);
  const saved = savedMin >= 60 ? (savedMin / 60).toFixed(1) + " h" : savedMin + " min";
  const late = pending.filter((r) => slaState(r).cls === "late").length;

  app.innerHTML = `
  <section class="hero">
    <span class="eyebrow">Accounts payable · overview</span>
    <h1>${s.processed ? `${s.processed} invoice${s.processed === 1 ? "" : "s"} handled. <em>${s.touchless_rate}% needed no one.</em>` : `Invoices in, decisions out. <em>Upload one to start.</em>`}</h1>
    <p class="muted" style="margin:0;max-width:640px;font-size:16px">ClearAP reads each invoice, checks it against your orders, vendors and past payments, then pays it, sends it to the right person, or rejects it, and tells you why.</p>
    <div class="row">
      ${pending.length ? `<a class="attn" href="#/queue"><b>${pending.length}</b>${pending.length === 1 ? "invoice is" : "invoices are"} waiting for you${late ? ` · ${late} overdue` : ""} →</a>` : `<span class="attn ok"><b>0</b>Nothing waiting for review</span>`}
      <a class="btn primary" href="#/inbox">${icon.upload} Upload an invoice</a>
    </div>
  </section>
  <div class="grid kpis">
    ${kpi("Invoices handled", s.processed, `${kmoney(s.value_processed)} in total`, "", "Every invoice that has finished processing.")}
    ${kpi("Done automatically", s.processed ? s.touchless_rate + "%" : "—", `${s.approved} paid with no one touching them`, "accent", "Share of invoices approved and scheduled for payment without a person stepping in.")}
    ${kpi("Waiting for a person", s.pending_review, pending.length ? `${late ? late + " past deadline" : "all within deadline"}` : "queue is clear", "", "Invoices on hold, each with the team that owns the problem.")}
    ${kpi("Stopped", s.rejected, "repeats, missing info, blocked vendors", "", "Invoices that should not be paid as sent.")}
    ${kpi("Money protected", kmoney(s.value_protected), "repeat bills + suspicious bank changes", "", "The value of duplicate invoices blocked plus invoices held for possible fraud.")}
    ${kpi("Time saved", done.length ? "~" + saved : "—", `avg ${s.avg_duration_ms ? secs(s.avg_duration_ms) : "—"} per invoice`, "", "Estimate: ~12 min of manual work saved per auto-approved invoice, ~10 per rejection, ~5 per hold (the evidence is already gathered).")}
  </div>
  <div class="grid dash-cols">
    <div class="card panel">
      <div class="panel-head"><span class="t">Recent invoices</span>
        <div class="toolbar">
          <label class="search">${icon.search}<input id="q" placeholder="Search vendor, invoice #, PO" value="${esc(dash.q)}"></label>
          <div class="chips" id="chips">${[["all", "All"], ["APPROVE", "Approved"], ["HOLD", "Needs review"], ["REJECT", "Rejected"]].map(([k, l]) => `<button data-f="${k}" class="${dash.filter === k ? "on" : ""}">${l}</button>`).join("")}</div>
          <button class="btn sm ghost" id="csv" title="Download as a spreadsheet (CSV)">${icon.download} Export</button>
        </div></div>
      <div id="runs-table"></div>
    </div>
    <div class="grid">
      <div class="card pad">
        <h3>What happened</h3>
        ${donut(s)}
      </div>
      <div class="card pad">
        <h3>Needs your attention</h3>
        <div class="side-list">${pending.length ? pending.slice(0, 5).map((r) => { const sl = slaState(r);
          return `<a class="item" href="#/runs/${r.id}"><div style="min-width:0"><div class="t">${esc(r.vendor_name || r.filename)}</div><div class="faint small">${esc(friendly(r.route_to))} · ${money(r.total)}</div></div><span class="sla-chip ${sl.cls}">${sl.text}</span></a>`; }).join("")
          : `<div class="faint small">You're all caught up.</div>`}</div>
        ${pending.length ? `<a class="btn sm" style="margin-top:14px" href="#/queue">Open review list →</a>` : ""}
      </div>
      <div class="card pad">
        <h3>Demo controls</h3>
        <p class="muted small" style="margin:0 0 12px">Start over: clears every invoice and puts the records back to how they started.</p>
        <button class="btn sm bad" id="reset">Reset demo data</button>
      </div>
    </div>
  </div>`;

  const paint = () => {
    const q = dash.q.trim().toLowerCase();
    const rows = runs.filter((r) => (dash.filter === "all" || outcome(r) === dash.filter || (dash.filter === "HOLD" && r.decision === "HOLD" && (r.review_status || "pending") === "pending"))
      && (!q || [r.vendor_name, r.invoice_number, r.po_number, r.filename, friendly(r.route_to)].join(" ").toLowerCase().includes(q)));
    $("#runs-table").innerHTML = !runs.length
      ? `<div class="empty"><div class="big">No invoices yet</div>Upload one, or try the sample inbox.<br><br><a class="btn primary" href="#/inbox">${icon.upload} Upload an invoice</a></div>`
      : !rows.length ? `<div class="empty">No invoices match.</div>`
      : `<div class="table-wrap"><table>
        <thead><tr><th>Vendor</th><th class="num">Amount</th><th>Result</th><th>Next step</th><th class="num">Received</th></tr></thead>
        <tbody>${rows.map((r) => `<tr class="click" onclick="location.hash='#/runs/${r.id}'">
          <td class="vcell"><b>${esc(r.vendor_name || r.filename)}</b><div class="faint mono">${esc(r.invoice_number || "no invoice #")}${r.po_number ? " · " + esc(r.po_number) : ""}</div></td>
          <td class="num">${money(r.total)}</td>
          <td>${decBadge(r.decision, r.review_status, r.status)}</td>
          <td class="muted" style="white-space:nowrap">${esc(r.status === "running" ? "Checking now…" : friendly(r.route_to))}</td>
          <td class="num faint" title="${esc(when(r.created_at))}">${ago(r.created_at)}</td></tr>`).join("")}</tbody></table></div>`;
  };
  paint();
  $("#q").oninput = (e) => { dash.q = e.target.value; paint(); };
  $("#chips").onclick = (e) => { const f = e.target.dataset.f; if (!f) return; dash.filter = f; $$("#chips button").forEach((b) => b.classList.toggle("on", b.dataset.f === f)); paint(); };
  $("#csv").onclick = () => exportCsv(runs);
  $("#reset").onclick = async () => {
    if (!confirm("Clear all invoices and reset the records?")) return;
    await api("/api/reset", { method: "POST" }); toast("Demo data reset"); viewDashboard();
  };
  // Keep the overview live while anything is still being processed.
  if (runs.some((r) => r.status === "running")) pollTimer = setTimeout(() => { if ((location.hash || "#/").match(/^#?\/?$/)) viewDashboard(); }, 2000);
}
const kpi = (label, value, sub, cls = "", help = "") => `<div class="card kpi ${cls}"><div class="label">${label}${help ? tip(help) : ""}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
function donut(s) {
  const segs = [["Approved", s.approved, "var(--ok)"], ["Needs review", s.held, "var(--warn)"], ["Rejected", s.rejected, "var(--bad)"]];
  const n = s.processed || 0, C = 2 * Math.PI * 42;
  let off = 0;
  const arcs = n ? segs.filter(([, v]) => v).map(([, v, c]) => { const len = (C * v) / n, a = `<circle r="42" cx="56" cy="56" stroke="${c}" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 56 56)"/>`; off += len; return a; }).join("") : "";
  return `<div class="donut-wrap"><svg class="donut" viewBox="0 0 112 112" role="img" aria-label="Outcome mix">
      <circle r="42" cx="56" cy="56" stroke="var(--surface-3)"/>${arcs}
      <text x="56" y="56" text-anchor="middle" font-size="22" font-weight="500" dy="2">${n}</text><text x="56" y="72" text-anchor="middle" font-size="9.5" fill="var(--ink-3)" style="fill:var(--ink-3)">invoices</text></svg>
    <div class="legend">${segs.map(([l, v, c]) => `<span><span><i style="background:${c}"></i>${l}</span><b>${v}${n ? ` <span class="faint" style="font-weight:400">· ${Math.round((100 * v) / n)}%</span>` : ""}</b></span>`).join("")}</div></div>`;
}
function exportCsv(runs) {
  const cols = [["Received", (r) => r.created_at], ["Vendor", (r) => r.vendor_name], ["Invoice #", (r) => r.invoice_number], ["PO", (r) => r.po_number], ["Amount", (r) => r.total],
    ["Currency", (r) => r.currency], ["Result", (r) => DEC_LABEL[outcome(r)] || ""], ["Next step", (r) => friendly(r.route_to)], ["Reason", (r) => r.headline], ["Seconds", (r) => (r.duration_ms || 0) / 1000], ["File", (r) => r.filename]];
  const cell = (v) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csv = [cols.map(([h]) => h).join(","), ...runs.map((r) => cols.map(([, f]) => cell(f(r))).join(","))].join("\n");
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: `clearap-invoices-${new Date().toISOString().slice(0, 10)}.csv` });
  a.click(); URL.revokeObjectURL(a.href); toast(`Exported ${runs.length} invoices`);
}

/* ------------------------------------------------------------------ inbox */
async function viewInbox() {
  const [samples, runs] = await Promise.all([api("/api/samples"), api("/api/runs")]);
  refreshChrome();
  const lastBy = {};
  runs.slice().reverse().forEach((r) => (lastBy[r.filename] = r));
  const kindCls = (k) => (k.startsWith("Edge") ? "edge" : "");
  app.innerHTML = `
  <div class="page-head">
    <div><span class="eyebrow">Upload</span><h1>Drop in an invoice</h1><p>Any invoice works: a PDF, a scan, or a photo taken on a phone. You'll watch every check happen live, in order.</p></div>
  </div>
  <label class="drop" id="drop"><input type="file" id="file" accept="application/pdf,.pdf,image/jpeg,image/png,image/webp,image/heic,.heic,.jpg,.jpeg,.png,.webp" hidden>
    <span class="ic">${icon.upload}</span>
    <b>Drag an invoice here, or click to choose</b><span class="muted">Up to 20 MB. Photos are straightened and turned into a PDF automatically.</span>
    <span class="formats"><span>PDF</span><span>JPG</span><span>PNG</span><span>WEBP</span><span>HEIC</span></span></label>
  <div class="sec-head"><div><h2>Sample inbox</h2><div class="muted small">Real-looking invoices, each built to test something different. Try them in order for the demo.</div></div>
    <button class="btn" id="runall">${icon.play} Run all ${samples.length}</button></div>
  <div class="grid samples">${samples.map((s) => { const last = lastBy[s.file]; return `
    <div class="card sample">
      <div class="row"><span class="badge kind plain ${kindCls(s.kind)}">${esc(s.kind)}</span>${s.scanned ? `<span class="badge info plain">Scan</span>` : ""}${s.photo ? `<span class="badge info plain">Phone photo</span>` : ""}</div>
      <div><div class="t">${esc(s.title)}</div><div class="v">${esc(s.vendor)}</div></div>
      <div class="x">${esc(s.what_to_expect)}</div>
      ${last ? `<a class="last faint" href="#/runs/${last.id}">Last result: ${decBadge(last.decision, last.review_status, last.status)}</a>` : ""}
      <div class="row"><button class="btn primary sm" data-run="${esc(s.file)}">${icon.play} Run</button><a class="btn sm" target="_blank" href="/api/samples/${encodeURIComponent(s.file)}">${s.photo ? "See photo" : "See PDF"}</a></div>
    </div>`; }).join("")}</div>`;
  $$("[data-run]").forEach((b) => (b.onclick = () => runSample(b.dataset.run)));
  const drop = $("#drop"), file = $("#file");
  file.onchange = () => file.files[0] && upload(file.files[0]);
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (ev) => ev.dataTransfer.files[0] && upload(ev.dataTransfer.files[0]));
  $("#runall").onclick = async () => {
    const b = $("#runall"); b.disabled = true;
    for (const [i, s] of samples.entries()) {
      b.textContent = `Sending ${i + 1} of ${samples.length}…`;
      const fd = new FormData(); fd.append("sample", s.file);
      await api("/api/runs", { method: "POST", body: fd });
      await new Promise((r) => setTimeout(r, 400));
    }
    toast(`${samples.length} invoices sent. Watch them land on the overview.`); location.hash = "#/";
  };
}
async function runSample(name) {
  const fd = new FormData(); fd.append("sample", name);
  const { run_id } = await api("/api/runs", { method: "POST", body: fd });
  location.hash = `#/runs/${run_id}`;
}
async function upload(f) {
  toast(`Uploading ${f.name}…`);
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
    <div style="min-width:0"><div class="crumbs"><a href="#/">Overview</a><span>/</span><span>Invoice</span><span class="mono">${esc(id)}</span></div>
      <h1 id="rh-title">${esc(run.filename)}</h1>
      <div class="meta" id="rh-meta">Received ${when(run.created_at)}</div></div>
    <div class="actions">
      <button class="btn sm" id="replay" title="Play this invoice's checks again, step by step">${icon.replay} Replay</button>
      <a class="btn sm" href="/api/runs/${id}/file" target="_blank">Original file</a>
      <a class="btn sm primary" href="#/inbox">Upload another</a></div>
  </div>
  <div class="progress-top" id="prog"><span style="width:0"></span></div>
  <div class="run-layout">
    <div class="card timeline"><div class="tl-head"><h3 style="margin:0">Steps</h3><span class="faint mono small" id="tl-count"></span></div><div id="timeline"></div></div>
    <div><div id="decision"></div><div class="card panel" id="detail"></div><div id="review"></div></div>
  </div>`;
  STAGES.forEach(([k, l, w]) => { st.stages[k] = { key: k, label: l, what: w, state: "pending" }; st.order.push(k); });

  const apply = (e) => {
    if (e.type === "stage_start") { st.stages[e.stage].state = "running"; if (!st.userSel) st.sel = e.stage; }
    if (e.type === "stage_end") {
      const keep = { label: st.stages[e.stage].label, what: st.stages[e.stage].what };
      Object.assign(st.stages[e.stage], e, keep, { state: e.skipped ? "skipped" : "done" });
      if (e.stage === "decision") { st.decision = e.data; if (!st.userSel) st.sel = "decision"; }
    }
    if (e.type === "run_error") st.error = e.message;
  };
  const render = () => { renderTimeline(st); renderDecision(st); renderDetail(st); };
  $("#replay").onclick = async () => {
    const r = await api(`/api/runs/${id}`);
    STAGES.forEach(([k, l, w]) => (st.stages[k] = { key: k, label: l, what: w, state: "pending" }));
    st.decision = null; st.userSel = false; st.tab = "checks"; render();
    for (const e of r.events) { apply(e); render(); if (e.type === "stage_end") await new Promise((res) => setTimeout(res, 380)); }
    finish(r);
  };
  const finish = async (r) => {
    r = r || (await api(`/api/runs/${id}`));
    st.run = r; st.running = false;
    $("#rh-title").textContent = r.vendor_name ? `${r.vendor_name} · ${r.invoice_number || "no invoice #"}` : r.filename;
    $("#rh-meta").innerHTML = `${esc(r.filename)} · received ${when(r.created_at)} · checked in ${secs(r.duration_ms)} · read by <b>${esc(r.extractor === "gemini" ? "Gemini AI" : r.extractor || "n/a")}</b>`;
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
  st.onSelect = (k) => { st.sel = k; st.userSel = true; if (!["pdf", "compare"].includes(st.tab)) st.tab = "checks"; render(); };
  st.onTab = (t) => { st.tab = t; renderDetail(st); };
  window._st = st;
}

function stageFlags(s) {
  const cs = s.checks || [];
  return { fail: cs.some((c) => c.status === "fail"), warn: cs.some((c) => c.status === "warn") };
}
function renderTimeline(st) {
  const finished = st.order.filter((k) => ["done", "skipped"].includes(st.stages[k].state)).length;
  const p = $("#prog");
  p.firstElementChild.style.width = (100 * finished) / st.order.length + "%";
  p.classList.toggle("done", finished === st.order.length);
  $("#tl-count").textContent = `${finished}/${st.order.length}`;
  $("#timeline").innerHTML = st.order.map((k, i) => {
    const s = st.stages[k], f = stageFlags(s);
    const cls = [s.state, f.fail ? "has-fail" : f.warn ? "has-warn" : "", st.sel === k ? "sel" : ""].join(" ");
    const ic = s.state === "done" ? (f.fail ? "!" : f.warn ? "~" : "✓") : s.state === "skipped" ? "–" : i + 1;
    return `<div class="stage ${cls}" onclick="_st.onSelect('${k}')">
      <div class="ic"><span>${ic}</span></div>
      <div style="min-width:0"><div class="nm">${esc(s.label)}<small>${s.duration_ms != null ? secs(s.duration_ms) : s.state === "running" ? "working" : ""}</small></div>
      ${s.summary ? `<div class="sm fade">${esc(s.summary)}</div>` : `<div class="what">${s.state === "running" ? "Working on it…" : esc(s.what)}</div>`}</div></div>`;
  }).join("");
}

function plainSentence(d) {
  const team = friendly(d.route_name);
  if (d.decision === "APPROVE") return "Everything checked out. It has been recorded and scheduled for payment, and nobody had to touch it.";
  if (d.decision === "HOLD") return `Something needs a person to look at it. It's waiting with the ${team.toLowerCase()}${d.sla ? `, who should answer within ${d.sla.toLowerCase()}` : ""}. Nothing gets paid until they do.`;
  if ((d.route_name || "").includes("duplicate")) return "Don't pay this. We have already been billed for it, so paying it again would mean paying twice.";
  if ((d.route_name || "").includes("Returned")) return "Don't pay this yet. Key details are missing, so we've drafted a polite email asking the vendor to resend it.";
  return "Don't pay this invoice as it was sent.";
}
function renderDecision(st) {
  const el = $("#decision"), d = st.decision;
  if (st.error) { el.innerHTML = `<div class="decision HOLD"><div class="verdict"><span class="word">Error</span></div><div class="headline">${esc(st.error)}</div><p class="plain">Nothing was paid. The invoice was safely passed to the accounts payable team to handle by hand.</p></div>`; return; }
  if (!d) { el.innerHTML = `<div class="decision"><div class="verdict"><span class="badge running">Checking</span><span class="muted">The decision appears here once every step is done…</span></div></div>`; return; }
  const p = d.payment, c = d.check_counts;
  el.innerHTML = `<div class="decision ${d.decision} fade">
    <div class="verdict"><span class="word">${DEC_LABEL[d.decision]}</span><span class="route">${esc(friendly(d.route_name))}</span>
      ${d.sla ? `<span class="sla">· answer within ${esc(d.sla.toLowerCase())}</span>` : ""}
      ${d.money_protected ? `<span class="protected">${icon.shield} ${money(d.money_protected)} protected</span>` : ""}
      ${(d.also_notify || []).length ? `<span class="sla">· also telling ${d.also_notify.map((n) => esc(friendly(n))).join(", ")}</span>` : ""}</div>
    <div class="headline">${esc(d.headline)}</div>
    <p class="plain">${esc(plainSentence(d))}</p>
    <div class="tally"><span class="p">${c.pass} OK</span><span class="w">${c.warn} heads-up</span><span class="f">${c.fail} problem${c.fail === 1 ? "" : "s"}</span><span>${c.info + c.skip} FYI / skipped</span></div>
    <div class="cols">
      <div><h3>Why</h3><ul>${d.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>
      <div><h3>What happens next</h3><ul>${d.next_actions.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
        ${d.partial_approval_amount != null ? `<p style="margin:12px 0 0"><b>Safe to pay now:</b> ${money(d.partial_approval_amount)} ${tip("The part of this invoice that is still within what the purchase order allows.")}</p>` : ""}</div>
    </div>
    ${p ? `<div class="sect"><h3>Payment</h3><dl class="kv">
        <dt>Pay</dt><dd><b>${money(p.amount)}</b> on <b>${esc(p.pay_on)}</b>${p.discount_note ? ` (${esc(p.discount_note)})` : ""}</dd>
        <dt>To</dt><dd>${esc(p.pay_to)}</dd><dt>Terms</dt><dd>${esc(p.terms)} · due ${esc(p.due_date)}</dd>
        <dt>Booked to ${tip("The accounting categories (general ledger accounts) this spend is recorded under.")}</dt><dd>${p.gl_coding.map((g) => `${esc(g.account)}: ${money(g.amount)}`).join(" · ")}${p.cost_center ? ` · ${esc(p.cost_center)}` : ""}</dd></dl></div>` : ""}
    ${d.internal_task ? `<div class="sect"><h3>Task for our team (don't reply to the sender)</h3><div class="task">${esc(d.internal_task)}</div></div>` : ""}
    ${d.vendor_email ? `<div class="sect"><h3 style="display:flex;align-items:center;gap:10px">Email to the vendor, ready to send <button class="btn sm" onclick="navigator.clipboard.writeText(_st.decision.vendor_email.body);toast('Email copied')">Copy</button></h3>
      <div class="email"><div class="hdr"><b>To:</b> ${esc(d.vendor_email.to)}<br><b>Subject:</b> ${esc(d.vendor_email.subject)}</div>${esc(d.vendor_email.body)}</div></div>` : ""}
  </div>`;
}

function renderDetail(st) {
  const el = $("#detail"), s = st.stages[st.sel];
  if (!s) { el.innerHTML = ""; return; }
  const extracted = st.stages.extract.state === "done";
  const tabs = [["checks", "Checks"], ["data", stageDataLabel(s.key)], ["compare", "Side by side"], ["pdf", "Document"], ["json", "Raw data"]]
    .filter(([t]) => (t !== "data" || stageDataLabel(s.key)) && (t !== "compare" || extracted));
  if (!tabs.some(([t]) => t === st.tab)) st.tab = "checks";
  let body = "";
  if (st.tab === "pdf") body = `<iframe class="pdf" src="/api/runs/${st.run.id}/file#view=FitH" title="Invoice document"></iframe>`;
  else if (st.tab === "compare") body = compareHtml(st);
  else if (st.tab === "json") body = `<div class="panel-body"><pre class="json">${esc(JSON.stringify(s, null, 2))}</pre></div>`;
  else if (s.state === "pending" || s.state === "running") body = `<div class="empty">${s.state === "running" ? "This step is running…" : "This step hasn't started yet."}</div>`;
  else if (st.tab === "data") body = `<div class="panel-body">${stageData(s, st)}</div>`;
  else if (s.key === "decision") {
    const fired = st.order.filter((k) => k !== "decision").flatMap((k) => (st.stages[k].checks || []).filter((c) => c.status === "fail" || c.status === "warn").map((c) => ({ ...c, label: `${st.stages[k].label} · ${c.label}` })));
    body = `<div class="panel-body"><p style="margin:0 0 14px">${fired.length ? (fired.length === 1 ? "This check shaped the decision:" : `These ${fired.length} checks shaped the decision:`) : "Every check passed, so nobody needed to step in."}</p>${checksHtml(fired)}
      <p class="faint small" style="margin:16px 0 0">How the decision is made: if any check says <b>reject</b>, it's rejected. Otherwise, if any check needs a person, it goes to the team best placed to fix it. Otherwise it's approved. The AI never makes this call.</p></div>`;
  }
  else body = `<div class="panel-body"><p class="faint small" style="margin:0 0 6px">${esc(s.what)}</p>${s.summary ? `<p style="margin:0 0 14px">${esc(s.summary)}</p>` : ""}${checksHtml(s.checks || []) || `<div class="faint">Nothing to check in this step.</div>`}</div>`;
  el.innerHTML = `<div class="panel-head"><span class="t">${esc(s.label)}</span>
    <div class="tabs">${tabs.map(([t, l]) => `<button class="${st.tab === t ? "on" : ""}" onclick="_st.onTab('${t}')">${l}</button>`).join("")}</div></div>${body}`;
}
const stageDataLabel = (k) => ({ extract: "What the AI read", po_match: "Line by line", vendor: "Vendor record", analyze: "Text found" }[k]);
const checksHtml = (cs) => cs.length ? `<div class="checks">${cs.map((c) => `<div class="check ${c.status}">
  <div><span class="badge ${c.status}">${STATUS_LABEL[c.status]}</span></div>
  <div style="min-width:0"><div class="lbl">${esc(c.label)}</div><div class="det">${esc(c.detail)}</div>
  ${c.outcome ? `<div class="route-tag">→ ${c.outcome === "reject" ? "rejects the invoice" : `sends it to the ${esc(friendly(QUEUE_KEYS[c.route] || "AP Specialist").toLowerCase())}`}</div>` : ""}</div></div>`).join("")}</div>` : "";

const FIELD_LIST = [["vendor_name", "Vendor"], ["invoice_number", "Invoice #"], ["invoice_date", "Invoice date"], ["due_date", "Due date"], ["po_reference", "Order (PO) #"], ["payment_terms", "Payment terms"],
  ["currency", "Currency"], ["bill_to_name", "Billed to"], ["vendor_email", "Sender email"], ["vendor_tax_id", "Tax ID"], ["subtotal", "Subtotal", 1], ["tax_amount", "Tax", 1], ["shipping_amount", "Shipping", 1], ["total_amount", "Total", 1]];
function fieldsHtml(f, conf, list = FIELD_LIST) {
  return `<div class="fields">${list.map(([k, l, isM]) => { const v = f[k], c = conf[k], low = c != null && c < 0.7;
    return `<div class="field ${low ? "low" : ""}"><div class="k"><span>${l}</span>${c != null ? `<span title="How sure the AI is">${Math.round(c * 100)}%</span>` : ""}</div><div class="v ${v == null ? "null" : ""}">${v == null ? "not on the invoice" : esc(isM ? money(v) : v)}</div>
    ${c != null ? `<div class="conf ${low ? "low" : ""}"><span style="width:${c * 100}%"></span></div>` : ""}</div>`; }).join("")}</div>`;
}
function compareHtml(st) {
  const d = st.stages.extract.data || {}, f = d.fields || {}, conf = f.field_confidence || {};
  return `<div class="compare"><iframe src="/api/runs/${st.run.id}/file#view=FitH" title="Invoice document"></iframe>
    <div class="side"><h3>What the AI read</h3><p class="faint small" style="margin:-4px 0 12px">Check these against the document on the left. The percentage is how sure the AI was. Red means a person should double-check.</p>
    ${fieldsHtml(f, conf)}
    <div class="sect"><h3>Lines</h3>${(f.line_items || []).map((l) => `<div class="side-list"><div class="item"><span style="min-width:0">${esc(l.description)} <span class="faint">× ${l.quantity ?? "—"}</span></span><b class="num">${money(l.amount)}</b></div></div>`).join("") || `<div class="faint small">No lines found.</div>`}</div></div></div>`;
}

function stageData(s, st) {
  const d = s.data || {};
  if (s.key === "analyze") return `<dl class="kv"><dt>Characters of text</dt><dd>${d.text_chars}</dd><dt>Type</dt><dd>${d.scanned ? "Image only (scan or photo), so it's read visually" : "Digital PDF with real text"}</dd></dl>
    ${d.text_preview ? `<div class="sect"><h3>Text found (first 1,500 characters)</h3><pre class="json">${esc(d.text_preview)}</pre></div>` : ""}`;
  if (s.key === "vendor" && d.vendor) return `<dl class="kv">${Object.entries(d.vendor).map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    <div class="sect"><h3>Closest name matches</h3><table><tbody>${(d.candidates || []).map((c) => `<tr><td class="mono">${esc(c.vendor_id)}</td><td>${esc(c.name)}</td><td class="num">${Math.round(c.score * 100)}% match</td></tr>`).join("")}</tbody></table></div>`;
  if (s.key === "extract") {
    const f = d.fields || {}, conf = f.field_confidence || {}, m = d.meta || {};
    const bank = f.bank_details || {};
    return `<div class="faint small" style="margin-bottom:12px">Read by <b>${esc(m.extractor === "gemini" ? "Gemini AI" : m.extractor)}</b>${m.model ? ` · ${esc(m.model)}` : ""}${m.latency_ms ? ` · ${secs(m.latency_ms)}` : ""}${m.input_tokens ? ` · ${m.input_tokens} in / ${m.output_tokens} out tokens` : ""}
      ${(d.notes || []).length ? `<br>${d.notes.map(esc).join(" ")}` : ""}</div>
      ${fieldsHtml(f, conf)}
      <div class="field" style="margin-top:10px"><div class="k"><span>Bank details printed on the invoice</span></div><div class="v">${esc([bank.bank_name, bank.routing_number && "routing " + bank.routing_number, bank.account_number && "acct " + bank.account_number].filter(Boolean).join(" · ") || "—")}</div>
        ${f.payment_instruction_notes ? `<div class="faint small" style="margin-top:4px">“${esc(f.payment_instruction_notes)}”</div>` : ""}</div>
      <div class="sect"><h3>Line items</h3><div class="table-wrap"><table><thead><tr><th>Description</th><th class="num">Qty</th><th class="num">Unit price</th><th class="num">Amount</th></tr></thead>
      <tbody>${(f.line_items || []).map((l) => `<tr><td>${esc(l.description)}</td><td class="num">${l.quantity ?? "—"}</td><td class="num">${money(l.unit_price)}</td><td class="num">${money(l.amount)}</td></tr>`).join("")}</tbody></table></div></div>`;
  }
  if (s.key === "po_match") {
    if (!d.lines) return `<div class="faint">No purchase order to compare against.</div>`;
    const b = d.po_state || {};
    const cap = b.po_total || 1, after = (b.prior_billed || 0) + (d.net_amount || 0), scale = Math.max(cap, after) * 1.04;
    const w = (x) => ((100 * x) / scale).toFixed(2) + "%";
    const over = after > cap + 0.01;
    return `<dl class="kv"><dt>Purchase order</dt><dd class="mono">${esc(d.po_number)} (${esc(d.how)})</dd><dt>This invoice</dt><dd>${money(d.net_amount)}</dd><dt>Order says it should be</dt><dd>${money(d.expected)}</dd><dt>Difference</dt><dd>${money(d.variance)} <span class="faint">(we allow up to ${money(d.allowed)})</span></dd></dl>
      <div class="sect"><h3>How much of the order has been billed</h3><div class="balance"><div class="bar">
        <span style="width:${w(b.prior_billed || 0)};background:var(--ink-3)" title="Billed before"></span>
        <span style="width:${w(d.net_amount || 0)};background:${over ? "var(--bad)" : "var(--accent)"}" title="This invoice"></span>
        <div class="cap" style="left:${w(cap)}" title="Order limit"></div></div>
        <div class="legend row" style="margin-top:10px"><span><span><i style="background:var(--ink-3)"></i>Billed before ${money(b.prior_billed)}</span></span><span><span><i style="background:${over ? "var(--bad)" : "var(--accent)"}"></i>This invoice ${money(d.net_amount)}</span></span><span><span><i style="background:var(--ink)"></i>Order limit ${money(b.po_total)}</span></span></div></div></div>
      ${d.inference ? `<div class="sect"><h3>No PO was quoted, so we worked out which order it belongs to</h3><div class="table-wrap"><table><thead><tr><th>PO</th><th>Description</th><th class="num">Items match</th><th class="num">Amount fits</th><th class="num">Goods received</th><th class="num">Overall</th></tr></thead><tbody>
        ${d.inference.candidates.map((c, i) => `<tr${i === 0 ? ' style="font-weight:500"' : ""}><td class="mono">${esc(c.po_number)}</td><td>${esc(c.description)}</td><td class="num">${Math.round(c.line_score * 100)}%</td><td class="num">${Math.round(c.amount_fit * 100)}%</td><td class="num">${Math.round(c.receipt_fit * 100)}%</td><td class="num">${Math.round(c.confidence * 100)}%</td></tr>`).join("")}</tbody></table></div>
        <p class="faint small">Overall = 55% items match + 25% amount fits what's left on the order + 20% goods actually received.</p></div>` : ""}
      <div class="sect"><h3>Each line vs. the order</h3><div class="table-wrap"><table><thead><tr><th>Invoice line</th><th>Matching order line</th><th class="num">Qty (ordered)</th><th class="num">Price (ordered)</th><th class="num">Difference</th><th>Booked to</th></tr></thead><tbody>
        ${d.lines.map((l) => `<tr><td>${esc(l.invoice)}</td><td>${l.po_line ? esc(l.po_line) : `<span class="badge warn">not on the order</span>`}<div class="faint" style="font-size:11.5px">${Math.round(l.score * 100)}% match</div></td>
          <td class="num">${l.qty ?? "—"}${l.po_qty != null ? ` <span class="faint">(${l.po_qty})</span>` : ""}</td><td class="num">${money(l.inv_price)}${l.po_price != null ? ` <span class="faint">(${money(l.po_price)})</span>` : ""}</td>
          <td class="num" style="color:${Math.abs(l.variance) > 0.005 ? "var(--warn)" : "inherit"}">${money(l.variance)}</td><td class="faint">${esc(l.gl || "")}</td></tr>`).join("")}</tbody></table></div></div>`;
  }
  return "";
}

const QUICK_NOTES = {
  "Treasury & Fraud": ["Called vendor on the number on file. They did NOT change bank details. Fraud attempt.", "Called vendor on the number on file. They confirmed the change; vendor setup team updated."],
  "Controller approval": ["Reviewed against budget. Approved.", "Over budget. Rejecting; please re-quote."],
  "Procurement / Buyer": ["Buyer confirmed the extra quantity was agreed. Approve.", "Not agreed with the buyer. Rejecting the overage."],
  "Vendor Management": ["New vendor verified and set up. Approve.", "Could not verify this vendor. Reject."],
  "AP Specialist": ["Checked fields against the document. All correct.", "Document unreadable. Asking vendor to resend."],
};
function renderReview(st) {
  const el = $("#review"), r = st.run;
  const audit = (r.audit || []).length ? `<div class="card pad" style="margin-top:16px"><h3>History</h3><ul class="audit">${r.audit.map((a) => `<li><span class="mono faint">${when(a.ts)}</span> · <b>${esc(a.actor)}</b> · ${esc(a.action.replace(/_/g, " "))}${a.note ? `: ${esc(a.note)}` : ""}</li>`).join("")}</ul></div>` : "";
  const pending = r.decision === "HOLD" && (!r.review_status || r.review_status === "pending");
  const notes = QUICK_NOTES[r.route_to] || [];
  el.innerHTML = (pending ? `<div class="card pad" style="margin-top:16px"><h3>Your decision · ${esc(friendly(r.route_to))}</h3><div class="review">
      <p class="muted small" style="margin:0 0 10px">${esc(TEAM_WHY[r.route_to] || "")}</p>
      <textarea id="note" placeholder="Required: what did you check, and why are you approving or rejecting it? This is saved to the history."></textarea>
      ${notes.length ? `<div class="quick">${notes.map((n) => `<button type="button" data-n="${esc(n)}">${esc(n.length > 58 ? n.slice(0, 56) + "…" : n)}</button>`).join("")}</div>` : ""}
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn ok" id="ap">Approve &amp; record</button><button class="btn bad" id="rj">Reject</button></div></div></div>` : "") + audit;
  if (pending) {
    $$(".quick button", el).forEach((b) => (b.onclick = () => { $("#note").value = b.dataset.n; $("#note").focus(); }));
    const act = async (action) => {
      try { st.run = await api(`/api/runs/${r.id}/action`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, note: $("#note").value }) });
        toast(action === "approve" ? "Approved and recorded" : "Rejected"); renderReview(st); refreshChrome(); }
      catch (e) { toast(e.message); }
    };
    $("#ap").onclick = () => act("approve"); $("#rj").onclick = () => act("reject");
  }
}

/* ------------------------------------------------------------------ queue */
async function viewQueue() {
  const runs = (await api("/api/runs")).filter((r) => r.decision === "HOLD" && (!r.review_status || r.review_status === "pending") && r.status !== "running");
  refreshChrome();
  const groups = {};
  runs.forEach((r) => (groups[r.route_to] = groups[r.route_to] || []).push(r));
  const late = runs.filter((r) => slaState(r).cls === "late").length;
  app.innerHTML = `<div class="page-head"><div><span class="eyebrow">Needs review</span><h1>${runs.length ? `${runs.length} invoice${runs.length === 1 ? "" : "s"} need${runs.length === 1 ? "s" : ""} a person` : "Nothing to review"}</h1>
    <p>Only the unusual ones end up here. Each goes to the team best placed to sort it out, with the evidence already attached${late ? `. <b style="color:var(--bad)">${late} past deadline.</b>` : "."}</p></div></div>
  ${runs.length ? Object.values(QUEUE_KEYS).filter((q) => groups[q]).map((q) => `<div class="card panel q-group">
    <div class="panel-head"><div><div class="t">${esc(friendly(q))} <span class="badge HOLD">${groups[q].length} waiting</span></div><div class="faint small" style="margin-top:2px">${esc(TEAM_WHY[q] || "")}</div></div></div>
    <div class="table-wrap"><table><tbody>${groups[q].sort((a, b) => a.created_at.localeCompare(b.created_at)).map((r) => { const sl = slaState(r);
      return `<tr class="click" onclick="location.hash='#/runs/${r.id}'"><td style="width:26%"><b style="font-weight:500">${esc(r.vendor_name || r.filename)}</b><div class="faint mono">${esc(r.invoice_number || "")}</div></td><td class="muted">${esc(r.headline)}</td><td class="num">${money(r.total)}</td><td class="num"><span class="sla-chip ${sl.cls}">${sl.text}</span></td></tr>`; }).join("")}</tbody></table></div></div>`).join("")
    : `<div class="card empty"><div class="big">You're all caught up</div>When an invoice needs a person, it will show up here.</div>`}`;
}

/* ------------------------------------------------------------------ data */
async function viewData() {
  const d = await api("/api/reference");
  const vname = Object.fromEntries(d.vendors.map((v) => [v.vendor_id, v.legal_name]));
  app.innerHTML = `<div class="page-head"><div><span class="eyebrow">Records</span><h1>Orders, vendors &amp; payments</h1><p>What every invoice is checked against. The payment record updates each time an invoice is approved, which is how repeat bills and over-billing get caught.</p></div></div>
  <div class="card panel"><div class="panel-head"><span class="t">Purchase orders ${tip("A purchase order (PO) is what we agreed to buy, at what price, before the invoice arrives.")}</span></div><div class="table-wrap"><table>
    <thead><tr><th>PO</th><th>Vendor</th><th>What for</th><th>Status</th><th class="num">Order value</th><th class="num">Billed so far</th><th>Used up</th><th>Check type ${tip("2-way: invoice vs order. 3-way: invoice vs order vs goods actually received.")}</th></tr></thead>
    <tbody>${d.purchase_orders.map((p) => { const pc = Math.min(100, (100 * p.prior_billed) / p.po_total);
      return `<tr><td class="mono">${esc(p.po_number)}</td><td>${esc(p.vendor)}</td><td class="muted">${esc(p.description)}<div class="faint" style="font-size:12px">${esc(p.buyer)}</div></td>
      <td><span class="badge ${p.status === "open" ? "pass" : "info"}">${esc(p.status)}</span></td><td class="num">${money(p.po_total)}</td><td class="num">${money(p.prior_billed)}</td>
      <td><div class="progress ${pc >= 99.9 ? "full" : ""}"><span style="width:${pc}%"></span></div><div class="faint" style="font-size:11.5px">${pc.toFixed(0)}% · ${money(p.remaining)} left</div></td>
      <td class="faint">${p.receipt_required ? "3-way" : "2-way"}</td></tr>`; }).join("")}</tbody></table></div></div>
  <div class="card panel" style="margin-top:16px"><div class="panel-head"><span class="t">Payment record ${tip("Every invoice we've recorded for payment (the invoice ledger).")}</span><span class="faint small">${d.ledger.length} entries</span></div><div class="table-wrap"><table>
    <thead><tr><th>Invoice #</th><th>Vendor</th><th>PO</th><th class="num">Before tax</th><th class="num">Total</th><th>Status</th><th>Recorded</th><th>Payment ref</th><th>From</th></tr></thead>
    <tbody>${d.ledger.slice().reverse().map((l) => `<tr><td class="mono">${esc(l.invoice_number)}</td><td>${esc(vname[l.vendor_id] || l.vendor_id)}</td><td class="mono">${esc(l.po_number || "")}</td>
      <td class="num">${money(l.net_amount)}</td><td class="num">${money(l.total_amount)}</td><td><span class="badge ${l.status === "paid" ? "pass" : "info"}">${esc(l.status)}</span></td>
      <td>${esc(l.posted_at)}</td><td class="mono">${esc(l.payment_ref || "")}</td><td>${l.source_run_id === "seed" ? `<span class="faint">earlier</span>` : `<a href="#/runs/${l.source_run_id}" style="text-decoration:underline">this app</a>`}</td></tr>`).join("")}</tbody></table></div></div>
  <div class="card panel" style="margin-top:16px"><div class="panel-head"><span class="t">Vendors</span></div><div class="table-wrap"><table>
    <thead><tr><th>ID</th><th>Name</th><th>Tax ID</th><th>Status</th><th>Terms</th><th>Bank on file</th><th>Email domain</th><th>Phone on file</th></tr></thead>
    <tbody>${d.vendors.map((v) => `<tr><td class="mono">${esc(v.vendor_id)}</td><td>${esc(v.legal_name)}</td><td class="mono">${esc(v.tax_id)}</td><td><span class="badge ${v.status === "active" ? "pass" : "fail"}">${esc(v.status)}</span></td><td>${esc(v.payment_terms)}</td><td>${esc(v.bank)}</td><td class="mono">${esc(v.email_domain)}</td><td class="mono">${esc(v.phone_on_file)}</td></tr>`).join("")}</tbody></table></div></div>`;
}

/* ------------------------------------------------------------------ rules */
async function viewRules() {
  const p = await api("/api/policy");
  const fields = [
    ["price_tolerance_pct", "Price wiggle room (%)", "How far an invoice can be off the order before a person looks"],
    ["price_tolerance_abs", "Price wiggle room ($)", "…and in dollars. Whichever is smaller wins."],
    ["high_value_threshold", "Manager sign-off above ($)", "Big invoices get a second look even when everything matches"],
    ["inferred_po_min_confidence", "How sure we must be of a guessed PO", "0 to 1. Below this, the buyer confirms which order it is"],
    ["min_field_confidence", "How sure the AI must be", "0 to 1. Key fields below this go to a person to double-check"],
    ["fuzzy_duplicate_window_days", "Look-alike repeat window (days)", "Same vendor and same amount within this many days counts as a likely repeat"],
    ["max_invoice_age_days", "Too old after (days)", "Older invoices are held for a person"],
  ];
  const steps = [["File received", "We take a fingerprint of the file. Photos are straightened and turned into a PDF.", "System"],
    ["File check", "Is it a digital PDF, a scan, or a photo? Scans and photos are read visually.", "System"],
    ["AI reads the invoice", "Gemini copies vendor, dates, amounts and lines into a fixed form and says how sure it is about each one. Blanks stay blank.", "AI"],
    ["Math & dates", "Totals add up, tax is sensible, dates make sense, and every key value really appears on the document.", "Rules"],
    ["Vendor check", "A known, active vendor? Bank account matches what's on file? Any \"we've changed banks\" wording or look-alike email address?", "Rules"],
    ["Repeat check", "Same file, same invoice number written differently (INV 2026/0417 = INV-2026-0417), or same vendor and amount recently.", "Rules"],
    ["Order match", "Find the purchase order (quoted or worked out), compare each line's price and quantity, check goods arrived, and check the order isn't over-billed.", "Rules"],
    ["Decision", "Any reason to reject → Rejected. Anything a person must see → sent to the right team with a deadline. Otherwise → approved and scheduled for payment.", "Rules"]];
  const terms = [["Purchase order (PO)", "What we agreed to buy and at what price, before the invoice arrives."], ["3-way match", "Invoice, order and goods-received note all agree."],
    ["Goods receipt (GRN)", "Proof that the warehouse actually received the items."], ["Tolerance", "Small differences we accept automatically, like a few cents of rounding."],
    ["Duplicate", "The same bill sent twice, sometimes with the number written a bit differently."], ["Remit-to bank", "The account the money is sent to. We only ever pay the one on file."],
    ["GL code", "The accounting category the spend is booked under, e.g. \"6120 Breakroom\"."], ["Split billing", "One order billed across several invoices that together exceed the order."],
    ["Touchless", "Handled from start to finish with no person involved."]];
  app.innerHTML = `<div class="page-head"><div><span class="eyebrow">How it works</span><h1>The AI reads. The rules decide.</h1><p>The AI's only job is to copy the details off the invoice. Every decision about money comes from clear rules you can see and change below.</p></div></div>
  <div class="card pad"><h2>Eight steps, every invoice</h2><div class="flow">
    ${steps.map(([t, d, w], i) => `<div class="step ${w === "AI" ? "ai" : ""}"><span class="n">${String(i + 1).padStart(2, "0")}</span><b>${t}</b><p>${d}</p><span class="who">${w}</span></div>`).join("")}
  </div></div>
  <div class="grid outcomes" style="margin-top:16px">
    <div class="card pad"><span class="badge APPROVE">Approved</span><p class="muted">Everything checked out, or small differences were within the wiggle room. Recorded and scheduled for payment, taking early-payment discounts when the terms offer one.</p></div>
    <div class="card pad"><span class="badge HOLD">Needs review</span><p class="muted">A person needs to look. It goes to the team that can fix it: <b>fraud check</b> (bank changes), <b>finance manager</b> (large amounts), <b>purchasing</b> (order problems), <b>vendor setup</b> (unknown vendor), or <b>AP</b> (hard to read).</p></div>
    <div class="card pad"><span class="badge REJECT">Rejected</span><p class="muted">Must not be paid as sent: already billed, vendor blocked, not an invoice, or missing the invoice number or date. Vendors get a ready-to-send email explaining why.</p></div>
  </div>
  <div class="card pad" style="margin-top:16px"><h2>Your rules</h2><p class="muted" style="margin:-6px 0 18px">Changes apply to the next invoice. Try setting the price wiggle room to 0.5% and re-running the Pinnacle sample: it flips from approved to needs review.</p>
    <form class="policy-form" id="pf">${fields.map(([k, l, h]) => `<label>${l}<input type="number" step="any" name="${k}" value="${p[k]}"><small>${h}</small></label>`).join("")}
      <label>Approve guessed POs automatically?<select name="inferred_po_auto_approve"><option value="true" ${p.inferred_po_auto_approve ? "selected" : ""}>Yes, if we're sure enough</option><option value="false" ${!p.inferred_po_auto_approve ? "selected" : ""}>No, always ask the buyer</option></select><small>Strict "no PO, no pay" companies pick No</small></label>
    </form><button class="btn primary" style="margin-top:18px" id="save">Save rules</button></div>
  <div class="grid" style="margin-top:16px;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))">
    <div class="card pad"><h2>Principles</h2><ul class="principles muted">
      <li><b>The AI reads, rules decide.</b> The AI only copies details. It can't approve a payment, and every decision points to named checks with evidence.</li>
      <li><b>Never trust an invoice's bank details.</b> We only pay the account on file. Changes go through vendor setup with a phone call, never via an invoice.</li>
      <li><b>Look across invoices, not just one.</b> Repeat bills and over-billing only show up when you compare with what's been paid before.</li>
      <li><b>Slow down rather than break.</b> If the AI is down, a simple text reader takes over. If nothing can read it, a person does. Weak data is never auto-approved.</li>
      <li><b>People handle the exceptions, with context.</b> Every hold has an owner, a deadline, the evidence and a suggested next step. Every human action is recorded.</li></ul></div>
    <div class="card pad"><h2>Words you'll see</h2><dl class="glossary">${terms.map(([t, d]) => `<div><dt>${t}</dt><dd>${d}</dd></div>`).join("")}</dl></div>
  </div>`;
  $("#save").onclick = async () => {
    const fd = new FormData($("#pf")), body = {};
    for (const [k, v] of fd.entries()) body[k] = k === "inferred_po_auto_approve" ? v === "true" : Number(v);
    await api("/api/policy", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    toast("Saved. Applies to the next invoice.");
  };
}

/* ------------------------------------------------------------------ command palette (⌘K / Ctrl+K) */
const cmd = { items: [], shown: [], i: 0 };
async function openCmd() {
  $("#cmd").hidden = false; const inp = $("#cmd-input"); inp.value = ""; inp.focus();
  cmd.items = [
    ...[["Overview", "#/"], ["Upload an invoice", "#/inbox"], ["Needs review", "#/queue"], ["Records: orders, vendors, payments", "#/data"], ["How it works & rules", "#/rules"]].map(([t, h]) => ({ grp: "Go to", t, s: "", go: () => (location.hash = h) })),
    { grp: "Actions", t: "Switch light / dark", s: "", go: () => $("#theme").click() },
  ];
  paintCmd();
  try {
    const [runs, samples] = await Promise.all([api("/api/runs"), api("/api/samples")]);
    cmd.items.push(...runs.map((r) => ({ grp: "Invoices", t: `${r.vendor_name || r.filename}${r.invoice_number ? " · " + r.invoice_number : ""}`, s: `${DEC_LABEL[outcome(r)] || "Working"} · ${money(r.total)}`, k: [r.po_number, friendly(r.route_to)].join(" "), go: () => (location.hash = `#/runs/${r.id}`) })),
      ...samples.map((s) => ({ grp: "Try a sample", t: s.title, s: s.vendor, go: () => runSample(s.file) })));
    paintCmd();
  } catch {}
}
function closeCmd() { $("#cmd").hidden = true; }
function paintCmd() {
  const words = $("#cmd-input").value.toLowerCase().split(/\s+/).filter(Boolean);
  cmd.shown = cmd.items.filter((it) => words.every((w) => `${it.t} ${it.s} ${it.k || ""} ${it.grp}`.toLowerCase().includes(w))).slice(0, 40);
  cmd.i = Math.min(cmd.i, Math.max(0, cmd.shown.length - 1));
  let last = "";
  $("#cmd-list").innerHTML = cmd.shown.length ? cmd.shown.map((it, i) => { const g = it.grp !== last ? `<div class="grp">${esc(it.grp)}</div>` : ""; last = it.grp;
    return `${g}<div class="opt ${i === cmd.i ? "on" : ""}" data-i="${i}"><span>${esc(it.t)}</span><span class="s">${esc(it.s)}</span></div>`; }).join("") : `<div class="empty">No matches</div>`;
}
$("#cmd-input").addEventListener("input", () => { cmd.i = 0; paintCmd(); });
$("#cmd-list").addEventListener("click", (e) => { const o = e.target.closest(".opt"); if (o) { closeCmd(); cmd.shown[+o.dataset.i].go(); } });
$("#cmd").addEventListener("click", (e) => { if (e.target.id === "cmd") closeCmd(); });
$("#open-cmd").onclick = openCmd;
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#cmd").hidden ? openCmd() : closeCmd(); return; }
  if ($("#cmd").hidden) {
    if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); openCmd(); }
    return;
  }
  if (e.key === "Escape") closeCmd();
  else if (e.key === "ArrowDown") { e.preventDefault(); cmd.i = Math.min(cmd.i + 1, cmd.shown.length - 1); paintCmd(); $(".opt.on")?.scrollIntoView({ block: "nearest" }); }
  else if (e.key === "ArrowUp") { e.preventDefault(); cmd.i = Math.max(cmd.i - 1, 0); paintCmd(); $(".opt.on")?.scrollIntoView({ block: "nearest" }); }
  else if (e.key === "Enter" && cmd.shown[cmd.i]) { e.preventDefault(); const it = cmd.shown[cmd.i]; closeCmd(); it.go(); }
});

refreshChrome();
router();
