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

# column constants 
COL_COST_CENTER = "Cost Center"
COL_COST_CENTER_SAL = "Cost_Center" #Used in the Actual/Budget Saaries table compute only
COL_PAY_PERIOD = "Pay Period"
COL_PP_START = "Pay Period Start Date"
COL_YEAR = "Year"
COL_BUDGET = "Budget Statistic Value"
COL_ACTUAL = "Actual Statistic Value"
COL_BUDGET_FTE = "Budget FTE's"
COL_ACTUAL_FTE = "Actual FTE's"
COL_DEPT = "Dept"
COL_STAT_DESC = "Stat_Desc"
COL_SAL_YEAR = "Year"
COL_MONTH = "Month_Number"
COL_GL_VALUE = "GL_Month_Value"
COL_WORKED_FTE = "Worked FTE's"



#Column Value Prefixes
YEAR_PREFIX_FLEX = "FLEX"
YEAR_PREFIX_ACTUAL = "ACTUAL"

# UI label constants
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
    fiscal_year: str
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
    roll4_worked_fte: str
    roll4_paid_fte: str
    roll4_vol: str



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
# Rounding helper 
# =============================================================================

def _round0(x):
    try:
        return str(int(round(float(x), 0)))
    except Exception:
        return str(x)

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


def extract_volume_description(cost_center: str, volume_df: pd.DataFrame) -> str:

    cc = _normalize_cost_center(cost_center)

    if volume_df is None or getattr(volume_df, "empty", True):
        raise RuntimeError("Volume description table not loaded")

    df = volume_df.copy()

    df[COL_DEPT] = df[COL_DEPT].astype(str).str.strip()

    row = df[df[COL_DEPT] == cc]

    if row.empty:
        return ""  # safe fallback (description null allowed)

    return str(row.iloc[0][COL_STAT_DESC]).strip()


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

    curr_pp_bud_vol = curr_row[COL_BUDGET]

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

def return_bud_pp_ftes(cost_center, pay_period, agg_df):

    cc = _normalize_cost_center(cost_center)

    cc_df = agg_df[
        agg_df[COL_COST_CENTER] == cc
    ]

    curr_rows = cc_df[
        cc_df[COL_PAY_PERIOD] == pay_period
    ]

    if curr_rows.empty:
        raise RuntimeError(
            f"No FTE data for CC {cost_center} PP {pay_period}"
        )

    return curr_rows.iloc[0][COL_BUDGET_FTE]


def return_act_pp_ftes(cost_center, pay_period, agg_df):

    cc = _normalize_cost_center(cost_center)

    cc_df = agg_df[
        agg_df[COL_COST_CENTER] == cc
    ]

    curr_rows = cc_df[
        cc_df[COL_PAY_PERIOD] == pay_period
    ]

    if curr_rows.empty:
        raise RuntimeError(
            f"No FTE data for CC {cost_center} PP {pay_period}"
        )

    return curr_rows.iloc[0][COL_ACTUAL_FTE]

def compute_salary_metrics(cost_center, salaries_df):

    cc = _normalize_cost_center(cost_center)

    df = salaries_df.copy()

    # ----------------------------------
    # normalize
    # ----------------------------------
    df[COL_COST_CENTER_SAL] = df[COL_COST_CENTER_SAL].apply(_normalize_cost_center)
    df[COL_MONTH] = pd.to_numeric(df[COL_MONTH], errors="coerce")
    df[COL_GL_VALUE] = pd.to_numeric(df[COL_GL_VALUE], errors="coerce").fillna(0)

    # ----------------------------------
    # derive time (previous month)
    # ----------------------------------
    today = datetime.now()

    year = str(today.year)
    prev_month = today.month - 1

    if prev_month == 0:
        prev_month = 12
        year = str(today.year - 1)

    # ----------------------------------
    # build Year keys
    # ----------------------------------
    flex_year = YEAR_PREFIX_FLEX + year
    actual_year = YEAR_PREFIX_ACTUAL + year

    # ----------------------------------
    # filter base
    # ----------------------------------
    base = df[df[COL_COST_CENTER_SAL] == cc]

    # ----------------------------------
    # YTD FILTER (<= prev month)
    # ----------------------------------
    bud_df = base[
        (base[COL_SAL_YEAR] == flex_year) &
        (base[COL_MONTH] <= prev_month)
    ]

    act_df = base[
        (base[COL_SAL_YEAR] == actual_year) &
        (base[COL_MONTH] <= prev_month)
    ]

    # ----------------------------------
    # SUM
    # ----------------------------------
    budget = bud_df[COL_GL_VALUE].sum()
    actual = act_df[COL_GL_VALUE].sum()

    return budget, actual

