# ClearAP: invoice PDF → reasoned decision

**AI Solutions Analyst case study · PS-1 (Finance / AP)**

ClearAP takes a vendor invoice PDF, digital or scanned, and returns one of three decisions:

- **Approve:** posted to the ledger and scheduled for payment.
- **Hold:** routed to the team that owns the problem, with an SLA.
- **Reject:** with a drafted vendor email where one is needed.

Every step in between is visible live, and every decision traces back to named checks with evidence.

> **Design principle: the AI reads, the rules decide.** Google Gemini does the one thing rules can't: reading messy, differently formatted, sometimes scanned documents into structured fields with a confidence per field. Every decision that moves money is made by deterministic rules you can audit, test and tune.

---

## Quick start (local, ~1 minute)

```bash
cp .env.example .env          # add your free GEMINI_API_KEY (aistudio.google.com/api-keys)
./run.sh                      # creates a venv, installs, starts on :8000
open http://localhost:8000
```

No API key? It still runs. It falls back to a heuristic text parser, and scanned invoices go to a human for manual keying. The badge in the top-right shows which reader is active.

**Before a demo:** `.venv/bin/python scripts/smoke_llm.py` runs every sample against the real Gemini API and checks each decision against the expected one.

**Tests:** `.venv/bin/python -m pytest -q tests` runs 19 end-to-end tests: every sample with a perfect reader, the heuristic path, policy changes, and re-submission.

## Deploy (live link)

The repo includes a `Dockerfile` and a `render.yaml`:

- **Render:** New → Blueprint → point it at this repo → set `GEMINI_API_KEY`.
- **Railway / Fly / Hugging Face Spaces (Docker):** same image. It listens on `$PORT`.

Storage is SQLite inside the container. On free tiers it resets on redeploy. That's fine for a demo, and "Reset demo data" on the dashboard does the same thing on purpose.

## What's in the box

| Path | What it is |
|---|---|
| `app/pipeline.py` | The 8-stage pipeline, every check, the decision engine, payment plan, vendor emails |
| `app/extract.py` | Gemini extraction (PDF in, strict JSON schema out, per-field confidence) plus the heuristic fallback |
| `app/db.py` | SQLite: runs, invoice ledger, audit trail, editable policy, extraction cache |
| `app/seed/master_data.json` | The "ERP": 8 vendors (one blocked), 8 POs, goods receipts, invoice history |
| `app/main.py` | FastAPI: REST, plus Server-Sent Events for the live run view |
| `static/` | The UI: dashboard, inbox, live run view, review queue, POs & ledger, rules & process |
| `samples/` | 7 test invoices in 3 vendor layouts, including one image-only scan |
| `scripts/generate_samples.py` | Regenerates the test invoices |
| `docs/DESIGN.md` | Process map, assumptions, rules, edge-case rationale, trade-offs, what's next |
| `docs/DEMO_SCRIPT.md` | 5-minute video script, live-demo runbook, likely interview questions |

## The pipeline

```
PDF ─▶ 1 Intake ─▶ 2 Analysis ─▶ 3 AI extraction ─▶ 4 Validation ─▶ 5 Vendor ─▶ 6 Duplicates ─▶ 7 PO & receipts ─▶ 8 Decision
       sha-256      digital vs     Gemini → schema     math, tax,       master,      exact file,       quoted or inferred PO,    reject > hold > approve
                    scanned        + confidence        dates,           status,      normalised #,     line match, 3-way,        route to owner + SLA,
                                   (fallback: parser)  grounding        bank, domain queue, fuzzy      cumulative vs PO cap      payment plan / email
```

## Test inbox: happy path + 4 edge cases (+2 extra inputs)

| # | Invoice | What makes it hard | Decision |
|---|---|---|---|
| Happy | Northwind, exact 3-way match | — | **Approve**, and flags the 2/10 early-payment discount ($88.52) |
| Edge 1 | Atlas Cloud, split billing | The invoice is fine on its own. Combined with the $30k already billed, it overruns the PO by $3,750 (7.8%) | **Hold → Buyer**, with a suggested partial approval of $18,000 |
| Edge 2 | Brightline, **scanned** with **no PO number** | No text layer; the PO has to be inferred from lines, amounts and receipts | **Approve**, PO inferred at 0.90 confidence (the threshold is configurable) |
| Edge 3 | Meridian, **disguised duplicate** | New file, number reformatted (`INV-2026-0417` vs `INV 2026/0417`), stamped "SECOND NOTICE" | **Reject**: duplicate of an invoice already paid on ACH-77812 |
| Edge 4 | Keystone, **bank details changed** | Every number is perfect. The remit-to account and sender domain aren't | **Hold → Treasury & Fraud**, call back on the number on file |
| Extra | Pinnacle, unbudgeted freight | +$45 (0.78%), inside the 2% / $500 tolerance | **Approve** with the variance noted |
| Extra | Halcyon, no invoice # or date | Can't post or dedupe | **Reject**: returned to vendor with a drafted email |

Approving posts to the ledger, so **running the same invoice twice is caught as a duplicate**. That's a good live moment.
