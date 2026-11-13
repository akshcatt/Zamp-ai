# NoCap: invoice PDF → reasoned decision

**AI Solutions Analyst case study · PS-1 (Finance / AP)**

NoCap takes a vendor invoice (a digital PDF, a scanned PDF, or a phone photo in JPG/PNG/HEIC) and returns one of three decisions:

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

**Before a demo:** `.venv/bin/python scripts/smoke_llm.py` runs every sample against the real Gemini API and checks each decision against the expected one. **How it works → Check the AI connection** does a one-call check from the browser.

**Benchmark:** `.venv/bin/python scripts/benchmark.py` scores what the AI read, field by field, against the exact data each sample was generated from, plus the final decision. Latest run: **105/105 fields (now including discount and round-off), 9/9 decisions, ~3–10 s per read** with `gemini-3.1-flash-lite` ([docs/BENCHMARK.md](docs/BENCHMARK.md)). Eight clean synthetic documents is a smoke test, not proof; the page says so too.

**Tests:** `.venv/bin/python -m pytest -q tests` runs 45 tests:
- **Pipeline:** every sample through the pipeline with a perfect reader, the heuristic path, photo intake (JPG/PNG/HEIC), unsupported files, policy changes, re-submission, and persistence across a restart.
- **HTTP API:** batch intake, correct-and-re-check, bulk review, restart recovery, not-an-invoice filing, archive and restore (with duplicate protection kept), schema migration, learning a new vendor and verifying it once, blocking vendors, discounts and round-offs, vendor profiles, Excel/CSV export, health and security headers, and the login with CSRF and tamper-proof sessions.

## Features

- **Built for Indian AP:** amounts in ₹ with lakh/crore grouping, GST invoices (CGST + SGST within Karnataka, IGST across states, reverse charge on freight), GSTIN on every vendor, remit-to banks checked by IFSC + account number, day-first dates (08/11/2026 is 8 November). The GST check accepts only real GST slabs.
- **Any bill, not just formal invoices:** GST tax invoices, utility bills, restaurant bills, shop receipts, travel receipts, online orders and handwritten cash memos. Each bill is typed, **categorised** (utilities, meals, travel, office supplies…) and booked to the matching ledger account, and bills that were already paid by card, UPI or cash are recorded as paid instead of scheduled. Everyday sellers are learned without a verification step, and a bill with no number is still checked for repeats by seller, date and amount.
- **Lenient: report, don't hold.** Only real money risks stop a bill: duplicates, a changed or look-alike bank account, a blocked vendor, no readable amount, money genuinely more than it should be (beyond the rounding limit, over the PO, PO overrun), goods not received, manager sign-off on very large invoices, and verifying a new supplier who will be paid by bank transfer. Everything else (missing number or date, no PO, GSTIN or bill-to mismatch, unusual GST rate, old dates, low reading confidence) is a **reported issue** with a tick box: the bill still goes through, and ticking records who looked and when. The Overview shows how many issues are left to tick and filters to them.
- **No vendor master:** NoCap learns vendors. Anyone already in the payment history is known; a new vendor is added automatically from their first invoice (name, GSTIN, email domain, bank + IFSC) as *Unverified*, and that invoice waits for a one-click **Verify vendor** (check the GSTIN on the GST portal, confirm the bank). Verifying re-checks their waiting invoices against every rule, and from then on their GSTIN and bank are the reference, so a later bank swap is still caught. Vendors can be blocked and unblocked from the Vendors page.
- **Discounts and round-offs:** a "Less: Trade Discount" row is detected and checked (GST must be charged on the discounted value), and "Round Off" rows are read. **Differences up to ₹10 or 2% of the amount, whichever is smaller, are never treated as a problem** (two settings on the Rules page): totals, line maths, price vs PO and repeat-invoice matching all treat them as rounding and say so.
- **Ask NoCap:** a read-only chat assistant (Gemini) that answers from the live data: what's waiting, why an invoice was held, how much was protected. It links straight to the invoice, and can't approve or pay anything. Without an API key it falls back to simple built-in answers.
- **Sections & progress:** a sidebar groups the app into Workspace (Overview, Inbox, Review queue), Insights (Vendors, Ledger & records) and Engine (Rules & accuracy). The sidebar shows how much of the queue you've cleared today and your review streak, and the Overview has milestones earned from real results (first duplicate blocked, ₹1 lakh protected, queue zero…).
- **Live processing:** each invoice streams through 8 steps with the evidence behind every check, and the original document can be shown side by side with what the AI read.
- **Batch intake:** drop up to 25 files at once and track them together; a bad file never blocks the others.
- **Exceptions only:** held invoices go to the team that owns the problem, with a deadline (due soon or overdue). **Bulk approve/reject** takes one note, and each invoice is still checked and audited on its own.
- **Correct & re-check:** a reviewer types or fixes the fields from the document (an unreadable scan, a misread total), and the invoice re-runs through every rule. The original is kept, marked superseded, and linked both ways in the history.
- **Vendor profiles:** what each vendor has sent, what was approved, what's waiting, open purchase-order balance, and red flags (bank-change attempts, repeat invoices, blocked, unverified).
- **Analytics:** volume over time, outcome mix, top exception reasons, reviewer workload and turnaround, time per step, and reader mix. Every chart has hover detail and a table view.
- **Not-an-invoice filing:** quotes, statements and purchase orders that land in the AP inbox are filed for reference, not rejected, and kept out of the approval and exception numbers. If one turns out to be a real bill, a reviewer corrects it and it's re-checked.
- **Archive, don't delete:** "Archive finished" clears completed work off the working list and charts. Nothing is lost: the history, documents and payment record stay, archived invoices still block repeat submissions, and any of them can be restored. Invoices waiting for a person are never archived.
- **Exports:** an Excel workbook (Summary, Invoices, every Exception, Ledger), CSV, and a full database backup.
- **Accountability:** every human action records who and why. With sign-in on, the signed-in user is recorded and can't be spoofed from the browser.
- **Resilience:** if the server restarts mid-run, that invoice is held for a person (never silently lost or paid); if the AI is down, a text parser takes over; if nothing can read the document, a person keys it in.

