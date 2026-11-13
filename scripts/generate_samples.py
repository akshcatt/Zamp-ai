"""Generate realistic test invoices for the demo inbox.

Each vendor uses a different layout and different labels on purpose ("Invoice #",
"Bill Number", "Invoice No."; "PO", "Your Ref", "Cust. PO") so the extraction step
is exercised the way it would be by a real vendor base. One invoice is rasterised
into an image-only PDF (rotated, noisy, no text layer) to simulate a scan.

Usage:  python scripts/generate_samples.py
"""
from __future__ import annotations

import io
import json
import random
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
BILL_TO = ["Lumen Home Goods, Inc.", "Accounts Payable", "410 Hudson Street", "New York, NY 10014"]


def money(x: float) -> str:
    return f"${x:,.2f}"


# --------------------------------------------------------------------------- layouts
def layout_modern(c: canvas.Canvas, s: dict) -> None:
    """Clean SaaS-style invoice: colour band, right-aligned meta block."""
    W, H = LETTER
    accent = colors.HexColor(s.get("accent", "#1F6FEB"))
    c.setFillColor(accent)
    c.rect(0, H - 1.1 * inch, W, 1.1 * inch, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(0.7 * inch, H - 0.62 * inch, s["vendor_name"])
    c.setFont("Helvetica", 9)
    c.drawString(0.7 * inch, H - 0.85 * inch, s["vendor_address"])
    c.setFont("Helvetica-Bold", 26)
    c.drawRightString(W - 0.7 * inch, H - 0.7 * inch, "INVOICE")

    c.setFillColor(colors.black)
    y = H - 1.6 * inch
    c.setFont("Helvetica-Bold", 9)
    c.drawString(0.7 * inch, y, "BILL TO")
    c.setFont("Helvetica", 10)
    for i, line in enumerate(BILL_TO):
        c.drawString(0.7 * inch, y - 14 * (i + 1), line)

    meta = s["meta"]
    c.setFont("Helvetica", 10)
    my = y
    for label, value in meta:
        c.setFont("Helvetica-Bold", 9)
        c.drawRightString(W - 2.3 * inch, my, label)
        c.setFont("Helvetica", 10)
        c.drawString(W - 2.2 * inch, my, value)
        my -= 15

    y = H - 3.1 * inch
    _table(c, s, y, header_fill=colors.HexColor("#EEF2F7"))


def layout_classic(c: canvas.Canvas, s: dict) -> None:
    """Letterhead-style services invoice with 'Bill Number' and 'Your Ref'."""
    W, H = LETTER
    c.setFont("Times-Bold", 22)
    c.drawString(0.8 * inch, H - 0.9 * inch, s["vendor_name"])
    c.setFont("Times-Roman", 10)
    c.drawString(0.8 * inch, H - 1.1 * inch, s["vendor_address"])
    c.drawString(0.8 * inch, H - 1.25 * inch, s.get("vendor_contact", ""))
    c.setLineWidth(1.2)
    c.line(0.8 * inch, H - 1.4 * inch, W - 0.8 * inch, H - 1.4 * inch)

    c.setFont("Times-Bold", 16)
    c.drawString(0.8 * inch, H - 1.85 * inch, s.get("doc_title", "INVOICE"))
    y = H - 2.2 * inch
    c.setFont("Times-Roman", 11)
    for label, value in s["meta"]:
        c.setFont("Times-Bold", 11)
        c.drawString(0.8 * inch, y, f"{label}:")
        c.setFont("Times-Roman", 11)
        c.drawString(2.2 * inch, y, value)
        y -= 15

    c.setFont("Times-Bold", 11)
    c.drawString(4.6 * inch, H - 2.2 * inch, "Invoice to:")
    c.setFont("Times-Roman", 11)
    for i, line in enumerate(BILL_TO):
        c.drawString(4.6 * inch, H - 2.2 * inch - 15 * (i + 1), line)

    _table(c, s, y - 25, header_fill=colors.HexColor("#F3EFE6"), font="Times-Roman", bold="Times-Bold")


def layout_industrial(c: canvas.Canvas, s: dict) -> None:
    """Dense distributor invoice with boxed fields and part numbers."""
    W, H = LETTER
    c.setStrokeColor(colors.black)
    c.setLineWidth(1.5)
    c.rect(0.5 * inch, H - 1.4 * inch, W - 1.0 * inch, 0.95 * inch)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(0.65 * inch, H - 0.8 * inch, s["vendor_name"].upper())
    c.setFont("Helvetica", 8)
    c.drawString(0.65 * inch, H - 1.0 * inch, s["vendor_address"])
    c.drawString(0.65 * inch, H - 1.15 * inch, s.get("vendor_contact", ""))
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(W - 0.65 * inch, H - 0.8 * inch, s.get("doc_title", "INVOICE"))

    # boxed meta grid
    y = H - 1.55 * inch
    cols = s["meta"]
    bw = (W - 1.0 * inch) / len(cols)
    for i, (label, value) in enumerate(cols):
        x = 0.5 * inch + i * bw
        c.setLineWidth(0.8)
        c.rect(x, y - 0.5 * inch, bw, 0.5 * inch)
        c.setFont("Helvetica", 7)
        c.drawString(x + 4, y - 11, label.upper())
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 4, y - 28, value)

    c.setFont("Helvetica-Bold", 8)
    c.drawString(0.6 * inch, y - 0.75 * inch, "SOLD TO")
    c.setFont("Helvetica", 9)
    for i, line in enumerate(BILL_TO):
        c.drawString(0.6 * inch, y - 0.75 * inch - 12 * (i + 1), line)

    _table(c, s, y - 1.95 * inch, header_fill=colors.HexColor("#DDDDDD"), font="Helvetica", bold="Helvetica-Bold", size=9)


