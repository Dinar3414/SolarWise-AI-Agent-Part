"""PDF export (ReportLab). Always stamped 'Preliminary — not for installation' unless a human approved it."""
from __future__ import annotations
import datetime as dt, io
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from . import config
from .schemas import ToolResult, Citation

INK, AMBER, TEAL, RED = colors.HexColor("#0B1F33"), colors.HexColor("#F59E0B"), colors.HexColor("#0F766E"), colors.HexColor("#B91C1C")
STATUS_COL = {"ok": TEAL, "warn": colors.HexColor("#B45309"), "fail": RED, "incomplete": colors.HexColor("#475569")}


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_report(project: dict, results: list[ToolResult], citations: list[Citation], boq: dict | None,
                 review: dict | None = None, title: str = "SolarWise AI — Preliminary Design Summary") -> bytes:
    review = review or {"status": "preliminary", "reviewer": None, "note": ""}
    approved = review.get("status") == "approved"
    stamp = "APPROVED BY QUALIFIED REVIEWER (preliminary design)" if approved else config.REPORT_STAMP
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16*mm, rightMargin=16*mm, topMargin=18*mm, bottomMargin=18*mm,
                            title=title, author="SolarWise AI")
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=ss["Heading1"], textColor=INK, fontSize=18, spaceAfter=4)
    H2 = ParagraphStyle("H2", parent=ss["Heading2"], textColor=INK, fontSize=12, spaceBefore=10, spaceAfter=4)
    B = ParagraphStyle("B", parent=ss["BodyText"], fontSize=9, leading=12)
    S = ParagraphStyle("S", parent=B, fontSize=8, textColor=colors.HexColor("#475569"))
    el = [Paragraph(title, H1), Paragraph(f"Generated {dt.datetime.now():%Y-%m-%d %H:%M}", S), Spacer(1, 4)]
    banner = Table([[Paragraph(f"<b>{_esc(stamp)}</b>", ParagraphStyle("st", parent=B, textColor=colors.white, alignment=1))]],
                   colWidths=[178*mm])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TEAL if approved else RED), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    el += [banner]

    el.append(Paragraph("Project inputs", H2))
    rows = [[k, _esc(v)] for k, v in project.items() if v not in (None, "")] or [["(none)", ""]]
    t = Table(rows, colWidths=[55*mm, 123*mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9"))]))
    el.append(t)

    el.append(Paragraph("Calculation results", H2))
    for r in results:
        col = STATUS_COL.get(r.status, INK)
        block = [Paragraph(f'<font color="{col.hexval()}"><b>{_esc(r.tool)} — {r.status.upper()}</b></font>', B)]
        for k, v in r.values.items():
            block.append(Paragraph(f"&nbsp;&nbsp;{_esc(k)}: {_esc(v)}", B))
        for m in r.missing:
            block.append(Paragraph(f"&nbsp;&nbsp;<i>missing:</i> {_esc(m)}", B))
        for w in r.warnings:
            block.append(Paragraph(f'&nbsp;&nbsp;<font color="#B91C1C">⚠ {_esc(w)}</font>', B))
        for a in r.assumptions:
            block.append(Paragraph(f"&nbsp;&nbsp;<i>assumption:</i> {_esc(a)}", S))
        el.append(KeepTogether(block + [Spacer(1, 4)]))

    if boq and boq.get("rows"):
        el.append(Paragraph("Preliminary bill of quantities", H2))
        data = [["Item", "Specification", "Qty", "Unit", "Source"]] + [
            [Paragraph(_esc(r["item"]), S), Paragraph(_esc(r["specification"]), S), _esc(r["qty"]), _esc(r["unit"]), Paragraph(_esc(r["source"]), S)]
            for r in boq["rows"]]
        t = Table(data, colWidths=[38*mm, 62*mm, 22*mm, 18*mm, 38*mm], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), INK), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, -1), 8),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        el.append(t)
        for w in boq.get("warnings", []):
            el.append(Paragraph(f'<font color="#B91C1C">⚠ {_esc(w)}</font>', B))
        el.append(Paragraph(_esc(boq.get("note", "")), S))

    if citations:
        el.append(Paragraph("Sources (datasheet receipts)", H2))
        seen = set()
        for c in citations:
            key = (c.source_file, c.page)
            if key in seen:
                continue
            seen.add(key)
            el.append(Paragraph(f"• {_esc(c.manufacturer)} {_esc(c.model)} {('rev ' + _esc(c.revision)) if c.revision else ''} — "
                                f"{_esc(c.source_file)}, page {c.page}", B))

    el.append(Paragraph("Review and approval", H2))
    el.append(Paragraph(f"Status: <b>{_esc(review.get('status'))}</b>. Reviewer: {_esc(review.get('reviewer') or '—')}. {_esc(review.get('note') or '')}", B))
    el.append(Paragraph("Estimated generation is not guaranteed output. This summary does not certify code compliance or authorise "
                        "installation; a qualified solar/electrical engineer must verify all values against the exact datasheet revisions.", S))

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(RED if not approved else TEAL)
        canvas.drawString(16*mm, 9*mm, stamp)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawRightString(194*mm, 9*mm, f"SolarWise AI · page {d.page}")
        canvas.restoreState()

    doc.build(el, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
