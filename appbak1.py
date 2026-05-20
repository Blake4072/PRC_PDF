import os
import logging
import threading
import time
from datetime import timedelta, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import create_engine

from flask import Flask, render_template, request, redirect, url_for, session, flash

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("prcapp")

# ---------------------------------------------------------------------
# Flask setup
# ---------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = "change-this-secret-key"
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

# Daily refresh time (local America/Chicago)
DAILY_REFRESH_TIME = os.environ.get("PRC_DAILY_REFRESH", "05:00")
LOCAL_TZ = ZoneInfo("America/Chicago")

# ---------------------------------------------------------------------
# In-memory cache shared across users / requests
# ---------------------------------------------------------------------
_CACHE_LOCK = threading.RLock()
_CACHE = {
    "cost_centers": None,
    "termination_detail": None,
    "productivity_data": None,
    "payperiod_table": None,
    "vol_desc_table": None,
    "loaded_at": None,
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
# Per-dataset loader functions
# ---------------------------------------------------------------------
def load_cost_centers_csv() -> pd.DataFrame:
    if not os.path.exists(CSV_CC_LOOKUP):
        raise FileNotFoundError(f"Cost Center CSV not found: {CSV_CC_LOOKUP}")
    df = (
        pd.read_csv(CSV_CC_LOOKUP, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        .fillna("")
    )
    cc = _pick_column(df, ["Cost Center", "CostCenter", "Cost_Center", "CC"])
    fac = _pick_column(df, ["Facility Desc", "Facility", "FacilityDesc"])
    desc = _pick_column(df, ["Cost Center Desc", "CostCenterDesc", "Description"])
    df["_CC_NORM"] = df[cc].map(_normalize_cost_center)
    df["_FAC_NORM"] = df[fac].map(_normalize_text)
    df = df.rename(columns={cc: "Cost Center", fac: "Facility Desc", desc: "Cost Center Desc"})
    return df[["Cost Center", "Facility Desc", "Cost Center Desc", "_CC_NORM", "_FAC_NORM"]]


def load_payperiod_excel() -> pd.DataFrame:
    if not os.path.exists(PAYPERIOD_XLSX):
        log.warning("PAYPERIOD Excel not found: %s", PAYPERIOD_XLSX)
        return pd.DataFrame()
    try:
        return pd.read_excel(PAYPERIOD_XLSX, sheet_name=PAYPERIOD_SHEET, dtype=str).fillna("")
    except Exception as e:
        log.exception("Failed reading PAYPERIOD Excel: %s", e)
        return pd.DataFrame()


def load_vol_desc_excel() -> pd.DataFrame:
    if not os.path.exists(VOLDESC_XLSX):
        log.warning("Volume Descriptions Excel not found: %s", VOLDESC_XLSX)
        return pd.DataFrame()
    try:
        df = pd.read_excel(VOLDESC_XLSX, dtype=str).fillna("")
        try:
            cc_col = _pick_column(df, ["Cost Center", "CostCenter", "CC", "Cost_Center"])
            vv_col = _pick_column(
                df, ["Volume Description", "Vol Description", "VolDesc", "VOLUME_DESC"]
            )
            df["_CC_NORM"] = df[cc_col].astype(str).map(_normalize_cost_center)
            df = df.rename(columns={cc_col: "Cost Center", vv_col: "Volume Description"})
        except Exception:
            # If headers don't match expectations, return raw dataframe
            pass
        return df
    except Exception as e:
        log.exception("Failed reading Volume Descriptions Excel: %s", e)
        return pd.DataFrame()


def _fabric_engine():
    driver = "ODBC Driver 17 for SQL Server"
    url = (
        f"mssql+pyodbc://@{FABRIC_SERVER}:1433/{FABRIC_DATABASE}"
        f"?driver={driver.replace(' ', '+')}"
        f"&authentication=ActiveDirectoryIntegrated"
        f"&Encrypt=yes&TrustServerCertificate=no"
    )
    return create_engine(url, fast_executemany=True)


def load_fabric_table() -> pd.DataFrame:
    try:
        eng = _fabric_engine()
        with eng.connect() as con:
            return pd.read_sql(f"SELECT * FROM {FABRIC_TABLE};", con)
    except Exception as e:
        log.exception("Failed loading Fabric table %s: %s", FABRIC_TABLE, e)
        return pd.DataFrame()


def _mssql_engine():
    driver = "SQL Server"
    url = (
        f"mssql+pyodbc://@{MSSQL_SERVER}/{MSSQL_DATABASE}"
        f"?driver={driver.replace(' ', '+')}"
        f"&Trusted_Connection=yes"
        f"&Encrypt=no"
    )
    return create_engine(url, fast_executemany=True)


def load_mssql_table() -> pd.DataFrame:
    try:
        eng = _mssql_engine()
        with eng.connect() as con:
            # Note: this query is intentionally hard-coded to match your current behavior
            return pd.read_sql("SELECT * FROM [dbo].[Productivity Data];", con)
    except Exception as e:
        log.exception("Failed loading MSSQL table %s: %s", MSSQL_TABLE, e)
        return pd.DataFrame()


# ---------------------------------------------------------------------
# Master preload/refresh functions
# ---------------------------------------------------------------------
def preload_all():
    """Load/Reload all datasets into the shared cache."""
    cc_df = load_cost_centers_csv()
    ppp_df = load_payperiod_excel()
    fab_df = load_fabric_table()
    prod_df = load_mssql_table()
    vol_df = load_vol_desc_excel()

    with _CACHE_LOCK:
        _CACHE["cost_centers"] = cc_df
        _CACHE["payperiod_table"] = ppp_df
        _CACHE["termination_detail"] = fab_df
        _CACHE["productivity_data"] = prod_df
        _CACHE["vol_desc_table"] = vol_df
        _CACHE["loaded_at"] = datetime.now(tz=LOCAL_TZ)

    log.info(
        "Datasets preloaded. Rows => cost_centers=%s, payperiod=%s, termination_detail=%s, productivity_data=%s, vol_desc=%s",
        len(cc_df),
        len(ppp_df),
        len(fab_df),
        len(prod_df),
        len(vol_df),
    )
    print(">> Datasets have been preloaded into memory and are shared across users.")


def get_cache_status():
    with _CACHE_LOCK:
        cc = _CACHE["cost_centers"]
        ppp = _CACHE["payperiod_table"]
        fab = _CACHE["termination_detail"]
        prod = _CACHE["productivity_data"]
        vol = _CACHE["vol_desc_table"]
        ts = _CACHE["loaded_at"]

    def _info(df):
        return {
            "loaded": df is not None and not getattr(df, "empty", True),
            "rows": (len(df) if df is not None else 0),
        }

    return {
        "loaded_at": (ts.isoformat() if ts else None),
        "cost_centers": _info(cc),
        "payperiod_table": _info(ppp),
        "termination_detail": _info(fab),
        "productivity_data": _info(prod),
        "vol_desc_table": _info(vol),
    }


def _seconds_until_next_5am_chicago() -> float:
    hh, mm = map(int, DAILY_REFRESH_TIME.split(":"))
    now = datetime.now(tz=LOCAL_TZ)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target = (target + timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return (target - now).total_seconds()


def start_daily_refresh_thread():
    """Background thread that refreshes all datasets every day at 05:00 America/Chicago."""

    def _worker():
        while True:
            wait_s = _seconds_until_next_5am_chicago()
            log.info("Daily refresh scheduled; sleeping %.1f seconds until %s.", wait_s, DAILY_REFRESH_TIME)
            time.sleep(max(wait_s, 1.0))
            try:
                log.info("Starting scheduled daily refresh.")
                preload_all()
                log.info("Scheduled daily refresh complete.")
            except Exception as e:
                log.exception("Scheduled daily refresh failed: %s", e)
            # small delay to avoid double-fire around clock edges
            time.sleep(60)

    t = threading.Thread(target=_worker, daemon=True, name="DailyRefresh5AM")
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
    """Synchronous: load once and start the daily refresh thread."""
    global _preloaded_once
    if _preloaded_once:
        return
    with _preload_lock:
        if _preloaded_once:
            return

        # BLOCKING preload here:
        preload_all()

        status = get_cache_status()
        log.info("Cache preload complete at startup: %s", status)

        # Start daily refresh scheduler AFTER initial load
        start_daily_refresh_thread()

        _preloaded_once = True


def startup():
    """Call this before starting the web server. Blocks until preload finishes."""
    _init_preload_once()


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    ensure_user_session()
    data = session.get("form_data", {})
    if not data.get("requested_date"):
        data["requested_date"] = date.today().isoformat()
        session["form_data"] = data
    return render_template("form.html", data=data)


@app.post("/submit")
def submit():
    ensure_user_session()
    form_fields = request.form.to_dict(flat=True)

    errors = []
    cost_center = form_fields.get("cost_center", "").strip()
    facility = form_fields.get("facility", "").strip()
    email = form_fields.get("emails", "").strip()

    if not cost_center or not facility:
        errors.append("Cost Center # and Facility should be entered")

    # Email is required and must be blessinghealth.org
    if not email:
        errors.append("Email address has to be entered (xxx@blessinghealth.org)")
    else:
        import re

        if not re.match(r"^[^@\s]+@blessinghealth\.org$", email, flags=re.IGNORECASE):
            errors.append("Email address has to be of format xx@blessinghealth.org")

    # Persist entries so user doesn't lose work
    session["form_data"] = form_fields

    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("index"))

    try:
        import processScreen1DatEntry as processor

        result = processor.process(form_fields)
        flash("Submitted successfully (demo). Processor returned: " + result[:200], "success")
    except Exception as e:
        log.exception("Processing failed: %s", e)
        flash("Submission failed. See server logs.", "error")

    return redirect(url_for("index"))


@app.post("/clear")
def clear():
    ensure_user_session()
    session["form_data"] = {}
    flash("Form cleared.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------
# Entrypoint for DEV only (python app.py)
# ---------------------------------------------------------------------
if __name__ == "__main__":
    startup()  # blocks until preload finishes
    app.run(host="0.0.0.0", port=5000, debug=True)
