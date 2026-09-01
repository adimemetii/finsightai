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
import json
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
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
try:
    from sklearn.linear_model import LinearRegression
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_absolute_error, r2_score
except ImportError as exc:
    # Keep health checks, authentication, uploads, analytics, and chat
    # available if an optional native ML wheel is unavailable on a host.
    print(f"[finsight] Warning: ML dependencies unavailable: {type(exc).__name__}")
    LinearRegression = None
    train_test_split = None
    mean_absolute_error = None
    r2_score = None
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from data_mapping import (
    CANONICAL_NUMERIC_FIELDS,
    CANONICAL_TEXT_FIELDS,
    FIELD_SPECS,
    apply_mapping,
    clean_dataframe,
    detect_columns,
    json_safe_record,
    profile_dataframe,
    profile_json_payload,
)

# ML Pipeline - improved ML with proper validation and no leakage
try:
    from ml_pipeline import get_pipeline, RuleBasedRiskModel
except ImportError as e:
    print(f"[finsight] Warning: Could not import ml_pipeline: {e}")
    get_pipeline = None
    RuleBasedRiskModel = None

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

from init_db import create_tables as create_database_tables


# =====================================================
# Configuration
# =====================================================
BASE_DIR = Path(__file__).resolve().parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


_DB_ENV_ALIASES = {
    "DB_HOST": ("DB_HOST", "MYSQL_HOST"),
    "DB_PORT": ("DB_PORT", "MYSQL_PORT"),
    "DB_USER": ("DB_USER", "MYSQL_USER"),
    "DB_PASSWORD": ("DB_PASSWORD", "MYSQL_PASSWORD"),
    "DB_NAME": ("DB_NAME", "MYSQL_DATABASE"),
    "DB_SSL_CA": ("DB_SSL_CA", "MYSQL_SSL_CA"),
}


def _db_env(name: str, default: str = "") -> str:
    """Read the documented DB_* names and legacy MYSQL_* aliases."""
    for candidate in _DB_ENV_ALIASES.get(name, (name,)):
        value = _env(candidate)
        if value:
            return value
    return default


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
    required = ("DB_HOST", "DB_PORT", "DB_USER", "DB_NAME")
    missing = [name for name in required if not _db_env(name)]
    if missing:
        raise RuntimeError("Missing required database environment variable(s): " + ", ".join(missing))
    try:
        port = int(_db_env("DB_PORT"))
    except ValueError as exc:
        raise RuntimeError("DB_PORT must be a number") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("DB_PORT must be between 1 and 65535")
    config = {
        "host": _db_env("DB_HOST"),
        "port": port,
        "user": _db_env("DB_USER"),
        "password": _db_env("DB_PASSWORD"),
        "database": _db_env("DB_NAME"),
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    }
    ssl_ca = _db_env("DB_SSL_CA")
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
    e.lower() for e in _env("ALLOWED_EXTENSIONS", "csv,xlsx,xls,json").split(",") if e
} | {"csv", "xlsx", "xls", "json"}
MAX_DATA_COLUMNS = _int_env("MAX_DATA_COLUMNS", 200)
GROQ_API_KEY = _env("GROQ_API_KEY")
GROQ_MODEL = _env("GROQ_MODEL") or "openai/gpt-oss-120b"
GROQ_TIMEOUT = max(10, min(120, _int_env("GROQ_TIMEOUT", 45)))




app = Flask(__name__)
_production_mode = _env("FLASK_ENV", "production").lower() == "production"
app.config.update(
    SECRET_KEY=SECRET_KEY,
    MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
    UPLOAD_FOLDER=str(UPLOAD_FOLDER),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_bool_env("SESSION_COOKIE_SECURE", _production_mode),
)
if _production_mode and not _env("SECRET_KEY"):
    app.logger.warning("SECRET_KEY is not configured; sessions will be invalidated when the server restarts.")
if i18n is not None:
    i18n.init_app(app)


# =====================================================
# MySQL connection pool (lazy - created on first request so that
# any updates to .env credentials are always picked up).
# =====================================================
db_pool = None


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
            except Exception as rebuild_exc:
                # Keep the original connector failure visible in the log; a
                # silent fallback makes a bad deployment configuration hard to
                # distinguish from a stale pooled connection.
                print(
                    "[finsight] Pool rebuild failed "
                    f"({type(rebuild_exc).__name__}: {rebuild_exc})."
                )
            # Last resort - direct connect with the current environment configuration.
            return mysql.connector.connect(**_db_config())
    return mysql.connector.connect(**_db_config())


def run_query(sql: str, params: tuple | list | None = None, *, fetchone=False, fetchall=False, commit=False):
    """Convenience helper for queries that don't need a long-lived cursor."""
    conn = get_db()
    cur = None
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
        return {"row": result, "rows": result if fetchall else None, "last_id": last_id}
    finally:
        if cur is not None:
            cur.close()
        conn.close()


# Existing deployments may use the older users-table naming convention. Keep
# the aliases deliberately small and only interpolate names returned by the
# database itself into SQL identifiers.
_USER_COLUMN_ALIASES = {
    "display": ("name", "full_name", "username", "user_name", "display_name"),
    "first_name": ("firstName", "first_name", "firstname"),
    "last_name": ("lastName", "last_name", "lastname"),
    "email": ("email", "email_address"),
    "password": ("password_hash", "password", "passwd", "hashed_password"),
    "company_id": ("company_id", "companyId"),
    "role": ("role", "user_role"),
    "created_at": ("created_at", "createdAt"),
}


def _quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _find_user_columns(cursor) -> dict[str, str]:
    """Discover live users-table columns without querying information_schema.

    Some hosted MySQL accounts can query ``users`` but cannot see its
    ``information_schema`` rows. A zero-row SELECT still exposes the table's
    column metadata through the connector and works for both old and current
    schemas.
    """
    cursor.execute("SELECT * FROM users LIMIT 0")
    cursor.fetchall()
    available = {}
    for metadata in cursor.description or ():
        value = metadata[0] if metadata else None
        if value:
            available[str(value).lower()] = str(value)

    result = {}
    for logical_name, aliases in _USER_COLUMN_ALIASES.items():
        for alias in aliases:
            actual = available.get(alias.lower())
            if actual:
                result[logical_name] = actual
                break
    return result


