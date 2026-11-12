"""FastAPI app: REST + Server-Sent Events for the live run view, and the static UI."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
print(f"ClearAP ready: data={db.DATA_DIR} reader={'gemini ' + MODEL if llm_available() else 'heuristic (no GEMINI_API_KEY)'}", flush=True)


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
    p = db.UPLOADS / f"{run_id}.pdf"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf")


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
