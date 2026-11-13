# NoCap: design notes

## 1. The problem, from the AP clerk's chair

An AP specialist does the same five things for every invoice:

1. Read it.
2. Find the vendor.
3. Find the PO.
4. Check that the numbers line up.
5. Decide.

The expensive mistakes aren't in steps 1–4 on a clean invoice. They happen when:

- **the invoice looks fine on its own but isn't fine in context**: a split bill that overruns the PO, or a resubmitted duplicate;
- **the invoice is perfect but the payment details aren't**: a bank-change fraud;
- **the document is hard to read**: a scan, a missing PO number, missing fields.

So the build optimises for two things:

- **Touchless on the clean majority.** Clean invoices go straight through with no human touch.
- **Precise, explained exceptions.** Everything else reaches the right human, with the evidence and a suggested action.

## 2. Process map

| # | Stage | Input → output | Decision points |
|---|---|---|---|
| 1 | Intake | File bytes → file type + SHA-256 fingerprint | Type detected from the bytes, not the extension. Photo (JPG/PNG/WEBP/HEIC) → auto-rotate, convert to a one-page PDF. Anything else → reject |
| 2 | Document analysis | PDF → text layer + page stats | Scanned (no text) → vision path, grounding check off |
| 3 | AI extraction | PDF → strict-schema JSON + per-field confidence | API down → cached extraction → heuristic parser → manual keying |
| 4 | Validation | Fields → checks | Missing invoice #/date → return to vendor; math/tax/date/confidence/grounding issue → AP |
| 5 | Vendor verification | Fields + learned vendor list → matched vendor | New vendor → added as unverified from this invoice, held once for Vendor Mgmt; blocked → reject; bank/domain/change-notice → Treasury & Fraud |
| 6 | Duplicate detection | Fields + ledger + queue → checks | Same normalised # and amount → reject; same # with a different amount, or same amount near the same date → AP |
| 7 | PO & receipt matching | Fields + POs + receipts + ledger → match | PO quoted or inferred; price/qty variance vs tolerance; goods received; cumulative vs PO cap; controller threshold |
| 8 | Decision | All checks → decision | any reject → **REJECT**; any hold → **HOLD** to the highest-priority owner; else **APPROVE** + post + schedule payment |

Routing priority when several teams have holds: **Treasury & Fraud → Controller → Procurement → Vendor Mgmt → AP**. The others are listed as "also notify".

## 3. Why the AI reads and the rules decide

- **Reading** is the step that breaks every rule-based system. Vendor layouts vary, labels vary ("Invoice #", "Bill Number", "Invoice No."), and some invoices are scans. A frontier model reading the PDF directly, text and page image together, handles this without per-vendor templates. That's why Gemini is here.
- **Deciding** must be explainable, consistent and testable. "The model thought it was fine" is not an audit trail. Every decision in NoCap is a pure function of named checks, so the same input always gives the same decision, the tests pin it down, and an auditor can read it.
- **Guards on the reader:**
  - a strict JSON schema, so fields can't be invented;
  - an instruction to return null rather than guess;
  - per-field confidence, with a configurable floor;
  - a **grounding check**: on digital PDFs, the invoice number, total, PO and bank account must each appear verbatim in the text layer, or the invoice is held. That catches a model misread before it becomes a payment.

**Model choice:** Google Gemini `gemini-3.1-flash-lite`. It's free-tier and reads PDFs natively, including scanned page images and photos. I measured the free-tier models on these invoices: `gemini-3.1-flash-lite` took about 3 s, `gemini-3.5-flash-lite` took 36 s or was overloaded (503), and `gemini-3.5-flash` took about 20–27 s. All read the fields correctly, so I picked the fastest. Extraction is a reading task, not a reasoning marathon, so a fast Flash-Lite model fits. Temperature is 0 for consistent reads. The model is env-configurable (`AP_GEMINI_MODEL`). The JSON schema is enforced by the API; if that's ever rejected, the call falls back to the schema in the prompt. Thinking is set to *minimal*, because transcription needs no deliberation and it's several times faster. Each model gets a hard 30-second wall-clock deadline. The SDK's HTTP timeout alone didn't stop a slow, trickling response, so the call runs in a thread and the pipeline stops waiting when the deadline passes. The worst case for a run is therefore bounded. Free-tier models are sometimes overloaded (503) or rate-limited (429). On a 503 the call moves straight to the next free model (`gemini-3.5-flash-lite`, then `gemini-3.5-flash`). On a 429 it waits once before moving on. The model that actually answered is recorded on the run.

**Data note:** on Gemini's free tier, Google may use submitted content to improve its products. That's fine for these synthetic invoices. In production, use the paid tier or Vertex AI, where it isn't.

## 4. Edge cases, and why these four

I picked edge cases that test **judgment and context**, not parsing tricks:

1. **Split billing that overruns the PO (Kaveri).** Each invoice is fine alone; the problem only exists across invoices. Tests whether the process keeps state (the ledger) and checks *cumulative* billing against the PO cap. The output is actionable: a change order, or a partial approval of exactly the remaining ₹14,40,000.
2. **Scanned invoice with no PO number (Ganga Freight).** Two hard things at once:
   - No text layer, so Gemini reads the image and the text-grounding check is honestly switched off.
   - No PO reference, so the process scores each of the vendor's open POs: `0.55 × line similarity + 0.25 × amount fit vs remaining balance + 0.20 × goods-receipt support`. It accepts the best only if it clears the confidence floor and beats the runner-up by a clear margin.

   Line matching deliberately weights structured signals (exact unit price, quantity within PO) over wording, because "LTL Freight CHI → CMH" and "LTL freight, Chicago IL to Columbus OH" share almost no words.