def _load_user_columns() -> dict[str, str]:
    """Return column names from the live users table."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        return _find_user_columns(cursor)
    finally:
        cursor.close()
        conn.close()


def _user_display_expression(columns: dict[str, str], qualifier: str = "u") -> str:
    display = columns.get("display")
    if display:
        return f"{qualifier}.{_quote_identifier(display)} AS name"
    first = columns.get("first_name")
    last = columns.get("last_name")
    if first and last:
        return (
            f"CONCAT({qualifier}.{_quote_identifier(first)}, ' ', "
            f"{qualifier}.{_quote_identifier(last)}) AS name"
        )
    if first or last:
        return f"{qualifier}.{_quote_identifier(first or last)} AS name"
    return "NULL AS name"


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


def _dataset_rows_frame(user_id: int, dataset_id: int | None = None) -> pd.DataFrame:
    """Restore the cleaned row JSON for one user and optional upload."""
    rows = run_query(
        """SELECT row_data FROM dataset_rows
           WHERE user_id=%s AND (%s IS NULL OR uploaded_file_id=%s)
           ORDER BY uploaded_file_id, `row_number`""",
        (user_id, dataset_id, dataset_id), fetchall=True,
    )["rows"] or []
    records = []
    for row in rows:
        try:
            record = json.loads(row.get("row_data") or "")
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return pd.DataFrame(records)


def _load_active_cleaned_dataset(user_id: int, dataset_id: int) -> pd.DataFrame:
    """Rebuild the active upload's cleaned frame and its models from owned rows."""
    try:
        restored = _dataset_rows_frame(user_id, dataset_id)
    except Exception as exc:
        print(f"[finsight] Could not restore dataset rows: {exc}")
        restored = pd.DataFrame()
    if not restored.empty:
        _run_universal_analysis(user_id, restored)
        active = restored.copy()
        has_date = "tx_date" in active.columns
        if has_date:
            active["tx_date"] = pd.to_datetime(active["tx_date"], errors="coerce")
            dated = active.dropna(subset=["tx_date"]).copy()
        else:
            dated = active.copy()
        if has_date and not dated.empty:
            dated["Date_Number"] = (dated["tx_date"] - dated["tx_date"].min()).dt.days
            dated["Month"] = dated["tx_date"].dt.month
            dated["Day_of_Week"] = dated["tx_date"].dt.dayofweek
        session_data[user_id] = {
            "df": dated if has_date and not dated.empty else active,
            "dataset_id": dataset_id,
            "models": {"amount": None, "expenses": None, "revenue": None, "profit": None, "risk": None},
        }
        if has_date and not dated.empty:
            _train_models_for(user_id, dated)
        return dated if has_date and not dated.empty else active

    rows = run_query(
        """SELECT tx_date, transaction_id, description, amount, revenue, expenses, profit,
                  customers, marketing_spend, tx_type, category, payment_method, department, city, status
           FROM financial_data
           WHERE user_id=%s AND uploaded_file_id=%s
           ORDER BY tx_date, id""",
        (user_id, dataset_id), fetchall=True,
    )["rows"] or []
    df = pd.DataFrame(rows)
    if df.empty:
        # Keep the older sidecar as a backward-compatible fallback for uploads
        # created before dataset_rows was introduced.
        try:
            path = _analysis_store_path(user_id)
            if path is not None and path.exists():
                side = pd.read_csv(path)
                _run_universal_analysis(user_id, side)
                df = side
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
# Groq chat assistant
# =====================================================
def _chat_number(value: object) -> str:
    try:
        number = float(value)
        if not np.isfinite(number):
            return "n/a"
        return f"{number:,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _chat_data_context(user_id: int) -> str:
    """Build a compact, user-scoped summary for the assistant.

    Only aggregate values, a small category ranking, and recent model output
    are sent to Groq. Raw uploads and credentials never leave this server.
    """
    try:
        dataset_id, frame = _active_dataset_and_frame(user_id)
    except Exception as exc:
        app.logger.warning("Chat data context could not load the active dataset: %s", exc)
        return "No uploaded dataset is currently available."
    if dataset_id is None or frame is None or frame.empty:
        return "No uploaded dataset is currently available."

    working = frame.copy()
    lines = [f"Active dataset: {len(working):,} cleaned rows."]
    if "tx_date" in working.columns:
        dates = pd.to_datetime(working["tx_date"], errors="coerce").dropna()
        if not dates.empty:
            lines.append(f"Date range: {dates.min().date()} to {dates.max().date()}.")
    metrics = ("revenue", "expenses", "profit", "amount", "customers", "marketing_spend")
    for metric in metrics:
        if metric not in working.columns:
            continue
        values = pd.to_numeric(working[metric], errors="coerce").dropna()
        if not values.empty:
            lines.append(
                f"{metric.replace('_', ' ').title()}: total {_chat_number(values.sum())}; "
                f"average {_chat_number(values.mean())}."
            )

    value_column = next((column for column in ("revenue", "expenses", "profit", "amount")
                         if column in working.columns), None)
    category_column = next((column for column in ("category", "department", "city", "status")
                            if column in working.columns), None)
    if value_column and category_column:
        grouped = working[[category_column, value_column]].copy()
        grouped[value_column] = pd.to_numeric(grouped[value_column], errors="coerce")
        grouped[category_column] = grouped[category_column].fillna("Unspecified").astype(str).str.strip()
        grouped = grouped.dropna(subset=[value_column]).groupby(category_column)[value_column].sum()
        if not grouped.empty:
            top = grouped.sort_values(ascending=False).head(8)
            ranking = ", ".join(f"{name}: {_chat_number(value)}" for name, value in top.items())
            lines.append(f"Top {category_column.replace('_', ' ')} by {value_column}: {ranking}.")

    analysis = _analysis_context(user_id) or {}
    trends = analysis.get("trends") or []
    if trends:
        trend_text = "; ".join(
            f"{item.get('metric', 'metric')} {item.get('direction', 'stable')}"
            for item in trends[:6] if isinstance(item, dict)
        )
        if trend_text:
            lines.append(f"Detected trends: {trend_text}.")

    try:
        predictions = run_query(
            """SELECT prediction_type, prediction_date, predicted_value, model_name
               FROM predictions WHERE user_id=%s AND uploaded_file_id=%s
               ORDER BY prediction_id DESC LIMIT 8""",
            (user_id, dataset_id), fetchall=True,
        )["rows"] or []
        if predictions:
            forecast_text = "; ".join(
                f"{row.get('prediction_type')} on {row.get('prediction_date')}: "
                f"{_chat_number(row.get('predicted_value'))}"
                for row in predictions
            )
            lines.append(f"Recent forecast results: {forecast_text}.")
    except Exception as exc:
        app.logger.warning("Chat forecast context unavailable: %s", exc)

    try:
        risk = run_query(
            """SELECT risk_level, classification_date, explanation
               FROM risk_classifications WHERE user_id=%s AND uploaded_file_id=%s
               ORDER BY risk_id DESC LIMIT 1""",
            (user_id, dataset_id), fetchone=True,
        )["row"]
        if risk:
            explanation = str(risk.get("explanation") or "")[:300]
            lines.append(
                f"Latest risk classification: {risk.get('risk_level')} on "
                f"{risk.get('classification_date')}; {explanation}"
            )
    except Exception as exc:
        app.logger.warning("Chat risk context unavailable: %s", exc)

    return "\n".join(lines)[:10000]


def _groq_answer(messages: list[dict[str, str]]) -> str:
    """Call Groq's OpenAI-compatible endpoint without exposing the API key."""
    if not GROQ_API_KEY:
        raise RuntimeError("The AI assistant is not configured on this deployment.")
    payload = json.dumps({
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.25,
        "max_tokens": 700,
    }).encode("utf-8")
    request_obj = Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request_obj, timeout=GROQ_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        app.logger.warning("Groq request returned HTTP %s", exc.code)
        if exc.code == 429:
            raise RuntimeError("The AI assistant is temporarily busy. Please try again in a moment.") from exc
        raise RuntimeError("The AI assistant could not complete that request.") from exc
    except (URLError, TimeoutError) as exc:
        app.logger.warning("Groq request failed: %s", type(exc).__name__)
        raise RuntimeError("The AI assistant is temporarily unavailable. Please try again.") from exc
    except (ValueError, OSError) as exc:
        app.logger.warning("Groq response could not be read: %s", type(exc).__name__)
        raise RuntimeError("The AI assistant returned an invalid response.") from exc

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        app.logger.warning("Groq response did not contain assistant content")
        raise RuntimeError("The AI assistant returned an empty response.") from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("The AI assistant returned an empty response.")
    return content.strip()[:12000]


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


@app.post("/api/chat")
@login_required
def chat():
    """Answer a natural-language question with the current user's context."""
    if not request.is_json:
        return jsonify({"error": "JSON body required."}), 400
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Enter a question for the AI assistant."}), 400
    if len(message) > 2000:
        return jsonify({"error": "Please keep your question under 2,000 characters."}), 400

    user_id = _session_user_id()
    locale = i18n.get_locale() if i18n is not None else "en"
    locale_names = getattr(i18n, "LOCALE_NAMES", {}) if i18n is not None else {}
    language = locale_names.get(locale, locale)
    system = (
        "You are FinSight AI, a concise and practical financial data-analysis assistant. "
        f"Answer in {language} ({locale}) because that is the user's selected language. "
        "Use the supplied dataset summary when present. Explain calculations plainly, "
        "state when information is unavailable, and do not invent figures, forecasts, "
        "or business facts. This is analytical guidance, not regulated financial advice. "
        "Keep answers helpful and readable with short paragraphs or bullets.\n\n"
        "DATA CONTEXT:\n" + _chat_data_context(user_id)
    )
    state = session_data.setdefault(user_id, {})
    history = state.setdefault("chat_messages", [])
    messages = [{"role": "system", "content": system}, *history[-10:],
                {"role": "user", "content": message}]
    try:
        answer = _groq_answer(messages)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    history.extend([{"role": "user", "content": message},
                    {"role": "assistant", "content": answer}])
    state["chat_messages"] = history[-12:]
    return jsonify({"success": True, "message": answer, "locale": locale})


@app.post("/api/chat/reset")
@login_required
def reset_chat():
    """Forget only the current user's in-memory assistant conversation."""
    session_data.setdefault(_session_user_id(), {}).pop("chat_messages", None)
    return jsonify({"success": True})


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


def _password_matches(stored_hash: object, password: str) -> bool:
    """Verify a stored Werkzeug hash without allowing malformed data to raise."""
    if not isinstance(stored_hash, str) or not stored_hash or not password:
        return False
    try:
        return bool(check_password_hash(stored_hash, password))
    except (AttributeError, TypeError, ValueError):
        # A missing, truncated, or legacy non-Werkzeug value is not a valid
        # credential. Never turn it into a successful login or a server error.
        return False


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
    if request.is_json or request.path in {"/upload", "/upload/preview", "/risk-classify", "/powerbi/generate"}:
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
    """Create or upgrade the complete schema without modifying existing data."""
    try:
        create_database_tables()
        print("[finsight] Database schema ready.")
    except mysql.connector.Error as err:
        app.logger.error(
            "Database schema initialization failed: %s: %s",
            type(err).__name__,
            err,
            exc_info=(type(err), err, err.__traceback__),
        )
    except (RuntimeError, ValueError) as err:
        app.logger.error("Database schema configuration failed: %s", err)


