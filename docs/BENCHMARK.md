# Extraction & decision benchmark

Run on 2026-11-13 with `gemini-3.1-flash-lite` (free tier) over the 8 sample documents, each through the full pipeline.
Ground truth is the exact data each document was rendered from; it is read only after the decision is made.

- **Fields read correctly:** 80/80 (100.0%)
- **Decisions and routing correct:** 8/8 (100.0%)
- **Median AI read time:** 3.5 s

| Field | Correct |
| --- | --- |
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
| 01_happy_northwind_NW-58213.pdf | 10/10 | APPROVE | APPROVE | — |
| 02_edge_split_billing_atlas_ATL-INV-1209.pdf | 10/10 | HOLD | HOLD | — |
| 03_edge_scanned_no_po_brightline_BLX-30871.pdf | 10/10 | APPROVE | APPROVE | — |
| 04_edge_disguised_duplicate_meridian_INV-2026-0417.pdf | 10/10 | REJECT | REJECT | — |
| 05_edge_bank_change_keystone_KFM-2026-1147.pdf | 10/10 | HOLD | HOLD | — |
| 06_variant_tolerance_pinnacle_PPP-7734.pdf | 10/10 | APPROVE | APPROVE | — |
| 07_variant_missing_fields_halcyon.pdf | 10/10 | REJECT | REJECT | — |
| 08_variant_phone_photo_northwind_NW-58377.jpg | 10/10 | APPROVE | APPROVE | — |

Caveat: 8 synthetic documents is a smoke-level benchmark, not a statistical claim. Real vendor invoices are messier;
the next step is a labelled set of real (redacted) invoices from the customer.
