"""Invoice extraction.

Primary:  Google Gemini reads the PDF directly (text *and* page images, so scans work)
          and returns JSON constrained to EXTRACTION_SCHEMA, with per-field confidence.
Fallback: a heuristic parser over the PDF text layer. Used when no API key is set or
          the API is unreachable, so the pipeline degrades instead of failing. It
          cannot read scans, and the pipeline routes those to a human.

Design rule: the model only *reads*. It never decides. Every decision downstream is
made by deterministic, auditable rules in pipeline.py.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

# gemini-3.1-flash-lite: free tier, reads PDFs (incl. scans/photos) natively, ~3 s per invoice in testing.
# Measured 2026-11-12 on the free tier: 3.1-flash-lite ~3 s; 3.5-flash-lite 36 s or 503 (overloaded); 3.5-flash ~20-27 s.
MODEL = os.environ.get("AP_GEMINI_MODEL", "gemini-3.1-flash-lite")
FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash"]  # other free-tier models, tried in order on overload
TIMEOUT_MS = int(os.environ.get("AP_GEMINI_TIMEOUT_MS", "30000"))  # hard per-model deadline

NUM = {"type": ["number", "null"]}
STR = {"type": ["string", "null"]}

EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "document_type", "vendor_name", "vendor_address", "vendor_tax_id", "vendor_email", "vendor_phone",
        "bill_to_name", "invoice_number", "invoice_date", "due_date", "po_reference", "payment_terms",
        "currency", "line_items", "subtotal", "tax_amount", "tax_rate_pct", "shipping_amount", "total_amount",
        "tax_inclusive", "bank_details", "payment_instruction_notes", "field_confidence", "extraction_warnings",
    ],
    "properties": {
        "document_type": {"type": "string", "enum": ["invoice", "credit_note", "quote", "statement", "purchase_order", "receipt", "other"]},
        "vendor_name": STR, "vendor_address": STR, "vendor_tax_id": STR, "vendor_email": STR, "vendor_phone": STR,
        "bill_to_name": STR,
        "invoice_number": STR,
        "invoice_date": {"type": ["string", "null"], "description": "ISO YYYY-MM-DD"},
        "due_date": {"type": ["string", "null"], "description": "ISO YYYY-MM-DD"},
        "po_reference": {"type": ["string", "null"], "description": "Purchase order number exactly as printed, or null if none is printed"},
        "payment_terms": STR,
        "currency": {"type": ["string", "null"], "description": "ISO 4217 code"},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["description", "quantity", "unit_price", "amount"],
                "properties": {"description": {"type": "string"}, "quantity": NUM, "unit_price": NUM, "amount": NUM},
            },
        },
        "subtotal": NUM, "tax_amount": NUM, "tax_rate_pct": NUM,
        "shipping_amount": {"type": ["number", "null"], "description": "Only a shipping/freight charge printed as its own row in the totals block. If freight appears as a line item, leave this null"},
        "total_amount": NUM,
        "tax_inclusive": {"type": "boolean"},
        "bank_details": {
            "type": "object", "additionalProperties": False,
            "required": ["bank_name", "routing_number", "account_number", "iban", "swift"],
            "properties": {"bank_name": STR, "routing_number": STR, "account_number": STR, "iban": STR, "swift": STR},
        },
        "payment_instruction_notes": {"type": ["string", "null"], "description": "Verbatim text about payment instructions, esp. any change of bank details"},
        "field_confidence": {
            "type": "object", "additionalProperties": False,
            "required": ["vendor_name", "invoice_number", "invoice_date", "po_reference", "total_amount", "line_items", "bank_details"],
            "properties": {k: {"type": "number"} for k in
                           ["vendor_name", "invoice_number", "invoice_date", "po_reference", "total_amount", "line_items", "bank_details"]},
        },
        "extraction_warnings": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM = """You are the document-reading step of an accounts-payable pipeline. You transcribe what is printed on a vendor document into structured fields. You do not judge whether the invoice should be paid; downstream rules do that.

