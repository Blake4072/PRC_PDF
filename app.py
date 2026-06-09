r"""
app.py

PURPOSE
-------
This module defines:
  - A Flask web application (routes: "/", "/submit", "/review", "/genpdf_email", "/clear_all")

RUN MODES
---------
(A) DEVELOPMENT:
    python app.py
    -> listens on port 5000

(B) PRODUCTION (recommended):
    python run_waitress.py
    -> calls app.startup() FIRST (blocking preload)
    -> then Waitress listens on port 8000

NEW UX FLOW (UPDATED)
---------------------
Screen 1 ("/") -> Submit (POST "/submit") -> Protected Review (GET "/review")

Protected Review page shows the same information the PDF would contain (read-only),
and provides two buttons:
  - GenPDF & Email (POST "/genpdf_email")
  - Clear (POST "/clear_all") -> returns to first screen

CRITICAL REQUIREMENT
--------------------
Cost Center validation must happen FIRST in /submit.
If cost center is invalid, redirect back to "/" with an error message.
"""

import os
import logging
import threading
import time
from datetime import timedelta, date, datetime
from zoneinfo import ZoneInfo
import json

import pandas as pd
from sqlalchemy import create_engine, text

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_file,
)

from processScreen1DatEntry import process, build_pdf

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("prcapp")

import time

startup_log = logging.getLogger("startup")

def step(name, fn):
    startup_log.info("START %s", name)
    t0 = time.time()
    try:
        result = fn()
        startup_log.info("END %s (%.2fs)", name, time.time() - t0)
        return result
    except Exception:
        startup_log.exception("FAIL %s", name)
        raise

# ---------------------------------------------------------------------
# Flask setup
# ---------------------------------------------------------------------

import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from flask import Flask


import urllib3
urllib3.disable_warnings()

from sentry_sdk.transport import HttpTransport

def _get_pool_options(self, *args, **kwargs):
    
    return {
            "num_pools": 2,
            "cert_reqs": "CERT_NONE",
        }


HttpTransport._get_pool_options = _get_pool_options


sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    integrations=[FlaskIntegration()],   
    send_default_pii=False,
)
print("SENTRY_DSN =", os.environ.get("SENTRY_DSN"))

app = Flask(__name__)
app.secret_key = os.environ.get("PRC_FLASK_SECRET_KEY", "change-this-secret-key")
app.permanent_session_lifetime = timedelta(hours=8)

# ---------------------------------------------------------------------
# Environment / Config (can be overridden via env vars)
# ---------------------------------------------------------------------

FABRIC_SERVER = os.environ.get("PRC_FABRIC_SERVER")

FABRIC_DATABASE = os.environ.get("PRC_FABRIC_DB")
FABRIC_TABLE = os.environ.get("PRC_FABRIC_TABLE")

MSSQL_SERVER = os.environ.get("PRC_MSSQL_SERVER")
MSSQL_DATABASE = os.environ.get("PRC_MSSQL_DB")
MSSQL_TABLE = os.environ.get("PRC_MSSQL_TABLE")

DAILY_REFRESH_TIME = os.environ.get("PRC_DAILY_REFRESH", "05:00")
LOCAL_TZ = ZoneInfo("America/Chicago")

# Where PDFs are written when GenPDF is clicked
OUTPUT_PDF_DIR = os.environ.get("PRC_OUTPUT_PDF_DIR", os.path.join(os.getcwd(), "output_pdfs"))

REVIEW_TEMPLATE = "review.html"

# ---------------------------------------------------------------------
# Required Environment Variables (Email)
# ---------------------------------------------------------------------
# edit Blake Bozarth 5/4/26 config placeholders for server based mailing


PRC_SMTP_HOST = os.environ.get("PRC_SMTP_HOST",)
PRC_SMTP_PORT = int(os.environ.get("PRC_SMTP_PORT"))
PRC_EMAIL_FROM = os.environ.get("PRC_EMAIL_FROM",)


# ---------------------------------------------------------------------
# In-memory cache shared across users / requests
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# Helpers for normalization and column picking
# ---------------------------------------------------------------------
def _normalize_cost_center(x: str) -> str:
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit():
        s = s.lstrip("0") or "0"
    return s.upper()

# ---------------------------------------------------------------------
# Dataset loader functions
# ---------------------------------------------------------------------

