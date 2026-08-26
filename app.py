"""
FinSight AI
===========
A Flask web application for cleaning, analysing, and forecasting
financial data with company-based data isolation and a per-company
Power BI dashboard link.

Entry point:  python app.py
Database init: python init_db.py
"""

from __future__ import annotations

import io
import os
import re
import secrets
import shutil
import tempfile
import threading
import traceback
import uuid
from datetime import datetime, date, timedelta
from functools import wraps
from pathlib import Path
from email.utils import parseaddr

# Load local development variables without overriding deployment variables.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

import base64
import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import mysql.connector
from mysql.connector import pooling, errorcode
import numpy as np
import pandas as pd
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo
from flask import (
    Flask, render_template, request, jsonify, redirect, url_for,
    session, flash, send_file,
)
from sklearn.linear_model import LinearRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ML Pipeline - improved ML with proper validation and no leakage
try:
    from ml_pipeline import get_pipeline
except ImportError as e:
    print(f"[finsight] Warning: Could not import ml_pipeline: {e}")
    get_pipeline = None

# Universal analysis - dataset-agnostic cleaning / target detection / models
try:
    import universal_analysis
except ImportError as e:
    print(f"[finsight] Warning: Could not import universal_analysis: {e}")
    universal_analysis = None

try:
    import i18n
except ImportError as e:
    print(f"[finsight] Warning: Could not import i18n: {e}")
    i18n = None


# =====================================================
# Configuration
# =====================================================
BASE_DIR = Path(__file__).resolve().parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int_env(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except Exception:
        return default


def _bool_env(name: str, default: bool) -> bool:
    return _env(name, str(default)).lower() in {"1", "true", "yes", "on"}


_db_ssl_ca_lock = threading.Lock()
_db_ssl_ca_source: str | None = None
_db_ssl_ca_path: str | None = None


def _ssl_ca_path(value: str) -> str:
    """Return a connector-readable CA path for a path or PEM environment value."""
    if not value.startswith("-----BEGIN CERTIFICATE-----"):
        return value

    pem = value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    global _db_ssl_ca_source, _db_ssl_ca_path
    with _db_ssl_ca_lock:
        if _db_ssl_ca_source == pem and _db_ssl_ca_path and os.path.isfile(_db_ssl_ca_path):
            return _db_ssl_ca_path

        fd, path = tempfile.mkstemp(prefix="finsight-aiven-", suffix=".pem")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as cert_file:
                cert_file.write(pem)
                if not pem.endswith("\n"):
                    cert_file.write("\n")
            os.chmod(path, 0o600)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(path)
            except OSError:
                pass
            raise

        _db_ssl_ca_source = pem
        _db_ssl_ca_path = path
        return path


def _db_config() -> dict[str, object]:
    """Build verified TLS configuration, materializing PEM CA values when needed."""
    required = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")
    missing = [name for name in required if not _env(name)]
    if missing:
        raise RuntimeError("Missing required database environment variable(s): " + ", ".join(missing))
    config = {
        "host": _env("DB_HOST"),
        "port": _int_env("DB_PORT", 25614),
        "user": _env("DB_USER"),
        "password": _env("DB_PASSWORD"),
        "database": _env("DB_NAME"),
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    }
    ssl_ca = _env("DB_SSL_CA")
    if ssl_ca:
        config["ssl_ca"] = _ssl_ca_path(ssl_ca)
    return config


SECRET_KEY = _env("SECRET_KEY") or secrets.token_hex(32)
UPLOAD_FOLDER = BASE_DIR / _env("UPLOAD_FOLDER", "uploads")
UPLOAD_FOLDER.mkdir(exist_ok=True)
POWERBI_ROOT = BASE_DIR / _env("POWERBI_ROOT", "users")
POWERBI_ROOT.mkdir(exist_ok=True)
POWERBI_TEMPLATE = BASE_DIR / _env("POWERBI_TEMPLATE", "finsightai.pbix")
MAX_CONTENT_LENGTH = _int_env("MAX_CONTENT_LENGTH", 16 * 1024 * 1024)
ALLOWED_EXTENSIONS = {
    e.lower() for e in _env("ALLOWED_EXTENSIONS", "csv,xlsx,xls").split(",") if e
} | {"csv", "xlsx", "xls"}




app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
    UPLOAD_FOLDER=str(UPLOAD_FOLDER),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_bool_env("SESSION_COOKIE_SECURE", False),
)
if _env("FLASK_ENV", "production").lower() == "production" and not _env("SECRET_KEY"):
    app.logger.warning("SECRET_KEY is not configured; sessions will be invalidated when the server restarts.")
if i18n is not None:
    i18n.init_app(app)


# =====================================================
# MySQL connection pool (lazy - created on first request so that
# any updates to .env credentials are always picked up).
# =====================================================
db_pool = None
_pool_lock_lock = False  # tiny guard so we only build the pool once


def _build_pool():
    global db_pool
    if db_pool is not None:
        return db_pool
    try:
        db_pool = pooling.MySQLConnectionPool(
            pool_name="finsight_pool",
            pool_size=5,
            pool_reset_session=True,
            **_db_config(),
        )
        print("[finsight] MySQL connection pool ready.")
    except Exception as exc:
        print(f"[finsight] WARNING: could not create pool ({exc}). Falling back to direct connections.")
        db_pool = None
    return db_pool


def get_db():
    """Return a fresh MySQL connection (uses pool when available)."""
    pool = _build_pool()
    if pool is not None:
        try:
            return pool.get_connection()
        except mysql.connector.Error as exc:
            # If the cached pool contains a stale connection (wrong
            # credentials that were valid at startup, or a closed
            # socket), rebuild the pool once and retry.
            print(f"[finsight] Pool get_connection failed ({exc}); rebuilding pool.")
            global db_pool
            try:
                db_pool = None
                pool = _build_pool()
                if pool is not None:
                    return pool.get_connection()
            except Exception:
                pass
            # Last resort - direct connect with the current environment configuration.
            return mysql.connector.connect(**_db_config())
    return mysql.connector.connect(**_db_config())


def run_query(sql: str, params: tuple | list | None = None, *, fetchone=False, fetchall=False, commit=False):
    """Convenience helper for queries that don't need a long-lived cursor."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params or ())
        result = None
        if fetchone:
            result = cur.fetchone()
        elif fetchall:
            result = cur.fetchall()
        if commit:
            conn.commit()
        last_id = cur.lastrowid
        cur.close()
        return {"row": result, "rows": result if fetchall else None, "last_id": last_id}
    finally:
        conn.close()


# =====================================================
# Per-request in-memory data
# =====================================================
# Each user session keeps its own dataframe + models so that companies
# do not share data. The MySQL tables are the durable source of truth.
session_data: dict[int, dict] = {}


def _session_user_id() -> int | None:
    uid = session.get("user_id")
    return int(uid) if uid else None


def _session_company_id() -> int | None:
    cid = session.get("company_id")
    return int(cid) if cid else None


def _message(key: str, **kwargs) -> str:
    """Localized API/flash text; keeps dynamic responses in the active locale."""
    return i18n.t(key, **kwargs) if i18n is not None else key


def _localized_insight(text: str) -> str:
    """Translate deterministic universal-analysis sentences at render time."""
    if not text or i18n is None or i18n.get_locale() == "en":
        return text
    parts = []
    for sentence in text.split(". "):
        match = re.match(r"(.+) show an upward trend, indicating positive growth\.?", sentence)
        if match:
            parts.append(_message("insight.trend.up", names=match.group(1)))
        elif sentence.startswith("The model points to the main business indicators"):
            parts.append(_message("insight.model_strong"))
        elif sentence.startswith("The most relevant outcome is "):
            label = sentence.split('"')[1] if '"' in sentence else ""
            parts.append(_message("insight.classification.focus", pred=label))
        else:
            parts.append(sentence)
    return " ".join(parts)


def get_dataset(user_id: int) -> pd.DataFrame | None:
    return session_data.get(user_id, {}).get("df")


def get_models(user_id: int) -> dict:
    return session_data.setdefault(user_id, {}).setdefault("models", {
        "amount": None, "expenses": None, "revenue": None, "profit": None, "risk": None
    })


def _current_dataset_id(user_id: int) -> int | None:
    """Return the authenticated user's most recently processed upload only."""
    row = run_query(
        """SELECT id FROM uploaded_files
           WHERE user_id=%s AND status='processed'
           ORDER BY id DESC LIMIT 1""",
        (user_id,), fetchone=True,
    )["row"]
    return int(row["id"]) if row else None


def _load_active_cleaned_dataset(user_id: int, dataset_id: int) -> pd.DataFrame:
    """Rebuild the active upload's cleaned frame and its models from owned rows."""
    rows = run_query(
        """SELECT tx_date, transaction_id, description, amount, revenue, expenses, profit,
                  tx_type, category, payment_method, department, city, status
           FROM financial_data
           WHERE user_id=%s AND uploaded_file_id=%s
           ORDER BY tx_date, id""",
        (user_id, dataset_id), fetchall=True,
    )["rows"] or []
    df = pd.DataFrame(rows)
    if df.empty:
        # No legacy financial rows were persisted (generic dataset). Try the
        # universal-analysis sidecar so predictions still work after a restart.
        try:
            path = _analysis_store_path(user_id)
            if path is not None and path.exists():
                side = pd.read_csv(path)
                _run_universal_analysis(user_id, side)
        except Exception as exc:
            print(f"[finsight] Could not restore universal analysis: {exc}")
        # Generic uploads have no canonical financial rows.  Record ownership
        # anyway so navigation does not repeatedly discard their restored
        # universal analysis and incorrectly show the upload-required state.
        session_data.setdefault(user_id, {})["dataset_id"] = dataset_id
        session_data.setdefault(user_id, {})["df"] = df
        return df
    df["tx_date"] = pd.to_datetime(df["tx_date"], errors="coerce")
    dated = df.dropna(subset=["tx_date"]).copy()
    if not dated.empty:
        dated["Date_Number"] = (dated["tx_date"] - dated["tx_date"].min()).dt.days
        dated["Month"] = dated["tx_date"].dt.month
        dated["Day_of_Week"] = dated["tx_date"].dt.dayofweek
    session_data[user_id] = {
        "df": dated,
        "dataset_id": dataset_id,
        "models": {"amount": None, "expenses": None, "revenue": None, "profit": None, "risk": None},
    }
    if not dated.empty:
        _train_models_for(user_id, dated)
    # Restore universal analysis from the sidecar (survives restarts).
    try:
        path = _analysis_store_path(user_id)
        if path is not None and path.exists():
            _run_universal_analysis(user_id, pd.read_csv(path))
    except Exception as exc:
        print(f"[finsight] Could not restore universal analysis: {exc}")
    return dated


def _active_dataset_and_frame(user_id: int) -> tuple[int | None, pd.DataFrame | None]:
    """Ensure the frame/model in memory belongs to this user's current upload."""
    dataset_id = _current_dataset_id(user_id)
    if dataset_id is None:
        return None, None
    state = session_data.get(user_id, {})
    if state.get("dataset_id") != dataset_id:
        return dataset_id, _load_active_cleaned_dataset(user_id, dataset_id)
    return dataset_id, state.get("df")


