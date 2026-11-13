"""FastAPI app: REST + Server-Sent Events for the live run view, and the static UI."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, pipeline, security
from .extract import MODEL, llm_available

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
STATIC = ROOT / "static"
BENCHMARK = Path(__file__).resolve().parent / "seed" / "benchmark.json"
MAX_BYTES = 20 * 1024 * 1024
MAX_BATCH = 25
VERSION = (os.environ.get("RENDER_GIT_COMMIT") or "dev")[:7]

app = FastAPI(title="ClearAP · Invoice to Decision", version="1.1", docs_url="/api/docs", redoc_url=None)
app.add_middleware(security.SecurityMiddleware)
db.init()
_recovered = db.recover_interrupted()
print(f"ClearAP ready: db={db.DB_PATH} reader={'gemini ' + MODEL if llm_available() else 'heuristic (no GEMINI_API_KEY)'}"
      f" login={'on' if security.AUTH_ENABLED else 'off'} recovered={len(_recovered)} version={VERSION}", flush=True)


def actor_of(request: Request, claimed: str | None) -> str:
    """Who did it: the signed-in user when login is on, otherwise the name the reviewer typed."""
    user = security.current_user(request)
    if user:
        return user
    name = " ".join((claimed or "").split())[:60]
    return name or "AP Reviewer"


def is_superseded(r: dict) -> bool:
    return r.get("review_status") == "corrected"


def is_invoice_run(r: dict) -> bool:
    """Counts towards invoice metrics: not superseded by a correction, not a filed non-invoice."""
    return not is_superseded(r) and r.get("decision") != "NOT_INVOICE"


# ---------------------------------------------------------------- health & session
@app.get("/healthz")
def healthz():
    ok = db.ping()
    return JSONResponse({"status": "ok" if ok else "unavailable"}, status_code=200 if ok else 503)


@app.get("/api/health")
def health():
    ok = db.ping()
    body = {"ok": ok, "db": "ok" if ok else "down", "llm": llm_available(), "model": MODEL if llm_available() else None,
            "extractor": "gemini" if llm_available() else "heuristic (set GEMINI_API_KEY for Gemini)",
            "auth": security.AUTH_ENABLED, "version": VERSION}
    return JSONResponse(body, status_code=200 if ok else 503)


@app.get("/api/me")
def me(request: Request):
    return {"auth": security.AUTH_ENABLED, "user": security.current_user(request),
            "csrf": getattr(request.state, "csrf", None)}


@app.get("/login")
def login_page(request: Request):
    if not security.AUTH_ENABLED or security.read_session(request.cookies.get(security.COOKIE)):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form("")):
    if not security.AUTH_ENABLED:
        return RedirectResponse("/", status_code=303)
    if not security.limiter.allow(f"login:{security.client_ip(request)}", 8, 300):
        return RedirectResponse("/login?e=locked", status_code=303)
    if not security.check_credentials(username, password):
        time.sleep(0.4)  # slow down guessing
        return RedirectResponse("/login?e=bad", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(security.COOKIE, security.make_session(username.strip()), max_age=security.SESSION_TTL,
                    httponly=True, samesite="lax", secure=https, path="/")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login" if security.AUTH_ENABLED else "/", status_code=303)
    resp.delete_cookie(security.COOKIE, path="/")
    return resp


# ---------------------------------------------------------------- samples
@app.get("/api/samples")
def samples():
    return json.loads((SAMPLES / "manifest.json").read_text())


@app.get("/api/samples/{name}")
def sample_file(name: str):
    p = (SAMPLES / name).resolve()
    types = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    if p.parent != SAMPLES.resolve() or not p.exists() or p.suffix.lower() not in types:
        raise HTTPException(404)
    return FileResponse(p, media_type=types[p.suffix.lower()])


# ---------------------------------------------------------------- runs
def _clean_name(name: str | None) -> str:
    base = os.path.basename((name or "upload").replace("\\", "/"))
    return "".join(c for c in base if c.isprintable())[:180] or "upload"


@app.post("/api/runs")
async def create_run(file: UploadFile | None = File(None), sample: str | None = Form(None)):
    if sample:
        p = (SAMPLES / sample).resolve()
        if p.parent != SAMPLES.resolve() or not p.exists():
            raise HTTPException(404, "unknown sample")
        data, name, source = p.read_bytes(), sample, "sample inbox"
    elif file:
        data, name, source = await file.read(MAX_BYTES + 1), _clean_name(file.filename), "upload"
    else:
        raise HTTPException(400, "provide a file or a sample name")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (20 MB max)")
    if not data:
        raise HTTPException(400, "empty file")
    return {"run_id": pipeline.start_run(data, name, source)}


@app.post("/api/runs/batch")
async def create_batch(files: list[UploadFile] = File(...)):
    """Up to 25 documents in one go. Each becomes its own run; a bad file never blocks the others."""
    if len(files) > MAX_BATCH:
        raise HTTPException(400, f"up to {MAX_BATCH} files per batch")
    out = []
    for f in files:
        name = _clean_name(f.filename)
        data = await f.read(MAX_BYTES + 1)
        if not data:
            out.append({"filename": name, "error": "empty file"})
        elif len(data) > MAX_BYTES:
            out.append({"filename": name, "error": "file too large (20 MB max)"})
        else:
            out.append({"filename": name, "run_id": pipeline.start_run(data, name, "batch upload")})
    return {"runs": out}


@app.get("/api/runs")
def runs(archived: str = "0"):
    """archived=0 (default): the working list · 1: archived only · all: everything."""
    return db.list_runs(archived={"0": False, "1": True, "all": None}.get(archived, False))


class ActorBody(BaseModel):
    actor: str = ""


@app.post("/api/runs/archive")
def archive(body: ActorBody, request: Request):
    return {"archived": db.archive_resolved(actor_of(request, body.actor))}


@app.post("/api/runs/{run_id}/restore")
def restore(run_id: str, body: ActorBody, request: Request):
    if not db.restore_run(run_id, actor_of(request, body.actor)):
        raise HTTPException(400, "that run isn't archived")
    return db.get_run(run_id)


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    r = db.get_run(run_id)
    if not r:
        raise HTTPException(404)
    return r


@app.get("/api/runs/{run_id}/file")
def run_file(run_id: str):
    f = db.get_file(run_id)
    if not f:
        raise HTTPException(404)
    data, mime = f
    return Response(content=data, media_type=mime, headers={"Content-Disposition": f'inline; filename="{run_id}.pdf"'})


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    if not db.get_run(run_id):
        raise HTTPException(404)

    async def gen():
        idx, idle = 0, 0
        while True:
            evs = pipeline.events_since(run_id, idx)
            for e in evs:
                idx += 1
                yield f"data: {json.dumps(e, default=str)}\n\n"
                if e["type"] in ("run_end", "run_error"):
                    return
            if not evs:
                idle += 1
                r = db.get_run(run_id)
                if r and r["status"] != "running" and not pipeline.events_since(run_id, idx):
                    yield f"data: {json.dumps({'type': 'run_end', 'decision': r['decision']})}\n\n"
                    return
                if idle % 75 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Action(BaseModel):
    action: str
    note: str = ""
    actor: str = ""


@app.post("/api/runs/{run_id}/action")
def action(run_id: str, a: Action, request: Request):
    try:
        return pipeline.human_action(run_id, a.action, actor_of(request, a.actor), a.note)
    except ValueError as e:
        raise HTTPException(400, str(e))


class BulkAction(BaseModel):
    run_ids: list[str] = Field(min_length=1, max_length=100)
    action: str
    note: str = ""
    actor: str = ""


@app.post("/api/runs/bulk-action")
def bulk_action(b: BulkAction, request: Request):
    """Approve or reject several held invoices with one note. Each is checked and audited on its own."""
    who = actor_of(request, b.actor)
    results = []
    for rid in dict.fromkeys(b.run_ids):
        try:
            pipeline.human_action(rid, b.action, who, b.note)
            results.append({"run_id": rid, "ok": True})
        except ValueError as e:
            results.append({"run_id": rid, "ok": False, "error": str(e)})
    return {"done": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results), "results": results}


class LineIn(BaseModel):
    description: str = Field("", max_length=300)
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None


class Rekey(BaseModel):
    note: str = Field("", max_length=1000)
    actor: str = ""
    vendor_name: str | None = Field(None, max_length=200)
    invoice_number: str | None = Field(None, max_length=80)
    invoice_date: str | None = Field(None, max_length=20)
    due_date: str | None = Field(None, max_length=20)
    po_reference: str | None = Field(None, max_length=80)
    payment_terms: str | None = Field(None, max_length=80)
    currency: str | None = Field("USD", max_length=8)
    vendor_email: str | None = Field(None, max_length=200)
    subtotal: float | None = None
    tax_amount: float | None = None
    shipping_amount: float | None = None
    total_amount: float | None = None
    line_items: list[LineIn] = Field(default_factory=list, max_length=200)


@app.post("/api/runs/{run_id}/rekey")
def rekey(run_id: str, body: Rekey, request: Request):
    fields = body.model_dump(exclude={"note", "actor"})
    fields = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in fields.items()}
    try:
        return {"run_id": pipeline.rekey(run_id, fields, actor_of(request, body.actor), body.note)}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- dashboard data
def _final(r: dict) -> str | None:
    if r["decision"] != "HOLD":
        return r["decision"]
    return {"approved": "APPROVE", "rejected": "REJECT"}.get(r.get("review_status") or "", "HOLD")


@app.get("/api/stats")
def stats():
    finished = [r for r in db.list_runs(10000) if r["status"] != "running" and not is_superseded(r)]
    rs = [r for r in finished if is_invoice_run(r)]
    filed = len(finished) - len(rs)
    n = len(rs)
    by = {d: [r for r in rs if r["decision"] == d] for d in ("APPROVE", "HOLD", "REJECT")}
    val = lambda xs: round(sum(r["total"] or 0 for r in xs), 2)
    prevented = 0.0
    for r in rs:
        if r["decision"] == "REJECT" and (r["route_to"] or "").startswith("Rejected: duplicate"):
            prevented += r["total"] or 0
        if r["decision"] == "HOLD" and r["route_to"] == "Treasury & Fraud":
            prevented += r["total"] or 0
    queues = {}
    for r in by["HOLD"]:
        if (r["review_status"] or "pending") == "pending":
            queues[r["route_to"]] = queues.get(r["route_to"], 0) + 1
    durs = [r["duration_ms"] for r in rs if r["duration_ms"]]
    return {
        "processed": n, "approved": len(by["APPROVE"]), "held": len(by["HOLD"]), "rejected": len(by["REJECT"]),
        "touchless_rate": round(100 * len(by["APPROVE"]) / n, 1) if n else 0,
        "value_processed": val(rs), "value_approved": val(by["APPROVE"]), "value_protected": round(prevented, 2),
        "avg_duration_ms": int(sum(durs) / len(durs)) if durs else 0,
        "pending_review": sum(queues.values()), "queues": queues, "filed": filed,
        "archived": len(db.list_runs(10000, archived=True)),
    }


STAGE_ORDER = [k for k, _ in pipeline.STAGES]


@app.get("/api/analytics")
def analytics():
    """Everything the dashboard charts need, computed from the stored run traces."""
    rs = [r for r in db.runs_for_analytics() if is_invoice_run(r)]
    timeline, stage_ms, reasons, teams, vendors, readers = [], defaultdict(list), Counter(), defaultdict(Counter), defaultdict(lambda: [0, 0.0]), Counter()
    money = Counter()
    for r in rs:
        tr, total = r["trace"], r["total"] or 0
        protected = (r["decision"] == "REJECT" and (r["route_to"] or "").startswith("Rejected: duplicate")) or \
                    (r["decision"] == "HOLD" and r["route_to"] == "Treasury & Fraud")
        timeline.append({"t": r["created_at"], "decision": r["decision"], "final": _final(r), "total": total,
                         "protected": total if protected else 0, "ms": r["duration_ms"] or 0})
        for k, st in (tr.get("stages") or {}).items():
            if st.get("duration_ms") is not None:
                stage_ms[k].append(st["duration_ms"])
            for c in st.get("checks") or []:
                if c.get("status") in ("fail", "warn") and c.get("outcome"):
                    reasons[(c["label"], c["outcome"])] += 1
        if r["decision"] == "HOLD":
            teams[r["route_to"]][r["review_status"] or "pending"] += 1
        money[_final(r)] += total
        if _final(r) == "APPROVE" and r["vendor_name"]:
            vendors[r["vendor_name"]][0] += 1
            vendors[r["vendor_name"]][1] += total
        readers[r["extractor"] or "none"] += 1
    created = {r["id"]: r["created_at"] for r in rs}
    turnaround = []
    for a in db.human_reviews():
        if a["run_id"] in created and a["action"] in ("approved_override", "rejected"):
            try:
                turnaround.append((datetime.fromisoformat(a["ts"]) - datetime.fromisoformat(created[a["run_id"]])).total_seconds())
            except ValueError:
                pass
    return {
        "timeline": timeline,
        "stage_ms": [{"stage": k, "avg_ms": round(sum(v) / len(v)), "max_ms": max(v)} for k in STAGE_ORDER if (v := stage_ms.get(k))],
        "reasons": [{"label": l, "outcome": o, "count": n} for (l, o), n in reasons.most_common(8)],
        "teams": [{"team": t, **dict(c)} for t, c in teams.items()],
        "money": {k: round(money.get(k, 0), 2) for k in ("APPROVE", "HOLD", "REJECT")},
        "vendors": sorted(({"vendor": v, "count": c, "total": round(s, 2)} for v, (c, s) in vendors.items()), key=lambda x: -x["total"])[:6],
        "readers": dict(readers),
        "reviews": {"count": len(turnaround), "avg_seconds": round(sum(turnaround) / len(turnaround)) if turnaround else None},
    }


@app.get("/api/vendors")
def vendors():
    """Vendor profiles: master data joined with everything the process has seen from each vendor."""
    m = db.master()
    rs = [r for r in db.list_runs(10000, archived=None) if r["status"] != "running" and is_invoice_run(r)]
    ledger = db.ledger_rows()
    out = []
    for v in m["vendors"]:
        mine = [r for r in rs if r["vendor_id"] == v["vendor_id"]]
        pos = [p for p in m["purchase_orders"] if p["vendor_id"] == v["vendor_id"]]
        open_balance = sum(pipeline._po_state(p)["remaining"] for p in pos if p["status"] == "open")
        flags = []
        if v["status"] != "active":
            flags.append("Blocked in vendor master")
        if any(r["route_to"] == "Treasury & Fraud" for r in mine):
            flags.append("Bank-change attempt seen")
        if any((r["route_to"] or "").startswith("Rejected: duplicate") for r in mine):
            flags.append("Sent a repeat invoice")
        out.append({
            "vendor_id": v["vendor_id"], "name": v["legal_name"], "status": v["status"], "terms": v["payment_terms"],
            "email_domain": v["email_domain"], "bank": f"{v['bank']['bank_name']} ••••{v['bank']['account_number'][-4:]}",
            "invoices": len(mine), "approved": sum(_final(r) == "APPROVE" for r in mine),
            "waiting": sum(r["decision"] == "HOLD" and (r["review_status"] or "pending") == "pending" for r in mine),
            "rejected": sum(_final(r) == "REJECT" for r in mine),
            "approved_spend": round(sum(r["total"] or 0 for r in mine if _final(r) == "APPROVE"), 2),
            "lifetime_billed": round(sum(l["total_amount"] or 0 for l in ledger if l["vendor_id"] == v["vendor_id"]), 2),
            "open_pos": sum(p["status"] == "open" for p in pos), "open_po_balance": round(open_balance, 2),
            "last_seen": max((r["created_at"] for r in mine), default=None), "flags": flags,
        })
    unknown = Counter(r["vendor_name"] or "Unreadable document" for r in rs if not r["vendor_id"])
    out.sort(key=lambda x: (-x["invoices"], x["name"]))
    return {"vendors": out, "unknown": [{"name": n, "invoices": c} for n, c in unknown.most_common()]}


# ---------------------------------------------------------------- exports
EXPORT_COLS = [("Received (UTC)", "created_at"), ("Run ID", "id"), ("Vendor", "vendor_name"), ("Vendor ID", "vendor_id"),
               ("Invoice #", "invoice_number"), ("PO", "po_number"), ("Amount", "total"), ("Currency", "currency"),
               ("Automated decision", "decision"), ("Routed to", "route_to"), ("Review", "review_status"),
               ("Reason", "headline"), ("Read by", "extractor"), ("Seconds", "duration_ms"), ("File", "filename"), ("Source", "source"),
               ("Archived (UTC)", "archived_at")]


def _export_rows():
    for r in db.list_runs(10000, archived=None):
        row = dict(r)
        row["duration_ms"] = round((r["duration_ms"] or 0) / 1000, 2)
        yield [row.get(k) for _, k in EXPORT_COLS]


@app.get("/api/export.csv")
def export_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([h for h, _ in EXPORT_COLS])
    for row in _export_rows():
        w.writerow(["'" + c if isinstance(c, str) and c[:1] in "=+-@" else c for c in row])  # no spreadsheet formulas
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="clearap-{db.now_iso()[:10]}.csv"'})


@app.get("/api/export.xlsx")
def export_xlsx():
    """A workbook an AP lead can hand to finance: invoices, every exception, the ledger, and a summary."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="171717")

    def sheet(ws, headers, rows, money_cols=()):
        ws.append(headers)
        for c in ws[1]:
            c.font, c.fill, c.alignment = head, fill, Alignment(vertical="center")
        for row in rows:
            ws.append(["'" + c if isinstance(c, str) and c[:1] in "=+-@" else c for c in row])
        for i, h in enumerate(headers, 1):
            width = max([len(str(h))] + [len(str(c.value or "")) for c in ws[get_column_letter(i)][1:200]])
            ws.column_dimensions[get_column_letter(i)].width = min(60, max(10, width + 2))
            if h in money_cols:
                for c in ws[get_column_letter(i)][1:]:
                    c.number_format = '#,##0.00'
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    s = stats()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["ClearAP export", db.now_iso()])
    ws["A1"].font = Font(bold=True, size=14)
    for k, v in [("Invoices handled", s["processed"]), ("Approved automatically", s["approved"]), ("Needs review", s["held"]),
                 ("Rejected", s["rejected"]), ("Filed (not invoices)", s["filed"]), ("Done automatically (%)", s["touchless_rate"]), ("Total value", s["value_processed"]),
                 ("Money protected", s["value_protected"]), ("Waiting for a person", s["pending_review"])]:
        ws.append([k, v])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 28, 22

    sheet(wb.create_sheet("Invoices"), [h for h, _ in EXPORT_COLS], _export_rows(), money_cols=("Amount",))
    exc = []
    for r in db.runs_for_analytics(10000, include_archived=True):
        for k, st in (r["trace"].get("stages") or {}).items():
            for c in st.get("checks") or []:
                if c.get("status") in ("fail", "warn"):
                    exc.append([r["created_at"], r["id"], r["vendor_name"], dict(pipeline.STAGES).get(k, k), c["label"],
                                c["status"], c.get("outcome") or "", c.get("detail")])
    sheet(wb.create_sheet("Exceptions"), ["Received (UTC)", "Run ID", "Vendor", "Step", "Check", "Status", "Effect", "Detail"], exc)
    sheet(wb.create_sheet("Ledger"), ["Invoice #", "Vendor ID", "PO", "Invoice date", "Net", "Total", "Status", "Posted", "Payment ref", "From run"],
          ([l["invoice_number"], l["vendor_id"], l["po_number"], l["invoice_date"], l["net_amount"], l["total_amount"], l["status"],
            l["posted_at"], l["payment_ref"], l["source_run_id"]] for l in db.ledger_rows()), money_cols=("Net", "Total"))
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="clearap-{db.now_iso()[:10]}.xlsx"'})