# trusted SQL connection WILL NOT WORK ON SERVER
# needs a user + pass auth layer that executes within the docker image where the source code is "hosted"
def _mssql_engine():
    driver = os.environ.get("PRC_MSSQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    server = os.environ.get("PRC_MSSQL_SERVER", "QYVMSTNDPDSQL02")
    database = os.environ.get("PRC_MSSQL_DB", "DecisionSupport")
    user = os.environ.get("PRC_MSSQL_USER")
    password = os.environ.get("PRC_MSSQL_PASSWORD")


    
    url = (
             f"mssql+pyodbc://{user}:{password}@{server}/{database}"
             f"?driver={driver.replace(' ', '+')}"
             f"&Encrypt=yes"
             f"&TrustServerCertificate=yes"
         )


    return create_engine(url, fast_executemany=True, pool_pre_ping=True, pool_size=5, max_overflow=10, future=True,)



def load_mssql_table() -> pd.DataFrame:
    eng = _mssql_engine()
    with eng.connect() as con:
        return pd.read_sql("SELECT * FROM [dbo].[Productivity Data];", con)

# ---------------------------------------------------------------------
# Master preload/refresh functions
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------
def ensure_user_session():
    session.permanent = True
    if "user_id" not in session:
        import uuid
        session["user_id"] = str(uuid.uuid4())

    if "form_data" not in session:
        session["form_data"] = {}

    # Review context dict (stored after submit)
    if "prc_ctx" not in session:
        session["prc_ctx"] = None

    # last generated pdf path (optional)
    if "pdf_path" not in session:
        session["pdf_path"] = None

# ---------------------------------------------------------------------
# Cost Center validation FIRST (uses preloaded cache)
# ---------------------------------------------------------------------

#******************************************************edit Blake Bozarth 4/29 (facility is now derived from the authoritative cost‑center lookup instead of user input; cost center is the unique key and facility is a dependent attribute.)
def lookup_cost_center_or_raise(cost_center: str) -> dict:
    
    cc = _normalize_cost_center(cost_center)

    eng = _mssql_engine()

    query = """
        SELECT TOP 1
            [Cost_Center] AS cost_center,
            [Facility_Desc] AS facility_desc,
            [Cost_Center_Desc] AS cost_center_desc
        FROM [DecisionSupport].[dbo].[ProdTrackerSalaries_PRC]
        WHERE [Cost_Center] = ?
    """

    import pandas as pd

    with eng.connect() as con:
        df = pd.read_sql(query, con, params=(cc,))

    if df.empty:
        raise ValueError(f"Invalid cost center: {cost_center}")

    row = df.iloc[0]

    return {
        "facility_desc": str(row["facility_desc"]),
        "cost_center_desc": str(row["cost_center_desc"]),
    }

def validate_cost_center_complete(cost_center, eng, current_pp):

    cc = _normalize_cost_center(cost_center)

    # --- 1: exists in PROD (any data) ---
    q_prod = """
        SELECT 1
        FROM [DecisionSupport].[dbo].[Productivity Data]
        WHERE [Cost Center] = ?
          AND [Year] LIKE 'PROD%'
    """

    # --- 2: exists in Volume ---
    q_vol = """
        SELECT 1
        FROM [DecisionSupport].[dbo].[ProductivityStatsVolumeDescriptions_USE]
        WHERE Dept = ?
    """

    # --- 3: exists in PROD for CURRENT PP ---
    q_pp = """
        SELECT 1
        FROM [DecisionSupport].[dbo].[Productivity Data]
        WHERE [Cost Center] = ?
          AND [Year] LIKE 'PROD%'
          AND [Pay Period] = ?
    """

    with eng.connect() as con:
        prod_exists = con.execute(text(q_prod), (cc,)).fetchone()
        vol_exists = con.execute(text(q_vol), (cc,)).fetchone()
        pp_exists = con.execute(text(q_pp), (cc, current_pp)).fetchone()

    if not prod_exists:
        return False, "Cost center missing in Productivity Data"

    if not vol_exists:
        return False, "Cost center missing in Volume Description table"

    if not pp_exists:
        return False, f"No data for current pay period ({current_pp})"

    return True, None








# ---------------------------------------------------------------------
# Preload orchestration (synchronous startup)
# ---------------------------------------------------------------------

def startup():
    startup_log.info("STARTUP: entered")
    #preload_all()
    #_validate_all_datasets_or_raise()
    os.makedirs(OUTPUT_PDF_DIR, exist_ok=True)
    startup_log.info("STARTUP: complete")

#Email dropdown helpers #############################

def load_known_emails(path=os.path.join("data", "emails.csv")):
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as f:
        return sorted(
            set(line.strip() for line in f if line.strip())
        )
    

def persist_email(email, path=os.path.join("data", "emails.csv")):
    email = email.strip().lower()
    if not email:
        return

    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = {line.strip() for line in f if line.strip()}

    if email not in existing:
        with open(path, "a", encoding="utf-8") as f:
            f.write(email + "\n")
###############################################################
# ---------------------------------------------------------------------
# ROUTES    
# ---------------------------------------------------------------------
#edit Blake Bozarth 5/4/26 Active Directory emails get passed to webpage for dropdown
@app.route("/", methods=["GET"])
def index():
    ensure_user_session()
    data = session.get("form_data", {})
    emails = load_known_emails()
    if not data.get("requested_date"):
        data["requested_date"] = date.today().isoformat()
        session["form_data"] = data

    return render_template("form.html", data=data, known_emails=emails)

@app.post("/submit")
def submit():
    ensure_user_session()

    #preload_all()

    form_fields = request.form.to_dict(flat=True)
    session["form_data"] = form_fields

    try:
        info = lookup_cost_center_or_raise(form_fields.get("cost_center", ""))

        valid, msg = validate_cost_center_complete(
            form_fields.get("cost_center", ""),
            eng,
            current_pp
        )

        if not valid:
            flash(msg, "error")
            return redirect(url_for("index"))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("index"))

    form_fields["facility"] = info["facility_desc"]

    session_id = session["user_id"]
    eng = _mssql_engine()

    
    payload = form_fields.copy()

    payload["facility"] = info["facility_desc"]
    payload["facility_desc"] = info["facility_desc"]
    payload["cost_center_desc"] = info["cost_center_desc"]


    payload_json = json.dumps(payload)

    with eng.begin() as con:
        log.warning("DB WRITE: deleting existing session row for %s", session_id)
        con.execute(
            text("DELETE FROM prc_sessions WHERE session_id = :sid"),
            {"sid": session_id},
        )

        log.warning("DB WRITE: inserting new session row for %s", session_id)
        con.execute(
            text("""
                INSERT INTO prc_sessions (session_id, payload)
                VALUES (:sid, :payload)
            """),
            {"sid": session_id, "payload": payload_json},
        )

    with eng.connect() as con:
        payperiod_df = pd.read_sql(
            "SELECT * FROM [DecisionSupport].[dbo].[PAYPERIODTABLE_];",
            con
        )

    from processScreen1DatEntry import get_latest_completed_pp

    current_pp, _ = get_latest_completed_pp(payperiod_df)

    return redirect(url_for("review"))