# =====================================================
# Universal analysis (dataset-agnostic predictions / insights)
# =====================================================
def _analysis_store_path(user_id: int) -> Path | None:
    """Optional sidecar CSV that keeps the cleaned original upload around so the
    universal analysis can survive a server restart."""
    try:
        resource = _powerbi_resource(user_id, create=False)
        if not resource or not resource.get("folder_token"):
            return None
        folder = POWERBI_ROOT / resource["folder_token"] / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "analysis_data.csv"
    except Exception:
        return None


def _run_universal_analysis(user_id: int, raw_df: pd.DataFrame) -> dict | None:
    """Run the universal analysis on the cleaned upload and cache it both in
    memory and in a sidecar CSV.  Never raises; returns the analysis dict or
    None when the module/data is unusable."""
    if universal_analysis is None:
        return None
    try:
        clean = universal_analysis.clean_dataset(raw_df)
        analysis = universal_analysis.auto_analyze(clean)
        session_data.setdefault(user_id, {})["analysis"] = analysis
        session_data.setdefault(user_id, {})["analysis_df"] = clean.head(1000)
        path = _analysis_store_path(user_id)
        if path is not None:
            try:
                clean.head(3000).to_csv(path, index=False)
            except Exception:
                pass
        return analysis
    except Exception as exc:
        print(f"[finsight] Universal analysis warning: {exc}")
        return None


def _analysis_context(user_id: int) -> dict | None:
    """Return the cached universal analysis (model objects removed)."""
    state = session_data.get(user_id, {})
    analysis = state.get("analysis")
    if isinstance(analysis, dict):
        try:
            sections = [
                universal_analysis.strip_model_keys(s)
                for s in analysis.get("sections", [])
            ]
        except Exception:
            sections = analysis.get("sections", [])
        return {
            "ok": analysis.get("ok", False),
            "rows": analysis.get("rows", 0),
            "columns": analysis.get("columns", 0),
            "types": analysis.get("types", {}),
            "sections": sections,
            "trends": analysis.get("trends", []),
            "insight": _localized_insight(analysis.get("insight", "")),
            "has_predictions": analysis.get("has_predictions", False),
            "message": analysis.get("message", ""),
        }
    return None


# =====================================================
# Auth helpers / decorators
# =====================================================
def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _session_user_id():
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _valid_email(value: str) -> bool:
    """Small, dependency-free validation before persisting login identifiers."""
    _, parsed = parseaddr(value)
    if parsed != value or len(value) > 254 or value.count("@") != 1:
        return False
    local, domain = value.rsplit("@", 1)
    # Keep this deliberately lightweight: reject malformed addresses without
    # imposing provider-specific rules on valid business email addresses.
    return bool(local and domain and "." in domain and not domain.startswith(".") and not domain.endswith("."))


def csrf_token() -> str:
    """Return a session-bound token for state-changing browser requests."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.before_request
def protect_csrf():
    """Reject cross-site writes while allowing normal HTML forms and AJAX calls."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    supplied = request.headers.get("X-CSRFToken") or request.form.get("csrf_token")
    expected = session.get("_csrf_token")
    if expected and supplied and secrets.compare_digest(expected, supplied):
        return None
    if request.is_json or request.path in {"/upload", "/risk-classify", "/powerbi/generate"}:
        return jsonify({"error": "Your session validation token is missing or expired. Refresh the page and try again."}), 400
    flash("Your form has expired. Please refresh the page and try again.", "warning")
    return redirect(request.referrer or url_for("index"))


@app.context_processor
def inject_security_context():
    return {"csrf_token": csrf_token}


# =====================================================
# Database initialization
# =====================================================
def init_database() -> None:
    """Verify connectivity and apply the small runtime schema upgrade."""
    try:
        conn = get_db()
        cur = conn.cursor()
        for table, column, definition in (
            ("uploaded_files", "version", "INT NOT NULL DEFAULT 1 AFTER company_id"),
            ("predictions", "actual_value", "DECIMAL(18, 2) NULL AFTER prediction_date"),
            ("predictions", "prediction_error", "DECIMAL(18, 2) NULL AFTER predicted_value"),
        ):
            cur.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s""",
                (table, column),
            )
            if not cur.fetchone()[0]:
                cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS powerbi_desktop_reports (
                report_id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                uploaded_file_id INT NOT NULL,
                pbix_filename VARCHAR(255) NOT NULL,
                status VARCHAR(40) NOT NULL DEFAULT 'generated',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_desktop_report_file (uploaded_file_id),
                INDEX idx_desktop_report_user (user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS risk_classifications (
                risk_id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL, company_id INT NOT NULL, uploaded_file_id INT NOT NULL,
                classification_date DATE NOT NULL, risk_level VARCHAR(20) NOT NULL,
                revenue DECIMAL(18,2) NULL, expenses DECIMAL(18,2) NULL,
                profit DECIMAL(18,2) NULL, amount DECIMAL(18,2) NULL,
                explanation TEXT NULL, model_name VARCHAR(80) NOT NULL DEFAULT 'decision_tree_classifier',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_risk_user_file_date (user_id, uploaded_file_id, classification_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        )
        conn.commit()
        cur.close()
        conn.close()
        print("[finsight] Database connection OK.")
    except mysql.connector.Error as err:
        print(f"[finsight] WARNING: cannot connect to MySQL: {err}")
        print("[finsight] Run `python init_db.py` to create the schema.")


# =====================================================
# Column normalisation for uploaded files
# =====================================================
COLUMN_ALIASES = {
    "date":          "tx_date",
    "transaction_id":"transaction_id",
    "tx_id":         "transaction_id",
    "id":            "transaction_id",
    "description":   "description",
    "desc":          "description",
    "amount":        "amount",
    "revenue":       "revenue",
    "expenses":      "expenses",
    "expense":       "expenses",
    "profit":        "profit",
    "type":          "tx_type",
    "tx_type":       "tx_type",
    "category":      "category",
    "payment_method":"payment_method",
    "payment":       "payment_method",
    "department":    "department",
    "city":          "city",
    "status":        "status",
}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename incoming columns to a canonical set used by the rest of the app.
    Case-insensitive, with common aliases (e.g. expense -> expenses)."""
    rename = {}
    for col in df.columns:
        key = col.strip().lower().replace(" ", "_")
        if key in COLUMN_ALIASES:
            rename[col] = COLUMN_ALIASES[key]
    return df.rename(columns=rename)


# =====================================================
# Per-user Power BI Desktop resources
# =====================================================
def _powerbi_resource(user_id: int, create: bool = True) -> dict | None:
    """Return the authenticated user's opaque Power BI resource record."""
    result = run_query("SELECT * FROM user_powerbi_resources WHERE user_id=%s", (user_id,), fetchone=True)
    row = result["row"]
    if row or not create:
        return row
    folder_token = secrets.token_urlsafe(18).replace("-", "").replace("_", "")
    run_query(
        "INSERT INTO user_powerbi_resources (user_id, folder_token) VALUES (%s, %s)",
        (user_id, folder_token), commit=True,
    )
    return run_query("SELECT * FROM user_powerbi_resources WHERE user_id=%s", (user_id,), fetchone=True)["row"]


def _powerbi_paths(resource: dict, user_id: int, dataset_id: int | None = None) -> dict[str, Path]:
    root = POWERBI_ROOT / resource["folder_token"] / "powerbi"
    if dataset_id is not None:
        root = root / f"dataset_{dataset_id}"
    data = root / "data"
    pbix_name = f"finsight_user_{user_id}_dataset_{dataset_id or 'latest'}.pbix"
    return {"root": root, "data": data, "financial": data / "financial_data.csv",
            "predictions": data / "predictions.csv", "pbix": root / pbix_name,
            "readme": root / "README-PowerBI-Desktop.txt"}


def _next_dataset_version(user_id: int) -> int:
    row = run_query(
        "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
        "FROM uploaded_files WHERE user_id=%s", (user_id,), fetchone=True,
    )["row"]
    return int(row["next_version"] if row else 1)


