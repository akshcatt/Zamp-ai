"""Extraction & decision benchmark against the REAL model.

For every sample: run the full pipeline (fresh, isolated database, real Gemini call), then score
  * each extracted field against the exact values the document was rendered from (ground truth), and
  * the final decision and routing against the expected outcome.
Writes app/seed/benchmark.json (shown on the "How it works" page) and docs/BENCHMARK.md.

    .venv/bin/python scripts/benchmark.py            # needs GEMINI_API_KEY in .env or the shell

Ground truth never enters the pipeline; it is only read here, after the decision is made.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["DEMO_PACING_MS"] = "0"
os.environ.setdefault("AP_TODAY", "2026-11-12")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db, pipeline  # noqa: E402
from app.extract import MODEL, llm_available  # noqa: E402
import generate_samples as G  # noqa: E402
from ground_truth import truth  # noqa: E402

EXPECTED = {
    "happy_path": ("APPROVE", None), "edge_split_po_overrun": ("HOLD", "Procurement / Buyer"),
    "edge_scanned_inferred_po": ("APPROVE", None), "edge_disguised_duplicate": ("REJECT", "Rejected: duplicate"),
    "edge_bank_change_fraud": ("HOLD", "Treasury & Fraud"), "variant_within_tolerance": ("APPROVE", None),
    "variant_missing_fields": ("REJECT", "Returned to vendor"), "variant_phone_photo": ("APPROVE", None),
    "variant_not_an_invoice": ("NOT_INVOICE", "Filed: not an invoice"),
}
norm = lambda s: re.sub(r"[^A-Z0-9]", "", (s or "").upper())
money_eq = lambda a, b: (a is None and b is None) or (a is not None and b is not None and abs(float(a) - float(b)) < 0.011)
FIELDS = {
    "Document type": lambda g, t: g.get("document_type") == t["document_type"],
    "Vendor name": lambda g, t: norm(t["vendor_name"])[:12] in norm(g.get("vendor_name")),
    "Invoice number": lambda g, t: norm(g.get("invoice_number")) == norm(t["invoice_number"]),
    "Invoice date": lambda g, t: g.get("invoice_date") == t["invoice_date"],
    "PO reference": lambda g, t: norm(g.get("po_reference")) == norm(t["po_reference"]),
    "Subtotal": lambda g, t: money_eq(g.get("subtotal"), t["subtotal"]),
    "Tax": lambda g, t: money_eq(g.get("tax_amount"), t["tax_amount"]),
    "Shipping": lambda g, t: money_eq(g.get("shipping_amount"), t["shipping_amount"]),
    "Discount": lambda g, t: money_eq(g.get("discount_amount") or 0, t["discount_amount"] or 0),
    "Round off": lambda g, t: money_eq(g.get("round_off") or 0, t["round_off"] or 0),
    "Total": lambda g, t: money_eq(g.get("total_amount"), t["total_amount"]),
    "Line items (count & amounts)": lambda g, t: len(g.get("line_items") or []) == len(t["line_items"]) and all(
        money_eq(a.get("amount"), b["amount"]) for a, b in zip(g.get("line_items") or [], t["line_items"])),
    "Remit-to account": lambda g, t: (t["bank_details"]["account_number"] or None) == ((g.get("bank_details") or {}).get("account_number") or None),
}

if not llm_available():
    sys.exit("Set GEMINI_API_KEY first (in .env or your shell).")
db.init(reset=True)
print(f"Model: {MODEL}\n")
rows, field_hits = [], {f: [0, 0] for f in FIELDS}
for spec in G.SAMPLES:
    time.sleep(4)  # stay under free-tier requests-per-minute
    path = ROOT / "samples" / spec["file"]
    t0 = time.time()
    rid = pipeline.start_run(path.read_bytes(), path.name, "benchmark")
    while db.get_run(rid)["status"] == "running":
        time.sleep(0.2)
    r = db.get_run(rid)
    ex = r["trace"]["stages"].get("extract", {}).get("data", {})
    got, t = ex.get("fields") or {}, truth(spec)
    scores = {}
    for name, fn in FIELDS.items():
        if t["document_type"] != "invoice" and name != "Document type":
            continue  # invoice fields don't apply to a quote/statement
        try:
            ok = bool(fn(got, t))
        except Exception:
            ok = False
        scores[name] = ok
        field_hits[name][0] += ok
        field_hits[name][1] += 1
    exp_d, exp_r = EXPECTED[spec["id"]]
    dec_ok = r["decision"] == exp_d and (exp_r is None or r["route_to"] == exp_r)
    row = {"sample": spec["file"], "kind": spec.get("kind", ""), "decision": r["decision"], "route": r["route_to"],
           "expected": exp_d, "decision_ok": dec_ok, "fields_ok": sum(scores.values()), "fields": len(scores),
           "misses": [k for k, v in scores.items() if not v], "extractor": r["extractor"],
           "model": (ex.get("meta") or {}).get("model"), "read_ms": (ex.get("meta") or {}).get("latency_ms"),
           "seconds": round(time.time() - t0, 1)}
    rows.append(row)
    print(f"{'PASS' if dec_ok else 'FAIL'}  {spec['file'][:46]:46} fields {row['fields_ok']}/{row['fields']}  {r['decision']:7} {row['seconds']:5.1f}s"
          + (f"  misses: {', '.join(row['misses'])}" if row["misses"] else ""))

tot_ok = sum(r["fields_ok"] for r in rows)
tot = sum(r["fields"] for r in rows)
reads = [r["read_ms"] for r in rows if r["read_ms"]]
result = {
    "run_on": date.today().isoformat(), "model": MODEL, "samples": len(rows),
    "field_accuracy": round(100 * tot_ok / tot, 1), "fields_correct": tot_ok, "fields_total": tot,
    "decision_accuracy": round(100 * sum(r["decision_ok"] for r in rows) / len(rows), 1),
    "decisions_correct": sum(r["decision_ok"] for r in rows),
    "median_read_seconds": round(statistics.median(reads) / 1000, 1) if reads else None,
    "by_field": [{"field": f, "correct": h[0], "total": h[1]} for f, h in field_hits.items()],
    "rows": rows,
}
(ROOT / "app" / "seed" / "benchmark.json").write_text(json.dumps(result, indent=2))
md = [f"# Extraction & decision benchmark\n",
      f"Run on {result['run_on']} with `{MODEL}` (free tier) over the {len(rows)} sample documents, each through the full pipeline.",
      "Ground truth is the exact data each document was rendered from; it is read only after the decision is made.\n",
      f"- **Fields read correctly:** {tot_ok}/{tot} ({result['field_accuracy']}%)",
      f"- **Decisions and routing correct:** {result['decisions_correct']}/{len(rows)} ({result['decision_accuracy']}%)",
      f"- **Median AI read time:** {result['median_read_seconds']} s\n",
      "| Field | Correct |", "| --- | --- |", *[f"| {f} | {h[0]}/{h[1]} |" for f, h in field_hits.items()], "",
      "| Sample | Fields | Decision | Expected | Misses |", "| --- | --- | --- | --- | --- |",
      *[f"| {r['sample']} | {r['fields_ok']}/{r['fields']} | {r['decision']} | {r['expected']} | {', '.join(r['misses']) or '—'} |" for r in rows], "",
      "Caveat: 8 synthetic documents is a smoke-level benchmark, not a statistical claim. Real vendor invoices are messier;",
      "the next step is a labelled set of real (redacted) invoices from the customer.", ""]
(ROOT / "docs" / "BENCHMARK.md").write_text("\n".join(md))
print(f"\nFields {tot_ok}/{tot} ({result['field_accuracy']}%) · decisions {result['decisions_correct']}/{len(rows)} · wrote app/seed/benchmark.json and docs/BENCHMARK.md")
