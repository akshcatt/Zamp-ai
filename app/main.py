"""FastAPI app: REST + Server-Sent Events for the live run view, and the static UI."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, pipeline
from .extract import MODEL, llm_available

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
STATIC = ROOT / "static"
MAX_BYTES = 20 * 1024 * 1024

app = FastAPI(title="Invoice-to-Decision", version="1.0")
db.init()
print(f"ClearAP ready: db={db.DB_PATH} reader={'gemini ' + MODEL if llm_available() else 'heuristic (no GEMINI_API_KEY)'}", flush=True)


@app.get("/api/health")
def health():
    return {"ok": True, "llm": llm_available(), "model": MODEL if llm_available() else None,
            "extractor": "gemini" if llm_available() else "heuristic (set GEMINI_API_KEY for Gemini)"}


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


@app.post("/api/runs")
async def create_run(file: UploadFile | None = File(None), sample: str | None = Form(None)):
    if sample:
        p = (SAMPLES / sample).resolve()
        if p.parent != SAMPLES.resolve() or not p.exists():
            raise HTTPException(404, "unknown sample")
        data, name, source = p.read_bytes(), sample, "sample inbox"
    elif file:
        data, name, source = await file.read(), file.filename or "upload", "upload"
    else:
        raise HTTPException(400, "provide a file or a sample name")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (20 MB max)")
    if not data:
        raise HTTPException(400, "empty file")
    return {"run_id": pipeline.start_run(data, name, source)}


@app.get("/api/runs")
def runs():
    return db.list_runs()


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


@app.get("/api/db")
def db_info():
    return db.info()


@app.get("/api/db/backup")
def db_backup():
    name = f"clearap-{db.now_iso()[:10]}.db"
    return Response(content=db.backup_bytes(), media_type="application/vnd.sqlite3",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


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
    actor: str = "AP Reviewer"


@app.post("/api/runs/{run_id}/action")
def action(run_id: str, a: Action):
    try:
        return pipeline.human_action(run_id, a.action, a.actor, a.note)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/stats")
def stats():
    rs = [r for r in db.list_runs(10000) if r["status"] != "running"]
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
        "pending_review": sum(queues.values()), "queues": queues,
    }


STAGE_ORDER = [k for k, _ in pipeline.STAGES]


@app.get("/api/analytics")
def analytics():
    """Everything the dashboard charts need, computed from the stored run traces."""
    from collections import Counter, defaultdict
    from datetime import datetime

    rs = db.runs_for_analytics()
    final = lambda r: ("APPROVE" if r["review_status"] == "approved" else "REJECT" if r["review_status"] == "rejected"
                       else r["decision"]) if r["decision"] == "HOLD" else r["decision"]
    timeline, stage_ms, reasons, teams, vendors, readers = [], defaultdict(list), Counter(), defaultdict(Counter), defaultdict(lambda: [0, 0.0]), Counter()
    money = Counter()
    for r in rs:
        tr, total = r["trace"], r["total"] or 0
        protected = (r["decision"] == "REJECT" and (r["route_to"] or "").startswith("Rejected: duplicate")) or \
                    (r["decision"] == "HOLD" and r["route_to"] == "Treasury & Fraud")
        timeline.append({"t": r["created_at"], "decision": r["decision"], "final": final(r), "total": total,
                         "protected": total if protected else 0, "ms": r["duration_ms"] or 0})
        for k, st in (tr.get("stages") or {}).items():
            if st.get("duration_ms") is not None:
                stage_ms[k].append(st["duration_ms"])
            for c in st.get("checks") or []:
                if c.get("status") in ("fail", "warn") and c.get("outcome"):
                    reasons[(c["label"], c["outcome"])] += 1
        if r["decision"] == "HOLD":
            teams[r["route_to"]][r["review_status"] or "pending"] += 1
        money[final(r)] += total
        if final(r) == "APPROVE" and r["vendor_name"]:
            vendors[r["vendor_name"]][0] += 1
            vendors[r["vendor_name"]][1] += total
        readers[r["extractor"] or "none"] += 1
    created = {r["id"]: r["created_at"] for r in rs}
    turnaround = []
    for a in db.human_reviews():
        if a["run_id"] in created:
            try:
                dt = datetime.fromisoformat(a["ts"]) - datetime.fromisoformat(created[a["run_id"]])
                turnaround.append(dt.total_seconds())
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


@app.get("/api/reference")
def reference():
    m = db.master()
    pos = []
    for p in m["purchase_orders"]:
        st = pipeline._po_state(p)
        vendor = next(v for v in m["vendors"] if v["vendor_id"] == p["vendor_id"])
        pos.append({**{k: p[k] for k in ("po_number", "vendor_id", "status", "buyer", "description", "receipt_required", "cost_center")},
                    "vendor": vendor["legal_name"], "lines": p["lines"], **{k: v for k, v in st.items() if k != "prior_invoices"}})
    vendors = [{**{k: v[k] for k in ("vendor_id", "legal_name", "tax_id", "status", "payment_terms", "email_domain", "phone_on_file")},
                "bank": f"{v['bank']['bank_name']} ••••{v['bank']['account_number'][-4:]}"} for v in m["vendors"]]
    return {"company": m["company"], "vendors": vendors, "purchase_orders": pos, "ledger": db.ledger_rows(),
            "receipts": m["goods_receipts"]}


@app.get("/api/policy")
def get_policy():
    return db.get_policy()


@app.put("/api/policy")
def put_policy(p: dict):
    return db.set_policy(p)


@app.post("/api/reset")
def reset():
    db.init(reset=True)
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
