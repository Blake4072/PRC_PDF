r"""
app.py

PURPOSE
-------
This module defines:
  - A Flask web application (routes: "/", "/submit", "/review", "/genpdf_email", "/clear_all")
  - Startup data preloading into a shared in-memory cache (_CACHE)
  - A background thread that refreshes all datasets daily at 05:00 AM (Chicago)

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

import pandas as pd
from sqlalchemy import create_engine

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
app = Flask(__name__)
app.secret_key = os.environ.get("PRC_FLASK_SECRET_KEY", "change-this-secret-key")
app.permanent_session_lifetime = timedelta(hours=8)

# ---------------------------------------------------------------------
# Environment / Config (can be overridden via env vars)
# ---------------------------------------------------------------------
CSV_CC_LOOKUP = os.environ.get(
    "PRC_CC_CSV",
    r"\\ss01\groups$\decs\Tableau\Productivity\Prod Tracker Salaries.csv",
)

VOLDESC_XLSX = os.environ.get(
    "PRC_VOLDESC_XLSX",
    r"\\ss01\groups$\decs\Tableau\Productivity\Productivity Stats Volume Descriptions.xlsx",
)

PAYPERIOD_XLSX = os.environ.get(
    "PRC_PAYPERIOD_XLSX",
    r"\\SS01\groups$\decs\Tableau\Productivity\PAYPERIODTABLE.xlsx",
)
PAYPERIOD_SHEET = os.environ.get("PRC_PAYPERIOD_SHEET", "PAYPERIODTABLE")

FABRIC_SERVER = os.environ.get(
    "PRC_FABRIC_SERVER",
    "lmxximtgum2ehbbgzpmsepntha-doih7hxdnwru5n7buhf5sjknle.datawarehouse.fabric.microsoft.com",
)
FABRIC_DATABASE = os.environ.get("PRC_FABRIC_DB", "SAM")
FABRIC_TABLE = os.environ.get("PRC_FABRIC_TABLE", "HR.Termination_DetailBASE")

MSSQL_SERVER = os.environ.get("PRC_MSSQL_SERVER", "QYVMSTNDPDSQL02")
MSSQL_DATABASE = os.environ.get("PRC_MSSQL_DB", "DecisionSupport")
MSSQL_TABLE = os.environ.get("PRC_MSSQL_TABLE", "dbo.Productivity Data")

DAILY_REFRESH_TIME = os.environ.get("PRC_DAILY_REFRESH", "05:00")
LOCAL_TZ = ZoneInfo("America/Chicago")

# Where PDFs are written when GenPDF is clicked
OUTPUT_PDF_DIR = os.environ.get("PRC_OUTPUT_PDF_DIR", os.path.join(os.getcwd(), "output_pdfs"))

# ---------------------------------------------------------------------
# Required Environment Variables (Email)
# ---------------------------------------------------------------------
# edit Blake Bozarth 5/4/26 config placeholders for server based mailing


PRC_SMTP_HOST = os.environ.get("PRC_SMTP_HOST", "exchange.adbcs.blessinghospital.com")
PRC_SMTP_PORT = int(os.environ.get("PRC_SMTP_PORT", "25"))
PRC_EMAIL_FROM = os.environ.get("PRC_EMAIL_FROM", "noreply@blessinghospital.com")


# ---------------------------------------------------------------------
# In-memory cache shared across users / requests
# ---------------------------------------------------------------------
_CACHE_LOCK = threading.RLock()
_CACHE = {
    "cost_centers": None,         # from CSV_CC_LOOKUP
    "termination_detail": None,   # from FABRIC_TABLE
    "productivity_data": None,    # from MSSQL_TABLE
    "payperiod_table": None,      # from PAYPERIOD_XLSX
    "vol_desc_table": None,       # from VOLDESC_XLSX
    "ad_recipient_emails": None,  # from Active Directory            #Blake Bozarth edit 5/4/26
    "loaded_at": None,            # timestamp of last successful preload
}
'''
def _validate_required_env_or_raise():
    missing = [k for k in REQUIRED_ENV_VARS if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
        )
'''
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

def _normalize_text(x: str) -> str:
    return str(x).strip().upper()

def _pick_column(df: pd.DataFrame, candidates) -> str:
    colmap = {c.upper().replace(" ", "").replace("_", ""): c for c in df.columns}
    for c in candidates:
        k = c.upper().replace(" ", "").replace("_", "")
        if k in colmap:
            return colmap[k]
    raise ValueError(
        f"Missing required column. Expected one of {candidates}. Present: {list(df.columns)}"
    )

# ---------------------------------------------------------------------
# Dataset loader functions
# ---------------------------------------------------------------------
def load_cost_centers_csv() -> pd.DataFrame:
    if not os.path.exists(CSV_CC_LOOKUP):
        raise FileNotFoundError(f"Cost Center CSV not found: {CSV_CC_LOOKUP}")

    df = pd.read_csv(
        CSV_CC_LOOKUP,
        dtype=str,
        keep_default_na=False,
       encoding="utf-8-sig",
    ).fillna("")

    cc   = _pick_column(df, ["Cost Center", "CostCenter", "Cost_Center", "CC"])
    fac  = _pick_column(df, ["Facility Desc", "Facility", "FacilityDesc"])
    desc = _pick_column(df, ["Cost Center Desc", "CostCenterDesc", "Description"])

    df["_CC_NORM"]  = df[cc].map(_normalize_cost_center)
    df["_FAC_NORM"] = df[fac].map(_normalize_text)
    df = df.rename(columns={cc: "Cost Center", fac: "Facility Desc", desc: "Cost Center Desc"})

    log.warning(
        "KNOWN COST CENTERS SAMPLE: %s",
        df["_CC_NORM"].drop_duplicates().head(20).tolist()
    )

    return df[["Cost Center", "Facility Desc", "Cost Center Desc", "_CC_NORM", "_FAC_NORM"]]

def load_payperiod_excel() -> pd.DataFrame:
    if not os.path.exists(PAYPERIOD_XLSX):
        raise FileNotFoundError(f"PAYPERIOD Excel not found: {PAYPERIOD_XLSX}")
    return pd.read_excel(PAYPERIOD_XLSX, sheet_name=PAYPERIOD_SHEET, dtype=str).fillna("")

def load_vol_desc_excel() -> pd.DataFrame:
    if not os.path.exists(VOLDESC_XLSX):
        raise FileNotFoundError(f"Volume Descriptions Excel not found: {VOLDESC_XLSX}")
    return pd.read_excel(VOLDESC_XLSX, dtype=str).fillna("")

def _fabric_engine():
################################## edit by Blake Bozarth 4/28/26 lines: 179 - 190 (migrate to ODBC 18 from ODBC 17)######################################
    driver = os.environ.get("PRC_FABRIC_ODBC_DRIVER", "ODBC Driver 18 for SQL Server")

    server = FABRIC_SERVER
    database = FABRIC_DATABASE

    url = (
        f"mssql+pyodbc://@{server}:1433/{database}"
        f"?driver={driver.replace(' ', '+')}"
        f"&authentication=ActiveDirectoryInteractive"
        f"&Encrypt=yes"
        f"&TrustServerCertificate=yes"
    )
    return create_engine(url, fast_executemany=True)
'''
def load_fabric_table() -> pd.DataFrame:
    eng = _fabric_engine()
    with eng.connect() as con:
        return pd.read_sql(f"SELECT * FROM {FABRIC_TABLE};", con)
'''
def load_fabric_table() -> pd.DataFrame:
    return pd.DataFrame()

# trusted SQL connection WILL NOT WORK ON SERVER
# needs a user + pass auth layer that executes within the docker image where the source code is "hosted"
def _mssql_engine():
    driver = os.environ.get("PRC_MSSQL_ODBC_DRIVER", "SQL Server")
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


    return create_engine(url, fast_executemany=True)



def load_mssql_table() -> pd.DataFrame:
    eng = _mssql_engine()
    with eng.connect() as con:
        return pd.read_sql("SELECT * FROM [dbo].[Productivity Data];", con)

# ---------------------------------------------------------------------
# Master preload/refresh functions
# ---------------------------------------------------------------------

def preload_all():
    cc_df   = load_cost_centers_csv()
    ppp_df  = load_payperiod_excel()
    #ad_emails = fetch_ad_recipient_emails()
    fab_df = load_fabric_table()
    vol_df  = load_vol_desc_excel()

########################################################################
    engine = _mssql_engine()
    
    import time

    with engine.connect() as con:
        log.info("Attempting MSSQL query against table: %s", MSSQL_TABLE)

        t0 = time.time()

        prod_df = pd.read_sql(
            f"SELECT TOP (100) * FROM [dbo].[Productivity Data]",
            con
        )

        dt = time.time() - t0

        log.info("MSSQL query succeeded; rows=%d time=%.3fs", len(prod_df), dt)
        log.info("Productivity DB columns: %s", prod_df.columns.tolist())


########################################################################

    with _CACHE_LOCK:
        _CACHE["cost_centers"]       = cc_df
        _CACHE["payperiod_table"]    = ppp_df
        #_CACHE["ad_recipient_emails"] = ad_emails
        _CACHE["termination_detail"] = fab_df
        _CACHE["productivity_data"]  = prod_df
        _CACHE["vol_desc_table"]     = vol_df
        _CACHE["loaded_at"]          = datetime.now(tz=LOCAL_TZ)

    log.info(
        "Datasets preloaded. Rows => cost_centers=%s, payperiod=%s, termination_detail=%s, productivity_data=%s, vol_desc=%s",
        len(cc_df), len(ppp_df), len(fab_df), len(prod_df), len(vol_df)
    )

'''
def preload_all():
    cc_df = step("cost_centers_csv", load_cost_centers_csv)
    ppp_df = step("payperiod_excel", load_payperiod_excel)
    ad_emails = step("ad_lookup", fetch_ad_recipient_emails)
    fab_df = step("fabric_sql", load_fabric_table)
    vol_df = step("vol_desc_excel", load_vol_desc_excel)

    prod_df = step(
        "productivity_csv",
        lambda: pd.read_csv(CSV_CC_LOOKUP, dtype=str).fillna("")
    )

    prod_df["GL Month Value"] = (
        prod_df["GL Month Value"]
        .str.replace(",", "", regex=False)
        .astype(float)
    )

    with _CACHE_LOCK:
        _CACHE["cost_centers"] = cc_df
        _CACHE["payperiod_table"] = ppp_df
        _CACHE["ad_recipient_emails"] = ad_emails
        _CACHE["termination_detail"] = fab_df
        _CACHE["vol_desc_table"] = vol_df
        _CACHE["productivity_data"] = prod_df
        _CACHE["loaded_at"] = datetime.now(tz=LOCAL_TZ)
'''
def get_cache_status():
    with _CACHE_LOCK:
        cc   = _CACHE["cost_centers"]
        ppp  = _CACHE["payperiod_table"]
        fab  = _CACHE["termination_detail"]
        prod = _CACHE["productivity_data"]
        vol  = _CACHE["vol_desc_table"]
        ts   = _CACHE["loaded_at"]

################################## edit by Blake Bozarth 4/27/26 lines: 247, 254, 255######################################

    def _info(df, allow_empty = False):
        return {"loaded": df is not None and (allow_empty or not getattr(df, "empty", True)),
                "rows": (len(df) if df is not None else 0)}

    return {
        "loaded_at": (ts.isoformat() if ts else None),
        "cost_centers": _info(cc),
        "payperiod_table": _info(ppp),
        "termination_detail": _info(fab, allow_empty=True),
        "productivity_data": _info(prod, allow_empty=True),
        "vol_desc_table": _info(vol),
    }

def _validate_all_datasets_or_raise():
    status = get_cache_status()
    required = ["cost_centers", "payperiod_table", "termination_detail", "productivity_data", "vol_desc_table"]
    missing = [k for k in required if not status[k]["loaded"]]
    if missing:
        details = {k: status[k] for k in required}
        raise RuntimeError(f"Startup failed: required datasets not loaded: {missing}. Status={details}")

def _seconds_until_next_refresh() -> float:
    hh, mm = map(int, DAILY_REFRESH_TIME.split(":"))
    now = datetime.now(tz=LOCAL_TZ)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target = (target + timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return (target - now).total_seconds()

def start_daily_refresh_thread():
    def _worker():
        while True:
            wait_s = _seconds_until_next_refresh()
            log.info("Daily refresh scheduled; sleeping %.1f seconds until %s.", wait_s, DAILY_REFRESH_TIME)
            time.sleep(max(wait_s, 1.0))
            try:
                log.info("Starting scheduled daily refresh.")
                preload_all()
                _validate_all_datasets_or_raise()
                log.info("Scheduled daily refresh complete.")
            except Exception as e:
                log.exception("Scheduled daily refresh failed: %s", e)
            time.sleep(60)

    t = threading.Thread(target=_worker, daemon=True, name="DailyRefresh")
    t.start()
    log.info("Daily refresh thread started (scheduled for %s America/Chicago).", DAILY_REFRESH_TIME)

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
'''
def lookup_cost_center_or_raise(cost_center: str) -> dict:
    """
    Uses preloaded _CACHE["cost_centers"] (from Prod Tracker Salaries CSV).
    Returns:
      {"facility_desc": "...", "cost_center_desc": "..."}
    Raises:
      ValueError if lookup table missing or cost center invalid.
    """
    cc_norm = _normalize_cost_center(cost_center)

    with _CACHE_LOCK:
        df = _CACHE.get("cost_centers")

    if df is None or getattr(df, "empty", True):
        raise ValueError("Cost center lookup table is not loaded. (Startup preload issue)")

    if "_CC_NORM" not in df.columns:
        raise ValueError("Cost center lookup table missing _CC_NORM column. (Unexpected)")

    hit = df.loc[df["_CC_NORM"] == cc_norm]
    if hit.empty:
        raise ValueError(f"Invalid cost center: {cost_center}")

    row = hit.iloc[0]
    return {
        "facility_desc": str(row.get("Facility Desc", "")).strip(),
        "cost_center_desc": str(row.get("Cost Center Desc", "")).strip(),
    }
'''
#******************************************************edit Blake Bozarth 4/29 (facility is now derived from the authoritative cost‑center lookup instead of user input; cost center is the unique key and facility is a dependent attribute.)
def lookup_cost_center_or_raise(cost_center: str) -> dict:
    """
    Uses preloaded _CACHE["cost_centers"].
    Validates by COST CENTER ONLY.
    Facility is DERIVED from CSV (authoritative).
    """

    if _CACHE.get("cost_centers") is None:
        raise ValueError("Cost center lookup table not loaded.")

    df = _CACHE["cost_centers"]

    # normalize input
    cc_norm = _normalize_cost_center(cost_center)

    log.warning("LOOKUP DEBUG cc_norm=%s", cc_norm)

    # match by cost center ONLY
    df_cc = df[df["_CC_NORM"] == cc_norm]

    log.warning(
        "LOOKUP DEBUG matches=%d sample=%s",
        len(df_cc),
        df_cc[["_CC_NORM", "Facility Desc"]].head().to_dict(orient="records")
    )

    if df_cc.empty:
        raise ValueError(f"Invalid Cost Center: {cost_center}")

    row = df_cc.iloc[0]

    return {
        "facility_desc": row["Facility Desc"],
        "cost_center_desc": row["Cost Center Desc"],
    }






# ---------------------------------------------------------------------
# Preload orchestration (synchronous startup)
# ---------------------------------------------------------------------
_preloaded_once = False
_preload_lock = threading.Lock()

def _init_preload_once():
    global _preloaded_once
    if _preloaded_once:
        return

    with _preload_lock:
        if _preloaded_once:
            return

        preload_all()
        _validate_all_datasets_or_raise()
        log.info("Cache preload complete at startup: %s", get_cache_status())

        start_daily_refresh_thread()
        _preloaded_once = True
'''
def startup():
    """Public startup entrypoint used by run_waitress.py."""
    #_validate_required_env_or_raise()
    _init_preload_once()
    #print(_CACHE["productivity_data"].columns.tolist())
    os.makedirs(OUTPUT_PDF_DIR, exist_ok=True)
'''

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
    
    with _CACHE_LOCK:
        recipient_emails = _CACHE["ad_recipient_emails"]

    return render_template("form.html", data=data, known_emails=emails)

@app.post("/submit")
def submit():
    """
    CRITICAL: validate cost center FIRST.
    If invalid => back to / with error message.
    If valid => build review ctx (NO PDF) and redirect to /review.
    """
    
    ensure_user_session()

    preload_all()

    form_fields = request.form.to_dict(flat=True)

    
    ######
    recipient_email = form_fields.get("recipient_email")
    '''
    if not recipient_email:
        flash("Recipient is required.", "error")
        return redirect(url_for("index"))
    '''

    # Save entries so user doesn't lose what they typed
    session["form_data"] = form_fields

    #**********************************BB test ***********************

    #raw_cc = form_fields.get("cost_center", "")
    #norm_cc = _normalize_cost_center(raw_cc)
    #log.warning("COST CENTER DEBUG raw='%s' normalized='%s'", raw_cc, norm_cc)

    #*****************************************************************
    # 1) Validate Cost Center FIRST (fast fail)
    try:
        info = lookup_cost_center_or_raise(form_fields.get("cost_center", ""))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("index"))

    # Optionally override facility selection with Facility Desc from lookup
    # This ensures consistency even if user picked a different facility in dropdown.
    form_fields["facility"] = info["facility_desc"]

    # 2) Build review context dict (NO PDF here)
    import processScreen1DatEntry as processor
    with _CACHE_LOCK:
        cost_centers_df = _CACHE.get("cost_centers")

    # processor.process returns ctx_dict (not pdf path)
    ctx_dict = processor.process(form_fields, cost_centers_df=cost_centers_df, prod_df=_CACHE["productivity_data"],)

    prod_df = _CACHE["productivity_data"]

    from processScreen1DatEntry import gen_operational_stats
    ctx_dict.update(
        gen_operational_stats(
            cost_center=ctx_dict["cost_center"],
            header_month=ctx_dict["header_month"],
            prod_df=prod_df,
        )
    )

    log.error("CTX_DICT KEYS: %s", ctx_dict.keys())
    
    #ctx_dict["recipient_email"] = recipient_email

    session["prc_ctx"] = ctx_dict
    session["pdf_path"] = None
    session.modified = True

    return redirect(url_for("review"))

@app.get("/review")
def review():
    ensure_user_session()
    ctx = session.get("prc_ctx")
    if not ctx:
        flash("No review data found. Please complete the form.", "error")
        return redirect(url_for("index"))
    return render_template("review.html", ctx=ctx)


# edit Blake Bozarth 5/4/26 pulls the data object --> builds pdf from it --> attempts email

from flask import redirect, url_for, flash, session
import smtplib
#from emailer import send_prc_pdf

@app.post("/genpdf_email")
def genpdf_email():
    log.error("GENPDF: ENTRYPOINT")

    ensure_user_session()
    log.error("GENPDF: after ensure_user_session")

    # ---- LOAD CONTEXT -------------------------------------------------


    ctx_dict = session.get("prc_ctx")
    log.error("GENPDF: ctx_dict loaded = %s", "YES" if ctx_dict else "NO")

    if not ctx_dict:
        log.error("GENPDF: EXIT early - ctx_dict missing")
        flash("Session expired. Please submit the form again.", "error")
        return redirect(url_for("index"))

    # ---- PDF GENERATION (MUST RUN UNCONDITIONALLY) --------------------
    try:
        log.error("GENPDF: importing PRCContext")
        from processScreen1DatEntry import PRCContext

        
        pdf_ctx_dict = {
            k: v
            for k, v in ctx_dict.items()
            if k in PRCContext.__annotations__
        }

        log.error("GENPDF: about to call build_pdf")
        ctx = PRCContext(**ctx_dict)
        pdf_path = build_pdf(ctx, OUTPUT_PDF_DIR)

        log.error("GENPDF: build_pdf returned path=%s", pdf_path)

        session["pdf_path"] = pdf_path
        session.modified = True
        log.error("GENPDF: pdf_path stored in session")

        persist_email(ctx.email_to)

        flash("PDF generated successfully.", "success")

    except Exception as e:
        log.exception("GENPDF: PDF GENERATION FAILED")
        flash("PDF generation failed.", "error")
        flash(str(e), "hint")
        return render_template("review.html", ctx=ctx_dict)

    # ---- EMAIL DECISION ----------------------------------------------
    
    recipient_email = ctx_dict.get("email_to")

    if recipient_email:
        try:
            # ALWAYS use SMTP
            send_via_smtp(recipient_email, pdf_path)
            flash("PDF emailed via SMTP.", "success")
        except Exception as e:
            flash("PDF generated, but email failed.", "error")
            flash(str(e), "hint")
    else:
        flash(
            "PDF generated successfully. Email not sent (no recipient selected).",
            "hint",
        )


    log.error("GENPDF: EXIT normal")
    return render_template("review.html", ctx=ctx_dict)


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
