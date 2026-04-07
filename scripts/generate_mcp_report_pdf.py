"""Generate PDF from mcp-servers-report.md using reportlab."""
import re
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Preformatted,
    HRFlowable, Table, TableStyle
)

SRC = Path(__file__).parent.parent / "docs" / "mcp-servers-report.md"
DST = Path(__file__).parent.parent / "docs" / "mcp-servers-report.pdf"

md = SRC.read_text(encoding="utf-8")

doc = SimpleDocTemplate(
    str(DST),
    pagesize=A4,
    leftMargin=2*cm, rightMargin=2*cm,
    topMargin=2*cm, bottomMargin=2*cm,
)

styles = getSampleStyleSheet()
base = styles["Normal"]

S = {
    "h1": ParagraphStyle("h1", parent=base, fontSize=20, spaceAfter=12, spaceBefore=18,
                         textColor=colors.HexColor("#1a1a2e"), fontName="Helvetica-Bold"),
    "h2": ParagraphStyle("h2", parent=base, fontSize=14, spaceAfter=8, spaceBefore=14,
                         textColor=colors.HexColor("#16213e"), fontName="Helvetica-Bold"),
    "h3": ParagraphStyle("h3", parent=base, fontSize=11, spaceAfter=6, spaceBefore=10,
                         textColor=colors.HexColor("#0f3460"), fontName="Helvetica-Bold"),
    "body": ParagraphStyle("body", parent=base, fontSize=9, spaceAfter=4, leading=14),
    "code": ParagraphStyle("code", parent=base, fontSize=7.5, fontName="Courier",
                           spaceAfter=6, spaceBefore=4, leading=11,
                           backColor=colors.HexColor("#f5f5f5"),
                           leftIndent=10, rightIndent=10),
    "bullet": ParagraphStyle("bullet", parent=base, fontSize=9, spaceAfter=3,
                              leftIndent=16, bulletIndent=6, leading=13),
}


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_inline(text):
    text = re.sub(r"`([^`]+)`",
                  lambda m: f'<font name="Courier" size="8" color="#c7254e">{esc(m.group(1))}</font>',
                  text)
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: f"<b>{m.group(1)}</b>", text)
    return text


story = []
lines = md.splitlines()
i = 0

while i < len(lines):
    line = lines[i]

    # Horizontal rule
    if re.match(r"^---+$", line.strip()):
        story.append(HRFlowable(width="100%", thickness=0.5,
                                color=colors.HexColor("#cccccc"), spaceAfter=6, spaceBefore=6))
        i += 1
        continue

    # Headings
    if line.startswith("# ") and not line.startswith("## "):
        story.append(Paragraph(esc(line[2:]), S["h1"]))
        i += 1
        continue
    if line.startswith("## "):
        story.append(Paragraph(esc(line[3:]), S["h2"]))
        i += 1
        continue
    if line.startswith("### "):
        story.append(Paragraph(esc(line[4:]), S["h3"]))
        i += 1
        continue

    # Fenced code block
    if line.startswith("```"):
        i += 1
        code_lines = []
        while i < len(lines) and not lines[i].startswith("```"):
            code_lines.append(lines[i])
            i += 1
        i += 1
        story.append(Preformatted("\n".join(code_lines), S["code"]))
        continue

    # Markdown table
    if line.startswith("|"):
        rows = []
        while i < len(lines) and lines[i].startswith("|"):
            if re.match(r"^\|[-| :]+\|$", lines[i]):
                i += 1
                continue
            cells = [c.strip() for c in lines[i].split("|")[1:-1]]
            rows.append(cells)
            i += 1
        if rows:
            col_count = max(len(r) for r in rows)
            rows = [r + [""] * (col_count - len(r)) for r in rows]
            table_data = []
            for ri, row in enumerate(rows):
                table_data.append([
                    Paragraph(
                        parse_inline(esc(c)),
                        ParagraphStyle("tc", parent=base, fontSize=8,
                                       fontName="Helvetica-Bold" if ri == 0 else "Helvetica",
                                       leading=11)
                    )
                    for c in row
                ])
            col_width = (A4[0] - 4 * cm) / col_count
            t = Table(table_data, colWidths=[col_width] * col_count, repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#16213e")),
                ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4ff")]),
                ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
                ("VALIGN",       (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING",  (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING",   (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
            ]))
            story.append(t)
            story.append(Spacer(1, 6))
        continue

    # Bullet
    if re.match(r"^[-*] ", line):
        story.append(Paragraph(f"\u2022\u00a0 {parse_inline(esc(line[2:]))}", S["bullet"]))
        i += 1
        continue

    # Empty line
    if line.strip() == "":
        story.append(Spacer(1, 4))
        i += 1
        continue

    # Regular paragraph
    story.append(Paragraph(parse_inline(esc(line)), S["body"]))
    i += 1

doc.build(story)
print(f"PDF written: {DST}  ({DST.stat().st_size:,} bytes)")
