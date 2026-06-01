"""
processScreen1DatEntry.py

Review-page workflow + cost center lookup support.

- process() returns a JSON-serializable context dict for rendering review.html
- build_pdf(ctx, out_dir) builds the PDF when user clicks GenPDF&Email

Facility and Cost Center Name are derived from the preloaded cost center lookup
dataframe passed from app.py (the Prod Tracker Salaries CSV loaded in _CACHE["cost_centers"]).
"""

import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any, Optional

import pandas as pd

# ReportLab (PDF)
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from flask import session

# column constants (Sonar S1192 fix)
COL_COST_CENTER = "Cost Center"
COL_PAY_PERIOD = "Pay Period"
COL_PP_START = "Pay Period Start Date"
COL_YEAR = "Year"
COL_BUDGET = "Budget Statistic Value"
COL_ACTUAL = "Actual Statistic Value"

# UI label constants (separate from schema)
LBL_COST_CENTER = "Cost Center"
LBL_FACILITY = "Facility"
LBL_COST_CENTER_NAME = "Cost Center Name"
LBL_DATE_REQUESTED = "Date Requested"
LBL_REQUISITIONS = "Requisition(s)"


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
# Normalization helper (match app.py behavior)
# =============================================================================
def _normalize_cost_center(x: str) -> str:
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit():
        s = s.lstrip("0") or "0"
    return s.upper()


# =============================================================================
# Step 1: SetHeaderMonth
# =============================================================================
def set_header_month() -> str:
    return datetime.now().strftime("%B %Y")


# =============================================================================
# Step 2: SetDisclaimerText
# =============================================================================
def set_disclaimer_text() -> str:
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
    # TODO later: derive from pay period table and requested date
    return "18"


# =============================================================================
# Step 4: CopyFromDataScreen
# =============================================================================
def copy_from_data_screen(form_fields: Dict[str, Any]) -> Dict[str, str]:
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
        "q1": g("q1"),
        "q2": g("q2"),
        "q3": g("q3"),
        "q4": g("q4"),
        "q5": g("q5"),
        "q6": g("q6"),
    }


# =============================================================================
# Step 5: ExtractFacilityName (lookup in preloaded cost center table)
# =============================================================================
def extract_facility_name(
    cost_center: str,
    facility_from_screen: str,
    cost_centers_df: Optional[pd.DataFrame] = None,
) -> Dict[str, str]:
    """
    Returns:
      facility         = Facility Desc
      cost_center_name = Cost Center Desc

    If missing or not found => ValueError.
    """
    cost_centers_df["_CC_NORM"] = cost_centers_df[COL_COST_CENTER].apply(_normalize_cost_center)

    _ = facility_from_screen  # facility is derived from lookup now

    if cost_centers_df is None or getattr(cost_centers_df, "empty", True):
        raise ValueError("Cost center lookup table is not loaded.")

    if "_CC_NORM" not in cost_centers_df.columns:
        raise ValueError("Cost center lookup table missing required column _CC_NORM.")

    cc_norm = _normalize_cost_center(cost_center)
    hit = cost_centers_df.loc[cost_centers_df["_CC_NORM"] == cc_norm]

    if hit.empty:
        raise ValueError(f"Invalid cost center: {cost_center}")

    row = hit.iloc[0]
    facility_desc = str(row.get("Facility Desc", "")).strip()
    cc_desc = str(row.get("Cost Center Desc", "")).strip()

    return {"facility": facility_desc, "cost_center_name": cc_desc}


# =============================================================================
# Step 6: GenOperationalStats (dummy)
# =============================================================================
'''
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
    '''

def gen_operational_stats(cost_center, pay_period, agg_df):

    cost_center = _normalize_cost_center(cost_center)

    cc_df = agg_df[agg_df[COL_COST_CENTER] == cost_center]

    curr_rows = cc_df[cc_df[COL_PAY_PERIOD] == pay_period]

    if curr_rows.empty:
        raise RuntimeError(f"No data for CC {cost_center} PP {pay_period}")

    curr_row = curr_rows.iloc[0]

    curr_pp_bud_vol = curr_row[COL_ACTUAL]

    ytd_df = cc_df[
        cc_df[COL_PAY_PERIOD] <= pay_period
    ]

    bud_pp_vol_ytd = ytd_df[COL_BUDGET].sum()
    act_pp_vol_ytd = ytd_df[COL_ACTUAL].sum()

    if act_pp_vol_ytd < curr_pp_bud_vol:
        raise RuntimeError("YTD < current PP — invalid state")
    
    print("DEBUG:", cost_center, pay_period, curr_pp_bud_vol, bud_pp_vol_ytd, act_pp_vol_ytd)


    return {
        "bud_pp_vol_ytd": bud_pp_vol_ytd,
        "act_pp_vol_ytd": act_pp_vol_ytd,
        "curr_pp_bud_vol": curr_pp_bud_vol
    }




