# Extraction & decision benchmark

Run on 2026-11-13 with `gemini-3.1-flash-lite` (free tier) over the 9 sample documents, each through the full pipeline.
Ground truth is the exact data each document was rendered from; it is read only after the decision is made.

- **Fields read correctly:** 89/89 (100.0%)
- **Decisions and routing correct:** 9/9 (100.0%)
- **Median AI read time:** 4.5 s

| Field | Correct |
| --- | --- |
| Document type | 9/9 |
| Vendor name | 8/8 |
| Invoice number | 8/8 |
| Invoice date | 8/8 |
| PO reference | 8/8 |
| Subtotal | 8/8 |
| Tax | 8/8 |
| Shipping | 8/8 |
| Total | 8/8 |
| Line items (count & amounts) | 8/8 |
| Remit-to account | 8/8 |

| Sample | Fields | Decision | Expected | Misses |
| --- | --- | --- | --- | --- |
| 01_happy_sahyadri_SOS-58213.pdf | 11/11 | APPROVE | APPROVE | — |
| 02_edge_split_billing_kaveri_KCS-INV-1209.pdf | 11/11 | HOLD | HOLD | — |
| 03_edge_scanned_no_po_ganga_GFC-30871.pdf | 11/11 | APPROVE | APPROVE | — |
| 04_edge_disguised_duplicate_meenakshi_INV-2026-0417.pdf | 11/11 | REJECT | REJECT | — |
| 05_edge_bank_change_kesari_KFS-2026-1147.pdf | 11/11 | HOLD | HOLD | — |
| 06_variant_tolerance_pragati_PPP-7734.pdf | 11/11 | APPROVE | APPROVE | — |
| 07_variant_missing_fields_annapurna.pdf | 11/11 | REJECT | REJECT | — |
| 08_variant_phone_photo_sahyadri_SOS-58377.jpg | 11/11 | APPROVE | APPROVE | — |
| 09_variant_quote_pragati_Q-2291.pdf | 1/1 | NOT_INVOICE | NOT_INVOICE | — |

Caveat: 8 synthetic documents is a smoke-level benchmark, not a statistical claim. Real vendor invoices are messier;
the next step is a labelled set of real (redacted) invoices from the customer.
