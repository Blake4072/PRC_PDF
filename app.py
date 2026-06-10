import os
import logging
import time
from datetime import timedelta, date
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
)

from processScreen1DatEntry import build_pdf

from dotenv import load_dotenv

from processScreen1DatEntry import (
    get_latest_completed_pp,
    _normalize_cost_center
)

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
# Flask setup && Sentry hook
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
# Environment / Config
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
    
    """
    Normalize Cost Center key
    IN: raw cost center (string/number)
    OUT: uppercase normalized string
    RULES:
      - trim whitespace
      - remove trailing ".0"
      - strip leading zeros
    """

    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit():
        s = s.lstrip("0") or "0"
    return s.upper()

# ---------------------------------------------------------------------
# Dataset loader functions
# ---------------------------------------------------------------------
def _mssql_engine():
    
    """
    Create MSSQL connection engine
    IN: environment variables
    OUT: SQLAlchemy engine
    GOAL: centralized DB connection
    """

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
    
    """
    Load full productivity dataset
    IN: none
    OUT: DataFrame (Productivity Data)
    GOAL: ad-hoc dataset inspection (not main pipeline)
    """

    eng = _mssql_engine()
    with eng.connect() as con:
        return pd.read_sql("SELECT * FROM [dbo].[Productivity Data];", con)

# ---------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------
def ensure_user_session():
    
    """
    Initialize session structure
    IN: Flask session
    OUT: session mutated
    GUARANTEES:
      - user_id exists
      - form_data exists
      - prc_ctx exists
      - pdf_path exists
    """

    session.permanent = True
    if "user_id" not in session:
        import uuid
        session["user_id"] = str(uuid.uuid4())

    if "form_data" not in session:
        session["form_data"] = {}

    if "prc_ctx" not in session:
        session["prc_ctx"] = None

    if "pdf_path" not in session:
        session["pdf_path"] = None

# ---------------------------------------------------------------------
# Cost Center validation 
# ---------------------------------------------------------------------

def lookup_cost_center_or_raise(cost_center: str) -> dict:
    
    """
    Validate cost center via authoritative table
    IN: cost_center (string)
    OUT: {facility_desc, cost_center_desc}
    FAIL: raises ValueError if not found
    DATA SOURCE: ProdTrackerSalaries_PRC
    """

    
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
    
    """
    Full cost center validation gate
    IN: cost_center, DB engine, current_pp
    OUT: (bool, error_message)

    ENFORCES:
      1. exists in PROD dataset
      2. exists in volume mapping table
      3. has row for (Cost Center, Pay Period)

    FAIL: returns (False, reason)
    """


    cc = _normalize_cost_center(cost_center)

    q_prod = """
        SELECT 1
        FROM [DecisionSupport].[dbo].[Productivity Data]
        WHERE [Cost Center] = :cc
          AND [Year] LIKE 'PROD%'
    """

    q_vol = """
        SELECT 1
        FROM [DecisionSupport].[dbo].[ProductivityStatsVolumeDescriptions_USE]
        WHERE Dept = :cc
    """

    q_strict = """
        SELECT 1
        FROM [DecisionSupport].[dbo].[Productivity Data]
        WHERE [Cost Center] = :cc
        AND [Year] LIKE 'PROD%'
        AND [Pay Period] = :pp
    """

    with eng.connect() as con:
        exact_match = con.execute(
            text(q_strict),
            {"cc": cc, "pp": current_pp}
        ).fetchone()

    if not exact_match:
        return False, f"No data for current pay period ({current_pp})"


    with eng.connect() as con:
        prod_exists = con.execute(text(q_prod), {"cc": cc}).fetchone()
        vol_exists = con.execute(text(q_vol), {"cc": cc}).fetchone()

    if not prod_exists:
        return False, "Cost center missing in Productivity Data"

    if not vol_exists:
        return False, "Cost center missing in Volume Description table"

    return True, None

# ---------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------

def startup():
    startup_log.info("STARTUP: entered")
    os.makedirs(OUTPUT_PDF_DIR, exist_ok=True)
    startup_log.info("STARTUP: complete")


def load_known_emails(path=os.path.join("data", "emails.csv")):
    
    """
    Load persisted email list
    IN: file path
    OUT: sorted unique email list
    GOAL: populate UI dropdown
    """

    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as f:
        return sorted(
            set(line.strip() for line in f if line.strip())
        )
    