@app.get("/review")
def review():
    ensure_user_session()

    session_id = session["user_id"]
    eng = _mssql_engine()

    with eng.connect() as con:
        log.warning("DB READ: fetching session row for %s", session_id)

        row = con.execute(
            text("SELECT payload FROM prc_sessions WHERE session_id = :sid"),
            {"sid": session_id},
        ).fetchone()

    if not row:
        flash("No review data found. Please complete the form.", "error")
        return redirect(url_for("index"))

    payload = json.loads(row[0])

    import processScreen1DatEntry as processor
    from processScreen1DatEntry import gen_operational_stats

    # authoritative lookup table
    
    query = """
        SELECT
            [Cost_Center] AS [Cost Center],
            [Facility_Desc] AS [Facility Desc],
            [Cost_Center_Desc] AS [Cost Center Desc],
            UPPER(LTRIM(RTRIM(REPLACE([Cost_Center], '.0', '')))) AS _CC_NORM
        FROM [DecisionSupport].[dbo].[ProdTrackerSalaries_PRC]
    """


    with eng.connect() as con:
        cost_centers_df = pd.read_sql(query, con)
        prod_df=pd.read_sql("SELECT * FROM [dbo].[Productivity Data] WHERE [Cost Center] = ? ", con, params=(payload["cost_center"],))
        payperiod_df=pd.read_sql("SELECT * FROM [dbo].[PAYPERIODTABLE_];",con)
        volume_df = pd.read_sql("SELECT Dept, Stat_Desc FROM [DecisionSupport].[dbo].[ProductivityStatsVolumeDescriptions_USE]",con)
        salaries_df = pd.read_sql("SELECT * FROM [DecisionSupport].[dbo].[ProdTrackerSalaries_PRC]",con)


    ctx_dict = processor.process(
        payload,
        cost_centers_df=cost_centers_df,
        prod_df=prod_df,
        payperiod_df=payperiod_df,
        volume_df=volume_df,
        salaries_df=salaries_df
    )

    return render_template(REVIEW_TEMPLATE, ctx=ctx_dict)


