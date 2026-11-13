"""SQLite persistence: runs, invoice ledger, audit trail, AP policy, extraction cache, and the uploaded files.

Everything lives in one database file (DATA_DIR/ap.db), including the uploaded documents, so a
backup or a move is a single-file copy. WAL mode lets the dashboard read while a run is writing.

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


# ---------------------------------------------------------------- connection adapter
_conn = None


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)  # autocommit
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def q(sql: str, params: tuple | list = (), fetch: str | None = None):
    """Run one statement. fetch: None | 'one' | 'all'. Rows come back as plain dicts.
    One shared connection, serialised by a lock (the app is a single small process)."""
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect()
        cur = _conn.execute(sql, tuple(params))
        if fetch == "one":
            r = cur.fetchone()
            return dict(r) if r else None
        if fetch == "all":
            return [dict(r) for r in cur.fetchall()]
        return None


def _schema() -> list[str]:
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT"
    blob = "BLOB"
    return [
        """CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, created_at TEXT, finished_at TEXT, filename TEXT, source TEXT,
          sha256 TEXT, status TEXT, decision TEXT, route_to TEXT, headline TEXT,
          vendor_id TEXT, vendor_name TEXT, invoice_number TEXT, total DOUBLE PRECISION, currency TEXT,
          po_number TEXT, duration_ms BIGINT, extractor TEXT, review_status TEXT,
          trace_json TEXT, events_json TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS ledger (
          id {pk}, vendor_id TEXT, invoice_number TEXT, invoice_number_norm TEXT,
          invoice_date TEXT, net_amount DOUBLE PRECISION, total_amount DOUBLE PRECISION, po_number TEXT, status TEXT,
          posted_at TEXT, payment_ref TEXT, note TEXT, source_run_id TEXT, sha256 TEXT)""",
        f"CREATE TABLE IF NOT EXISTS audit (id {pk}, run_id TEXT, ts TEXT, actor TEXT, action TEXT, note TEXT)",
        "CREATE TABLE IF NOT EXISTS policy (key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS extraction_cache (sha256 TEXT PRIMARY KEY, extractor TEXT, data_json TEXT, created_at TEXT)",
        f"CREATE TABLE IF NOT EXISTS files (run_id TEXT PRIMARY KEY, mime TEXT, data {blob}, created_at TEXT)",
        "CREATE INDEX IF NOT EXISTS runs_created ON runs (created_at)",
        "CREATE INDEX IF NOT EXISTS runs_sha ON runs (sha256)",
        "CREATE INDEX IF NOT EXISTS ledger_po ON ledger (po_number)",
        "CREATE INDEX IF NOT EXISTS audit_run ON audit (run_id)",
    ]


def master() -> dict:
    return json.loads(SEED.read_text())


def init(reset: bool = False) -> None:
    with _lock:
        if reset:
            for t in ("runs", "ledger", "audit", "policy", "files"):
                q(f"DROP TABLE IF EXISTS {t}")
        for stmt in _schema():
            q(stmt)
        if q("SELECT COUNT(*) AS n FROM ledger", fetch="one")["n"] == 0:
            for e in master()["invoice_ledger"]:
                q("INSERT INTO ledger (vendor_id, invoice_number, invoice_number_norm, invoice_date, net_amount, total_amount,"
                  " po_number, status, posted_at, payment_ref, note, source_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (e["vendor_id"], e["invoice_number"], norm_invoice_no(e["invoice_number"]), e["invoice_date"],
                   e["net_amount"], e["total_amount"], e["po_number"], e["status"], e["posted_at"], e["payment_ref"],
                   e.get("note"), "seed"))
        for k, v in DEFAULT_POLICY.items():
            q("INSERT INTO policy (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING", (k, json.dumps(v)))


def info() -> dict:
    """Storage summary for the UI: engine, file size, and row counts per table."""
    counts = {t: q(f"SELECT COUNT(*) AS n FROM {t}", fetch="one")["n"] for t in ("runs", "ledger", "audit", "files", "extraction_cache")}
    size = sum(p.stat().st_size for p in DATA_DIR.glob("ap.db*") if p.is_file())
    return {"engine": f"SQLite {sqlite3.sqlite_version}", "file": DB_PATH.name, "size_bytes": size, "counts": counts}


# ---------------------------------------------------------------- policy
def get_policy() -> dict:
    p = dict(DEFAULT_POLICY)
    p.update({r["key"]: json.loads(r["value"]) for r in q("SELECT key, value FROM policy", fetch="all")})
    return p


def set_policy(updates: dict) -> dict:
    with _lock:
        for k, v in updates.items():
            if k not in DEFAULT_POLICY:
                continue
            default = DEFAULT_POLICY[k]
            v = bool(v) if isinstance(default, bool) else float(v)
            q("INSERT INTO policy (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
              (k, json.dumps(v)))
    return get_policy()


# ---------------------------------------------------------------- ledger
def ledger_rows() -> list[dict]:
    return q("SELECT * FROM ledger ORDER BY id", fetch="all")


def ledger_for_po(po_number: str) -> list[dict]:
    return q("SELECT * FROM ledger WHERE po_number = ? ORDER BY id", (po_number,), fetch="all")


def post_to_ledger(run_id: str, inv: dict, vendor_id: str, po_number: str | None, net: float, sha: str, note: str) -> None:
    q("INSERT INTO ledger (vendor_id, invoice_number, invoice_number_norm, invoice_date, net_amount, total_amount,"
      " po_number, status, posted_at, payment_ref, note, source_run_id, sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (vendor_id, inv.get("invoice_number"), norm_invoice_no(inv.get("invoice_number")), inv.get("invoice_date"),
       net, inv.get("total_amount"), po_number, "posted", now_iso()[:10], None, note, run_id, sha))


# ---------------------------------------------------------------- runs
def insert_run(row: dict) -> None:
    q(f"INSERT INTO runs ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))


def update_run(run_id: str, **fields) -> None:
    q(f"UPDATE runs SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), run_id))


def get_run(run_id: str) -> dict | None:
    d = q("SELECT * FROM runs WHERE id = ?", (run_id,), fetch="one")
    if not d:
        return None
    d["trace"] = json.loads(d.pop("trace_json") or "{}")
    d["events"] = json.loads(d.pop("events_json") or "[]")
    d["audit"] = q("SELECT * FROM audit WHERE run_id = ? ORDER BY id", (run_id,), fetch="all")
    return d


def list_runs(limit: int = 200) -> list[dict]:
    return q("SELECT id, created_at, finished_at, filename, source, status, decision, route_to, headline, vendor_id, vendor_name,"
             " invoice_number, total, currency, po_number, duration_ms, extractor, review_status FROM runs"
             " ORDER BY created_at DESC, id DESC LIMIT ?", (limit,), fetch="all")


def runs_with_sha(sha: str, exclude: str) -> list[dict]:
    return q("SELECT id, created_at, decision, invoice_number FROM runs WHERE sha256 = ? AND id != ? AND status = 'done'",
             (sha, exclude), fetch="all")


def open_runs_for_vendor(vendor_id: str, exclude: str) -> list[dict]:
    """Invoices sitting in the review queue (not yet posted) - also count for duplicate checks."""
    return q("SELECT id, created_at, invoice_number, total FROM runs WHERE vendor_id = ? AND id != ? AND decision = 'HOLD'"
             " AND (review_status IS NULL OR review_status = 'pending')", (vendor_id, exclude), fetch="all")


def runs_for_analytics(limit: int = 5000) -> list[dict]:
    rows = q("SELECT id, created_at, finished_at, decision, review_status, route_to, total, vendor_name, duration_ms,"
             " extractor, trace_json FROM runs WHERE status != 'running' ORDER BY created_at, id LIMIT ?", (limit,), fetch="all")
    for r in rows:
        r["trace"] = json.loads(r.pop("trace_json") or "{}")
    return rows


def human_reviews() -> list[dict]:
    return q("SELECT run_id, ts, actor, action FROM audit WHERE actor != 'system' ORDER BY id", fetch="all")


def add_audit(run_id: str, actor: str, action: str, note: str = "") -> None:
    q("INSERT INTO audit (run_id, ts, actor, action, note) VALUES (?,?,?,?,?)", (run_id, now_iso(), actor, action, note))


# ---------------------------------------------------------------- uploaded documents (stored in the database, not loose on disk)
def put_file(run_id: str, data: bytes, mime: str = "application/pdf") -> None:
    q("INSERT INTO files (run_id, mime, data, created_at) VALUES (?,?,?,?)"
      " ON CONFLICT (run_id) DO UPDATE SET mime = excluded.mime, data = excluded.data",
      (run_id, mime, data, now_iso()))


def get_file(run_id: str) -> tuple[bytes, str] | None:
    r = q("SELECT mime, data FROM files WHERE run_id = ?", (run_id,), fetch="one")
    return (bytes(r["data"]), r["mime"]) if r else None


# ---------------------------------------------------------------- extraction cache
def cache_get(sha: str) -> dict | None:
    r = q("SELECT extractor, data_json FROM extraction_cache WHERE sha256 = ?", (sha,), fetch="one")
    return {"extractor": r["extractor"], "data": json.loads(r["data_json"])} if r else None


def cache_put(sha: str, extractor: str, data: dict) -> None:
    q("INSERT INTO extraction_cache (sha256, extractor, data_json, created_at) VALUES (?,?,?,?)"
      " ON CONFLICT (sha256) DO UPDATE SET extractor = excluded.extractor, data_json = excluded.data_json,"
      " created_at = excluded.created_at", (sha, extractor, json.dumps(data), now_iso()))


def backup_bytes() -> bytes:
    """A consistent snapshot of the whole database (safe while runs are writing), as one .db file."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        dst_path = Path(tmp) / "clearap-backup.db"
        dst = sqlite3.connect(dst_path)
        with _lock:
            if _conn is None:
                q("SELECT 1")
            _conn.backup(dst)
        dst.close()
        return dst_path.read_bytes()
