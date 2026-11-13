"""The invoice-to-decision pipeline.

  intake -> analyze -> extract -> validate -> vendor -> duplicates -> po_match -> decision

Every stage emits a start and an end event (streamed live to the UI) and returns a set of
*checks*. A check is the atomic unit of explanation:

  {id, label, status: pass|warn|fail|info|skip, detail, evidence, outcome: None|hold|reject, route}

The decision engine is a pure function of the checks: any reject -> REJECT; otherwise any
hold -> HOLD, routed to the highest-priority queue; otherwise APPROVE. That keeps every
decision explainable ("which checks fired, with what evidence") and testable.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from . import db
from .extract import ExtractionError, extract_heuristic, extract_with_gemini, llm_available

PACING_MS = int(os.environ.get("DEMO_PACING_MS", "450"))  # min visible time per stage so humans can follow along
TODAY = lambda: date.fromisoformat(os.environ["AP_TODAY"]) if os.environ.get("AP_TODAY") else date.today()

STAGES = [
    ("intake", "Intake"),
    ("analyze", "Document analysis"),
    ("extract", "AI extraction"),
    ("validate", "Validation"),
    ("vendor", "Vendor verification"),
    ("duplicates", "Duplicate detection"),
    ("po_match", "PO & receipt matching"),
    ("decision", "Decision"),
]

# Queue priority: the most specialised owner of a hold wins the routing.
QUEUES = {
    "fraud": {"name": "Treasury & Fraud", "priority": 1, "sla": "Same day"},
    "controller": {"name": "Controller approval", "priority": 2, "sla": "1 business day"},
    "procurement": {"name": "Procurement / Buyer", "priority": 3, "sla": "2 business days"},
    "vendor_mgmt": {"name": "Vendor Management", "priority": 4, "sla": "2 business days"},
    "ap": {"name": "AP Specialist", "priority": 5, "sla": "1 business day"},
}

# Live event buffers for runs in flight (also persisted to SQLite when each stage ends).
LIVE: dict[str, list] = {}
LIVE_LOCK = threading.Lock()


# ============================================================ helpers
SMALL_DIFF = 10.0  # ₹: default for policy small_difference_max. Gaps this small are rounding, never a threat.


def small(P: dict) -> float:
    return float(P.get("small_difference_max", SMALL_DIFF))


GST_SLABS = (0.25, 3, 5, 12, 18, 28, 40)  # GST rate slabs in India (combined CGST+SGST or IGST)


def inr(x: float) -> str:
    """Indian digit grouping: 1234567.5 -> '12,34,567.50'."""
    neg, x = x < 0, abs(round(float(x), 2))
    whole, frac = f"{x:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    while len(head) > 2:
        tail, head = head[-2:] + "," + tail, head[:-2]
    return ("-" if neg else "") + (head + "," + tail if head else tail) + "." + frac


def money(x: float | None) -> str:
    return "n/a" if x is None else "₹" + inr(x)


def chk(id, label, status, detail, outcome=None, route=None, evidence=None) -> dict:
    return {"id": id, "label": label, "status": status, "detail": detail, "outcome": outcome,
            "route": route, "evidence": evidence or {}}


_STOP = {"of", "the", "and", "a", "an", "to", "for", "in", "x", "with", "per"}


def tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if t not in _STOP}


def text_sim(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(jac, seq * 0.9), 3)


def norm_company(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"\b(inc|llc|ltd|co|corp|corporation|company|limited|n\.a)\b\.?", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def parse_terms(terms: str | None) -> dict:
    t = (terms or "").lower()
    net = re.search(r"net\s*(\d+)", t)
    disc = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+)", t)
    return {"net_days": int(net.group(1)) if net else 30,
            "discount_pct": float(disc.group(1)) if disc else 0.0,
            "discount_days": int(disc.group(2)) if disc else 0}


def iso(d: str | None) -> date | None:
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


def r2(x):
    return None if x is None else round(float(x), 2)


# ============================================================ run orchestration
def start_run(pdf_bytes: bytes, filename: str, source: str, manual: dict | None = None, saved_read: bool = False) -> str:
    """Start a run in the background. `manual` = fields a person keyed in (skips the AI read, every rule still runs).
    `saved_read` = reuse a stored AI read of this exact file if there is one (used to reload the demo samples)."""
    run_id = datetime.now().strftime("R%y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    db.put_file(run_id, pdf_bytes)
    db.insert_run({"id": run_id, "created_at": db.now_iso(), "filename": filename, "source": source,
                   "sha256": hashlib.sha256(pdf_bytes).hexdigest(), "status": "running",
                   "trace_json": "{}", "events_json": "[]"})
    with LIVE_LOCK:
        LIVE[run_id] = []
    threading.Thread(target=_run, args=(run_id, pdf_bytes, filename, manual, saved_read), daemon=True).start()
    return run_id


def events_since(run_id: str, idx: int) -> list:
    with LIVE_LOCK:
        buf = LIVE.get(run_id)
        if buf is not None:
            return buf[idx:]
    r = db.get_run(run_id)
    return (r or {}).get("events", [])[idx:]


def _emit(run_id: str, etype: str, stage: str | None = None, **data) -> None:
    with LIVE_LOCK:
        buf = LIVE.setdefault(run_id, [])
        buf.append({"seq": len(buf), "ts": db.now_iso(), "type": etype, "stage": stage, **data})


def _persist_events(run_id: str, trace: dict) -> None:
    import json
    with LIVE_LOCK:
        evs = list(LIVE.get(run_id, []))
    db.update_run(run_id, events_json=json.dumps(evs, default=str), trace_json=json.dumps(trace, default=str))


def _run(run_id: str, pdf: bytes, filename: str, manual: dict | None = None, saved_read: bool = False) -> None:
    import json
    t_start = time.time()
    ctx = {"run_id": run_id, "pdf": pdf, "filename": filename, "policy": db.get_policy(),
           "master": db.master(), "trace": {"stages": {}}, "manual": manual, "saved_read": saved_read}
    trace = ctx["trace"]
    _emit(run_id, "run_start", stages=[{"key": k, "label": l} for k, l in STAGES], policy=ctx["policy"])
    try:
        for key, label in STAGES:
            t0 = time.time()
            _emit(run_id, "stage_start", key, label=label)
            fn = STAGE_FNS[key]
            if ctx.get("halt") and key not in ("decision",):
                out = {"summary": "Skipped: an earlier stage could not produce usable data.", "checks": [], "data": {}, "skipped": True}
            else:
                out = fn(ctx)
            elapsed = time.time() - t0
            if PACING_MS and not saved_read and elapsed * 1000 < PACING_MS:
                time.sleep(PACING_MS / 1000 - elapsed)
            out["duration_ms"] = int(elapsed * 1000)  # real work time; the demo pause above is not counted
            trace["stages"][key] = out
            _emit(run_id, "stage_end", key, label=label, **out)
            _persist_events(run_id, trace)

        dec = trace["decision"]
        inv = ctx.get("inv") or {}
        vendor = ctx.get("vendor") or {}
        db.update_run(
            run_id, status="done", finished_at=db.now_iso(), decision=dec["decision"], route_to=dec.get("route_name"),
            headline=dec["headline"], vendor_id=vendor.get("vendor_id"),
            vendor_name=vendor.get("legal_name") or inv.get("vendor_name"), invoice_number=inv.get("invoice_number"),
            total=inv.get("total_amount"), currency=inv.get("currency") or "INR",
            po_number=(ctx.get("po") or {}).get("po_number"), duration_ms=int((time.time() - t_start) * 1000),
            extractor=(ctx.get("extract_meta") or {}).get("extractor"),
            review_status="pending" if dec["decision"] == "HOLD" else None,
        )
        if dec["decision"] == "APPROVE":
            _post(ctx, "auto-approved")
            db.add_audit(run_id, "system", "auto_approved", dec["headline"])
        elif dec["decision"] == "NOT_INVOICE":
            db.add_audit(run_id, "system", "filed", dec["headline"])
        else:
            db.add_audit(run_id, "system", dec["decision"].lower(), dec["headline"])
        _emit(run_id, "run_end", decision=dec["decision"], duration_ms=int((time.time() - t_start) * 1000))
    except Exception as e:  # never leave a run hanging. Surface the failure as a held invoice.
        tb = traceback.format_exc()
        trace["error"] = {"message": str(e), "traceback": tb}
        _emit(run_id, "run_error", message=str(e))
        db.update_run(run_id, status="error", finished_at=db.now_iso(), decision="HOLD", route_to=QUEUES["ap"]["name"],
                      headline=f"Processing error: {e}. Routed to AP for manual handling.", review_status="pending",
                      duration_ms=int((time.time() - t_start) * 1000))
    finally:
        _persist_events(run_id, trace)
        with LIVE_LOCK:
            LIVE.pop(run_id, None)


def _post(ctx: dict, note: str) -> None:
    inv, vendor = ctx["inv"], ctx["vendor"]
    db.post_to_ledger(ctx["run_id"], inv, vendor["vendor_id"], (ctx.get("po") or {}).get("po_number"),
                      ctx["net_amount"], ctx["sha"], note)


def human_action(run_id: str, action: str, actor: str, note: str) -> dict:
    """Reviewer decision on a held invoice. Approval posts to the ledger; everything is audited."""
    import json
    run = db.get_run(run_id)
    if not run:
        raise ValueError("run not found")
    if run["decision"] != "HOLD" or run.get("review_status") not in (None, "pending"):
        raise ValueError("only pending held invoices can be actioned")
    if not note.strip():
        raise ValueError("a note is required. It goes into the audit trail.")
    if action == "approve":
        tr = run["trace"]
        inv = tr.get("stages", {}).get("extract", {}).get("data", {}).get("fields")
        vend = tr.get("stages", {}).get("vendor", {}).get("data", {}).get("vendor")
        if not inv or not vend:
            raise ValueError("cannot approve. The invoice has no extracted data or matched vendor.")
        net = tr.get("stages", {}).get("po_match", {}).get("data", {}).get("net_amount") or inv.get("total_amount")
        partial = tr.get("decision", {}).get("partial_approval_amount")
        db.post_to_ledger(run_id, inv, vend["vendor_id"], run.get("po_number"), float(net), run["sha256"],
                          f"approved by {actor}: {note}")
        db.update_run(run_id, review_status="approved")
        db.add_audit(run_id, actor, "approved_override", note)
    elif action == "reject":
        db.update_run(run_id, review_status="rejected")
        db.add_audit(run_id, actor, "rejected", note)
    else:
        raise ValueError("unknown action")
    return db.get_run(run_id)


MANUAL_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "due_date", "po_reference", "payment_terms", "currency",
                 "vendor_email", "subtotal", "discount_amount", "tax_amount", "shipping_amount", "round_off", "total_amount", "line_items")


def rekey(run_id: str, fields: dict, actor: str, note: str) -> str:
    """A reviewer corrects or types in the invoice fields; the document is re-checked as a new run.
    The original is marked 'corrected' (superseded), keeps its own trace, and both are linked in the audit trail."""
    run = db.get_run(run_id)
    if not run:
        raise ValueError("run not found")
    misfiled = run["decision"] == "NOT_INVOICE" and run.get("review_status") is None
    if not misfiled and (run["decision"] != "HOLD" or run.get("review_status") not in (None, "pending")):
        raise ValueError("only invoices waiting for review (or documents filed as not-an-invoice) can be corrected")
    if not note.strip():
        raise ValueError("a note is required. It goes into the audit trail.")
    clean = {k: fields.get(k) for k in MANUAL_FIELDS}
    if not clean.get("invoice_number") or not clean.get("total_amount"):
        raise ValueError("at least the invoice number and the total are needed")
    f = db.get_file(run_id)
    if not f:
        raise ValueError("the original document is no longer stored")
    clean["_actor"] = actor
    new_id = start_run(f[0], run["filename"], f"re-keyed from {run_id}", manual=clean)
    db.update_run(run_id, review_status="corrected")
    db.add_audit(run_id, actor, "corrected", f"{note} → re-checked as {new_id}")
    db.add_audit(new_id, actor, "keyed_in", f"Corrected from {run_id}: {note}")
    return new_id


def _po_is_vendors(po: dict, vendor: dict) -> bool:
    """POs name the vendor by ID and GSTIN, so a vendor re-learned from an invoice still matches POs raised for them."""
    g = (vendor.get("tax_id") or "").upper()
    return po["vendor_id"] == vendor["vendor_id"] or bool(g and g == (po.get("vendor_gstin") or "").upper())


def recheck(run_id: str, actor: str, note: str) -> str:
    """Run the same document through every rule again (e.g. after its vendor was verified). The original is
    marked superseded first, so the new run isn't flagged as a duplicate of it."""
    run = db.get_run(run_id)
    if not run or run["decision"] != "HOLD" or run.get("review_status") not in (None, "pending"):
        raise ValueError("only invoices waiting for review can be re-checked")
    f = db.get_file(run_id)
    if not f:
        raise ValueError("the original document is no longer stored")
    db.update_run(run_id, review_status="corrected")
    new_id = start_run(f[0], run["filename"], f"re-checked from {run_id}", saved_read=True)
    db.add_audit(run_id, actor, "rechecked", f"{note} → re-checked as {new_id}")
    db.add_audit(new_id, actor, "rechecked_from", f"Re-checked from {run_id}: {note}")
    return new_id


