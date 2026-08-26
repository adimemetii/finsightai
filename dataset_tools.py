"""
dataset_tools.py — Universal business prediction toolkit for FinSight AI.
=======================================================================

Makes the prediction workflow dataset-agnostic:

* inspects any CSV/XLSX dataset (column types, missing values, duplicates,
  constants, invalid/useless columns),
* suggests a meaningful numeric target with a human-readable reason,
* decides whether the dataset is suitable for Linear Regression / Decision Tree,
* prepares a cleaned feature matrix without hardcoding column names,
* trains and evaluates Linear Regression and Decision Tree,
* computes quality (good / medium / poor) plus actionable suggestion keys
  (translated by the i18n layer),
* turns user-entered dynamic inputs into a prediction.

No function raises raw exceptions to callers — each entry point returns a
result dict so the web layer can always show a friendly message instead of a
crash.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MIN_ROWS = 8                # below this we cannot train a meaningful model
MAX_CATEGORY_CARDS = 40     # keep <select> widgets manageable in the UI
MIN_TARGET_UNIQUE = 3       # target must have at least this many unique values
RANDOM_STATE = 42
TEST_SIZE = 0.2
DROP_NA_FRACTION = 0.5      # numeric features with >50% NaN are dropped

# Column names that hint at a business prediction target.
TARGET_HINTS: List[str] = [
    "revenue", "sale", "sales", "profit", "amount", "demand", "cost",
    "customers", "customer_count", "total", "total_amount", "quantity",
    "count", "value", "price", "income", "balance", "conversions",
    "clicks", "expenses", "orders", "units", "turnover", "gmv",
]

# Column names that are usually not predictive for our two models.
IGNORED_NAMES: List[str] = [
    "id", "index", "row", "record", "serial", "ref", "reference", "note",
    "notes", "description", "desc", "comment", "comments", "remarks",
]


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------
def _is_numeric_free(series: pd.Series) -> bool:
    """True if the column converts cleanly to numbers (most non-null values)."""
    sample = series.dropna()
    if sample.empty:
        return False
    if sample.dtype.kind in "biufc":
        return True
    coerced = pd.to_numeric(sample.astype(str).str.strip(), errors="coerce")
    ratio = coerced.notna().mean()
    return bool(ratio >= 0.6)


def _looks_like_date(series: pd.Series) -> bool:
    sample = series.dropna().astype(str).str.strip()
    sample = sample[sample != ""]
    if len(sample) < 3:
        return False
    try:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    except (ValueError, TypeError):
        parsed = pd.to_datetime(sample, errors="coerce")
    return bool(parsed.notna().mean() >= 0.6)


def _looks_like_boolean(series: pd.Series) -> bool:
    sample = series.dropna().astype(str).str.strip().str.lower().unique()
    allowed = {"0", "1", "true", "false", "yes", "no", "y", "n"}
    return bool(len(sample) > 0 and all(v in allowed for v in sample))


def _max_corr(df: pd.DataFrame, col: str) -> float:
    """Largest absolute correlation between a numeric column and other nums."""
    try:
        nums = df.select_dtypes(include=[np.number])
        if col not in nums.columns or len(nums.columns) < 2:
            return 0.0
        corr = nums.corr()[col].drop(labels=[col], errors="ignore").abs()
        return float(corr.max()) if not corr.empty else 0.0
    except Exception:
        return 0.0


def _target_ratio(series: pd.Series) -> float:
    uniq = series.nunique(dropna=True)
    return uniq / len(series) if len(series) > 0 else 0.0


# ------------------------------------------------------------------
# Public API — dataset inspection
# ------------------------------------------------------------------
def inspect_dataset(df: pd.DataFrame) -> Dict[str, Any]:
    """Produce a full profile of an uploaded dataset (never crashes)."""
    profile: Dict[str, Any] = {
        "rows": int(len(df)),
        "columns": list(df.columns.astype(str)),
        "column_types": {},
        "numeric_cols": [],
        "categorical_cols": [],
        "date_cols": [],
        "boolean_cols": [],
        "constant_cols": [],
        "empty_cols": [],
        "invalid_cols": [],
        "missing": {},
        "missing_total": 0,
        "duplicates": 0,
        "target_candidates": [],
        "suggested_target": None,
        "suggested_reason": None,
        "ok": True,
        "suitable": True,
        "warning": "",
    }

    if df is None or df.empty:
        profile["ok"] = False
        profile["warning"] = "ml.error.insufficient_data"
        return profile

    profile["duplicates"] = int(df.duplicated().sum())

    for raw_col in df.columns:
        col = str(raw_col)
        series = df[raw_col]
        n_null = int(series.isna().sum())
        profile["missing"][col] = n_null
        profile["missing_total"] += n_null

        if n_null == len(series):
            profile["empty_cols"].append(col)
            profile["column_types"][col] = "empty"
            continue
        if series.nunique(dropna=True) <= 1:
            profile["constant_cols"].append(col)
            profile["column_types"][col] = "constant"
            continue
        if _looks_like_boolean(series):
            profile["boolean_cols"].append(col)
            profile["column_types"][col] = "boolean"
            continue
        if _is_numeric_free(series):
            profile["numeric_cols"].append(col)
            profile["column_types"][col] = "numeric"
            continue
        if _looks_like_date(series):
            profile["date_cols"].append(col)
            profile["column_types"][col] = "date"
            continue
        cards = series.nunique(dropna=True)
        if cards <= 1000:
            profile["categorical_cols"].append(col)
            profile["column_types"][col] = "categorical"
        else:
            profile["invalid_cols"].append(col)
            profile["column_types"][col] = "invalid"

    return profile
# --- candidate targets: numeric columns with real variation ---
    candidates: List[Dict[str, Any]] = []
    for col in profile["numeric_cols"]:
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(series) < MIN_ROWS:
            continue
        uniq = int(series.nunique())
        if uniq < MIN_TARGET_UNIQUE or _target_ratio(series) > 0.9:
            continue
        mean = float(series.mean())
        std = float(series.std() or 0.0)
        cv = std / abs(mean) if mean else std
        hinted = any(word in col.lower() for word in TARGET_HINTS)
        ignored = col.lower() in IGNORED_NAMES or col.lower().strip().endswith("_id")
        if ignored:
            continue
        candidates.append({
            "name": col, "unique": uniq, "std": std, "mean": mean,
            "cv": cv, "hinted": hinted, "correlation": _max_corr(df, col),
        })
    candidates.sort(key=lambda c: (not c["hinted"], -c["cv"]))

    # Drop near-duplicate target copies so we don't offer two identical goals.
    keep: List[str] = []
    for cand in candidates:
        if cand["name"] in keep:
            continue
        dup = any(cand["name"] != o["name"] and cand["correlation"] > 0.9999
                  for o in candidates if o["name"] in keep)
        if not dup:
            keep.append(cand["name"])
    candidates = [c for c in candidates if c["name"] in keep]
    profile["target_candidates"] = candidates

    target, reason = suggest_target(profile)
    profile["suggested_target"] = target
    profile["suggested_reason"] = reason

    ok, warning = _suitability(profile)
    profile["ok"] = ok
    profile["suitable"] = ok
    profile["warning"] = warning
    return profile


def suggest_target(profile: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Pick the best numeric target and the reason (an i18n key)."""
    candidates = profile.get("target_candidates") or []
    if not candidates:
        return None, None
    hinted = [c for c in candidates if c["hinted"]]
    pool = hinted or candidates
    best = pool[0]
    if best["hinted"]:
        reason = "ml.target.suggested_reason.name"
    elif best["cv"] > 0.0:
        reason = "ml.target.suggested_reason.numeric"
    else:
        reason = "ml.target.suggested_reason.correlation"
    return best["name"], reason