def persist_email(email, path=os.path.join("data", "emails.csv")):
    
    """
    Append email to persistence store
    IN: email string
    OUT: file write (if new)
    GOAL: maintain dropdown list
    """

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

# ---------------------------------------------------------------------
# ROUTES    
# ---------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    
    """
    Render input form
    IN: session
    OUT: form.html

    BEHAVIOR:
      - preload session data
      - set default date
      - provide known emails
    """

    ensure_user_session()
    data = session.get("form_data", {})
    emails = load_known_emails()
    if not data.get("requested_date"):
        data["requested_date"] = date.today().isoformat()
        session["form_data"] = data

    return render_template("form.html", data=data, known_emails=emails)

@app.post("/submit")
def submit():
    
    """
    Primary input handler
    IN: form POST
    OUT: redirect

    FLOW:
      1. load payperiod table
      2. compute current_pp
      3. validate cost center (lookup + strict check)
      4. persist payload to DB
      5. redirect to review

    FAIL:
      - invalid CC → redirect to form with flash
    """

    ensure_user_session()

    #preload_all()

    eng = _mssql_engine()

    with eng.connect() as con:
        payperiod_df = pd.read_sql(
            "SELECT * FROM [DecisionSupport].[dbo].[PAYPERIODTABLE_];",
            con
        )

    current_pp, _ = get_latest_completed_pp(payperiod_df)
    current_pp = int(current_pp)

    form_fields = request.form.to_dict(flat=True)
    session["form_data"] = form_fields

    try:
        info = lookup_cost_center_or_raise(form_fields.get("cost_center", ""))

        valid, msg = validate_cost_center_complete(
            form_fields.get("cost_center", ""),
            eng,
            current_pp,
        )

        if not valid:
            flash(msg, "error")
            return redirect(url_for("index"))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("index"))

    form_fields["facility"] = info["facility_desc"]

    session_id = session["user_id"]
   

    
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

    

    return redirect(url_for("review"))

@app.get("/review")
def review():
    
    """
    Protected review render
    IN: session_id
    OUT: review.html

    FLOW:
      1. load payload from DB
      2. load all required datasets:
         - cost_centers_df
         - prod_df (PROD only)
         - payperiod_df
         - volume_df
         - salaries_df
      3. call processor.process()
      4. render ctx

    FAIL:
      - no session data → redirect
      - processing mismatch → redirect with error
    """

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
        prod_df = pd.read_sql("""SELECT * FROM [dbo].[Productivity Data] WHERE [Cost Center] = ? AND [Year] LIKE 'PROD%'""",con,params=(payload["cost_center"],))
        payperiod_df=pd.read_sql("SELECT * FROM [dbo].[PAYPERIODTABLE_];",con)
        volume_df = pd.read_sql("SELECT Dept, Stat_Desc FROM [DecisionSupport].[dbo].[ProductivityStatsVolumeDescriptions_USE]",con)
        salaries_df = pd.read_sql("SELECT * FROM [DecisionSupport].[dbo].[ProdTrackerSalaries_PRC]",con)

    try:
        ctx_dict = processor.process(
            payload,
            cost_centers_df=cost_centers_df,
            prod_df=prod_df,
            payperiod_df=payperiod_df,
            volume_df=volume_df,
            salaries_df=salaries_df
        )
    except RuntimeError as e:
        flash(str(e), "error")
        return redirect(url_for("index"))
    

    return render_template(REVIEW_TEMPLATE, ctx=ctx_dict)

from flask import redirect, url_for, flash, session

@app.post("/genpdf_email")
def genpdf_email():
    
    """Generate PDF and email
    IN: session_id
    OUT: review.html

    FLOW:
      1. reload payload from DB
      2. rebuild datasets + context
      3. build PDF
      4. persist email
      5. send SMTP email (optional)

    FAIL:
      - DB read failure
      - context failure
      - PDF failure
      - SMTP failure
    """

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
        from processScreen1DatEntry import PRCContext

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
            prod_df = pd.read_sql("""SELECT * FROM [dbo].[Productivity Data] WHERE [Cost Center] = ? AND [Year] LIKE 'PROD%'""", con, params=(payload["cost_center"],))
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

@app.post("/clear_all")
def clear_all():
    
    """
    Reset session state
    IN: session
    OUT: redirect to index

    EFFECT:
      clears all stored user data
    """

    ensure_user_session()
    session["form_data"] = {}
    session["prc_ctx"] = None
    session["pdf_path"] = None
    session.modified = True
    return redirect(url_for("index"))


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