# edit Blake Bozarth 5/4/26 pulls the data object --> builds pdf from it --> attempts email

from flask import redirect, url_for, flash, session
import smtplib
#from emailer import send_prc_pdf

@app.post("/genpdf_email")
def genpdf_email():
    log.error("GENPDF: ENTRYPOINT")

    ensure_user_session()
    log.error("GENPDF: after ensure_user_session")

    session_id = session["user_id"]
    eng = _mssql_engine()

    # ---- LOAD FROM DB -------------------------------------------------
    try:
        with eng.connect() as con:
            log.error("GENPDF: DB READ start for session_id=%s", session_id)

            row = con.execute(
                text("SELECT payload FROM prc_sessions WHERE session_id = :sid"),
                {"sid": session_id},
            ).fetchone()

        if not row:
            log.error("GENPDF: EXIT early - no DB row")
            flash("Session expired. Please submit the form again.", "error")
            return redirect(url_for("index"))

        payload = json.loads(row[0])
        log.error("GENPDF: payload loaded from DB")

    except Exception as e:
        log.exception("GENPDF: DB READ FAILED")
        flash("Failed to retrieve session data.", "error")
        return redirect(url_for("index"))

    # ---- REBUILD CONTEXT ----------------------------------------------
    try:
        import processScreen1DatEntry as processor
        from processScreen1DatEntry import gen_operational_stats, PRCContext

        query = """
            SELECT
                [Cost_Center] AS [Cost Center],
                [Facility_Desc] AS [Facility Desc],
                [Cost_Center_Desc] AS [Cost Center Desc],
                UPPER(LTRIM(RTRIM(REPLACE([Cost_Center], '.0', '')))) AS _CC_NORM
            FROM [DecisionSupport].[dbo].[ProdTrackerSalaries_PRC]
        """


        with eng.connect() as con:
            cost_centers_df = pd.read_sql(query, con)
            prod_df=pd.read_sql("SELECT * FROM [dbo].[Productivity Data] WHERE [Cost Center] = ? ", con, params=(payload["cost_center"],))
            payperiod_df = pd.read_sql("SELECT * FROM [dbo].[PAYPERIODTABLE_];",con)
            volume_df = pd.read_sql("SELECT Dept, Stat_Desc FROM [DecisionSupport].[dbo].[ProductivityStatsVolumeDescriptions_USE]",con)
            salaries_df = pd.read_sql("SELECT * FROM [DecisionSupport].[dbo].[ProdTrackerSalaries_PRC]",con)




        ctx_dict = processor.process(
            payload,
            cost_centers_df=cost_centers_df,
            prod_df=prod_df,
            payperiod_df=payperiod_df,
            volume_df=volume_df,
            salaries_df=salaries_df
        )

        log.error("GENPDF: ctx rebuilt from DB payload")

    except Exception as e:
        log.exception("GENPDF: CONTEXT BUILD FAILED")
        flash("Failed to rebuild context.", "error")
        return redirect(url_for("index"))

    # ---- PDF ----------------------------------------------------------
    try:
        log.error("GENPDF: about to call build_pdf")

        ctx = PRCContext(**ctx_dict)
        pdf_path = build_pdf(ctx, OUTPUT_PDF_DIR)

        log.error("GENPDF: build_pdf returned path=%s", pdf_path)

        persist_email(ctx.email_to)
        flash("PDF generated successfully.", "success")

    except Exception as e:
        log.exception("GENPDF: PDF GENERATION FAILED")
        flash("PDF generation failed.", "error")
        flash(str(e), "hint")
        return render_template(REVIEW_TEMPLATE, ctx=ctx_dict)

    # ---- EMAIL --------------------------------------------------------
    recipient_email = ctx_dict.get("email_to")

    if recipient_email:
        try:
            send_via_smtp(recipient_email, pdf_path)
            flash("PDF emailed via SMTP.", "success")
        except Exception as e:
            flash("PDF generated, but email failed.", "error")
            flash(str(e), "hint")
    else:
        flash("PDF generated successfully. Email not sent.", "hint")

    log.error("GENPDF: EXIT normal")
    return render_template(REVIEW_TEMPLATE, ctx=ctx_dict)