# =============================================================================
# Step 7: GenProductivityStats (dummy)
# =============================================================================

def gen_productivity_stats(cost_center=None, header_month=None, prod_df=None) -> Dict[str, str]:
    return {
        "curr_pp_worked_fte": "0",
        "curr_pp_paid_fte": "0",
        "curr_pp_ot_pct": "0%",
        "curr_pp_act_vol": "0",
        "curr_prod_index": "0",
    }

'''
def gen_productivity_stats(cost_center: str, header_month: str, prod_df: pd.DataFrame) -> Dict[str, str]:
    rows = prod_df[
        (prod_df["Cost Center"] == str(cost_center)) &
        (prod_df["Month Number Desc"].str.upper() == header_month.split()[0].upper())
    ]

    worked_fte = rows["Worked FTE"].sum()
    paid_fte = rows["Paid FTE"].sum()
    ot_pct = rows["OT %"].mean()
    act_vol = rows["Actual Volume"].sum()
    prod_index = rows["Prod Index"].mean()

    return {
        "curr_pp_worked_fte": str(round(worked_fte, 2)),
        "curr_pp_paid_fte": str(round(paid_fte, 2)),
        "curr_pp_ot_pct": f"{round(ot_pct * 100, 1)}%",
        "curr_pp_act_vol": str(round(act_vol, 2)),
        "curr_prod_index": str(round(prod_index, 2)),
    }
''' 
#========================================================
# find last completed PP + aggregate rows w/ same Year, Cost Center and Pay Period Start Date
#========================================================
def get_latest_completed_pp(payperiod_df):

    df = payperiod_df.copy()

    df["Payroll_End_Date"] = pd.to_datetime(df["Payroll_End_Date"])

    today = pd.Timestamp.now()

    df = df[df["Payroll_End_Date"] < today]

    if df.empty:
        raise RuntimeError("No completed pay periods found")

    df = df.sort_values("Payroll_End_Date")

    row = df.iloc[-1]

    
    latest_end = row["Payroll_End_Date"]

    if (pd.Timestamp.now() - latest_end).days > 20:
        raise RuntimeError("PAYPERIODTABLE_ appears stale")


    pay_period = row["Pay_Period"]
    pp_start_date = row["Payroll_Start_Date"]

    if pd.isna(pp_start_date):
        raise RuntimeError("Missing Payroll_Start_Date in PAYPERIODTABLE_")

    return pay_period, pd.to_datetime(pp_start_date)

def aggregate_prod(prod_df, target_year):

    df = prod_df.copy()

    # --- normalize keys ---
    df[COL_COST_CENTER] = df[COL_COST_CENTER].apply(_normalize_cost_center)
    df[COL_PAY_PERIOD] = pd.to_numeric(df[COL_PAY_PERIOD], errors="coerce")

    df[COL_ACTUAL] = pd.to_numeric(df[COL_ACTUAL], errors="coerce").fillna(0)
    df[COL_BUDGET] = pd.to_numeric(df[COL_BUDGET], errors="coerce").fillna(0)

    
    year_str = str(target_year)

    df = df[
        df[COL_YEAR].str.endswith(year_str, na=False)
    ]


    # ============================================================
    # SPLIT DATASETS BY TYPE
    # ============================================================

    df_prod = df[df[COL_YEAR].astype(str).str.startswith("PROD")]
    df_budget = df[df[COL_YEAR].astype(str).str.startswith("BUDGET")]

    # ============================================================
    # FORCE PROJECTION (CRITICAL)
    # Only retain columns required for aggregation
    # ============================================================

    df_prod = df_prod[[COL_COST_CENTER, COL_PAY_PERIOD, COL_ACTUAL]].copy()
    df_budget = df_budget[[COL_COST_CENTER, COL_PAY_PERIOD, COL_BUDGET]].copy()

    # ============================================================
    # AGGREGATE EACH PIPELINE
    # ============================================================

    agg_prod = (
        df_prod
        .groupby([COL_COST_CENTER, COL_PAY_PERIOD], as_index=False)
        .agg({COL_ACTUAL: "sum"})
    )

    agg_budget = (
        df_budget
        .groupby([COL_COST_CENTER, COL_PAY_PERIOD], as_index=False)
        .agg({COL_BUDGET: "sum"})
    )

    # ============================================================
    # MERGE INTO SINGLE DATASET
    # ============================================================

    agg = pd.merge(
        agg_prod,
        agg_budget,
        on=[COL_COST_CENTER, COL_PAY_PERIOD],
        how="outer",
        validate="one_to_one"
    )

    
    agg[COL_ACTUAL] = agg[COL_ACTUAL].fillna(0)
    agg[COL_BUDGET] = agg[COL_BUDGET].fillna(0)



    # ============================================================
    # VALIDATION (DETERMINISTIC GUARANTEE)
    # ============================================================

    agg_counts = agg.groupby([COL_COST_CENTER, COL_PAY_PERIOD]).size()

    print("DEBUG agg:")
    print(agg)

    print("DEBUG agg_counts:")
    print(agg_counts)

    if (agg_counts != 1).any():
        raise RuntimeError("Aggregation failed: multiple rows per CC+PP")

    return agg


