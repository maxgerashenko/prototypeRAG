"""Synthetic menu PDFs for scripts/compare_crawlers.py pdf (issue #116): a table menu,
a two-column layout, and a scanned (image-only) page. Usage:
  uv run --with reportlab --with pillow python scripts/make_test_pdfs.py OUT_DIR
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

# 1. table menu: service / duration / price
rows = [["Service", "Duration", "Price"], ["Classic haircut", "30 min", "$35"], ["Skin fade", "45 min", "$45"],
        ["Hot towel shave", "30 min", "$40"], ["Beard trim", "20 min", "$20"], ["Cut + beard combo", "60 min", "$60"]]
styles = getSampleStyleSheet()
doc = SimpleDocTemplate(str(out / "table_menu.pdf"), pagesize=letter)
table = Table(rows, colWidths=[200, 100, 80])
table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
doc.build([Paragraph("B&H Price List", styles["Title"]), table,
           Paragraph("Cash, card, Venmo accepted. Kids under 12: $5 off.", styles["Normal"])])

# 2. two columns, text drawn column by column (a typical restaurant/bar menu)
c = canvas.Canvas(str(out / "two_column_menu.pdf"), pagesize=letter)
c.setFont("Helvetica-Bold", 18)
c.drawString(72, 740, "Bar Menu")
c.setFont("Helvetica", 11)
left = [("Espresso", "4"), ("Cappuccino", "5"), ("Cold brew", "6"), ("Matcha latte", "7")]
right = [("Old Fashioned", "14"), ("Negroni", "13"), ("House red", "11"), ("IPA draft", "8")]
for col_x, items in ((72, left), (330, right)):
    y = 700
    for name, price in items:
        c.drawString(col_x, y, name)
        c.drawRightString(col_x + 190, y, f"${price}")
        y -= 20
c.showPage()
c.save()

# 3. scanned: the price list rendered to an image and embedded, no text layer
img = Image.new("RGB", (850, 400), "white")
d = ImageDraw.Draw(img)
for i, line in enumerate(["Sauna day pass  $45", "Cold plunge add-on  $15", "Towel rental  $5", "Open 9am - 10pm daily"]):
    d.text((40, 40 + i * 50), line, fill="black")
img.save(out / "scan.png")
c = canvas.Canvas(str(out / "scanned_menu.pdf"), pagesize=letter)
c.drawImage(str(out / "scan.png"), 40, 380, width=510, height=240)
c.save()
