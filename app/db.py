"""SQLite persistence: runs, invoice ledger, audit trail, and the AP policy.

Master data (vendors, POs, goods receipts) is read-only reference data loaded from
seed/master_data.json; it stands in for the ERP / procurement system. The invoice
ledger is mutable: approved invoices are posted to it, which is what makes split
billing and duplicate detection stateful across runs.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
UPLOADS = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "ap.db"
SEED = Path(__file__).resolve().parent / "seed" / "master_data.json"

_lock = threading.RLock()

DEFAULT_POLICY = {
    "price_tolerance_pct": 2.0,          # max variance vs PO, percent ...
    "price_tolerance_abs": 500.0,        # ... and absolute USD; the tighter of the two applies
    "arithmetic_tolerance": 0.05,        # rounding slack on sums
    "high_value_threshold": 25000.0,     # above this a controller signs off, even if matched
    "inferred_po_auto_approve": True,    # may an inferred (not quoted) PO be auto-approved?
    "inferred_po_min_confidence": 0.85,
    "min_field_confidence": 0.70,        # below this on a critical field -> human review
    "fuzzy_duplicate_window_days": 45,   # same vendor + same amount within N days -> review
    "max_invoice_age_days": 180,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm_invoice_no(s: str | None) -> str:
    """'INV 2026/0417', 'inv-2026-0417', 'INV20260417' -> 'INV20260417'."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_conn: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = connect()
    return _conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, created_at TEXT, finished_at TEXT, filename TEXT, source TEXT,
  sha256 TEXT, status TEXT, decision TEXT, route_to TEXT, headline TEXT,
  vendor_id TEXT, vendor_name TEXT, invoice_number TEXT, total REAL, currency TEXT,
  po_number TEXT, duration_ms INTEGER, extractor TEXT, review_status TEXT,
  trace_json TEXT, events_json TEXT
);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT, invoice_number TEXT, invoice_number_norm TEXT,
  invoice_date TEXT, net_amount REAL, total_amount REAL, po_number TEXT, status TEXT,
  posted_at TEXT, payment_ref TEXT, note TEXT, source_run_id TEXT, sha256 TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts TEXT, actor TEXT, action TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS policy (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS extraction_cache (sha256 TEXT PRIMARY KEY, extractor TEXT, data_json TEXT, created_at TEXT);
"""


def master() -> dict:
    return json.loads(SEED.read_text())


def init(reset: bool = False) -> None:
    with _lock:
        c = db()
        if reset:
            c.executescript("DROP TABLE IF EXISTS runs; DROP TABLE IF EXISTS ledger; DROP TABLE IF EXISTS audit; DROP TABLE IF EXISTS policy;")
            for f in UPLOADS.glob("*"):
                f.unlink()
        c.executescript(SCHEMA)
        if c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0:
            for e in master()["invoice_ledger"]:
                c.execute(
                    "INSERT INTO ledger (vendor_id, invoice_number, invoice_number_norm, invoice_date, net_amount, total_amount,"
                    " po_number, status, posted_at, payment_ref, note, source_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e["vendor_id"], e["invoice_number"], norm_invoice_no(e["invoice_number"]), e["invoice_date"],
                     e["net_amount"], e["total_amount"], e["po_number"], e["status"], e["posted_at"], e["payment_ref"],
                     e.get("note"), "seed"),
                )
        for k, v in DEFAULT_POLICY.items():
            c.execute("INSERT OR IGNORE INTO policy (key, value) VALUES (?, ?)", (k, json.dumps(v)))
        c.commit()


# ---------------------------------------------------------------- policy
def get_policy() -> dict:
    rows = db().execute("SELECT key, value FROM policy").fetchall()
    p = dict(DEFAULT_POLICY)
    p.update({r["key"]: json.loads(r["value"]) for r in rows})
    return p


def set_policy(updates: dict) -> dict:
    with _lock:
        for k, v in updates.items():
            if k not in DEFAULT_POLICY:
                continue
            default = DEFAULT_POLICY[k]
            v = bool(v) if isinstance(default, bool) else float(v)
            db().execute("INSERT OR REPLACE INTO policy (key, value) VALUES (?, ?)", (k, json.dumps(v)))
        db().commit()
    return get_policy()


# ---------------------------------------------------------------- ledger
def ledger_rows() -> list[dict]:
    return [dict(r) for r in db().execute("SELECT * FROM ledger ORDER BY id").fetchall()]


def ledger_for_po(po_number: str) -> list[dict]:
    return [dict(r) for r in db().execute("SELECT * FROM ledger WHERE po_number = ?", (po_number,)).fetchall()]


def post_to_ledger(run_id: str, inv: dict, vendor_id: str, po_number: str | None, net: float, sha: str, note: str) -> None:
    with _lock:
        db().execute(
            "INSERT INTO ledger (vendor_id, invoice_number, invoice_number_norm, invoice_date, net_amount, total_amount,"
            " po_number, status, posted_at, payment_ref, note, source_run_id, sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vendor_id, inv.get("invoice_number"), norm_invoice_no(inv.get("invoice_number")), inv.get("invoice_date"),
             net, inv.get("total_amount"), po_number, "posted", now_iso()[:10], None, note, run_id, sha),
        )
        db().commit()


# ---------------------------------------------------------------- runs
def insert_run(row: dict) -> None:
    with _lock:
        cols = ",".join(row)
        db().execute(f"INSERT INTO runs ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        db().commit()


def update_run(run_id: str, **fields) -> None:
    with _lock:
        sets = ",".join(f"{k} = ?" for k in fields)
        db().execute(f"UPDATE runs SET {sets} WHERE id = ?", (*fields.values(), run_id))
        db().commit()


def get_run(run_id: str) -> dict | None:
    r = db().execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["trace"] = json.loads(d.pop("trace_json") or "{}")
    d["events"] = json.loads(d.pop("events_json") or "[]")
    d["audit"] = [dict(a) for a in db().execute("SELECT * FROM audit WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()]
    return d


def list_runs(limit: int = 200) -> list[dict]:
    rows = db().execute(
        "SELECT id, created_at, finished_at, filename, source, status, decision, route_to, headline, vendor_id, vendor_name,"
        " invoice_number, total, currency, po_number, duration_ms, extractor, review_status FROM runs"
        " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def runs_with_sha(sha: str, exclude: str) -> list[dict]:
    return [dict(r) for r in db().execute(
        "SELECT id, created_at, decision, invoice_number FROM runs WHERE sha256 = ? AND id != ? AND status = 'done'",
        (sha, exclude)).fetchall()]


def open_runs_for_vendor(vendor_id: str, exclude: str) -> list[dict]:
    """Invoices sitting in the review queue (not yet posted) - also count for duplicate checks."""
    return [dict(r) for r in db().execute(
        "SELECT id, created_at, invoice_number, total FROM runs WHERE vendor_id = ? AND id != ? AND decision = 'HOLD'"
        " AND (review_status IS NULL OR review_status = 'pending')", (vendor_id, exclude)).fetchall()]


def add_audit(run_id: str, actor: str, action: str, note: str = "") -> None:
    with _lock:
        db().execute("INSERT INTO audit (run_id, ts, actor, action, note) VALUES (?,?,?,?,?)",
                     (run_id, now_iso(), actor, action, note))
        db().commit()


# ---------------------------------------------------------------- extraction cache
def cache_get(sha: str) -> dict | None:
    r = db().execute("SELECT extractor, data_json FROM extraction_cache WHERE sha256 = ?", (sha,)).fetchone()
    return {"extractor": r["extractor"], "data": json.loads(r["data_json"])} if r else None


def cache_put(sha: str, extractor: str, data: dict) -> None:
    with _lock:
        db().execute("INSERT OR REPLACE INTO extraction_cache VALUES (?,?,?,?)", (sha, extractor, json.dumps(data), now_iso()))
        db().commit()
