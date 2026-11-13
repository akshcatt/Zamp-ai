"""End-to-end tests of the decision logic.

Two modes:
  * ground truth: extraction is replaced by the exact values the sample was generated
    from (a "perfect reader"). This isolates the rules engine from the model.
  * heuristic: the offline text parser, so we know the no-API-key path still works.

Run:  .venv/bin/python -m pytest -q tests
"""
from __future__ import annotations

import importlib
import os
import re
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

import generate_samples as G  # noqa: E402
from ground_truth import truth  # noqa: E402

EXPECTED = {
    "happy_path": ("APPROVE", None),
    "edge_split_po_overrun": ("HOLD", "Procurement / Buyer"),
    "edge_scanned_inferred_po": ("APPROVE", None),
    "edge_disguised_duplicate": ("REJECT", "Rejected: duplicate"),
    "edge_bank_change_fraud": ("HOLD", "Treasury & Fraud"),
    "variant_within_tolerance": ("APPROVE", None),
    "variant_missing_fields": ("REJECT", "Returned to vendor"),
    "variant_phone_photo": ("APPROVE", None),
    "variant_not_an_invoice": ("NOT_INVOICE", "Filed: not an invoice"),
}




@pytest.fixture()
def env(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("DATA_DIR", tmp)
    import app.db as db
    importlib.reload(db)
    import app.pipeline as pl
    importlib.reload(pl)
    db.init(reset=True)
    return db, pl


def run_sync(pl, db, path: Path) -> dict:
    rid = pl.start_run(path.read_bytes(), path.name, "test")
    for _ in range(400):
        r = db.get_run(rid)
        if r["status"] != "running":
            return r
        time.sleep(0.02)
    raise TimeoutError


@pytest.mark.parametrize("spec", G.SAMPLES, ids=[s["id"] for s in G.SAMPLES])
def test_ground_truth_decisions(env, monkeypatch, spec):
    db, pl = env
    monkeypatch.setattr(pl, "llm_available", lambda: True)
    monkeypatch.setattr(pl, "extract_with_gemini", lambda pdf, text: (truth(spec), {"extractor": "truth", "model": "fixture", "latency_ms": 0}))
    r = run_sync(pl, db, ROOT / "samples" / spec["file"])
    assert r["status"] == "done", r["trace"].get("error")
    exp_dec, exp_route = EXPECTED[spec["id"]]
    assert r["decision"] == exp_dec, r["headline"]
    if exp_route:
        assert r["route_to"] == exp_route, r["route_to"]


def test_split_billing_suggests_partial(env, monkeypatch):
    db, pl = env
    spec = next(s for s in G.SAMPLES if s["id"] == "edge_split_po_overrun")
    monkeypatch.setattr(pl, "llm_available", lambda: True)
    monkeypatch.setattr(pl, "extract_with_gemini", lambda pdf, text: (truth(spec), {"extractor": "truth"}))
    r = run_sync(pl, db, ROOT / "samples" / spec["file"])
    assert r["trace"]["decision"]["partial_approval_amount"] == 1440000.0


def test_inferred_po_is_correct(env, monkeypatch):
    db, pl = env
    spec = next(s for s in G.SAMPLES if s["id"] == "edge_scanned_inferred_po")
    monkeypatch.setattr(pl, "llm_available", lambda: True)
    monkeypatch.setattr(pl, "extract_with_gemini", lambda pdf, text: (truth(spec), {"extractor": "truth"}))
    r = run_sync(pl, db, ROOT / "samples" / spec["file"])
    assert r["po_number"] == "PO-2026-0342"


def test_rerun_of_approved_invoice_is_duplicate(env, monkeypatch):
    """Approving posts to the ledger, so sending the same invoice again must be rejected."""
    db, pl = env
    spec = G.SAMPLES[0]
    monkeypatch.setattr(pl, "llm_available", lambda: True)
    monkeypatch.setattr(pl, "extract_with_gemini", lambda pdf, text: (truth(spec), {"extractor": "truth"}))
    p = ROOT / "samples" / spec["file"]
    assert run_sync(pl, db, p)["decision"] == "APPROVE"
    second = run_sync(pl, db, p)
    assert second["decision"] == "REJECT" and second["route_to"] == "Rejected: duplicate"


def test_tolerance_policy_change_flips_decision(env, monkeypatch):
    db, pl = env
    spec = next(s for s in G.SAMPLES if s["id"] == "variant_within_tolerance")
    monkeypatch.setattr(pl, "llm_available", lambda: True)
    monkeypatch.setattr(pl, "extract_with_gemini", lambda pdf, text: (truth(spec), {"extractor": "truth"}))
    db.set_policy({"price_tolerance_pct": 0.5})
    r = run_sync(pl, db, ROOT / "samples" / spec["file"])
    assert r["decision"] == "HOLD"


HEURISTIC_OK = ["happy_path", "edge_split_po_overrun", "edge_disguised_duplicate", "edge_bank_change_fraud",
                "variant_within_tolerance", "variant_missing_fields", "variant_not_an_invoice"]


@pytest.mark.parametrize("sid", HEURISTIC_OK)
def test_heuristic_fallback_reaches_same_decision(env, sid):
    db, pl = env
    spec = next(s for s in G.SAMPLES if s["id"] == sid)
    r = run_sync(pl, db, ROOT / "samples" / spec["file"])
    assert r["status"] == "done", r["trace"].get("error")
    exp_dec, exp_route = EXPECTED[sid]
    # The heuristic reader caps confidence at 0.75, above the 0.70 policy floor, so decisions should hold.
    assert r["decision"] == exp_dec, (r["headline"], [c["detail"] for s in r["trace"]["stages"].values() for c in s.get("checks", []) if c["status"] == "fail"])
    if exp_route:
        assert r["route_to"] == exp_route, r["headline"]


def test_scan_without_llm_goes_to_manual_keying(env):
    db, pl = env
    spec = next(s for s in G.SAMPLES if s["id"] == "edge_scanned_inferred_po")
    r = run_sync(pl, db, ROOT / "samples" / spec["file"])
    assert r["decision"] == "HOLD" and r["route_to"] == "AP Specialist"


def test_unsupported_file_rejected(env, tmp_path):
    db, pl = env
    p = tmp_path / "x.pdf"  # extension lies: content is what counts
    p.write_bytes(b"PK\x03\x04 this is really a .docx")
    r = run_sync(pl, db, p)
    assert r["decision"] == "REJECT"
    assert "Not a PDF or image" in r["trace"]["stages"]["intake"]["checks"][0]["detail"]


def test_photo_is_converted_and_treated_as_scan(env, monkeypatch):
    db, pl = env
    spec = next(s for s in G.SAMPLES if s["id"] == "variant_phone_photo")
    seen = {}
    def fake(pdf, text):
        seen["pdf_header"], seen["text"] = pdf[:5], text
        return truth(spec), {"extractor": "truth"}
    monkeypatch.setattr(pl, "llm_available", lambda: True)
    monkeypatch.setattr(pl, "extract_with_gemini", fake)
    r = run_sync(pl, db, ROOT / "samples" / spec["file"])
    assert seen["pdf_header"] == b"%PDF-"          # reader receives a PDF, not the JPEG
    assert seen["text"].strip() == ""               # no text layer -> scan path
    assert r["trace"]["stages"]["analyze"]["data"]["scanned"] is True
    assert db.get_file(r["id"])[0][:5] == b"%PDF-"  # viewer gets the converted PDF (stored in the DB)
    assert r["decision"] == "APPROVE"


def test_photo_without_llm_goes_to_manual_keying(env):
    db, pl = env
    spec = next(s for s in G.SAMPLES if s["id"] == "variant_phone_photo")
    r = run_sync(pl, db, ROOT / "samples" / spec["file"])
    assert r["decision"] == "HOLD" and r["route_to"] == "AP Specialist"


def test_heic_and_png_detected(env, tmp_path):
    from PIL import Image
    import pillow_heif
    db, pl = env
    img = Image.new("RGB", (60, 40), "white")
    for fmt, ext in (("PNG", "png"), ("HEIF", "heic")):
        if fmt == "HEIF":
            pillow_heif.register_heif_opener()
        p = tmp_path / f"x.{ext}"
        img.save(p, format=fmt)
        assert pl.detect_format(p.read_bytes()) == ("png" if ext == "png" else "heic")
        pdf, _ = pl.image_to_pdf(p.read_bytes())
        assert pdf[:5] == b"%PDF-"


def test_double_counted_shipping_is_reconciled(env, monkeypatch):
    """Reader reports a freight line item AND the same amount as shipping: count it once, don't hold."""
    db, pl = env
    spec = next(s for s in G.SAMPLES if s["id"] == "variant_within_tolerance")
    t = truth(spec)
    t["shipping_amount"] = 3600.0
    monkeypatch.setattr(pl, "llm_available", lambda: True)
    monkeypatch.setattr(pl, "extract_with_gemini", lambda pdf, text: (t, {"extractor": "truth"}))
    r = run_sync(pl, db, ROOT / "samples" / spec["file"])
    assert r["decision"] == "APPROVE", r["headline"]
    assert r["trace"]["stages"]["po_match"]["data"]["variance"] == 3600.0


# ---------------------------------------------------------------- database
def test_everything_is_in_one_sqlite_file_and_survives_restart(env):
    """Runs, ledger postings, the audit trail and the uploaded document all persist in ap.db."""
    import sqlite3
    db, pl = env
    spec = next(s for s in G.SAMPLES if s["id"] == "happy_path")
    r = run_sync(pl, db, ROOT / "samples" / spec["file"])
    assert r["decision"] == "APPROVE"
    assert not (db.DATA_DIR / "uploads").exists()            # no loose files on disk
    # Simulate a process restart: drop the module (and its connection), reopen the same file.
    db._conn.close()
    importlib.reload(db)
    db.init()
    again = db.get_run(r["id"])
    assert again["decision"] == "APPROVE" and again["audit"]
    assert db.get_file(r["id"])[0][:5] == b"%PDF-"
    assert any(l["source_run_id"] == r["id"] for l in db.ledger_rows())
    info = db.info()
    assert info["engine"].startswith("SQLite") and info["counts"]["runs"] == 1 and info["counts"]["files"] == 1
    # The backup is a valid, self-contained SQLite database with the same data.
    snap = Path(tempfile.mkdtemp()) / "b.db"
    snap.write_bytes(db.backup_bytes())
    with sqlite3.connect(snap) as c:
        assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1


def test_reset_clears_runs_and_files_but_keeps_seed_ledger(env):
    db, pl = env
    seeded = len(db.ledger_rows())
    run_sync(pl, db, ROOT / "samples" / G.SAMPLES[0]["file"])
    db.set_policy({"price_tolerance_pct": 0.5})
    db.init(reset=True)
    c = db.info()["counts"]
    assert c["runs"] == 0 and c["files"] == 0 and c["audit"] == 0
    assert len(db.ledger_rows()) == seeded
    assert db.get_policy()["price_tolerance_pct"] == 2.0
