"""
Generates apps/api/tests/fixtures/demo_business.pdf

A realistic SMB customer-service document for Acme Coffee Roasters,
designed to exercise the M2 ingestion pipeline end-to-end:

- Multiple headings (Docling layout detection)
- Mixed paragraph styles (Chonkie structure-aware chunking boundaries)
- A real text-extractable table (not an image) for table-preservation tests
- Enough named entities to make M2 entity extraction non-trivial
  (products, policies, places, named processes)
- Realistic length (~3-4 pages of content)
- Final size well under 500KB

Run:
    python scripts/build_demo_pdf.py
"""

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)


OUTPUT_PATH = "apps/api/tests/fixtures/demo_business.pdf"


def build_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="DocTitle",
            parent=styles["Title"],
            fontSize=20,
            spaceAfter=14,
            textColor=colors.HexColor("#1a1a1a"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Subtitle",
            parent=styles["Normal"],
            fontSize=11,
            textColor=colors.HexColor("#555555"),
            spaceAfter=18,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHeading",
            parent=styles["Heading1"],
            fontSize=14,
            spaceBefore=14,
            spaceAfter=8,
            textColor=colors.HexColor("#0F4C3A"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="SubHeading",
            parent=styles["Heading2"],
            fontSize=11,
            spaceBefore=10,
            spaceAfter=4,
            textColor=colors.HexColor("#1a1a1a"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Body",
            parent=styles["BodyText"],
            fontSize=10,
            leading=14,
            spaceAfter=8,
        )
    )
    return styles


def build_story(styles):
    s = []
    p = lambda text, style="Body": s.append(Paragraph(text, styles[style]))
    sp = lambda h=8: s.append(Spacer(1, h))

    # --- Header --------------------------------------------------------------
    p("Acme Coffee Roasters — Customer Service Handbook", "DocTitle")
    p(
        "Internal reference document. Version 4.2. Last reviewed: 14 March 2026.",
        "Subtitle",
    )

    # --- Section 1: About Us -------------------------------------------------
    p("1. About Acme Coffee Roasters", "SectionHeading")
    p(
        "Acme Coffee Roasters is a specialty coffee company based in Cape Town, "
        "South Africa, with a roastery in Woodstock and retail locations in "
        "Sea Point and Stellenbosch. We source single-origin green coffee from "
        "smallholder farms in Ethiopia, Kenya, Rwanda, and Burundi, and roast "
        "in small batches three times a week. Our wholesale customers include "
        "independent cafés across the Western Cape and Gauteng provinces."
    )
    p(
        "This handbook is the authoritative reference for our customer service "
        "team. It is also the document our AI customer service agent draws from "
        "when answering customer questions. If any information here is incorrect "
        "or updated, customers will receive incorrect answers. Please flag "
        "errors to operations@acmecoffee.example."
    )

    # --- Section 2: Products -------------------------------------------------
    p("2. Our Coffee Range", "SectionHeading")
    p(
        "We offer three core product lines: Single Origin, Signature Blends, "
        "and Decaffeinated. All beans are roasted to order and shipped within "
        "48 hours of roasting. Whole bean is the default; we grind to order on "
        "request and recommend whole bean for freshness."
    )

    p("2.1 Single Origin", "SubHeading")
    p(
        "Our Single Origin range rotates seasonally based on harvest cycles. "
        "Current offerings include Yirgacheffe (Ethiopia), Nyeri AA (Kenya), "
        "and Mibirizi (Rwanda). Each origin is sold as 250g and 1kg bags. "
        "Tasting notes, altitude, and processing method are printed on each bag "
        "and listed on our website."
    )

    p("2.2 Signature Blends", "SubHeading")
    p(
        "We offer four house blends year-round: Table Mountain Espresso (our "
        "flagship), Cape Point Filter, Karoo Decaf, and Winelands Espresso. "
        "Blends are formulated to maintain consistent flavour profiles even as "
        "individual component origins rotate. The Table Mountain Espresso has "
        "been our best-seller since 2019 and accounts for roughly 40 percent "
        "of our retail volume."
    )

    p("2.3 Decaffeinated", "SubHeading")
    p(
        "Our Karoo Decaf is processed using the Swiss Water Method, which uses "
        "no chemical solvents. It is suitable for customers avoiding caffeine "
        "for medical or personal reasons. The Swiss Water Method removes "
        "approximately 99.9 percent of caffeine from the green beans before "
        "roasting."
    )

    # --- Section 3: Pricing Table -------------------------------------------
    p("3. Wholesale Pricing", "SectionHeading")
    p(
        "Prices below are per kilogram, excluding VAT, for wholesale accounts. "
        "Minimum order quantities apply. All prices are in South African Rand "
        "and are valid through 30 June 2026. Retail pricing differs and is "
        "available on the public website."
    )
    sp(6)

    table_data = [
        ["Product", "Grade", "MOQ (kg)", "Price (R/kg)", "Lead Time"],
        ["Yirgacheffe", "Single Origin", "5", "R 480", "3-5 days"],
        ["Nyeri AA", "Single Origin", "5", "R 520", "3-5 days"],
        ["Mibirizi", "Single Origin", "5", "R 460", "3-5 days"],
        ["Table Mountain Espresso", "Signature Blend", "10", "R 380", "2-3 days"],
        ["Cape Point Filter", "Signature Blend", "10", "R 360", "2-3 days"],
        ["Winelands Espresso", "Signature Blend", "10", "R 395", "2-3 days"],
        ["Karoo Decaf", "Decaffeinated", "5", "R 440", "5-7 days"],
    ]

    tbl = Table(
        table_data,
        colWidths=[2.0 * inch, 1.4 * inch, 0.9 * inch, 1.1 * inch, 1.1 * inch],
        repeatRows=1,
    )
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F4C3A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#F5F5F0")]),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    s.append(tbl)
    sp(10)
    p(
        "Wholesale accounts must complete a credit application before their "
        "first order. Approved accounts receive 30-day payment terms. New "
        "accounts pay on order for the first three months."
    )

    s.append(PageBreak())

    # --- Section 4: Shipping & Returns --------------------------------------
    p("4. Shipping and Delivery", "SectionHeading")
    p(
        "We ship nationally within South Africa via The Courier Guy for orders "
        "under 20kg and via Aramex for larger consignments. Deliveries to major "
        "metros (Cape Town, Johannesburg, Pretoria, Durban) typically arrive "
        "within 2-3 business days of dispatch. Outlying areas may take 5-7 days."
    )
    p(
        "We do not currently ship internationally. Customers outside South "
        "Africa should contact our wholesale team to discuss freight forwarding "
        "options."
    )
    p(
        "Shipping is free for retail orders above R 750 and for all wholesale "
        "orders meeting the minimum order quantity. Below those thresholds, "
        "shipping is calculated at checkout based on weight and destination."
    )

    # --- Section 5: Returns Policy ------------------------------------------
    p("5. Returns and Refunds", "SectionHeading")
    p(
        "We stand behind every bag we roast. If you are unhappy with your "
        "coffee for any reason, contact us within 14 days of delivery and we "
        "will replace the product or refund your purchase."
    )

    p("5.1 What We Replace", "SubHeading")
    p(
        "We replace coffee that has been damaged in transit, was incorrectly "
        "fulfilled (wrong product, wrong grind, wrong quantity), or arrived "
        "past its roast-date freshness window of six weeks. Photographic "
        "evidence of damage is required for transit claims."
    )

    p("5.2 What We Refund", "SubHeading")
    p(
        "We issue refunds for unopened bags returned within 14 days, for "
        "subscription cancellations processed before the next billing cycle, "
        "and for wholesale orders cancelled before dispatch. Refunds are issued "
        "to the original payment method within 5 business days of receipt."
    )

    p("5.3 What We Do Not Replace or Refund", "SubHeading")
    p(
        "We do not replace or refund coffee that has been opened and partially "
        "consumed unless there is a verifiable quality defect. Personal taste "
        "preferences are not grounds for refund — we encourage customers "
        "unsure of their preferences to order our Discovery Sample Pack first."
    )

    # --- Section 6: Subscriptions -------------------------------------------
    p("6. Subscriptions", "SectionHeading")
    p(
        "Acme Subscriptions ship every two or four weeks. Subscribers receive "
        "a 10 percent discount on retail pricing and free shipping on every "
        "order regardless of value. Subscribers can pause, skip, or cancel at "
        "any time through their account dashboard. Cancellations made at least "
        "48 hours before the next billing date take effect immediately; later "
        "cancellations take effect from the cycle after next."
    )
    p(
        "The Roaster's Choice subscription is our most popular tier. Each "
        "shipment includes a 250g bag of a Single Origin selected by our head "
        "roaster, with tasting notes and brewing recommendations. Roaster's "
        "Choice is the best way for new customers to explore the range."
    )

    # --- Section 7: Wholesale Accounts --------------------------------------
    p("7. Wholesale Accounts", "SectionHeading")
    p(
        "Wholesale accounts are available to cafés, restaurants, offices, and "
        "retail stockists. Application is via the wholesale form on our "
        "website. Approved accounts receive trade pricing, dedicated account "
        "management, training resources, and equipment service referrals to "
        "our partner technicians."
    )
    p(
        "We provide barista training at no charge for accounts ordering more "
        "than 20kg per month. Training is delivered at our Woodstock roastery "
        "and covers extraction theory, milk technique, calibration, and basic "
        "machine maintenance. Two training slots are reserved per quarter for "
        "each qualifying account."
    )

    # --- Section 8: Contact -------------------------------------------------
    p("8. Contact and Escalation", "SectionHeading")
    p(
        "Retail customer enquiries: hello@acmecoffee.example. Response time "
        "during business hours is typically under 4 hours. Wholesale account "
        "enquiries: wholesale@acmecoffee.example. Urgent operational issues "
        "(missed deliveries, machine failures at training-tier accounts): "
        "021 555 0143 during business hours, 082 555 0143 after hours."
    )
    p(
        "Customer service agents should escalate to a human team member when: "
        "a customer asks about an order placed more than 60 days ago, a refund "
        "request exceeds R 2000, a wholesale account requests credit terms or "
        "pricing changes, or a customer expresses dissatisfaction in language "
        "suggesting they may leave a public review."
    )

    return s


def build_pdf():
    doc = SimpleDocTemplate(
        OUTPUT_PATH,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="Acme Coffee Roasters — Customer Service Handbook",
        author="Acme Coffee Roasters",
        subject="Customer service reference document",
    )
    styles = build_styles()
    story = build_story(styles)
    doc.build(story)


if __name__ == "__main__":
    build_pdf()
    import os

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"Wrote {OUTPUT_PATH} ({size_kb:.1f} KB)")
