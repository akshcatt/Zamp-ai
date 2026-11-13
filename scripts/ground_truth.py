"""Ground truth for the generated samples: the exact values each invoice was rendered from.

Used by the tests (a "perfect reader" that isolates the rules engine) and by scripts/benchmark.py
(to score what the AI actually read). Never used by the production pipeline.
"""
from __future__ import annotations

import re

def truth(spec: dict) -> dict:
    meta = dict(spec["meta"])
    parse = lambda s: None if not s else next((d for d in [_d(s)] if d), None)
    num = lambda label: next((float(v.replace("$", "").replace(",", "")) for l, v, _ in spec["totals"] if l.startswith(label)), None)
    notes = " ".join(n.lstrip("!") for n in spec.get("notes", []))
    routing = re.search(r"(?:Routing|ABA)\s+(\d{9})", notes)
    acct = re.search(r"(?:Account|Acct)\s+(\d{6,})", notes)
    total = next(float(v.replace("$", "").replace(",", "")) for l, v, b in spec["totals"] if b)
    inv_no = meta.get("Invoice #") or meta.get("Bill Number") or meta.get("Invoice No.")
    po = meta.get("PO Number") or meta.get("Your Ref") or meta.get("Cust. PO")
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", spec["vendor_address"] + " " + spec.get("vendor_contact", ""))
    change = next((n.lstrip("!") for n in spec.get("notes", []) if "changed banks" in n), None)
    return {
        "document_type": spec.get("doc_type", "invoice"), "vendor_name": spec["vendor_name"], "vendor_address": None, "vendor_tax_id": None,
        "vendor_email": email.group(0) if email else None, "vendor_phone": None, "bill_to_name": "Lumen Home Goods, Inc.",
        "invoice_number": inv_no, "invoice_date": parse(meta.get("Invoice Date") or meta.get("Date")), "due_date": None,
        "po_reference": po, "payment_terms": meta.get("Terms"), "currency": "USD",
        "line_items": [{"description": l["description"], "quantity": l["qty"], "unit_price": l["unit_price"],
                        "amount": round(l["qty"] * l["unit_price"], 2)} for l in spec["lines"]],
        "subtotal": num("Subtotal"), "tax_amount": num("Sales Tax"), "tax_rate_pct": None, "shipping_amount": num("Shipping"),
        "total_amount": total, "tax_inclusive": False,
        "bank_details": {"bank_name": None, "routing_number": routing.group(1) if routing else None,
                         "account_number": acct.group(1) if acct else None, "iban": None, "swift": None},
        "payment_instruction_notes": change,
        "field_confidence": {k: 0.97 for k in ["vendor_name", "invoice_number", "invoice_date", "po_reference", "total_amount", "line_items", "bank_details"]},
        "extraction_warnings": [],
    }


def _d(s):
    from datetime import datetime
    for f in ["%b %d, %Y", "%d %B %Y", "%m/%d/%Y", "%d-%b-%Y"]:
        try:
            return datetime.strptime(s.title(), f).date().isoformat()
        except ValueError:
            pass
    return None