def compute_current_pp_ot_pct(cost_center, pay_period, prod_df):

    cc = _normalize_cost_center(cost_center)

    df = prod_df.copy()

    df[COL_COST_CENTER] = df[COL_COST_CENTER].apply(_normalize_cost_center)
    df[COL_PAY_PERIOD] = pd.to_numeric(df[COL_PAY_PERIOD], errors="coerce")
    df["Hours"] = pd.to_numeric(df["Hours"], errors="coerce").fillna(0)

    df = df[
        (df[COL_COST_CENTER] == cc) &
        (df[COL_PAY_PERIOD] == pay_period)
    ]

    if df.empty:
        return "0%"

    # numerator = Paid hours
    paid_hours = df[df["Paid/Worked"] == "P"]["Hours"].sum()

    # denominator = Worked hours
    worked_hours = df[df["Paid/Worked"] == "W"]["Hours"].sum()

    if worked_hours == 0:
        return "0%"

    pct = (paid_hours / worked_hours) * 100

    return f"{round(pct, 1)}%"


def compute_current_pp_act_vol(cost_center, pay_period, agg_df):

    cc = _normalize_cost_center(cost_center)

    row = agg_df[
        (agg_df[COL_COST_CENTER] == cc) &
        (agg_df[COL_PAY_PERIOD] == pay_period)
    ]

    if row.empty:
        return 0

    return row.iloc[0][COL_ACTUAL]