# =====================================================
# Column normalisation for uploaded files
# =====================================================
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Backward-compatible wrapper around the conservative business mapper."""
    return apply_mapping(df, detect_columns(df).get("mapping", {}))


def _excel_first_row_is_data(frame: pd.DataFrame) -> bool:
    """Detect an Excel sheet whose first row is data rather than a header."""
    if frame is None or frame.empty:
        return False

    aliases = {
        alias.casefold()
        for spec in FIELD_SPECS.values()
        for alias in spec.get("aliases", [])
    }
    header_names = {
        re.sub(r"[^a-z0-9]+", "_", str(column).strip().casefold()).strip("_")
        for column in frame.columns
    }
    if header_names & aliases:
        return False
    header_values = pd.Series(list(frame.columns), dtype="string").str.strip()
    header_numeric = pd.to_numeric(header_values, errors="coerce").notna()
    header_blank = header_values.eq("") | header_values.str.match(r"^Unnamed", case=False, na=False)
    if not bool((header_numeric | header_blank).any()):
        return False

    first_row = frame.iloc[0]
    non_empty = first_row.dropna()
    if non_empty.empty:
        return True
    numeric_count = int(pd.to_numeric(non_empty, errors="coerce").notna().sum())
    # A normal header row is textual. Multiple numeric values in the first row
    # are therefore a strong signal that pandas consumed a real data row as the
    # header (the common shape of an uncleaned Excel export).
    return numeric_count >= 2


def _read_excel_upload(source: str | Path | io.BytesIO) -> pd.DataFrame:
    """Read normal Excel headers while preserving headerless data exports."""
    frame = pd.read_excel(source)
    if not _excel_first_row_is_data(frame):
        return frame
    if hasattr(source, "seek"):
        source.seek(0)
    frame = pd.read_excel(source, header=None)
    frame.columns = [f"column_{index + 1}" for index in range(len(frame.columns))]
    return frame


def _read_upload_dataframe(source: str | Path | io.BytesIO, filename: str) -> pd.DataFrame:
    """Read one supported upload format into a bounded dataframe."""
    extension = Path(filename).suffix.lower()
    if extension == ".csv":
        try:
            frame = pd.read_csv(source)
            if len(frame.columns) == 1:
                # A semicolon-delimited export is common in European tools.
                if hasattr(source, "seek"):
                    source.seek(0)
                frame = pd.read_csv(source, sep=None, engine="python")
        except UnicodeDecodeError:
            if hasattr(source, "seek"):
                source.seek(0)
            frame = pd.read_csv(source, encoding="latin-1", sep=None, engine="python")
    elif extension in {".xlsx", ".xls"}:
        frame = _read_excel_upload(source)
    elif extension == ".json":
        if hasattr(source, "seek"):
            source.seek(0)
        if isinstance(source, (str, Path)):
            with open(source, "r", encoding="utf-8") as json_file:
                payload = json.load(json_file)
        else:
            payload = json.load(source)
        frame = profile_json_payload(payload)
    else:
        raise ValueError("File must be CSV, XLSX, XLS, or JSON.")

    if frame.empty or len(frame.columns) == 0:
        raise ValueError("File is empty or has no tabular data.")
    if len(frame.columns) > MAX_DATA_COLUMNS:
        raise ValueError(f"The file contains too many columns. Maximum allowed is {MAX_DATA_COLUMNS}.")
    if len(frame) > 1_000_000:
        raise ValueError("The file contains too many rows. Maximum allowed is 1,000,000.")
    return frame


def _mapping_from_request() -> dict[str, str]:
    raw = request.form.get("mapping")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("The column mapping is invalid.") from exc
    if not isinstance(payload, dict):
        raise ValueError("The column mapping is invalid.")
    return {str(key): str(value).strip() for key, value in payload.items() if value}


def _add_generic_prediction_mapping(raw_df: pd.DataFrame, detection: dict,
                                    mapping: dict[str, str]) -> dict[str, str]:
    """Give non-standard uploads a usable date and numeric forecast target."""
    selected = dict(mapping)
    used_sources = set(selected.values())
    candidates = detection.get("columns", []) if isinstance(detection, dict) else []

    if not selected.get("tx_date"):
        for candidate in candidates:
            source = str(candidate.get("source") or "").strip()
            if candidate.get("type") != "date" or not source or source in used_sources:
                continue
            selected["tx_date"] = source
            used_sources.add(source)
            break

    if not any(selected.get(field) for field in ("amount", "revenue", "expenses", "profit")):
        source_columns = {str(column).strip(): column for column in raw_df.columns}
        for candidate in candidates:
            source = str(candidate.get("source") or "").strip()
            normalized = str(candidate.get("normalized") or "").lower()
            if (candidate.get("type") != "numeric" or not source or source in used_sources
                    or normalized == "id" or normalized.endswith("_id")):
                continue
            try:
                source_values = raw_df[source_columns.get(source, source)]
                values = pd.to_numeric(source_values, errors="coerce")
                if values.notna().sum() < 3:
                    text_values = source_values.astype("string").str.strip()
                    negative = text_values.str.startswith("(") & text_values.str.endswith(")")
                    text_values = text_values.str.replace(r"[^0-9+\-.()]", "", regex=True)
                    text_values = text_values.str.replace("(", "", regex=False).str.replace(")", "", regex=False)
                    values = pd.to_numeric(text_values, errors="coerce")
                    values = values.where(~negative, -values.abs())
            except (KeyError, TypeError, ValueError):
                continue
            if values.notna().sum() < 3 or values.nunique(dropna=True) < 2:
                continue
            selected["amount"] = source
            break
    return selected


BUSINESS_TEMPLATE_ROWS = {
    "general": [
        {"Date": "2026-01-05", "Transaction_ID": "GEN-1001", "Description": "Product sale", "Revenue": 12500, "Amount": 12500, "Expenses": 7300, "Profit": 5200, "Customers": 84, "Category": "Sales", "Department": "Commercial", "Payment_Method": "Card", "City": "Berlin", "Status": "Completed"},
        {"Date": "2026-02-05", "Transaction_ID": "GEN-1002", "Description": "Subscription renewal", "Revenue": 14800, "Amount": 14800, "Expenses": 8100, "Profit": 6700, "Customers": 96, "Category": "Subscriptions", "Department": "Commercial", "Payment_Method": "Transfer", "City": "Berlin", "Status": "Completed"},
        {"Date": "2026-03-05", "Transaction_ID": "GEN-1003", "Description": "Product sale", "Revenue": 16100, "Amount": 16100, "Expenses": 8950, "Profit": 7150, "Customers": 103, "Category": "Sales", "Department": "Commercial", "Payment_Method": "Card", "City": "Munich", "Status": "Completed"},
    ],
    "retail": [
        {"Date": "2026-01-10", "Transaction_ID": "RTL-2001", "Description": "Store order", "Revenue": 4200, "Amount": 4200, "Expenses": 2550, "Profit": 1650, "Customers": 32, "Category": "Electronics", "Department": "Store", "Payment_Method": "Card", "City": "Berlin", "Status": "Completed"},
        {"Date": "2026-02-10", "Transaction_ID": "RTL-2002", "Description": "Online order", "Revenue": 5100, "Amount": 5100, "Expenses": 2990, "Profit": 2110, "Customers": 39, "Category": "Home", "Department": "E-commerce", "Payment_Method": "PayPal", "City": "Hamburg", "Status": "Completed"},
        {"Date": "2026-03-10", "Transaction_ID": "RTL-2003", "Description": "Store order", "Revenue": 5750, "Amount": 5750, "Expenses": 3380, "Profit": 2370, "Customers": 44, "Category": "Electronics", "Department": "Store", "Payment_Method": "Card", "City": "Berlin", "Status": "Completed"},
    ],
    "service": [
        {"Date": "2026-01-15", "Transaction_ID": "SRV-3001", "Description": "Consulting engagement", "Revenue": 8600, "Amount": 8600, "Expenses": 4100, "Profit": 4500, "Customers": 7, "Category": "Consulting", "Department": "Delivery", "Payment_Method": "Transfer", "City": "Frankfurt", "Status": "Completed"},
        {"Date": "2026-02-15", "Transaction_ID": "SRV-3002", "Description": "Support retainer", "Revenue": 9200, "Amount": 9200, "Expenses": 4350, "Profit": 4850, "Customers": 9, "Category": "Support", "Department": "Customer Success", "Payment_Method": "Transfer", "City": "Frankfurt", "Status": "Completed"},
        {"Date": "2026-03-15", "Transaction_ID": "SRV-3003", "Description": "Implementation project", "Revenue": 11200, "Amount": 11200, "Expenses": 5660, "Profit": 5540, "Customers": 6, "Category": "Implementation", "Department": "Delivery", "Payment_Method": "Card", "City": "Cologne", "Status": "Completed"},
    ],
}


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


@app.get("/healthz")
def healthz():
    """Lightweight deployment health check that does not require a DB round trip."""
    return jsonify({"status": "ok", "service": "finsight-ai"})


@app.get("/templates/<template_name>/<file_format>")
@login_required
def download_business_template(template_name: str, file_format: str):
    """Download a small, valid business template accepted by the upload flow."""
    if template_name not in BUSINESS_TEMPLATE_ROWS or file_format not in {"csv", "xlsx", "json"}:
        return jsonify({"error": "Template not found."}), 404
    frame = pd.DataFrame(BUSINESS_TEMPLATE_ROWS[template_name])
    safe_name = f"finsight_{template_name}_template.{file_format}"
    if file_format == "csv":
        output = io.BytesIO(frame.to_csv(index=False).encode("utf-8"))
        mimetype = "text/csv"
    elif file_format == "json":
        output = io.BytesIO(frame.to_json(orient="records", date_format="iso").encode("utf-8"))
        mimetype = "application/json"
    else:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name="Business_Data")
        output.seek(0)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return send_file(output, as_attachment=True, download_name=safe_name, mimetype=mimetype)


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

        try:
            user_columns = _load_user_columns()
            email_column = user_columns.get("email")
            password_column = user_columns.get("password")
        except (mysql.connector.Error, RuntimeError) as exc:
            app.logger.error("Signup schema lookup failed: %s", exc, exc_info=True)
            flash("Database connection failed. Please try again later.", "danger")
            return render_template("signup.html"), 503
        if not email_column or not password_column:
            flash("The users table is missing its email or password field.", "danger")
            return render_template("signup.html"), 503

        # Check whether the email is already used.
        existing = run_query(
            f"SELECT id FROM users WHERE {_quote_identifier(email_column)} = %s",
            (email,), fetchone=True,
        )
        if existing and existing["row"]:
            flash("This email is already registered. Please log in.", "warning")
            return redirect(url_for("login"))

        conn = None
        cur = None
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

            name_parts = name.split(maxsplit=1)
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else ""
            insert_values = []
            insert_columns = []
            display_column = user_columns.get("display")
            if display_column:
                insert_columns.append(display_column)
                insert_values.append(name)
            else:
                if user_columns.get("first_name"):
                    insert_columns.append(user_columns["first_name"])
                    insert_values.append(first_name)
                if user_columns.get("last_name"):
                    insert_columns.append(user_columns["last_name"])
                    insert_values.append(last_name)
            insert_columns.extend([email_column, password_column])
            insert_values.extend([email, generate_password_hash(password)])
            if user_columns.get("company_id"):
                insert_columns.append(user_columns["company_id"])
                insert_values.append(company_id)
            if user_columns.get("role"):
                insert_columns.append(user_columns["role"])
                insert_values.append("user")
            if not display_column and not user_columns.get("first_name") and not user_columns.get("last_name"):
                raise RuntimeError("The users table has no supported name field.")
            quoted_columns = ", ".join(_quote_identifier(column) for column in insert_columns)
            placeholders = ", ".join(["%s"] * len(insert_values))
            cur.execute(
                f"INSERT INTO users ({quoted_columns}) VALUES ({placeholders})",
                tuple(insert_values),
            )
            user_id = cur.lastrowid
            conn.commit()

            _powerbi_resource(user_id)

            add_history(user_id, company_id, "signup", f"New account created for {company_name}")

            session["user_id"] = user_id
            session["user_name"] = name
            session["company_id"] = company_id
            session["company_name"] = company_name
            flash(f"Welcome to FinSight AI, {name}!", "success")
            return redirect(url_for("dashboard"))
        except (mysql.connector.Error, RuntimeError) as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except mysql.connector.Error:
                    pass
            app.logger.error("Signup failed: %s", exc, exc_info=True)
            if getattr(exc, "errno", None) == errorcode.ER_ACCESS_DENIED_ERROR:
                flash("Cannot connect to MySQL: access denied. "
                      "Check DB_USER and DB_PASSWORD in your environment configuration.", "danger")
            else:
                flash("We could not create your account right now. Please try again.", "danger")
            return render_template("signup.html")
        finally:
            if cur is not None:
                cur.close()
            if conn is not None:
                conn.close()

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

        try:
            user_columns = _load_user_columns()
            email_column = user_columns.get("email")
            password_column = user_columns.get("password")
            if not email_column or not password_column:
                flash("The users table is missing its email or password field.", "danger")
                return render_template("login.html")

            company_column = user_columns.get("company_id")
            if company_column:
                company_sql = f"u.{_quote_identifier(company_column)} AS company_id, c.company_name"
                # A user row must not disappear merely because its company
                # lookup is unavailable; the checked-in schema enforces the
                # relationship, while LEFT JOIN keeps auth failure explicit
                # for any pre-existing data that does not.
                company_join = "LEFT JOIN companies c ON c.id = u." + _quote_identifier(company_column)
            else:
                company_sql = "NULL AS company_id, '' AS company_name"
                company_join = ""
            query_result = run_query(
                f"""
                SELECT u.id, {_user_display_expression(user_columns)},
                       u.{_quote_identifier(password_column)} AS password_hash,
                       {company_sql}
                FROM users u
                {company_join}
                WHERE u.{_quote_identifier(email_column)} = %s
                """,
                (email,),
                fetchone=True,
            )
            row = query_result.get("row") if isinstance(query_result, dict) else None
        except mysql.connector.Error as exc:
            app.logger.error(
                "Login database lookup failed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            flash("Database connection failed. Please try again later.", "danger")
            return render_template("login.html"), 503
        except RuntimeError as exc:
            app.logger.error("Login database configuration failed: %s", exc)
            flash("Database connection failed. Please try again later.", "danger")
            return render_template("login.html"), 503

        if not isinstance(row, dict) or not row.get("id") or not _password_matches(row.get("password_hash"), password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        display_name = row.get("name") or email
        session.clear()
        session["user_id"] = row["id"]
        session["user_name"] = display_name
        session["company_id"] = row.get("company_id")
        session["company_name"] = row.get("company_name") or ""
        flash(f"Welcome back, {display_name}!", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.post("/logout")
def logout():
    user_id = _session_user_id()
    if user_id is not None:
        session_data.pop(user_id, None)
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
    active_dataset_id, df = _active_dataset_and_frame(user_id)
    risk_status = None
    if active_dataset_id is not None:
        risk_status = run_query(
            """SELECT risk_level, classification_date, explanation FROM risk_classifications
               WHERE user_id=%s AND uploaded_file_id=%s ORDER BY risk_id DESC LIMIT 1""",
            (user_id, active_dataset_id), fetchone=True,
        )["row"]

    # Aggregates for KPI cards (use live data if loaded, otherwise fall
    # back to whatever is stored in financial_data)
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

    analysis_context = _analysis_context(user_id) or {}
    return render_template(
        "dashboard.html",
        company_name=session["company_name"],
        user_name=session["user_name"],
        uploads=uploads,
        predictions=predictions,
        stats=stats,
        risk_status=risk_status,
        analysis=analysis_context,
        insight=analysis_context.get("insight", ""),
        analysis_trends=analysis_context.get("trends", []),
    )


@app.route("/analytics")
@app.route("/visualizations")
@login_required
def analytics():
    """Render only charts supported by the active dataset."""
    user_id = _session_user_id()
    _, df = _active_dataset_and_frame(user_id)
    empty_context = {
        "company_name": session["company_name"], "has_data": False, "monthly": [],
        "categories": [], "cities": [], "totals": {}, "trend_keys": [],
        "trend_labels": [], "category_label": "Category", "has_trend": False,
        "has_categories": False, "has_totals": False, "has_cities": False,
    }
    if df is None or df.empty:
        return render_template("analytics.html", **empty_context)

    working = df.copy()
    analysis = _analysis_context(user_id) or {}
    types = analysis.get("types") or {}
    canonical_order = ["revenue", "expenses", "profit", "amount", "customers", "marketing_spend"]
    typed_numeric = [name for name, kind in types.items() if kind == "numeric"]
    typed_dimensions = [name for name, kind in types.items()
                        if kind in {"categorical", "text"}]
    numeric_columns = []
    for column in canonical_order + typed_numeric:
        if column in working.columns and column not in numeric_columns:
            values = pd.to_numeric(working[column], errors="coerce")
            if values.notna().any() and column not in {"Date_Number", "Month", "Day_of_Week"}:
                working[column] = values
                numeric_columns.append(column)

    date_col = None
    for candidate in ["tx_date", *[name for name, kind in types.items() if kind == "date"]]:
        if candidate in working.columns and pd.to_datetime(working[candidate], errors="coerce").notna().any():
            date_col = candidate
            break

    trend_keys = numeric_columns[:4]
    monthly: list[dict] = []
    if date_col and trend_keys:
        dated = working.copy()
        dated["_period"] = pd.to_datetime(dated[date_col], errors="coerce").dt.to_period("M").astype("string")
        dated = dated.dropna(subset=["_period"])
        if not dated.empty:
            monthly_frame = dated.groupby("_period", as_index=False)[trend_keys].sum(min_count=1)
            monthly = monthly_frame.rename(columns={"_period": "period"}).to_dict("records")

    category_col = next((name for name in ("category", "department", "city", "payment_method", "status", *typed_dimensions)
                         if name in working.columns and types.get(name, "categorical") in {"categorical", "text"}
                         and working[name].notna().any()), None)
    value_col = next((name for name in ["revenue", "expenses", "profit", "amount", *numeric_columns]
                      if name in working.columns and name in numeric_columns), None)
    categories: list[dict] = []
    if category_col and value_col:
        grouped = working[[category_col, value_col]].copy()
        grouped[category_col] = grouped[category_col].fillna("Unspecified").astype(str)
        grouped = grouped.groupby(category_col, as_index=False)[value_col].sum(min_count=1)
        categories = grouped.rename(columns={category_col: "category", value_col: "amount"}) \
            .sort_values("amount", ascending=False).head(12).to_dict("records")

    cities: list[dict] = []
    if "city" in working.columns and value_col and working["city"].notna().any():
        city_frame = working[["city", value_col]].copy()
        city_frame["city"] = city_frame["city"].astype(str).str.strip()
        city_frame = city_frame[city_frame["city"] != ""]
        cities = city_frame.groupby("city", as_index=False)[value_col].sum(min_count=1) \
            .rename(columns={value_col: "amount"}).sort_values("amount", ascending=False).head(20).to_dict("records")

    totals = {column: float(pd.to_numeric(working[column], errors="coerce").sum())
              for column in numeric_columns}
    generic_analytics = not any(column in numeric_columns for column in ("revenue", "expenses", "profit", "amount"))
    return render_template(
        "analytics.html", company_name=session["company_name"], has_data=True,
        monthly=monthly, categories=categories, cities=cities, totals=totals,
        generic_analytics=generic_analytics, trend_keys=trend_keys,
        trend_labels=[str(column).replace("_", " ").title() for column in trend_keys],
        category_label=category_col.replace("_", " ").title() if category_col else "Category",
        has_trend=bool(monthly and trend_keys), has_categories=bool(categories),
        has_totals=bool(totals), has_cities=bool(cities),
    )


# =====================================================
# Routes - File upload
# =====================================================
@app.post("/upload/preview")
@login_required
def upload_preview():
    """Inspect an upload without persisting it or changing the active dataset."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    file = request.files["file"]
    filename = (file.filename or "").strip()
    if not filename:
        return jsonify({"error": "No file selected."}), 400
    if not allowed_file(filename):
        return jsonify({"error": "File must be CSV, XLSX, XLS, or JSON."}), 400
    contents = file.stream.read()
    if not contents:
        return jsonify({"error": "File is empty."}), 400
    if len(contents) > MAX_CONTENT_LENGTH:
        limit = round(MAX_CONTENT_LENGTH / (1024 * 1024), 2)
        return jsonify({"error": f"Uploaded file is too large. Maximum size is {limit:g} MB."}), 413
    try:
        frame = _read_upload_dataframe(io.BytesIO(contents), filename)
        detection = detect_columns(frame)
        preview_mapping = _add_generic_prediction_mapping(
            frame, detection, detection.get("mapping", {})
        )
        profile = profile_dataframe(frame, preview_mapping)
        # Mark inferred mappings explicitly so the review table does not show
        # a blank-looking status for a valid generic numeric/date fallback.
        for field, source in preview_mapping.items():
            detail = profile.get("detected", {}).get("fields", {}).get(field)
            if detail and not detail.get("source"):
                detail["source"] = source
                detail["confidence"] = "low"
                detail["candidates"] = detail.get("candidates") or [
                    {"source": source, "confidence": "low"}
                ]
        return jsonify({"success": True, "profile": profile})
    except (ValueError, pd.errors.ParserError, OSError) as exc:
        return jsonify({"error": str(exc) or "We could not read this file."}), 400
    except Exception:
        app.logger.exception("Upload preview failed")
        return jsonify({"error": "We could not read this file. Check its format and try again."}), 400


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload_file():
    """Accept CSV, XLSX, XLS, or JSON, map business fields, and persist the result."""
    user_id = _session_user_id()
    company_id = _session_company_id()

    if request.method == "GET":
        return render_template(
            "upload.html", company_name=session["company_name"], field_specs=FIELD_SPECS,
            max_upload_mb=round(MAX_CONTENT_LENGTH / (1024 * 1024), 2),
        )

    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    file = request.files["file"]
    original_name = (file.filename or "").strip()
    if not original_name:
        return jsonify({"error": "No file selected."}), 400
    if not allowed_file(original_name):
        return jsonify({"error": "File must be CSV, XLSX, XLS, or JSON."}), 400
    safe_name = secure_filename(original_name)
    if not safe_name:
        return jsonify({"error": "The file name is not valid."}), 400

    resource = _powerbi_resource(user_id)
    user_upload_dir = POWERBI_ROOT / resource["folder_token"] / "uploads"
    user_upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    disk_path = user_upload_dir / stored_name
    try:
        file.save(disk_path)
    except Exception:
        app.logger.exception("Could not save uploaded file")
        return jsonify({"error": "Could not save the file. Please try again."}), 500

    file_id: int | None = None
    upload_succeeded = False
    try:
        try:
            raw_df = _read_upload_dataframe(disk_path, safe_name)
        except Exception as exc:
            add_history(user_id, company_id, "upload", safe_name, status="failed",
                        details=f"read error: {exc}")
            return jsonify({"error": str(exc) or "We could not read this file. Check its format and try again."}), 400

        mapping = _mapping_from_request()
        detection = detect_columns(raw_df)
        if not mapping:
            mapping = detection.get("mapping", {})
        mapping = _add_generic_prediction_mapping(raw_df, detection, mapping)
        cleaned_df, cleaning = clean_dataframe(raw_df, mapping)
        mapped_labels = {field: mapping.get(field) for field in FIELD_SPECS if mapping.get(field)}
        missing_required = [
            FIELD_SPECS[field]["label"] for field in ("tx_date", "revenue")
            if field not in mapping
        ]
        warnings = list(detection.get("warnings", []))
        if missing_required:
            warnings.append("Missing important fields: " + ", ".join(missing_required) + ".")

        file_id = run_query(
            """INSERT INTO uploaded_files
               (user_id, company_id, version, original_name, stored_name, file_size,
                rows_imported, status, source_format, source_columns, column_mapping,
                cleaning_summary, upload_warnings)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'processing', %s, %s, %s, %s, %s)""",
            (user_id, company_id, _next_dataset_version(user_id), safe_name, stored_name,
             disk_path.stat().st_size, int(len(raw_df)), Path(safe_name).suffix.lower().lstrip("."),
             json.dumps([str(column) for column in raw_df.columns], ensure_ascii=False),
             json.dumps(mapped_labels, ensure_ascii=False), json.dumps(cleaning),
             json.dumps(warnings, ensure_ascii=False)),
            commit=True,
        )["last_id"]

        for column in CANONICAL_NUMERIC_FIELDS:
            if column not in cleaned_df.columns:
                cleaned_df[column] = np.nan
        for column in CANONICAL_TEXT_FIELDS:
            if column not in cleaned_df.columns:
                cleaned_df[column] = None
            else:
                cleaned_df[column] = cleaned_df[column].astype(object).where(cleaned_df[column].notna(), None)
        if "tx_date" not in cleaned_df.columns:
            cleaned_df["tx_date"] = pd.NaT

        financial_sql = """INSERT INTO financial_data
            (user_id, company_id, uploaded_file_id, tx_date, transaction_id, description,
             amount, revenue, expenses, profit, customers, marketing_spend,
             tx_type, category, payment_method, department, city, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        financial_rows = []
        dataset_rows = []
        for row_number, (_, row) in enumerate(cleaned_df.iterrows(), start=1):
            tx_value = row.get("tx_date")
            tx_value = pd.to_datetime(tx_value, errors="coerce") if tx_value is not None else pd.NaT
            financial_rows.append((
                user_id, company_id, file_id,
                None if pd.isna(tx_value) else tx_value.date(),
                row.get("transaction_id"), row.get("description"),
                *[None if pd.isna(row.get(column)) else float(row.get(column))
                  for column in ("amount", "revenue", "expenses", "profit", "customers", "marketing_spend")],
                row.get("tx_type"), row.get("category"), row.get("payment_method"),
                row.get("department"), row.get("city"), row.get("status"),
            ))
            dataset_rows.append((
                user_id, company_id, file_id, row_number,
                json.dumps(json_safe_record(row.to_dict()), ensure_ascii=False, allow_nan=False),
            ))

        conn = get_db()
        try:
            cur = conn.cursor()
            for start in range(0, len(financial_rows), 500):
                cur.executemany(financial_sql, financial_rows[start:start + 500])
                cur.executemany(
                    """INSERT INTO dataset_rows
                       (user_id, company_id, uploaded_file_id, `row_number`, row_data)
                       VALUES (%s, %s, %s, %s, %s)""",
                    dataset_rows[start:start + 500],
                )
            conn.commit()
            cur.close()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        run_query(
            """UPDATE uploaded_files SET rows_imported=%s, status='processed',
               source_columns=%s, column_mapping=%s, cleaning_summary=%s, upload_warnings=%s
               WHERE id=%s AND user_id=%s""",
            (int(len(cleaned_df)), json.dumps([str(column) for column in raw_df.columns], ensure_ascii=False),
             json.dumps(mapped_labels, ensure_ascii=False), json.dumps(cleaning),
             json.dumps(warnings, ensure_ascii=False), file_id, user_id), commit=True,
        )

        train_df = cleaned_df.copy()
        train_df["tx_date"] = pd.to_datetime(train_df["tx_date"], errors="coerce")
        train_df = train_df.dropna(subset=["tx_date"])
        active_frame = train_df if not train_df.empty else cleaned_df.copy()
        session_data[user_id] = {
            "df": active_frame,
            "dataset_id": file_id,
            "models": {"amount": None, "expenses": None, "revenue": None, "profit": None, "risk": None},
        }
        if not train_df.empty:
            train_df["Date_Number"] = (train_df["tx_date"] - train_df["tx_date"].min()).dt.days
            train_df["Month"] = train_df["tx_date"].dt.month
            train_df["Day_of_Week"] = train_df["tx_date"].dt.dayofweek
            session_data[user_id]["df"] = train_df
            try:
                _train_models_for(user_id, train_df)
            except Exception as exc:
                app.logger.warning("Model training skipped: %s", exc)

        _run_universal_analysis(user_id, cleaned_df)
        add_history(user_id, company_id, "upload", safe_name, file_id=file_id,
                    status="processed", details=f"{len(cleaned_df)} rows imported")
        try:
            _generate_powerbi_resources(user_id, company_id, session["company_name"], file_id)
        except Exception as exc:
            # Power BI artefacts are optional and must not invalidate a valid upload.
            app.logger.warning("Power BI resource generation skipped: %s", exc)

        stats = {
            "rows": int(len(cleaned_df)),
            "rows_detected": int(len(raw_df)),
            "columns": int(len(raw_df.columns)),
            "duplicates_removed": cleaning["duplicates_removed"],
            "blank_rows_removed": cleaning["blank_rows_removed"],
            "missing_values_remaining": cleaning["missing_values_remaining"],
            "invalid_dates": cleaning["invalid_dates"],
            "date_column": mapping.get("tx_date"),
            "mapping": mapped_labels,
            "warnings": warnings,
            "missing_required": missing_required,
            "preview": [json_safe_record(record) for record in cleaned_df.head(8).to_dict(orient="records")],
        }
        for column in ("revenue", "expenses", "profit", "amount", "customers", "marketing_spend"):
            if column in cleaned_df.columns and cleaned_df[column].notna().any():
                stats[f"{column}_total"] = float(pd.to_numeric(cleaned_df[column], errors="coerce").sum())
        valid_dates = pd.to_datetime(cleaned_df["tx_date"], errors="coerce").dropna()
        stats["date_range"] = (f"{valid_dates.min().date()} to {valid_dates.max().date()}"
                                if not valid_dates.empty else "")
        stats["numeric_columns"] = [column["source"] for column in detection["columns"] if column["type"] == "numeric"]
        stats["categorical_columns"] = [column["source"] for column in detection["columns"] if column["type"] == "categorical"]
        stats["detected_fields"] = detection["fields"]

        upload_succeeded = True
        return jsonify({"success": True, "stats": stats, "file_id": file_id})
    except ValueError as exc:
        app.logger.info("Invalid upload from user %s: %s", user_id, exc)
        if file_id is not None:
            run_query("UPDATE uploaded_files SET status='failed' WHERE id=%s AND user_id=%s",
                      (file_id, user_id), commit=True)
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Upload processing failed for user %s", user_id)
        if file_id is not None:
            run_query("UPDATE uploaded_files SET status='failed' WHERE id=%s AND user_id=%s",
                      (file_id, user_id), commit=True)
        try:
            add_history(user_id, company_id, "upload", safe_name, file_id=file_id,
                        status="failed", details=str(exc))
        except Exception:
            app.logger.exception("Could not record failed upload")
        return jsonify({"error": "We could not process this file. Please try again."}), 500
    finally:
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
    if LinearRegression is None or train_test_split is None:
        app.logger.warning("Regression training skipped because scikit-learn is unavailable.")
        return
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
    - Chronological daily aggregation
    - Pre-defined risk thresholds with no target leakage
    - Transparent rule-based risk scoring
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
    
    # If the ML module is unavailable, retain the same transparent risk rules
    # in session rather than falling back to a classifier trained on its own
    # derived labels.
    print("[finsight] Falling back to rule-based risk scoring")
    features = ["revenue", "expenses", "profit", "amount"]
    if (RuleBasedRiskModel is None or df.empty or "tx_date" not in df.columns
            or any(c not in df.columns for c in features)):
        models["risk"] = None
        return
    working = df.copy()
    for column in features:
        working[column] = pd.to_numeric(working[column], errors="coerce").fillna(0)
    periods = working.groupby(working["tx_date"].dt.date, as_index=False)[features].sum()
    if periods.empty:
        models["risk"] = None
        return
    try:
        rule_model = RuleBasedRiskModel()
        models["risk"] = {"model": rule_model, "features": features, "periods": len(periods)}
    except Exception as exc:
        print(f"[finsight] Could not train risk classifier: {exc}")
        models["risk"] = None


# =====================================================
# Routes - Predictions
# =====================================================
MIN_FORECAST_OBSERVATIONS = 12


def _forecast_targets(df: pd.DataFrame | None) -> list[str]:
    """Return numeric financial targets with enough dated history to try."""
    if df is None or df.empty or "tx_date" not in df.columns:
        return []
    result = []
    dates = pd.to_datetime(df["tx_date"], errors="coerce")
    for target in ("revenue", "expenses", "profit", "amount"):
        if target not in df.columns:
            continue
        values = pd.to_numeric(df[target], errors="coerce")
        valid_values = values[dates.notna()].dropna()
        if (len(valid_values) >= MIN_FORECAST_OBSERVATIONS
                and valid_values.nunique() >= 2):
            result.append(target)
    return result


def _forecast_step_days(df: pd.DataFrame) -> int:
    dates = pd.to_datetime(df.get("tx_date"), errors="coerce").dropna().sort_values().drop_duplicates()
    if len(dates) < 2:
        return 1
    step = dates.diff().dt.total_seconds().div(86400).dropna().median()
    try:
        return max(1, min(366, int(round(float(step)))))
    except (TypeError, ValueError):
        return 1


def _forecast_status(user_id: int, df: pd.DataFrame | None) -> dict[str, object]:
    """Explain forecast readiness without turning data quality into a crash."""
    status: dict[str, object] = {
        "valid_dates": 0,
        "invalid_dates": 0,
        "date_column_found": False,
        "date_mapping_found": True,
        "target_counts": {},
        "warning": "",
    }
    if df is None or df.empty:
        status["warning"] = "Upload a dataset to enable forecasting."
        return status

    if "tx_date" not in df.columns:
        status["warning"] = (
            "Forecasting is currently unavailable because no usable date column was found. "
            "Your data is still available for dashboard and analytics use."
        )
        return status

    dates = pd.to_datetime(df["tx_date"], errors="coerce")
    valid_date_count = int(dates.notna().sum())
    status["valid_dates"] = valid_date_count
    status["date_column_found"] = bool(valid_date_count)
    try:
        upload = run_query(
            """SELECT column_mapping, cleaning_summary FROM uploaded_files
               WHERE user_id=%s AND status='processed' ORDER BY id DESC LIMIT 1""",
            (user_id,), fetchone=True,
        )["row"] or {}
        cleaning = json.loads(upload.get("cleaning_summary") or "{}")
        status["invalid_dates"] = int(cleaning.get("invalid_dates") or 0)
        mapping = json.loads(upload.get("column_mapping") or "{}")
        if isinstance(mapping, dict) and not mapping.get("tx_date"):
            status["date_column_found"] = False
            status["date_mapping_found"] = False
    except Exception as exc:
        app.logger.warning("Forecast quality details unavailable: %s", exc)

    counts: dict[str, int] = {}
    for target in ("revenue", "expenses", "profit", "amount"):
        if target not in df.columns:
            continue
        counts[target] = int(pd.to_numeric(df.loc[dates.notna(), target], errors="coerce").notna().sum())
    status["target_counts"] = counts
    if not status["date_mapping_found"]:
        status["warning"] = (
            "Forecasting is currently unavailable because no usable date column was found. "
            "Check for Date, Transaction Date, Created Date, Timestamp, or Time. Your data is "
            "still available for dashboard and analytics use."
        )
    elif not status["date_column_found"]:
        invalid_note = (
            f" {status['invalid_dates']} date values could not be parsed."
            if status["invalid_dates"] else ""
        )
        status["warning"] = (
            "Forecasting is currently unavailable because no valid dated observations were found."
            f"{invalid_note} Check the date column and supported date formats. Your data is still "
            "available for dashboard and analytics use."
        )
    else:
        best_count = max(counts.values(), default=0)
        if best_count < MIN_FORECAST_OBSERVATIONS:
            invalid_note = (
                f" {status['invalid_dates']} date values could not be parsed."
                if status["invalid_dates"] else ""
            )
            status["warning"] = (
                f"Forecasting is currently unavailable because the uploaded dataset contains only "
                f"{best_count} valid dated numeric observations; at least {MIN_FORECAST_OBSERVATIONS} "
                f"are needed for a reliable forecast.{invalid_note} Your data has still been successfully "
                "loaded and can be used in the dashboard and analytics."
            )
        elif not _forecast_targets(df):
            status["warning"] = (
                "Forecasting is currently unavailable because the available financial values do not "
                "contain enough variation. Your data is still available for dashboard and analytics use."
            )
    return status


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
        forecast_targets = _forecast_targets(df)
        forecast_status = _forecast_status(user_id, df)
        if available_dates:
            next_history_date = (pd.to_datetime(available_dates[-1]) +
                                 pd.Timedelta(days=_forecast_step_days(df))).date()
            default_date = max(date.today() + timedelta(days=1), next_history_date).isoformat()
        else:
            default_date = (date.today() + timedelta(days=1)).isoformat()
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
            risk_max_date="",
            risk_default_date=default_date,
            forecast_targets=forecast_targets,
            expense_targets=[target for target in forecast_targets if target in {"expenses", "profit", "amount"}],
            risk_available=all(target in forecast_targets for target in ("revenue", "expenses", "profit")),
            analysis=analysis if analysis is not None else {},
            insight=(analysis or {}).get("insight", ""),
            section_types=(analysis or {}).get("types", {}),
            date_column=date_column,
            forecast_status=forecast_status,
        )

    if not request.is_json:
        return jsonify({"error": _message("api.error.predict_json_required")}), 400
    payload = request.get_json(silent=True) or {}
    model_type = payload.get("model_type")
    date_str = payload.get("date")
    try:
        forecast_periods = max(1, min(24, int(payload.get("forecast_periods", 1))))
    except (TypeError, ValueError):
        return jsonify({"error": "Forecast horizon must be a whole number between 1 and 24."}), 400

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
        step_days = _forecast_step_days(df)
        forecast_points = []
        prediction_info = {}
        for period in range(forecast_periods):
            forecast_date = target_date + timedelta(days=step_days * period)
            if get_pipeline is not None:
                value, info = get_pipeline().predict_regression(
                    user_id, model_type, {}, history_df=df, prediction_date=forecast_date
                )
                # A model may have been persisted by an older pipeline
                # version or for a previous upload. Rebuild once from the
                # active dataset before returning a user-facing failure.
                if value is None:
                    _train_models_for(user_id, df)
                    value, info = get_pipeline().predict_regression(
                        user_id, model_type, {}, history_df=df, prediction_date=forecast_date
                    )
                if value is None:
                    return jsonify({"error": info.get("error") or _message("api.error.predict_invalid_input")}), 400
                predicted_value = float(value)
                prediction_info = info or {}
            else:
                date_number = (pd.to_datetime(forecast_date) - model_info["df_min"]).days
                X_future = pd.DataFrame({"Date_Number": [date_number], "Month": [forecast_date.month],
                                             "Day_of_Week": [forecast_date.weekday()]})
                predicted_value = float(model_info["model"].predict(X_future)[0])
                prediction_info = {
                    "model_name": "linear_regression",
                    "estimated_error": model_info.get("metrics", {}).get("rmse", 0),
                }
            matching = df[pd.to_datetime(df["tx_date"], errors="coerce").dt.date == forecast_date]
            actual_value = None
            if not matching.empty and model_type in matching.columns:
                actual_value = round(float(pd.to_numeric(matching[model_type], errors="coerce").fillna(0).sum()), 2)
            forecast_points.append({
                "date": str(forecast_date), "value": round(predicted_value, 2),
                "actual": actual_value,
            })

        predicted_value = forecast_points[0]["value"]

        # Give the UI a meaningful, per-metric reference point. This makes
        # advice compare this forecast with the user's recent actual data.
        recent_baseline = None
        if model_type in df.columns:
            recent_values = pd.to_numeric(df[model_type], errors="coerce").dropna().tail(7)
            if not recent_values.empty:
                recent_baseline = round(float(recent_values.mean()), 2)

        # Save the prediction
        file_id = dataset_id

        prediction_ids = []
        for point in forecast_points:
            actual_value = point["actual"]
            prediction_error = round(actual_value - point["value"], 2) if actual_value is not None else None
            prediction_id = run_query(
                """INSERT INTO predictions
                   (user_id, company_id, uploaded_file_id, prediction_type, prediction_date,
                    actual_value, predicted_value, prediction_error, model_name)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (user_id, company_id, file_id, model_type, point["date"], actual_value,
                 point["value"], prediction_error, prediction_info.get("model_name", "linear_regression")),
                commit=True,
            )["last_id"]
            prediction_ids.append(prediction_id)

        add_history(
            user_id, company_id, "prediction",
            f"{model_type.title()} forecast from {target_date}",
            prediction_id=prediction_ids[0], file_id=file_id,
            status="ok", details=f"periods={forecast_periods}; first_value={predicted_value}",
        )

        try:
            _generate_powerbi_resources(user_id, company_id, session["company_name"])
        except Exception as exc:
            app.logger.warning("Power BI resource refresh skipped after prediction: %s", exc)

        # Build the historical + prediction chart
        # A chart is an enhancement, never a reason for a valid ML result to
        # fail (some matplotlib backends can be unavailable on Windows).
        try:
            chart = _build_chart(df, model_type, forecast_points)
        except Exception as chart_exc:
            print(f"[finsight] Chart rendering skipped: {chart_exc}")
            chart = ""

        return jsonify({
            "success": True,
            "prediction": predicted_value,
            "date": str(target_date),
            "model_type": model_type,
            "forecast_periods": forecast_periods,
            "forecasts": forecast_points,
            "history": [
                {"date": str(pd.to_datetime(row["tx_date"]).date()), "value": float(row[model_type])}
                for _, row in df.dropna(subset=["tx_date", model_type]).tail(60).iterrows()
            ],
            "chart": chart,
            "prediction_id": prediction_ids[0],
            "prediction_ids": prediction_ids,
            "baseline": recent_baseline,
            "model": {
                "name": prediction_info.get("model_name", "linear_regression"),
                "estimated_error": float(prediction_info.get("estimated_error") or 0),
            },
        })
    except Exception:
        app.logger.exception("Prediction request failed for user %s", user_id)
        return jsonify({"error": "Prediction could not be completed. Please try again."}), 500