def _table(c, s, y, header_fill, font="Helvetica", bold="Helvetica-Bold", size=10):
    W, _ = LETTER
    left, right = 0.7 * inch, W - 0.7 * inch
    x_qty, x_unit, x_amt = right - 2.6 * inch, right - 1.4 * inch, right
    c.setFillColor(header_fill)
    c.rect(left - 4, y - 5, right - left + 8, 18, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont(bold, size - 1)
    c.drawString(left, y, s.get("col_desc", "Description"))
    c.drawRightString(x_qty, y, s.get("col_qty", "Qty"))
    c.drawRightString(x_unit, y, s.get("col_unit", "Unit Price"))
    c.drawRightString(x_amt, y, s.get("col_amt", "Amount"))
    y -= 22
    c.setFont(font, size)
    for ln in s["lines"]:
        c.drawString(left, y, ln["description"])
        qty = ln["qty"]
        c.drawRightString(x_qty, y, f"{qty:,.0f}" if float(qty).is_integer() else f"{qty:,.2f}")
        c.drawRightString(x_unit, y, money(ln["unit_price"]))
        c.drawRightString(x_amt, y, money(round(ln["qty"] * ln["unit_price"], 2)))
        y -= 18
    c.setLineWidth(0.5)
    c.line(x_qty - 0.6 * inch, y + 6, right, y + 6)
    y -= 10
    for label, value, is_bold in s["totals"]:
        c.setFont(bold if is_bold else font, size + (1 if is_bold else 0))
        c.drawRightString(x_unit, y, label)
        c.drawRightString(x_amt, y, value)
        y -= 17

    y -= 18
    c.setFont(font, size - 1)
    for note in s.get("notes", []):
        if note.startswith("!!"):
            c.setFont(bold, size)
            c.setFillColor(colors.HexColor("#B42318"))
            c.drawString(left, y, note[2:])
            c.setFillColor(colors.black)
            c.setFont(font, size - 1)
        else:
            c.drawString(left, y, note)
        y -= 14

    if s.get("stamp"):
        c.saveState()
        c.translate(W / 2, 3.2 * inch)
        c.rotate(18)
        c.setStrokeColor(colors.HexColor("#C0392B"))
        c.setFillColor(colors.HexColor("#C0392B"))
        c.setLineWidth(2.5)
        c.roundRect(-1.6 * inch, -0.35 * inch, 3.2 * inch, 0.7 * inch, 8)
        c.setFont("Helvetica-Bold", 22)
        c.drawCentredString(0, -0.1 * inch, s["stamp"])
        c.restoreState()

    c.setFont(font, 7)
    c.setFillColor(colors.grey)
    c.drawString(left, 0.5 * inch, s.get("footer", "Thank you for your business."))
    c.setFillColor(colors.black)


LAYOUTS = {"modern": layout_modern, "classic": layout_classic, "industrial": layout_industrial}


def render(spec: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    c.setTitle(spec.get("pdf_title", "Invoice"))
    c.setAuthor(spec["vendor_name"])
    LAYOUTS[spec["layout"]](c, spec)
    c.showPage()
    c.save()
    return buf.getvalue()


def rasterise_as_scan(pdf_bytes: bytes, seed: int = 7) -> bytes:
    """Turn a digital PDF into an image-only PDF that looks like a phone/flatbed scan."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageFilter, ImageEnhance

    rnd = random.Random(seed)
    pdf = pdfium.PdfDocument(pdf_bytes)
    page = pdf[0]
    img = page.render(scale=150 / 72).to_pil().convert("L")
    img = img.rotate(1.3, resample=Image.BICUBIC, expand=True, fillcolor=245)
    # paper tone + noise speckle
    px = img.load()
    w, h = img.size
    for _ in range(int(w * h * 0.004)):
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = rnd.choice([90, 140, 200])
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img = ImageEnhance.Contrast(img).enhance(0.85)
    img = ImageEnhance.Brightness(img).enhance(0.97)
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PDF", resolution=150)
    return out.getvalue()


def photograph(pdf_bytes: bytes, seed: int = 11) -> bytes:
    """Make it look like a phone photo of a printed invoice: paper on a desk, tilted, uneven light, JPEG."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

    rnd = random.Random(seed)
    page = pdfium.PdfDocument(pdf_bytes)[0].render(scale=170 / 72).to_pil().convert("RGB")
    pw, ph = page.size
    W, H = int(pw * 1.22), int(ph * 1.16)
    desk = Image.new("RGB", (W, H), (92, 74, 58))  # wooden desk tone
    dpx = desk.load()
    for _ in range(int(W * H * 0.01)):  # grain
        x, y = rnd.randrange(W), rnd.randrange(H)
        r, g, b = dpx[x, y]
        k = rnd.randint(-14, 14)
        dpx[x, y] = (r + k, g + k, b + k)
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).rectangle([int(W * .1) + 14, int(H * .07) + 18, int(W * .1) + pw + 14, int(H * .07) + ph + 18], fill=150)
    desk.paste((40, 32, 25), mask=shadow.filter(ImageFilter.GaussianBlur(18)))
    paper = page.rotate(-3.2, resample=Image.BICUBIC, expand=True, fillcolor=(0, 0, 0))
    mask = Image.new("L", page.size, 255).rotate(-3.2, expand=True, fillcolor=0)
    desk.paste(paper, (int(W * .1) - 20, int(H * .07) - 30), mask)
    # uneven lighting: soft vignette, light falling off toward the edges and one corner
    vig = Image.radial_gradient("L").resize((int(W * 1.5), int(H * 1.5))).crop((int(W * .15), int(H * .1), int(W * 1.15), int(H * 1.1)))
    vig = vig.filter(ImageFilter.GaussianBlur(40))
    desk = Image.composite(ImageEnhance.Brightness(desk).enhance(0.72), ImageEnhance.Brightness(desk).enhance(1.06), vig)
    r, g, b = desk.split()  # warm indoor white balance
    desk = Image.merge("RGB", (r.point(lambda v: min(255, int(v * 1.04))), g, b.point(lambda v: int(v * 0.92))))
    desk = desk.filter(ImageFilter.GaussianBlur(0.8))
    out = io.BytesIO()
    desk.save(out, format="JPEG", quality=80)
    return out.getvalue()


# --------------------------------------------------------------------------- specs
def totals(sub, tax=None, rate=None, ship=None, label_total="Total Due"):
    rows = [("Subtotal", money(sub), False)]
    if ship:
        rows.append(("Shipping", money(ship), False))
    if tax is not None:
        rows.append((f"Sales Tax ({rate})" if rate else "Tax", money(tax), False))
    grand = round(sub + (ship or 0) + (tax or 0), 2)
    rows.append((label_total, money(grand), True))
    return rows


SAMPLES = [
    {
        "file": "01_happy_northwind_NW-58213.pdf",
        "id": "happy_path",
        "kind": "Happy path",
        "title": "Clean invoice, exact 3-way match",
        "what_to_expect": "Approved. PO match is exact, goods received, and the 2/10 early-payment discount is flagged.",
        "layout": "modern", "accent": "#0E7C66",
        "vendor_name": "Northwind Office Supplies Inc.",
        "vendor_address": "88 Commerce Way, Secaucus, NJ 07094  ·  ar@northwindsupplies.com  ·  EIN 94-3127756",
        "meta": [("Invoice #", "NW-58213"), ("Invoice Date", "Nov 09, 2026"), ("PO Number", "PO-2026-0311"),
                 ("Terms", "2/10 Net 30"), ("Due Date", "Dec 09, 2026")],
        "lines": [
            {"description": "A4 copy paper 80gsm - case of 10 reams", "qty": 40, "unit_price": 38.50},
            {"description": "HP 58A Black Toner Cartridge", "qty": 12, "unit_price": 96.00},
            {"description": "Ergonomic Mesh Office Chair", "qty": 6, "unit_price": 289.00},
        ],
        "totals": totals(4426.00, 392.81, "8.875%"),
        "notes": ["Remit to: JPMorgan Chase Bank, N.A.  Routing 021000021  Account 000123454417",
                  "2% discount if paid within 10 days of invoice date."],
    },
    {
        "file": "02_edge_split_billing_atlas_ATL-INV-1209.pdf",
        "id": "edge_split_po_overrun",
        "kind": "Edge case 1",
        "title": "Split billing pushes PO over its cap",
        "what_to_expect": "On hold for the buyer. This invoice alone looks fine, but combined with the $30,000 already billed it overruns the 320-hour PO by $3,750 (7.8%). The process suggests a partial approval of $18,000.",
        "layout": "classic",
        "vendor_name": "Atlas Cloud Services LLC",
        "vendor_address": "500 Howard Street, Suite 900, San Francisco, CA 94105",
        "vendor_contact": "billing@atlascloud.io  ·  Tax ID 47-2210983",
        "meta": [("Bill Number", "ATL-INV-1209"), ("Date", "31 October 2026"), ("Your Ref", "PO-2026-0287"),
                 ("Period", "1 Oct - 31 Oct 2026"), ("Terms", "Net 30")],
        "col_desc": "Service", "col_qty": "Hours", "col_unit": "Rate",
        "lines": [
            {"description": "Data platform implementation - consulting hours (October)", "qty": 145, "unit_price": 150.00},
        ],
        "totals": totals(21750.00, label_total="Amount Due"),
        "notes": ["Milestone 2 of engagement. Detailed timesheets available on request.",
                  "Remit to: Silicon Valley Bank  Routing 121140399  Account 3300918842"],
    },
    {
        "file": "03_edge_scanned_no_po_brightline_BLX-30871.pdf",
        "id": "edge_scanned_inferred_po",
        "kind": "Edge case 2",
        "title": "Scanned image, no PO reference",
        "what_to_expect": "The PDF has no text layer, so Gemini reads the page image. No PO is quoted. The process scores the vendor's 2 open POs on lines, amounts and remaining balance, infers PO-2026-0342 with high confidence, confirms delivery, and approves with an inferred-PO note.",
        "layout": "industrial", "scan": True,
        "vendor_name": "Brightline Logistics Ltd.",
        "vendor_address": "2150 S Canal St, Chicago, IL 60616",
        "vendor_contact": "Tel (312) 555-0122  ·  billing@brightlinelogistics.com  ·  FEIN 36-4419270",
        "doc_title": "FREIGHT INVOICE",
        "meta": [("Invoice No.", "BLX-30871"), ("Invoice Date", "11/08/2026"), ("Account", "LUMEN-NY"), ("Terms", "Net 45")],
        "lines": [
            {"description": "LTL Freight CHI -> CMH (BOL 88213, 88219, 88227)", "qty": 3, "unit_price": 1240.00},
            {"description": "Fuel Surcharge 8%", "qty": 1, "unit_price": 297.60},
            {"description": "Liftgate Delivery", "qty": 3, "unit_price": 85.00},
        ],
        "totals": totals(4272.60, label_total="TOTAL USD"),
        "notes": ["Delivered 11/07/2026 - signed POD on file.",
                  "Remit: BMO Bank N.A. ABA 071000288 Acct 2209914460"],
    },
    {
        "file": "04_edge_disguised_duplicate_meridian_INV-2026-0417.pdf",
        "id": "edge_disguised_duplicate",
        "kind": "Edge case 3",
        "title": "Re-sent duplicate with a reformatted number",
        "what_to_expect": "Rejected as a duplicate. It's a different file (new hash) and the number is written differently ('INV-2026-0417' vs 'INV 2026/0417'), but the normalised number, vendor and amount match an invoice already paid on ACH-77812.",
        "layout": "industrial",
        "vendor_name": "Meridian Industrial Parts Co.",
        "vendor_address": "1200 Route 46 West, Parsippany, NJ 07054",
        "vendor_contact": "ar@meridianparts.com  ·  (973) 555-0190  ·  EIN 22-3098817",
        "meta": [("Invoice No.", "INV-2026-0417"), ("Date", "08-OCT-2026"), ("Cust. PO", "PO-2026-0275"), ("Terms", "NET 30")],
        "col_desc": "Part / Description",
        "lines": [
            {"description": "BRG-6204  Ball Bearing 6204-2RS", "qty": 500, "unit_price": 4.20},
            {"description": "HOS-HYD-36  Hydraulic Hose Assy 1/2in x 36in", "qty": 40, "unit_price": 62.50},
            {"description": "BLT-B48  V-Belt B48", "qty": 120, "unit_price": 11.75},
        ],
        "totals": totals(6010.00, 533.39, "8.875%", label_total="BALANCE DUE"),
        "stamp": "SECOND NOTICE",
        "notes": ["Our records show this invoice as unpaid. Please remit promptly.",
                  "Remit to: TD Bank, N.A.  ABA 031201360  Acct 4410287736"],
    },
    {
        "file": "05_edge_bank_change_keystone_KFM-2026-1147.pdf",
        "id": "edge_bank_change_fraud",
        "kind": "Edge case 4",
        "title": "Perfect match, but the bank account changed",
        "what_to_expect": "On hold for Treasury and fraud review. Every number matches the PO, but the remit-to bank account and the sender's email domain don't match the vendor master. This is the classic business email compromise pattern. The process never uses contact details printed on the invoice, and tells AP to call back on the number on file.",
        "layout": "classic",
        "vendor_name": "Keystone Facilities Management Inc.",
        "vendor_address": "77-15 Queens Blvd, Elmhurst, NY 11373",
        "vendor_contact": "accounts@keystone-fm-billing.com  ·  (718) 555-0163",
        "meta": [("Bill Number", "KFM-2026-1147"), ("Date", "3 November 2026"), ("Your Ref", "PO-2026-0330"),
                 ("Terms", "Net 30")],
        "lines": [
            {"description": "Janitorial services - November 2026", "qty": 1, "unit_price": 7850.00},
            {"description": "Exterior window cleaning (quarterly)", "qty": 1, "unit_price": 1200.00},
        ],
        "totals": totals(9050.00, label_total="Amount Due"),
        "notes": ["!!IMPORTANT: We have changed banks. Effective immediately please remit ALL payments to:",
                  "Metro Community Bank   Routing 026013673   Account 8841902233",
                  "Please update your records. Payments to the old account will be returned."],
    },
    {
        "file": "06_variant_tolerance_pinnacle_PPP-7734.pdf",
        "id": "variant_within_tolerance",
        "kind": "Extra input",
        "title": "Close but not exact: unbudgeted freight",
        "what_to_expect": "Approved with a note. The vendor added a $45 freight line that isn't on the PO. That's +0.78%, inside the 2% / $500 tolerance, so it passes without a human touch and the variance is logged.",
        "layout": "modern", "accent": "#7A3EF0",
        "vendor_name": "Pinnacle Print & Packaging LLC",
        "vendor_address": "41 Industrial Pkwy, Hicksville, NY 11801  ·  invoices@pinnacleprint.com",
        "meta": [("Invoice #", "PPP-7734"), ("Invoice Date", "Nov 06, 2026"), ("PO Number", "PO-2026-0356"),
                 ("Terms", "Net 30")],
        "lines": [
            {"description": "Custom corrugated box 12x10x8, 2-colour", "qty": 2000, "unit_price": 1.85},
            {"description": "Branded tissue paper 20x30 (ream)", "qty": 50, "unit_price": 42.00},
            {"description": "Freight - expedited", "qty": 1, "unit_price": 45.00},
        ],
        "totals": totals(5845.00, 518.74, "8.875%"),
        "notes": ["Remit to: Citibank, N.A.  Routing 021000089  Account 6672019934"],
    },
    {
        "file": "07_variant_missing_fields_halcyon.pdf",
        "id": "variant_missing_fields",
        "kind": "Extra input",
        "title": "No invoice number, no date",
        "what_to_expect": "Returned to the vendor. Without an invoice number the invoice can't be posted or checked for duplicates. The process drafts the email to the vendor explaining exactly what's missing.",
        "layout": "modern", "accent": "#B54708",
        "vendor_name": "Halcyon Catering Co.",
        "vendor_address": "19 W 24th St, New York, NY 10010  ·  hello@halcyoncatering.com",
        "meta": [("Event", "Lumen Q4 All-Hands"), ("Guests", "120"), ("PO Number", "PO-2026-0361")],
        "lines": [
            {"description": "Event catering - Q4 all-hands, 120 guests", "qty": 1, "unit_price": 3450.00},
        ],
        "totals": totals(3450.00, 306.19, "8.875%"),
        "notes": ["Thank you for choosing Halcyon!", "Remit to: Bank of America  Routing 026009593  Account 4830021175"],
    },
    {
        "file": "08_variant_phone_photo_northwind_NW-58377.jpg",
        "id": "variant_phone_photo",
        "kind": "Extra input",
        "title": "Phone photo of a paper invoice",
        "what_to_expect": "A JPEG photo, not a PDF: tilted, on a desk, uneven light. Intake detects the file type from its bytes, auto-rotates the photo and converts it to a PDF, then Gemini reads it like a scan. It's a 3-way match on a second Northwind PO, so it's approved.",
        "layout": "modern", "accent": "#0E7C66", "photo": True,
        "vendor_name": "Northwind Office Supplies Inc.",
        "vendor_address": "88 Commerce Way, Secaucus, NJ 07094  ·  ar@northwindsupplies.com  ·  EIN 94-3127756",
        "meta": [("Invoice #", "NW-58377"), ("Invoice Date", "Nov 10, 2026"), ("PO Number", "PO-2026-0368"),
                 ("Terms", "Net 30"), ("Due Date", "Dec 10, 2026")],
        "lines": [
            {"description": "Whole-bean coffee 1 kg", "qty": 10, "unit_price": 24.00},
            {"description": "Paper towels - case of 12 rolls", "qty": 8, "unit_price": 31.50},
            {"description": "Disinfectant wipes 80 ct", "qty": 12, "unit_price": 7.25},
        ],
        "totals": totals(579.00, 51.39, "8.875%"),
        "notes": ["Remit to: JPMorgan Chase Bank, N.A.  Routing 021000021  Account 000123454417"],
    },
    {
        "file": "09_variant_quote_pinnacle_Q-2291.pdf",
        "id": "variant_not_an_invoice",
        "kind": "Extra input",
        "doc_type": "quote",
        "title": "Not an invoice: a supplier quote",
        "what_to_expect": "Filed, not paid. A vendor sent a price quote to the AP inbox. It has prices and a total, but it isn't a bill, so there's nothing to pay and nothing wrong with the sender. It's filed for reference and kept out of the rejection numbers. If it ever turns out to be a real invoice, a reviewer can correct it and re-check.",
        "layout": "industrial", "doc_title": "QUOTATION",
        "vendor_name": "Pinnacle Print & Packaging LLC",
        "vendor_address": "41 Industrial Pkwy, Hicksville, NY 11801",
        "vendor_contact": "sales@pinnacleprint.com  ·  (516) 555-0148",
        "meta": [("Quote No.", "Q-2291"), ("Quote Date", "Nov 11, 2026"), ("Valid Until", "Dec 11, 2026"), ("Terms", "Net 30")],
        "lines": [
            {"description": "Custom mailer box 9x6x3, 1-colour", "qty": 5000, "unit_price": 0.92},
            {"description": "Die / plate setup (one-time)", "qty": 1, "unit_price": 350.00},
        ],
        "totals": totals(4950.00, label_total="Quote Total"),
        "notes": ["This is a quotation, not a request for payment. Prices are valid for 30 days.",
                  "To accept, please issue a purchase order referencing Q-2291."],
    },
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    manifest = []
    for spec in SAMPLES:
        spec = dict(spec)
        pdf = render(spec)
        if spec.get("scan"):
            pdf = rasterise_as_scan(pdf)
        if spec.get("photo"):
            pdf = photograph(pdf)
        (OUT / spec["file"]).write_bytes(pdf)
        manifest.append({
            "file": spec["file"], "id": spec["id"], "kind": spec["kind"],
            "title": spec["title"],
            "vendor": spec["vendor_name"], "what_to_expect": spec["what_to_expect"],
            "scanned": bool(spec.get("scan")), "photo": bool(spec.get("photo")),
        })
        print("wrote", spec["file"], f"{len(pdf)/1024:.0f} KB")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