''' (old pdf generation + save to local data path)
    import processScreen1DatEntry as processor
    from processScreen1DatEntry import PRCContext

    ctx = PRCContext(**ctx_dict)
    pdf_path = processor.build_pdf(ctx, out_dir=OUTPUT_PDF_DIR)

    session["pdf_path"] = pdf_path
    session.modified = True

    # TODO: hook in real email sender
    # import SendEmailPDF
    # SendEmailPDF.send(pdf_path, ctx.email_to)

    log.info("Generated PDF: %s", pdf_path)
    log.info("Email placeholder: would send to %s", ctx.email_to)
    flash(f"Generated PDF and emailed to: {ctx.email_to}", "success")

    # Return to first screen (your request)
    return redirect(url_for("index"))
'''


@app.post("/clear_all")
def clear_all():
    ensure_user_session()
    session["form_data"] = {}
    session["prc_ctx"] = None
    session["pdf_path"] = None
    session.modified = True
    return redirect(url_for("index"))

################################## edit by Blake Bozarth 4/29/26 lines: ######################################

#------------------------------------------
#Email Helper
#------------------------------------------

def send_via_smtp(recipient_email, pdf_path):
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = "PRC Submission"
    msg["From"] = PRC_EMAIL_FROM
    msg["To"] = recipient_email
    msg.set_content("Please find attached PRC document.")

    with open(pdf_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=os.path.basename(pdf_path),
        )

    with smtplib.SMTP(PRC_SMTP_HOST, PRC_SMTP_PORT) as smtp:
        smtp.send_message(msg)


# --------------------------------------------------
# TEMPORARY CONCURRENCY TEST CONTEXT (DEV ONLY)
# --------------------------------------------------


@app.post("/_test_genpdf")
def _test_genpdf():
    ensure_user_session()
    session["prc_ctx"] = _FAKE_PRC_CTX.copy()
    return genpdf_email()


_FAKE_PRC_CTX = {
    "header_month": "May 2026",
    "pay_period": "10",
    "disclaimer_text": "TEST CONTEXT – NOT REAL DATA",

    "date_requested": "2026-05-11",
    "cost_center": "10101",
    "facility": "BLESSING HOSPITAL",
    "cost_center_name": "Nursing Administration",
    "requisitions": "1",

    "position_requested": "RN",
    "position_title": "Registered Nurse",
    "open_fte": "0.5",
    "posted_fte": "0.0",
    "total_requested_fte": "0.5",
    "email_to": "test@example.com",

    "q1_workflow_change": "No",
    "q2_replacement_detail": "N/A",
    "q3_absorb_work": "No",
    "q4_skillset": "Clinical",
    "q5_similar_roles": "Yes",
    "q6_part_time": "Yes",

    "bud_pp_vol_ytd": "100",
    "act_pp_vol_ytd": "95",
    "curr_pp_bud_vol": "10",
    "bud_pp_paid_fte": "1.2",
    "act_pp_paid_fte": "1.4",
    "index_ytd": "0.95",
    "volume_description": "TEST VOLUME",
    "budget_salaries": "120000",
    "actual_salaries": "118500",
    "turnover_12mo": "5%",

    "curr_pp_worked_fte": "1.4",
    "curr_pp_paid_fte": "1.3",
    "curr_pp_ot_pct": "2%",
    "curr_pp_act_vol": "9",
    "curr_prod_index": "0.9",
}



# ---------------------------------------------------------------------
# DEV ENTRYPOINT ONLY (python app.py)
# ---------------------------------------------------------------------
if __name__ == "__main__":
    try:
        startup()
    except Exception as e:
        log.exception("Startup aborted: %s", e)
        raise SystemExit(1)

    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