def _suitability(profile: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (ok, warning_key) assessing whether prediction is meaningful."""
    rows = profile["rows"]
    if rows < MIN_ROWS:
        return False, "ml.validation.reason.rows"
    if not profile["numeric_cols"]:
        if not profile["date_cols"] and not profile["categorical_cols"]:
            return False, "ml.validation.reason.no_info"
        return False, "ml.validation.reason.no_numeric_target"
    if not profile["target_candidates"]:
        return False, "ml.validation.reason.unsuitable"
    return True, ""


def _fmt_num(value: float) -> str:
    try:
        value = float(value)
        if value == int(value):
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def prepare_dataset(df: pd.DataFrame, target: str) -> Dict[str, Any]:
    """Build a numeric feature matrix + input-field metadata for any target."""
    result: Dict[str, Any] = {"ok": False, "error": ""}

    if df is None or df.empty or target not in df.columns:
        result["error"] = "ml.error.no_target"
        return result

    working = df.copy()
    y_raw = pd.to_numeric(working[target], errors="coerce")
    if y_raw.notna().sum() < MIN_ROWS:
        result["error"] = "ml.error.insufficient_data"
        return result
    if y_raw.nunique(dropna=True) < MIN_TARGET_UNIQUE:
        result["error"] = "ml.suggest.target_variation"
        return result
    y = y_raw.dropna()
    working = working.loc[y.index].copy()
    working[target] = y

    fields: List[Dict[str, Any]] = []
    label_maps: Dict[str, Dict[str, int]] = {}
    numeric_defaults: Dict[str, float] = {}
    date_bases: Dict[str, str] = {}
    dropped: List[str] = []

    for col in working.columns.astype(str):
        if col == target:
            continue
        series = working[col]
        if series.isna().all() or series.nunique(dropna=True) <= 1:
            dropped.append(col)
            continue
        if col.lower() in IGNORED_NAMES or col.lower().strip().endswith("_id"):
            dropped.append(col)
            continue

        # --- numeric ---
        if _is_numeric_free(series):
            nums = pd.to_numeric(series, errors="coerce").dropna()
            if len(nums) >= MIN_ROWS:
                median = float(nums.median())
                working[col] = pd.to_numeric(working[col], errors="coerce").fillna(median)
                numeric_defaults[col] = median
                fields.append({"key": col, "label": col, "type": "number",
                               "default": _fmt_num(median)})
                continue

        # --- boolean ---
        if _looks_like_boolean(series):
            mapped = series.astype(str).str.strip().str.lower().map(
                {"true": 1, "yes": 1, "y": 1, "1": 1,
                 "false": 0, "no": 0, "n": 0, "0": 0}).astype(float)
            working[col] = mapped.fillna(mapped.mode().iloc[0] if len(mapped) else 0)
            fields.append({"key": col, "label": col, "type": "select",
                           "options": ["1", "0"], "default": "1"})
            continue

        # --- date → numeric temporal features ---
        try:
            parsed = pd.to_datetime(series, errors="coerce", format="mixed")
        except (ValueError, TypeError):
            parsed = pd.to_datetime(series, errors="coerce")
        if parsed.notna().sum() >= MIN_ROWS:
            valid = parsed.dropna()
            base = valid.min()
            working[f"{col}_year"] = parsed.dt.year.astype(float)
            working[f"{col}_month"] = parsed.dt.month.astype(float)
            working[f"{col}_dayofweek"] = parsed.dt.dayofweek.astype(float)
            working[f"{col}_days"] = (parsed - base).dt.days.astype(float)
            for feat in (f"{col}_year", f"{col}_month",
                         f"{col}_dayofweek", f"{col}_days"):
                working[feat] = working[feat].fillna(float(working[feat].median()))
            fields.append({"key": col, "label": col, "type": "date",
                           "default": valid.max().strftime("%Y-%m-%d")})
            date_bases[col] = base.strftime("%Y-%m-%d")
            continue

        # --- categorical ---
        cards = series.astype(str).nunique(dropna=True)
        if cards > MAX_CATEGORY_CARDS:
            dropped.append(col)
            continue
        filled = series.astype(str).fillna("(missing)")
        mode_val = filled.mode().iloc[0] if len(filled) else "(missing)"
        levels = sorted(filled.unique().tolist())
        code = {v: i for i, v in enumerate(levels)}
        working[col] = filled.map(code).astype(float)
        label_maps[col] = code
        fields.append({"key": col, "label": col, "type": "select",
                       "options": levels, "default": mode_val})

    # --- final numeric feature matrix ---
    feature_cols = [c for c in working.columns if c != target
                    and working[c].dtype.kind in "biufc"
                    and c not in dropped]
    if not feature_cols:
        result["error"] = "ml.error.no_features"
        return result
    X = working[feature_cols].astype(float)

    result.update({
        "ok": True,
        "X": X,
        "y": y.astype(float),
        "feature_names": list(X.columns),
        "fields": fields,
        "label_maps": label_maps,
        "numeric_defaults": numeric_defaults,
        "date_bases": date_bases,
        "dropped": dropped,
        "raw_columns": list(df.columns.astype(str)),
    })
    return result
# ------------------------------------------------------------------
# Model training / evaluation / suggestions
# ------------------------------------------------------------------
def evaluate_model(X: pd.DataFrame, y: pd.Series, model_key: str = "linear",
                   profile: Optional[Dict[str, Any]] = None,
                   target: Optional[str] = None) -> Dict[str, Any]:
    """Train + evaluate one model. Always returns a safe dict."""
    out: Dict[str, Any] = {"ok": False}
    if X is None or y is None or len(X) < MIN_ROWS or len(X) != len(y):
        out["error"] = "ml.error.insufficient_data"
        return out
    if model_key not in ("linear", "tree"):
        out["error"] = "ml.error.training_failed"
        return out

    try:
        from sklearn.pipeline import Pipeline
        from sklearn.linear_model import LinearRegression as _LR
        x_train, x_test, y_train, y_test = train_test_split(
            X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE)

        if model_key == "linear":
            pipe: Any = Pipeline([("scaler", StandardScaler()),
                                  ("model", _LR())])
        else:
            depth = min(12, max(6, int(np.log2(max(len(X), 4))) + 2))
            pipe = DecisionTreeRegressor(max_depth=depth, min_samples_leaf=2,
                                         random_state=RANDOM_STATE)
        pipe.fit(x_train, y_train)

        y_pred = pipe.predict(x_test)
        mae = float(mean_absolute_error(y_test, y_pred))
        mse = float(mean_squared_error(y_test, y_pred))
        rmse = float(np.sqrt(mse))
        r2 = float(r2_score(y_test, y_pred))
        train_r2 = float(r2_score(y_train, pipe.predict(x_train)))

        if r2 >= 0.6:
            quality = "good"
        elif r2 >= 0.25:
            quality = "medium"
        else:
            quality = "poor"

        out.update({
            "ok": True,
            "model": pipe,
            "model_key": model_key,
            "n_train": int(len(x_train)),
            "n_test": int(len(x_test)),
            "features": int(X.shape[1]),
            "mae": mae, "mse": mse, "rmse": rmse, "r2": r2,
            "train_r2": train_r2,
            "quality": quality,
            "suggestions": build_suggestions(
                model_key=model_key, r2=r2, train_r2=train_r2,
                rows=len(X), profile=profile or {}),
        })
    except Exception:
        out["error"] = "ml.error.training_failed"
    return out


def build_suggestions(model_key: str, r2: float, train_r2: float,
                      rows: int, profile: Dict[str, Any]) -> List[str]:
    """Translate evaluation results into i18n suggestion keys."""
    tips: List[str] = []
    if profile.get("missing_total", 0) > 0:
        tips.append("ml.suggest.handle_missing")
    if profile.get("duplicates", 0) > 0:
        tips.append("ml.suggest.remove_duplicates")
    if r2 < 0.25:
        if rows < 40:
            tips.append("ml.suggest.more_rows")
        tips.append("ml.suggest.remove_outliers")
        tips.append("ml.suggest.remove_irrelevant")
        tips.append("ml.suggest.try_other_model")
    if model_key == "tree" and (train_r2 - r2) > 0.3 and r2 < 0.5:
        tips.append("ml.suggest.tree_overfit")
    if model_key == "linear" and r2 < 0.25:
        tips.append("ml.suggest.linear_not_appropriate")
    if tips:
        return _dedupe(tips)
    return ["ml.summary.good"]


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
# ------------------------------------------------------------------
# Dynamic prediction inputs -> numeric row -> prediction
# ------------------------------------------------------------------
def build_input_row(prepared: Dict[str, Any],
                    values: Optional[Dict[str, Any]] = None) -> Optional[pd.DataFrame]:
    """Convert user-entered dynamic inputs into a single feature row."""
    values = values or {}
    try:
        row = prepared["X"].median().to_dict()
    except Exception:
        return None

    for field in prepared.get("fields", []):
        key = field["key"]
        ftype = field.get("type")
        raw = values.get(key, field.get("default"))
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            raw = field.get("default")

        if ftype == "date":
            try:
                dt = pd.to_datetime(str(raw))
            except (ValueError, TypeError):
                dt = pd.to_datetime(str(field.get("default")))
            if pd.isna(dt):
                continue
            base = pd.to_datetime(str(prepared.get("date_bases", {}).get(key, "")))
            if pd.isna(base):
                base = dt
            row[f"{key}_year"] = float(dt.year)
            row[f"{key}_month"] = float(dt.month)
            row[f"{key}_dayofweek"] = float(dt.dayofweek)
            row[f"{key}_days"] = float((dt - base).days)
            continue

        if ftype == "select":
            code_map = prepared.get("label_maps", {}).get(key)
            if code_map is not None:
                code = code_map.get(str(raw))
                if code is None:
                    code = code_map.get(str(field.get("default")), 0)
                row[key] = float(code)
            elif str(raw) in ("1", "0"):
                row[key] = float(raw)
            continue

        # number
        try:
            row[key] = float(str(raw).replace(",", ".").replace(" ", ""))
        except (ValueError, TypeError, KeyError):
            continue

    feature_names = prepared.get("feature_names", [])
    cols = [c for c in feature_names if c in row]
    return pd.DataFrame([row], columns=cols)


def predict_from_prepared(prepared: Dict[str, Any], model: Any,
                          values: Dict[str, Any]) -> Dict[str, Any]:
    """Run a single prediction. Returns a safe dict with ok/value/error."""
    out: Dict[str, Any] = {"ok": False}
    row_df = build_input_row(prepared, values)
    if row_df is None or row_df.empty:
        out["error"] = "ml.error.invalid_input"
        return out
    try:
        row_df = row_df.reindex(columns=prepared["feature_names"], fill_value=0.0)
        result = float(model.predict(row_df)[0])
        if not np.isfinite(result):
            out["error"] = "ml.error.invalid_input"
            return out
        out.update({"ok": True, "value": round(result, 4)})
    except Exception:
        out["error"] = "ml.error.invalid_input"
    return out