def _build_chart(df: pd.DataFrame, model_type: str, forecast_points: list[dict]) -> str:
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
    forecast_dates = [pd.to_datetime(point["date"]) for point in forecast_points]
    forecast_values = [point["value"] for point in forecast_points]
    plt.plot(forecast_dates, forecast_values, color="red", marker="o",
             linewidth=2, linestyle="--", zorder=5, label="Forecast")
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


@app.get("/database")
@login_required
def database():
    """Compatibility view for the database-backed upload/prediction activity."""
    return history()


@app.get("/settings")
@login_required
def settings():
    """Show the signed-in account and active application settings."""
    user_columns = _load_user_columns()
    company_column = user_columns.get("company_id")
    email_column = user_columns.get("email")
    created_column = user_columns.get("created_at")
    if company_column:
        company_sql = f"u.{_quote_identifier(company_column)} AS company_id, c.company_name"
        company_join = "JOIN companies c ON c.id = u." + _quote_identifier(company_column)
    else:
        company_sql = "NULL AS company_id, '' AS company_name"
        company_join = ""
    email_sql = (
        f"u.{_quote_identifier(email_column)} AS email"
        if email_column else "'' AS email"
    )
    created_sql = (
        f"u.{_quote_identifier(created_column)} AS created_at"
        if created_column else "NULL AS created_at"
    )
    account = run_query(
        f"""SELECT {_user_display_expression(user_columns)},
                  {email_sql}, {created_sql},
                  {company_sql}
           FROM users u {company_join}
           WHERE u.id=%s""",
        (_session_user_id(),), fetchone=True,
    )["row"] or {
        "name": session.get("user_name", ""),
        "email": "",
        "company_name": session.get("company_name", ""),
        "created_at": None,
    }
    return render_template(
        "settings.html",
        company_name=session["company_name"],
        account=account,
        supported_formats="CSV, XLSX, XLS, JSON",
        max_upload_mb=round(MAX_CONTENT_LENGTH / (1024 * 1024), 2),
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


def _powerbi_export_frame(frame: pd.DataFrame, *, source: bool = False) -> pd.DataFrame:
    """Return a stable, Power BI-friendly frame without app-only artifacts."""
    work = frame.copy() if frame is not None else pd.DataFrame()
    if work.empty and len(work.columns) == 0:
        return work
    if source:
        detection = detect_columns(work)
        mapping = _add_generic_prediction_mapping(work, detection, detection.get("mapping", {}))
        try:
            work, _ = clean_dataframe(work, mapping)
        except ValueError:
            work = apply_mapping(work, mapping)
        work = work.rename(columns={
            "tx_date": "Date", "transaction_id": "Transaction_ID", "description": "Description",
            "amount": "Amount", "revenue": "Revenue", "expenses": "Expenses", "profit": "Profit",
            "customers": "Customers", "marketing_spend": "Marketing_Spend", "tx_type": "Transaction_Type",
            "category": "Category", "payment_method": "Payment_Method", "department": "Department",
            "city": "City", "status": "Status",
        })
    work.columns = [str(column).strip() for column in work.columns]
    work = work.loc[:, ~work.columns.duplicated(keep="first")]
    artifacts = {"date_number", "month", "day_of_week"}
    remove = []
    for column in work.columns:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(column).casefold()).strip("_")
        if normalized.startswith("unnamed_") or normalized in artifacts:
            remove.append(column)
        elif normalized in {"index", "row_number"}:
            values = pd.to_numeric(work[column], errors="coerce")
            expected = pd.Series(range(len(work)), index=values.index, dtype="float64")
            if values.notna().all() and values.astype("float64").equals(expected):
                remove.append(column)
    if remove:
        work = work.drop(columns=remove)

    for column in ("Date", "Prediction_Date", "First_Date", "Last_Date"):
        if column in work.columns:
            parsed = pd.to_datetime(work[column], errors="coerce")
            work[column] = parsed.dt.date.where(parsed.notna(), None)
    numeric_columns = {
        "Amount", "Revenue", "Expenses", "Profit", "Customers", "Marketing_Spend",
        "Predicted_Value", "Actual_Value", "Prediction_Error", "Total_Amount",
        "Total_Revenue", "Total_Expenses", "Total_Profit", "Total_Customers",
    }
    for column in numeric_columns.intersection(work.columns):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    try:
        work = work.drop_duplicates()
    except TypeError:
        work = work.loc[~work.astype("string").duplicated()]
    return work.reset_index(drop=True)