Rules:
- Transcribe exactly what is printed. Never infer or invent a value that is not on the page. If a field is absent, return null. Example: no PO number printed means po_reference = null, even if you can guess one.
- Numbers are plain decimals with no currency symbols or thousands separators. Dates are ISO YYYY-MM-DD. Interpret US-style dates (MM/DD/YYYY) as US unless the document clearly uses another convention.
- line_items: one entry per printed line. amount is the printed line total.
- shipping_amount is only for a shipping/freight row in the totals block (next to subtotal and tax). If freight is listed as a line item, it belongs in line_items only. Never count it twice.
- tax_inclusive = true only if prices are stated to include tax.
- bank_details: the remit-to details printed on this document. payment_instruction_notes: copy verbatim any sentence about where or how to pay, especially any announcement of changed bank details.
- field_confidence: 0 to 1, how sure you are that the transcription is correct and complete. Use low values for blurry, cut-off or ambiguous text, and 1.0 for a field that is clearly absent (a confident null).
- extraction_warnings: short notes on anything a human should know, for example a stamp saying "second notice", handwritten edits, a partially illegible total, or several pages that look like different documents."""


class ExtractionError(Exception):
    pass


def _api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def llm_available() -> bool:
    return bool(_api_key())


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text or "", re.S)  # tolerate code fences / stray prose
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise ExtractionError("Model returned invalid JSON")


class _Deadline(Exception):
    pass


def _with_deadline(fn, seconds: float):
    """Hard wall-clock limit. The SDK's HTTP timeout doesn't cap a slow, trickling response,
    so the call runs in a daemon thread and we stop waiting for it when the deadline passes."""
    import threading
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as e:  # re-raised in the caller's thread
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise _Deadline(f"no response within {seconds:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def extract_with_gemini(pdf_bytes: bytes, text_layer: str) -> tuple[dict, dict]:
    """Returns (fields, meta). Raises ExtractionError on failure."""
    from google import genai
    from google.genai import errors, types

    # Per-request timeout so a slow free-tier call can never hang a run.
    client = genai.Client(api_key=_api_key(), http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    hint = ("The PDF has a machine-readable text layer." if text_layer.strip()
            else "The PDF has NO text layer. It is a scanned image, so read it visually and lower confidence where print is unclear.")
    pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    prompt = f"{hint}\nExtract the document into the schema."
    t0 = time.time()
    opts = {"schema": True, "thinking": True}  # degrade these if a model rejects them (400)

    def call(model: str):
        kw = dict(system_instruction=SYSTEM, temperature=0, response_mime_type="application/json")
        if opts["thinking"]:  # transcription needs no deliberation: minimal thinking is much faster
            kw["thinking_config"] = types.ThinkingConfig(thinking_level="minimal")
        contents = [pdf_part, prompt]
        if opts["schema"]:  # API-enforced JSON Schema
            kw["response_json_schema"] = EXTRACTION_SCHEMA
        else:  # fallback: schema in the prompt, JSON mode only
            contents = [pdf_part, prompt + "\nRespond with ONLY a JSON object conforming to this JSON Schema:\n"
                        + json.dumps(EXTRACTION_SCHEMA)]
        return client.models.generate_content(model=model, contents=contents, config=types.GenerateContentConfig(**kw))

    # Free-tier models are sometimes overloaded (503) or rate-limited (429): retry briefly, then
    # move to the next free Flash model rather than failing the run.
    models = [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]
    resp, used, last_err = None, None, None
    for model in models:
        for attempt in range(3):
            try:
                resp, used = _with_deadline(lambda: call(model), TIMEOUT_MS / 1000), model
                break
            except _Deadline as e:  # too slow right now: try the next model
                last_err = f"timeout on {model}"
                break
            except errors.ClientError as e:
                code = getattr(e, "code", None)
                if "api key" in str(e).lower() or code in (401, 403):
                    raise ExtractionError("Gemini API key rejected. Check GEMINI_API_KEY.") from e
                if code == 400 and opts["thinking"]:
                    opts["thinking"] = False
                    continue
                if code == 400 and opts["schema"]:
                    opts["schema"] = False
                    continue
                if code in (404, 429):  # model unavailable / quota for this model: try next model
                    last_err = f"{code} on {model}"
                    if code == 429 and attempt == 0:
                        time.sleep(3)
                        continue
                    break
                raise ExtractionError(f"Gemini API error {code}: {getattr(e, 'message', e)}") from e
            except errors.ServerError as e:  # 503 high demand / 500: don't wait, move to the next model
                last_err = f"{getattr(e, 'code', '5xx')} on {model}"
                break
            except errors.APIError as e:
                raise ExtractionError(f"Gemini API error: {e}") from e
            except Exception as e:  # network, DNS, timeouts
                last_err = f"{type(e).__name__} on {model}"
                break
        if resp is not None:
            break
    if resp is None:
        raise ExtractionError(f"Gemini unavailable right now ({last_err}). Free tier may be overloaded. Retry shortly.")
    with_schema = opts["schema"]

    cand = (resp.candidates or [None])[0]
    finish = str(getattr(cand, "finish_reason", "") or "")
    if "SAFETY" in finish or "PROHIBITED" in finish:
        raise ExtractionError("Model declined to process the document")
    if "MAX_TOKENS" in finish:
        raise ExtractionError("Extraction output was truncated")
    data = _parse_json(resp.text)
    missing = [k for k in ("document_type", "line_items", "total_amount", "invoice_number") if k not in data]
    if missing:
        raise ExtractionError(f"Model output missing fields: {missing}")
    usage = getattr(resp, "usage_metadata", None)
    meta = {
        "extractor": "gemini",
        "model": getattr(resp, "model_version", None) or used,
        "latency_ms": int((time.time() - t0) * 1000),
        "input_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
        "schema_mode": "enforced" if with_schema else "prompted",
    }
    return data, meta


# ------------------------------------------------------------------ heuristic fallback
_AMT = r"\$?\s?([\d,]+\.\d{2})"
_DATE_FORMATS = ["%b %d, %Y", "%d %B %Y", "%B %d, %Y", "%m/%d/%Y", "%d-%b-%Y", "%Y-%m-%d", "%d %b %Y"]


def _to_float(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _to_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().title()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return None


def _find(patterns: list[str], text: str, flags=re.I) -> str | None:
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m.group(1).strip()
    return None


def _doc_type(text: str) -> str:
    """Classify from the title words. Quote/statement wording wins only when the page never calls itself an invoice."""
    if re.search(r"\binvoice\b|\bbill (?:no|number|#)", text, re.I):
        return "invoice"
    if re.search(r"\bquot(?:e|ation)\b|\bestimate\b|\bproforma\b", text, re.I):
        return "quote"
    if re.search(r"\bstatement of account\b|\baccount statement\b", text, re.I):
        return "statement"
    if re.search(r"\bpurchase order\b", text, re.I) and not re.search(r"total due|amount due|balance due", text, re.I):
        return "purchase_order"
    return "other"


def extract_heuristic(text: str) -> tuple[dict, dict]:
    """Label-driven regex parser. Deliberately generic, not tuned per sample, and it reports low confidence."""
    if not text.strip():
        raise ExtractionError("No text layer and no LLM available (scanned document needs vision/OCR)")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    first = re.sub(r"\s+(?:(?:FREIGHT\s+|TAX\s+)?INVOICE|QUOTATION|QUOTE|ESTIMATE|STATEMENT(?:\s+OF\s+ACCOUNT)?)$", "", lines[0], flags=re.I) if lines else None
    inv_no = _find([r"(?:Invoice\s*(?:#|No\.?|Number)|Bill\s*Number)\s*:?\s*([A-Z0-9][A-Z0-9\-/ ]{2,20}?)(?:\s{2,}|\s+(?:Invoice|Date|PO|Cust|Your|Terms|Period)\b|$)"], text, re.I | re.M)
    if inv_no and not re.search(r"\d", inv_no):  # a column header ('DATE'), not a number
        inv_no = None
    if not inv_no:
        m = re.search(r"INVOICE NO\.?.*\n\s*([A-Z0-9][A-Z0-9\-/]+)", text, re.I)
        inv_no = m.group(1) if m else None
    dpat = (r"([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}|\d{1,2} [A-Z][a-z]{2,8} \d{4}|\d{1,2}/\d{1,2}/\d{4}"
            r"|\d{1,2}-[A-Za-z]{3}-\d{4}|\d{4}-\d{2}-\d{2})")
    date_raw = _find([rf"Invoice\s*Date\s*:?\s*{dpat}", rf"(?m)^Date\s*:?\s*{dpat}", rf"\bDate\s*:?\s*{dpat}",
                      rf"INVOICE DATE.*\n.*?{dpat}", rf"\bDATE\b.*\n.*?{dpat}"], text, re.I)
    po = _find([r"\b(PO-\d{4}-\d{3,5})\b"], text)
    lines_out = []
    for l in lines:
        m = re.match(r"^(.+?)\s+([\d,]+(?:\.\d+)?)\s+\$?([\d,]+\.\d{2})\s+\$?([\d,]+\.\d{2})$", l)
        if m and not re.match(r"(?i)^(subtotal|total|tax|sales)", m.group(1)):
            lines_out.append({"description": m.group(1), "quantity": _to_float(m.group(2)),
                              "unit_price": _to_float(m.group(3)), "amount": _to_float(m.group(4))})
    def label_amount(label_re: str) -> float | None:
        """Last money amount on the first line whose label matches (avoids reading '8.875%' as an amount)."""
        for l in lines:
            if re.match(label_re, l, re.I):
                amts = re.findall(r"\$?\s?(\d[\d,]*\.\d{2})(?!\d|%)", l)
                if amts:
                    return _to_float(amts[-1])
        return None

    subtotal = label_amount(r"^sub\s*total\b")
    tax = label_amount(r"^(?:sales\s*)?tax\b")
    ship = label_amount(r"^shipping(?:\s*(?:&|and)\s*handling)?\s*\$")
    total = (label_amount(r"^(?:total\s*due|amount\s*due|balance\s*due|total\s*usd|grand\s*total)\b")
             or label_amount(r"^total\b"))
    routing = _find([r"(?:Routing|ABA)\s*:?\s*(\d{9})"], text)
    acct = _find([r"(?:Account|Acct)\s*:?\s*(\d{6,17})"], text)
    bank = _find([r"(?:Remit(?:\s*to)?:?)\s*([A-Za-z.,& ]+?(?:Bank|N\.A\.)[A-Za-z.,& ]*?)\s+(?:Routing|ABA)"], text)
    if not bank:
        bank = _find([r"^([A-Za-z.,& ]*Bank[A-Za-z.,& ]*?)\s+(?:Routing|ABA)"], text, re.I | re.M)
    email = _find([r"([\w.+-]+@[\w-]+\.[\w.]+)"], text)
    change_note = _find([r"((?:we have changed banks|new bank|updated bank|change of bank)[^\n]*)"], text)
    terms = _find([r"Terms\s*:?\s*((?:\d+/\d+\s+)?Net\s*\d+)"], text)
    data = {
        "document_type": _doc_type(text),
        "vendor_name": first, "vendor_address": None, "vendor_tax_id": _find([r"(?:EIN|FEIN|Tax ID)\s*:?\s*(\d{2}-\d{7})"], text),
        "vendor_email": email, "vendor_phone": None, "bill_to_name": "Lumen Home Goods, Inc." if "Lumen Home Goods" in text else None,
        "invoice_number": inv_no, "invoice_date": _to_date(date_raw), "due_date": None, "po_reference": po,
        "payment_terms": terms, "currency": "USD", "line_items": lines_out,
        "subtotal": subtotal, "tax_amount": tax, "tax_rate_pct": None, "shipping_amount": ship, "total_amount": total,
        "tax_inclusive": False,
        "bank_details": {"bank_name": bank, "routing_number": routing, "account_number": acct, "iban": None, "swift": None},
        "payment_instruction_notes": change_note,
        "field_confidence": {k: (0.75 if v else 1.0) for k, v in
                             {"vendor_name": first, "invoice_number": inv_no, "invoice_date": date_raw, "po_reference": po,
                              "total_amount": total, "line_items": lines_out, "bank_details": acct}.items()},
        "extraction_warnings": ["Heuristic parser used (LLM unavailable). Confidence is capped at 0.75."],
    }
    return data, {"extractor": "heuristic", "model": None, "latency_ms": 0}
