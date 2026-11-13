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
    "price_tolerance_abs": 40000.0,      # ... and absolute rupees; the tighter of the two applies
    "arithmetic_tolerance": 0.05,        # rounding slack on sums
    "small_difference_max": 10.0,        # Rs.: amount gaps up to this are rounding, never a problem (totals, lines, PO, repeats)
    "high_value_threshold": 2500000.0,   # Rs. 25 lakh: above this a controller signs off, even if matched
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
          trace_json TEXT, events_json TEXT, archived_at TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS ledger (
          id {pk}, vendor_id TEXT, invoice_number TEXT, invoice_number_norm TEXT,
          invoice_date TEXT, net_amount DOUBLE PRECISION, total_amount DOUBLE PRECISION, po_number TEXT, status TEXT,
          posted_at TEXT, payment_ref TEXT, note TEXT, source_run_id TEXT, sha256 TEXT)""",
        f"CREATE TABLE IF NOT EXISTS audit (id {pk}, run_id TEXT, ts TEXT, actor TEXT, action TEXT, note TEXT)",
        "CREATE TABLE IF NOT EXISTS policy (key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS extraction_cache (sha256 TEXT PRIMARY KEY, extractor TEXT, data_json TEXT, created_at TEXT)",
        f"CREATE TABLE IF NOT EXISTS files (run_id TEXT PRIMARY KEY, mime TEXT, data {blob}, created_at TEXT)",
        # Vendors are learned, not loaded from a master file: from the payment history, or from a new vendor's first invoice.
        """CREATE TABLE IF NOT EXISTS vendors (
          vendor_id TEXT PRIMARY KEY, legal_name TEXT, aliases_json TEXT, gstin TEXT, email_domain TEXT, phone TEXT,
          bank_name TEXT, ifsc TEXT, account_number TEXT, payment_terms TEXT, status TEXT, status_reason TEXT,
          source TEXT, first_run_id TEXT, created_at TEXT, verified_at TEXT, verified_by TEXT)""",
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
            for t in ("runs", "ledger", "audit", "policy", "files", "vendors"):
                q(f"DROP TABLE IF EXISTS {t}")
        for stmt in _schema():
            q(stmt)
        # additive migration for databases created before archiving existed
        if "archived_at" not in {c["name"] for c in q("PRAGMA table_info(runs)", fetch="all")}:
            q("ALTER TABLE runs ADD COLUMN archived_at TEXT")
        seed = master()
        if q("SELECT COUNT(*) AS n FROM vendors", fetch="one")["n"] == 0:
            for e in seed["payment_history"]:  # anyone we've already paid is a known, verified vendor
                if not vendor_get(e["vendor_id"]):
                    b = e["paid_to"]
                    q("INSERT INTO vendors (vendor_id, legal_name, aliases_json, gstin, email_domain, phone, bank_name, ifsc, account_number,"
                      " payment_terms, status, source, created_at, verified_at, verified_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (e["vendor_id"], e["vendor_name"], json.dumps(e.get("aliases") or []), e["gstin"], e["email_domain"], e["phone"],
                       b["bank_name"], b["ifsc"], b["account_number"], e["payment_terms"], "active", "payment history",
                       e["posted_at"], e["posted_at"], "payment history"))
        if q("SELECT COUNT(*) AS n FROM ledger", fetch="one")["n"] == 0:
            for e in seed["payment_history"]:
                q("INSERT INTO ledger (vendor_id, invoice_number, invoice_number_norm, invoice_date, net_amount, total_amount,"
                  " po_number, status, posted_at, payment_ref, note, source_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (e["vendor_id"], e["invoice_number"], norm_invoice_no(e["invoice_number"]), e["invoice_date"],
                   e["net_amount"], e["total_amount"], e["po_number"], e["status"], e["posted_at"], e["payment_ref"],
                   e.get("note"), "seed"))
        for k, v in DEFAULT_POLICY.items():
            q("INSERT INTO policy (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING", (k, json.dumps(v)))


# ---------------------------------------------------------------- vendors (learned, no master file)
def _vendor(r: dict | None) -> dict | None:
    """Row -> the shape the rules engine uses."""
    if not r:
        return None
    return {"vendor_id": r["vendor_id"], "legal_name": r["legal_name"], "aliases": json.loads(r["aliases_json"] or "[]"),
            "tax_id": r["gstin"] or "", "email_domain": r["email_domain"] or "", "phone_on_file": r["phone"],
            "bank": {"bank_name": r["bank_name"], "ifsc": r["ifsc"] or "", "account_number": r["account_number"] or ""},
            "payment_terms": r["payment_terms"] or "Net 30", "status": r["status"], "status_reason": r["status_reason"],
            "source": r["source"], "first_run_id": r["first_run_id"], "created_at": r["created_at"],
            "verified_at": r["verified_at"], "verified_by": r["verified_by"]}


def vendors_all() -> list[dict]:
    return [_vendor(r) for r in q("SELECT * FROM vendors ORDER BY vendor_id", fetch="all")]


def vendor_get(vendor_id: str) -> dict | None:
    return _vendor(q("SELECT * FROM vendors WHERE vendor_id = ?", (vendor_id,), fetch="one"))


def vendor_add_from_invoice(inv: dict, run_id: str) -> dict:
    """First invoice from someone new: add them as 'unverified' using what the invoice says. The details become the
    reference for later invoices, but nothing is paid until a person verifies the vendor once."""
    gstin = re.sub(r"\s", "", (inv.get("vendor_tax_id") or "")).upper() or None
    name = (inv.get("vendor_name") or "").strip() or "Unnamed vendor"
    with _lock:  # a batch can carry two invoices from the same new vendor: add them once
        if gstin:
            hit = q("SELECT * FROM vendors WHERE gstin = ?", (gstin,), fetch="one")
            if hit:
                return _vendor(hit)
        hit = q("SELECT * FROM vendors WHERE lower(legal_name) = lower(?)", (name,), fetch="one")
        if hit:
            return _vendor(hit)
        n = max([int(r["vendor_id"].split("-")[1]) for r in q("SELECT vendor_id FROM vendors", fetch="all")] or [1000]) + 1
        vid = f"V-{n}"
        bank = inv.get("bank_details") or {}
        email = (inv.get("vendor_email") or "")
        q("INSERT INTO vendors (vendor_id, legal_name, aliases_json, gstin, email_domain, phone, bank_name, ifsc, account_number,"
          " payment_terms, status, source, first_run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (vid, name, "[]", gstin, email.split("@")[-1].lower() if "@" in email else None, inv.get("vendor_phone"),
           bank.get("bank_name"), (bank.get("ifsc") or "").upper() or None, bank.get("account_number"),
           inv.get("payment_terms"), "unverified", "first invoice", run_id, now_iso()))
        add_audit(run_id, "system", "vendor_added", f"New vendor {vid} {name} added from this invoice, unverified.")
        return vendor_get(vid)


def vendor_set_status(vendor_id: str, status: str, actor: str, reason: str = "") -> dict:
    if status not in ("active", "blocked"):
        raise ValueError("status must be active or blocked")
    v = vendor_get(vendor_id)
    if not v:
        raise ValueError("vendor not found")
    if status == "active" and v["status"] == "unverified":
        q("UPDATE vendors SET status = 'active', status_reason = NULL, verified_at = ?, verified_by = ? WHERE vendor_id = ?",
          (now_iso(), actor, vendor_id))
    else:
        q("UPDATE vendors SET status = ?, status_reason = ? WHERE vendor_id = ?", (status, reason or None, vendor_id))
    if v.get("first_run_id"):
        add_audit(v["first_run_id"], actor, {"active": "vendor_verified" if v["status"] == "unverified" else "vendor_unblocked",
                                              "blocked": "vendor_blocked"}[status], f"{vendor_id} {v['legal_name']}: {reason or status}")
    return vendor_get(vendor_id)


def ping() -> bool:
    try:
        return q("SELECT 1 AS ok", fetch="one")["ok"] == 1
    except Exception:
        return False


def recover_interrupted() -> list[str]:
    """Runs still 'running' at startup were cut off by a restart or crash. Hold them for a person
    instead of leaving them spinning forever, and record why in the audit trail."""
    ids = [r["id"] for r in q("SELECT id FROM runs WHERE status = 'running'", fetch="all")]
    for rid in ids:
        q("UPDATE runs SET status = 'error', finished_at = ?, decision = 'HOLD', route_to = 'AP Specialist',"
          " review_status = 'pending', headline = ? WHERE id = ?",
          (now_iso(), "Processing was interrupted by a server restart. Nothing was paid; re-key it or upload it again.", rid))
        add_audit(rid, "system", "interrupted", "Server restarted while this invoice was being processed.")
    return ids


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


def list_runs(limit: int = 200, archived: bool | None = False) -> list[dict]:
    """archived=False: the working list (default). True: only archived. None: everything (exports, vendor history)."""
    where = {False: "WHERE archived_at IS NULL", True: "WHERE archived_at IS NOT NULL", None: ""}[archived]
    return q("SELECT id, created_at, finished_at, filename, source, status, decision, route_to, headline, vendor_id, vendor_name,"
             f" invoice_number, total, currency, po_number, duration_ms, extractor, review_status, archived_at FROM runs {where}"
             " ORDER BY created_at DESC, id DESC LIMIT ?", (limit,), fetch="all")


def archive_resolved(actor: str) -> int:
    """Clear finished work off the working lists. Nothing is deleted: archived runs keep their trace, documents
    and audit trail, and still count for duplicate detection and PO balances (the ledger is untouched).
    Invoices still waiting for a person are never archived."""
    rows = q("SELECT id FROM runs WHERE archived_at IS NULL AND status != 'running'"
             " AND NOT (decision = 'HOLD' AND (review_status IS NULL OR review_status = 'pending'))", fetch="all")
    ts = now_iso()
    for r in rows:
        q("UPDATE runs SET archived_at = ? WHERE id = ?", (ts, r["id"]))
        add_audit(r["id"], actor, "archived", "Moved off the working list. Kept for audit and duplicate checks.")
    return len(rows)


def restore_run(run_id: str, actor: str) -> bool:
    r = q("SELECT archived_at FROM runs WHERE id = ?", (run_id,), fetch="one")
    if not r or not r["archived_at"]:
        return False
    q("UPDATE runs SET archived_at = NULL WHERE id = ?", (run_id,))
    add_audit(run_id, actor, "restored", "Back on the working list.")
    return True


def runs_with_sha(sha: str, exclude: str) -> list[dict]:
    # A run a reviewer re-keyed ("corrected") is superseded by its replacement, so it is not a prior copy.
    return q("SELECT id, created_at, decision, invoice_number FROM runs WHERE sha256 = ? AND id != ? AND status = 'done'"
             " AND (review_status IS NULL OR review_status != 'corrected')", (sha, exclude), fetch="all")


def open_runs_for_vendor(vendor_id: str, exclude: str) -> list[dict]:
    """Invoices sitting in the review queue (not yet posted) - also count for duplicate checks."""
    return q("SELECT id, created_at, invoice_number, total FROM runs WHERE vendor_id = ? AND id != ? AND decision = 'HOLD'"
             " AND (review_status IS NULL OR review_status = 'pending')", (vendor_id, exclude), fetch="all")


def runs_for_analytics(limit: int = 5000, include_archived: bool = False) -> list[dict]:
    extra = "" if include_archived else " AND archived_at IS NULL"
    rows = q("SELECT id, created_at, finished_at, decision, review_status, route_to, total, vendor_name, duration_ms,"
             f" extractor, trace_json FROM runs WHERE status != 'running'{extra} ORDER BY created_at, id LIMIT ?", (limit,), fetch="all")
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