# =====================================================
# History helpers
# =====================================================
def add_history(user_id: int, company_id: int, event_type: str, title: str,
                *, file_id: int | None = None, prediction_id: int | None = None,
                status: str = "ok", details: str | None = None) -> None:
    run_query(
        """
        INSERT INTO dashboard_history
            (user_id, company_id, event_type, event_title, file_id, prediction_id, status, details)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, company_id, event_type, title, file_id, prediction_id, status, details),
        commit=True,
    )


# =====================================================
# Routes - Auth
# =====================================================
@app.route("/")
def index():
    if _session_user_id():
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        company_name = (request.form.get("company_name") or "").strip()

        if not name or not email or not password or not company_name:
            flash("All fields are required.", "danger")
            return render_template("signup.html")

        if not _valid_email(email):
            flash("Enter a valid email address.", "danger")
            return render_template("signup.html")

        if len(name) > 120 or len(company_name) > 255:
            flash("Name or company name is too long.", "danger")
            return render_template("signup.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template("signup.html")

        # Check whether the email is already used
        existing = run_query(
            "SELECT id FROM users WHERE email = %s", (email,), fetchone=True
        )
        if existing and existing["row"]:
            flash("This email is already registered. Please log in.", "warning")
            return redirect(url_for("login"))

        try:
            conn = get_db()
            cur = conn.cursor(dictionary=True)
            # Find or create the company
            cur.execute("SELECT id FROM companies WHERE company_name = %s", (company_name,))
            row = cur.fetchone()
            if row:
                company_id = row["id"]
            else:
                cur.execute(
                    "INSERT INTO companies (company_name) VALUES (%s)",
                    (company_name,),
                )
                company_id = cur.lastrowid

            # Create the user
            cur.execute(
                """
                INSERT INTO users (name, email, password_hash, company_id)
                VALUES (%s, %s, %s, %s)
                """,
                (name, email, generate_password_hash(password), company_id),
            )
            user_id = cur.lastrowid
            conn.commit()
            cur.close()
            conn.close()

            _powerbi_resource(user_id)

            add_history(user_id, company_id, "signup", f"New account created for {company_name}")

            session["user_id"] = user_id
            session["user_name"] = name
            session["company_id"] = company_id
            session["company_name"] = company_name
            flash(f"Welcome to FinSight AI, {name}!", "success")
            return redirect(url_for("dashboard"))
        except mysql.connector.Error as exc:
            print(f"[finsight] Signup error: {exc}")
            if exc.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                flash("Cannot connect to MySQL: access denied. "
                      "Check DB_USER and DB_PASSWORD in your environment configuration.", "danger")
            else:
                flash("We could not create your account right now. Please try again.", "danger")
            return render_template("signup.html")

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not email or not password:
            flash("Email and password are required.", "danger")
            return render_template("login.html")

        if not _valid_email(email):
            flash("Enter a valid email address.", "danger")
            return render_template("login.html")

        row = run_query(
            """
            SELECT u.id, u.name, u.password_hash, u.company_id, c.company_name
            FROM users u
            JOIN companies c ON c.id = u.company_id
            WHERE u.email = %s
            """,
            (email,),
            fetchone=True,
        )["row"]

        if not row or not check_password_hash(row["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        session["user_id"] = row["id"]
        session["user_name"] = row["name"]
        session["company_id"] = row["company_id"]
        session["company_name"] = row["company_name"]
        flash(f"Welcome back, {row['name']}!", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# =====================================================
# Routes - Dashboard
# =====================================================
@app.route("/dashboard")
@login_required
def dashboard():
    user_id = _session_user_id()
    company_id = _session_company_id()

    # Pull recent uploads
    uploads = run_query(
        """
        SELECT id, original_name, rows_imported, status, created_at
        FROM uploaded_files
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (user_id,),
        fetchall=True,
    )["rows"] or []

    # Recent predictions
    predictions = run_query(
        """
        SELECT prediction_id, prediction_type, prediction_date, predicted_value, created_at
        FROM predictions
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (user_id,),
        fetchall=True,
    )["rows"] or []
    active_dataset_id = _current_dataset_id(user_id)
    risk_status = None
    if active_dataset_id is not None:
        risk_status = run_query(
            """SELECT risk_level, classification_date, explanation FROM risk_classifications
               WHERE user_id=%s AND uploaded_file_id=%s ORDER BY risk_id DESC LIMIT 1""",
            (user_id, active_dataset_id), fetchone=True,
        )["row"]

    # Aggregates for KPI cards (use live data if loaded, otherwise fall
    # back to whatever is stored in financial_data)
    df = get_dataset(user_id)
    if df is not None and not df.empty:
        stats = {
            "rows": int(len(df)),
            "revenue_total": float(df.get("revenue", pd.Series(dtype=float)).sum()),
            "expenses_total": float(df.get("expenses", pd.Series(dtype=float)).sum()),
            "profit_total": float(df.get("profit", pd.Series(dtype=float)).sum()),
            "date_range": "",
        }
        if "tx_date" in df.columns and df["tx_date"].notna().any():
            stats["date_range"] = (
                f"{df['tx_date'].min().date()} to {df['tx_date'].max().date()}"
            )
    else:
        agg = run_query(
            """
            SELECT
                COUNT(*)        AS row_count,
                COALESCE(SUM(revenue),  0) AS revenue_total,
                COALESCE(SUM(expenses), 0) AS expenses_total,
                COALESCE(SUM(profit),   0) AS profit_total
            FROM financial_data
            WHERE user_id = %s
            """,
            (user_id,),
            fetchone=True,
        )["row"] or {}
        stats = {
            "rows": int(agg.get("row_count") or 0),
            "revenue_total": float(agg.get("revenue_total") or 0),
            "expenses_total": float(agg.get("expenses_total") or 0),
            "profit_total": float(agg.get("profit_total") or 0),
            "date_range": "",
        }

    return render_template(
        "dashboard.html",
        company_name=session["company_name"],
        user_name=session["user_name"],
        uploads=uploads,
        predictions=predictions,
        stats=stats,
        risk_status=risk_status,
        analysis=_analysis_context(user_id),
        insight=(_analysis_context(user_id) or {}).get("insight", ""),
        analysis_trends=(_analysis_context(user_id) or {}).get("trends", []),
    )


@app.route("/analytics")
@login_required
def analytics():
    """Render interactive charts from the authenticated user's active dataset."""
    user_id = _session_user_id()
    _, df = _active_dataset_and_frame(user_id)
    if df is None or df.empty:
        return render_template(
            "analytics.html", company_name=session["company_name"], has_data=False,
            monthly=[], categories=[], cities=[], totals={}, generic_analytics=False,
            trend_keys=[], trend_labels=[], category_label="Category",
        )

    working = df.copy()
    for column in ("amount", "revenue", "expenses", "profit"):
        if column not in working.columns:
            working[column] = 0
        working[column] = pd.to_numeric(working[column], errors="coerce").fillna(0)

    # Generic (universal) analytics: when the upload is a non-financial business
    # dataset, still build a useful monthly trend + category breakdown.
    has_financial = (
        "tx_date" in working.columns
        and bool(working[["revenue", "expenses", "profit"]].abs().sum().sum())
    )
    if not has_financial:
        try:
            analysis = _analysis_context(user_id) or {}
            types = analysis.get("types") or {}
            date_col = next((c for c, t in types.items() if t == "date"), None) or "tx_date"
            num_cols = [c for c, t in types.items() if t == "numeric"]
            cat_cols = [c for c, t in types.items() if t == "categorical"]
            if date_col not in working.columns:
                date_col = None
            if num_cols:
                num_cols = [c for c in num_cols if c in working.columns]
            if date_col:
                parsed = pd.to_datetime(working[date_col], errors="coerce")
                temp = working.copy()
                temp["_period"] = parsed.dt.to_period("M").astype(str)
                temp = temp.dropna(subset=["_period"])
                if not temp.empty:
                    keys = num_cols[:3]
                    monthly = temp.groupby("_period", as_index=False)[keys].sum()
                    monthly = monthly.rename(columns={"_period": "period"})
                    monthly = monthly.to_dict("records")
                else:
                    monthly = []
            else:
                monthly = []
            cats = []
            if cat_cols and num_cols:
                cat = cat_cols[0]
                value_col = num_cols[0]
                if cat in working.columns and value_col in working.columns:
                    agg = working.fillna({cat: "Unknown"}).groupby(
                        cat, as_index=False)[value_col].sum()
                    if cat != "category":
                        agg = agg.rename(columns={cat: "category"})
                    cats = agg.rename(columns={value_col: "amount"}) \
                        .sort_values("amount", ascending=False).head(12).to_dict("records")
            totals = {c: float(working[c].sum()) for c in num_cols[:4]}
            return render_template(
                "analytics.html",
                company_name=session["company_name"],
                has_data=True,
                monthly=monthly,
                categories=cats,
                cities=[],
                totals=totals,
                trend_keys=num_cols[:3],
                trend_labels=[
                    str(c).replace("_", " ").title() for c in num_cols[:3]
                ],
                category_label=cat_cols[0].replace("_", " ").title()
                if cat_cols else "Category",
                generic_analytics=True,
            )
        except Exception as exc:
            # Generic analytics is an optional enhancement.  Fall back to the
            # established financial view if a heterogeneous upload cannot be
            # summarized here.
            print(f"[finsight] Generic analytics warning: {exc}")

    monthly = working.dropna(subset=["tx_date"]).assign(
        period=lambda frame: pd.to_datetime(frame["tx_date"]).dt.to_period("M").astype(str)
    ).groupby("period", as_index=False)[["revenue", "expenses", "profit"]].sum()
    categories = []
    if "category" in working.columns:
        categories = working.assign(category=working["category"].fillna("Uncategorized").astype(str)) \
            .groupby("category", as_index=False)["amount"].sum().sort_values("amount", ascending=False).head(12).to_dict("records")
    cities = []
    if "city" in working.columns:
        cities = working.assign(city=working["city"].fillna("").astype(str))
        cities = cities[cities["city"].str.strip() != ""].groupby("city", as_index=False)["amount"].sum() \
            .sort_values("amount", ascending=False).head(20).to_dict("records")

    return render_template(
        "analytics.html", company_name=session["company_name"], has_data=True,
        monthly=monthly.to_dict("records"), categories=categories, cities=cities,
        totals={column: float(working[column].sum()) for column in ("revenue", "expenses", "profit", "amount")},
        generic_analytics=False,
        trend_keys=["revenue", "expenses", "profit"],
        trend_labels=["Revenue", "Expenses", "Profit"],
        category_label="Category",
    )


# =====================================================
# Routes - File upload
# =====================================================
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload_file():
    """Accept a CSV/XLSX file, clean it, store rows in MySQL, train models."""
    user_id = _session_user_id()
    company_id = _session_company_id()

    if request.method == "GET":
        return render_template("upload.html", company_name=session["company_name"])

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "File must be CSV, XLSX, or XLS"}), 400

    # Persist the file on disk so we have a record of every upload
    safe_name = secure_filename(file.filename)
    # The stored path is derived solely from the authenticated user and an
    # opaque resource token; no client-supplied user/company identifier is used.
    resource = _powerbi_resource(user_id)
    user_upload_dir = POWERBI_ROOT / resource["folder_token"] / "uploads"
    user_upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    disk_path = user_upload_dir / stored_name
    try:
        file.save(disk_path)
    except Exception as exc:
        app.logger.exception("Could not save uploaded file")
        return jsonify({"error": "Could not save the file. Please try again."}), 500

    file_id: int | None = None
    upload_succeeded = False
    try:
        # ---------- Read with Pandas ----------
        try:
            if safe_name.lower().endswith(".csv"):
                df = pd.read_csv(disk_path)
            else:
                df = pd.read_excel(disk_path)
        except Exception as exc:
            add_history(user_id, company_id, "upload", safe_name, status="failed",
                        details=f"read error: {exc}")
            app.logger.info("Invalid upload from user %s: %s", user_id, exc)
            return jsonify({"error": "We could not read this file. Check its format and try again."}), 400

        if df.empty:
            return jsonify({"error": "File is empty or has no data"}), 400

        # ---------- Register the upload ----------
        file_id = run_query(
            """
            INSERT INTO uploaded_files
                (user_id, company_id, version, original_name, stored_name, file_size, rows_imported, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'processing')
            """,
            (user_id, company_id, _next_dataset_version(user_id), safe_name, stored_name,
             disk_path.stat().st_size, int(len(df))),
            commit=True,
        )["last_id"]

        # ---------- Normalize columns ----------
        df = normalize_columns(df)

        # Drop fully empty rows / duplicates
        df.dropna(how="all", inplace=True)
        df.drop_duplicates(inplace=True)

        # Keep a copy of the cleaned upload (original column names) for the
        # universal, dataset-agnostic analysis pipeline.
        universal_df = df.copy()

        # ---------- Date ----------
        if "tx_date" in df.columns:
            df["tx_date"] = pd.to_datetime(df["tx_date"], errors="coerce")
            df["tx_date"] = df["tx_date"].dt.date  # store as python date
        else:
            df["tx_date"] = None

        # ---------- Numeric columns ----------
        had_money_cols = any(
            c in df.columns for c in ("amount", "revenue", "expenses", "profit")
        )
        for col in ("amount", "revenue", "expenses", "profit"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = np.nan

        # Compute profit when both columns exist and profit missing
        if df["profit"].isna().all() and "revenue" in df.columns and "expenses" in df.columns:
            df["profit"] = df["revenue"].fillna(0) - df["expenses"].fillna(0)

        # Drop rows with no money value, but ONLY when the uploaded file actually
        # contained one of the financial columns (a generic business dataset must
        # never be wiped out because it has no Revenue/Expenses/Profit).
        money_cols = [c for c in ("amount", "revenue", "expenses", "profit") if c in df.columns]
        if money_cols and had_money_cols:
            df = df.dropna(subset=money_cols, how="all")

        if df.empty:
            run_query(
                "UPDATE uploaded_files SET status='failed' WHERE id=%s AND user_id=%s",
                (file_id, user_id), commit=True,
            )
            add_history(user_id, company_id, "upload", safe_name, file_id=file_id,
                        status="failed", details="no usable rows after cleaning")
            return jsonify({"error": "No valid rows after cleaning. "
                                     "Check that your file contains numeric or "
                                     "categorical business data."}), 400

        # Optional string columns - never required
        for col in ("transaction_id", "description", "tx_type", "category",
                    "payment_method", "department", "city", "status"):
            if col not in df.columns:
                df[col] = None
            else:
                df[col] = df[col].astype(object).where(df[col].notna(), None)

        # ---------- Persist to MySQL ----------
        insert_sql = """
            INSERT INTO financial_data
                (user_id, company_id, uploaded_file_id,
                 tx_date, transaction_id, description,
                 amount, revenue, expenses, profit,
                 tx_type, category, payment_method, department, city, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        rows = []
        for _, r in df.iterrows():
            rows.append((
                user_id, company_id, file_id,
                r["tx_date"] if isinstance(r["tx_date"], date) else None,
                r.get("transaction_id"),
                r.get("description"),
                None if pd.isna(r["amount"])  else float(r["amount"]),
                None if pd.isna(r["revenue"]) else float(r["revenue"]),
                None if pd.isna(r["expenses"]) else float(r["expenses"]),
                None if pd.isna(r["profit"])  else float(r["profit"]),
                r.get("tx_type"),
                r.get("category"),
                r.get("payment_method"),
                r.get("department"),
                r.get("city"),
                r.get("status"),
            ))

        # Insert in chunks to avoid huge packets
        conn = get_db()
        try:
            cur = conn.cursor()
            CHUNK = 500
            for i in range(0, len(rows), CHUNK):
                cur.executemany(insert_sql, rows[i:i + CHUNK])
            conn.commit()
            cur.close()
        finally:
            conn.close()

        run_query(
            "UPDATE uploaded_files SET rows_imported=%s, status='processed' WHERE id=%s AND user_id=%s",
            (int(len(df)), file_id, user_id), commit=True,
        )

        # ---------- Train models for this user/company ----------
        # Convert tx_date back to datetime so the model code can use it
        train_df = df.copy()
        train_df["tx_date"] = pd.to_datetime(train_df["tx_date"], errors="coerce")
        train_df = train_df.dropna(subset=["tx_date"])
        # A new upload always replaces the active dataset/model for this user.
        # Never leave a model trained from an earlier upload in this session.
        session_data[user_id] = {
            "df": train_df,
            "dataset_id": file_id,
            "models": {"amount": None, "expenses": None, "revenue": None, "profit": None, "risk": None},
        }
        if not train_df.empty:
            train_df["Date_Number"] = (
                train_df["tx_date"] - train_df["tx_date"].min()
            ).dt.days
            train_df["Month"] = train_df["tx_date"].dt.month
            train_df["Day_of_Week"] = train_df["tx_date"].dt.dayofweek
            session_data.setdefault(user_id, {})["df"] = train_df
            try:
                _train_models_for(user_id, train_df)
            except Exception as exc:
                print(f"[finsight] Model training warning: {exc}")

        # Universal, dataset-agnostic analysis (any CSV/XLSX works).
        try:
            _run_universal_analysis(user_id, universal_df)
        except Exception as exc:
            print(f"[finsight] Universal analysis warning: {exc}")

        add_history(user_id, company_id, "upload", safe_name, file_id=file_id,
                    status="processed", details=f"{len(df)} rows imported")

        _generate_powerbi_resources(user_id, company_id, session["company_name"])

        # ---------- Stats for the front-end ----------
        stats = {
            "rows": int(len(df)),
            "columns": int(len(df.columns)),
            "duplicates": 0,
            "missing_values": {c: int(df[c].isna().sum()) for c in df.columns},
            "date_range": "",
            "revenue_total": float(df["revenue"].fillna(0).sum()),
            "expenses_total": float(df["expenses"].fillna(0).sum()),
            "profit_total": float(df["profit"].fillna(0).sum()),
            "amount_mean": float(df["amount"].dropna().mean()) if df["amount"].notna().any() else 0.0,
            "amount_max": float(df["amount"].dropna().max()) if df["amount"].notna().any() else 0.0,
            "amount_min": float(df["amount"].dropna().min()) if df["amount"].notna().any() else 0.0,
        }
        # For generic datasets, surface the single largest numeric total so the
        # upload page still shows something meaningful.
        num_sums = (
            df.select_dtypes(include=["number"])
            .sum()
            .sort_values(ascending=False)
        )
        top_num = None
        for col in num_sums.index:
            low = str(col).lower()
            if any(tok in low for tok in ("tx_", "date", "month", "day", "year", "number")):
                continue
            top_num = str(col)
            break
        if top_num:
            stats["top_metric"] = top_num
            stats["top_metric_total"] = float(num_sums[top_num])
            if "tx_date" in df.columns and df["tx_date"].notna().any():
                stats["date_range"] = (
                    f"{pd.to_datetime(df['tx_date']).min().date()} to "
                    f"{pd.to_datetime(df['tx_date']).max().date()}"
                )
        else:
            if "tx_date" in df.columns and df["tx_date"].notna().any():
                stats["date_range"] = (
                    f"{pd.to_datetime(df['tx_date']).min().date()} to "
                    f"{pd.to_datetime(df['tx_date']).max().date()}"
                )

        # Expose the dataset-agnostic profile to the upload screen.  The
        # financial compatibility columns above remain untouched for legacy
        # dashboards and exports.
        analysis_summary = _analysis_context(user_id) or {}
        detected_types = analysis_summary.get("types", {})
        stats["numeric_columns"] = [
            name for name, kind in detected_types.items() if kind == "numeric"
        ]
        stats["categorical_columns"] = [
            name for name, kind in detected_types.items() if kind == "categorical"
        ]
        stats["date_column"] = next(
            (name for name, kind in detected_types.items() if kind == "date"),
            None,
        )
        sections = analysis_summary.get("sections", [])
        stats["prediction_target"] = sections[0].get("target") if sections else None

        upload_succeeded = True
        return jsonify({"success": True, "stats": stats, "file_id": file_id})

    except Exception as exc:
        app.logger.exception("Upload processing failed for user %s", user_id)
        if file_id is not None:
            run_query("UPDATE uploaded_files SET status='failed' WHERE id=%s AND user_id=%s",
                      (file_id, user_id), commit=True)
        add_history(user_id, company_id, "upload", safe_name,
                    file_id=file_id, status="failed", details=str(exc))
        return jsonify({"error": "We could not process this file. Please try again."}), 500
    finally:
        # Invalid and failed uploads should not consume a user's storage forever.
        if not upload_succeeded and disk_path.exists():
            try:
                disk_path.unlink()
            except OSError:
                app.logger.warning("Could not remove failed upload %s", disk_path)


def _train_models_for(user_id: int, df: pd.DataFrame) -> None:
    """Train regression models using improved ML pipeline.
    
    Uses:
    - Chronological train/test split (not random, for time-series)
    - Feature engineering (temporal, lag, rolling, derived features)
    - Multiple model comparison (Linear, Ridge, Lasso, Tree, Forest, Boosting)
    - Cross-validation (TimeSeriesSplit) for stability assessment
    - Comprehensive evaluation metrics (MAE, RMSE, MSE, R², overfitting detection)
    - Model persistence to disk with metadata
    
    Still maintains session-based models for backward compatibility.
    """
    if len(df) < 10:
        models = get_models(user_id)
        for key in ("amount", "expenses", "revenue", "profit"):
            models[key] = None
        return

    models = get_models(user_id)
    
    # Use new ML pipeline if available
    if get_pipeline is not None:
        try:
            pipeline = get_pipeline()
            
            # Train all regression models using the pipeline
            for target in ["amount", "revenue", "expenses", "profit"]:
                if target not in df.columns:
                    models[target] = None
                    continue
                
                result = pipeline.train_regression_model(df, target, user_id, model_type='best')
                
                if result.get('success'):
                    best_model_name = result.get('selected_model', 'linear')
                    metrics = result.get('metrics', {})
                    
                    # Store in session for immediate use
                    try:
                        loaded_model, metadata = pipeline._load_model(user_id, target)
                        models[target] = {
                            "model": loaded_model,
                            "df_min": df["tx_date"].min() if "tx_date" in df.columns else None,
                            "metrics": {
                                "mae": float(metrics.get("test_mae", 0)),
                                "r2": float(metrics.get("test_r2", 0)),
                                "rmse": float(metrics.get("test_rmse", 0)),
                                "train_r2": float(metrics.get("train_r2", 0)),
                                "cv_r2_mean": float(metrics.get("cv_r2_mean", 0)),
                                "overfitting": metrics.get("overfitting_warning", "Unknown"),
                                "model_type": best_model_name,
                                "features_used": len(metrics.get("features_used", [])),
                                "train_samples": metrics.get("n_train", 0),
                                "test_samples": metrics.get("n_test", 0),
                            }
                        }
                        print(f"[finsight] Trained {target} model ({best_model_name}): "
                              f"R²={metrics.get('test_r2', 0):.4f}, "
                              f"MAE={metrics.get('test_mae', 0):.2f}, "
                              f"Overfitting={metrics.get('overfitting_warning', 'unknown')}")
                    except Exception as e:
                        print(f"[finsight] Could not load trained {target} model: {e}")
                        models[target] = None
                else:
                    error = result.get('error', 'Unknown error')
                    print(f"[finsight] Could not train {target} model: {error}")
                    models[target] = None
            
            _train_risk_model_for(user_id, df)
            return
        except Exception as e:
            print(f"[finsight] ML pipeline error: {e}")
            traceback.print_exc()
    
    # Fallback to old training if pipeline not available
    print("[finsight] Falling back to legacy model training")
    feature_cols = ["Date_Number", "Month", "Day_of_Week"]
    X = df[feature_cols].fillna(0)

    for key, target in (("amount", "amount"),
                        ("expenses", "expenses"),
                        ("revenue", "revenue"),
                        ("profit", "profit")):
        if target not in df.columns or df[target].isna().all():
            continue
        y = df[target].fillna(0)
        if y.nunique() < 2:
            continue
        try:
            x_train, x_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42,
            )
            m = LinearRegression().fit(x_train, y_train)
            models[key] = {"model": m, "df_min": df["tx_date"].min(),
                           "metrics": {
                               "mae": float(mean_absolute_error(y_test, m.predict(x_test))),
                               "r2":  float(r2_score(y_test, m.predict(x_test))),
                           }}
        except Exception as exc:
            print(f"[finsight] Could not train {key} model: {exc}")
            models[key] = None
    
    # Also train risk model in fallback
    _train_risk_model_for(user_id, df)



def _train_risk_model_for(user_id: int, df: pd.DataFrame) -> None:
    """Train risk classifier using improved ML pipeline.
    
    Uses:
    - Proper train/test split (chronological)
    - Pre-defined risk thresholds (no target leakage)
    - DecisionTreeClassifier with optimized hyperparameters
    - Cross-validation for stability assessment
    - Model persistence to disk with metadata
    
    Risk classification is based on deterministic business rules:
    - HIGH RISK: Negative profit OR expenses > revenue
    - MEDIUM RISK: Profit margin < 8% OR expense ratio > 85%
    - LOW RISK: Otherwise
    
    These rules are applied to create labels BEFORE any features are used.
    """
    models = get_models(user_id)
    
    # Use new ML pipeline if available
    if get_pipeline is not None:
        try:
            pipeline = get_pipeline()
            result = pipeline.train_risk_classifier(df, user_id)
            
            if result.get('success'):
                metrics = result.get('metrics', {})
                
                # Store in session for immediate use
                try:
                    loaded_model, metadata = pipeline._load_model(user_id, 'risk')
                    features = metadata.get('features', ['revenue', 'expenses', 'profit', 'amount'])
                    models["risk"] = {
                        "model": loaded_model,
                        "features": features,
                        "periods": metrics.get('n_daily_periods', 0),
                        "metrics": {
                            "train_accuracy": metrics.get("train_accuracy", 0),
                            "test_accuracy": metrics.get("test_accuracy", 0),
                            "test_f1": metrics.get("test_f1_weighted", 0),
                            "overfitting": metrics.get("overfitting_warning", "Unknown"),
                        }
                    }
                    models.pop("risk_error", None)
                    print(f"[finsight] Trained risk classifier: "
                          f"accuracy={metrics.get('test_accuracy', 0):.4f}, "
                          f"F1={metrics.get('test_f1_weighted', 0):.4f}, "
                          f"periods={metrics.get('n_daily_periods', 0)}")
                except Exception as e:
                    print(f"[finsight] Could not load trained risk model: {e}")
                    models["risk"] = None
            else:
                error = result.get('error', 'Unknown error')
                print(f"[finsight] Could not train risk classifier: {error}")
                models["risk"] = None
                models["risk_error"] = error
            
            return
        except Exception as e:
            print(f"[finsight] Risk ML pipeline error: {e}")
            traceback.print_exc()
    
    # Fallback to old training if pipeline not available
    print("[finsight] Falling back to legacy risk model training")
    features = ["revenue", "expenses", "profit", "amount"]
    if df.empty or "tx_date" not in df.columns or any(c not in df.columns for c in features):
        models["risk"] = None
        return
    working = df.copy()
    for column in features:
        working[column] = pd.to_numeric(working[column], errors="coerce").fillna(0)
    periods = working.groupby(working["tx_date"].dt.date, as_index=False)[features].sum()
    if periods.empty:
        models["risk"] = None
        return
    revenue = periods["revenue"].replace(0, np.nan)
    margin = (periods["profit"] / revenue).fillna(-1)
    expense_ratio = (periods["expenses"] / revenue).replace([np.inf, -np.inf], np.nan).fillna(1)
    periods["risk_label"] = np.select(
        [(periods["profit"] < 0) | (expense_ratio > 1), (margin < 0.08) | (expense_ratio > 0.85)],
        ["HIGH RISK", "MEDIUM RISK"], default="LOW RISK",
    )
    try:
        classifier = DecisionTreeClassifier(max_depth=3, min_samples_leaf=2, random_state=42)
        classifier.fit(periods[features], periods["risk_label"])
        models["risk"] = {"model": classifier, "features": features, "periods": len(periods)}
    except Exception as exc:
        print(f"[finsight] Could not train risk classifier: {exc}")
        models["risk"] = None


# =====================================================
# Routes - Predictions
# =====================================================
@app.route("/predict", methods=["GET", "POST"])
@login_required
def predict():
    user_id = _session_user_id()
    company_id = _session_company_id()

    if request.method == "GET":
        _, df = _active_dataset_and_frame(user_id)
        available_dates = []
        if df is not None and not df.empty and "tx_date" in df.columns:
            available_dates = sorted(
                pd.to_datetime(df["tx_date"], errors="coerce")
                .dropna().dt.strftime("%Y-%m-%d").unique().tolist()
            )
        # Universal, dataset-agnostic prediction sections + business insight.
        analysis = _analysis_context(user_id)
        date_column = next(
            (name for name, kind in (analysis or {}).get("types", {}).items()
             if kind == "date"),
            "—",
        )
        return render_template(
            "predict.html",
            company_name=session["company_name"],
            has_data=analysis is not None and bool(
                df is not None and not df.empty
            ) or analysis is not None and bool(analysis.get("sections")),
            risk_min_date=(date.today() + timedelta(days=1)).isoformat(),
            risk_max_date=available_dates[-1] if available_dates else "",
            risk_default_date=(date.today() + timedelta(days=1)).isoformat() if available_dates else "",
            analysis=analysis if analysis is not None else {},
            insight=(analysis or {}).get("insight", ""),
            section_types=(analysis or {}).get("types", {}),
            date_column=date_column,
        )

    if not request.is_json:
        return jsonify({"error": _message("api.error.predict_json_required")}), 400
    payload = request.get_json(silent=True) or {}
    model_type = payload.get("model_type")
    date_str = payload.get("date")
    forecast_periods = payload.get("forecast_periods", 1)

    # ---------- Dynamic (universal) prediction support ----------
    if model_type not in ("amount", "expenses", "revenue", "profit"):
        analysis = _analysis_context(user_id)
        section = next(
            (s for s in (analysis or {}).get("sections", [])
             if s.get("target") == model_type),
            None,
        )
        if analysis is None or section is None:
            return jsonify({"error": _message("api.error.predict_invalid_model")}), 400
        model = next(
            (s for s in session_data.get(user_id, {}).get("analysis", {}).get("sections", [])
             if s.get("target") == model_type), None)
        result = universal_analysis.predict_dynamic_periods(model, forecast_periods) if (
            universal_analysis is not None and model is not None) else {"ok": False}
        if not result.get("ok"):
            return jsonify({"error": result.get("error",
                             "Prediction could not be generated.")}), 400
        return jsonify({
            "success": True,
            "prediction": result.get("value"),
            "label": result.get("label"),
            "forecasts": result.get("values", []),
            "frequency": result.get("frequency", "period"),
            "date": str(date_str or "next-period"),
            "model_type": model_type,
            "kind": result.get("type", "regression"),
            "chart": "",
        })
    if not date_str:
        return jsonify({"error": _message("api.error.predict_date_required")}), 400

    try:
        target_date = pd.to_datetime(date_str).date()
    except Exception:
        return jsonify({"error": _message("api.error.predict_invalid_date")}), 400

    dataset_id, df = _active_dataset_and_frame(user_id)
    models = get_models(user_id)
    model_info = models.get(model_type)

    if dataset_id is None or df is None or df.empty:
        return jsonify({"error": _message("api.error.predict_need_data")}), 400
    # Models from a previously uploaded dataset can be absent after a restart
    # or after a validation fix. Rebuild them from the active upload instead
    # of incorrectly asking the user to upload the same file again.
    if model_info is None:
        _train_models_for(user_id, df)
        models = get_models(user_id)
        model_info = models.get(model_type)
    if model_info is None:
        return jsonify({"error": _message("api.error.predict_need_data")}), 400

    try:
        if get_pipeline is not None:
            predicted_value, prediction_info = get_pipeline().predict_regression(
                user_id, model_type, {}, history_df=df, prediction_date=target_date
            )
            if predicted_value is None:
                return jsonify({"error": prediction_info.get("error") or _message("api.error.predict_invalid_input")}), 400
        else:
            date_number = (pd.to_datetime(target_date) - model_info["df_min"]).days
            X_future = pd.DataFrame({"Date_Number": [date_number], "Month": [target_date.month],
                                     "Day_of_Week": [target_date.weekday()]})
            predicted_value = float(model_info["model"].predict(X_future)[0])
            prediction_info = {
                "model_name": "linear_regression",
                "estimated_error": model_info.get("metrics", {}).get("rmse", 0),
            }
        predicted_value = round(predicted_value, 2)

        # Give the UI a meaningful, per-metric reference point.  This makes
        # advice compare this forecast with the user's recent actual data
        # instead of repeating generic text for every request.
        recent_baseline = None
        if model_type in df.columns:
            recent_values = pd.to_numeric(df[model_type], errors="coerce").dropna().tail(7)
            if not recent_values.empty:
                recent_baseline = round(float(recent_values.mean()), 2)

        # Save the prediction
        file_id = dataset_id

        actual_value = None
        if "tx_date" in df.columns and model_type in df.columns:
            matching = df[pd.to_datetime(df["tx_date"], errors="coerce").dt.date == target_date]
            if not matching.empty:
                actual_value = round(float(matching[model_type].fillna(0).sum()), 2)
        prediction_error = round(actual_value - predicted_value, 2) if actual_value is not None else None

        prediction_id = run_query(
            """
            INSERT INTO predictions
                (user_id, company_id, uploaded_file_id,
                 prediction_type, prediction_date, actual_value, predicted_value,
                 prediction_error, model_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, company_id, file_id, model_type, target_date, actual_value,
             predicted_value, prediction_error, "linear_regression"),
            commit=True,
        )["last_id"]

        add_history(
            user_id, company_id, "prediction",
            f"{model_type.title()} forecast for {target_date}",
            prediction_id=prediction_id, file_id=file_id,
            status="ok", details=f"value={predicted_value}; actual={actual_value}; error={prediction_error}",
        )

        _generate_powerbi_resources(user_id, company_id, session["company_name"])

        # Build the historical + prediction chart
        # A chart is an enhancement, never a reason for a valid ML result to
        # fail (some matplotlib backends can be unavailable on Windows).
        try:
            chart = _build_chart(df, model_type, target_date, predicted_value)
        except Exception as chart_exc:
            print(f"[finsight] Chart rendering skipped: {chart_exc}")
            chart = ""

        return jsonify({
            "success": True,
            "prediction": predicted_value,
            "date": str(target_date),
            "model_type": model_type,
            "chart": chart,
            "prediction_id": prediction_id,
            "baseline": recent_baseline,
            "model": {
                "name": prediction_info.get("model_name", "linear_regression"),
                "estimated_error": float(prediction_info.get("estimated_error") or 0),
            },
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": _message("api.error.predict_failed", error=str(exc))}), 500


def _build_chart(df: pd.DataFrame, model_type: str,
                 forecast_date: date, predicted_value: float) -> str:
    column_map = {"amount": "amount", "expenses": "expenses",
                  "revenue": "revenue", "profit": "profit"}
    col = column_map.get(model_type)
    if col is None or col not in df.columns:
        return ""

    df_plot = df.dropna(subset=["tx_date", col]).sort_values("tx_date")
    if df_plot.empty:
        return ""

    plt.figure(figsize=(10, 5))
    plt.plot(df_plot["tx_date"], df_plot[col], marker="o",
             linewidth=2, label=f"Historical {col.title()}")
    plt.scatter([pd.to_datetime(forecast_date)], [predicted_value],
                color="red", s=120, zorder=5, label="Forecast")
    plt.title(f"{col.title()} Forecast")
    plt.xlabel("Date")
    plt.ylabel(col.title())
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100)
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode()


@app.post("/risk-classify")
@login_required
def risk_classify():
    """Classify one date using only the current user's active upload."""
    user_id, company_id = _session_user_id(), _session_company_id()
    payload = request.get_json(silent=True) or {}
    try:
        selected_date = pd.to_datetime(payload.get("date")).date()
    except Exception:
        return jsonify({"error": _message("api.error.risk_invalid_date")}), 400
    try:
        dataset_id, df = _active_dataset_and_frame(user_id)
        risk_info = get_models(user_id).get("risk")
        if dataset_id is None or df is None or df.empty:
            return jsonify({"error": _message("api.error.risk_no_data")}), 400
        if risk_info is None:
            _train_models_for(user_id, df)
            risk_info = get_models(user_id).get("risk")
        if risk_info is None:
            return jsonify({"error": get_models(user_id).get(
                "risk_error", "Risk classifier training did not produce a model for this dataset."
            )}), 400
        matching = df[pd.to_datetime(df["tx_date"], errors="coerce").dt.date == selected_date]
        features = risk_info["features"]
        is_forecast = matching.empty
        if is_forecast:
            models = get_models(user_id)
            values = {}
            for column in features:
                model_info = models.get(column)
                if model_info is None:
                    return jsonify({"error": "Forecast models are unavailable for this dataset. Upload more varied dated data first."}), 400
                if get_pipeline is not None:
                    value, prediction_info = get_pipeline().predict_regression(
                        user_id, column, {}, history_df=df, prediction_date=selected_date
                    )
                    if value is None:
                        return jsonify({"error": prediction_info.get("error", "Risk forecast input is invalid.")}), 400
                    values[column] = float(value)
                else:
                    date_number = (pd.to_datetime(selected_date) - model_info["df_min"]).days
                    future_features = pd.DataFrame({"Date_Number": [date_number], "Month": [selected_date.month],
                                                    "Day_of_Week": [selected_date.weekday()]})
                    values[column] = float(model_info["model"].predict(future_features)[0])
        else:
            values = {column: float(pd.to_numeric(matching[column], errors="coerce").fillna(0).sum()) for column in features}
        if get_pipeline is not None:
            risk_level, risk_prediction = get_pipeline().predict_risk(user_id, values)
            if risk_level is None:
                return jsonify({"error": risk_prediction.get("error", "Risk prediction input is invalid.")}), 400
        else:
            risk_input = pd.DataFrame([values], columns=features).apply(pd.to_numeric, errors="coerce")
            if not np.isfinite(risk_input.to_numpy()).all():
                return jsonify({"error": "Risk prediction input contains missing or non-finite values."}), 400
            risk_level = str(risk_info["model"].predict(risk_input)[0])
        if values["profit"] < 0:
            explanation = _message("risk.explain.negative_forecast" if is_forecast else "risk.explain.negative_actual")
        elif values["revenue"] > 0 and values["expenses"] > values["revenue"]:
            explanation = _message("risk.explain.expense_over_forecast" if is_forecast else "risk.explain.expense_over_actual")
        elif values["revenue"] > 0 and values["profit"] / values["revenue"] < 0.08:
            explanation = _message("risk.explain.margin_low_forecast" if is_forecast else "risk.explain.margin_low_actual")
        else:
            explanation = _message("risk.explain.stable_forecast" if is_forecast else "risk.explain.stable_actual")
        risk_id = run_query(
            """INSERT INTO risk_classifications
               (user_id, company_id, uploaded_file_id, classification_date, risk_level,
                revenue, expenses, profit, amount, explanation)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (user_id, company_id, dataset_id, selected_date, risk_level, values["revenue"],
             values["expenses"], values["profit"], values["amount"], explanation), commit=True,
        )["last_id"]
        add_history(user_id, company_id, "risk_classification", f"{risk_level} for {selected_date}",
                    file_id=dataset_id, status="ok", details=explanation)
        return jsonify({"success": True, "risk_id": risk_id, "risk_level": risk_level,
                        "date": str(selected_date), "revenue": values["revenue"],
                        "expenses": values["expenses"], "profit": values["profit"],
                        "amount": values["amount"], "explanation": explanation})
    except mysql.connector.Error:
        return jsonify({"error": "Risk classification could not be saved because the database is unavailable."}), 500
    except Exception as exc:
        print(f"[finsight] Risk classification failed: {exc}")
        return jsonify({"error": "Risk classification could not be completed."}), 500


# =====================================================
# Routes - History
# =====================================================
@app.route("/history")
@login_required
def history():
    user_id = _session_user_id()
    company_id = _session_company_id()
    events = run_query(
        """
        SELECT
            h.id, h.event_type, h.event_title, h.status, h.details, h.created_at,
            h.file_id, h.prediction_id,
            u.original_name  AS file_name,
            p.prediction_type, p.prediction_date, p.predicted_value
        FROM dashboard_history h
        LEFT JOIN uploaded_files u ON u.id = h.file_id
        LEFT JOIN predictions    p ON p.prediction_id = h.prediction_id
        WHERE h.user_id = %s
        ORDER BY h.created_at DESC
        LIMIT 200
        """,
        (user_id,),
        fetchall=True,
    )["rows"] or []

    return render_template(
        "history.html",
        company_name=session["company_name"],
        history=events,
    )


@app.route("/api/history")
@login_required
def api_history():
    user_id = _session_user_id()
    events = run_query(
        """
        SELECT id, event_type, event_title, status, details, created_at
        FROM dashboard_history
        WHERE user_id = %s
        ORDER BY created_at DESC
        """,
        (user_id,),
        fetchall=True,
    )["rows"] or []

    for e in events:
        e["created_at"] = e["created_at"].isoformat() if e.get("created_at") else None
    return jsonify(events)


# =====================================================
# Routes - Power BI
# =====================================================
@app.route("/powerbi")
@login_required
def powerbi():
    """Local Power BI Desktop exports for the signed-in user's active upload."""
    user_id = _session_user_id()
    dataset_id = _current_dataset_id(user_id)
    return render_template(
        "powerbi.html",
        company_name=session["company_name"],
        has_data=dataset_id is not None,
    )


def _excel_download(frame: pd.DataFrame, filename: str):
    """Return one in-memory .xlsx file without exposing a server file path."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl", datetime_format="yyyy-mm-dd") as writer:
        frame.to_excel(writer, sheet_name="Data", index=False)
        worksheet = writer.sheets["Data"]
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for column_cells in worksheet.columns:
            width = max(len(str(cell.value or "")) for cell in column_cells[:200]) + 2
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(width, 35)
    output.seek(0)
    return send_file(
        output, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _visual_dashboard_download(user_id: int, company_name: str, dataset_id: int):
    """Build a charted Excel dashboard from one user's current dataset only."""
    frames = _powerbi_export_frames(user_id, company_name, dataset_id)
    cleaned = frames["Cleaned_Data"]
    if cleaned.empty:
        return None
    monthly = frames["Monthly_Analysis"]
    categories = frames["Category_Analysis"]
    predictions = frames["Predictions"]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl", datetime_format="yyyy-mm-dd") as writer:
        dashboard = writer.book.create_sheet("Dashboard", 0)
        dashboard.sheet_view.showGridLines = False
        dashboard.merge_cells("A1:N1")
        dashboard["A1"] = f"FinSight AI | {company_name} | Current Dataset Dashboard"
        dashboard["A1"].font = Font(size=18, bold=True, color="FFFFFF")
        dashboard["A1"].fill = PatternFill("solid", fgColor="17324D")
        dashboard["A1"].alignment = Alignment(horizontal="center")
        dashboard.row_dimensions[1].height = 32
        dashboard.merge_cells("A2:N2")
        dashboard["A2"] = "Generated locally from this user's current cleaned data and predictions"
        dashboard["A2"].font = Font(italic=True, color="5B6B7A")
        dashboard["A2"].alignment = Alignment(horizontal="center")

        kpis = [
            ("A4", "Total Revenue", cleaned["Revenue"].fillna(0).sum()),
            ("D4", "Total Expenses", cleaned["Expenses"].fillna(0).sum()),
            ("G4", "Total Profit", cleaned["Profit"].fillna(0).sum()),
            ("J4", "Transactions", len(cleaned)),
        ]
        for cell, label, value in kpis:
            label_cell = dashboard[cell]
            value_cell = dashboard.cell(row=label_cell.row + 1, column=label_cell.column)
            label_cell.value = label
            label_cell.font = Font(bold=True, color="FFFFFF")
            label_cell.fill = PatternFill("solid", fgColor="287D8E")
            value_cell.value = float(value or 0)
            value_cell.font = Font(size=15, bold=True, color="17324D")
            value_cell.number_format = '#,##0.00' if label != "Transactions" else '0'
            dashboard.merge_cells(start_row=4, start_column=label_cell.column,
                                  end_row=4, end_column=label_cell.column + 1)
            dashboard.merge_cells(start_row=5, start_column=label_cell.column,
                                  end_row=5, end_column=label_cell.column + 1)

        tables = {
            "Cleaned_Data": cleaned,
            "Monthly_Analysis": monthly,
            "Category_Analysis": categories,
            "Predictions": predictions,
        }
        for sheet_name, frame in tables.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column_cells in worksheet.columns:
                width = max(len(str(cell.value or "")) for cell in column_cells[:200]) + 2
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(width, 35)

        if not monthly.empty:
            sheet = writer.book["Monthly_Analysis"]
            chart = LineChart()
            chart.title = "Monthly Revenue, Expenses and Profit"
            chart.y_axis.title = "Value"
            chart.x_axis.title = "Month"
            chart.height, chart.width = 8, 15
            chart.add_data(Reference(sheet, min_col=3, max_col=5, min_row=1,
                                     max_row=len(monthly) + 1), titles_from_data=True)
            chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=len(monthly) + 1))
            dashboard.add_chart(chart, "A8")

        if not categories.empty:
            sheet = writer.book["Category_Analysis"]
            chart = BarChart()
            chart.type = "bar"
            chart.style = 10
            chart.title = "Revenue by Category"
            chart.x_axis.title = "Revenue"
            chart.height, chart.width = 8, 15
            chart.add_data(Reference(sheet, min_col=3, max_col=3, min_row=1,
                                     max_row=len(categories) + 1), titles_from_data=True)
            chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=len(categories) + 1))
            dashboard.add_chart(chart, "J8")

        if not predictions.empty:
            sheet = writer.book["Predictions"]
            chart = LineChart()
            chart.title = "Prediction Values by Date"
            chart.y_axis.title = "Predicted Value"
            chart.x_axis.title = "Date"
            chart.height, chart.width = 8, 15
            chart.add_data(Reference(sheet, min_col=5, max_col=5, min_row=1,
                                     max_row=len(predictions) + 1), titles_from_data=True)
            chart.set_categories(Reference(sheet, min_col=3, min_row=2,
                                           max_row=len(predictions) + 1))
            dashboard.add_chart(chart, "A25")

        for column in range(1, 15):
            dashboard.column_dimensions[chr(64 + column)].width = 14

    output.seek(0)
    return send_file(
        output, as_attachment=True, download_name="finsight_visual_dashboard.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _active_upload_record(user_id: int) -> dict | None:
    return run_query(
        """SELECT id, original_name, stored_name FROM uploaded_files
           WHERE user_id=%s AND status='processed' ORDER BY id DESC LIMIT 1""",
        (user_id,), fetchone=True,
    )["row"]


@app.route("/powerbi/download-excel/<export_type>")
@login_required
def download_powerbi_excel(export_type: str):
    """Download raw, cleaned, or prediction data for only the active user/upload."""
    if export_type not in {"raw", "cleaned", "predictions", "dashboard"}:
        return jsonify({"error": "Unknown Power BI export."}), 404

    user_id = _session_user_id()
    upload = _active_upload_record(user_id)
    if not upload:
        return jsonify({"error": "No processed dataset has been uploaded for this user."}), 404
    dataset_id = int(upload["id"])

    try:
        if export_type == "dashboard":
            dashboard = _visual_dashboard_download(user_id, session["company_name"], dataset_id)
            if dashboard is None:
                return jsonify({"error": "Cleaned data does not exist for the current dataset."}), 404
            return dashboard

        if export_type == "raw":
            resource = _powerbi_resource(user_id, create=False)
            if not resource:
                return jsonify({"error": "The uploaded source file is unavailable."}), 404
            source = POWERBI_ROOT / resource["folder_token"] / "uploads" / upload["stored_name"]
            if not source.is_file():
                return jsonify({"error": "The original uploaded file is unavailable."}), 404
            raw = (pd.read_csv(source, dtype=object, keep_default_na=False)
                   if source.suffix.lower() == ".csv"
                   else pd.read_excel(source, dtype=object))
            return _excel_download(raw, "finsight_raw_data.xlsx")

        if export_type == "cleaned":
            frames = _powerbi_export_frames(user_id, session["company_name"], dataset_id)
            cleaned = frames["Cleaned_Data"].copy()
            if cleaned.empty:
                return jsonify({"error": "Cleaned data does not exist for the current dataset."}), 404
            dates = pd.to_datetime(cleaned["Date"], errors="coerce")
            if dates.notna().any():
                cleaned["Date_Number"] = (dates - dates.min()).dt.days
                cleaned["Month"] = dates.dt.month
                cleaned["Day_of_Week"] = dates.dt.dayofweek
            return _excel_download(cleaned, "finsight_cleaned_data.xlsx")

        prediction_rows = run_query(
            """SELECT prediction_id, prediction_type, prediction_date, predicted_value
               FROM predictions
               WHERE user_id=%s AND uploaded_file_id=%s
               ORDER BY prediction_date, prediction_id""",
            (user_id, dataset_id), fetchall=True,
        )["rows"] or []
        if not prediction_rows:
            return jsonify({"error": "No predictions exist for the current dataset yet."}), 404
        predictions = pd.DataFrame(prediction_rows)
        predictions["prediction_type"] = predictions["prediction_type"].str.lower()
        # Keep the latest generated value when a user predicted the same metric/date again.
        predictions = predictions.drop_duplicates(["prediction_date", "prediction_type"], keep="last")
        predictions = predictions.pivot(
            index="prediction_date", columns="prediction_type", values="predicted_value"
        ).reset_index().rename(columns={
            "prediction_date": "Date", "amount": "Predicted_Amount",
            "revenue": "Predicted_Revenue", "expenses": "Predicted_Expenses",
            "profit": "Predicted_Profit",
        })
        ordered = ["Date", "Predicted_Amount", "Predicted_Revenue", "Predicted_Expenses", "Predicted_Profit"]
        predictions = predictions.reindex(columns=ordered).sort_values("Date")
        risk_rows = run_query(
            """SELECT classification_date AS Date, risk_level AS Risk_Level
               FROM risk_classifications
               WHERE user_id=%s AND uploaded_file_id=%s
               ORDER BY classification_date, risk_id""",
            (user_id, dataset_id), fetchall=True,
        )["rows"] or []
        if risk_rows:
            risks = pd.DataFrame(risk_rows).drop_duplicates("Date", keep="last")
            predictions = predictions.merge(risks, on="Date", how="left")
        else:
            predictions["Risk_Level"] = None
        return _excel_download(predictions, "finsight_predictions.xlsx")
    except (mysql.connector.Error, OSError, ValueError, pd.errors.ParserError) as exc:
        print(f"[finsight] Power BI {export_type} export failed: {exc}")
        return jsonify({"error": f"Could not create the {export_type} export. Please try again."}), 500


def _powerbi_export_frames(user_id: int, company_name: str,
                           dataset_id: int | None = None) -> dict[str, pd.DataFrame]:
    """Build Power BI tables from backend-enforced user-owned data only."""
    cleaned_rows = run_query(
        """
         SELECT tx_date AS Date, transaction_id AS Transaction_ID, description AS Description,
               amount AS Amount, revenue AS Revenue, expenses AS Expenses, profit AS Profit,
               tx_type AS Transaction_Type, category AS Category,
               payment_method AS Payment_Method, department AS Department, city AS City,
             status AS Status, uploaded_file_id AS Upload_File_ID, user_id AS User_ID,
             created_at AS Imported_At
        FROM financial_data
                WHERE user_id = %s
                    AND (%s IS NULL OR uploaded_file_id = %s)
        ORDER BY tx_date, id
        """,
        (user_id, dataset_id, dataset_id), fetchall=True,
    )["rows"] or []
    prediction_rows = run_query(
        """
         SELECT prediction_id AS Prediction_ID, prediction_type AS Prediction_Type,
             prediction_date AS Prediction_Date, actual_value AS Actual_Value,
             predicted_value AS Predicted_Value, prediction_error AS Prediction_Error,
               model_name AS Model, created_at AS Created_At
        FROM predictions
                WHERE user_id = %s
                    AND (%s IS NULL OR uploaded_file_id = %s)
        ORDER BY prediction_date, prediction_id
        """,
        (user_id, dataset_id, dataset_id), fetchall=True,
    )["rows"] or []

    cleaned_columns = [
        "Date", "Transaction_ID", "Description", "Amount", "Revenue", "Expenses",
        "Profit", "Transaction_Type", "Category", "Payment_Method", "Department",
        "City", "Status", "Upload_File_ID", "User_ID", "Imported_At",
    ]
    prediction_columns = [
        "Prediction_ID", "Prediction_Type", "Prediction_Date", "Actual_Value",
        "Predicted_Value", "Prediction_Error", "Model", "Created_At",
    ]
    cleaned = pd.DataFrame(cleaned_rows).reindex(columns=cleaned_columns)
    predictions = pd.DataFrame(prediction_rows).reindex(columns=prediction_columns)
    metric_columns = ["Amount", "Revenue", "Expenses", "Profit"]
    for column in metric_columns:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    cleaned["Company"] = company_name

    kpis = pd.DataFrame([{
        "Company": company_name,
        "User_ID": user_id,
        "Exported_At": datetime.now().replace(microsecond=0),
        "Cleaned_Rows": int(len(cleaned)),
        "Predictions": int(len(predictions)),
        "Total_Amount": cleaned["Amount"].fillna(0).sum(),
        "Total_Revenue": cleaned["Revenue"].fillna(0).sum(),
        "Total_Expenses": cleaned["Expenses"].fillna(0).sum(),
        "Total_Profit": cleaned["Profit"].fillna(0).sum(),
        "First_Date": cleaned["Date"].min() if not cleaned.empty else None,
        "Last_Date": cleaned["Date"].max() if not cleaned.empty else None,
    }])

    if cleaned.empty:
        monthly = pd.DataFrame(columns=["Month", *metric_columns, "Transactions"])
        categories = pd.DataFrame(columns=["Category", *metric_columns, "Transactions"])
    else:
        dated = cleaned.copy()
        dated["Date"] = pd.to_datetime(dated["Date"], errors="coerce")
        dated = dated.dropna(subset=["Date"])
        if dated.empty:
            monthly = pd.DataFrame(columns=["Month", *metric_columns, "Transactions"])
        else:
            dated["Month"] = dated["Date"].dt.to_period("M").astype(str)
            monthly = dated.groupby("Month", as_index=False).agg(
                Amount=("Amount", "sum"), Revenue=("Revenue", "sum"),
                Expenses=("Expenses", "sum"), Profit=("Profit", "sum"),
                Transactions=("Date", "size"),
            )
        grouped = cleaned.copy()
        grouped["Category"] = grouped["Category"].fillna("Uncategorized")
        categories = grouped.groupby("Category", as_index=False).agg(
            Amount=("Amount", "sum"), Revenue=("Revenue", "sum"),
            Expenses=("Expenses", "sum"), Profit=("Profit", "sum"),
            Transactions=("Category", "size"),
        ).sort_values("Revenue", ascending=False)

    def grouped_dimension(column: str) -> pd.DataFrame:
        columns = [column, "Transactions", *metric_columns]
        if cleaned.empty or column not in cleaned.columns:
            return pd.DataFrame(columns=columns)
        dimension = cleaned.copy()
        dimension[column] = dimension[column].fillna("Unspecified").astype(str)
        return dimension.groupby(column, as_index=False).agg(
            Transactions=(column, "size"), Amount=("Amount", "sum"),
            Revenue=("Revenue", "sum"), Expenses=("Expenses", "sum"),
            Profit=("Profit", "sum"),
        ).sort_values("Transactions", ascending=False)

    distributions = {
        "City_Analysis": grouped_dimension("City"),
        "Payment_Analysis": grouped_dimension("Payment_Method"),
        "Company_Analysis": grouped_dimension("Company"),
        "Department_Analysis": grouped_dimension("Department"),
        "Status_Analysis": grouped_dimension("Status"),
    }

    prediction_comparison = pd.DataFrame(
        columns=["Prediction_ID", "Prediction_Type", "Date", "Actual", "Predicted", "Difference"]
    )
    if not predictions.empty:
        actual_by_date = cleaned.copy()
        actual_by_date["Date"] = pd.to_datetime(actual_by_date["Date"], errors="coerce").dt.date
        actual_by_date = actual_by_date.dropna(subset=["Date"])
        comparison_rows = []
        for _, prediction in predictions.iterrows():
            metric = str(prediction["Prediction_Type"]).lower()
            metric_column = next((column for column in metric_columns
                                  if column.lower() == metric), None)
            prediction_date = pd.to_datetime(prediction["Prediction_Date"], errors="coerce")
            prediction_date = prediction_date.date() if not pd.isna(prediction_date) else None
            actual = None
            if metric_column and prediction_date and not actual_by_date.empty:
                matches = actual_by_date[actual_by_date["Date"] == prediction_date]
                if not matches.empty:
                    actual = float(matches[metric_column].fillna(0).sum())
            predicted = float(prediction["Predicted_Value"])
            comparison_rows.append({
                "Prediction_ID": prediction["Prediction_ID"],
                "Prediction_Type": prediction["Prediction_Type"],
                "Date": prediction_date,
                "Actual": actual,
                "Predicted": predicted,
                "Difference": actual - predicted if actual is not None else None,
            })
        prediction_comparison = pd.DataFrame(comparison_rows)

    readme = pd.DataFrame([
        {"Sheet": "Dashboard", "Description": "Automatic KPI cards and Excel charts for the exported company data."},
        {"Sheet": "Cleaned_Data", "Description": "Validated, de-duplicated financial rows owned by this user."},
        {"Sheet": "Predictions", "Description": "Predictions created by this user."},
        {"Sheet": "Prediction_vs_Actual", "Description": "Prediction dates matched to same-day actual totals where available."},
        {"Sheet": "KPI_Summary", "Description": "Current totals and export metadata."},
        {"Sheet": "Monthly_Analysis", "Description": "Monthly revenue, expenses, profit, amount and transaction count."},
        {"Sheet": "Category_Analysis", "Description": "Financial totals and transaction count by category."},
        {"Sheet": "City_Analysis", "Description": "Transactions and financial totals grouped by city."},
        {"Sheet": "Payment_Analysis", "Description": "Transactions and financial totals grouped by payment method."},
        {"Sheet": "Company_Analysis", "Description": "Transactions and financial totals grouped by company when available."},
        {"Sheet": "Department_Analysis", "Description": "Transactions and financial totals grouped by department when available."},
        {"Sheet": "Status_Analysis", "Description": "Transactions and financial totals grouped by status when available."},
    ])
    return {"Cleaned_Data": cleaned, "Predictions": predictions,
            "Prediction_vs_Actual": prediction_comparison, "KPI_Summary": kpis,
            "Monthly_Analysis": monthly, "Category_Analysis": categories,
            **distributions, "README": readme}


def _generate_powerbi_resources(user_id: int, company_id: int, company_name: str,
                                dataset_id: int | None = None) -> dict:
    """Create/update private CSV sources and a copy of the local Desktop template.

    The browser never receives filesystem paths.  The resource directory uses an
    opaque token and is authorized by `user_id` at every download endpoint.
    """
    resource = _powerbi_resource(user_id)
    if dataset_id is None:
        dataset_id = _current_dataset_id(user_id)
    paths = _powerbi_paths(resource, user_id, dataset_id)
    paths["data"].mkdir(parents=True, exist_ok=True)
    frames = _powerbi_export_frames(user_id, company_name, dataset_id)
    for sheet_name, frame in frames.items():
        if sheet_name == "README":
            continue
        filename = sheet_name.lower() + ".csv"
        frame.to_csv(paths["data"] / filename, index=False)
    frames["Cleaned_Data"].to_csv(paths["financial"], index=False)
    frames["Predictions"].to_csv(paths["predictions"], index=False)
    # Always copy the neutral visual template into the active user's private
    # dataset folder. This prevents a stale PBIX from a previous dataset/template
    # version being returned to the current user.
    if POWERBI_TEMPLATE.is_file():
        shutil.copy2(POWERBI_TEMPLATE, paths["pbix"])
    paths["readme"].write_text(
        "FinSight AI Power BI Desktop\n\n"
        "This folder belongs to one authenticated FinSight user. In Power BI Desktop, "
        "set the existing Cleaned_Data query source to data/financial_data.csv and the "
        "Predictions query source to data/predictions.csv, then save and use Refresh. "
        "Additional visualization-ready CSV tables are in the same data folder. "
        "Do not connect this report to the shared MySQL database.\n\n"
        "The Flask app generates data and copies the reusable PBIX template; it does not "
        "edit or create a PBIX file.\n",
        encoding="utf-8",
    )
    run_query(
        """UPDATE user_powerbi_resources SET financial_csv=%s, predictions_csv=%s,
           pbix_filename=%s, generated_at=NOW() WHERE user_id=%s""",
        ("data/financial_data.csv", "data/predictions.csv", paths["pbix"].name if paths["pbix"].exists() else None, user_id),
        commit=True,
    )
    if dataset_id is not None and paths["pbix"].exists():
        run_query(
            """INSERT INTO powerbi_desktop_reports
               (user_id, uploaded_file_id, pbix_filename, status)
               VALUES (%s, %s, %s, 'generated')
               ON DUPLICATE KEY UPDATE pbix_filename=VALUES(pbix_filename), status='generated'""",
            (user_id, dataset_id, paths["pbix"].name), commit=True,
        )
    return {"resource": resource, "paths": paths}


@app.route("/powerbi/export")
@login_required
def export_powerbi_data():
    """Download one Excel workbook that Power BI Desktop can import locally."""
    user_id = _session_user_id()
    company_id = _session_company_id()
    company_name = session["company_name"]
    frames = _powerbi_export_frames(user_id, company_name)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl", datetime_format="yyyy-mm-dd hh:mm") as writer:
        dashboard = writer.book.create_sheet("Dashboard", 0)
        dashboard.sheet_view.showGridLines = False
        dashboard.merge_cells("A1:N1")
        dashboard["A1"] = f"FinSight AI | {company_name}"
        dashboard["A1"].font = Font(size=20, bold=True, color="FFFFFF")
        dashboard["A1"].fill = PatternFill("solid", fgColor="17324D")
        dashboard["A1"].alignment = Alignment(horizontal="left", vertical="center")
        dashboard.row_dimensions[1].height = 34
        dashboard.merge_cells("A2:N2")
        dashboard["A2"] = "Financial overview generated from your exported data"
        dashboard["A2"].font = Font(italic=True, color="5B6B7A")

        for sheet_name, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            if not frame.empty and len(frame.columns) > 0:
                table_name = f"tbl_{sheet_name.replace('-', '_')}"
                table = Table(displayName=table_name, ref=worksheet.dimensions)
                table.tableStyleInfo = TableStyleInfo(
                    name="TableStyleMedium2", showFirstColumn=False,
                    showLastColumn=False, showRowStripes=True, showColumnStripes=False,
                )
                worksheet.add_table(table)
            for column_cells in worksheet.columns:
                width = max(len(str(cell.value or "")) for cell in column_cells[:200]) + 2
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(width, 35)

        kpi_values = frames["KPI_Summary"].iloc[0]
        kpis = [
            ("A4", "Total Revenue", kpi_values["Total_Revenue"]),
            ("D4", "Total Expenses", kpi_values["Total_Expenses"]),
            ("G4", "Total Profit", kpi_values["Total_Profit"]),
            ("J4", "Transactions", kpi_values["Cleaned_Rows"]),
        ]
        for cell, label, value in kpis:
            label_cell = dashboard[cell]
            value_cell = dashboard.cell(row=label_cell.row + 1, column=label_cell.column)
            label_cell.value = label
            label_cell.font = Font(bold=True, color="FFFFFF")
            label_cell.fill = PatternFill("solid", fgColor="287D8E")
            value_cell.value = float(value or 0)
            value_cell.font = Font(size=16, bold=True, color="17324D")
            value_cell.number_format = '#,##0.00'
            dashboard.merge_cells(start_row=4, start_column=label_cell.column,
                                  end_row=4, end_column=label_cell.column + 1)
            dashboard.merge_cells(start_row=5, start_column=label_cell.column,
                                  end_row=5, end_column=label_cell.column + 1)

        monthly = frames["Monthly_Analysis"]
        if not monthly.empty:
            monthly_chart = LineChart()
            monthly_chart.title = "Monthly Revenue, Expenses and Profit"
            monthly_chart.y_axis.title = "Value"
            monthly_chart.x_axis.title = "Month"
            monthly_chart.height = 8
            monthly_chart.width = 15
            monthly_chart.add_data(Reference(writer.book["Monthly_Analysis"], min_col=3, max_col=5,
                                             min_row=1, max_row=len(monthly) + 1), titles_from_data=True)
            monthly_chart.set_categories(Reference(writer.book["Monthly_Analysis"], min_col=1,
                                                   min_row=2, max_row=len(monthly) + 1))
            dashboard.add_chart(monthly_chart, "A8")

        categories = frames["Category_Analysis"]
        if not categories.empty:
            category_chart = BarChart()
            category_chart.type = "bar"
            category_chart.style = 10
            category_chart.title = "Revenue by Category"
            category_chart.x_axis.title = "Revenue"
            category_chart.height = 8
            category_chart.width = 15
            category_sheet = writer.book["Category_Analysis"]
            category_chart.add_data(Reference(category_sheet, min_col=3, max_col=3,
                                              min_row=1, max_row=len(categories) + 1), titles_from_data=True)
            category_chart.set_categories(Reference(category_sheet, min_col=1,
                                                    min_row=2, max_row=len(categories) + 1))
            dashboard.add_chart(category_chart, "J8")

            mix_chart = PieChart()
            mix_chart.title = "Transactions by Category"
            mix_chart.height = 8
            mix_chart.width = 15
            mix_chart.add_data(Reference(category_sheet, min_col=6, max_col=6,
                                         min_row=1, max_row=len(categories) + 1), titles_from_data=True)
            mix_chart.set_categories(Reference(category_sheet, min_col=1,
                                               min_row=2, max_row=len(categories) + 1))
            dashboard.add_chart(mix_chart, "A25")

        for column in range(1, 15):
            dashboard.column_dimensions[chr(64 + column)].width = 14
        dashboard.freeze_panes = "A4"

    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"FinSight_powerbi_data_{timestamp}.xlsx"
    add_history(user_id, company_id, "powerbi_export", filename, status="ok",
                details="Power BI-ready workbook downloaded")
    return send_file(output, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/powerbi/generate")
@login_required
def generate_powerbi_data():
    user_id = _session_user_id()
    dataset_id = _current_dataset_id(user_id)
    result = _generate_powerbi_resources(user_id, _session_company_id(), session["company_name"], dataset_id)
    add_history(user_id, _session_company_id(), "powerbi_generate", "Power BI Desktop data updated", status="ok")
    if not request.is_json:
        return redirect(url_for("powerbi"))
    return jsonify({"success": True, "has_template": result["paths"]["pbix"].exists()})


@app.route("/powerbi/download/<resource_type>")
@login_required
def download_powerbi_resource(resource_type: str):
    user_id = _session_user_id()
    resource = _powerbi_resource(user_id, create=False)
    if not resource:
        return jsonify({"error": "Power BI resource not found"}), 404
    report = run_query(
        """SELECT uploaded_file_id, pbix_filename FROM powerbi_desktop_reports
           WHERE user_id=%s ORDER BY updated_at DESC LIMIT 1""",
        (user_id,), fetchone=True,
    )["row"]
    if not report:
        return jsonify({"error": "Power BI report not generated"}), 404
    paths = _powerbi_paths(resource, user_id, report["uploaded_file_id"])
    allowed = {"template": paths["pbix"], "financial_data": paths["financial"],
               "predictions": paths["predictions"], "instructions": paths["readme"]}
    target = allowed.get(resource_type)
    if target is None or not target.is_file():
        return jsonify({"error": "Power BI resource not found"}), 404
    return send_file(target, as_attachment=True, download_name=target.name)


# =====================================================
# Authenticated upload download
# =====================================================
@app.route("/uploads/<int:file_id>")
@login_required
def uploaded_file(file_id: int):
    row = run_query("SELECT stored_name FROM uploaded_files WHERE id=%s AND user_id=%s",
                    (file_id, _session_user_id()), fetchone=True)["row"]
    if not row:
        return jsonify({"error": "File not found"}), 404
    resource = _powerbi_resource(_session_user_id(), create=False)
    if not resource:
        return jsonify({"error": "File not found"}), 404
    target = POWERBI_ROOT / resource["folder_token"] / "uploads" / row["stored_name"]
    if not target.is_file():
        return jsonify({"error": "File not found"}), 404
    return send_file(target, as_attachment=True, download_name=target.name.split("_", 1)[-1])


# =====================================================
# Error handlers
# =====================================================
@app.errorhandler(404)
def not_found(_):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("404.html"), 404


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "Uploaded file is too large. Maximum size is 16MB."}), 413


@app.errorhandler(500)
def server_error(_):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return render_template("500.html"), 500


# =====================================================
# Entry point
# =====================================================
if __name__ == "__main__":
    init_database()
    host = _env("FLASK_RUN_HOST", "0.0.0.0")
    port = _int_env("PORT", _int_env("FLASK_RUN_PORT", 5000))
    debug = _bool_env("FLASK_DEBUG", False)
    print(f"[finsight] Starting Flask on http://{host}:{port} (debug={debug})")
    app.run(host=host, port=port, debug=debug)