def _excel_download(frame: pd.DataFrame, filename: str):
    """Return one in-memory .xlsx file without exposing a server file path."""
    frame = _powerbi_export_frame(frame)
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
            raw = _read_upload_dataframe(source, source.name)
            raw = _powerbi_export_frame(raw, source=True)
            return _excel_download(raw, "finsight_raw_data.xlsx")

        if export_type == "cleaned":
            frames = _powerbi_export_frames(user_id, session["company_name"], dataset_id)
            cleaned = frames["Cleaned_Data"].copy()
            if cleaned.empty:
                return jsonify({"error": "Cleaned data does not exist for the current dataset."}), 404
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
    cleaned_columns = [
        "Date", "Transaction_ID", "Description", "Amount", "Revenue", "Expenses",
        "Profit", "Customers", "Marketing_Spend", "Transaction_Type", "Category", "Payment_Method", "Department",
        "City", "Status", "Upload_File_ID", "User_ID", "Imported_At",
    ]
    prediction_columns = [
        "Prediction_ID", "Prediction_Type", "Prediction_Date", "Actual_Value",
        "Predicted_Value", "Prediction_Error", "Model", "Created_At",
    ]

    try:
        stored_frame = _dataset_rows_frame(user_id, dataset_id)
    except Exception as exc:
        print(f"[finsight] Could not read preserved dataset rows for Power BI: {exc}")
        stored_frame = pd.DataFrame()
    if not stored_frame.empty:
        cleaned = stored_frame.rename(columns={
            "tx_date": "Date", "transaction_id": "Transaction_ID", "description": "Description",
            "amount": "Amount", "revenue": "Revenue", "expenses": "Expenses", "profit": "Profit",
            "customers": "Customers", "marketing_spend": "Marketing_Spend", "tx_type": "Transaction_Type",
            "category": "Category", "payment_method": "Payment_Method", "department": "Department",
            "city": "City", "status": "Status",
        })
        cleaned = cleaned.loc[:, ~cleaned.columns.duplicated(keep="first")]
        extra_columns = [column for column in cleaned.columns if column not in cleaned_columns]
        cleaned = cleaned.reindex(columns=[*cleaned_columns, *extra_columns])
    else:
        cleaned_rows = run_query(
            """
             SELECT tx_date AS Date, transaction_id AS Transaction_ID, description AS Description,
                   amount AS Amount, revenue AS Revenue, expenses AS Expenses, profit AS Profit,
                   customers AS Customers, marketing_spend AS Marketing_Spend,
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
        cleaned = pd.DataFrame(cleaned_rows).reindex(columns=cleaned_columns)

    cleaned = _powerbi_export_frame(cleaned)
    for column, value in (("Upload_File_ID", dataset_id), ("User_ID", user_id),
                          ("Imported_At", datetime.now().replace(microsecond=0))):
        if column not in cleaned.columns:
            cleaned[column] = value
        else:
            cleaned[column] = cleaned[column].where(cleaned[column].notna(), value)

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
    predictions = _powerbi_export_frame(
        pd.DataFrame(prediction_rows).reindex(columns=prediction_columns)
    )
    metric_columns = ["Amount", "Revenue", "Expenses", "Profit", "Customers", "Marketing_Spend"]
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
                Customers=("Customers", "sum"), Marketing_Spend=("Marketing_Spend", "sum"),
                Transactions=("Date", "size"),
            )
        grouped = cleaned.copy()
        grouped["Category"] = grouped["Category"].fillna("Uncategorized")
        categories = grouped.groupby("Category", as_index=False).agg(
            Amount=("Amount", "sum"), Revenue=("Revenue", "sum"),
            Expenses=("Expenses", "sum"), Profit=("Profit", "sum"),
            Customers=("Customers", "sum"), Marketing_Spend=("Marketing_Spend", "sum"),
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
            Profit=("Profit", "sum"), Customers=("Customers", "sum"),
            Marketing_Spend=("Marketing_Spend", "sum"),
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
        frame.to_csv(paths["data"] / filename, index=False, encoding="utf-8-sig")
    frames["Cleaned_Data"].to_csv(paths["financial"], index=False, encoding="utf-8-sig")
    frames["Predictions"].to_csv(paths["predictions"], index=False, encoding="utf-8-sig")
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
            mix_chart.add_data(Reference(category_sheet, min_col=9, max_col=9,
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
    limit = round(MAX_CONTENT_LENGTH / (1024 * 1024), 2)
    return jsonify({"error": f"Uploaded file is too large. Maximum size is {limit:g} MB."}), 413


@app.errorhandler(RuntimeError)
def runtime_configuration_error(error):
    """Keep configuration/runtime failures useful without exposing internals."""
    app.logger.error("Application runtime failure: %s", error, exc_info=True)
    message = "The application is temporarily unavailable. Please try again later."
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": message}), 503
    flash(message, "danger")
    return render_template("500.html"), 503


@app.errorhandler(mysql.connector.Error)
def database_unavailable(error):
    """Return a useful response when MySQL is unavailable or misconfigured."""
    app.logger.error(
        "Database request failed: %s: %s",
        type(error).__name__,
        error,
        exc_info=(type(error), error, error.__traceback__),
    )
    message = "Database connection failed. Please try again later."
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": message}), 503
    if request.path == "/signup":
        flash(message, "danger")
        return render_template("signup.html"), 503
    if request.path == "/login":
        flash(message, "danger")
        return render_template("login.html"), 503
    return render_template("500.html"), 503


@app.errorhandler(500)
def server_error(_):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return render_template("500.html"), 500


# =====================================================
# Entry point
# =====================================================
init_database()

if __name__ == "__main__":
    host = _env("FLASK_RUN_HOST", "0.0.0.0")
    port = _int_env("PORT", _int_env("FLASK_RUN_PORT", 5000))
    debug = _bool_env("FLASK_DEBUG", False)
    print(f"[finsight] Starting Flask on http://{host}:{port} (debug={debug})")
    app.run(host=host, port=port, debug=debug)