def compute_roll4_metrics(cost_center, pay_period, agg_df):

    cc = _normalize_cost_center(cost_center)

    df = agg_df[
        agg_df[COL_COST_CENTER] == cc
    ]

    # last 4 PP including current
    start_pp = max(1, pay_period - 3)

    window = df[
        (df[COL_PAY_PERIOD] >= start_pp) &
        (df[COL_PAY_PERIOD] <= pay_period)
    ]

    roll4_worked = window[COL_WORKED_FTE].sum()
    roll4_paid = window[COL_ACTUAL_FTE].sum()
    roll4_vol = window[COL_ACTUAL].sum()

    return roll4_worked, roll4_paid, roll4_vol



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

    # ------------------------------------------------------------
    # NORMALIZE KEYS
    # ------------------------------------------------------------
    df[COL_COST_CENTER] = df[COL_COST_CENTER].apply(_normalize_cost_center)
    df[COL_PAY_PERIOD] = pd.to_numeric(df[COL_PAY_PERIOD], errors="coerce")

    # ------------------------------------------------------------
    # NUMERIC ENFORCEMENT (CRITICAL)
    # ------------------------------------------------------------
    df[COL_ACTUAL] = pd.to_numeric(df[COL_ACTUAL], errors="coerce").fillna(0)
    df[COL_BUDGET] = pd.to_numeric(df[COL_BUDGET], errors="coerce").fillna(0)

    # ------------------------------------------------------------
    # YEAR FILTER (STRICT)
    # ------------------------------------------------------------
    year_str = str(target_year)

    df = df[
        df[COL_YEAR].str.endswith(year_str, na=False)
    ]

    # ------------------------------------------------------------
    # USE ONLY PROD ROWS (CORRECT DATA SOURCE)
    # ------------------------------------------------------------
    df = df[
        df[COL_YEAR].astype(str).str.startswith("PROD")
    ]

    # ------------------------------------------------------------
    # FORCE PROJECTION (REMOVE ALL EXTRA DIMENSIONS)
    # ------------------------------------------------------------
    df = df[
        [COL_COST_CENTER, COL_PAY_PERIOD, COL_ACTUAL, COL_BUDGET, COL_BUDGET_FTE,COL_ACTUAL_FTE, COL_WORKED_FTE,]
    ].copy()

    # ------------------------------------------------------------
    # AGGREGATE (SINGLE PASS)
    # ------------------------------------------------------------
    agg = (
        df
        .groupby([COL_COST_CENTER, COL_PAY_PERIOD], as_index=False)
        .agg({
            COL_ACTUAL: "sum",
            COL_BUDGET: "sum",
            COL_BUDGET_FTE: "sum",
            COL_ACTUAL_FTE: "sum",
            COL_WORKED_FTE: "sum",
        })
    )

    # ------------------------------------------------------------
    # VALIDATION (MUST BE 1 ROW PER CC + PP)
    # ------------------------------------------------------------
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
def build_context(form_fields: Dict[str, Any], cost_centers_df=None, prod_df=None,payperiod_df=None, volume_df=None, salaries_df=None) -> PRCContext:
    disclaimer_text = set_disclaimer_text()

    pay_period, pp_start_date = get_latest_completed_pp(payperiod_df)

    target_year = pp_start_date.year

    fiscal_year = str(target_year)

    cost_center = form_fields.get("cost_center")

    if not cost_center:
        raise RuntimeError("Missing cost_center")

    prod_df[COL_PAY_PERIOD] = pd.to_numeric(prod_df[COL_PAY_PERIOD], errors="coerce")

    agg_df = aggregate_prod(prod_df, target_year)

    cc_norm = _normalize_cost_center(cost_center)

    
    matches = agg_df[
        (agg_df[COL_COST_CENTER] == cc_norm) &
        (agg_df[COL_PAY_PERIOD] == pay_period)
    ]

    if matches.empty:
        raise RuntimeError(
            f"No aggregated data for CC {cost_center} at PP {pay_period}"
        )

    curr_row = matches.iloc[0]


    curr_pp_worked_fte = curr_row[COL_WORKED_FTE]
    curr_pp_paid_fte = curr_row[COL_ACTUAL_FTE]

    header_month = pp_start_date.strftime("%B %Y")

    if not header_month:
        raise RuntimeError("Invalid header_month")

    ops = gen_operational_stats(
        cost_center=cost_center,
        pay_period=pay_period,
        agg_df=agg_df
    )

    bud_pp_paid_fte = return_bud_pp_ftes(
        cost_center,
        pay_period,
        agg_df
    )

    act_pp_paid_fte = return_act_pp_ftes(
        cost_center,
        pay_period,
        agg_df
    )

    volume_description = extract_volume_description(
        cost_center,
        volume_df
    )

    budget_salaries, actual_salaries = compute_salary_metrics(
        cost_center,
        salaries_df
    )

    curr_pp_ot_pct = compute_current_pp_ot_pct(
        cost_center,
        pay_period,
        prod_df
    )

    curr_pp_act_vol = compute_current_pp_act_vol(
        cost_center,
        pay_period,
        agg_df
    )

    roll4_worked, roll4_paid, roll4_vol = compute_roll4_metrics(
        cost_center,
        pay_period,
        agg_df
    )
    


    for k, v in ops.items():
        if v is None:
            raise RuntimeError(f"Null stat: {k}")

    data = copy_from_data_screen(form_fields)
    fac = extract_facility_name(data["cost_center"], data["facility"], cost_centers_df=cost_centers_df)

    pr = gen_productivity_stats(cost_center, header_month, prod_df)

    return PRCContext(
        header_month=header_month,
        pay_period=str(pay_period),
        fiscal_year=fiscal_year,
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

        bud_pp_vol_ytd=_round0(ops["bud_pp_vol_ytd"]),
        act_pp_vol_ytd=_round0(ops["act_pp_vol_ytd"]),
        curr_pp_bud_vol=_round0(ops["curr_pp_bud_vol"]),
        bud_pp_paid_fte=_round0(bud_pp_paid_fte),
        act_pp_paid_fte=_round0(act_pp_paid_fte),
        index_ytd="",
        volume_description=volume_description,
        budget_salaries=_round0(budget_salaries),
        actual_salaries=_round0(actual_salaries),
        turnover_12mo="",
     
        curr_pp_worked_fte=_round0(curr_pp_worked_fte),
        curr_pp_paid_fte=_round0(curr_pp_paid_fte),
        curr_pp_ot_pct=curr_pp_ot_pct,
        curr_pp_act_vol=_round0(curr_pp_act_vol),
        curr_prod_index=pr["curr_prod_index"],        
        roll4_worked_fte=_round0(roll4_worked),
        roll4_paid_fte=_round0(roll4_paid),
        roll4_vol=_round0(roll4_vol),

    )


