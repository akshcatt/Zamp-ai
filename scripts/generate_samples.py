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
BILL_TO = ["Lumen Home Goods Pvt. Ltd.", "Accounts Payable, 4th Floor, Prestige Tech Park", "Outer Ring Road, Bengaluru 560103",
           "GSTIN 29AAECL4821K1Z3"]


def inr(x: float) -> str:
    """Indian digit grouping: 1234567.5 -> '12,34,567.50'."""
    neg, x = x < 0, abs(round(x, 2))
    whole, frac = f"{x:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    while len(head) > 2:
        tail = head[-2:] + "," + tail
        head = head[:-2]
    out = (head + "," + tail if head else tail) + "." + frac
    return ("-" if neg else "") + out


def money(x: float) -> str:
    return "Rs. " + inr(x)


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
    c.drawRightString(W - 0.7 * inch, H - 0.7 * inch, s.get("doc_title", "TAX INVOICE"))

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
    c.drawString(0.8 * inch, H - 1.85 * inch, s.get("doc_title", "TAX INVOICE"))
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
    c.drawRightString(W - 0.65 * inch, H - 0.8 * inch, s.get("doc_title", "TAX INVOICE"))

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
    c.drawRightString(x_unit, y, s.get("col_unit", "Rate (Rs.)"))
    c.drawRightString(x_amt, y, s.get("col_amt", "Amount (Rs.)"))
    y -= 22
    c.setFont(font, size)
    for ln in s["lines"]:
        c.drawString(left, y, ln["description"])
        qty = ln["qty"]
        c.drawRightString(x_qty, y, f"{qty:,.0f}" if float(qty).is_integer() else f"{qty:,.2f}")
        c.drawRightString(x_unit, y, inr(ln["unit_price"]))
        c.drawRightString(x_amt, y, inr(round(ln["qty"] * ln["unit_price"], 2)))
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
    c.drawString(left, 0.5 * inch, s.get("footer", "This is a computer-generated invoice. Subject to Bengaluru jurisdiction."))
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
def totals(sub, gst=None, rate=18, ship=None, label_total="Total (INR)", note=None):
    """GST rows the way Indian invoices print them: CGST + SGST within a state, IGST across states."""
    rows = [("Taxable Value" if gst else "Subtotal", money(sub), False)]
    if ship:
        rows.append(("Shipping", money(ship), False))
    tax = 0.0
    if gst == "intra":
        half = round((sub + (ship or 0)) * rate / 200, 2)
        rows += [(f"CGST @ {rate / 2:g}%", money(half), False), (f"SGST @ {rate / 2:g}%", money(half), False)]
        tax = 2 * half
    elif gst == "inter":
        tax = round((sub + (ship or 0)) * rate / 100, 2)
        rows.append((f"IGST @ {rate:g}%", money(tax), False))
    if note:
        rows.append((note, "-", False))
    rows.append((label_total, money(round(sub + (ship or 0) + tax, 2)), True))
    return rows