# ============================================================ stages
FORMAT_NAMES = {"pdf": "PDF", "jpeg": "JPEG photo", "png": "PNG image", "webp": "WEBP image", "heic": "HEIC photo (iPhone)"}


def detect_format(b: bytes) -> str | None:
    """Identify the file by its magic bytes. Never trust the extension."""
    if b[:5] == b"%PDF-":
        return "pdf"
    if b[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b[4:8] == b"ftyp" and b[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
        return "heic"
    return None


def image_to_pdf(b: bytes) -> tuple[bytes, dict]:
    """Normalise a photo into a one-page PDF so every downstream stage sees the same input type."""
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    img = Image.open(io.BytesIO(b))
    orig = img.size
    img = ImageOps.exif_transpose(img)  # phones store rotation in EXIF
    img = img.convert("RGB")
    img.thumbnail((2400, 2400))  # plenty for reading, keeps the request small
    out = io.BytesIO()
    img.save(out, format="PDF", resolution=200)
    return out.getvalue(), {"original_px": f"{orig[0]}×{orig[1]}", "pdf_px": f"{img.size[0]}×{img.size[1]}"}


def st_intake(ctx):
    raw = ctx["pdf"]
    sha = hashlib.sha256(raw).hexdigest()  # fingerprint the file as received
    ctx["sha"] = sha
    checks, data = [], {"filename": ctx["filename"], "bytes": len(raw), "sha256": sha}
    fmt = detect_format(raw)
    data["format"] = fmt
    if fmt is None:
        checks.append(chk("intake.format", "Supported file type", "fail",
                          "Not a PDF or image (supported: PDF, JPG, PNG, WEBP, HEIC). Ask the sender for a PDF.", "reject"))
        ctx["halt"] = True
        return {"summary": f"Received {ctx['filename']}: unsupported file type.", "checks": checks, "data": data}
    if fmt == "pdf":
        checks.append(chk("intake.format", "Supported file type", "pass", "PDF document"))
        summary = f"Received {ctx['filename']} ({len(raw)/1024:.0f} KB PDF). Fingerprint {sha[:12]}…"
    else:
        try:
            pdf, info = image_to_pdf(raw)
        except Exception as e:
            checks.append(chk("intake.format", "Supported file type", "fail", f"Image could not be decoded ({e})", "hold", "ap"))
            ctx["halt"] = True
            return {"summary": "Image could not be decoded.", "checks": checks, "data": data}
        ctx["pdf"] = pdf
        db.put_file(ctx["run_id"], pdf)  # what the reviewer sees in the viewer
        data.update(info)
        checks.append(chk("intake.format", "Supported file type", "pass",
                          f"{FORMAT_NAMES[fmt]} ({info['original_px']} px). Auto-rotated and converted to a one-page PDF, then read like a scan."))
        summary = f"Received a {FORMAT_NAMES[fmt]} ({len(raw)/1024:.0f} KB). Converted to PDF for reading. Fingerprint {sha[:12]}…"
    return {"summary": summary, "checks": checks, "data": data}


def st_analyze(ctx):
    import pdfplumber
    checks, pages_info, text = [], [], ""
    try:
        with pdfplumber.open(io.BytesIO(ctx["pdf"])) as p:
            for i, page in enumerate(p.pages):
                t = page.extract_text() or ""
                pages_info.append({"page": i + 1, "chars": len(t), "images": len(page.images)})
                text += t + "\n"
    except Exception as e:
        ctx["halt"] = True
        return {"summary": "PDF could not be opened (corrupt or encrypted).",
                "checks": [chk("analyze.open", "PDF readable", "fail", str(e), "hold", "ap")], "data": {}}
    ctx["text"] = text
    chars = sum(p["chars"] for p in pages_info)
    scanned = chars < 40
    ctx["scanned"] = scanned
    photo = (ctx["trace"]["stages"].get("intake", {}).get("data", {}).get("format") or "pdf") != "pdf"
    kind = ("Photo (no text layer)" if photo else "Scanned image (no text layer)") if scanned else "Digital PDF (machine-readable text)"
    if llm_available():
        path = ("Gemini reads the page image (vision). The text-grounding check is not possible."
                if scanned else "Gemini reads the PDF. Extracted values are then grounded against the text layer.")
    else:
        path = ("No AI reader configured and no text layer. This needs manual keying."
                if scanned else "Heuristic text parser (no AI reader configured).")
    checks.append(chk("analyze.type", "Document type", "info", kind, evidence={"pages": pages_info}))
    if len(pages_info) > 5:
        checks.append(chk("analyze.pages", "Page count", "warn", f"{len(pages_info)} pages. It may be several documents in one file."))
    return {"summary": f"{kind}, {len(pages_info)} page(s). {path}",
            "checks": checks, "data": {"pages": pages_info, "text_chars": chars, "scanned": scanned,
                                       "text_preview": text[:1500]}}


def st_extract(ctx):
    checks, meta, data, notes = [], None, None, []
    if ctx.get("manual"):
        m = ctx["manual"]
        data = {k: v for k, v in m.items() if k not in ("_actor",)}
        keyed = [k for k, v in data.items() if v not in (None, "", [])]
        data["field_confidence"] = {k: 1.0 for k in keyed}  # a person read it off the document
        data.setdefault("document_type", "invoice")
        meta = {"extractor": "manual", "model": None, "latency_ms": 0}
        notes.append(f"Keyed in by {m.get('_actor') or 'a reviewer'}. The AI read was skipped; every rule still runs.")
    elif ctx.get("saved_read") and (saved := db.cache_get(ctx["sha"])):
        data, meta = saved["data"], {"extractor": f"{saved['extractor']} (cached)", "model": None, "latency_ms": 0}
        notes.append("Reloaded sample: reusing the stored AI read of this exact file. Every rule still runs.")
    elif llm_available():
        try:
            data, meta = extract_with_gemini(ctx["pdf"], ctx.get("text", ""))
            db.cache_put(ctx["sha"], "gemini", data)
        except ExtractionError as e:
            notes.append(f"Gemini extraction failed: {e}.")
            cached = db.cache_get(ctx["sha"])
            if cached:
                data, meta = cached["data"], {"extractor": f"{cached['extractor']} (cached)", "model": None, "latency_ms": 0}
                notes.append("Using a previous extraction of this exact file.")
    else:
        notes.append("No GEMINI_API_KEY configured.")
    if data is None:
        try:
            data, meta = extract_heuristic(ctx.get("text", ""))
            notes.append("Fell back to the heuristic text parser.")
        except ExtractionError as e:
            ctx["halt"] = True
            ctx["extract_meta"] = {"extractor": "none"}
            checks.append(chk("extract.ok", "Fields extracted", "fail", f"{e}. Needs manual keying.", "hold", "ap"))
            return {"summary": "Could not read this document automatically.", "checks": checks,
                    "data": {"notes": notes}}
    ctx["extract_meta"] = meta
    inv = _normalise(data)
    ctx["inv"] = inv
    n_fields = sum(1 for k, v in inv.items() if v not in (None, [], {}) and not isinstance(v, (dict, list)))
    checks.append(chk("extract.ok", "Fields extracted", "pass",
                      f"{n_fields} header fields and {len(inv['line_items'])} line item(s) via {meta['extractor']}"
                      + (f" ({meta.get('model')}, {meta.get('latency_ms')} ms)" if meta.get("model") else "")))
    for w in inv.get("extraction_warnings") or []:
        checks.append(chk("extract.warning", "Reader note", "warn" if meta["extractor"] != "heuristic" else "info", w))
    return {"summary": f"Read {inv.get('vendor_name') or 'unknown vendor'} · {inv.get('invoice_number') or 'no invoice #'} · "
                       f"{money(inv.get('total_amount'))} using {meta['extractor']}.",
            "checks": checks, "data": {"fields": inv, "meta": meta, "notes": notes}}


def _normalise(d: dict) -> dict:
    inv = dict(d)
    for k in ("subtotal", "tax_amount", "shipping_amount", "total_amount", "tax_rate_pct", "round_off", "discount_amount"):
        inv[k] = r2(inv.get(k))
    inv["line_items"] = [{"description": li.get("description") or "", "quantity": li.get("quantity"),
                          "unit_price": r2(li.get("unit_price")), "amount": r2(li.get("amount"))}
                         for li in (inv.get("line_items") or [])]
    for k in ("invoice_number", "po_reference", "vendor_name"):
        if isinstance(inv.get(k), str):
            inv[k] = inv[k].strip() or None
    inv["currency"] = (inv.get("currency") or "INR").upper()
    # Reconcile a common reader slip: freight listed as a line item AND reported again as shipping.
    ship, sub, tax, tot = inv.get("shipping_amount"), inv.get("subtotal"), inv.get("tax_amount") or 0, inv.get("total_amount")
    if ship and sub is not None and tot is not None and not inv.get("tax_inclusive"):
        line_sum = sum(li["amount"] or 0 for li in inv["line_items"])
        in_lines = any(li["amount"] is not None and abs(li["amount"] - ship) < 0.005 for li in inv["line_items"])
        if in_lines and abs(line_sum - sub) <= 0.05 and abs(sub + tax - tot) <= 0.05 and abs(sub + tax + ship - tot) > 0.05:
            inv["shipping_amount"] = None
            inv["extraction_warnings"] = list(inv.get("extraction_warnings") or []) + [
                f"Shipping {ship:,.2f} was also listed as a line item and is already in the subtotal. Counted once."]
    inv["bank_details"] = inv.get("bank_details") or {}
    inv["field_confidence"] = inv.get("field_confidence") or {}
    return inv


def st_validate(ctx):
    inv, P, checks = ctx["inv"], ctx["policy"], []
    company = ctx["master"]["company"]
    tol = P["arithmetic_tolerance"]
    sd = small(P)

    # document type
    dt = inv.get("document_type")
    if dt == "invoice":
        checks.append(chk("val.doctype", "Is an invoice", "pass", "Document classified as an invoice"))
    elif dt == "credit_note":
        checks.append(chk("val.doctype", "Is an invoice", "fail", "This is a credit note. It goes to the credits workflow, not payment.", "hold", "ap"))
    else:
        # Quotes, statements, POs, receipts, delivery notes: nothing to pay, and nothing wrong with the sender.
        # File them instead of rejecting, so they don't inflate rejection/exception metrics, and stop here:
        # invoice checks (required fields, PO match, duplicates) don't apply to a non-invoice.
        label = DOC_TYPE_NAMES.get(dt, dt or "unknown document")
        ctx["not_invoice"] = label
        ctx["halt"] = True
        checks.append(chk("val.doctype", "Is an invoice", "info", f"This is a {label}, not an invoice. Filed for reference; nothing to pay.", "file"))
        return {"summary": f"Not an invoice: this is a {label}. Filed, not paid.", "checks": checks, "data": {}}

    # required fields
    missing_vendor_fix = [f for f, lbl in (("invoice_number", "invoice number"), ("invoice_date", "invoice date")) if not inv.get(f)]
    missing_internal = [f for f in ("vendor_name", "total_amount") if inv.get(f) in (None, "")]
    if missing_vendor_fix:
        checks.append(chk("val.required", "Required fields present", "fail",
                          "Missing " + " and ".join(f.replace("_", " ") for f in missing_vendor_fix)
                          + ". The invoice can't be posted or checked for duplicates without them, so it must be reissued by the vendor.",
                          "reject", evidence={"missing": missing_vendor_fix}))
        ctx["return_to_vendor"] = missing_vendor_fix
    elif missing_internal:
        checks.append(chk("val.required", "Required fields present", "fail",
                          "Could not find " + ", ".join(missing_internal), "hold", "ap"))
    else:
        checks.append(chk("val.required", "Required fields present", "pass", "Invoice number, date, vendor and total all present"))

    # line arithmetic
    lines = inv["line_items"]
    bad = [li for li in lines if None not in (li["quantity"], li["unit_price"], li["amount"])
           and abs(li["quantity"] * li["unit_price"] - li["amount"]) > max(sd, 0.005 * abs(li["amount"]))]
    line_sum = r2(sum(li["amount"] or 0 for li in lines))
    sub = inv.get("subtotal") if inv.get("subtotal") is not None else line_sum
    if bad:
        checks.append(chk("val.lines", "Line math (qty × price = amount)", "fail",
                          f"{len(bad)} line(s) don't multiply out: " + "; ".join(b["description"][:40] for b in bad), "hold", "ap"))
    elif lines:
        checks.append(chk("val.lines", "Line math (qty × price = amount)", "pass", f"All {len(lines)} lines multiply out"))
    if lines and inv.get("subtotal") is not None:
        g = abs(line_sum - inv["subtotal"])
        ok = g <= sd
        checks.append(chk("val.subtotal", "Lines sum to subtotal", "pass" if ok else "fail",
                          f"Σ lines {money(line_sum)} vs subtotal {money(inv['subtotal'])}"
                          + (f" (off by {money(g)}: under {money(sd)}, treated as rounding)" if tol < g <= sd else ""),
                          None if ok else "hold", None if ok else "ap"))

    # totals & tax (tax may be separate or embedded)
    total, tax, ship = inv.get("total_amount"), inv.get("tax_amount") or 0.0, inv.get("shipping_amount") or 0.0
    ro = inv.get("round_off") or 0.0
    disc = abs(inv.get("discount_amount") or 0.0)
    if disc and sub:
        pct_d = 100 * disc / sub
        checks.append(chk("val.discount", "Discount detected", "pass" if pct_d <= 30 else "warn",
                          f"Discount of {money(disc)} ({pct_d:.2f}% of {money(sub)}) deducted before GST; taxable value {money(sub - disc)}"
                          + ("" if pct_d <= 30 else ". Unusually large: worth a glance."), evidence={"discount": disc, "pct": round(pct_d, 2)}))
    if total is not None and sub is not None:
        expected = (sub - disc) if inv.get("tax_inclusive") else r2(sub - disc + tax + ship)
        how = "tax-inclusive pricing" if inv.get("tax_inclusive") else (f"subtotal {money(sub)}" + (f" − discount {money(disc)}" if disc else "")
                                                                         + f" + tax {money(tax)}" + (f" + shipping {money(ship)}" if ship else ""))
        gap = r2(total - expected)
        if abs(gap) <= tol:
            checks.append(chk("val.total", "Totals reconcile", "pass", f"{how} = {money(expected)} vs stated total {money(total)}"))
        elif abs(gap) <= 1.0 and (abs(gap - ro) <= tol or abs(total - round(total)) <= tol):
            # Indian invoices round the payable to the nearest rupee ("Round Off -0.40"): accept it, and say so.
            checks.append(chk("val.total", "Totals reconcile", "pass",
                              f"{how} = {money(expected)}, rounded off by {'+' if gap > 0 else '−'}{money(abs(gap))} to {money(total)}"
                              + ("" if ro else " (no round-off line printed)")))
        elif abs(gap) <= sd:
            checks.append(chk("val.total", "Totals reconcile", "pass",
                              f"{how} = {money(expected)} vs stated total {money(total)}: a {money(abs(gap))} difference, "
                              f"under {money(sd)}, so it's treated as rounding, not a problem"))
        else:
            checks.append(chk("val.total", "Totals reconcile", "fail",
                              f"{how} = {money(expected)} vs stated total {money(total)}; off by {money(abs(gap))}, "
                              f"more than the {money(sd)} allowed for rounding", "hold", "ap"))
    net = (total - tax) if (inv.get("tax_inclusive") and total is not None) else ((sub or 0) - disc + ship)
    ctx["net_amount"] = r2(net)
    if tax and net:
        rate = 100 * tax / (net if not inv.get("tax_inclusive") else net)
        slab = min(GST_SLABS, key=lambda r: abs(r - rate))
        plausible = abs(slab - rate) <= 0.15 or (0 < rate <= 28 and len(inv.get("line_items") or []) > 1)  # mixed-rate lines blend
        checks.append(chk("val.tax", "GST rate is a valid slab", "pass" if plausible else "fail",
                          f"Effective GST {rate:.2f}% on {money(net)}" + (f" (the {slab:g}% slab)" if abs(slab - rate) <= 0.15 else "")
                          + ("" if plausible else f". Not a GST slab ({', '.join(f'{x:g}%' for x in GST_SLABS)})."),
                          None if plausible else "hold", None if plausible else "ap"))
    else:
        checks.append(chk("val.tax", "GST rate is a valid slab", "info", "No GST charged on this invoice (e.g. reverse charge, exempt supply, or GST billed separately)"))

    # dates
    d, due, today = iso(inv.get("invoice_date")), iso(inv.get("due_date")), TODAY()
    if d:
        if d > today + timedelta(days=1):
            checks.append(chk("val.date", "Invoice date sane", "fail", f"Invoice dated in the future ({d})", "hold", "ap"))
        elif (today - d).days > P["max_invoice_age_days"]:
            checks.append(chk("val.date", "Invoice date sane", "fail", f"Invoice is {(today - d).days} days old (stale-dated)", "hold", "ap"))
        else:
            checks.append(chk("val.date", "Invoice date sane", "pass", f"Dated {d.isoformat()} ({(today - d).days} days ago)"))
        if due and due < d:
            checks.append(chk("val.due", "Due date after invoice date", "warn", f"Due {due} is before invoice date {d}"))

    # extraction confidence on critical fields
    conf = inv.get("field_confidence") or {}
    low = {k: v for k, v in conf.items() if isinstance(v, (int, float)) and v < P["min_field_confidence"]
           and k in ("invoice_number", "total_amount", "vendor_name", "bank_details", "line_items")}
    if low:
        checks.append(chk("val.confidence", "Extraction confidence", "fail",
                          "Low confidence on " + ", ".join(f"{k} ({v:.2f})" for k, v in low.items()) + ". A human must verify against the image.",
                          "hold", "ap", evidence=conf))
    else:
        checks.append(chk("val.confidence", "Extraction confidence", "pass",
                          "All critical fields ≥ " + f"{P['min_field_confidence']:.2f}", evidence=conf))

    # grounding: anti-hallucination. Every extracted key value must literally appear in the text layer.
    text = ctx.get("text") or ""
    if text.strip():
        flat = re.sub(r"\s+", "", text)
        probes = {}
        if inv.get("invoice_number"):
            probes["invoice_number"] = re.sub(r"\s+", "", inv["invoice_number"]) in flat
        if total is not None:
            probes["total_amount"] = inr(total) in flat or f"{total:,.2f}" in flat or f"{total:.2f}" in flat
        if inv.get("po_reference"):
            probes["po_reference"] = re.sub(r"\s+", "", inv["po_reference"]) in flat
        acct = (inv.get("bank_details") or {}).get("account_number")
        if acct:
            probes["bank_account"] = acct in flat
        miss = [k for k, v in probes.items() if not v]
        checks.append(chk("val.grounding", "Values grounded in document text", "fail" if miss else "pass",
                          ("Not found verbatim in the PDF text: " + ", ".join(miss)) if miss
                          else f"{len(probes)} key values found verbatim in the PDF text layer",
                          "hold" if miss else None, "ap" if miss else None, evidence=probes))
    else:
        checks.append(chk("val.grounding", "Values grounded in document text", "skip",
                          "No text layer (scan). Relying on arithmetic consistency and confidence scores instead."))

    # bill-to entity
    bt = inv.get("bill_to_name")
    if bt:
        names = [company["legal_name"]] + company["aliases"]
        ok = max(text_sim(norm_company(bt), norm_company(n)) for n in names) >= 0.8
        checks.append(chk("val.billto", "Billed to our entity", "pass" if ok else "fail",
                          f"Bill-to '{bt}'" + ("" if ok else f" is not {company['legal_name']}"), None if ok else "hold", None if ok else "ap"))
    if inv.get("currency") != company["base_currency"]:
        checks.append(chk("val.currency", "Currency", "warn", f"Invoice in {inv['currency']}, books in {company['base_currency']}"))

    fails = sum(1 for c in checks if c["status"] == "fail")
    return {"summary": "All validation checks passed." if not fails else f"{fails} validation issue(s) found.",
            "checks": checks, "data": {"net_amount": ctx["net_amount"], "line_sum": line_sum}}


def st_vendor(ctx):
    inv, checks = ctx["inv"], []
    vendors = db.vendors_all()  # learned list: payment history + vendors added from their first invoice
    name = inv.get("vendor_name") or ""
    email_dom = (inv.get("vendor_email") or "").split("@")[-1].lower() or None
    gstin = (inv.get("vendor_tax_id") or "").replace(" ", "").replace("-", "").upper()
    scored = []
    for v in vendors:
        s = max(text_sim(norm_company(name), norm_company(n)) for n in [v["legal_name"]] + v["aliases"])
        if gstin and gstin == v["tax_id"].replace("-", "").upper():
            s = max(s, 0.97)
        scored.append((round(s, 3), v))
    scored.sort(key=lambda x: -x[0])
    best_s, best = scored[0] if scored else (0.0, None)
    cands = [{"vendor_id": v["vendor_id"], "name": v["legal_name"], "score": s} for s, v in scored[:3]]
    if best is None or best_s < 0.6:
        if not name.strip() and not gstin:
            checks.append(chk("ven.match", "Vendor on file", "fail", "No vendor name or GSTIN could be read, so the vendor can't be identified.",
                              "hold", "ap", evidence={"candidates": cands}))
            ctx["halt_po"] = True
            return {"summary": "Vendor could not be identified.", "checks": checks, "data": {"candidates": cands}}
        best = db.vendor_add_from_invoice(inv, ctx["run_id"])
        ctx["vendor"], ctx["new_vendor"] = best, True
        b = best["bank"]
        checks.append(chk("ven.match", "Vendor on file", "fail",
                          f"New vendor: {best['legal_name']} ({best['vendor_id']}) was added to the vendor list from this invoice"
                          + (f", GSTIN {best['tax_id']}" if best["tax_id"] else ", no GSTIN printed")
                          + (f", paid to {b['bank_name'] or 'a bank'} IFSC {b['ifsc'] or '—'} a/c ••••{b['account_number'][-4:]}" if b["account_number"] else "")
                          + ". Verify the vendor once before anything is paid; their details then become the reference for every later invoice.",
                          "hold", "vendor_mgmt", evidence={"candidates": cands, "vendor": best}))
        ctx["fraud_signals"] = []
        note = inv.get("payment_instruction_notes") or ""
        if re.search(r"(new|changed?|update[ds]?)\s+(bank|account|banking|remit)|changed banks", note, re.I):
            ctx["fraud_signals"].append("invoice announces a change of bank details")
            checks.append(chk("ven.change_notice", "Change-of-bank language", "fail",
                              f"Invoice text: “{note[:160]}”. A brand-new vendor asking to change banks is a red flag.", "hold", "fraud"))
        return {"summary": f"New vendor added: {best['legal_name']} ({best['vendor_id']}). Needs a one-time verification.",
                "checks": checks, "data": {"vendor": {k: best[k] for k in ("vendor_id", "legal_name", "status", "payment_terms", "phone_on_file", "email_domain")},
                                           "new_vendor": True, "candidates": cands, "fraud_signals": ctx["fraud_signals"]}}
    ctx["vendor"] = best
    checks.append(chk("ven.match", "Vendor on file", "pass" if best_s >= 0.85 else "warn",
                      f"Matched {best['vendor_id']} {best['legal_name']} (similarity {best_s:.2f}; known from {best.get('source') or 'the vendor list'})",
                      evidence={"candidates": cands}))

    if best["status"] == "blocked":
        checks.append(chk("ven.status", "Vendor is active", "fail",
                          f"Vendor is blocked for payment: {best.get('status_reason') or 'no reason recorded'}", "reject"))
    elif best["status"] == "unverified":
        checks.append(chk("ven.status", "Vendor is verified", "fail",
                          f"{best['legal_name']} was added on {(best.get('created_at') or '')[:10]} from their first invoice and hasn't been verified yet.",
                          "hold", "vendor_mgmt"))
    else:
        checks.append(chk("ven.status", "Vendor is active", "pass", "Verified, approved for payment"))

    if gstin and best["tax_id"]:
        ok = gstin == best["tax_id"].replace("-", "").upper()
        checks.append(chk("ven.taxid", "GSTIN matches vendor record", "pass" if ok else "fail",
                          f"Invoice {inv['vendor_tax_id']} vs on file {best['tax_id']}", None if ok else "hold", None if ok else "vendor_mgmt"))

    # Bank details: the single most important fraud control in AP.
    bank = inv.get("bank_details") or {}
    mb = best["bank"]
    fraud_signals = []
    if (bank.get("account_number") or bank.get("ifsc")) and mb["account_number"]:
        acct_ok = (bank.get("account_number") or "") == mb["account_number"]
        rout_ok = (bank.get("ifsc") or mb["ifsc"]).upper().replace(" ", "") == mb["ifsc"]
        if acct_ok and rout_ok:
            checks.append(chk("ven.bank", "Remit-to bank matches vendor record", "pass",
                              f"Account ••••{mb['account_number'][-4:]} at {mb['bank_name']} matches the account on file"))
        else:
            fraud_signals.append("bank account differs from the verified account on file")
            checks.append(chk("ven.bank", "Remit-to bank matches vendor record", "fail",
                              f"Invoice says {bank.get('bank_name') or 'a bank'} IFSC {bank.get('ifsc')} a/c ••••{(bank.get('account_number') or '????')[-4:]}; "
                              f"on file: {mb['bank_name']} ••••{mb['account_number'][-4:]}",
                              "hold", "fraud", evidence={"invoice": bank, "master_last4": mb["account_number"][-4:]}))
    else:
        checks.append(chk("ven.bank", "Remit-to bank matches vendor record", "info", "No remit details on the invoice (or none on file yet). Payment only ever goes to the verified account on file."))

    note = inv.get("payment_instruction_notes") or ""
    if re.search(r"(new|changed?|update[ds]?)\s+(bank|account|banking|remit)|changed banks", note, re.I):
        fraud_signals.append("invoice announces a change of bank details")
        checks.append(chk("ven.change_notice", "Change-of-bank language", "fail",
                          f"Invoice text: “{note[:160]}”. Bank changes are only accepted through vendor management, never from an invoice.",
                          "hold", "fraud"))
    if email_dom and best["email_domain"]:
        dom_ok = email_dom == best["email_domain"] or email_dom.endswith("." + best["email_domain"])
        if not dom_ok:
            lookalike = SequenceMatcher(None, email_dom, best["email_domain"]).ratio()
            if lookalike > 0.5:
                fraud_signals.append(f"look-alike sender domain ({email_dom})")
            checks.append(chk("ven.domain", "Sender domain matches vendor record", "fail" if lookalike > 0.5 else "warn",
                              f"Invoice email domain '{email_dom}' vs on file '{best['email_domain']}'"
                              + (f". Look-alike domain (similarity {lookalike:.2f})." if lookalike > 0.5 else ""),
                              "hold" if lookalike > 0.5 else None, "fraud" if lookalike > 0.5 else None))
        else:
            checks.append(chk("ven.domain", "Sender domain matches vendor record", "pass", f"{email_dom}"))
    ctx["fraud_signals"] = fraud_signals
    return {"summary": f"{best['legal_name']} ({best['vendor_id']})"
                       + (f". {len(fraud_signals)} payment-fraud signal(s)." if fraud_signals else ". Verified."),
            "checks": checks, "data": {"vendor": {k: best[k] for k in ("vendor_id", "legal_name", "status", "payment_terms", "phone_on_file", "email_domain")},
                                       "candidates": cands, "fraud_signals": fraud_signals}}


def st_duplicates(ctx):
    inv, P, checks = ctx["inv"], ctx["policy"], []
    vendor = ctx.get("vendor")
    prior_same_file = db.runs_with_sha(ctx["sha"], ctx["run_id"])
    if prior_same_file:
        p = prior_same_file[0]
        checks.append(chk("dup.hash", "Exact file seen before", "warn",
                          f"Identical file processed in run {p['id']} ({p['created_at'][:16].replace('T', ' ')}, decision {p['decision']})",
                          evidence={"runs": prior_same_file}))
    else:
        checks.append(chk("dup.hash", "Exact file seen before", "pass", "New file fingerprint"))
    if not vendor or not inv.get("invoice_number"):
        checks.append(chk("dup.number", "Invoice number not already booked", "skip",
                          "Needs a matched vendor and an invoice number"))
        return {"summary": "Duplicate check limited (missing vendor or number).", "checks": checks, "data": {}}

    n = db.norm_invoice_no(inv["invoice_number"])
    ledger = [r for r in db.ledger_rows() if r["vendor_id"] == vendor["vendor_id"]]
    hit = next((r for r in ledger if r["invoice_number_norm"] == n), None)
    if hit:
        same_amt = hit["total_amount"] is not None and inv.get("total_amount") is not None and abs(hit["total_amount"] - inv["total_amount"]) <= small(P)
        paid = f", paid {hit['payment_ref']}" if hit.get("payment_ref") else ""
        detail = (f"'{inv['invoice_number']}' normalises to {n}, the same as ledger entry '{hit['invoice_number']}' "
                  f"(booked {hit['posted_at']}{paid}, {money(hit['total_amount'])})")
        if same_amt:
            checks.append(chk("dup.number", "Invoice number not already booked", "fail",
                              detail + ". Same amount: this is a duplicate.", "reject", evidence={"ledger_entry": hit}))
            ctx["duplicate_of"] = hit
        else:
            checks.append(chk("dup.number", "Invoice number not already booked", "fail",
                              detail + f". Amount differs ({money(inv.get('total_amount'))}): possibly a re-issued or corrected invoice.",
                              "hold", "ap", evidence={"ledger_entry": hit}))
    else:
        checks.append(chk("dup.number", "Invoice number not already booked", "pass",
                          f"No ledger entry for {vendor['vendor_id']} with number {n} (normalised)"))

    queued = [r for r in db.open_runs_for_vendor(vendor["vendor_id"], ctx["run_id"]) if db.norm_invoice_no(r["invoice_number"]) == n]
    if queued:
        checks.append(chk("dup.queue", "Not already in review queue", "fail",
                          f"Same invoice is already awaiting review in run {queued[0]['id']}", "reject", evidence={"runs": queued}))
        ctx["duplicate_of"] = ctx.get("duplicate_of") or {"invoice_number": inv["invoice_number"], "posted_at": "pending review", "payment_ref": None}

    # fuzzy: same vendor, same amount, close dates, different number. Catches renumbered resubmissions.
    d = iso(inv.get("invoice_date"))
    fuzzy = [r for r in ledger if r["invoice_number_norm"] != n and inv.get("total_amount") is not None
             and abs((r["total_amount"] or 0) - inv["total_amount"]) <= small(P) and d and iso(r["invoice_date"])
             and abs((iso(r["invoice_date"]) - d).days) <= P["fuzzy_duplicate_window_days"]]
    if fuzzy:
        checks.append(chk("dup.fuzzy", "No near-duplicate (same amount, nearby date)", "fail",
                          f"{len(fuzzy)} ledger invoice(s) with identical amount within {int(P['fuzzy_duplicate_window_days'])} days: "
                          + ", ".join(f["invoice_number"] for f in fuzzy), "hold", "ap", evidence={"matches": fuzzy}))
    else:
        checks.append(chk("dup.fuzzy", "No near-duplicate (same amount, nearby date)", "pass",
                          f"No same-amount invoice from this vendor within {int(P['fuzzy_duplicate_window_days'])} days"))
    fails = [c for c in checks if c["status"] == "fail"]
    return {"summary": "Duplicate detected." if any(c["outcome"] == "reject" for c in fails)
            else ("Possible duplicate. Needs review." if fails else "No duplicates found."),
            "checks": checks, "data": {"normalised_number": n}}


def _po_state(po: dict) -> dict:
    billed = db.ledger_for_po(po["po_number"])
    po_total = r2(sum(l["qty"] * l["unit_price"] for l in po["lines"]))
    prior = r2(sum(b["net_amount"] or 0 for b in billed))
    return {"po_total": po_total, "prior_billed": prior, "remaining": r2(po_total - prior), "prior_invoices": billed}


def _match_lines(inv_lines: list, po: dict) -> list:
    out = []
    for li in inv_lines:
        best, best_s, best_text = None, 0.0, 0.0
        for pl in po["lines"]:
            ts = text_sim(li["description"], pl["description"] + " " + pl["sku"])
            # Structured signals (exact unit price, quantity within PO) outweigh wording: vendors
            # describe the same item differently ("LTL Freight CHI -> CMH" vs "LTL freight, Chicago IL to Columbus OH").
            price_eq = li["unit_price"] is not None and abs(li["unit_price"] - pl["unit_price"]) < 0.005
            qty_ok = li["quantity"] is not None and 0 < li["quantity"] <= pl["qty"]
            s = 0.5 * ts + (0.35 if price_eq else 0.0) + (0.15 if qty_ok else 0.0)
            if s > best_s:
                best, best_s, best_text = pl, s, ts
        matched = best is not None and (best_s >= 0.5 or best_text >= 0.6)
        out.append({"invoice_line": li, "po_line": best if matched else None, "score": round(best_s, 2)})
    return out


def _receipts(po_number: str) -> dict:
    rec = {}
    for g in db.master()["goods_receipts"]:
        if g["po_number"] == po_number:
            rec[g["line_no"]] = rec.get(g["line_no"], 0) + g["qty_received"]
    return rec


def st_po_match(ctx):
    inv, P, checks = ctx["inv"], ctx["policy"], []
    vendor = ctx.get("vendor")
    if ctx.get("halt_po") or not vendor:
        return {"summary": "Skipped: no verified vendor to match against.", "checks": [
            chk("po.identify", "PO identified", "skip", "Requires a verified vendor")], "data": {}, "skipped": True}
    pos = ctx["master"]["purchase_orders"]
    net = ctx.get("net_amount") or 0.0
    ref = inv.get("po_reference")
    po, how, inference = None, None, None

    if ref:
        nref = re.sub(r"[^0-9]", "", ref)
        po = next((p for p in pos if p["po_number"] == ref), None) or \
             next((p for p in pos if re.sub(r"[^0-9]", "", p["po_number"]) == nref and nref), None)
        if po and not _po_is_vendors(po, vendor):
            checks.append(chk("po.identify", "PO identified", "fail",
                              f"{po['po_number']} belongs to a different vendor ({po['vendor_id']})", "hold", "procurement"))
            return {"summary": "PO quoted belongs to another vendor.", "checks": checks, "data": {}}
        if po:
            how = "quoted"
            checks.append(chk("po.identify", "PO identified", "pass", f"Invoice quotes {ref} → {po['po_number']}: {po['description']}"))
        else:
            checks.append(chk("po.identify", "PO identified", "warn", f"Quoted PO '{ref}' not found. Trying to infer from lines and amounts."))

    if not po:
        cands = []
        for p in pos:
            if not _po_is_vendors(p, vendor) or p["status"] != "open":
                continue
            stt = _po_state(p)
            lm = _match_lines(inv["line_items"], p)
            line_score = sum(m["score"] for m in lm if m["po_line"]) / max(1, len(lm))
            tol_abs = min(P["price_tolerance_pct"] / 100 * stt["remaining"], P["price_tolerance_abs"]) if stt["remaining"] > 0 else 0
            amount_fit = 0.0 if net > stt["remaining"] + tol_abs else max(0.0, 1 - abs(stt["remaining"] - net) / max(stt["remaining"], 1))
            rec = _receipts(p["po_number"])
            rec_fit = 1.0 if not p["receipt_required"] else (
                1.0 if all(m["po_line"] and rec.get(m["po_line"]["line_no"], 0) >= (m["invoice_line"]["quantity"] or 0) for m in lm) else 0.3)
            conf = round(0.55 * line_score + 0.25 * amount_fit + 0.20 * rec_fit, 3)
            cands.append({"po_number": p["po_number"], "description": p["description"], "confidence": conf,
                          "line_score": round(line_score, 2), "amount_fit": round(amount_fit, 2), "receipt_fit": rec_fit,
                          "remaining": stt["remaining"]})
        cands.sort(key=lambda c: -c["confidence"])
        inference = {"candidates": cands}
        if not cands:
            checks.append(chk("po.identify", "PO identified", "fail",
                              "No PO quoted and the vendor has no open POs. Treat as a non-PO invoice that needs budget-owner approval.",
                              "hold", "procurement"))
            return {"summary": "No PO and no open POs for this vendor.", "checks": checks, "data": {"inference": inference}}
        top = cands[0]
        margin = top["confidence"] - (cands[1]["confidence"] if len(cands) > 1 else 0)
        po = next(p for p in pos if p["po_number"] == top["po_number"])
        how = "inferred"
        ok = top["confidence"] >= P["inferred_po_min_confidence"] and margin >= 0.15
        auto = ok and P["inferred_po_auto_approve"]
        detail = (f"No PO on invoice. Scored {len(cands)} open PO(s) on line similarity, remaining balance and receipts. "
                  f"Best: {top['po_number']} at {top['confidence']:.2f} confidence (lines {top['line_score']:.2f}, amount fit {top['amount_fit']:.2f}, "
                  f"receipts {top['receipt_fit']:.2f}; margin {margin:.2f} over next).")
        if auto:
            checks.append(chk("po.identify", "PO identified", "warn", detail + " Accepted automatically under policy.", evidence=inference))
        else:
            checks.append(chk("po.identify", "PO identified", "fail",
                              detail + (" Below the auto-accept threshold." if not ok else " Policy requires confirmation of inferred POs."),
                              "hold", "procurement", evidence=inference))
    ctx["po"] = po
    ctx["po_how"] = how

    if po["status"] != "open":
        checks.append(chk("po.status", "PO is open", "fail", f"{po['po_number']} is {po['status']}", "hold", "procurement"))
    else:
        checks.append(chk("po.status", "PO is open", "pass", f"{po['po_number']} open · buyer {po['buyer']}"))
    if inv["currency"] != po["currency"]:
        checks.append(chk("po.currency", "Currency matches PO", "fail", f"Invoice {inv['currency']} vs PO {po['currency']}", "hold", "ap"))

    # line-level 2-way match
    lm = _match_lines(inv["line_items"], po)
    expected, variance_lines = 0.0, []
    rows = []
    for m in lm:
        li, pl = m["invoice_line"], m["po_line"]
        amt = li["amount"] or 0.0
        if pl:
            exp = round((li["quantity"] or 0) * pl["unit_price"], 2)
            expected += exp
            pv = round(amt - exp, 2)
            rows.append({"invoice": li["description"], "po_line": f"#{pl['line_no']} {pl['description']}", "qty": li["quantity"],
                         "po_qty": pl["qty"], "inv_price": li["unit_price"], "po_price": pl["unit_price"], "variance": pv,
                         "gl": pl["gl_account"], "score": m["score"]})
            if abs(pv) > 0.005:
                variance_lines.append(f"{li['description'][:40]}: {money(pv)} price variance")
            if (li["quantity"] or 0) > pl["qty"]:
                variance_lines.append(f"{li['description'][:40]}: billed qty {li['quantity']} > PO qty {pl['qty']}")
        else:
            rows.append({"invoice": li["description"], "po_line": None, "qty": li["quantity"], "inv_price": li["unit_price"],
                         "variance": amt, "gl": None, "score": m["score"]})
            variance_lines.append(f"'{li['description'][:40]}' not on PO (+{money(amt)})")
    ship = inv.get("shipping_amount") or 0.0
    if ship:
        variance_lines.append(f"Shipping charge +{money(ship)} not on PO")
    expected = round(expected, 2)
    variance = round(net - expected, 2)
    allowed = round(min(P["price_tolerance_pct"] / 100 * expected, P["price_tolerance_abs"]), 2)
    pct = (variance / expected * 100) if expected else 0
    disc = abs(inv.get("discount_amount") or 0.0)
    if disc and abs(variance + disc) <= small(P):  # lines match the PO (give or take rounding); the vendor then gave a discount
        off = round(variance + disc, 2)
        checks.append(chk("po.price", "Price & quantity vs PO", "pass",
                          f"Lines match the PO value {money(expected)}; the vendor's {money(disc)} discount brings the net to {money(net)}"
                          + (f". Lines are {money(abs(off))} {'over' if off > 0 else 'under'} the PO: under {money(small(P))}, treated as rounding" if abs(off) > 0.5 else ""),
                          evidence={"lines": rows}))
    elif variance <= 0.005:
        checks.append(chk("po.price", "Price & quantity vs PO", "pass" if variance > -0.005 else "info",
                          f"Invoice net {money(net)} vs PO value of billed lines {money(expected)}"
                          + ("" if variance > -0.005 else f" (under-billed by {money(-variance)})"), evidence={"lines": rows}))
    elif variance <= small(P):
        checks.append(chk("po.price", "Price & quantity vs PO", "pass",
                          f"Invoice net {money(net)} is {money(variance)} over the PO value {money(expected)}: under {money(small(P))}, "
                          "treated as rounding, not a price difference", evidence={"lines": rows}))
    elif variance <= allowed:
        checks.append(chk("po.price", "Price & quantity vs PO", "warn",
                          f"Variance +{money(variance)} ({pct:.2f}%) is within tolerance ({P['price_tolerance_pct']:.1f}% / {money(P['price_tolerance_abs'])}, max {money(allowed)}): "
                          + "; ".join(variance_lines), evidence={"lines": rows}))
    else:
        checks.append(chk("po.price", "Price & quantity vs PO", "fail",
                          f"Variance +{money(variance)} ({pct:.2f}%) exceeds tolerance (max {money(allowed)}): " + "; ".join(variance_lines),
                          "hold", "procurement", evidence={"lines": rows}))

    # 3-way match: billed quantity must have been received
    if po["receipt_required"]:
        rec = _receipts(po["po_number"])
        short = [f"{m['invoice_line']['description'][:35]}: billed {m['invoice_line']['quantity']}, received {rec.get(m['po_line']['line_no'], 0)}"
                 for m in lm if m["po_line"] and (m["invoice_line"]["quantity"] or 0) > rec.get(m["po_line"]["line_no"], 0)]
        if short:
            checks.append(chk("po.receipt", "Goods received (3-way match)", "fail", "Not yet received: " + "; ".join(short), "hold", "procurement"))
        else:
            grns = sorted({g["grn"] for g in db.master()["goods_receipts"] if g["po_number"] == po["po_number"]})
            checks.append(chk("po.receipt", "Goods received (3-way match)", "pass", f"All billed quantities received ({', '.join(grns)})"))
    else:
        checks.append(chk("po.receipt", "Goods received (3-way match)", "info", "Service PO. No goods receipt required (2-way match)."))

    # cumulative billing: catches split invoices that are fine one at a time
    stt = _po_state(po)
    after = round(stt["prior_billed"] + net, 2)
    overrun = round(after - stt["po_total"], 2)
    cum_allowed = max(round(min(P["price_tolerance_pct"] / 100 * stt["po_total"], P["price_tolerance_abs"]), 2), small(P))
    prior_list = ", ".join(f"{b['invoice_number']} {money(b['net_amount'])}" for b in stt["prior_invoices"]) or "none"
    if overrun > cum_allowed:
        ctx["partial_approval"] = max(0.0, stt["remaining"])
        checks.append(chk("po.cumulative", "Cumulative billing within PO", "fail",
                          f"PO value {money(stt['po_total'])}; already billed {money(stt['prior_billed'])} ({prior_list}); this invoice {money(net)} "
                          f"→ {money(after)}, over by {money(overrun)} ({overrun / stt['po_total'] * 100:.1f}%). Remaining balance is only {money(stt['remaining'])}.",
                          "hold", "procurement", evidence=stt))
    else:
        checks.append(chk("po.cumulative", "Cumulative billing within PO", "pass",
                          f"{money(after)} of {money(stt['po_total'])} billed after this invoice (prior: {prior_list})", evidence=stt))

    total = inv.get("total_amount") or 0
    if total >= P["high_value_threshold"]:
        checks.append(chk("po.authority", "Within auto-approval authority", "fail",
                          f"{money(total)} ≥ {money(P['high_value_threshold'])} requires controller sign-off", "hold", "controller"))
    else:
        checks.append(chk("po.authority", "Within auto-approval authority", "pass", f"{money(total)} < {money(P['high_value_threshold'])} limit"))

    fails = [c for c in checks if c["status"] == "fail"]
    return {"summary": f"{po['po_number']} ({how})" + (f". {len(fails)} matching issue(s)." if fails else ". Matched within tolerance."),
            "checks": checks, "data": {"po_number": po["po_number"], "how": how, "inference": inference, "lines": rows,
                                       "net_amount": net, "expected": expected, "variance": variance, "allowed": allowed,
                                       "po_state": {k: v for k, v in stt.items() if k != "prior_invoices"}}}


DOC_TYPE_NAMES = {"quote": "quote", "statement": "statement of account", "purchase_order": "purchase order",
                  "receipt": "receipt", "other": "document that isn't an invoice"}


def st_decision(ctx):
    trace = ctx["trace"]
    all_checks = [c for k, s in trace["stages"].items() for c in s.get("checks", [])]
    rejects = [c for c in all_checks if c["outcome"] == "reject"]
    holds = [c for c in all_checks if c["outcome"] == "hold"]
    warns = [c for c in all_checks if c["status"] == "warn"]
    inv = ctx.get("inv") or {}
    vendor = ctx.get("vendor") or {}
    po = ctx.get("po") or {}
    P = ctx["policy"]
    out = {"reasons": [], "next_actions": [], "vendor_email": None, "internal_task": None, "payment": None}

    if ctx.get("not_invoice"):
        label = ctx["not_invoice"]
        out["decision"] = "NOT_INVOICE"
        out["route_name"] = "Filed: not an invoice"
        out["headline"] = f"Filed, not paid: this is a {label}, not an invoice."
        out["reasons"] = [f"The document reads as a {label}. There is no amount due to pay."]
        out["next_actions"] = ["Kept on file for reference. It doesn't count towards invoice approval or exception rates.",
                               "If it is actually an invoice, open it and use Correct details & re-check."]
    elif rejects:
        out["decision"] = "REJECT"
        primary = rejects[0]
        out["reasons"] = [c["detail"] for c in rejects] + [f"Also noted: {c['detail']}" for c in holds]
        if primary["id"] == "val.required":
            out["route_name"] = "Returned to vendor"
            out["headline"] = "Returned to vendor: invoice is missing " + " and ".join(f.replace("_", " ") for f in ctx.get("return_to_vendor", [])) + "."
            out["vendor_email"] = _email_missing(inv, vendor, ctx.get("return_to_vendor", []), po)
            out["next_actions"] = ["Send the drafted email to the vendor", "Nothing is posted. The vendor must resubmit a corrected invoice."]
        elif primary["id"].startswith("dup."):
            d = ctx.get("duplicate_of") or {}
            out["route_name"] = "Rejected: duplicate"
            out["headline"] = (f"Rejected as duplicate of {d.get('invoice_number')}" +
                               (f", already paid ({d['payment_ref']})." if d.get("payment_ref") else f" ({d.get('posted_at')})."))
            out["vendor_email"] = _email_duplicate(inv, vendor, d)
            out["next_actions"] = ["Do not pay", "Optional: send the drafted note confirming the original payment"]
            out["money_protected"] = inv.get("total_amount")
        elif primary["id"] == "ven.status":
            out["route_name"] = "Rejected: blocked vendor"
            out["headline"] = f"Rejected: {vendor.get('legal_name')} is blocked for payment."
            out["next_actions"] = ["Escalate to Vendor Management / Compliance"]
        else:
            out["route_name"] = "Rejected"
            out["headline"] = f"Rejected: {primary['detail']}"
    elif holds:
        out["decision"] = "HOLD"
        qkeys = sorted({c["route"] or "ap" for c in holds}, key=lambda q: QUEUES[q]["priority"])
        if "vendor_mgmt" in qkeys and qkeys[0] != "fraud" and (ctx.get("new_vendor") or vendor.get("status") == "unverified"):
            qkeys = ["vendor_mgmt"] + [k for k in qkeys if k != "vendor_mgmt"]  # verifying the vendor comes first
        q = QUEUES[qkeys[0]]
        out["route"] = qkeys[0]
        out["route_name"] = q["name"]
        out["sla"] = q["sla"]
        out["also_notify"] = [QUEUES[k]["name"] for k in qkeys[1:]]
        out["reasons"] = [c["detail"] for c in holds]
        if qkeys[0] == "fraud":
            sig = ctx.get("fraud_signals") or []
            out["headline"] = f"On hold for Treasury & Fraud: {len(sig)} payment-fraud signal(s) on an otherwise clean invoice."
            out["internal_task"] = (f"Call {vendor.get('legal_name')} on the phone number on file ({vendor.get('phone_on_file') or 'from your past correspondence'}), "
                                    "NOT any number or email on this invoice, to confirm whether they changed banks. "
                                    "Only Vendor Management may update bank details, after documented verification. "
                                    "Until then, do not pay, and do not reply to the invoice's email address.")
            out["next_actions"] = ["Verbal callback to the number on file", "If not confirmed: report as attempted fraud, notify the vendor's real AP contact",
                                   "If confirmed: Vendor Management updates the bank on the vendor record, then release"]
            out["money_protected"] = inv.get("total_amount")
        elif any(c["id"] == "po.cumulative" for c in holds):
            pa = ctx.get("partial_approval", 0)
            out["partial_approval_amount"] = pa
            out["headline"] = f"On hold for {po.get('buyer', 'the buyer')}: this invoice would take {po.get('po_number')} over its approved value."
            out["next_actions"] = [f"Buyer raises a PO change order to cover the overrun, or",
                                   f"Approve a partial payment of {money(pa)} (the remaining PO balance) and dispute the rest with the vendor"]
        elif qkeys[0] == "vendor_mgmt":
            if ctx.get("new_vendor"):
                out["headline"] = f"On hold: {vendor.get('legal_name')} is a new vendor. Verify them once, then this invoice is re-checked automatically."
            elif vendor.get("status") == "unverified":
                out["headline"] = f"On hold: {vendor.get('legal_name')} hasn't been verified yet."
            else:
                out["headline"] = f"On hold for Vendor Management: " + holds[0]["detail"].rstrip(".") + "."
            out["vendor_to_verify"] = vendor.get("vendor_id") if vendor.get("status") == "unverified" else None
            out["next_actions"] = ["Check the GSTIN on the GST portal and confirm the bank account (cancelled cheque or a call to a known contact)",
                                   "Click Verify vendor: this invoice and any others from them are re-checked against every rule"]
        else:
            out["headline"] = f"On hold for {q['name']}: " + holds[0]["detail"].rstrip(".") + "."
            out["next_actions"] = ["Reviewer checks the flagged items against the PDF, then approves or rejects with a note"]
    else:
        out["decision"] = "APPROVE"
        out["route_name"] = "Auto-approved → payment run"
        how = ctx.get("po_how")
        out["headline"] = (f"Approved: {money(inv.get('total_amount'))} to {vendor.get('legal_name')}, matched to {po.get('po_number')}"
                           + (" (PO inferred, not quoted)" if how == "inferred" else "") + ".")
        out["reasons"] = ["All checks passed"] + [f"Note: {c['detail']}" for c in warns if c["id"] not in ("dup.hash",)]
        out["payment"] = _payment_plan(inv, vendor, po, ctx)
        out["next_actions"] = ["Posted to the invoice ledger", f"Scheduled for payment on {out['payment']['pay_on']}"]
        if how == "inferred":
            out["next_actions"].append("Ask the vendor to quote the PO number on future invoices")

    counts = {s: sum(1 for c in all_checks if c["status"] == s) for s in ("pass", "warn", "fail", "info", "skip")}
    out["check_counts"] = counts
    trace["decision"] = out
    return {"summary": out["headline"], "checks": [], "data": out}


def _payment_plan(inv, vendor, po, ctx) -> dict:
    terms = parse_terms(inv.get("payment_terms") or vendor.get("payment_terms"))
    d = iso(inv.get("invoice_date")) or TODAY()
    due = d + timedelta(days=terms["net_days"])
    plan = {"terms": inv.get("payment_terms") or vendor.get("payment_terms"), "due_date": due.isoformat(), "pay_on": due.isoformat(),
            "pay_to": f"{vendor['bank']['bank_name']} ••••{vendor['bank']['account_number'][-4:]} (verified account on file)",
            "amount": inv.get("total_amount")}
    if terms["discount_pct"]:
        disc_by = d + timedelta(days=terms["discount_days"])
        saving = round((ctx.get("net_amount") or 0) * terms["discount_pct"] / 100, 2)
        if disc_by >= TODAY():
            plan.update({"discount_by": disc_by.isoformat(), "discount_amount": saving, "pay_on": disc_by.isoformat(),
                         "amount": round((inv.get("total_amount") or 0) - saving, 2),
                         "discount_note": f"Pay by {disc_by.isoformat()} to capture {terms['discount_pct']:g}% early-payment discount ({money(saving)})"})
    gl = {}
    for row in ctx["trace"]["stages"].get("po_match", {}).get("data", {}).get("lines", []):
        acct = row.get("gl") or (po["lines"][0]["gl_account"] if po else "2000 Suspense")
        gl[acct] = round(gl.get(acct, 0) + (row["qty"] or 0) * (row.get("inv_price") or 0), 2)
    if inv.get("tax_amount"):
        gl["GST input tax credit (CGST/SGST/IGST receivable)"] = inv["tax_amount"]
    plan["gl_coding"] = [{"account": k, "amount": v} for k, v in gl.items()]
    plan["cost_center"] = po.get("cost_center")
    return plan


def _email_missing(inv, vendor, missing, po) -> dict:
    to = (inv.get("vendor_email") or f"ap@{vendor.get('email_domain', 'vendor.com')}")
    items = "\n".join(f"  • {m.replace('_', ' ').capitalize()}" for m in missing)
    return {"to": to, "subject": f"Invoice could not be processed: {money(inv.get('total_amount'))}"
                                 + (f" (PO {po.get('po_number') or inv.get('po_reference')})" if (po or inv.get('po_reference')) else ""),
            "body": f"Hello {vendor.get('legal_name', 'team')},\n\nThank you for your invoice for {money(inv.get('total_amount'))}"
                    + (f" against PO {inv.get('po_reference')}" if inv.get("po_reference") else "") +
                    ". We're unable to process it because the following required information is missing:\n\n"
                    f"{items}\n\nPlease reissue the invoice with these details and send it to ap@lumenhomegoods.com. "
                    "Payment terms will start from the date we receive the corrected invoice.\n\nKind regards,\nAccounts Payable, Lumen Home Goods, Inc."}


def _email_duplicate(inv, vendor, d) -> dict:
    return {"to": inv.get("vendor_email") or f"ap@{vendor.get('email_domain', 'vendor.com')}",
            "subject": f"Re: invoice {inv.get('invoice_number')}, already processed",
            "body": f"Hello {vendor.get('legal_name', 'team')},\n\nWe received invoice {inv.get('invoice_number')} for {money(inv.get('total_amount'))}. "
                    f"Our records show this invoice was already processed as {d.get('invoice_number')} on {d.get('posted_at')}"
                    + (f" and paid under reference {d['payment_ref']}" if d.get("payment_ref") else "") +
                    ". We won't be issuing a second payment. If you believe this is in error, please send a statement of account "
                    "and we'll reconcile it with you.\n\nKind regards,\nAccounts Payable, Lumen Home Goods, Inc."}


STAGE_FNS = {"intake": st_intake, "analyze": st_analyze, "extract": st_extract, "validate": st_validate,
             "vendor": st_vendor, "duplicates": st_duplicates, "po_match": st_po_match, "decision": st_decision}