# =============================================================================
# Build Context (NO PDF)
# =============================================================================
def build_context(form_fields: Dict[str, Any], cost_centers_df=None, prod_df=None,payperiod_df=None,) -> PRCContext:
    disclaimer_text = set_disclaimer_text()

    pay_period, pp_start_date = get_latest_completed_pp(payperiod_df)

    target_year = pp_start_date.year

    prod_df[COL_PAY_PERIOD] = pd.to_numeric(prod_df[COL_PAY_PERIOD], errors="coerce")

    agg_df = aggregate_prod(prod_df, target_year)

    header_month = pp_start_date.strftime("%B %Y")

    if not header_month:
        raise RuntimeError("Invalid header_month")

    cost_center = form_fields.get("cost_center")

    if not cost_center:
        raise RuntimeError("Missing cost_center")

    ops = gen_operational_stats(
        cost_center=cost_center,
        pay_period=pay_period,
        agg_df=agg_df
    )

    for k, v in ops.items():
        if v is None:
            raise RuntimeError(f"Null stat: {k}")


    cost_center = str(form_fields.get("cost_center", "")).strip()

    data = copy_from_data_screen(form_fields)
    fac = extract_facility_name(data["cost_center"], data["facility"], cost_centers_df=cost_centers_df)

    pr = gen_productivity_stats(cost_center, header_month, prod_df)

    return PRCContext(
        header_month=header_month,
        pay_period=str(pay_period),
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

        bud_pp_vol_ytd=ops["bud_pp_vol_ytd"],
        act_pp_vol_ytd=ops["act_pp_vol_ytd"],
        curr_pp_bud_vol=ops["curr_pp_bud_vol"],
        bud_pp_paid_fte="",
        act_pp_paid_fte="",
        index_ytd="",
        volume_description="",
        budget_salaries="",
        actual_salaries="",
        turnover_12mo="",

        curr_pp_worked_fte=pr["curr_pp_worked_fte"],
        curr_pp_paid_fte=pr["curr_pp_paid_fte"],
        curr_pp_ot_pct=pr["curr_pp_ot_pct"],
        curr_pp_act_vol=pr["curr_pp_act_vol"],
        curr_prod_index=pr["curr_prod_index"],
    )


# =============================================================================
# PDF builder (called only on GenPDF&Email)
# =============================================================================
def build_pdf(ctx: PRCContext, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)

    safe_cc = "".join(ch for ch in ctx.cost_center if ch.isalnum() or ch in ("-", "_")) or "CC"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = session.get("user_id", "nosession")
    pdf_path = os.path.join(out_dir, f"PRC_BusinessCase_{safe_cc}_{session_id}_{ts}.pdf")

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
        alignment=1,
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

    story.append(Paragraph(
        f"Position Review Committee Business Case as of {{{ctx.header_month}}} &amp; Pay Period {{{ctx.pay_period}}}",
        title_style
    ))

    story.append(Paragraph(ctx.disclaimer_text.replace("\n", "<br/>"), small_style))
    story.append(Spacer(1, 8))

    
    top_headers = [
        LBL_DATE_REQUESTED,
        LBL_COST_CENTER,
        LBL_FACILITY,
        LBL_COST_CENTER_NAME,
        LBL_REQUISITIONS
    ]

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
# Main entrypoint called by Flask on submit (returns dict for review page)
# =============================================================================

def process(form_fields, cost_centers_df=None, prod_df=None, payperiod_df=None):

    if payperiod_df is None or payperiod_df.empty:
        raise RuntimeError("PAYPERIODTABLE not loaded")

    ctx = build_context(
        form_fields,
        cost_centers_df=cost_centers_df,
        prod_df=prod_df,
        payperiod_df=payperiod_df
    )

    return asdict(ctx)

