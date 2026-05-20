r"""
processScreen1DatEntry.py

Logic and sequence of steps for processScreen1DatEntry

GOAL
----
Extract relevant information from Screen 1, generate a PDF in the PRC format,
and return the PDF path to the calling Flask route.

SEQUENCE (matches your design)
------------------------------
1. SetHeaderMonth
2. SetDisclaimerText
3. SetHeaderPayPeriod
4. CopyFromDataScreen
5. ExtractFacilityName
6. GenOperationalStats (dummy for now)
7. GenProductivityStats (dummy for now)
8. Assemble a formatted PDF page
9. Return PDF path so the UI can show "Email PDF" or "Cancel"
10. If "Email PDF" clicked, SendEmailPDF handles sending (separate module)
"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional

# ReportLab (PDF)
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer


# =============================================================================
# Data container
# =============================================================================
@dataclass
class PRCContext:
    header_month: str
    pay_period: str
    disclaimer_text: str

    # From data screen
    date_requested: str
    cost_center: str
    facility: str
    cost_center_name: str
    requisitions: str

    position_requested: str
    position_title: str
    open_fte: str
    posted_fte: str
    total_requested_fte: str
    email_to: str

    q1_workflow_change: str
    q2_replacement_detail: str
    q3_absorb_work: str
    q4_skillset: str
    q5_similar_roles: str
    q6_part_time: str

    # Operational stats (dummy now)
    bud_pp_vol_ytd: str
    act_pp_vol_ytd: str
    curr_pp_bud_vol: str
    bud_pp_paid_fte: str
    act_pp_paid_fte: str
    index_ytd: str
    volume_description: str
    budget_salaries: str
    actual_salaries: str
    turnover_12mo: str

    # Productivity stats (dummy now)
    curr_pp_worked_fte: str
    curr_pp_paid_fte: str
    curr_pp_ot_pct: str
    curr_pp_act_vol: str
    curr_prod_index: str


# =============================================================================
# Step 1: SetHeaderMonth
# =============================================================================
def set_header_month() -> str:
    """
    Sets the header month shown as {Full Month}.
    For now: uses current month and year, e.g., 'December 2025'.
    """
    return datetime.now().strftime("%B %Y")


# =============================================================================
# Step 2: SetDisclaimerText
# =============================================================================
def set_disclaimer_text() -> str:
    """
    Sets the disclaimer text exactly as required by the PRC form.
    """
    return (
        "For consideration at the next Position Review Committee meeting, this form must be completed and "
        "returned to Janet Hinds by Friday at 5pm, along with all the required approvals in Hiring Manager.\n\n"
        "You will also copy your HR Business Partner on the e-mail. Those managers who have positions to be reviewed "
        "will be invited to attend the Position Review Committee meeting and time scheduled to review the business case. "
        "In the event of a full agenda, every effort will be made to review all requests, but requests will be reviewed "
        "in the order that this form was received."
    )


# =============================================================================
# Step 3: SetHeaderPayPeriod
# =============================================================================
def set_header_pay_period() -> str:
    """
    For now: return 18 (per your instruction).
    Later: determine based on pay period table / requested date.
    """
    return "18"


# =============================================================================
# Step 4: CopyFromDataScreen  (UPDATED TO MATCH YOUR HTML FORM)
# =============================================================================
def copy_from_data_screen(form_fields: Dict[str, Any]) -> Dict[str, str]:
    """
    Reads values submitted from PRC Data Entry form.

    Your form uses these keys:
      cost_center
      facility
      requisition_no
      position_title
      total_requested_ftes
      position_requested
      open_fte
      posted_fte
      requested_date
      emails
      q1..q6
    """
    def g(key: str) -> str:
        return str(form_fields.get(key, "")).strip()

    return {
        "date_requested": g("requested_date"),
        "cost_center": g("cost_center"),
        "facility": g("facility"),
        "requisitions": g("requisition_no"),
        "position_requested": g("position_requested"),
        "position_title": g("position_title"),
        "open_fte": g("open_fte"),
        "posted_fte": g("posted_fte"),
        "total_requested_fte": g("total_requested_ftes"),
        "email_to": g("emails"),

        # Questions 1–6 (your form uses q1..q6)
        "q1": g("q1"),
        "q2": g("q2"),
        "q3": g("q3"),
        "q4": g("q4"),
        "q5": g("q5"),
        "q6": g("q6"),
    }


# =============================================================================
# Step 5: ExtractFacilityName
# =============================================================================
def extract_facility_name(cost_center: str, facility_from_screen: str) -> Dict[str, str]:
    """
    For now (per your instruction):
      - Facility BHS
      - Cost Center Name Ambulatory

    Later: lookup from cached cost center dataset using cost_center.
    """
    _ = cost_center
    _ = facility_from_screen
    return {
        "facility": "BHS",
        "cost_center_name": "Ambulatory",
    }


# =============================================================================
# Step 6: GenOperationalStats (dummy)
# =============================================================================
def gen_operational_stats() -> Dict[str, str]:
    return {
        "bud_pp_vol_ytd": "0",
        "act_pp_vol_ytd": "0",
        "curr_pp_bud_vol": "0",
        "bud_pp_paid_fte": "0",
        "act_pp_paid_fte": "0",
        "index_ytd": "0",
        "volume_description": "DUMMY",
        "budget_salaries": "0",
        "actual_salaries": "0",
        "turnover_12mo": "0",
    }


# =============================================================================
# Step 7: GenProductivityStats (dummy)
# =============================================================================
def gen_productivity_stats() -> Dict[str, str]:
    return {
        "curr_pp_worked_fte": "0",
        "curr_pp_paid_fte": "0",
        "curr_pp_ot_pct": "0%",
        "curr_pp_act_vol": "0",
        "curr_prod_index": "0",
    }


# =============================================================================
# Step 8: Assemble formatted PDF page
# =============================================================================
def build_pdf(ctx: PRCContext, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)

    safe_cc = "".join(ch for ch in ctx.cost_center if ch.isalnum() or ch in ("-", "_")) or "CC"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = os.path.join(out_dir, f"PRC_BusinessCase_{safe_cc}_{ts}.pdf")

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.4 * inch,
        bottomMargin=0.4 * inch,
        title="Position Review Committee Business Case",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title",
        parent=styles["Title"],
        fontSize=12,
        leading=14,
        alignment=1,  # center
        spaceAfter=8,
    )
    small_style = ParagraphStyle(
        "small",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=10,
    )
    q_style = ParagraphStyle(
        "q",
        parent=styles["Normal"],
        fontSize=9,
        leading=11,
    )

    yellow = colors.Color(1.0, 0.95, 0.75)
    gray = colors.Color(0.92, 0.92, 0.92)

    story = []

    # Header
    story.append(Paragraph(
        f"Position Review Committee Business Case as of {{{ctx.header_month}}} &amp; Pay Period {{{ctx.pay_period}}}",
        title_style
    ))

    # Disclaimer
    story.append(Paragraph(ctx.disclaimer_text.replace("\n", "<br/>"), small_style))
    story.append(Spacer(1, 8))

    # Top grid
    top_headers = ["Date Requested", "Cost Center", "Facility", "Cost Center Name", "Requisition(s)"]
    top_values  = [ctx.date_requested, ctx.cost_center, ctx.facility, ctx.cost_center_name, ctx.requisitions]
    t1 = Table([top_headers, top_values], colWidths=[1.2*inch, 1.2*inch, 1.2*inch, 2.2*inch, 2.0*inch])
    t1.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), yellow),
        ("BACKGROUND", (0,1), (-1,1), gray),
        ("BOX", (0,0), (-1,-1), 0.8, colors.black),
        ("INNERGRID", (0,0), (-1,-1), 0.5, colors.black),
        ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
    ]))
    story.append(t1)
    story.append(Spacer(1, 8))

    # Position row
    pos_headers = ["Position Requested", "Position Title", "Open FTE's", "Posted FTE's", "Total Requested FTE'S", "emailtoAddress"]
    pos_values  = [ctx.position_requested, ctx.position_title, ctx.open_fte, ctx.posted_fte, ctx.total_requested_fte, ctx.email_to]
    t2 = Table([pos_headers, pos_values], colWidths=[1.35*inch, 2.2*inch, 0.95*inch, 0.95*inch, 1.25*inch, 1.5*inch])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), yellow),
        ("BACKGROUND", (0,1), (-1,1), gray),
        ("BOX", (0,0), (-1,-1), 0.8, colors.black),
        ("INNERGRID", (0,0), (-1,-1), 0.5, colors.black),
        ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
    ]))
    story.append(t2)
    story.append(Spacer(1, 10))

    # Operational stats section
    story.append(Paragraph("Operational Statistics", ParagraphStyle("sec", parent=styles["Heading4"], alignment=1)))
    op_headers = ["BUD PP Vol YTD", "Act PP Vol YTD", "Current PP Bud Vol", "Bud PP Paid FTE's", "Act PP Paid FTE's", "Index YTD"]
    op_values  = [ctx.bud_pp_vol_ytd, ctx.act_pp_vol_ytd, ctx.curr_pp_bud_vol, ctx.bud_pp_paid_fte, ctx.act_pp_paid_fte, ctx.index_ytd]
    t3 = Table([op_headers, op_values], colWidths=[1.3*inch]*6)
    t3.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), yellow),
        ("BACKGROUND", (0,1), (-1,1), gray),
        ("BOX", (0,0), (-1,-1), 0.8, colors.black),
        ("INNERGRID", (0,0), (-1,-1), 0.5, colors.black),
        ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
    ]))
    story.append(t3)
    story.append(Spacer(1, 6))

    op2_headers = ["Volume Description", "Budget Salaries", "Actual Salaries", "12 Month Turnover"]
    op2_values  = [ctx.volume_description, ctx.budget_salaries, ctx.actual_salaries, ctx.turnover_12mo]
    t4 = Table([op2_headers, op2_values], colWidths=[2.6*inch, 1.6*inch, 1.6*inch, 1.9*inch])
    t4.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), yellow),
        ("BACKGROUND", (0,1), (-1,1), gray),
        ("BOX", (0,0), (-1,-1), 0.8, colors.black),
        ("INNERGRID", (0,0), (-1,-1), 0.5, colors.black),
        ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
    ]))
    story.append(t4)
    story.append(Spacer(1, 10))

    # Productivity stats section
    story.append(Paragraph("Productivity Statistics", ParagraphStyle("sec2", parent=styles["Heading4"], alignment=1)))
    pr_headers = ["Current PP Worked FTE's", "Current PP Paid FTE's", "Current PP OT%", "Current PP Act Vol", "Current Prod Index"]
    pr_values  = [ctx.curr_pp_worked_fte, ctx.curr_pp_paid_fte, ctx.curr_pp_ot_pct, ctx.curr_pp_act_vol, ctx.curr_prod_index]
    t5 = Table([pr_headers, pr_values], colWidths=[1.6*inch, 1.6*inch, 1.2*inch, 1.6*inch, 1.4*inch])
    t5.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), yellow),
        ("BACKGROUND", (0,1), (-1,1), gray),
        ("BOX", (0,0), (-1,-1), 0.8, colors.black),
        ("INNERGRID", (0,0), (-1,-1), 0.5, colors.black),
        ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
    ]))
    story.append(t5)
    story.append(Spacer(1, 12))

    # Questions 1–6 blocks
    def q_block(n: int, question: str, answer: str):
        story.append(Paragraph(f"<b>{n}. {question}</b>", q_style))
        story.append(Spacer(1, 2))
        ans = answer if answer else " "
        tb = Table([[Paragraph(ans.replace("\n", "<br/>"), q_style)]], colWidths=[7.5*inch])
        tb.setStyle(TableStyle([
            ("BOX", (0,0), (-1,-1), 0.8, colors.black),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("RIGHTPADDING", (0,0), (-1,-1), 6),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 18),
        ]))
        story.append(tb)
        story.append(Spacer(1, 10))

    q_block(1, "If position(s) were not filled, how would the workflow change?", ctx.q1_workflow_change)
    q_block(2, "If this is a replacement request, who left the role, where did they go, why did they leave and what efforts were made to retain them?", ctx.q2_replacement_detail)
    q_block(3, "What current positions may be able to absorb the work? Can technology replace or reduce any of the work functions?", ctx.q3_absorb_work)
    q_block(4, "Is there a different skill set that is needed?", ctx.q4_skillset)
    q_block(5, "Are there other roles within the organization that perform similar functions?", ctx.q5_similar_roles)
    q_block(6, "If a full-time position is being requested, could the work process be modified to be reduced to a part-time position?", ctx.q6_part_time)

    doc.build(story)
    return pdf_path


# =============================================================================
# Main entrypoint called by Flask: processor.process(form_fields)
# =============================================================================
def process(form_fields: Dict[str, Any], out_dir: Optional[str] = None) -> str:
    """
    Called by app.py submit().
    Returns the generated PDF file path.
    """
    if out_dir is None:
        out_dir = os.path.join(os.getcwd(), "output_pdfs")

    header_month = set_header_month()
    disclaimer_text = set_disclaimer_text()
    pay_period = set_header_pay_period()

    data = copy_from_data_screen(form_fields)

    fac = extract_facility_name(
        cost_center=data["cost_center"],
        facility_from_screen=data["facility"],
    )

    op = gen_operational_stats()
    pr = gen_productivity_stats()

    ctx = PRCContext(
        header_month=header_month,
        pay_period=pay_period,
        disclaimer_text=disclaimer_text,

        date_requested=data["date_requested"],
        cost_center=data["cost_center"],
        facility=fac["facility"],
        cost_center_name=fac["cost_center_name"],
        requisitions=data["requisitions"],

        position_requested=data["position_requested"],
        position_title=data["position_title"],
        open_fte=data["open_fte"],
        posted_fte=data["posted_fte"],
        total_requested_fte=data["total_requested_fte"],
        email_to=data["email_to"],

        q1_workflow_change=data["q1"],
        q2_replacement_detail=data["q2"],
        q3_absorb_work=data["q3"],
        q4_skillset=data["q4"],
        q5_similar_roles=data["q5"],
        q6_part_time=data["q6"],

        bud_pp_vol_ytd=op["bud_pp_vol_ytd"],
        act_pp_vol_ytd=op["act_pp_vol_ytd"],
        curr_pp_bud_vol=op["curr_pp_bud_vol"],
        bud_pp_paid_fte=op["bud_pp_paid_fte"],
        act_pp_paid_fte=op["act_pp_paid_fte"],
        index_ytd=op["index_ytd"],
        volume_description=op["volume_description"],
        budget_salaries=op["budget_salaries"],
        actual_salaries=op["actual_salaries"],
        turnover_12mo=op["turnover_12mo"],

        curr_pp_worked_fte=pr["curr_pp_worked_fte"],
        curr_pp_paid_fte=pr["curr_pp_paid_fte"],
        curr_pp_ot_pct=pr["curr_pp_ot_pct"],
        curr_pp_act_vol=pr["curr_pp_act_vol"],
        curr_prod_index=pr["curr_prod_index"],
    )

    pdf_path = build_pdf(ctx, out_dir=out_dir)
    return pdf_path
