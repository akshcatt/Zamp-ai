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
    "variant_missing_fields": ("APPROVE", None),  # lenient: missing number/date is reported, not blocking
    "variant_phone_photo": ("APPROVE", None),
    "variant_not_an_invoice": ("NOT_INVOICE", "Filed: not an invoice"),
    "bill_restaurant": ("APPROVE", "Recorded: already paid"),
    "bill_electricity": ("APPROVE", "Auto-approved → payment run"),
    "bill_handwritten_memo": ("APPROVE", "Recorded: already paid"),
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
                "variant_within_tolerance", "variant_missing_fields", "variant_not_an_invoice", "bill_restaurant", "bill_electricity"]


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



def _truth_run(env, monkeypatch, sid, tweak=None):
    db, pl = env
    spec = next(s for s in G.SAMPLES if s["id"] == sid)
    t = truth(spec)
    if tweak:
        tweak(t)
    monkeypatch.setattr(pl, "llm_available", lambda: True)
    monkeypatch.setattr(pl, "extract_with_gemini", lambda pdf, text: (t, {"extractor": "truth"}))
    return run_sync(pl, db, ROOT / "samples" / spec["file"])


def _check(r, cid):
    return next(c for s in r["trace"]["stages"].values() for c in s.get("checks", []) if c["id"] == cid)


def test_discount_and_round_off_are_detected(env, monkeypatch):
    """Photo invoice: 5% trade discount before GST, total rounded up to the rupee. Both detected, maths reconciles, approved."""
    r = _truth_run(env, monkeypatch, "variant_phone_photo")
    assert r["decision"] == "APPROVE", r["headline"]
    d = _check(r, "val.discount")
    assert d["status"] == "pass" and "₹2,316.00" in d["detail"] and "5.00%" in d["detail"]
    tot = _check(r, "val.total")
    assert tot["status"] == "pass" and "rounded off by +₹0.28" in tot["detail"]
    assert _check(r, "val.tax")["status"] == "pass"  # 18% charged on the discounted value
    assert "discount" in _check(r, "po.price")["detail"]


def test_round_off_without_a_printed_line_and_real_mismatch(env, monkeypatch):
    def unprinted(t):
        t["round_off"] = None  # vendor rounded the total but didn't print a round-off row
    r = _truth_run(env, monkeypatch, "happy_path", unprinted)
    assert r["decision"] == "APPROVE" and "no round-off line printed" in _check(r, "val.total")["detail"]


def test_differences_up_to_10_rupees_are_not_a_threat(env, monkeypatch):
    def seven(t):
        t["total_amount"] = t["total_amount"] + 7  # ₹7 off: rounding, not a problem
        t["round_off"] = None
    r = _truth_run(env, monkeypatch, "variant_phone_photo", seven)  # a photo: no text layer to compare the edited total with
    assert r["decision"] == "APPROVE", r["headline"]
    assert "treated as rounding" in _check(r, "val.total")["detail"]


def test_small_price_difference_vs_po_is_ignored(env, monkeypatch):
    def nudge(t):  # vendor bills ₹6 over the PO on one line (amounts stay internally consistent)
        t["line_items"][0]["amount"] += 6
        t["line_items"][0]["unit_price"] = t["line_items"][0]["amount"] / t["line_items"][0]["quantity"]
        t["subtotal"] += 6
        t["total_amount"] += 6
    r = _truth_run(env, monkeypatch, "variant_phone_photo", nudge)
    assert r["decision"] == "APPROVE", r["headline"]
    assert "treated as rounding" in _check(r, "po.price")["detail"]


def test_total_off_by_more_than_10_rupees_is_held(env, monkeypatch):
    def wrong(t):
        t["total_amount"] = t["total_amount"] + 15
        t["round_off"] = None
    r = _truth_run(env, monkeypatch, "happy_path", wrong)
    assert r["decision"] == "HOLD" and "more than the ₹10.00 allowed" in _check(r, "val.total")["detail"]


def test_small_difference_is_the_smaller_of_10_rupees_and_2_percent(env):
    db, pl = env
    P = db.get_policy()
    assert pl.small(P, 50000) == 10.0    # big amount: ₹10 is smaller than 2% (₹1,000)
    assert pl.small(P, 200) == 4.0       # small amount: 2% (₹4) is smaller than ₹10
    assert pl.small(P, 500) == 10.0      # the crossover


def test_missing_bank_details_are_not_an_issue(env, monkeypatch):
    """No bank details printed (and the reader unsure about that empty field): approved, payment goes to the account on file."""
    def no_bank(t):
        t["bank_details"] = {"bank_name": None, "ifsc": None, "account_number": None, "iban": None, "swift": None}
        t["field_confidence"]["bank_details"] = 0.2
    r = _truth_run(env, monkeypatch, "happy_path", no_bank)
    assert r["decision"] == "APPROVE", r["headline"]
    assert _check(r, "val.confidence")["status"] == "pass"
    assert "That's fine" in _check(r, "ven.bank")["detail"]



def test_everyday_bills_are_categorised_recorded_and_never_held(env, monkeypatch):
    r = _truth_run(env, monkeypatch, "bill_restaurant")
    d = r["trace"]["decision"]
    assert r["decision"] == "APPROVE" and d["category"] == "meals_and_entertainment" and d["already_paid"]
    assert d["payment"]["terms"] == "Already paid" and d["payment"]["gl_coding"][0]["account"].startswith("6710")
    assert _check(r, "ven.match")["status"] == "info"  # new seller learned, no verification hold
    assert _check(r, "po.identify")["status"] == "info"  # no PO needed


def test_utility_bill_is_paid_on_its_due_date(env, monkeypatch):
    r = _truth_run(env, monkeypatch, "bill_electricity")
    p = r["trace"]["decision"]["payment"]
    assert r["decision"] == "APPROVE" and p["pay_on"] == "2026-11-20" and p["gl_coding"][0]["account"].startswith("6200")


def test_non_money_problems_are_reported_not_held(env, monkeypatch):
    """Lenient: missing invoice number, bill-to mismatch and low confidence are reported issues, not holds."""
    def messy(t):
        t["invoice_number"] = None
        t["bill_to_name"] = "Some Other Company"
        t["field_confidence"]["vendor_name"] = 0.3
    r = _truth_run(env, monkeypatch, "variant_phone_photo", messy)
    d = r["trace"]["decision"]
    assert r["decision"] == "APPROVE", r["headline"]
    assert {"val.required", "val.billto", "val.confidence"} <= {i["id"] for i in d["issues"]}
    assert "issues reported to tick off" in r["headline"]


def test_money_problems_still_block(env, monkeypatch):
    """Lenient never means paying more than we should: an overrun or a bank swap still stops the bill."""
    assert _truth_run(env, monkeypatch, "edge_split_po_overrun")["decision"] == "HOLD"
    assert _truth_run(env, monkeypatch, "edge_bank_change_fraud")["route_to"] == "Treasury & Fraud"


def test_repeat_bill_without_a_number_is_caught(env, monkeypatch):
    first = _truth_run(env, monkeypatch, "bill_handwritten_memo", lambda t: t.update(invoice_number=None))
    assert first["decision"] == "APPROVE"
    again = _truth_run(env, monkeypatch, "bill_handwritten_memo", lambda t: t.update(invoice_number=None))
    assert again["decision"] == "REJECT" and "same seller, date and amount" in again["headline"] + str(again["trace"])
