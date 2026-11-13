/* NoCap: vanilla JS single-page app. No build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const app = $("#app");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (x) => (x == null ? "—" : "₹" + Number(x).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
// Compact rupees the way Indian finance teams say them: K (thousand), L (lakh), Cr (crore).
const short = (x) => (x >= 1e7 ? (x / 1e7).toFixed(2).replace(/\.?0+$/, "") + " Cr" : x >= 1e5 ? (x / 1e5).toFixed(1).replace(/\.0$/, "") + " L" : x >= 1e3 ? (x / 1e3).toFixed(1).replace(/\.0$/, "") + "K" : String(Math.round(x)));
const cmoney = (x) => (x == null ? "—" : "₹" + short(x));
const kmoney = (x) => (x == null ? "—" : x >= 1e5 ? "₹" + short(x) : money(x));
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
const session = { auth: false, user: null, csrf: null };
const store = { get: (k) => { try { return localStorage.getItem(k) || ""; } catch { return ""; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
const reviewer = () => session.user || store.get("clearap-reviewer") || "";
const api = async (path, opts = {}) => {
  const method = (opts.method || "GET").toUpperCase();
  const headers = { ...(opts.headers || {}) };
  if (method !== "GET" && session.csrf) headers["X-CSRF-Token"] = session.csrf;
  // Free hosting sleeps when idle and briefly answers 502/503/504 while waking or redeploying.
  // Reads retry quietly for about a minute; writes never auto-retry (that could submit twice).
  const retryable = method === "GET";
  let r;
  for (let i = 0; ; i++) {
    try {
      r = await fetch(path, { ...opts, headers });
      if (!(retryable && [502, 503, 504].includes(r.status))) break;
    } catch (e) { if (!retryable) { waking(false); throw new Error("Can't reach the server. Check your connection and try again."); } }
    if (i >= 14) { waking(false); throw new Error("The server is still starting. Refresh in a moment."); }
    waking(true);
    await new Promise((res) => setTimeout(res, Math.min(1000 * (i + 1), 5000)));
  }
  waking(false);
  if ([502, 503, 504].includes(r.status)) throw new Error("The server is waking up. Try again in a few seconds.");
  if (r.status === 401) { location.href = "/login"; throw new Error("Please sign in again."); }
  if (!r.ok) { let m = r.statusText; try { const j = await r.json(); m = typeof j.detail === "string" ? j.detail : (j.detail?.[0]?.msg || m); } catch {} throw new Error(m); }
  return r.json();
};
function waking(on) {
  let el = document.getElementById("waking");
  if (!on) { if (el) el.hidden = true; return; }
  if (!el) {
    el = document.createElement("div"); el.id = "waking"; el.setAttribute("role", "status");
    el.textContent = "Server is waking up… this takes up to a minute on free hosting.";
    document.body.appendChild(el);
  }
  el.hidden = false;
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const TOAST_ICON = {
  ok: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/></svg>`,
  error: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5h.01"/></svg>`,
  info: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5h.01"/></svg>`,
  win: `<svg viewBox="0 0 24 24"><path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M9 20h6"/></svg>`,
};
const TOAST_TITLE = { ok: "Done", error: "Couldn't do that", info: "Working on it", win: "Nice work" };
// Stacked toasts: kind = ok | error | info | win. Errors stay longer; every toast can be dismissed.
function toast(msg, kind = "ok") {
  const box = $("#toasts");
  const t = document.createElement("div");
  const ms = kind === "error" ? 6000 : kind === "win" ? 5000 : 3400;
  t.className = `toast ${kind}`;
  t.setAttribute("role", kind === "error" ? "alert" : "status");
  t.style.setProperty("--life", ms + "ms");
  t.innerHTML = `<span class="ti">${TOAST_ICON[kind] || TOAST_ICON.ok}</span><div class="tb"><b>${TOAST_TITLE[kind] || ""}</b><span>${esc(msg)}</span></div>
    <button class="tx" aria-label="Dismiss"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button><i class="tl"></i>`;
  const close = () => { t.classList.add("out"); setTimeout(() => t.remove(), 200); };
  t.querySelector(".tx").onclick = close;
  box.appendChild(t);
  while (box.children.length > 4) box.firstElementChild.remove();
  setTimeout(close, ms);
}
// Styled replacement for confirm()/prompt(). Resolves true/false, or the typed text (null if cancelled).
function ask({ title, body = "", ok = "Confirm", cancel = "Cancel", danger = false, input = null, icon: ic = "" }) {
  return new Promise((resolve) => {
    const m = $("#modal");
    m.innerHTML = `<div class="modal-box ${danger ? "danger" : ""}" role="dialog" aria-modal="true" aria-labelledby="m-t">
      ${ic ? `<div class="m-icon">${ic}</div>` : ""}<h2 id="m-t">${esc(title)}</h2>${body ? `<div class="m-body">${body}</div>` : ""}
      ${input ? `<input class="m-input" id="m-in" value="${esc(input.value || "")}" placeholder="${esc(input.placeholder || "")}" maxlength="60" autocomplete="off">` : ""}
      <div class="modal-actions"><button class="btn ghost" data-a="0">${esc(cancel)}</button><button class="btn ${danger ? "danger" : "primary"}" data-a="1">${esc(ok)}</button></div></div>`;
    m.hidden = false;
    const no = input ? null : false;
    const done = (v) => { m.hidden = true; m.innerHTML = ""; document.removeEventListener("keydown", key, true); resolve(v); };
    const key = (e) => { if (e.key === "Escape") { e.stopPropagation(); done(no); } else if (e.key === "Enter" && input) { e.preventDefault(); done($("#m-in").value); } };
    m.onclick = (e) => { if (e.target === m) return done(no); const b = e.target.closest("[data-a]"); if (b) done(b.dataset.a === "1" ? (input ? $("#m-in").value : true) : no); };
    document.addEventListener("keydown", key, true);
    setTimeout(() => (input ? $("#m-in") : $('[data-a="1"]', m)).focus(), 20);
  });
}
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
  edit: `<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4Z"/><path d="m13.5 6.5 4 4"/></svg>`,
  user: `<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>`,
  archive: `<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"/></svg>`,
  alert: `<svg viewBox="0 0 24 24"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/></svg>`,
  spark: `<svg viewBox="0 0 24 24"><path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/></svg>`,
  flag: `<svg viewBox="0 0 24 24"><path d="M5 21V4h11l-1.5 4L16 12H5"/></svg>`,
};

/* ------------------------------------------------------------------ plain-language vocabulary */
const DEC_LABEL = { APPROVE: "Approved", HOLD: "Needs review", REJECT: "Rejected", NOT_INVOICE: "Not an invoice" };
const STATUS_LABEL = { pass: "OK", warn: "Heads-up", fail: "Problem", info: "FYI", skip: "Skipped" };
// Backend queue/route names → the words a non-specialist would use.
const FRIENDLY = {
  "Treasury & Fraud": "Fraud check team", "Controller approval": "Finance manager", "Procurement / Buyer": "Purchasing team",
  "Vendor Management": "Vendor setup team", "AP Specialist": "Accounts payable team",
  "Auto-approved → payment run": "Scheduled for payment", "Rejected: duplicate": "Blocked: already billed",
  "Rejected: blocked vendor": "Blocked: vendor is on hold", "Returned to vendor": "Sent back to vendor", Rejected: "Rejected", "Filed: not an invoice": "Filed for reference",
};
const friendly = (n) => FRIENDLY[n] || n || "";
const QUEUE_KEYS = { fraud: "Treasury & Fraud", controller: "Controller approval", procurement: "Procurement / Buyer", vendor_mgmt: "Vendor Management", ap: "AP Specialist" };
const TEAM_WHY = {
  "Treasury & Fraud": "Bank details changed or look suspicious. They phone the vendor on a known number before any money moves.",
  "Controller approval": "Large amounts need a manager's sign-off even when everything matches.",
  "Procurement / Buyer": "The invoice doesn't line up with the purchase order. The person who placed the order confirms it.",
  "Vendor Management": "A new vendor, learned from this invoice. Verify them once (GSTIN + bank) and every later invoice from them flows straight through.",
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
  if (review === "corrected") return `<span class="badge info">Corrected · re-checked</span>`;
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
  [/^#\/vendors$/, viewVendors, "vendors"],
  [/^#\/rules$/, viewRules, "rules"],
];
const PAGES = { dashboard: ["Workspace", "Overview"], inbox: ["Workspace", "Inbox"], queue: ["Workspace", "Review queue"],
  vendors: ["Insights", "Vendors"], data: ["Insights", "Ledger & records"], rules: ["Engine", "Rules & accuracy"] };
function drawer(open) {
  $("#sidebar").classList.toggle("open", open); $("#scrim").hidden = !open; $("#menu").setAttribute("aria-expanded", String(open));
}
$("#menu").onclick = () => drawer(!$("#sidebar").classList.contains("open"));
$("#scrim").onclick = () => drawer(false);
async function router() {
  stopLive();
  const h = location.hash || "#/";
  for (const [re, fn, key] of routes) {
    const m = h.match(re);
    if (m) {
      $$("nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === key));
      const [grp, name] = PAGES[key] || ["Workspace", "Invoice"];
      $("#crumb").innerHTML = `<span class="faint">${grp}</span><span class="sep">/</span><b>${name}</b>`;
      document.title = `${name} · NoCap`;
      drawer(false);
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
    chrome.health = h;
    const m = $("#mode");
    m.className = "mode" + (h.llm ? "" : " off");
    m.innerHTML = `<span class="dot"></span>${h.llm ? `AI on · ${esc(h.model)}` : "AI off · basic reader"}`;
    m.title = h.llm ? `Invoices are read by Google Gemini (${h.model}). Rules still make every decision.` : "No GEMINI_API_KEY set. A simple text parser reads digital PDFs; scans and photos go to a person.";
    const q = $("#queue-count");
    q.hidden = !s.pending_review; q.textContent = s.pending_review;
    paintMomentum(s);
  } catch {}
  paintWho();
}

/* ------------------------------------------------------------------ progress & milestones (light gamification) */
// Facts only: every milestone is computed from real stats, never invented.
function milestones(s) {
  return [
    { k: "first", name: "First decision", desc: "Process your first invoice", have: s.processed, need: 1, ic: "✓" },
    { k: "handsfree", name: "Hands-free", desc: "Half of all invoices approved with no one touching them", have: s.touchless_rate, need: 50, unit: "%", ic: "⚡" },
    { k: "dupe", name: "Double-pay blocker", desc: "Stop an invoice that was already paid", have: s.duplicates_blocked, need: 1, ic: "⛔" },
    { k: "fraud", name: "Fraud spotter", desc: "Hold an invoice with a suspicious bank change", have: s.fraud_held, need: 1, ic: "🛡" },
    { k: "lakh", name: "Lakh guardian", desc: "Protect ₹1 lakh from repeats and fraud", have: s.value_protected, need: 1e5, money: true, ic: "₹" },
    { k: "zero", name: "Queue zero", desc: "Clear the review queue completely", have: s.processed && !s.pending_review ? 1 : 0, need: 1, ic: "◎" },
    { k: "loop", name: "Human in the loop", desc: "Make 5 review decisions", have: s.reviews_total, need: 5, ic: "👤" },
    { k: "century", name: "Century", desc: "Handle 100 invoices", have: s.processed, need: 100, ic: "💯" },
  ].map((m) => ({ ...m, done: (m.have || 0) >= m.need, pct: Math.min(100, Math.round((100 * (m.have || 0)) / m.need)) }));
}
const chrome = { pending: null, stats: null, health: null };
function paintMomentum(s) {
  chrome.stats = s;
  const ms = milestones(s), won = ms.filter((m) => m.done);
  const today = s.reviews_today || 0, pend = s.pending_review || 0;
  const pct = pend ? Math.round((100 * today) / (today + pend)) : 100;
  const el = $("#momentum");
  el.innerHTML = `<div class="mo-top">
      <div class="ring" style="--p:${pct}"><svg viewBox="0 0 36 36" aria-hidden="true"><circle class="bg" cx="18" cy="18" r="15.915"/><circle class="fg" cx="18" cy="18" r="15.915" stroke-dasharray="${pct} 100"/></svg><span>${pend || "✓"}</span></div>
      <div style="min-width:0"><b>${pend ? `${pend} to clear` : s.processed ? "Queue zero" : "No invoices yet"}</b><div class="faint small">${today} decided today${s.streak_days > 1 ? ` · 🔥 ${s.streak_days}-day streak` : ""}</div></div></div>
    <a class="mo-wins" href="#/" title="Milestones on the Overview"><span>Milestones</span><span class="mono">${won.length}/${ms.length}</span><i style="width:${(100 * won.length) / ms.length}%"></i></a>`;
  // celebrate real progress, once
  if (chrome.pending > 0 && pend === 0) { toast("Queue zero. Every invoice has a decision.", "win"); el.classList.add("celebrate"); setTimeout(() => el.classList.remove("celebrate"), 1400); }
  chrome.pending = pend;
  const seen = new Set(store.get("clearap-wins").split(",").filter(Boolean));
  const fresh = won.filter((m) => !seen.has(m.k));
  if (fresh.length) {
    if (store.get("clearap-wins-init")) fresh.slice(0, 2).forEach((m) => toast(`Milestone unlocked: ${m.name}`, "win"));
    store.set("clearap-wins", [...seen, ...fresh.map((m) => m.k)].join(","));
  }
  store.set("clearap-wins-init", "1");
}
function milestonesPanel(s) {
  const ms = milestones(s), won = ms.filter((m) => m.done).length;
  return `<div class="card panel wins-panel"><div class="panel-head"><div><span class="t">Team wins</span><div class="faint small">${won} of ${ms.length} unlocked · earned from real results, not clicks</div></div>
    <div class="progress" style="min-width:140px"><span style="width:${(100 * won) / ms.length}%"></span></div></div>
    <div class="wins">${ms.map((m) => `<div class="win ${m.done ? "done" : ""}" title="${esc(m.desc)}">
      <span class="wi">${m.ic}</span><div style="min-width:0"><b>${esc(m.name)}</b><div class="faint small">${esc(m.desc)}</div>
      ${m.done ? `<span class="badge APPROVE">Unlocked</span>` : `<div class="mini"><i style="width:${m.pct}%"></i></div><div class="faint mono" style="font-size:11px">${m.money ? cmoney(m.have || 0) + " / " + cmoney(m.need) : `${Math.round(m.have || 0)}${m.unit || ""} / ${m.need}${m.unit || ""}`}</div>`}</div></div>`).join("")}</div></div>`;
}
const greet = () => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; };
const sectionTitle = (t, sub) => `<div class="section-title"><h2>${t}</h2>${sub ? `<p>${sub}</p>` : ""}</div>`;
function paintWho() {
  const w = $("#who");
  if (session.auth) {
    w.innerHTML = `${icon.user}<span>${esc(session.user)}</span>`;
    w.title = "Signed in. Click to sign out.";
    w.onclick = async () => { if (!(await ask({ title: "Sign out?", body: "You'll need your username and password to get back in.", ok: "Sign out" }))) return; await fetch("/logout", { method: "POST", headers: { "X-CSRF-Token": session.csrf } }); location.href = "/login"; };
  } else {
    const n = store.get("clearap-reviewer");
    w.innerHTML = `${icon.user}<span>${n ? esc(n) : "Set your name"}</span>`;
    w.title = "Your name is recorded on every review you make";
    w.onclick = async () => {
      const v = await ask({ title: "What should we call you?", body: "Your name goes on every review you make, so the audit trail shows who decided what.", ok: "Save", input: { value: n, placeholder: "e.g. Priya Nair" }, icon: icon.user });
      if (v != null) { store.set("clearap-reviewer", v.trim().slice(0, 60)); paintWho(); refreshChrome(); if (v.trim()) toast(`Hi ${v.trim().split(" ")[0]}! Your reviews will be signed with your name.`); }
    };
  }
}

/* ------------------------------------------------------------------ dashboard */
// Outcome colors are chart tokens (validated per theme). Fixed order everywhere: Approved · Needs review · Rejected.
const OUT = [["APPROVE", "Approved", "var(--c-ok)"], ["HOLD", "Needs review", "var(--c-hold)"], ["REJECT", "Rejected", "var(--c-bad)"]];
const STAGE_NAME = Object.fromEntries(STAGES.map(([k, l]) => [k, l]));
const READER_NAME = { gemini: "Gemini AI", heuristic: "Basic text reader", cache: "Re-used earlier AI read", manual: "Typed by a person", none: "Not read" };
const dash = { filter: "all", q: "", tables: {}, archived: false };

async function viewDashboard() {
  const [s, runs, a] = await Promise.all([api("/api/stats"), api("/api/runs"), api("/api/analytics")]);
  if (dash.archived && !s.archived) dash.archived = false;
  const archivedRuns = dash.archived ? await api("/api/runs?archived=1") : [];
  refreshChrome();
  const pending = runs.filter((r) => r.decision === "HOLD" && (!r.review_status || r.review_status === "pending") && r.status !== "running");
  const tl = a.timeline;
  const savedMin = tl.reduce((x, r) => x + (MANUAL_MIN[r.decision] || 0), 0);
  const saved = savedMin >= 60 ? (savedMin / 60).toFixed(1) + " h" : savedMin + " min";
  const sla = { ok: 0, soon: 0, late: 0 };
  pending.forEach((r) => sla[slaState(r).cls || "ok"]++);

  // Cumulative series for the KPI sparklines, one point per finished invoice.
  const cum = (f) => { let acc = 0; return tl.map((r, i) => (acc += f(r, i))); };
  let ap = 0;
  const spark = {
    handled: cum(() => 1),
    auto: tl.map((r, i) => ((ap += r.decision === "APPROVE" ? 1 : 0), Math.round((100 * ap) / (i + 1)))),
    stopped: cum((r) => (r.decision === "REJECT" ? 1 : 0)),
    protectedAmt: cum((r) => r.protected),
    saved: cum((r) => MANUAL_MIN[r.decision] || 0),
    speed: tl.map((r) => r.ms / 1000),
  };

  app.innerHTML = `
  <section class="hero">
    <span class="eyebrow">${greet()}${reviewer() ? ", " + esc(reviewer().split(" ")[0]) : ""} · ${new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}</span>
    <h1>${s.processed ? `${s.processed} invoice${s.processed === 1 ? "" : "s"} handled. <em>${s.touchless_rate}% needed no one.</em>` : `Invoices in, decisions out. <em>Upload one to start.</em>`}</h1>
    <p class="muted" style="margin:0;max-width:640px;font-size:16px">NoCap reads every GST invoice, checks it against your purchase orders, vendors and past payments, then pays it, routes it to the right person, or rejects it, and tells you exactly why. No guesswork, no cap.</p>
    <div class="row">
      ${pending.length ? `<a class="attn" href="#/queue"><b>${pending.length}</b>${pending.length === 1 ? "invoice is" : "invoices are"} waiting for you${sla.late ? ` · ${sla.late} overdue` : ""} →</a>` : `<span class="attn ok"><b>0</b>Nothing waiting for review</span>`}
      <a class="btn primary" href="#/inbox">${icon.upload} Upload an invoice</a>
    </div>
  </section>

  ${sectionTitle("At a glance", "The numbers that matter this period")}
  <div class="grid kpis">
    ${kpi("Invoices handled", s.processed, `${kmoney(s.value_processed)} in total${s.filed ? ` · ${s.filed} non-invoice${s.filed === 1 ? "" : "s"} filed` : ""}`, sparkline(spark.handled, "count", (v) => `${v} handled`), "Every invoice that has finished processing.")}
    ${kpi("Done automatically", s.processed ? s.touchless_rate + "%" : "—", `${s.approved} paid with no one touching them`, sparkline(spark.auto, "rate", (v) => `${v}% automatic so far`), "Share of invoices approved and scheduled for payment without a person stepping in. The line shows how that share moved, invoice by invoice.", "accent")}
    ${kpi("Waiting for a person", s.pending_review, pending.length ? (sla.late ? `${sla.late} past deadline` : "all within deadline") : "queue is clear", slaBar(sla), "Invoices on hold, split by how close they are to their review deadline.")}
    ${kpi("Stopped", s.rejected, "repeats, missing info, blocked vendors", sparkline(spark.stopped, "count", (v) => `${v} stopped so far`), "Invoices that should not be paid as sent.")}
    ${kpi("Money protected", kmoney(s.value_protected), "repeat bills + suspicious bank changes", sparkline(spark.protectedAmt, "money", (v) => `${kmoney(v)} protected so far`), "Value of duplicate invoices blocked plus invoices held for possible fraud.")}
    ${kpi("Time saved", tl.length ? "~" + saved : "—", `avg ${s.avg_duration_ms ? secs(s.avg_duration_ms) : "—"} per invoice`, sparkline(spark.saved, "min", (v) => `~${v} min saved so far`), "Estimate: ~12 min of manual work saved per auto-approved invoice, ~10 per rejection, ~5 per hold (the evidence is already gathered).")}
  </div>

  ${sectionTitle("Trends &amp; outcomes", "What came in, and what happened to it")}
  <div class="grid chart-row r-2-1">
    ${chartCard("activity", "Invoices over time", "How many came in, and what happened to each", activityChart(tl))}
    ${chartCard("mix", "What happened", "Share of invoices, and the money behind them", mixChart(s, a.money))}
  </div>
  ${sectionTitle("Risk &amp; workload", "Why invoices get stopped, and who picks them up")}
  <div class="grid chart-row r-1-1">
    ${chartCard("reasons", "Why invoices were stopped", "The checks that sent invoices to a person or rejected them", reasonsChart(a.reasons))}
    ${chartCard("teams", "Who's reviewing", a.reviews.count ? `${a.reviews.count} decided by people · avg ${dur(a.reviews.avg_seconds)} to decide` : "Held invoices by team, and what reviewers decided", teamsChart(a.teams))}
  </div>
  ${sectionTitle("Spend &amp; engine", "Where the money goes and how the engine performs")}
  <div class="grid chart-row r-1-1-1">
    ${chartCard("stages", "Where the time goes", "Average time per step", stagesChart(a.stage_ms))}
    ${chartCard("vendors", "Top vendors", "Approved spend by vendor", vendorsChart(a.vendors))}
    ${chartCard("readers", "How invoices were read", "Who copied the details off the document", readersChart(a.readers))}
  </div>

  ${sectionTitle("Milestones", "Wins your team has earned")}
  ${milestonesPanel(s)}
  ${sectionTitle("Work list", "Every invoice, newest first")}
  <div class="grid dash-cols">
    <div class="card panel">
      <div class="panel-head"><div><span class="t">${dash.archived ? "Archived" : "Recent invoices"}</span>
          ${s.archived || dash.archived ? `<button class="linkish" id="arch-toggle">${dash.archived ? "← Back to the working list" : `View archived (${s.archived})`}</button>` : ""}</div>
        <div class="toolbar">
          <label class="search">${icon.search}<input id="q" placeholder="Search vendor, invoice #, PO" value="${esc(dash.q)}"></label>
          <div class="chips" id="chips">${[["all", "All"], ["APPROVE", "Approved"], ["HOLD", "Needs review"], ["REJECT", "Rejected"], ["NOT_INVOICE", "Filed"]].map(([k, l]) => `<button data-f="${k}" class="${dash.filter === k ? "on" : ""}">${l}</button>`).join("")}</div>
          <div class="menu"><button class="btn sm ghost" id="exp" aria-haspopup="true">${icon.download} Export</button>
            <div class="menu-pop" id="exp-pop" hidden><a href="/api/export.xlsx" download>Excel workbook <span class="faint">invoices, exceptions, ledger</span></a><a href="/api/export.csv" download>CSV <span class="faint">one row per invoice</span></a><button type="button" id="csv">CSV of this filtered view</button></div></div>
          ${dash.archived ? "" : `<button class="btn sm ghost" id="archive" title="Move finished invoices off this list. Nothing is deleted.">Archive finished</button>`}
        </div></div>
      <div id="runs-table"></div>
    </div>
    <div class="grid">
      <div class="card pad">
        <h3>Needs your attention</h3>
        <div class="side-list">${pending.length ? pending.slice(0, 5).map((r) => { const sl = slaState(r);
          return `<a class="item" href="#/runs/${r.id}"><div style="min-width:0"><div class="t">${esc(r.vendor_name || r.filename)}</div><div class="faint small">${esc(friendly(r.route_to))} · ${money(r.total)}</div></div><span class="sla-chip ${sl.cls}">${sl.text}</span></a>`; }).join("")
          : `<div class="faint small">You're all caught up.</div>`}</div>
        ${pending.length ? `<a class="btn sm" style="margin-top:14px" href="#/queue">Open review list →</a>` : ""}
      </div>
      <div class="card pad">
        <h3>Demo controls</h3>
        <p class="muted small" style="margin:0 0 12px">Start over: permanently clears every invoice and puts the records back to how they started. To tidy up without losing history, use <b>Archive finished</b> instead.</p>
        <button class="btn sm bad" id="reset">Reset demo data</button>
      </div>
    </div>
  </div>`;

  const base = dash.archived ? archivedRuns : runs;
  const filtered = () => { const q = dash.q.trim().toLowerCase(); return base.filter((r) => (dash.filter === "all" || outcome(r) === dash.filter || (dash.filter === "HOLD" && r.decision === "HOLD" && (r.review_status || "pending") === "pending"))
      && (!q || [r.vendor_name, r.invoice_number, r.po_number, r.filename, friendly(r.route_to)].join(" ").toLowerCase().includes(q))); };
  const paint = () => {
    const q = dash.q.trim().toLowerCase();
    const rows = base.filter((r) => (dash.filter === "all" || outcome(r) === dash.filter || (dash.filter === "HOLD" && r.decision === "HOLD" && (r.review_status || "pending") === "pending"))
      && (!q || [r.vendor_name, r.invoice_number, r.po_number, r.filename, friendly(r.route_to)].join(" ").toLowerCase().includes(q)));
    $("#runs-table").innerHTML = !base.length && dash.archived ? `<div class="empty">Nothing archived.</div>`
      : !base.length
      ? `<div class="empty"><div class="big">No invoices yet</div>Upload one, or try the sample inbox.<br><br><a class="btn primary" href="#/inbox">${icon.upload} Upload an invoice</a></div>`
      : !rows.length ? `<div class="empty">No invoices match.</div>`
      : `<div class="table-wrap"><table>
        <thead><tr><th>Vendor</th><th class="num">Amount</th><th>Result</th><th>Next step</th><th class="num">Received</th>${dash.archived ? "<th></th>" : ""}</tr></thead>
        <tbody>${rows.map((r) => `<tr class="click" onclick="location.hash='#/runs/${r.id}'">
          <td class="vcell"><b>${esc(r.vendor_name || r.filename)}</b><div class="faint mono">${esc(r.invoice_number || "no invoice #")}${r.po_number ? " · " + esc(r.po_number) : ""}</div></td>
          <td class="num">${money(r.total)}</td>
          <td>${decBadge(r.decision, r.review_status, r.status)}</td>
          <td class="muted" style="white-space:nowrap">${esc(r.status === "running" ? "Checking now…" : friendly(r.route_to))}</td>
          <td class="num faint" title="${esc(when(r.created_at))}">${ago(r.created_at)}</td>
          ${dash.archived ? `<td class="num"><button class="btn sm" data-restore="${r.id}">Restore</button></td>` : ""}</tr>`).join("")}</tbody></table></div>`;
    $$("[data-restore]").forEach((b) => (b.onclick = async (e) => {
      e.stopPropagation();
      try { await postJSON(`/api/runs/${b.dataset.restore}/restore`, { actor: reviewer() }); toast("Restored to the working list"); viewDashboard(); } catch (err) { toast(err.message, "error"); }
    }));
  };
  paint();
  $("#q").oninput = (e) => { dash.q = e.target.value; paint(); };
  $("#chips").onclick = (e) => { const f = e.target.dataset.f; if (!f) return; dash.filter = f; $$("#chips button").forEach((b) => b.classList.toggle("on", b.dataset.f === f)); paint(); };
  $("#exp").onclick = (e) => { e.stopPropagation(); $("#exp-pop").hidden = !$("#exp-pop").hidden; };
  document.addEventListener("click", () => { const m = $("#exp-pop"); if (m) m.hidden = true; }, { once: true });
  $("#csv").onclick = () => exportCsv(filtered());
  $$("[data-view]").forEach((b) => (b.onclick = () => {
    const id = b.dataset.view; dash.tables[id] = !dash.tables[id];
    const card = b.closest(".chart-card");
    card.classList.toggle("as-table", dash.tables[id]);
    b.textContent = dash.tables[id] ? "Chart" : "Table";
  }));
  $("#arch-toggle") && ($("#arch-toggle").onclick = () => { dash.archived = !dash.archived; dash.filter = "all"; viewDashboard(); });
  $("#archive") && ($("#archive").onclick = async () => {
    if (!(await ask({ title: "Archive every finished invoice?", ok: "Archive", icon: icon.archive,
      body: `<ul class="m-list"><li>They leave this list and the charts. <b>Nothing is deleted.</b></li><li>History, documents and the payment record stay, and repeat invoices are still caught.</li><li>Invoices still waiting for a person stay put. You can restore any archived invoice.</li></ul>` }))) return;
    try { const r = await postJSON("/api/runs/archive", { actor: reviewer() }); toast(r.archived ? `Archived ${r.archived} invoice${r.archived === 1 ? "" : "s"}` : "Nothing finished to archive"); viewDashboard(); }
    catch (e) { toast(e.message, "error"); }
  });
  $("#reset").onclick = async () => {
    if (!(await ask({ title: "Reset all demo data?", danger: true, ok: "Reset everything", icon: icon.alert,
      body: "This permanently clears every invoice, review and document, and puts the ledger back to how it started. <b>It can't be undone.</b> To tidy up without losing history, use <b>Archive finished</b> instead." }))) return;
    await api("/api/reset", { method: "POST" }); toast("Fresh start. All demo data cleared."); viewDashboard();
  };
  // Keep the overview live while anything is still being processed.
  if (runs.some((r) => r.status === "running")) pollTimer = setTimeout(() => { if ((location.hash || "#/").match(/^#?\/?$/)) viewDashboard(); }, 2000);
}

const dur = (sec) => (sec == null ? "—" : sec < 60 ? Math.round(sec) + " s" : sec < 3600 ? Math.round(sec / 60) + " min" : (sec / 3600).toFixed(1) + " h");
const kpi = (label, value, sub, chart = "", help = "", cls = "") => `<div class="card kpi ${cls}"><div class="label">${label}${help ? tip(help) : ""}</div>
  <div class="kpi-row"><div class="value">${value}</div><div class="kpi-chart">${chart}</div></div><div class="sub">${sub}</div></div>`;

/* ---- chart primitives: plain HTML/SVG, theme tokens, hover tooltips (data-ct), table fallback */
function chartCard(id, title, sub, { chart, table, legend = "" }) {
  const on = !!dash.tables[id];
  return `<div class="card chart-card ${on ? "as-table" : ""}">
    <div class="chart-head"><div style="min-width:0"><div class="chart-title">${title}</div><div class="faint small">${sub}</div></div>
      ${table ? `<button class="btn sm ghost view-btn" data-view="${id}" title="Switch between chart and table">${on ? "Chart" : "Table"}</button>` : ""}</div>
    ${legend}<div class="chart-body">${chart}</div>${table ? `<div class="table-body">${table}</div>` : ""}</div>`;
}
const legendHtml = (items) => `<div class="legend row chart-legend">${items.map(([l, c]) => `<span><span><i style="background:${c}"></i>${l}</span></span>`).join("")}</div>`;
const tableHtml = (head, rows) => `<div class="table-wrap"><table class="mini"><thead><tr>${head.map((h, i) => `<th class="${i ? "num" : ""}">${h}</th>`).join("")}</tr></thead>
  <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${i ? "num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
const emptyChart = (msg = "Run a few invoices to fill this in.") => ({ chart: `<div class="chart-empty">${msg}</div>`, table: "" });

function sparkline(vals, kind, fmt) {
  if (vals.length < 2) return "";
  const w = 120, h = 36, max = Math.max(...vals), min = kind === "rate" ? 0 : Math.min(0, ...vals), span = max - min || 1;
  const pts = vals.map((v, i) => [(i / (vals.length - 1)) * w, h - 3 - ((v - min) / span) * (h - 6)]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  const [lx, ly] = pts[pts.length - 1];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Trend: ${esc(fmt(vals[vals.length - 1]))}" data-ct="${esc(fmt(vals[vals.length - 1]))}">
    <path d="${d}L${w},${h}L0,${h}Z" class="spark-area"/><path d="${d}" class="spark-line" vector-effect="non-scaling-stroke"/>
    <circle cx="${lx}" cy="${ly}" r="3" class="spark-dot"/></svg>`;
}
function slaBar(sla) {
  const n = sla.ok + sla.soon + sla.late;
  if (!n) return "";
  const seg = [["On time", sla.ok, "var(--c-ok)"], ["Due soon", sla.soon, "var(--c-hold)"], ["Overdue", sla.late, "var(--c-bad)"]].filter(([, v]) => v);
  return `<div class="stackbar thin" role="img" aria-label="Review deadlines">${seg.map(([l, v, c]) => `<span style="flex:${v};background:${c}" data-ct="${l}: ${v}"></span>`).join("")}</div>`;
}

function activityChart(tl) {
  if (!tl.length) return emptyChart();
  const t0 = toDate(tl[0].t).getTime(), t1 = toDate(tl[tl.length - 1].t).getTime();
  const steps = [10e3, 30e3, 60e3, 5 * 60e3, 15 * 60e3, 3600e3, 6 * 3600e3, 86400e3, 7 * 86400e3];
  const step = steps.find((s) => (t1 - t0) / s < 20) || steps[steps.length - 1];
  const MIN_COLS = 8; // a short demo still reads as a timeline, with the activity at the right
  const last = Math.floor(t1 / step) * step;
  const start = Math.min(Math.floor(t0 / step) * step, last - (MIN_COLS - 1) * step), nb = Math.floor((last - start) / step) + 1;
  const buckets = Array.from({ length: nb }, (_, i) => ({ t: start + i * step, APPROVE: 0, HOLD: 0, REJECT: 0, total: 0 }));
  tl.forEach((r) => { const b = buckets[Math.floor((toDate(r.t).getTime() - start) / step)]; b[r.decision]++; b.total += r.total || 0; });
  const max = Math.max(...buckets.map((b) => b.APPROVE + b.HOLD + b.REJECT));
  const top = max <= 4 ? max || 1 : Math.ceil(max / 2) * 2;
  const lab = (t) => { const d = new Date(t); return step >= 86400e3 ? d.toLocaleDateString([], { month: "short", day: "numeric" })
    : d.toLocaleTimeString([], step < 60e3 ? { hour: "2-digit", minute: "2-digit", second: "2-digit" } : { hour: "2-digit", minute: "2-digit" }); };
  const every = Math.ceil(nb / 6);
  const chart = `<div class="cols-chart" role="img" aria-label="Invoices per ${dur(step / 1000)} by result">
    <div class="y-axis"><span>${top}</span><span>${top / 2 % 1 ? "" : top / 2}</span><span>0</span></div>
    <div class="plot"><div class="grid-lines"><i></i><i></i><i></i></div>
      <div class="col-bars">${buckets.map((b, i) => { const n = b.APPROVE + b.HOLD + b.REJECT;
        const tipTxt = `<b>${lab(b.t)}</b><br>${n} invoice${n === 1 ? "" : "s"} · ${money(b.total)}${OUT.map(([k, l]) => (b[k] ? `<br>${l}: ${b[k]}` : "")).join("")}`;
        return `<div class="col" data-ct="${esc(tipTxt)}"><div class="area"><div class="stack" style="height:${(100 * n) / top}%">${OUT.map(([k, , c]) => (b[k] ? `<span style="flex:${b[k]};background:${c}"></span>` : "")).join("")}</div></div>
          <div class="x">${i % every === 0 ? lab(b.t) : ""}</div></div>`; }).join("")}</div></div></div>`;
  return { chart, legend: legendHtml(OUT.map(([, l, c]) => [l, c])),
    table: tableHtml(["Time", "Approved", "Needs review", "Rejected", "Value"], buckets.filter((b) => b.APPROVE + b.HOLD + b.REJECT).map((b) => [lab(b.t), b.APPROVE, b.HOLD, b.REJECT, money(b.total)])) };
}

function mixChart(s, m) {
  if (!s.processed) return emptyChart();
  const cnt = { APPROVE: s.approved, HOLD: s.held, REJECT: s.rejected };
  const totalMoney = OUT.reduce((x, [k]) => x + (m[k] || 0), 0) || 1;
  const bar = (vals, fmt) => `<div class="stackbar" role="img">${OUT.filter(([k]) => vals[k]).map(([k, l, c]) => `<span style="flex:${vals[k]};background:${c}" data-ct="${esc(`${l}: ${fmt(vals[k])}`)}"></span>`).join("")}</div>`;
  const chart = `<div class="mix">
      <div class="mix-h"><span>Invoices</span><b>${s.processed}</b></div>${bar(cnt, (v) => `${v} · ${Math.round((100 * v) / s.processed)}%`)}
      <div class="mix-h" style="margin-top:18px"><span>Money <span class="faint">(after reviews)</span></span><b>${kmoney(totalMoney)}</b></div>${bar(m, (v) => `${money(v)} · ${Math.round((100 * v) / totalMoney)}%`)}
      <div class="mix-rows">${OUT.map(([k, l, c]) => `<div><span><i style="background:${c}"></i>${l}</span><b>${cnt[k]}</b><span class="faint">${Math.round((100 * cnt[k]) / s.processed)}%</span><span class="num">${kmoney(m[k] || 0)}</span></div>`).join("")}</div>
    </div>`;
  return { chart, table: tableHtml(["Result", "Invoices", "Share", "Money"], OUT.map(([k, l]) => [l, cnt[k], Math.round((100 * cnt[k]) / s.processed) + "%", money(m[k] || 0)])) };
}

function hbars(items, { fmt = (v) => v, color = () => "var(--c-bar)" } = {}) {
  const max = Math.max(...items.map((i) => i.value), 0) || 1;
  return `<div class="hbars">${items.map((it) => `<div class="hbar" data-ct="${esc(it.tip || `${esc(it.label)}: ${fmt(it.value)}`)}">
      <div class="hbar-top"><span class="hbar-label">${esc(it.label)}</span><span class="hbar-val">${fmt(it.value, it)}</span></div>
      <div class="hbar-track">${it.parts ? it.parts.filter((p) => p.v).map((p) => `<span style="width:${(100 * p.v) / max}%;background:${p.c}"></span>`).join("")
        : `<span style="width:${Math.max(1.5, (100 * it.value) / max)}%;background:${color(it)}"></span>`}</div></div>`).join("")}</div>`;
}

function reasonsChart(reasons) {
  if (!reasons.length) return emptyChart("No exceptions yet. Every invoice so far passed cleanly.");
  const items = reasons.map((r) => ({ label: r.label, value: r.count, outcome: r.outcome, tip: `${esc(r.label)}: ${r.count}× · ${r.outcome === "reject" ? "rejects the invoice" : "sends it to a person"}` }));
  return { legend: legendHtml([["Sends to a person", "var(--c-hold)"], ["Rejects", "var(--c-bad)"]]),
    chart: hbars(items, { fmt: (v) => `${v}×`, color: (it) => (it.outcome === "reject" ? "var(--c-bad)" : "var(--c-hold)") }),
    table: tableHtml(["Check", "Effect", "Times"], reasons.map((r) => [esc(r.label), r.outcome === "reject" ? "Rejects" : "To a person", r.count])) };
}

function teamsChart(teams) {
  if (!teams.length) return emptyChart("Nothing has needed a person yet.");
  const order = Object.values(QUEUE_KEYS);
  const rows = teams.sort((a, b) => order.indexOf(a.team) - order.indexOf(b.team)).map((t) => ({ team: t.team, approved: t.approved || 0, pending: t.pending || 0, rejected: t.rejected || 0 }));
  const items = rows.map((t) => ({ label: friendly(t.team), value: t.approved + t.pending + t.rejected,
    tip: `${esc(friendly(t.team))}: ${t.pending} waiting · ${t.approved} approved · ${t.rejected} rejected`,
    parts: [{ v: t.approved, c: "var(--c-ok)" }, { v: t.pending, c: "var(--c-hold)" }, { v: t.rejected, c: "var(--c-bad)" }] }));
  return { legend: legendHtml([["Approved by reviewer", "var(--c-ok)"], ["Waiting", "var(--c-hold)"], ["Rejected by reviewer", "var(--c-bad)"]]),
    chart: hbars(items),
    table: tableHtml(["Team", "Waiting", "Approved", "Rejected"], rows.map((t) => [esc(friendly(t.team)), t.pending, t.approved, t.rejected])) };
}

function stagesChart(st) {
  if (!st.length) return emptyChart();
  const top = Math.max(...st.map((x) => x.avg_ms));
  const items = st.map((x) => ({ label: STAGE_NAME[x.stage] || x.stage, value: x.avg_ms, hot: x.avg_ms === top, tip: `${esc(STAGE_NAME[x.stage] || x.stage)}: avg ${secs(x.avg_ms)} · slowest ${secs(x.max_ms)}` }));
  return { chart: hbars(items, { fmt: (v) => secs(v), color: (it) => (it.hot ? "var(--accent)" : "var(--c-bar)") }) + `<p class="faint small chart-note">Slowest step highlighted. Reading the document is the only step that calls the AI.</p>`,
    table: tableHtml(["Step", "Average", "Slowest"], st.map((x) => [STAGE_NAME[x.stage] || x.stage, secs(x.avg_ms), secs(x.max_ms)])) };
}

function vendorsChart(v) {
  if (!v.length) return emptyChart("No approved spend yet.");
  return { chart: hbars(v.map((x) => ({ label: x.vendor, value: x.total, tip: `${esc(x.vendor)}: ${money(x.total)} across ${x.count} invoice${x.count === 1 ? "" : "s"}` })), { fmt: kmoney }),
    table: tableHtml(["Vendor", "Invoices", "Approved spend"], v.map((x) => [esc(x.vendor), x.count, money(x.total)])) };
}

function readersChart(r) {
  const items = Object.entries(r).sort((a, b) => b[1] - a[1]).map(([k, n]) => ({ label: READER_NAME[k] || k, value: n, k }));
  if (!items.length) return emptyChart();
  const total = items.reduce((x, i) => x + i.value, 0);
  return { chart: hbars(items, { fmt: (v) => `${v} · ${Math.round((100 * v) / total)}%`, color: (it) => (it.k === "gemini" ? "var(--accent)" : "var(--c-bar)") }) + `<p class="faint small chart-note">If the AI is unavailable, a basic reader takes over. If nothing can read it, a person does.</p>`,
    table: tableHtml(["Reader", "Invoices"], items.map((i) => [i.label, i.value])) };
}

/* ---- chart tooltip: one floating element, driven by data-ct on any mark */
const ctip = document.createElement("div");
ctip.className = "ctip"; ctip.hidden = true; document.body.appendChild(ctip);
const placeTip = (e) => {
  const pad = 14, r = ctip.getBoundingClientRect();
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + r.width > innerWidth - 8) x = e.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = e.clientY - r.height - pad;
  ctip.style.transform = `translate(${Math.max(8, x)}px, ${Math.max(8, y)}px)`;
};
document.addEventListener("mouseover", (e) => { const t = e.target.closest("[data-ct]"); if (!t) return; ctip.innerHTML = t.dataset.ct; ctip.hidden = false; placeTip(e); });
document.addEventListener("mousemove", (e) => { if (!ctip.hidden) placeTip(e); });
document.addEventListener("mouseout", (e) => { const t = e.target.closest("[data-ct]"); if (t && !t.contains(e.relatedTarget)) ctip.hidden = true; });
document.addEventListener("scroll", () => (ctip.hidden = true), true);

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
    <div><span class="eyebrow">Inbox</span><h1>Drop in your invoices</h1><p>Any invoice works: a PDF, a scan, or a photo taken on a phone. Drop one to watch every check live, or drop a whole batch (up to 25) and track them together.</p></div>
  </div>
  <label class="drop" id="drop"><input type="file" id="file" multiple accept="application/pdf,.pdf,image/jpeg,image/png,image/webp,image/heic,.heic,.jpg,.jpeg,.png,.webp" hidden>
    <span class="ic">${icon.upload}</span>
    <b>Drag invoices here, or click to choose</b><span class="muted">One file opens the live view. Several files run as a batch. Up to 20 MB each; photos are straightened automatically.</span>
    <span class="formats"><span>PDF</span><span>JPG</span><span>PNG</span><span>WEBP</span><span>HEIC</span></span></label>
  <div id="batch"></div>
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
  const take = (list) => { const fs = [...list]; if (!fs.length) return; fs.length === 1 ? upload(fs[0]) : uploadBatch(fs); };
  file.onchange = () => { take(file.files); file.value = ""; };
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (ev) => take(ev.dataTransfer.files));
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
async function uploadBatch(files) {
  if (files.length > 25) { toast("Up to 25 files at a time. The first 25 are on their way.", "info"); files = files.slice(0, 25); }
  const box = $("#batch");
  const fd = new FormData(); files.forEach((f) => fd.append("files", f));
  box.innerHTML = `<div class="card panel batch"><div class="panel-head"><span class="t">Uploading ${files.length} files…</span></div></div>`;
  let res;
  try { res = (await api("/api/runs/batch", { method: "POST", body: fd })).runs; } catch (e) { box.innerHTML = ""; toast(e.message, "error"); return; }
  const track = async () => {
    if (!$("#batch")) return; // left the page
    const all = await api("/api/runs");
    const byId = Object.fromEntries(all.map((r) => [r.id, r]));
    const rows = res.map((x) => ({ ...x, r: x.run_id ? byId[x.run_id] : null }));
    const done = rows.filter((x) => x.error || (x.r && x.r.status !== "running")).length;
    const tally = { APPROVE: 0, HOLD: 0, REJECT: 0 };
    rows.forEach((x) => x.r && x.r.status !== "running" && tally[x.r.decision]++);
    $("#batch").innerHTML = `<div class="card panel batch">
      <div class="panel-head"><div><span class="t">Batch · ${done} of ${rows.length} done</span>
        <div class="faint small">${tally.APPROVE} approved · ${tally.HOLD} need review · ${tally.REJECT} rejected</div></div>
        <div class="progress" style="min-width:160px"><span style="width:${(100 * done) / rows.length}%"></span></div></div>
      <div class="table-wrap"><table><tbody>${rows.map((x) => `<tr class="${x.run_id ? "click" : ""}" ${x.run_id ? `onclick="location.hash='#/runs/${x.run_id}'"` : ""}>
        <td><b style="font-weight:500">${esc(x.r?.vendor_name || x.filename)}</b><div class="faint mono">${esc(x.filename)}</div></td>
        <td>${x.error ? `<span class="badge fail">${esc(x.error)}</span>` : decBadge(x.r?.decision, x.r?.review_status, x.r?.status || "running")}</td>
        <td class="muted">${esc(x.r && x.r.status !== "running" ? friendly(x.r.route_to) : x.error ? "Not processed" : "Checking now…")}</td>
        <td class="num">${money(x.r?.total)}</td></tr>`).join("")}</tbody></table></div></div>`;
    if (done < rows.length) setTimeout(track, 1200); else { toast(`Batch finished: ${tally.APPROVE} approved, ${tally.HOLD} to review, ${tally.REJECT} rejected`); refreshChrome(); }
  };
  track();
}
async function upload(f) {
  toast(`Uploading ${f.name}…`, "info");
  const fd = new FormData(); fd.append("file", f);
  try { const { run_id } = await api("/api/runs", { method: "POST", body: fd }); location.hash = `#/runs/${run_id}`; }
  catch (e) { toast(e.message, "error"); }
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
    $("#rh-title").textContent = r.vendor_name ? `${r.vendor_name} · ${r.decision === "NOT_INVOICE" ? "not an invoice" : r.invoice_number || "no invoice #"}` : r.filename;
    $("#rh-meta").innerHTML = `${esc(r.filename)} · received ${when(r.created_at)} · checked in ${secs(r.duration_ms)} · read by <b>${esc(READER_NAME[r.extractor] || r.extractor || "nobody yet (needs keying)")}</b>`;
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
  if (d.decision === "NOT_INVOICE") return "Nothing to pay. This isn't a bill, so it's filed for reference and doesn't count as a rejection. If it really is an invoice, correct the details below to re-check it.";
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
    body = `<div class="panel-body"><p style="margin:0 0 14px">${st.decision?.decision === "NOT_INVOICE" ? "This document isn't an invoice, so the invoice checks (vendor, repeats, order match) were skipped and it was filed." : fired.length ? (fired.length === 1 ? "This check shaped the decision:" : `These ${fired.length} checks shaped the decision:`) : "Every check passed, so nobody needed to step in."}</p>${checksHtml(fired)}
      <p class="faint small" style="margin:16px 0 0">How the decision is made: if it isn't an invoice at all, it's <b>filed</b>. If any check says <b>reject</b>, it's rejected. Otherwise, if any check needs a person, it goes to the team best placed to fix it. Otherwise it's approved. The AI never makes this call.</p></div>`;
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
  ["currency", "Currency"], ["bill_to_name", "Billed to"], ["vendor_email", "Sender email"], ["vendor_tax_id", "GSTIN"], ["subtotal", "Subtotal", 1], ["discount_amount", "Discount", 1], ["tax_amount", "GST", 1], ["shipping_amount", "Shipping", 1], ["round_off", "Round off", 1], ["total_amount", "Total", 1]];
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
      <div class="field" style="margin-top:10px"><div class="k"><span>Bank details printed on the invoice</span></div><div class="v">${esc([bank.bank_name, bank.ifsc && "IFSC " + bank.ifsc, bank.account_number && "A/c " + bank.account_number].filter(Boolean).join(" · ") || "—")}</div>
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
  const replacedBy = r.review_status === "corrected" && ((r.audit || []).map((a) => (a.note || "").match(/re-checked as (R[\w-]+)/)).find(Boolean) || [])[1];
  const fromRun = (r.source || "").match(/re-(?:keyed|checked) from (R[\w-]+)/)?.[1];
  const links = replacedBy ? `<div class="notice">This invoice was re-checked (corrected details or a newly verified vendor). <a href="#/runs/${replacedBy}">Open the latest version →</a></div>`
    : fromRun ? `<div class="notice">${/re-checked/.test(r.source) ? "Re-checked after the vendor was verified." : "These details were typed in by a reviewer from the document."} <a href="#/runs/${fromRun}">See the original attempt →</a></div>` : "";
  const audit = (r.audit || []).length ? `<div class="card pad" style="margin-top:16px"><h3>History</h3><ul class="audit">${r.audit.map((a) => `<li><span class="mono faint">${when(a.ts)}</span> · <b>${esc(a.actor)}</b> · ${esc(a.action.replace(/_/g, " "))}${a.note ? `: ${esc(a.note)}` : ""}</li>`).join("")}</ul></div>` : "";
  const pending = r.decision === "HOLD" && (!r.review_status || r.review_status === "pending");
  const misfiled = r.decision === "NOT_INVOICE" && !r.review_status;
  const archived = r.archived_at ? `<div class="notice">Archived ${when(r.archived_at)}. It's off the working list but kept for audit, and it still counts when checking for repeat invoices. <button class="linkish" id="restore">Restore it</button></div>` : "";
  const notes = QUICK_NOTES[r.route_to] || [];
  const vv = pending && r.trace?.decision?.vendor_to_verify;
  const verifyCard = vv ? `<div class="card pad verify-card" style="margin-top:16px"><div class="vc-head"><span class="vc-ic">${icon.shield}</span>
      <div><h3 style="margin:0">New vendor · verify once</h3><div class="muted small">NoCap has no vendor master. It learned this vendor from the invoice; check them once and they're trusted from then on.</div></div></div>
      <dl class="kv small" id="vv-details"><dt>Vendor</dt><dd>Loading…</dd></dl>
      <ul class="m-list small muted"><li>Look up the GSTIN on the GST portal: is it active, and does the legal name match?</li><li>Confirm the bank account with a cancelled cheque or a call to a contact you already know.</li></ul>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px"><button class="btn ok" id="vv-ok">${icon.shield} Verify vendor &amp; re-check</button><button class="btn bad" id="vv-block">Block vendor</button></div></div>` : "";
  el.innerHTML = archived + links + verifyCard + (misfiled ? `<div class="card pad" style="margin-top:16px"><h3>Is this actually an invoice?</h3>
      <p class="muted small" style="margin:0 0 12px">If the vendor is really asking to be paid, type the details from the document. It will be re-checked against every rule, like any invoice.</p>
      <button class="btn" id="fix">${icon.edit} Correct details &amp; re-check</button></div><div id="fix-form"></div>` : "") + (pending ? `<div class="card pad" style="margin-top:16px"><h3>Your decision · ${esc(friendly(r.route_to))}</h3><div class="review">
      <p class="muted small" style="margin:0 0 10px">${esc(TEAM_WHY[r.route_to] || "")}</p>
      <textarea id="note" placeholder="Required: what did you check, and why are you approving or rejecting it? This is saved to the history."></textarea>
      ${notes.length ? `<div class="quick">${notes.map((n) => `<button type="button" data-n="${esc(n)}">${esc(n.length > 58 ? n.slice(0, 56) + "…" : n)}</button>`).join("")}</div>` : ""}
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><button class="btn ok" id="ap">Approve &amp; record</button><button class="btn bad" id="rj">Reject</button>
        <button class="btn ghost" id="fix">${icon.edit} Correct details &amp; re-check</button>
        <span class="faint small" style="margin-left:auto">Recorded as <b>${esc(reviewer() || "AP Reviewer")}</b></span></div></div></div>
      <div id="fix-form"></div>` : "") + audit;
  if (misfiled) $("#fix").onclick = () => renderFixForm(st);
  if (vv) {
    api("/api/vendors").then(({ vendors }) => {
      const v = vendors.find((x) => x.vendor_id === vv); if (!v || !$("#vv-details")) return;
      $("#vv-details").innerHTML = `<dt>Vendor</dt><dd><b>${esc(v.name)}</b> <span class="faint mono">${esc(v.vendor_id)}</span></dd>
        <dt>GSTIN</dt><dd class="mono">${esc(v.tax_id || "Not printed on the invoice")}</dd><dt>Bank</dt><dd>${esc(v.bank)}${v.ifsc ? ` · IFSC <span class="mono">${esc(v.ifsc)}</span>` : ""}</dd>
        <dt>Email domain</dt><dd class="mono">${esc(v.email_domain || "—")}</dd><dt>Added</dt><dd>${when(v.created_at)} from this invoice</dd>`;
    }).catch(() => {});
    $("#vv-ok").onclick = async () => {
      if (!(await ask({ title: "Verify this vendor?", ok: "Verify & re-check", icon: icon.shield,
        body: "Their GSTIN and bank account become the reference for every future invoice, and any of their invoices waiting here are re-checked against every rule." }))) return;
      try {
        const out = await postJSON(`/api/vendors/${vv}/verify`, { actor: reviewer() });
        toast(`Vendor verified. Re-checking ${out.rechecked.length} invoice${out.rechecked.length === 1 ? "" : "s"}…`);
        const again = await api(`/api/runs/${r.id}`);
        const next = ((again.audit || []).map((a) => (a.note || "").match(/re-checked as (R[\w-]+)/)).find(Boolean) || [])[1];
        refreshChrome(); if (next) location.hash = `#/runs/${next}`; else router();
      } catch (e) { toast(e.message, "error"); }
    };
    $("#vv-block").onclick = () => blockVendor(vv, () => router());
  }
  $("#restore") && ($("#restore").onclick = async () => { try { st.run = await postJSON(`/api/runs/${r.id}/restore`, { actor: reviewer() }); toast("Restored"); renderReview(st); } catch (e) { toast(e.message, "error"); } });
  if (pending) {
    $$(".quick button", el).forEach((b) => (b.onclick = () => { $("#note").value = b.dataset.n; $("#note").focus(); }));
    const act = async (action) => {
      try { st.run = await postJSON(`/api/runs/${r.id}/action`, { action, note: $("#note").value, actor: reviewer() });
        toast(action === "approve" ? "Approved and recorded" : "Rejected"); renderReview(st); refreshChrome(); }
      catch (e) { toast(e.message, "error"); }
    };
    $("#ap").onclick = () => act("approve"); $("#rj").onclick = () => act("reject");
    $("#fix").onclick = () => renderFixForm(st);
  }
}

async function blockVendor(id, after) {
  const reason = await ask({ title: "Block this vendor?", danger: true, ok: "Block vendor", icon: icon.alert,
    body: "Every invoice from them will be rejected until someone unblocks them. The reason is recorded.", input: { placeholder: "Reason, e.g. GSTIN cancelled on the GST portal" } });
  if (reason == null) return;
  try { await postJSON(`/api/vendors/${id}/block`, { reason, actor: reviewer() }); toast("Vendor blocked"); after && after(); } catch (e) { toast(e.message, "error"); }
}

/* Correct & re-check: a person types or fixes the fields; the document re-runs through every rule. */
function renderFixForm(st) {
  const box = $("#fix-form");
  if (box.innerHTML) { box.innerHTML = ""; return; }
  const f = st.stages.extract?.data?.fields || {};
  const lines = (f.line_items || []).length ? f.line_items : [{ description: "", quantity: null, unit_price: null, amount: null }];
  const inp = (k, l, type = "text", wide = false) => `<label class="${wide ? "wide" : ""}">${l}<input name="${k}" type="${type}" ${type === "number" ? 'step="0.01"' : ""} value="${esc(f[k] ?? "")}"></label>`;
  box.innerHTML = `<div class="card pad fix" style="margin-top:16px">
    <h3>Correct the details</h3><p class="muted small" style="margin:-4px 0 14px">Type what's on the document (use the <b>Side by side</b> tab). The invoice is then re-checked from scratch: vendor, repeats, order match, everything. The AI read is skipped; the rules are not.</p>
    <form id="fixf" class="fix-grid">
      ${inp("vendor_name", "Vendor", "text", true)}${inp("invoice_number", "Invoice #")}${inp("invoice_date", "Invoice date", "date")}
      ${inp("po_reference", "PO number")}${inp("due_date", "Due date", "date")}${inp("payment_terms", "Payment terms")}
      ${inp("subtotal", "Subtotal", "number")}${inp("discount_amount", "Discount", "number")}${inp("tax_amount", "GST", "number")}${inp("shipping_amount", "Shipping", "number")}${inp("round_off", "Round off (±)", "number")}${inp("total_amount", "Total", "number")}
    </form>
    <div class="sect"><h3>Line items</h3><div class="table-wrap"><table class="lines"><thead><tr><th>Description</th><th class="num">Qty</th><th class="num">Unit price</th><th class="num">Amount</th><th></th></tr></thead>
      <tbody id="lines">${lines.map(lineRow).join("")}</tbody></table></div>
      <button class="btn sm ghost" id="addl" type="button">+ Add line</button></div>
    <label class="note-label">Why are you correcting it?<textarea id="fixnote" placeholder="e.g. Scan was unreadable; typed from the paper copy."></textarea></label>
    <div style="display:flex;gap:8px;margin-top:12px"><button class="btn primary" id="fixgo">Re-check with these details</button><button class="btn ghost" id="fixcancel">Cancel</button></div></div>`;
  $("#addl").onclick = () => $("#lines").insertAdjacentHTML("beforeend", lineRow({}));
  $("#lines").onclick = (e) => { if (e.target.closest(".rm")) e.target.closest("tr").remove(); };
  $("#lines").oninput = (e) => { const tr = e.target.closest("tr"); const [q, u, a] = ["quantity", "unit_price", "amount"].map((n) => tr.querySelector(`[name=${n}]`));
    if (e.target !== a && q.value && u.value) a.value = (Number(q.value) * Number(u.value)).toFixed(2); };
  $("#fixcancel").onclick = () => (box.innerHTML = "");
  box.scrollIntoView({ behavior: "smooth", block: "start" });
  $("#fixgo").onclick = async () => {
    const fd = new FormData($("#fixf")), body = { note: $("#fixnote").value, actor: reviewer() };
    for (const [k, v] of fd.entries()) body[k] = ["subtotal", "discount_amount", "tax_amount", "shipping_amount", "round_off", "total_amount"].includes(k) ? (v === "" ? null : Number(v)) : v;
    body.line_items = [...$$("#lines tr")].map((tr) => { const g = (n) => tr.querySelector(`[name=${n}]`).value; const num = (n) => (g(n) === "" ? null : Number(g(n)));
      return { description: g("description"), quantity: num("quantity"), unit_price: num("unit_price"), amount: num("amount") }; }).filter((l) => l.description || l.amount != null);
    try { const { run_id } = await postJSON(`/api/runs/${st.run.id}/rekey`, body); toast("Re-checking with the corrected details…"); location.hash = `#/runs/${run_id}`; }
    catch (e) { toast(e.message, "error"); }
  };
}
const lineRow = (l) => `<tr><td><input name="description" value="${esc(l.description ?? "")}"></td><td><input name="quantity" type="number" step="any" value="${l.quantity ?? ""}"></td>
  <td><input name="unit_price" type="number" step="0.01" value="${l.unit_price ?? ""}"></td><td><input name="amount" type="number" step="0.01" value="${l.amount ?? ""}"></td>
  <td><button type="button" class="rm" title="Remove line" aria-label="Remove line">×</button></td></tr>`;

/* ------------------------------------------------------------------ queue */
async function viewQueue() {
  const runs = (await api("/api/runs")).filter((r) => r.decision === "HOLD" && (!r.review_status || r.review_status === "pending") && r.status !== "running");
  refreshChrome();
  const groups = {};
  runs.forEach((r) => (groups[r.route_to] = groups[r.route_to] || []).push(r));
  const late = runs.filter((r) => slaState(r).cls === "late").length;
  app.innerHTML = `<div class="page-head"><div><span class="eyebrow">Review queue</span><h1>${runs.length ? `${runs.length} invoice${runs.length === 1 ? "" : "s"} need${runs.length === 1 ? "s" : ""} a person` : "Nothing to review"}</h1>
    <p>Only the unusual ones end up here. Each goes to the team best placed to sort it out, with the evidence already attached${late ? `. <b style="color:var(--bad)">${late} past deadline.</b>` : "."}</p></div></div>
  ${runs.length ? Object.values(QUEUE_KEYS).filter((q) => groups[q]).map((q) => `<div class="card panel q-group">
    <div class="panel-head"><label class="selall"><input type="checkbox" data-group="${esc(q)}" aria-label="Select all in ${esc(friendly(q))}">
      <div><div class="t">${esc(friendly(q))} <span class="badge HOLD">${groups[q].length} waiting</span></div><div class="faint small" style="margin-top:2px">${esc(TEAM_WHY[q] || "")}</div></div></label></div>
    <div class="table-wrap"><table><tbody>${groups[q].sort((a, b) => a.created_at.localeCompare(b.created_at)).map((r) => { const sl = slaState(r);
      return `<tr class="click" data-open="${r.id}"><td class="pick"><input type="checkbox" value="${r.id}" data-g="${esc(q)}" aria-label="Select ${esc(r.vendor_name || r.filename)}"></td>
        <td style="width:26%"><b style="font-weight:500">${esc(r.vendor_name || r.filename)}</b><div class="faint mono">${esc(r.invoice_number || "")}</div></td><td class="muted">${esc(r.headline)}</td><td class="num">${money(r.total)}</td><td class="num"><span class="sla-chip ${sl.cls}">${sl.text}</span></td></tr>`; }).join("")}</tbody></table></div></div>`).join("")
    : `<div class="card empty"><div class="big">You're all caught up</div>When an invoice needs a person, it will show up here.</div>`}
  <div class="bulkbar" id="bulkbar" hidden><b id="bulkn"></b>
    <input id="bulknote" placeholder="Note for the history (required)" maxlength="1000">
    <button class="btn sm ok" data-bulk="approve">Approve</button><button class="btn sm bad" data-bulk="reject">Reject</button>
    <button class="btn sm ghost" id="bulkclear">Clear</button></div>`;
  const picked = () => $$("input[type=checkbox][value]:checked").map((i) => i.value);
  const sync = () => { const n = picked().length; $("#bulkbar").hidden = !n; $("#bulkn").textContent = `${n} selected`; };
  $$("tr[data-open]").forEach((tr) => (tr.onclick = (e) => { if (!e.target.closest(".pick")) location.hash = `#/runs/${tr.dataset.open}`; }));
  $$("input[type=checkbox][value]").forEach((c) => (c.onchange = sync));
  $$("input[data-group]").forEach((c) => (c.onchange = () => { $$(`input[data-g="${CSS.escape(c.dataset.group)}"]`).forEach((x) => (x.checked = c.checked)); sync(); }));
  $("#bulkclear") && ($("#bulkclear").onclick = () => { $$("input[type=checkbox]").forEach((c) => (c.checked = false)); sync(); });
  $$("[data-bulk]").forEach((b) => (b.onclick = async () => {
    const ids = picked(), note = $("#bulknote").value.trim();
    if (!note) { toast("Add a note first. It goes into each invoice's history.", "error"); $("#bulknote").focus(); return; }
    const yes = b.dataset.bulk === "approve";
    if (!(await ask({ title: `${yes ? "Approve" : "Reject"} ${ids.length} invoice${ids.length === 1 ? "" : "s"}?`, ok: yes ? "Approve all" : "Reject all", danger: !yes,
      body: `Your note goes into each invoice's history: <i>“${esc(note)}”</i>` }))) return;
    try {
      const res = await postJSON("/api/runs/bulk-action", { run_ids: ids, action: b.dataset.bulk, note, actor: reviewer() });
      const why = res.results.find((x) => !x.ok)?.error;
      toast(`${res.done} done${res.failed ? ` · ${res.failed} couldn't be (${why})` : ""}`);
      viewQueue();
    } catch (e) { toast(e.message, "error"); }
  }));
}

/* ------------------------------------------------------------------ data */
async function viewData() {
  const [d, dbi] = await Promise.all([api("/api/reference"), api("/api/db")]);
  const vname = Object.fromEntries(d.vendors.map((v) => [v.vendor_id, v.legal_name]));
  app.innerHTML = `<div class="page-head"><div><span class="eyebrow">Ledger &amp; records</span><h1>Orders, vendors &amp; payments</h1><p>What every invoice is checked against. The payment record updates each time an invoice is approved (that's how repeat bills and over-billing get caught), and the vendor list grows on its own as new vendors send invoices.</p></div></div>
  <div class="card pad db-card"><div class="db-head"><div><h3>Database</h3><div class="db-title">${esc(dbi.engine)} · <span class="mono">${esc(dbi.file)}</span> · ${((n) => (n < 1048576 ? (n / 1024).toFixed(0) + " KB" : (n / 1048576).toFixed(1) + " MB"))(dbi.size_bytes)}</div>
      <div class="faint small">Everything lives in this one file: invoices, the payment record, review history, your rules, and the original documents.</div></div>
      <a class="btn sm" href="/api/db/backup" download>${icon.download} Download backup</a></div>
    <div class="db-stats">${[["runs", "Invoices processed"], ["files", "Documents stored"], ["ledger", "Payment records"], ["audit", "History entries"], ["extraction_cache", "Saved AI reads"]].map(([k, l]) => `<div><b>${dbi.counts[k] ?? 0}</b><span>${l}</span></div>`).join("")}</div></div>
  <div class="card panel" style="margin-top:16px"><div class="panel-head"><span class="t">Purchase orders ${tip("A purchase order (PO) is what we agreed to buy, at what price, before the invoice arrives.")}</span></div><div class="table-wrap"><table>
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
  <div class="card panel" style="margin-top:16px"><div class="panel-head"><div><span class="t">Vendor list ${tip("Not a master file: built automatically from the payment history and from each new vendor's first invoice.")}</span><div class="faint small">${d.vendors.length} vendors · learned, not loaded</div></div><a class="btn sm" href="#/vendors">Manage vendors →</a></div><div class="table-wrap"><table>
    <thead><tr><th>ID</th><th>Name</th><th>GSTIN</th><th>Status</th><th>Known from</th><th>Terms</th><th>Bank on file</th><th>IFSC</th><th>Email domain</th></tr></thead>
    <tbody>${d.vendors.map((v) => `<tr><td class="mono">${esc(v.vendor_id)}</td><td>${esc(v.legal_name)}</td><td class="mono">${esc(v.tax_id || "—")}</td><td><span class="badge ${{ active: "pass", unverified: "warn", blocked: "fail" }[v.status]}">${esc({ active: "verified", unverified: "unverified", blocked: "blocked" }[v.status])}</span></td><td>${v.source === "first invoice" ? `<a href="#/runs/${esc(v.first_run_id)}" style="text-decoration:underline">first invoice</a>` : "payment history"}</td><td>${esc(v.payment_terms || "—")}</td><td>${esc(v.bank)}</td><td class="mono">${esc(v.ifsc || "—")}</td><td class="mono">${esc(v.email_domain || "—")}</td></tr>`).join("")}</tbody></table></div></div>`;
}

/* ------------------------------------------------------------------ vendors */
async function viewVendors() {
  const { vendors, unknown } = await api("/api/vendors");
  refreshChrome();
  const maxSpend = Math.max(1, ...vendors.map((v) => v.approved_spend));
  const flagged = vendors.filter((v) => v.flags.length).length;
  vendors.sort((a, b) => (a.status === "unverified" ? -1 : 0) - (b.status === "unverified" ? -1 : 0));
  app.innerHTML = `<div class="page-head"><div><span class="eyebrow">Vendors</span><h1>Who's billing you</h1>
    <p>There's no vendor master to maintain. NoCap learns vendors: anyone you've paid before is known, and a new vendor is added automatically from their first invoice, then verified once by a person.</p></div>
    <div class="actions"><span class="attn ${flagged ? "" : "ok"}"><b>${flagged}</b>${flagged === 1 ? "vendor needs" : "vendors need"} attention</span></div></div>
  <div class="grid vendor-grid">${vendors.map((v) => `<div class="card vendor ${v.flags.length ? "flagged" : ""}">
      <div class="v-head"><div style="min-width:0"><div class="v-name">${esc(v.name)}</div><div class="faint mono small">${esc(v.vendor_id)} · ${esc(v.terms)} · ${esc(v.email_domain)}</div></div>
        <span class="badge ${{ active: "pass", unverified: "warn", blocked: "fail" }[v.status]}">${{ active: "Verified", unverified: "Unverified", blocked: "Blocked" }[v.status]}</span></div>
      <div class="v-src faint small">${v.source === "first invoice" ? `Added ${ago(v.created_at)} from <a href="#/runs/${esc(v.first_run_id)}">their first invoice</a>` : "Known from payment history"}${v.verified_by && v.source === "first invoice" ? ` · verified by ${esc(v.verified_by)}` : ""}${v.status === "blocked" && v.status_reason ? ` · blocked: ${esc(v.status_reason)}` : ""}</div>
      ${v.flags.length ? `<div class="v-flags">${v.flags.map((f) => `<span>${icon.flag}${esc(f)}</span>`).join("")}</div>` : ""}
      <div class="v-stats">
        <div><b>${v.invoices}</b><span>invoices here</span></div>
        <div><b>${cmoney(v.approved_spend)}</b><span>approved</span></div>
        <div><b>${v.waiting}</b><span>waiting</span></div>
        <div><b>${v.rejected}</b><span>rejected</span></div></div>
      <div class="hbar-track v-track" title="Approved spend compared with your top vendor">${v.approved_spend ? `<span style="width:${(100 * v.approved_spend) / maxSpend}%;background:var(--c-bar)"></span>` : ""}</div>
      <dl class="kv small" style="margin-top:12px"><dt>Open orders</dt><dd>${v.open_pos} · ${v.open_po_balance < 0 ? `billed ${money(-v.open_po_balance)} over (within tolerance)` : `${money(v.open_po_balance)} left to bill`}</dd>
        <dt>Paid to</dt><dd>${esc(v.bank)} <span class="faint">(only this account is ever paid)</span></dd>
        <dt>GSTIN</dt><dd class="mono">${esc(v.tax_id || "—")}</dd>
        <dt>Billed to date</dt><dd>${money(v.lifetime_billed)}</dd><dt>Last invoice</dt><dd>${v.last_seen ? ago(v.last_seen) : "—"}</dd></dl>
      <div class="v-actions">${v.status === "unverified" ? `<button class="btn sm ok" data-verify="${esc(v.vendor_id)}">${icon.shield} Verify vendor</button>` : ""}
        ${v.status === "blocked" ? `<button class="btn sm" data-unblock="${esc(v.vendor_id)}">Unblock</button>` : `<button class="btn sm ghost" data-block="${esc(v.vendor_id)}">Block</button>`}</div>
    </div>`).join("")}</div>
  ${unknown.length ? `<div class="card panel" style="margin-top:16px"><div class="panel-head"><span class="t">Not on file</span><span class="faint small">Senders we couldn't match to a vendor. They go to the vendor setup team.</span></div>
    <div class="table-wrap"><table><tbody>${unknown.map((u) => `<tr><td>${esc(u.name)}</td><td class="num">${u.invoices} invoice${u.invoices === 1 ? "" : "s"}</td></tr>`).join("")}</tbody></table></div></div>` : ""}`;
  $$("[data-verify]").forEach((b) => (b.onclick = async () => {
    if (!(await ask({ title: "Verify this vendor?", ok: "Verify & re-check", icon: icon.shield, body: "Their GSTIN and bank become the reference, and their waiting invoices are re-checked against every rule." }))) return;
    try { const out = await postJSON(`/api/vendors/${b.dataset.verify}/verify`, { actor: reviewer() }); toast(`Vendor verified. ${out.rechecked.length} invoice${out.rechecked.length === 1 ? "" : "s"} re-checked.`); refreshChrome(); viewVendors(); }
    catch (e) { toast(e.message, "error"); }
  }));
  $$("[data-block]").forEach((b) => (b.onclick = () => blockVendor(b.dataset.block, viewVendors)));
  $$("[data-unblock]").forEach((b) => (b.onclick = async () => { try { await postJSON(`/api/vendors/${b.dataset.unblock}/unblock`, { actor: reviewer() }); toast("Vendor unblocked"); viewVendors(); } catch (e) { toast(e.message, "error"); } }));
}

/* ------------------------------------------------------------------ rules */
async function viewRules() {
  const p = await api("/api/policy");
  const fields = [
    ["price_tolerance_pct", "Price wiggle room (%)", "How far an invoice can be off the order before a person looks"],
    ["price_tolerance_abs", "Price wiggle room (₹)", "…and in rupees. Whichever is smaller wins."],
    ["small_difference_max", "Ignore small differences up to (₹)", "Gaps this small are rounding, never a problem: totals, line maths, PO prices and repeat checks"],
    ["high_value_threshold", "Manager sign-off above (₹)", "Big invoices get a second look even when everything matches"],
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
    ["Goods receipt (GRN)", "Proof that the warehouse actually received the items."], ["Tolerance", "Small differences we accept automatically. Anything up to ₹10 is treated as rounding, never a problem."],
    ["Duplicate", "The same bill sent twice, sometimes with the number written a bit differently."], ["Remit-to bank", "The account the money is sent to. We only ever pay the one on file."],
    ["GL code", "The accounting category the spend is booked under, e.g. \"6120 Breakroom\"."], ["Split billing", "One order billed across several invoices that together exceed the order."],
    ["Touchless", "Handled from start to finish with no person involved."]];
  app.innerHTML = `<div class="page-head"><div><span class="eyebrow">Rules &amp; accuracy</span><h1>The AI reads. The rules decide.</h1><p>The AI's only job is to copy the details off the invoice. Every decision about money comes from clear rules you can see and change below.</p></div></div>
  <div class="card pad"><h2>Eight steps, every invoice</h2><div class="flow">
    ${steps.map(([t, d, w], i) => `<div class="step ${w === "AI" ? "ai" : ""}"><span class="n">${String(i + 1).padStart(2, "0")}</span><b>${t}</b><p>${d}</p><span class="who">${w}</span></div>`).join("")}
  </div></div>
  <div class="grid outcomes" style="margin-top:16px">
    <div class="card pad"><span class="badge APPROVE">Approved</span><p class="muted">Everything checked out, or small differences were within the wiggle room. Recorded and scheduled for payment, taking early-payment discounts when the terms offer one.</p></div>
    <div class="card pad"><span class="badge HOLD">Needs review</span><p class="muted">A person needs to look. It goes to the team that can fix it: <b>fraud check</b> (bank changes), <b>finance manager</b> (large amounts), <b>purchasing</b> (order problems), <b>vendor setup</b> (unknown vendor), or <b>AP</b> (hard to read).</p></div>
    <div class="card pad"><span class="badge REJECT">Rejected</span><p class="muted">Must not be paid as sent: already billed, vendor blocked, not an invoice, or missing the invoice number or date. Vendors get a ready-to-send email explaining why.</p></div>
  </div>
  <div class="grid quality" style="margin-top:16px">
    <div class="card pad" id="bench"><h2>How accurate is it?</h2><div class="faint small">Loading…</div></div>
    <div class="card pad"><h2>Check the AI connection</h2>
      <p class="muted small" style="margin-top:-6px">Sends a one-word test to the model with the server's key, so you can confirm reading works before a demo. The key itself never leaves the server.</p>
      <button class="btn" id="aitest">Run connection test</button><div id="aires" class="small" style="margin-top:12px"></div></div>
  </div>
  <div class="card pad" style="margin-top:16px"><h2>Your rules</h2><p class="muted" style="margin:-6px 0 18px">Changes apply to the next invoice. Try setting the price wiggle room to 0.5% and re-running the Pragati sample: it flips from approved to needs review.</p>
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
  $("#aitest").onclick = async () => {
    const b = $("#aitest"); b.disabled = true; b.textContent = "Testing…";
    try {
      const r = await postJSON("/api/ai/test", {});
      $("#aires").innerHTML = r.ok ? `<span class="badge pass">Working</span> ${esc(r.model)} answered in ${secs(r.latency_ms)}.`
        : `<span class="badge fail">Not working</span> <span class="muted">${esc(r.error)}</span>`;
    } catch (e) { $("#aires").textContent = e.message; }
    b.disabled = false; b.textContent = "Run connection test";
  };
  api("/api/benchmark").then((bm) => {
    const el = $("#bench"); if (!el) return;
    if (!bm.available) { el.innerHTML = `<h2>How accurate is it?</h2><p class="muted small">No benchmark yet. Run <code>scripts/benchmark.py</code> with a Gemini key.</p>`; return; }
    el.innerHTML = `<h2>How accurate is it?</h2>
      <p class="muted small" style="margin-top:-6px">Measured on ${esc(bm.run_on)} with ${esc(bm.model)}: every sample through the full process, then compared with the exact data each document was made from.</p>
      <div class="bench-top"><div><b>${bm.fields_correct}/${bm.fields_total}</b><span>fields read correctly</span></div><div><b>${bm.decisions_correct}/${bm.samples}</b><span>right decision &amp; team</span></div><div><b>${bm.median_read_seconds ?? "—"} s</b><span>typical AI read</span></div></div>
      ${hbars(bm.by_field.map((f) => ({ label: f.field, value: f.correct, total: f.total, tip: `${esc(f.field)}: ${f.correct}/${f.total}` })), { fmt: (v, it) => `${v}/${it.total}`, color: () => "var(--c-ok)" })}
      <p class="faint small chart-note">Honest caveat: ${bm.samples} clean, generated documents is a smoke test, not proof. Real supplier invoices are messier; the next step is a labelled set of real (redacted) invoices.</p>`;
  }).catch(() => {});
  $("#save").onclick = async () => {
    const fd = new FormData($("#pf")), body = {};
    for (const [k, v] of fd.entries()) body[k] = k === "inferred_po_auto_approve" ? v === "true" : Number(v);
    await api("/api/policy", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    toast("Saved. Applies to the next invoice.");
  };
}

/* ------------------------------------------------------------------ Ask NoCap (assistant) */
const chat = { msgs: [], busy: false };
const SUGGEST = ["What's waiting for me?", "How much money have we protected?", "Why was the Kesari invoice held?", "Which invoices were rejected, and why?"];
function md(text) { // escaped first; then only bold, in-app links and bullet lists
  const inline = (l) => l.replace(/\[([^\]]+)\]\((#\/[\w\/-]*)\)/g, '<a href="$2">$1</a>').replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  let out = "", list = false;
  for (const raw of esc(text).split("\n")) {
    const b = raw.match(/^\s*[-*•]\s+(.*)/);
    if (b) { if (!list) { out += "<ul>"; list = true; } out += `<li>${inline(b[1])}</li>`; continue; }
    if (list) { out += "</ul>"; list = false; }
    if (raw.trim()) out += `<p>${inline(raw)}</p>`;
  }
  return out + (list ? "</ul>" : "");
}
function paintChat() {
  const log = $("#chat-log");
  log.innerHTML = (chat.msgs.length ? "" : `<div class="bubble bot"><p>Hi${reviewer() ? " " + esc(reviewer().split(" ")[0]) : ""}! I'm <b>NoCap</b>, your AP sidekick. Ask me about invoices, vendors, the review queue or the money we've protected. I read the live data; I can't approve or pay anything.</p></div>
      <div class="chips">${SUGGEST.map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`)
    + chat.msgs.map((m) => `<div class="bubble ${m.role === "user" ? "me" : "bot"}">${m.role === "user" ? `<p>${esc(m.text)}</p>` : md(m.text)}</div>`
      + (m.role === "bot" ? `<div class="by">${m.model ? `${icon.spark}Gemini · ${esc(m.model)}${m.ms ? ` · ${secs(m.ms)}` : ""}` : "Basic answer · Gemini wasn't available"}</div>` : "")).join("")
    + (chat.busy ? `<div class="bubble bot typing"><i></i><i></i><i></i></div>` : "");
  $$(".chip", log).forEach((c) => (c.onclick = () => sendChat(c.dataset.q)));
  log.scrollTop = log.scrollHeight;
}
async function sendChat(text) {
  text = (text || "").trim();
  if (!text || chat.busy) return;
  chat.msgs.push({ role: "user", text }); chat.busy = true; paintChat();
  try {
    const r = await postJSON("/api/chat", { messages: chat.msgs.slice(-10).map(({ role, text }) => ({ role, text })) });
    chat.msgs.push({ role: "bot", text: r.answer, model: r.model, ms: r.ms });
  }
  catch (e) { chat.msgs.push({ role: "bot", text: "Sorry, I couldn't answer that just now: " + e.message }); }
  chat.busy = false; paintChat(); $("#chat-input").focus();
}
function openChat() {
  const h = chrome.health;
  $("#chat-sub").innerHTML = h && h.llm ? `Powered by Gemini · ${esc(h.model)} · read-only` : "Gemini isn't connected: set GEMINI_API_KEY on the server";
  $("#chat").hidden = false; hideTeaser(true);
  $("#chat-fab").classList.add("open"); $("#chat-fab").setAttribute("aria-expanded", "true"); $("#open-chat").classList.add("on"); paintChat(); setTimeout(() => $("#chat-input").focus(), 30); }
function closeChat() { $("#chat").hidden = true; $("#open-chat").classList.remove("on"); $("#chat-fab").classList.remove("open"); $("#chat-fab").setAttribute("aria-expanded", "false"); }
$("#chat-fab").onclick = () => ($("#chat").hidden ? openChat() : closeChat());
// A one-time greeting so people notice the assistant; never again once dismissed or used.
function hideTeaser(forever) { $("#chat-teaser").hidden = true; if (forever) store.set("nocap-teaser-seen", "1"); }
$("#teaser-x").onclick = (e) => { e.stopPropagation(); hideTeaser(true); };
$("#chat-teaser").onclick = () => openChat();
if (!store.get("nocap-teaser-seen")) setTimeout(() => { if ($("#chat").hidden) { $("#chat-teaser").hidden = false; setTimeout(() => hideTeaser(false), 12000); } }, 2500);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#chat").hidden && $("#cmd").hidden && $("#modal").hidden) closeChat(); });
$("#open-chat").onclick = () => ($("#chat").hidden ? openChat() : closeChat());
$("#chat-close").onclick = closeChat;
$("#chat-form").onsubmit = (e) => { e.preventDefault(); const i = $("#chat-input"); const v = i.value; i.value = ""; sendChat(v); };
$("#chat-log").addEventListener("click", (e) => { if (e.target.closest("a") && innerWidth < 720) closeChat(); });

/* ------------------------------------------------------------------ command palette (⌘K / Ctrl+K) */
const cmd = { items: [], shown: [], i: 0 };
async function openCmd() {
  $("#cmd").hidden = false; const inp = $("#cmd-input"); inp.value = ""; inp.focus();
  cmd.items = [
    ...[["Overview", "#/"], ["Inbox: upload invoices", "#/inbox"], ["Review queue", "#/queue"], ["Vendors", "#/vendors"], ["Ledger & records: POs, vendors, payments", "#/data"], ["Rules & accuracy", "#/rules"]].map(([t, h]) => ({ grp: "Go to", t, s: "", go: () => (location.hash = h) })),
    { grp: "Actions", t: "Ask NoCap a question", s: "AI assistant", go: () => openChat() },
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

(async () => {
  try { Object.assign(session, await (await fetch("/api/me")).json()); } catch {}
  refreshChrome();
  router();
})();