# ---------------------------------------------------------------- AI & quality
@app.post("/api/ai/test")
def ai_test():
    """Tiny live call to the configured model, so an operator can confirm the key works. Never echoes the key."""
    if not llm_available():
        return {"ok": False, "error": "No GEMINI_API_KEY is set on the server. The basic reader is in use."}
    from google import genai
    from google.genai import types
    from . import extract
    t0 = time.time()
    try:
        client = genai.Client(api_key=extract._api_key(), http_options=types.HttpOptions(timeout=15000))
        r = extract._with_deadline(lambda: client.models.generate_content(
            model=MODEL, contents="Reply with the single word OK.",
            config=types.GenerateContentConfig(temperature=0, max_output_tokens=5)), 20)
        return {"ok": True, "model": MODEL, "latency_ms": int((time.time() - t0) * 1000), "reply": (r.text or "").strip()[:20]}
    except Exception as e:
        msg = str(e)
        key = extract._api_key() or ""
        if key:
            msg = msg.replace(key, "***")
        return {"ok": False, "model": MODEL, "error": msg[:300]}


@app.get("/api/benchmark")
def benchmark():
    if not BENCHMARK.exists():
        return {"available": False}
    return {"available": True, **json.loads(BENCHMARK.read_text())}


# ---------------------------------------------------------------- reference data, policy, storage
@app.get("/api/reference")
def reference():
    m = db.master()
    pos = []
    for p in m["purchase_orders"]:
        st = pipeline._po_state(p)
        vendor = next(v for v in m["vendors"] if v["vendor_id"] == p["vendor_id"])
        pos.append({**{k: p[k] for k in ("po_number", "vendor_id", "status", "buyer", "description", "receipt_required", "cost_center")},
                    "vendor": vendor["legal_name"], "lines": p["lines"], **{k: v for k, v in st.items() if k != "prior_invoices"}})
    vendors_ = [{**{k: v[k] for k in ("vendor_id", "legal_name", "tax_id", "status", "payment_terms", "email_domain", "phone_on_file")},
                 "bank": f"{v['bank']['bank_name']} ••••{v['bank']['account_number'][-4:]}"} for v in m["vendors"]]
    return {"company": m["company"], "vendors": vendors_, "purchase_orders": pos, "ledger": db.ledger_rows(),
            "receipts": m["goods_receipts"]}


@app.get("/api/policy")
def get_policy():
    return db.get_policy()


@app.put("/api/policy")
def put_policy(p: dict):
    return db.set_policy(p)


@app.get("/api/db")
def db_info():
    return db.info()


@app.get("/api/db/backup")
def db_backup():
    name = f"clearap-{db.now_iso()[:10]}.db"
    return Response(content=db.backup_bytes(), media_type="application/vnd.sqlite3",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/reset")
def reset():
    db.init(reset=True)
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
