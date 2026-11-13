# Extraction & decision benchmark

Run on 2026-11-13 with `gemini-3.1-flash-lite` (free tier) over the 12 sample documents, each through the full pipeline.
Ground truth is the exact data each document was rendered from; it is read only after the decision is made.

- **Fields read correctly:** 166/166 (100.0%)
- **Decisions and routing correct:** 12/12 (100.0%)
- **Median AI read time:** 3.4 s

| Field | Correct |
| --- | --- |
| Document type | 12/12 |
| Bill type | 11/11 |
| Already paid? | 11/11 |
| Vendor name | 11/11 |
| Invoice number | 11/11 |
| Invoice date | 11/11 |
| PO reference | 11/11 |
| Subtotal | 11/11 |
| Tax | 11/11 |
| Shipping | 11/11 |
| Discount | 11/11 |
| Round off | 11/11 |
| Total | 11/11 |
| Line items (count & amounts) | 11/11 |
| Remit-to account | 11/11 |

| Sample | Fields | Decision | Expected | Misses |
| --- | --- | --- | --- | --- |
| 01_happy_sahyadri_SOS-58213.pdf | 15/15 | APPROVE | APPROVE | — |
| 02_edge_split_billing_kaveri_KCS-INV-1209.pdf | 15/15 | HOLD | HOLD | — |
| 03_edge_scanned_no_po_ganga_GFC-30871.pdf | 15/15 | APPROVE | APPROVE | — |
| 04_edge_disguised_duplicate_meenakshi_INV-2026-0417.pdf | 15/15 | REJECT | REJECT | — |
| 05_edge_bank_change_kesari_KFS-2026-1147.pdf | 15/15 | HOLD | HOLD | — |
| 06_variant_tolerance_pragati_PPP-7734.pdf | 15/15 | APPROVE | APPROVE | — |
| 07_variant_missing_fields_annapurna.pdf | 15/15 | APPROVE | APPROVE | — |
| 08_variant_phone_photo_sahyadri_SOS-58377.jpg | 15/15 | APPROVE | APPROVE | — |
| 09_variant_quote_pragati_Q-2291.pdf | 1/1 | NOT_INVOICE | NOT_INVOICE | — |
| 10_bill_restaurant_truffles_TCK-0457.pdf | 15/15 | APPROVE | APPROVE | — |
| 11_bill_electricity_bescom_Nov-2026.pdf | 15/15 | APPROVE | APPROVE | — |
| 12_bill_handwritten_cash_memo_lakshmi_412.jpg | 15/15 | APPROVE | APPROVE | — |

Caveat: a dozen synthetic documents is a smoke-level benchmark, not a statistical claim. Real vendor invoices are messier;
the next step is a labelled set of real (redacted) invoices from the customer.