## Security & operations

| Setting | Purpose |
|---|---|
| `GEMINI_API_KEY` | Enables the AI reader. Without it, the basic text reader is used. |
| `APP_USERNAME`, `APP_PASSWORD` | Turn on sign-in. If either is empty, the app is open (handy for a demo). |
| `APP_SECRET` | Signs session cookies (Render generates it). Sessions last 7 days. |
| `AP_GEMINI_MODEL` | Optional model override (default `gemini-3.1-flash-lite`, with fallbacks). |

- **Sign-in:** HMAC-signed session cookies (HttpOnly, SameSite=Lax, Secure on HTTPS) and a CSRF token on every state-changing request. Login attempts are rate-limited, and so are uploads (60 per minute per IP).
- **Headers:** CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, and HSTS on HTTPS.
- **Uploads:** checked by magic bytes (never the extension), limited to 20 MB each and 25 per batch, filenames sanitised. Exports neutralise spreadsheet formulas.
- **Container:** runs as a non-root user, with a Docker `HEALTHCHECK`. `/healthz` checks the database for the platform's probe, and API docs are at `/api/docs`.
- **Secrets:** the API key never leaves the server. The AI test endpoint redacts it from any error message.

## Deploy (live link)

The repo includes a `Dockerfile` and a `render.yaml`:

- **Render:** New → Blueprint → point it at this repo → set `GEMINI_API_KEY` (and optionally `APP_USERNAME` / `APP_PASSWORD` to require sign-in).
- **Railway / Fly / Hugging Face Spaces (Docker):** same image. It listens on `$PORT`.

Storage is a single SQLite database file (`data/ap.db`). It holds the invoices, the payment record, the review history, your rules, the saved AI reads, and the original documents. **Records → Database** shows what's in it and has a **Download backup** button, a consistent snapshot taken even while invoices are running. Render's free plan has no persistent disk, so the database is wiped when the instance sleeps (after ~15 idle minutes) and on every redeploy; while it wakes, Render answers 503. The blueprint handles this for a demo:

- `KEEP_AWAKE=1` makes the app ping its own URL every 10 minutes, so it doesn't sleep (data now only resets on a redeploy).
- `DEMO_AUTOLOAD=1` reloads the 9 sample invoices whenever the database starts empty, from stored Gemini reads in `app/seed/demo_reads.json` (no AI calls; every rule still runs). Rebuild them with `scripts/build_demo_reads.py`.
- The page retries quietly with a "waking up" notice instead of failing on a 502/503/504.

For data that survives redeploys, use a paid instance with a disk mounted at `/app/data` (commented out at the bottom of `render.yaml`).

## What's in the box