SAHYADRI = "No. 14, 2nd Cross, Peenya Industrial Area, Bengaluru 560058  ·  accounts@sahyadrisupplies.in  ·  GSTIN 29AAKCS3127M1Z6"
SAMPLES = [
    {
        "file": "01_happy_sahyadri_SOS-58213.pdf",
        "id": "happy_path",
        "kind": "Happy path",
        "title": "Clean invoice, exact 3-way match",
        "what_to_expect": "Approved. PO match is exact, goods received (GRN on file), GST is CGST + SGST at 18% as expected for a Karnataka vendor, and the 2/10 early-payment discount is flagged.",
        "layout": "modern", "accent": "#0E7C66",
        "vendor_name": "Sahyadri Office Supplies Pvt. Ltd.",
        "vendor_address": SAHYADRI,
        "meta": [("Invoice No.", "SOS/26-27/58213"), ("Invoice Date", "09 Nov 2026"), ("PO Number", "PO-2026-0311"),
                 ("Terms", "2/10 Net 30"), ("Due Date", "09 Dec 2026")],
        "lines": [
            {"description": "A4 copier paper 75gsm - box of 10 reams", "qty": 40, "unit_price": 3080.00},
            {"description": "HP 58A Black Toner Cartridge", "qty": 12, "unit_price": 7680.00},
            {"description": "Ergonomic Mesh Office Chair", "qty": 6, "unit_price": 23120.00},
        ],
        "totals": totals(354080.00, "intra"),
        "notes": ["Bank: HDFC Bank Ltd.  A/c No. 50200012345417  IFSC HDFC0000523",
                  "2% discount if paid within 10 days of invoice date."],
    },
    {
        "file": "02_edge_split_billing_kaveri_KCS-INV-1209.pdf",
        "id": "edge_split_po_overrun",
        "kind": "Edge case 1",
        "title": "Split billing pushes PO over its cap",
        "what_to_expect": "On hold for the buyer. This invoice alone looks fine, but combined with the ₹24,00,000 already billed it overruns the 320-hour PO by ₹3,00,000 (7.8%). The process suggests a partial approval of ₹14,40,000.",
        "layout": "classic",
        "vendor_name": "Kaveri Cloud Solutions LLP",
        "vendor_address": "Office 902, Panchshil Tech Park, Yerwada, Pune, Maharashtra 411006",
        "vendor_contact": "billing@kavericloud.in  ·  GSTIN 27AAPFK2210Q1Z9  ·  SAC 998313",
        "meta": [("Bill Number", "KCS-INV-1209"), ("Date", "31 October 2026"), ("Your Ref", "PO-2026-0287"),
                 ("Period", "1 Oct - 31 Oct 2026"), ("Terms", "Net 30")],
        "col_desc": "Service", "col_qty": "Hours", "col_unit": "Rate (Rs.)",
        "lines": [
            {"description": "Data platform implementation - consulting hours (October)", "qty": 145, "unit_price": 12000.00},
        ],
        "totals": totals(1740000.00, "inter", label_total="Amount Payable"),
        "notes": ["Milestone 2 of engagement. Detailed timesheets available on request.",
                  "Bank: ICICI Bank Ltd.  A/c No. 182905009188  IFSC ICIC0001829"],
    },
    {
        "file": "03_edge_scanned_no_po_ganga_GFC-30871.pdf",
        "id": "edge_scanned_inferred_po",
        "kind": "Edge case 2",
        "title": "Scanned image, no PO reference",
        "what_to_expect": "The PDF has no text layer, so Gemini reads the page image. No PO is quoted. The process scores the vendor's 2 open POs on lines, amounts and remaining balance, infers PO-2026-0342 with high confidence, confirms delivery against the lorry receipts, and approves with an inferred-PO note. GST is under reverse charge (paid by us), so none is charged on the invoice.",
        "layout": "industrial", "scan": True,
        "vendor_name": "Ganga Freight Carriers Pvt. Ltd.",
        "vendor_address": "Gala 7, Rahnal Village, Bhiwandi, Thane, Maharashtra 421302",
        "vendor_contact": "Tel +91 22 2851 0122  ·  billing@gangafreight.co.in  ·  GSTIN 27AAGCG4419R1Z2",
        "doc_title": "FREIGHT INVOICE",
        "meta": [("Invoice No.", "GFC-30871"), ("Invoice Date", "08/11/2026"), ("Account", "LUMEN-BLR"), ("Terms", "Net 45")],
        "lines": [
            {"description": "PTL Freight BHW -> PNQ (LR 88213, 88219, 88227)", "qty": 3, "unit_price": 99200.00},
            {"description": "Fuel Surcharge 8%", "qty": 1, "unit_price": 23808.00},
            {"description": "Unloading / Hamali Charges", "qty": 3, "unit_price": 6800.00},
        ],
        "totals": totals(341808.00, note="GST payable by recipient under RCM", label_total="TOTAL INR"),
        "notes": ["Delivered 07/11/2026 - signed POD on file.",
                  "Bank: Axis Bank Ltd. A/c No. 912020044601460 IFSC UTIB0000412"],
    },
    {
        "file": "04_edge_disguised_duplicate_meenakshi_INV-2026-0417.pdf",
        "id": "edge_disguised_duplicate",
        "kind": "Edge case 3",
        "title": "Re-sent duplicate with a reformatted number",
        "what_to_expect": "Rejected as a duplicate. It's a different file (new hash) and the number is written differently ('INV-2026-0417' vs 'INV 2026/0417'), but the normalised number, vendor and amount match an invoice already paid on UTR-77812.",
        "layout": "industrial",
        "vendor_name": "Meenakshi Industrial Spares Co.",
        "vendor_address": "112, SIDCO Industrial Estate, Ambattur, Chennai, Tamil Nadu 600098",
        "vendor_contact": "ar@meenakshispares.com  ·  +91 44 2625 0190  ·  GSTIN 33AAHFM3098P1Z4",
        "meta": [("Invoice No.", "INV-2026-0417"), ("Date", "08-OCT-2026"), ("Cust. PO", "PO-2026-0275"), ("Terms", "NET 30")],
        "col_desc": "Part / Description",
        "lines": [
            {"description": "BRG-6204  Ball Bearing 6204-2RS", "qty": 500, "unit_price": 336.00},
            {"description": "HOS-HYD-36  Hydraulic Hose Assy 1/2in x 36in", "qty": 40, "unit_price": 5000.00},
            {"description": "BLT-B48  V-Belt B48", "qty": 120, "unit_price": 940.00},
        ],
        "totals": totals(480800.00, "inter", label_total="BALANCE DUE"),
        "stamp": "SECOND NOTICE",
        "notes": ["Our records show this invoice as unpaid. Kindly remit at the earliest.",
                  "Bank: State Bank of India  A/c No. 38410287736  IFSC SBIN0000800"],
    },
    {
        "file": "05_edge_bank_change_kesari_KFS-2026-1147.pdf",
        "id": "edge_bank_change_fraud",
        "kind": "Edge case 4",
        "title": "Perfect match, but the bank account changed",
        "what_to_expect": "On hold for Treasury and fraud review. Every number matches the PO and the GSTIN is right, but the remit-to bank account and the sender's email domain don't match the vendor master. This is the classic business email compromise pattern. The process never uses contact details printed on the invoice, and tells AP to call back on the number on file.",
        "layout": "classic",
        "vendor_name": "Kesari Facility Services Pvt. Ltd.",
        "vendor_address": "No. 221, 5th Main, HSR Layout Sector 7, Bengaluru, Karnataka 560102",
        "vendor_contact": "accounts@kesari-facility-billing.com  ·  +91 80 4718 0163  ·  GSTIN 29AAJCK5582N1Z8",
        "meta": [("Bill Number", "KFS-2026-1147"), ("Date", "3 November 2026"), ("Your Ref", "PO-2026-0330"),
                 ("Terms", "Net 30")],
        "lines": [
            {"description": "Housekeeping services - November 2026", "qty": 1, "unit_price": 628000.00},
            {"description": "Facade glass cleaning (quarterly)", "qty": 1, "unit_price": 96000.00},
        ],
        "totals": totals(724000.00, "intra", label_total="Amount Payable"),
        "notes": ["!!IMPORTANT: We have changed banks. Effective immediately please remit ALL payments to:",
                  "Jana Small Finance Bank   A/c No. 4127019902233   IFSC JSFB0004127",
                  "Please update your records. Payments to the old account will be returned."],
    },
    {
        "file": "06_variant_tolerance_pragati_PPP-7734.pdf",
        "id": "variant_within_tolerance",
        "kind": "Extra input",
        "title": "Close but not exact: unbudgeted freight",
        "what_to_expect": "Approved with a note. The vendor added a ₹3,600 freight line that isn't on the PO. That's +0.78%, inside the 2% / ₹40,000 tolerance, so it passes without a human touch and the variance is logged.",
        "layout": "modern", "accent": "#7A3EF0",
        "vendor_name": "Pragati Print & Packaging",
        "vendor_address": "Plot 38, KIADB Industrial Area, Bommasandra, Bengaluru 560099  ·  invoices@pragatiprint.in  ·  GSTIN 29ABBFP3374E1Z1",
        "meta": [("Invoice No.", "PPP-7734"), ("Invoice Date", "06.11.2026"), ("PO Number", "PO-2026-0356"),
                 ("Terms", "Net 30")],
        "lines": [
            {"description": "Custom corrugated box 12x10x8, 2-colour", "qty": 2000, "unit_price": 148.00},
            {"description": "Branded tissue paper 20x30 (ream)", "qty": 50, "unit_price": 3360.00},
            {"description": "Freight - express", "qty": 1, "unit_price": 3600.00},
        ],
        "totals": totals(467600.00, "intra"),
        "notes": ["Bank: Canara Bank  A/c No. 2611201019934  IFSC CNRB0002611"],
    },
    {
        "file": "07_variant_missing_fields_annapurna.pdf",
        "id": "variant_missing_fields",
        "kind": "Extra input",
        "title": "No invoice number, no date",
        "what_to_expect": "Returned to the vendor. Without an invoice number the invoice can't be posted, checked for duplicates, or claimed for GST input credit. The process drafts the email to the vendor explaining exactly what's missing.",
        "layout": "modern", "accent": "#B54708",
        "vendor_name": "Annapurna Caterers",
        "vendor_address": "45, 100 Feet Road, Indiranagar, Bengaluru 560038  ·  hello@annapurnacaterers.in  ·  GSTIN 29AQRPA4471H1Z5",
        "meta": [("Event", "Lumen Q4 Town Hall"), ("Guests", "120"), ("PO Number", "PO-2026-0361")],
        "lines": [
            {"description": "Event catering - Q4 town hall, 120 guests", "qty": 1, "unit_price": 276000.00},
        ],
        "totals": totals(276000.00, "intra", rate=5),
        "notes": ["Thank you for choosing Annapurna!", "Bank: Bank of Baroda  A/c No. 48300200021175  IFSC BARB0INDIRA"],
    },
    {
        "file": "08_variant_phone_photo_sahyadri_SOS-58377.jpg",
        "id": "variant_phone_photo",
        "kind": "Extra input",
        "title": "Phone photo of a paper invoice",
        "what_to_expect": "A JPEG photo, not a PDF: tilted, on a desk, uneven light. Intake detects the file type from its bytes, auto-rotates the photo and converts it to a PDF, then Gemini reads it like a scan. It's a 3-way match on a second Sahyadri PO, so it's approved.",
        "layout": "modern", "accent": "#0E7C66", "photo": True,
        "vendor_name": "Sahyadri Office Supplies Pvt. Ltd.",
        "vendor_address": SAHYADRI,
        "meta": [("Invoice No.", "SOS/26-27/58377"), ("Invoice Date", "10 Nov 2026"), ("PO Number", "PO-2026-0368"),
                 ("Terms", "Net 30"), ("Due Date", "10 Dec 2026")],
        "lines": [
            {"description": "Filter coffee powder 1 kg", "qty": 10, "unit_price": 1920.00},
            {"description": "Tissue rolls - case of 12", "qty": 8, "unit_price": 2520.00},
            {"description": "Disinfectant wipes 80 ct", "qty": 12, "unit_price": 580.00},
        ],
        "totals": totals(46320.00, "intra"),
        "notes": ["Bank: HDFC Bank Ltd.  A/c No. 50200012345417  IFSC HDFC0000523"],
    },
    {
        "file": "09_variant_quote_pragati_Q-2291.pdf",
        "id": "variant_not_an_invoice",
        "kind": "Extra input",
        "doc_type": "quote",
        "title": "Not an invoice: a supplier quote",
        "what_to_expect": "Filed, not paid. A vendor sent a price quote to the AP inbox. It has prices and a total, but it isn't a bill, so there's nothing to pay and nothing wrong with the sender. It's filed for reference and kept out of the rejection numbers. If it ever turns out to be a real invoice, a reviewer can correct it and re-check.",
        "layout": "industrial", "doc_title": "QUOTATION",
        "vendor_name": "Pragati Print & Packaging",
        "vendor_address": "Plot 38, KIADB Industrial Area, Bommasandra, Bengaluru 560099",
        "vendor_contact": "sales@pragatiprint.in  ·  +91 80 2837 0148  ·  GSTIN 29ABBFP3374E1Z1",
        "meta": [("Quote No.", "Q-2291"), ("Quote Date", "11 Nov 2026"), ("Valid Until", "11 Dec 2026"), ("Terms", "Net 30")],
        "lines": [
            {"description": "Custom mailer box 9x6x3, 1-colour", "qty": 5000, "unit_price": 73.60},
            {"description": "Die / plate setup (one-time)", "qty": 1, "unit_price": 28000.00},
        ],
        "totals": totals(396000.00, note="GST extra as applicable", label_total="Quote Total"),
        "notes": ["This is a quotation, not a request for payment. Prices are valid for 30 days.",
                  "To accept, please issue a purchase order referencing Q-2291."],
        "footer": "This is a computer-generated quotation. Prices are exclusive of GST.",
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