# =============================================================================
# PDF builder (called only on GenPDF&Email)
# =============================================================================
def build_pdf(ctx, out_dir):

    os.makedirs(out_dir, exist_ok=True)

    safe_cc = "".join(ch for ch in ctx.cost_center if ch.isalnum() or ch in ("-", "_")) or "CC"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = session.get("user_id", "nosession")

    pdf_path = os.path.join(out_dir, f"PRC_{safe_cc}_{session_id}_{ts}.pdf")

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        leftMargin=0.5*inch,
        rightMargin=0.5*inch,
        topMargin=0.4*inch,
        bottomMargin=0.4*inch,
    )

    styles = getSampleStyleSheet()

    normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=8.5, leading=10)
    header = ParagraphStyle("header", parent=styles["Normal"], alignment=1, fontSize=8.5)
    title  = ParagraphStyle("title", parent=styles["Title"], fontSize=12, alignment=1)
    qstyle = ParagraphStyle("q", parent=styles["Normal"], fontSize=9, leading=11)

    def p_cell(x):
        return Paragraph(str(x).replace("\n", "<br/>"), normal)

    def h_cell(x):
        return Paragraph(str(x), header)

    yellow = colors.Color(1, 0.95, 0.75)
    gray   = colors.Color(0.92, 0.92, 0.92)

    story = []

    # ✅ TITLE (fixed)
    story.append(Paragraph(
        f"Position Review Committee Business Case as of {ctx.header_month} & Pay Period {ctx.pay_period}",
        title
    ))

    story.append(Spacer(1, 6))

    # ✅ DISCLAIMER (RESTORED)
    story.append(Paragraph(
        ctx.disclaimer_text.replace("\n", "<br/>"),
        normal
    ))

    story.append(Spacer(1, 10))

    # ----------------------------------------------------------------------------
    # TOP TABLE
    # ----------------------------------------------------------------------------
    t1 = Table([
        [
            h_cell(LBL_DATE_REQUESTED),
            h_cell(LBL_COST_CENTER),
            h_cell(LBL_FACILITY),
            h_cell(LBL_COST_CENTER_NAME),
            h_cell(LBL_REQUISITIONS)
        ],
        [
            p_cell(ctx.date_requested),
            p_cell(ctx.cost_center),
            p_cell(ctx.facility),
            p_cell(ctx.cost_center_name),
            p_cell(ctx.requisitions)
        ]
    ])

    t1.setStyle([
        ("BACKGROUND",(0,0),(-1,0),yellow),
        ("BACKGROUND",(0,1),(-1,1),gray),
        ("BOX",(0,0),(-1,-1),0.8,colors.black),
        ("INNERGRID",(0,0),(-1,-1),0.5,colors.black),
    ])

    story.append(t1)
    story.append(Spacer(1, 8))

    # ----------------------------------------------------------------------------
    # POSITION TABLE (RESTORED)
    # ----------------------------------------------------------------------------
    t2 = Table([
        [
            h_cell("Position Requested"),
            h_cell("Position Title"),
            h_cell("Open FTE's"),
            h_cell("Posted FTE's"),
            h_cell("Total Requested FTE'S"),
            h_cell("emailtoAddress")
        ],
        [
            p_cell(ctx.position_requested),
            p_cell(ctx.position_title),
            p_cell(ctx.open_fte),
            p_cell(ctx.posted_fte),
            p_cell(ctx.total_requested_fte),
            p_cell(ctx.email_to)
        ]
    ])

    t2.setStyle([
        ("BACKGROUND",(0,0),(-1,0),yellow),
        ("BACKGROUND",(0,1),(-1,1),gray),
        ("BOX",(0,0),(-1,-1),0.8,colors.black),
        ("INNERGRID",(0,0),(-1,-1),0.5,colors.black),
    ])

    story.append(t2)
    story.append(Spacer(1, 10))

    # ----------------------------------------------------------------------------
    # OPERATIONAL
    # ----------------------------------------------------------------------------
    story.append(Paragraph("Operational Statistics", title))

    t3 = Table([
        [
            h_cell(f"BUD PP Vol YTD ({ctx.fiscal_year})"),
            h_cell(f"Act PP Vol YTD ({ctx.fiscal_year})"),
            h_cell("Current PP Bud Vol"),
            h_cell("Bud PP Paid FTE's"),
            h_cell("Act PP Paid FTE's"),
            h_cell("Index YTD")
        ],
        [
            p_cell(ctx.bud_pp_vol_ytd),
            p_cell(ctx.act_pp_vol_ytd),
            p_cell(ctx.curr_pp_bud_vol),
            p_cell(ctx.bud_pp_paid_fte),
            p_cell(ctx.act_pp_paid_fte),
            p_cell(ctx.index_ytd)
        ]
    ])

    t3.setStyle([
        ("BACKGROUND",(0,0),(-1,0),yellow),
        ("BACKGROUND",(0,1),(-1,1),gray),
        ("BOX",(0,0),(-1,-1),0.8,colors.black),
        ("INNERGRID",(0,0),(-1,-1),0.5,colors.black),
    ])

    story.append(t3)
    story.append(Spacer(1, 6))

    t4 = Table([
        [
            h_cell("Volume Description"),
            h_cell("Budget Salaries"),
            h_cell("Actual Salaries"),
            h_cell("12 Month Turnover")
        ],
        [
            p_cell(ctx.volume_description),
            p_cell(ctx.budget_salaries),
            p_cell(ctx.actual_salaries),
            p_cell(ctx.turnover_12mo)
        ]
    ])

    t4.setStyle([
        ("BACKGROUND",(0,0),(-1,0),yellow),
        ("BACKGROUND",(0,1),(-1,1),gray),
    ])

    story.append(t4)
    story.append(Spacer(1, 10))

    # ----------------------------------------------------------------------------
    # PRODUCTIVITY
    # ----------------------------------------------------------------------------
    story.append(Paragraph("Productivity Statistics", title))

    # ✅ current row
    t5 = Table([
        [
            h_cell("Current PP Worked FTE's"),
            h_cell("Current PP Paid FTE's"),
            h_cell("Current PP OT%"),
            h_cell("Current PP Act Vol"),
            h_cell("Current Prod Index"),
        ],
        [
            p_cell(ctx.curr_pp_worked_fte),
            p_cell(ctx.curr_pp_paid_fte),
            p_cell(ctx.curr_pp_ot_pct),
            p_cell(ctx.curr_pp_act_vol),
            p_cell(ctx.curr_prod_index)
        ]
    ])

    story.append(t5)

    # ✅ NEW ROLL 4 ROW (added)
    t6 = Table([
        [
            h_cell("Roll 4 Worked FTE's"),
            h_cell("Roll 4 Paid FTE's"),
            h_cell("Vol Roll 4 PP"),
        ],
        [
            p_cell(ctx.roll4_worked_fte),
            p_cell(ctx.roll4_paid_fte),
            p_cell(ctx.roll4_vol)
        ]
    ])

    story.append(Spacer(1, 6))
    story.append(t6)
    story.append(Spacer(1, 12))

    # ----------------------------------------------------------------------------
    # QUESTIONS (RESTORED)
    # ----------------------------------------------------------------------------
    def q_block(n, text, answer):
        story.append(Paragraph(f"<b>{n}. {text}</b>", qstyle))
        story.append(Spacer(1, 2))

        table = Table([
            [p_cell(answer if answer else " ")]
        ], colWidths=[7.5 * inch])

        table.setStyle([
            ("BOX",(0,0),(-1,-1),0.8,colors.black),
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("BOTTOMPADDING",(0,0),(-1,-1),18),
        ])

        story.append(table)
        story.append(Spacer(1, 10))

    q_block(1, "If position(s) were not filled, how would the workflow change?", ctx.q1_workflow_change)
    q_block(2, "If this is a replacement request...", ctx.q2_replacement_detail)
    q_block(3, "What current positions...", ctx.q3_absorb_work)
    q_block(4, "Is there a different skill set...", ctx.q4_skillset)
    q_block(5, "Are there other roles...", ctx.q5_similar_roles)
    q_block(6, "If a full-time position...", ctx.q6_part_time)

    doc.build(story)

    return pdf_path


# =============================================================================
# Main entrypoint called by Flask on submit (returns dict for review page)
# =============================================================================

def process(form_fields, cost_centers_df=None, prod_df=None, payperiod_df=None, volume_df=None, salaries_df=None):

    if payperiod_df is None or payperiod_df.empty:
        raise RuntimeError("PAYPERIODTABLE not loaded")

    ctx = build_context(
        form_fields,
        cost_centers_df=cost_centers_df,
        prod_df=prod_df,
        payperiod_df=payperiod_df,
        volume_df=volume_df,
        salaries_df=salaries_df
    )

    return asdict(ctx)