| Path | What it is |
|---|---|
| `app/pipeline.py` | The 8-stage pipeline, every check, the decision engine, payment plan, vendor emails |
| `app/extract.py` | Gemini extraction (PDF in, strict JSON schema out, per-field confidence) plus the heuristic fallback |
| `app/db.py` | SQLite (WAL mode, one file): runs, invoice ledger, audit trail, editable policy, extraction cache, uploaded documents; backup snapshot |
| `app/seed/master_data.json` | The "ERP": 8 vendors (one blocked), 8 POs, goods receipts, invoice history |
| `app/main.py` | FastAPI: REST + Server-Sent Events, batch intake, bulk review, re-keying, vendor profiles, exports, health |
| `app/security.py` | Optional sign-in, signed sessions, CSRF, rate limits, security headers |
| `static/` | The UI: overview with analytics, upload (single or batch), live invoice view with side-by-side check and correction form, needs-review with bulk actions, vendors, records, how it works (accuracy, AI connection test, editable rules). Light/dark, ⌘K search |
| `scripts/benchmark.py` | Field-level and decision accuracy against the real model; writes `docs/BENCHMARK.md` |
| `samples/` | 8 test invoices in 3 vendor layouts, including an image-only scan and a phone photo (JPEG) |
| `scripts/generate_samples.py` | Regenerates the test invoices |
| `docs/DESIGN.md` | Process map, assumptions, rules, edge-case rationale, trade-offs, what's next |
| `docs/DEMO_SCRIPT.md` | 5-minute video script, live-demo runbook, likely interview questions |

## The pipeline

```
PDF/photo ─▶ 1 Intake ─▶ 2 Analysis ─▶ 3 AI extraction ─▶ 4 Validation ─▶ 5 Vendor ─▶ 6 Duplicates ─▶ 7 PO & receipts ─▶ 8 Decision
             type+sha-256 digital vs     Gemini → schema     math, tax,       master,      exact file,       quoted or inferred PO,    reject > hold > approve
                    scanned        + confidence        dates,           status,      normalised #,     line match, 3-way,        route to owner + SLA,
                                   (fallback: parser)  grounding        bank, domain queue, fuzzy      cumulative vs PO cap      payment plan / email
```

## Test inbox: happy path + 4 edge cases (+2 extra inputs)

| # | Invoice | What makes it hard | Decision |
|---|---|---|---|
| Happy | Sahyadri, exact 3-way match | — | **Approve**, and flags the 2/10 early-payment discount (₹7,081.60) |
| Edge 1 | Kaveri Cloud, split billing | The invoice is fine on its own. Combined with the ₹24 lakh already billed, it overruns the PO by ₹3,00,000 (7.8%) | **Hold → Buyer**, with a suggested partial approval of ₹14,40,000 |
| Edge 2 | Ganga Freight, **scanned** with **no PO number** | No text layer; the PO has to be inferred from lines, amounts and receipts | **Approve**, PO inferred at 0.90 confidence (the threshold is configurable) |
| Edge 3 | Meenakshi, **disguised duplicate** | New file, number reformatted (`INV-2026-0417` vs `INV 2026/0417`), stamped "SECOND NOTICE" | **Reject**: duplicate of an invoice already paid on UTR-77812 |
| Edge 4 | Kesari, **bank details changed** | Every number is perfect. The remit-to account and sender domain aren't | **Hold → Treasury & Fraud**, call back on the number on file |
| Extra | Pragati, unbudgeted freight | +₹3,600 (0.78%), inside the 2% / ₹40,000 tolerance | **Approve** with the variance noted |
| Extra | Annapurna, no invoice # or date | A formal invoice missing key details | **Approve** with an issue to tick off, and a drafted email asking for the number and date |
| Bill | Truffles, **restaurant bill** paid by card | Not a formal invoice, no PO, new seller | **Recorded as already paid** under Meals & entertainment |
| Bill | BESCOM, **electricity bill** | No GST, no PO, pay by due date | **Approve**, scheduled for the due date, under Utilities |
| Bill | Sri Lakshmi Stores, **handwritten cash memo** (photo) | No GSTIN, paid in cash, photographed | **Recorded as already paid** under Pantry & housekeeping |
| Extra | Sahyadri, **phone photo** (JPEG) | Not a PDF at all: tilted, on a desk, uneven light | **Approve**: converted to PDF at intake, read like a scan |

Approving posts to the ledger, so **running the same invoice twice is caught as a duplicate**. That's a good live moment.