3. **Disguised duplicate (Meenakshi).** The most common way AP pays twice: the vendor re-sends a "second notice" with the number written differently. The file hash differs, so naive dedup fails. Normalising invoice numbers (`INV 2026/0417` = `INV-2026-0417` = `INV20260417`) plus vendor plus amount catches it, and the explanation cites the original payment reference. The process also checks the review queue, so the same invoice can't be in review twice.
4. **Bank-detail change (Kesari).** Business email compromise is the single most costly AP fraud. It's interesting because **every financial check passes**. Three independent signals fire:
   - the remit-to account differs from the verified account on file;
   - the invoice text announces a "new bank";
   - the sender uses a look-alike domain (`keystone-fm-billing.com` vs `keystonefm.com`).

   The output is a hold for Treasury, an internal task to **call back on the phone number on file**, and an explicit "do not reply to the invoice's email". Payments only ever go to the verified account on file-file account. That's a control, not a check.

Three extra inputs show the "close but not exact", "communicate back" and "it isn't even a PDF" behaviours. Pragati has ₹3,600 of freight within tolerance, so it's approved with a note. Annapurna has no invoice number or date, so it's returned to the vendor with a drafted email. The Sahyadri phone photo (a JPEG of a printed invoice, tilted on a desk) is normalised to a PDF at intake, so every later stage sees one input type, and it's read like a scan.

## 5. Assumptions (stated, so they can be challenged)

- **Buyer:** Lumen Home Goods Pvt. Ltd., a fictional Bengaluru retailer (GSTIN 29AAECL4821K1Z3). INR only. GST: CGST + SGST from Karnataka vendors, IGST from other states, reverse charge (RCM) on freight. Remit-to banks are checked by IFSC + account number.
- **POs are net of tax.** Matching compares the invoice net (subtotal + shipping, or total − tax when prices include tax) against PO line values.
- **Tolerance:** 2% **and** ₹40,000, whichever is tighter. This applies per invoice (price/qty variance) and to cumulative billing against the PO.
- **Goods POs need a receipt (3-way); service POs don't (2-way).**
- **Inferred POs** can be auto-accepted at ≥ 0.85 confidence with a ≥ 0.15 margin. Many companies run "no PO, no pay"; that's one toggle on the Rules page.
- **Controller sign-off** above ₹25 lakh, even when everything matches.
- **Missing invoice number or date → return to vendor**, not "AP fixes it". Without them you can't post or dedupe, and the payment clock should start from a valid invoice.
- **Demo date is pinned** (`AP_TODAY=2026-11-12`) so the sample dates and discount windows stay meaningful. Remove it in real use.
- **Line-level quantity history isn't tracked.** Receipts are compared against this invoice's quantities. Cumulative control works on amounts.

## 6. Trade-offs I made deliberately

- **Rules over ML for decisions.** Less clever, far more trustworthy. Thresholds live in the UI, not the code.
- **Synchronous single-invoice pipeline**, with live streaming so a human can follow it. Batch throughput would come from a queue (see next steps).
- **SQLite and a JSON "ERP".** One database file holds every run, the ledger, the audit trail, the policy and the original documents. That keeps backup a single file and lets the dashboard read while a run writes (WAL mode). It's enough to make matching stateful and realistic. In production the reference data comes from an ERP connector (NetSuite, SAP, Coupa), and the store moves to a managed database with a mounted volume or point-in-time recovery.
- **Graceful degradation** over hard dependencies: Gemini, then cached extraction, then heuristic parser, then human. The system never auto-approves on a failed read.
- **Demo pacing** (≈450 ms minimum per stage) so viewers can follow. The extraction stage is real latency. Set `DEMO_PACING_MS=0` to switch pacing off.

## 7. What I'd build next

1. **Email intake.** An AP inbox webhook, split multi-invoice PDFs, attach to the run.
2. **ERP connectors.** Read POs and GRNs live, and sync the learned vendor list with the ERP's vendor records, and post approved invoices back.
3. **Credit notes and statements.** They're classified today but parked; next is to match them against open items.
4. **Multi-currency and VAT/GST**, including tax-ID format validation per country.
5. **Learning from reviewers.** Track override reasons per rule to tune tolerances, while keeping the rules deterministic.
6. **Eval set.** Fifty or more real-world invoices with labelled fields, to measure extraction accuracy per field and per model or effort level, and to catch regressions.
7. **Segregation of duties.** Role-based approvals: the person who releases a fraud hold can't be the one who changed the bank details.

## Vendors without a master file

There is no vendor master to load or maintain. Vendors are learned:

- **Payment history.** Anyone we've already paid is known and verified; the account we paid is their reference bank account.
- **First invoice.** A vendor we've never seen is added automatically from the invoice (name, GSTIN, email domain, bank, IFSC) with status *unverified*. That first invoice is held for Vendor Management, never auto-paid: auto-trusting a new payee is exactly how fake-vendor fraud works.
- **Verify once.** A person checks the GSTIN on the GST portal and confirms the bank (cancelled cheque or a call to a known contact), then clicks *Verify vendor*. Their waiting invoices are re-checked against every rule, and their details become the reference, so a later "we've changed banks" invoice is still caught.
- **Purchase orders** name the vendor by ID and GSTIN, so a learned vendor is matched to POs raised for them.

## Discounts and round-offs

- A discount row in the totals ("Less: Trade Discount @ 5%") is read as `discount_amount`; totals must reconcile as subtotal − discount + GST + shipping, the GST slab is checked on the discounted value, and a PO match notes that the lines match before the discount.
- Indian invoices round the payable to the nearest rupee. A total that differs from the computed amount by up to `round_off_max` (₹1) is accepted when a Round Off row explains it or the total is a whole rupee; anything larger is held as a real mismatch.
- Repeat-invoice amount matching uses the same ₹1 tolerance.
