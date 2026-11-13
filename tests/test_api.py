"""HTTP-level tests: the API a browser (or an integrator) actually uses.

Covers batch intake, re-keying a document a person read, bulk review, restart recovery,
vendor profiles, exports, health, security headers, and the optional login + CSRF.
"""
from __future__ import annotations

import importlib
import io
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["DEMO_PACING_MS"] = "0"
os.environ["AP_TODAY"] = "2026-11-12"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

import generate_samples as G  # noqa: E402
from ground_truth import truth  # noqa: E402

SAMPLE = {s["id"]: s for s in G.SAMPLES}


def make_client(monkeypatch, **env):
    monkeypatch.setenv("DATA_DIR", tempfile.mkdtemp())
    for k in ("APP_USERNAME", "APP_PASSWORD", "APP_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app.db as db
    import app.pipeline as pl
    import app.security as sec
    import app.main as main
    for m in (db, pl, sec, main):
        importlib.reload(m)
    db.init(reset=True)
    return TestClient(main.app), db, pl


def wait(c, rid, timeout=8.0):
    t = time.time()
    while time.time() - t < timeout:
        r = c.get(f"/api/runs/{rid}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.03)
    raise TimeoutError(rid)


def run_sample(c, sid):
    rid = c.post("/api/runs", data={"sample": SAMPLE[sid]["file"]}).json()["run_id"]
    return wait(c, rid)


def test_health_and_security_headers(monkeypatch):
    c, *_ = make_client(monkeypatch)
    h = c.get("/api/health")
    assert h.status_code == 200 and h.json()["db"] == "ok" and h.json()["auth"] is False
    assert c.get("/healthz").json() == {"status": "ok"}
    r = c.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-cache"


def test_batch_upload_isolates_bad_files(monkeypatch):
    c, *_ = make_client(monkeypatch)
    files = [("files", (SAMPLE["happy_path"]["file"], (ROOT / "samples" / SAMPLE["happy_path"]["file"]).read_bytes(), "application/pdf")),
             ("files", ("empty.pdf", b"", "application/pdf")),
             ("files", ("notes.txt", b"hello, not an invoice", "text/plain"))]
    out = c.post("/api/runs/batch", files=files).json()["runs"]
    assert out[1] == {"filename": "empty.pdf", "error": "empty file"}
    good = wait(c, out[0]["run_id"])
    assert good["decision"] == "APPROVE" and good["source"] == "batch upload"
    assert wait(c, out[2]["run_id"])["decision"] == "REJECT"  # unsupported type is a recorded decision, not a crash
    too_many = [("files", (f"f{i}.pdf", b"%PDF-1.4", "application/pdf")) for i in range(26)]
    assert c.post("/api/runs/batch", files=too_many).status_code == 400


def test_rekey_a_scan_nobody_could_read(monkeypatch):
    """No AI configured: a scan goes to AP for keying. The reviewer types the fields; every rule still runs."""
    c, db, _ = make_client(monkeypatch)
    spec = SAMPLE["edge_scanned_inferred_po"]
    first = run_sample(c, "edge_scanned_inferred_po")
    assert first["decision"] == "HOLD" and first["route_to"] == "AP Specialist"
    t = truth(spec)
    body = {k: t[k] for k in ("vendor_name", "invoice_number", "invoice_date", "po_reference", "currency", "subtotal",
                              "tax_amount", "shipping_amount", "total_amount", "line_items")}
    assert c.post(f"/api/runs/{first['id']}/rekey", json={**body}).status_code == 400  # note required
    r = c.post(f"/api/runs/{first['id']}/rekey", json={**body, "note": "Typed from the scan", "actor": "Priya"})
    assert r.status_code == 200, r.text
    second = wait(c, r.json()["run_id"])
    assert second["decision"] == "APPROVE", second["headline"]
    assert second["extractor"] == "manual" and second["source"].startswith("re-keyed from")
    orig = c.get(f"/api/runs/{first['id']}").json()
    assert orig["review_status"] == "corrected" and orig["audit"][-1]["actor"] == "Priya"
    # the superseded run is not double counted, and is not "seen before" for the replacement
    assert c.get("/api/stats").json()["processed"] == 1
    dup = next(x for x in second["trace"]["stages"]["duplicates"]["checks"] if x["id"] == "dup.hash")
    assert dup["status"] == "pass"
    assert c.post(f"/api/runs/{first['id']}/rekey", json={**body, "note": "again"}).status_code == 400


def test_bulk_review(monkeypatch):
    c, *_ = make_client(monkeypatch)
    a = run_sample(c, "edge_bank_change_fraud")
    b = run_sample(c, "edge_split_po_overrun")
    ok = run_sample(c, "happy_path")
    assert c.post("/api/runs/bulk-action", json={"run_ids": [a["id"], b["id"]], "action": "reject", "note": ""}).json()["failed"] == 2
    res = c.post("/api/runs/bulk-action", json={"run_ids": [a["id"], b["id"], ok["id"]], "action": "reject",
                                                "note": "Checked with buyer", "actor": "Sam"}).json()
    assert res["done"] == 2 and res["failed"] == 1  # the approved one can't be "reviewed"
    assert c.get(f"/api/runs/{a['id']}").json()["review_status"] == "rejected"
    assert c.get("/api/stats").json()["pending_review"] == 0


def test_interrupted_runs_are_recovered(monkeypatch):
    c, db, _ = make_client(monkeypatch)
    db.insert_run({"id": "R-stuck", "created_at": db.now_iso(), "filename": "x.pdf", "source": "upload", "sha256": "0",
                   "status": "running", "trace_json": "{}", "events_json": "[]"})
    assert db.recover_interrupted() == ["R-stuck"]
    r = c.get("/api/runs/R-stuck").json()
    assert r["status"] == "error" and r["decision"] == "HOLD" and r["review_status"] == "pending"
    assert r["audit"][0]["action"] == "interrupted"


def test_vendor_profiles_and_exports(monkeypatch):
    from openpyxl import load_workbook
    c, *_ = make_client(monkeypatch)
    run_sample(c, "happy_path")
    run_sample(c, "edge_bank_change_fraud")
    v = c.get("/api/vendors").json()
    assert len(v["vendors"]) == 8
    keystone = next(x for x in v["vendors"] if x["name"].startswith("Keystone"))
    assert keystone["waiting"] == 1 and "Bank-change attempt seen" in keystone["flags"]
    blocked = next(x for x in v["vendors"] if x["status"] != "active")
    assert "Blocked in vendor master" in blocked["flags"]
    wb = load_workbook(io.BytesIO(c.get("/api/export.xlsx").content))
    assert wb.sheetnames == ["Summary", "Invoices", "Exceptions", "Ledger"]
    assert wb["Invoices"].max_row == 3 and wb["Exceptions"].max_row >= 2
    csv_text = c.get("/api/export.csv").text
    assert csv_text.splitlines()[0].startswith("Received (UTC),Run ID,Vendor")


def test_ai_test_without_key_is_explicit(monkeypatch):
    c, *_ = make_client(monkeypatch)
    r = c.post("/api/ai/test").json()
    assert r["ok"] is False and "GEMINI_API_KEY" in r["error"]


def test_login_session_and_csrf(monkeypatch):
    c, *_ = make_client(monkeypatch, APP_USERNAME="lead", APP_PASSWORD="s3cret-pass", APP_SECRET="x" * 32)
    assert c.get("/api/health").status_code == 200                       # health stays public for the platform
    assert c.get("/api/runs").status_code == 401
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    bad = c.post("/login", data={"username": "lead", "password": "nope"}, follow_redirects=False)
    assert bad.headers["location"] == "/login?e=bad"
    ok = c.post("/login", data={"username": "lead", "password": "s3cret-pass"}, follow_redirects=False)
    assert ok.status_code == 303 and "clearap_session" in ok.headers["set-cookie"] and "httponly" in ok.headers["set-cookie"].lower()
    me = c.get("/api/me").json()
    assert me["user"] == "lead" and me["csrf"]
    assert c.post("/api/reset").status_code == 403                       # no CSRF token
    assert c.post("/api/reset", headers={"X-CSRF-Token": me["csrf"]}).status_code == 200
    # the signed-in user, not the request body, is recorded as the reviewer
    rid = c.post("/api/runs", data={"sample": SAMPLE["edge_bank_change_fraud"]["file"]}, headers={"X-CSRF-Token": me["csrf"]}).json()["run_id"]
    wait(c, rid)
    r = c.post(f"/api/runs/{rid}/action", json={"action": "reject", "note": "called vendor", "actor": "someone-else"},
               headers={"X-CSRF-Token": me["csrf"]}).json()
    assert r["audit"][-1]["actor"] == "lead"
    tampered = c.cookies.get("clearap_session")[:-2] + "xx"
    c.cookies.clear()
    c.cookies.set("clearap_session", tampered)  # signature no longer matches
    assert c.get("/api/runs").status_code == 401
