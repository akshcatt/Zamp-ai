# Demo playbook

## A. Pre-flight (do this every time, 5 minutes before recording or the call)

1. `cp .env.example .env` and put your free Gemini key in (https://aistudio.google.com/api-keys). Then `./run.sh` → open http://localhost:8000.
2. `.venv/bin/python scripts/smoke_llm.py`. Every line must say PASS. It pauses between invoices to stay within free-tier rate limits. (It uses its own temporary database, so it won't touch the demo data.)
3. On the Overview page, click **Reset demo data**. This gives you a clean ledger and empty history.
4. The top-right badge must read **AI on · gemini-3.1-flash-lite** (green dot). If it says "AI off · basic reader", the key isn't loaded.
   On the deployed site, **How it works → Run connection test** confirms the key works on the server.
   Click **Set your name** in the top bar so your reviews are recorded under your name.
5. Rules page: confirm the defaults (2%, $500, $25,000, 0.85, 0.70, 45, 180, Yes).
6. Browser at 110–125% zoom, notifications off, one tab, other windows closed.

## B. The 5-minute video (Loom)

Target 4:45. Speak to a non-technical AP manager, not to an engineer.

**0:00–0:25 · Frame it** (Overview, empty)
> "Mid-size companies get hundreds of invoices a month. Someone opens each PDF, finds the PO, checks the numbers and decides. The costly errors aren't typos. They're the invoice that's fine on its own but wrong in context, and the invoice that's perfect but pays a fraudster. I built ClearAP around one principle: **the AI reads, the rules decide.** Gemini reads messy invoices; every decision that moves money comes from rules you can audit."

**0:25–1:25 · Happy path, live** (Upload → *Clean invoice* → Run)
- While the stages tick:
  > "Intake fingerprints the file. Analysis sees it's a digital PDF. Now Gemini reads it into a fixed schema, with a confidence score per field."
- Click **AI reads the invoice → Side by side** (document on the left, what the AI read on the right):
  > "Fields it couldn't find stay blank. It never guesses."
- Validation:
  > "Arithmetic, tax, dates, and grounding: every key value Gemini extracted must literally appear in the PDF text, which catches misreads."
- The decision appears:
  > "Approved, posted, scheduled. And it noticed the 2/10 terms, so it schedules payment on day 10 to capture an $88 discount, which humans routinely miss."

**1:25–2:20 · Edge case: bank change** (Run *Perfect match, but the bank account changed*)
> "This one is the scariest invoice an AP team gets, because every number is correct: right vendor, right PO, right amount."
- Point at Vendor verification turning red:
  > "But the remit-to account doesn't match our vendor master. The invoice says 'we've changed banks', and the sender's domain is a look-alike. This is business email compromise."
- The decision appears:
  > "So it's held for Treasury, same-day SLA, and look at the task: call the vendor on the number **we** have on file, not the one on the invoice. Payments only ever go to the master-file account."

**2:20–3:10 · Edge case: scanned, no PO** (Run *Scanned image, no PO reference*)
> "A scan: no text layer at all, so Gemini reads the image."
- When PO matching finishes, click **Order match → Line by line**:
  > "There's no PO number on it either. So it scores this vendor's open POs on line items, remaining balance and delivery receipts. One PO wins at 0.90 confidence, far ahead of the other. Note that it matched 'LTL Freight CHI→CMH' to 'Chicago to Columbus' on price and quantity, not just wording."
- Decision:
  > "Approved, flagged as an inferred PO. If your policy is 'no PO, no pay', that's one toggle and this goes to the buyer instead."

**3:10–3:50 · Edge case: split billing** (Run *Split billing pushes PO over its cap*)
> "This invoice is fine on its own: 145 hours at the PO rate. But the process checks the ledger."
- Show the PO balance bar:
  > "$30k already billed, plus this, overruns the 320-hour PO by $3,750. It's held for the named buyer, with the exact partial amount we could safely pay today."

**3:50–4:15 · Duplicate, live** (re-run the *Clean invoice*)
> "Remember the first invoice? It's now in the ledger. If the vendor sends it again…"
- Decision: rejected as a duplicate.
  > "…it's caught. The same normalisation catches reformatted numbers like 'INV 2026/0417' versus 'INV-2026-0417'."

**4:15–4:45 · Overview & close** (Overview → Needs review → open the Keystone invoice → pick a quick note or type one → Reject)
> "The overview shows how much ran with no one touching it, what's waiting and with which team and how close it is to its deadline, the money protected, and the time saved. The charts show what came in over time, why invoices were stopped, which team is reviewing what, and where the time goes; any chart flips to a table. You can search, filter and export to a spreadsheet. Humans only see exceptions, routed to the team that owns them, and every action needs a note that goes into the audit trail. Rules and tolerances are editable here, in plain language. That's ClearAP."

## C. Live interview runbook (longer version, ~10 min)

Order: Happy → Bank change → Scanned/no PO → Split billing → Disguised duplicate (sample 04) → Re-run the happy path (live duplicate) → **Policy flip** → Upload *their* PDF.

- **Policy flip:**
  - On Rules, set the price tolerance to `0.5` and save.
  - Run *Pinnacle*: it's now **held**, because the $45 freight is 0.78%, over the new limit.
  - Set it back to `2`.
  - Point: "The business owns the thresholds, not the code."
- **If they send a phone photo:** drop it in too. JPG, PNG and iPhone HEIC are converted to a PDF at intake (sample 08 shows this).
- **If they hand you a random invoice:** drop it in. An unknown vendor goes to Vendor Management (onboarding). A non-invoice is rejected. Either way it fails safe.
- **Correct & re-check (if the AI is off, or a scan was misread):**
  - Open the held invoice and click **Correct details & re-check**.
  - Type the fields from the **Side by side** view, add a note, and submit.
  - It re-runs through every rule as a new run. The original is kept and linked.
  - Point: "People fix the reading, never the rules."
- **Bulk review:** in Needs review, tick several invoices, add one note, and Approve or Reject. Each is still checked on its own, and one that can't be actioned is reported, not silently skipped.
- **Vendors:** open Vendors to show the Keystone card flagged *Bank-change attempt seen* and Meridian flagged *Sent a repeat invoice*: risk by vendor, not just by invoice.
- **Exports:** Overview → Export → Excel gives sheets for Summary, Invoices, every Exception, and the Ledger. That's what an AP lead hands to finance.
- **Accuracy question:** How it works → *How accurate is it?* shows 80/80 fields and 8/8 decisions on the samples. Be upfront that this is a smoke test on clean generated documents; the next step is real redacted invoices.
- **Free-tier limits:** if a run shows "rate limit reached", wait about 60 seconds. Don't click *Run all* during the live demo; run invoices one at a time.
- **Backup if Wi-Fi or the API dies:** the app keeps running in heuristic mode for digital PDFs, and the scan goes to manual keying. Say so; that *is* the design. Every past run can be re-animated with **↻ Replay** from its event log.

## D. Questions you'll likely get, with crisp answers

- **Why not let the LLM make the decision?**
  > Because AP decisions need to be consistent, explainable and auditable. Rules give the same answer every time, and every decision is traceable to evidence. The model does what rules can't: read unstructured documents.
- **How do you stop hallucinated numbers?**
  > A strict schema; null instead of guessing; per-field confidence with a floor; arithmetic reconciliation (lines → subtotal → tax → total); and, on digital PDFs, a grounding check that each key value appears verbatim in the text layer.
- **Why is the PO-inference formula weighted like that?**
  > Line similarity is the strongest evidence, so it gets 0.55. Amount fit against the remaining balance gets 0.25. Receipts get 0.20. It also requires a margin over the runner-up, because ambiguity is the real risk. The weights are a starting point. With reviewer data I'd calibrate them, but I'd keep the scoring transparent.
- **What happens at 10,000 invoices a month?**
  > Put a queue in front (email webhook → job queue → workers), replace JSON/SQLite with ERP connectors and Postgres, batch extraction for non-urgent mail, and add an eval set to monitor extraction accuracy per field. The decision engine doesn't change.
- **Cost per invoice?**
  > One Gemini Flash-Lite call per invoice, a few thousand tokens for a one-page PDF. The demo runs on the free tier; at production volume the paid tier costs a fraction of a cent per invoice, against several minutes of AP labour. The model is an env var, so I'd run an eval before switching models.
- **Why route to different teams?**
  > Because the fix differs. A bank change needs Treasury and a callback; a PO overrun needs the buyer's change order; an unknown vendor needs onboarding. Sending everything to "AP" just moves the pile.
- **What did you assume?**
  > See DESIGN.md §5. USD; POs net of tax; 2%/$500 tolerance; 3-way match for goods; missing invoice number means return to vendor.
- **What would you build next?**
  > Email intake, ERP connectors, credit-note matching, multi-currency/VAT, an eval set, and segregation of duties (DESIGN.md §7).
- **How did you use AI to build this?**
  > An AI coding assistant helped scaffold the app and generate realistic test invoices, three vendor layouts plus a rasterised scan. I designed the process map, the rules and the edge cases. A test suite with a "perfect reader" fixture verifies the rules independently of the model, and a smoke script verifies the live model path.
