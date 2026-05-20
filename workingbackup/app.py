r"""
app.py

PURPOSE
-------
This module defines:
  - A Flask web application (routes: "/", "/submit", "/preview", "/pdf", "/email_pdf", "/clear_all")
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

NEW UX FLOW
-----------
Screen 1 ("/") -> Submit (POST "/submit") -> PDF Preview (GET "/preview")
Preview page shows embedded PDF (GET "/pdf") and buttons:
  - EMAIL PDF (POST "/email_pdf")
  - CLEAR (POST "/clear_all") -> returns to first screen
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
    flash,      # kept for now (not used in new submit flow)
    send_file,
)

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("prcapp")

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
    "loaded_at": None,            # timestamp of last successful preload
}

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
    driver = os.environ.get("PRC_FABRIC_ODBC_DRIVER", "ODBC Driver 17 for SQL Server")
    url = (
        f"mssql+pyodbc://@{FABRIC_SERVER}:1433/{FABRIC_DATABASE}"
        f"?driver={driver.replace(' ', '+')}"
        f"&authentication=ActiveDirectoryIntegrated"
        f"&Encrypt=yes&TrustServerCertificate=no"
    )
    return create_engine(url, fast_executemany=True)

def load_fabric_table() -> pd.DataFrame:
    eng = _fabric_engine()
    with eng.connect() as con:
        return pd.read_sql(f"SELECT * FROM {FABRIC_TABLE};", con)

def _mssql_engine():
    driver = os.environ.get("PRC_MSSQL_ODBC_DRIVER", "SQL Server")
    url = (
        f"mssql+pyodbc://@{MSSQL_SERVER}/{MSSQL_DATABASE}"
        f"?driver={driver.replace(' ', '+')}"
        f"&Trusted_Connection=yes"
        f"&Encrypt=no"
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
    fab_df  = load_fabric_table()
    prod_df = load_mssql_table()
    vol_df  = load_vol_desc_excel()

    with _CACHE_LOCK:
        _CACHE["cost_centers"]       = cc_df
        _CACHE["payperiod_table"]    = ppp_df
        _CACHE["termination_detail"] = fab_df
        _CACHE["productivity_data"]  = prod_df
        _CACHE["vol_desc_table"]     = vol_df
        _CACHE["loaded_at"]          = datetime.now(tz=LOCAL_TZ)

    log.info(
        "Datasets preloaded. Rows => cost_centers=%s, payperiod=%s, termination_detail=%s, productivity_data=%s, vol_desc=%s",
        len(cc_df), len(ppp_df), len(fab_df), len(prod_df), len(vol_df)
    )

def get_cache_status():
    with _CACHE_LOCK:
        cc   = _CACHE["cost_centers"]
        ppp  = _CACHE["payperiod_table"]
        fab  = _CACHE["termination_detail"]
        prod = _CACHE["productivity_data"]
        vol  = _CACHE["vol_desc_table"]
        ts   = _CACHE["loaded_at"]

    def _info(df):
        return {"loaded": df is not None and not getattr(df, "empty", True),
                "rows": (len(df) if df is not None else 0)}

    return {
        "loaded_at": (ts.isoformat() if ts else None),
        "cost_centers": _info(cc),
        "payperiod_table": _info(ppp),
        "termination_detail": _info(fab),
        "productivity_data": _info(prod),
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
    if "user_id" not in session:
        import uuid
        session["user_id"] = str(uuid.uuid4())
    if "form_data" not in session:
        session["form_data"] = {}

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

def startup():
    """Public startup entrypoint used by run_waitress.py."""
    _init_preload_once()

# ---------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    ensure_user_session()
    data = session.get("form_data", {})
    if not data.get("requested_date"):
        data["requested_date"] = date.today().isoformat()
        session["form_data"] = data
    return render_template("form.html", data=data)

# -----------------------
# NEW: Submit -> Generate PDF -> Preview (no flash messages)
# -----------------------
@app.post("/submit")
def submit():
    ensure_user_session()
    form_fields = request.form.to_dict(flat=True)

    # Save form values so the user can come back to screen 1 with their entries
    session["form_data"] = form_fields

    # Generate PDF
    import processScreen1DatEntry as processor
    pdf_path = processor.process(form_fields)

    # Store PDF path for preview + email
    session["pdf_path"] = pdf_path

    # Go directly to preview page (no message display)
    return redirect(url_for("preview_pdf"))

@app.get("/preview")
def preview_pdf():
    ensure_user_session()
    pdf_path = session.get("pdf_path")
    if not pdf_path or not os.path.exists(pdf_path):
        return redirect(url_for("index"))
    return render_template("preview.html")

@app.get("/pdf")
def serve_pdf():
    ensure_user_session()
    pdf_path = session.get("pdf_path")
    if not pdf_path or not os.path.exists(pdf_path):
        return ("PDF not found", 404)
    return send_file(pdf_path, mimetype="application/pdf", as_attachment=False)

@app.post("/email_pdf")
def email_pdf():
    ensure_user_session()
    pdf_path = session.get("pdf_path")
    if not pdf_path or not os.path.exists(pdf_path):
        return redirect(url_for("index"))

    # TODO: hook in real email sender here, for example:
    # import SendEmailPDF
    # SendEmailPDF.send(pdf_path, session.get("form_data", {}).get("emails", ""))

    # After emailing, return to first screen (your request)
    return redirect(url_for("index"))

@app.post("/clear_all")
def clear_all():
    ensure_user_session()
    session["form_data"] = {}
    session.pop("pdf_path", None)
    return redirect(url_for("index"))

# ---------------------------------------------------------------------
# DEV ENTRYPOINT ONLY (python app.py)
# ---------------------------------------------------------------------
if __name__ == "__main__":
    try:
        startup()
    except Exception as e:
        log.exception("Startup aborted: %s", e)
        raise SystemExit(1)

    app.run(host="0.0.0.0", port=5000, debug=True)
