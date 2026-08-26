"""
universal_analysis.py — Universal data analysis & prediction for FinSight AI.
==========================================================================

Dataset-agnostic pipeline configured around the rule:

    UPLOAD CSV/EXCEL -> AUTOMATIC CLEANING -> AUTOMATIC ANALYSIS
    -> AUTOMATIC TARGET DETECTION -> LINEAR REGRESSION / DECISION TREE CLASSIFIER
    -> DYNAMIC PREDICTION SECTORS -> SHORT BUSINESS GROWTH SUGGESTIONS

No fixed column names are assumed.  Every entry point is defensive and returns
plain JSON-safe dicts so the web layer can always render a friendly message
instead of crashing on an unexpected or unsuitable dataset.

This module has no dependency on app.py / database code; it only uses Pandas,
NumPy and scikit-learn.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier

# ---------------------------------------------------------------------------
# Tunables (kept small / lightweight for speed)
# ---------------------------------------------------------------------------
MOST_ROWS = 2000              # cap expensive model work without losing trends
MIN_ROWS = 8                  # below this there is simply too little data
MIN_REGRESSION_ROWS = 12      # minimum valid rows for a meaningful regression
MAX_CATEGORY_CARDS = 20       # ignore categorical targets with too many levels
RANDOM_STATE = 42

# Priority order for target discovery.  These hints are ONLY used to rank
# candidates — never required.
REGRESSION_HINTS: List[str] = [
    "revenue", "sales", "sale", "profit", "amount", "expense", "expenses",
    "price", "income", "customer", "customers", "cost", "costs", "quantity",
    "qty", "unit", "units", "orders", "order", "count", "total", "value",
    "demand", "balance", "budget", "target", "gmv", "turnover", "conversion",
    "clicks", "sales_amount", "net", "gross",
]
CLASSIFICATION_HINT: List[str] = [
    "risk", "status", "segment", "class", "category", "type", "performance",
    "outcome", "result", "level", "tier", "grade", "group", "quality",
    "churn", "priority", "region", "branch", "city", "country", "channel",
    "market", "cohort", "plan", "sentiment", "label", "flag", "category_name",
]
ID_WORDS: List[str] = ["id", "identif", "serial", "record", "reference",
                       "ref", "key", "sku", "code", "number", "num"]
TEXT_WORDS: List[str] = ["name", "desc", "note", "comment", "address", "email",
                         "phone", "url", "message", "text", "summary", "detail"]


# ---------------------------------------------------------------------------
# Column type detection
# ---------------------------------------------------------------------------
def _as_text(series: pd.Series) -> pd.Series:
    return series.dropna().astype(str)


def _is_numeric(series: pd.Series) -> bool:
    s = _as_text(series).str.strip()
    if s.empty:
        return False
    if series.dtype.kind in "biufc":
        return True
    return bool(pd.to_numeric(s, errors="coerce").notna().mean() >= 0.6)


def _is_boolean(series: pd.Series) -> bool:
    s = _as_text(series).str.strip().str.lower()
    if len(s) < 2:
        return False
    allowed = {"0", "1", "true", "false", "yes", "no", "y", "n"}
    return bool(s.nunique() <= 4 and set(s.unique()).issubset(allowed))


def _is_date(series: pd.Series) -> bool:
    s = _as_text(series).str.strip()
    s = s[s != ""]
    if len(s) < 3:
        return False
    try:
        parsed = pd.to_datetime(s, errors="coerce", format="mixed")
    except Exception:
        return False
    return bool(parsed.notna().mean() >= 0.6)
def detect_types(df: pd.DataFrame) -> Dict[str, str]:
    """Classify every column as one of:
    numeric / categorical / date / boolean / text / id / constant / empty.
    Never raises even on a one-column or fully empty-styled dataset.
    """
    result: Dict[str, str] = {}
    if df is None or df.empty:
        return result
    n = len(df)
    for name in df.columns.astype(str):
        try:
            series = df[name]
        except Exception:
            result[name] = "invalid"
            continue
        nn = int(series.notna().sum())
        if nn == 0:
            result[name] = "empty"
            continue
        if int(series.nunique(dropna=True)) <= 1:
            result[name] = "constant"
            continue
        low = name.strip().lower()
        if _is_boolean(series):
            result[name] = "boolean"
            continue
        if _is_numeric(series):
            result[name] = "numeric"
            continue
        if _is_date(series):
            result[name] = "date"
            continue
        unique = int(series.nunique(dropna=True))
        ends_id = low.endswith("_id") or low.endswith("-id")
        has_id_word = any(w in low for w in ID_WORDS) and not any(
            w in low for w in REGRESSION_HINTS
        )
        if ends_id or has_id_word:
            if unique >= 0.85 * n:
                result[name] = "id"
                continue
        vals = _as_text(series)
        mean_len = float(np.mean([len(x) for x in vals if str(x).strip()]))
        high_card = unique >= 0.8 * n
        text_word = any(w in low for w in TEXT_WORDS)
        if text_word or (high_card and mean_len > 30):
            result[name] = "text"
            continue
        result[name] = "categorical"
    return result


# ---------------------------------------------------------------------------
# Cleaning (works on a copy — the original upload is never modified)
# ---------------------------------------------------------------------------
def _clean_copy(df: pd.DataFrame) -> pd.DataFrame:
    """Robust preprocessing copy: drop empty rows/cols, duplicates, Inf, and
    messy formatting.  Never raises."""
    if df is None:
        return pd.DataFrame()
    try:
        work = df.copy()
    except Exception:
        return pd.DataFrame()
    if work.empty:
        return work
    # De-duplicate column headers if the file has repeated names.
    if len(work.columns) != len(set(map(str, work.columns))):
        seen: Dict[str, int] = {}
        cols = []
        for c in work.columns:
            base = str(c)
            seen[base] = seen.get(base, -1) + 1
            cols.append(base if seen[base] == 0 else f"{base}_{seen[base]}")
        work.columns = cols
    work.columns = [str(c).strip() for c in work.columns]
    try:
        work = work.dropna(how="all")
        work = work.dropna(axis=1, how="all")
        work = work.drop_duplicates()
    except Exception:
        pass
    num = work.select_dtypes(include=[np.number])
    if not num.empty:
        try:
            work[num.columns] = num.replace([np.inf, -np.inf], np.nan)
        except Exception:
            pass
    return work


def clean_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """Public entry: returns a cleaned copy ready for analysis."""
    return _clean_copy(df)
# ---------------------------------------------------------------------------
# Target discovery
# ---------------------------------------------------------------------------
def _hint_rank(name: str, hints: List[str]) -> int:
    low = name.strip().lower()
    for i, hint in enumerate(hints):
        if hint in low:
            return i
    return 10 ** 6


def _is_id_name(name: str) -> bool:
    """True for columns that look like a unique-ID column (by name)."""
    low = name.strip().lower()
    if low.endswith("_id") or low.endswith("-id"):
        return True
    return any(w in low for w in ID_WORDS)


def regression_targets(df: pd.DataFrame, types: Dict[str, str]) -> List[str]:
    """Choose useful numeric prediction targets (up to 4), hint-ranked.
    Numeric ID-like columns are skipped."""
    cands: List[Tuple[int, float, int, str]] = []
    n_rows = int(len(df))
    for name, t in types.items():
        if t != "numeric":
            continue
        s = pd.to_numeric(df[name], errors="coerce").dropna()
        if len(s) < MIN_REGRESSION_ROWS or s.nunique(dropna=True) < 3:
            continue
        if abs(float(s.std(ddof=1))) < 1e-9:
            continue
        # Skip unique-ID style numeric columns (e.g. Order_ID as a number).
        if _is_id_name(name) and int(s.nunique()) >= max(10, 0.9 * n_rows):
            continue
        rank = _hint_rank(name, REGRESSION_HINTS)
        cv = float(s.std(ddof=1) / (abs(s.mean()) if s.mean() else 1.0))
        cands.append((rank, -cv, -int(s.nunique()), name))
    cands.sort(key=lambda x: (x[0], x[1], x[2]))
    return [c[3] for c in cands[:4]]


def classification_targets(df: pd.DataFrame, types: Dict[str, str]) -> List[str]:
    """Choose categorical/boolean targets suitable for a Decision Tree.
    Only meaningful targets (hint-matched or boolean flags) are considered."""
    cands: List[Tuple[int, int, str]] = []
    for name, t in types.items():
        if t not in ("categorical", "boolean"):
            continue
        if _is_id_name(name):
            continue
        s = df[name].dropna()
        classes = int(s.nunique(dropna=True))
        if classes < 2 or classes > MAX_CATEGORY_CARDS:
            continue
        counts = s.value_counts()
        if len(counts) < 2 or int(counts.min()) < 2:
            continue
        rank = _hint_rank(name, CLASSIFICATION_HINT)
        # Only meaningful targets: a hint match, or a boolean flag (is_* / yes-no).
        if rank == 10 ** 6 and t != "boolean":
            continue
        cands.append((rank, -classes, name))
    cands.sort(key=lambda x: (x[0], x[1]))
    return [c[2] for c in cands[:2]]


# ---------------------------------------------------------------------------
# Feature construction (never leaks the target as a feature)
# ---------------------------------------------------------------------------
def _build_matrix(df: pd.DataFrame, types: Dict[str, str],
                  target: str) -> pd.DataFrame:
    """Numeric feature matrix for a given target (target itself removed)."""
    cols: Dict[str, pd.Series] = {}
    for name in df.columns.astype(str):
        if name == target:
            continue
        t = types.get(name, "categorical")
        try:
            series = df[name]
        except Exception:
            continue
        try:
            if t == "numeric":
                s = pd.to_numeric(series, errors="coerce")
                if s.notna().sum() >= 6 and s.nunique(dropna=True) >= 2:
                    cols[name] = s.fillna(float(s.median()))
            elif t == "boolean":
                s = series.astype(str).str.strip().str.lower().map(
                    {"true": 1.0, "yes": 1.0, "y": 1.0, "1": 1.0,
                     "false": 0.0, "no": 0.0, "n": 0.0, "0": 0.0})
                m = s.mode()
                cols[name] = s.fillna(float(m.iloc[0] if len(m) else 0))
            elif t == "categorical":
                if int(series.nunique(dropna=True)) > MAX_CATEGORY_CARDS:
                    continue
                filled = series.fillna("(missing)").astype(str)
                uniq = sorted(filled.unique().tolist())
                cols[name] = np.array([uniq.index(v) for v in filled], dtype=float)
            elif t == "date":
                parsed = pd.to_datetime(
                    series, errors="coerce", format="mixed")
                valid = parsed.dropna()
                if len(valid) >= 6:
                    base = valid.min()
                    cols[name + "_days"] = (parsed - base).dt.days.fillna(0.0).astype(float)
                    cols[name + "_year"] = parsed.dt.year.fillna(
                        float(np.median(parsed.dt.year.dropna()))).astype(float)
                    cols[name + "_month"] = parsed.dt.month.fillna(0.0).astype(float)
        except Exception:
            continue
    if not cols:
        return pd.DataFrame()
    try:
        X = pd.DataFrame(cols, index=df.index)
        X = X.loc[:, X.nunique(dropna=True) > 1]
        X = X.replace([np.inf, -np.inf], np.nan)
        X = X.fillna(X.median(numeric_only=True))
    except Exception:
        return pd.DataFrame()
    return X


def _representative_row(X: pd.DataFrame) -> Dict[str, float]:
    """A defensible input row: medians, but advancing any date feature by one
    typical step so the prediction represents the next similar period."""
    rep: Dict[str, float] = {}
    if X is None or X.empty:
        return rep
    for col in X.columns:
        vals = pd.to_numeric(X[col], errors="coerce").dropna()
        if vals.empty:
            rep[col] = 0.0
            continue
        if col.endswith("_days") and len(vals) >= 2:
            arr = np.sort(vals.to_numpy())
            step = float(np.median(np.diff(arr))) if len(arr) > 1 else 1.0
            rep[col] = float(arr[-1]) + (step if step > 0 else 1.0)
        else:
            rep[col] = float(vals.median())
    return rep


def _fmt_number(value: Any, decimals: int = 2) -> str:
    try:
        v = float(value)
        if v != v or v in (float("inf"), float("-inf")):
            return "n/a"
        if abs(v) >= 1e6:
            return f"{v:,.0f}"
        if abs(v - round(v)) < 1e-9:
            return f"{int(round(v)):,}"
        return f"{v:,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)
def _split(X: pd.DataFrame, y) -> Tuple:
    try:
        if len(X) < 12:
            return None, None, None, None
        test_size = 0.2 if len(X) >= 20 else 0.3
        n_test = max(2, int(round(len(X) * test_size)))
        test_size = n_test / len(X)
        stratify = None
        if y.dtype == object:
            try:
                stratify = y
            except Exception:
                stratify = None
        return train_test_split(X, y, test_size=test_size,
                                random_state=RANDOM_STATE, stratify=stratify)
    except Exception:
        try:
            return train_test_split(X, y, test_size=0.2, random_state=RANDOM_STATE)
        except Exception:
            return None, None, None, None


def train_regression(df: pd.DataFrame, target: str,
                     types: Dict[str, str]) -> Dict[str, Any]:
    """Linear Regression for a numeric target.  Returns a JSON-safe dict and
    never raises.  When unsuitable it returns a friendly message instead of an
    invented value."""
    base: Dict[str, Any] = {"target": target, "kind": "regression"}
    date_col = _date_col(types)
    if date_col:
        try:
            dates = pd.to_datetime(df[date_col], errors="coerce", format="mixed").dropna().sort_values()
            step = float(dates.diff().dt.total_seconds().div(86400).dropna().median())
            base["forecast_step_days"] = step if step > 0 else 1.0
            base["forecast_frequency"] = "monthly" if step >= 25 else "weekly" if step >= 6 else "daily"
        except Exception:
            pass
    try:
        X = _build_matrix(df, types, target)
        y = pd.to_numeric(df[target], errors="coerce").dropna()
        if len(y) < MIN_REGRESSION_ROWS:
            base["error"] = "Not enough suitable data for this prediction."
            return base
        merged = pd.concat([X, y.astype(float)], axis=1, join="inner")
        if len(merged) < MIN_REGRESSION_ROWS:
            base["error"] = "Not enough suitable data for this prediction."
            return base
        y = merged.iloc[:, -1].astype(float)
        X = merged.iloc[:, :-1].astype(float)
        if X.shape[1] == 0:
            base["error"] = "Not enough suitable features for this prediction."
            return base
        X_train, X_test, y_train, y_test = _split(X, y)
        if X_train is None or len(X_train) < 6:
            base["error"] = "Not enough suitable data for this prediction."
            return base
        model = LinearRegression()
        model.fit(X_train, y_train)
    except Exception:
        base["error"] = "Prediction could not be generated from this dataset."
        return base

    metrics: Dict[str, Any] = {}
    if X_test is not None and len(X_test) > 0:
        try:
            pred = model.predict(X_test)
            metrics["rmse"] = float(np.sqrt(mean_squared_error(y_test, pred)))
            metrics["mae"] = float(mean_absolute_error(y_test, pred))
            metrics["r2"] = float(r2_score(y_test, pred))
        except Exception:
            pass
    rep_row = _representative_row(X)
    predicted = None
    if rep_row:
        try:
            predicted = float(model.predict(
                pd.DataFrame([rep_row], columns=X.columns))[0])
        except Exception:
            predicted = None

    base.update({
        "model_name": "Linear Regression",
        "metrics": metrics,
        "metric_name": "R²",
        "metric_value": metrics.get("r2"),
        "prediction_value": predicted,
        "prediction_label": _fmt_number(predicted),
        "trained_rows": int(len(y_train)),
        "interpretation": _regression_note(metrics, y_train, predicted),
        "_model": model,
        "feature_names": list(X.columns),
        "rep_row": rep_row,
    })
    return base
def _regression_note(metrics: Dict[str, Any], y_train: pd.Series,
                     predicted: Any) -> str:
    r2 = metrics.get("r2")
    if r2 is None:
        return "The model is trained, but evaluation was limited by the small dataset."
    if r2 >= 0.6:
        note = "The model tracks the data well."
    elif r2 >= 0.3:
        note = "The model captures a meaningful part of the trend."
    else:
        note = "The link is weak, so treat this as an early indicator only."
    mean = float(y_train.mean()) if len(y_train) else None
    if predicted is not None and mean:
        if predicted > mean * 1.1:
            return note + " The indicator points above the typical level."
        if predicted < mean * 0.9:
            return note + " The indicator points below the typical level."
    return note


def _class_feature_matrix(df: pd.DataFrame,
                          types: Dict[str, str]) -> pd.DataFrame:
    """Encoded numeric features for classification (label-encoded categoricals,
    numeric, boolean, and date-derived features)."""
    return _build_matrix(df, types, target="__none__")


def train_classifier(df: pd.DataFrame, target: str,
                     types: Dict[str, str]) -> Dict[str, Any]:
    """Decision Tree Classifier for a categorical/bool target. Never raises."""
    base: Dict[str, Any] = {"target": target, "kind": "classification"}
    try:
        X = _class_feature_matrix(df, types)
        y = df[target].dropna().astype(str)
        if len(y) < MIN_ROWS:
            base["error"] = "Not enough suitable data for this classification."
            return base
        if y.nunique() < 2:
            base["error"] = "Not enough classes in the target for classification."
            return base
        classes_sorted = sorted(y.unique().tolist())
        y_codes = np.array([classes_sorted.index(v) for v in y], dtype=int)
        X = X.loc[y.index]
        X = X.loc[:, X.nunique(dropna=True) > 1]
        X = X.replace([np.inf, -np.inf], np.nan).fillna(
            X.median(numeric_only=True))
        if X.shape[1] == 0:
            base["error"] = "Not enough suitable features for this classification."
            return base
        X_train, X_test, y_train, y_test = _split(X, y_codes)
        if X_train is None or len(X_train) < 6 or len(np.unique(y_train)) < 2:
            base["error"] = "Not enough suitable data for this classification."
            return base
        model = DecisionTreeClassifier(max_depth=5, min_samples_leaf=2,
                                       random_state=RANDOM_STATE)
        model.fit(X_train, y_train)
    except Exception:
        base["error"] = "Prediction could not be generated from this dataset."
        return base

    metrics: Dict[str, Any] = {}
    if X_test is not None and len(X_test) > 0:
        try:
            acc = accuracy_score(y_test, model.predict(X_test))
            metrics["accuracy"] = float(acc)
        except Exception:
            metrics["accuracy"] = None

    rep_row = _representative_row(X)
    predicted_label = None
    if rep_row:
        try:
            i = int(model.predict(
                pd.DataFrame([rep_row], columns=X.columns))[0])
            predicted_label = classes_sorted[i]
        except Exception:
            predicted_label = None

    base.update({
        "model_name": "Decision Tree Classifier",
        "metrics": metrics,
        "metric_name": "Accuracy",
        "metric_value": metrics.get("accuracy"),
        "prediction_value": predicted_label,
        "prediction_label": str(predicted_label) if predicted_label is not None else "n/a",
        "trained_rows": int(len(y_train)),
        "interpretation": _classification_note(metrics, y, predicted_label),
        "_model": model,
        "feature_names": list(X.columns),
        "rep_row": rep_row,
        "classes": classes_sorted,
    })
    return base
def _classification_note(metrics: Dict[str, Any], y: pd.Series,
                         predicted_label: Any) -> str:
    acc = metrics.get("accuracy")
    dist = y.value_counts(normalize=True).to_dict() if len(y) else {}
    majority = max(dist, key=dist.get) if dist else None
    if acc is None:
        base_txt = "The classifier was trained, but evaluation was limited by data size."
    elif acc >= 0.7:
        base_txt = "The classifier is accurate on this dataset."
    elif acc >= 0.5:
        base_txt = "The classifier shows a useful signal but has room to improve."
    else:
        base_txt = "The classifier is only slightly better than guessing; use with care."
    if predicted_label is not None:
        if majority is not None and str(predicted_label) == str(majority):
            return base_txt + f" The predicted class \"{predicted_label}\" is also the most common one."
        return base_txt + f" The predicted class is \"{predicted_label}\"."
    return base_txt


# ---------------------------------------------------------------------------
# Trend analysis (only when a usable date column exists)
# ---------------------------------------------------------------------------
def _date_col(types: Dict[str, str]) -> Optional[str]:
    for name, t in types.items():
        if t == "date":
            return name
    return None


def trend_analysis(df: pd.DataFrame, types: Dict[str, str],
                   numeric_cols: List[str]) -> List[Dict[str, Any]]:
    """Compare the first half vs the second half of the time series.  Returns
    increase / decrease / stable flags.  Empty when there is no date column."""
    date = _date_col(types)
    if date is None:
        return []
    try:
        work = df.copy()
        work[date] = pd.to_datetime(work[date], errors="coerce", format="mixed")
        work = work.dropna(subset=[date]).sort_values(date)
    except Exception:
        return []
    if len(work) < 6:
        return []
    trends: List[Dict[str, Any]] = []
    for col in numeric_cols:
        s = pd.to_numeric(work[col], errors="coerce")
        s = s[s.notna()]
        if len(s) < 6:
            continue
        half = len(s) // 2
        first, second = s.iloc[:half].mean(), s.iloc[half:].mean()
        if first == 0:
            continue
        pct = float((second - first) / abs(first))
        if pct > 0.05:
            direction = "increasing"
        elif pct < -0.05:
            direction = "decreasing"
        else:
            direction = "stable"
        trends.append({
            "metric": col,
            "direction": direction,
            "first_half": float(first),
            "second_half": float(second),
            "change_pct": round(pct * 100, 1),
            "message": _trend_message(col, direction, pct),
        })
    return trends


def _trend_message(metric: str, direction: str, pct: float) -> str:
    if direction == "increasing":
        return f"{metric} shows an upward trend, indicating growth."
    if direction == "decreasing":
        return f"{metric} shows a downward trend and should be monitored closely."
    return f"{metric} is stable."
# ---------------------------------------------------------------------------
# Business suggestion generation (based on actual data/model results only)
# ---------------------------------------------------------------------------
def _metric_match(name: str, words) -> bool:
    low = name.strip().lower()
    return any(w in low for w in words)


def build_insight(sections: List[Dict[str, Any]], trends: List[Dict[str, Any]],
                  types: Dict[str, str]) -> str:
    """A short business-growth paragraph grounded in the analysis. Never
    invents facts that the data does not support."""
    parts: List[str] = []

    # 1) Trend-based facts.
    increasing = [t for t in trends if t["direction"] == "increasing"]
    decreasing = [t for t in trends if t["direction"] == "decreasing"]
    if increasing:
        names = ", ".join(t["metric"] for t in increasing[:2])
        parts.append(f"{names.capitalize()} show an upward trend, indicating positive growth.")
    if decreasing:
        names = ", ".join(t["metric"] for t in decreasing[:2])
        parts.append(f"{names.capitalize()} show a downward trend and should be monitored closely.")

    rev_t = next((t for t in trends if _metric_match(
        t["metric"], ("revenue", "sales", "income"))), None)
    exp_t = next((t for t in trends if _metric_match(
        t["metric"], ("expense", "cost"))), None)
    if rev_t and exp_t:
        if exp_t["direction"] == "increasing" and rev_t["direction"] != "increasing":
            parts.append("Expenses are growing faster than revenue, which may reduce profitability. Cost control should be prioritized.")
        elif rev_t["direction"] == "increasing" and exp_t["direction"] in ("stable", "decreasing"):
            parts.append("Revenue is growing while expenses are under control, improving efficiency.")

    # 2) Prediction-based facts.
    classification = [s for s in sections if s.get("kind") == "classification"
                      and "error" not in s]
    regression = [s for s in sections if s.get("kind") == "regression"
                  and "error" not in s and s.get("prediction_value") is not None]
    if regression:
        strong = [s for s in regression if (s.get("metric_value") or 0) >= 0.3]
        if strong:
            parts.append("The model points to the main business indicators keeping their current trajectory, so investing in the strongest-performing areas makes sense.")
        else:
            parts.append("Prediction quality is limited by the current data; improving data collection would make forecasts more reliable.")

    if classification:
        pred = classification[0].get("prediction_label")
        low_t = classification[0].get("target", "").lower()
        if pred and _metric_match(low_t, ("risk",)):
            plow = str(pred).lower()
            if plow.startswith("low"):
                parts.append("The current risk outlook is low. Maintaining current practices keeps exposure manageable.")
            elif plow.startswith("high"):
                parts.append("At-risk customers or periods should receive closer monitoring. Improving retention and reducing negative indicators could lower future risk.")
            else:
                parts.append("The risk prediction should be used to prioritise monitoring of higher-risk segments.")
        elif pred:
            parts.append(f"The most relevant outcome is \"{pred}\". Focusing on the drivers of this outcome can support growth.")

    if parts:
        return " ".join(parts)
    if sections:
        return "The dataset does not yet contain enough signal for business suggestions. Upload more complete data to unlock deeper insights."
    return ""
# ---------------------------------------------------------------------------
# Orchestrator — run everything
# ---------------------------------------------------------------------------
def auto_analyze(df: pd.DataFrame) -> Dict[str, Any]:
    """Universal entry point.

    Returns a dict with:
      - overview  : rows/cols numbers and the column-type profile
      - sections  : prediction sections (regression + classification), each
                    JSON-safe and each optionally carrying a "_model" for live
                    on-demand predictions
      - trends    : growth / decline / stable analysis
      - insight   : short auto-generated business-growth suggestion
      - has_predictions / message
    Never raises.
    """
    result: Dict[str, Any] = {
        "ok": True,
        "rows": 0,
        "columns": 0,
        "types": {},
        "sections": [],
        "trends": [],
        "insight": "",
        "has_predictions": False,
        "message": "",
    }
    clean = _clean_copy(df)
    if clean is None or clean.empty:
        result["ok"] = False
        result["message"] = "Not enough suitable data for analysis."
        return result

    if len(clean) > MOST_ROWS:
        try:
            clean = clean.sample(n=MOST_ROWS, random_state=RANDOM_STATE)
        except Exception:
            pass

    try:
        types = detect_types(clean)
    except Exception:
        result["ok"] = False
        result["message"] = "Not enough suitable data for analysis."
        return result
    numeric_cols = [c for c, t in types.items() if t == "numeric"]

    sections: List[Dict[str, Any]] = []
    for target in regression_targets(clean, types):
        try:
            sections.append(train_regression(clean, target, types))
        except Exception:
            sections.append({"target": target, "kind": "regression",
                             "error": "Prediction could not be generated from this dataset."})
    for target in classification_targets(clean, types):
        try:
            sections.append(train_classifier(clean, target, types))
        except Exception:
            sections.append({"target": target, "kind": "classification",
                             "error": "Prediction could not be generated from this dataset."})

    trends: List[Dict[str, Any]] = []
    try:
        trends = trend_analysis(clean, types, numeric_cols)
    except Exception:
        trends = []
    insight: str = ""
    try:
        insight = build_insight(sections, trends, types)
    except Exception:
        insight = ""

    result.update({
        "ok": True,
        "rows": int(len(clean)),
        "columns": int(len(clean.columns)),
        "types": {c: types.get(c, "unknown") for c in clean.columns.astype(str)},
        "sections": sections,
        "trends": trends,
        "insight": insight,
        "has_predictions": any(s.get("error") is None for s in sections),
        "message": "",
    })
    return result


def predict_dynamic(section: Dict[str, Any]) -> Dict[str, Any]:
    """Re-run a stored model on its representative row for a live prediction.
    Returns JSON-safe output (never invents values)."""
    out: Dict[str, Any] = {"ok": False}
    try:
        model = section.get("_model")
        rep_row = section.get("rep_row")
        if model is None or not rep_row:
            out["error"] = "Prediction could not be generated for this target."
            return out
        frame = pd.DataFrame([rep_row], columns=section.get("feature_names") or [])
        if section.get("kind") == "regression":
            val = float(model.predict(frame)[0])
            out.update({"ok": True, "value": val, "label": _fmt_number(val),
                        "type": "regression"})
        else:
            idx = int(model.predict(frame)[0])
            classes = section.get("classes") or []
            label = classes[idx] if idx < len(classes) else str(idx)
            out.update({"ok": True, "value": label, "label": str(label),
                        "type": "classification"})
    except Exception:
        out["error"] = "Prediction could not be generated for this target."
    return out


def predict_dynamic_periods(section: Dict[str, Any], periods: int) -> Dict[str, Any]:
    """Generate successive forecasts using the detected date cadence."""
    periods = max(1, min(int(periods or 1), 24))
    if section.get("kind") != "regression":
        return predict_dynamic(section)
    try:
        model, base = section.get("_model"), dict(section.get("rep_row") or {})
        if model is None or not base:
            return {"ok": False, "error": "Prediction could not be generated for this target."}
        step = float(section.get("forecast_step_days") or 1)
        date_features = [name for name in base if name.endswith("_days")]
        values = []
        for index in range(periods):
            row = dict(base)
            for name in date_features:
                row[name] = float(base[name]) + (step * index)
            values.append(float(model.predict(pd.DataFrame([row], columns=section.get("feature_names") or []))[0]))
        return {"ok": True, "type": "regression", "values": values,
                "value": values[-1], "label": _fmt_number(values[-1]),
                "frequency": section.get("forecast_frequency", "period")}
    except Exception:
        return {"ok": False, "error": "Prediction could not be generated for this target."}


def strip_model_keys(section: Dict[str, Any]) -> Dict[str, Any]:
    """Remove non-JSON model objects before sending a section to the UI."""
    return {k: v for k, v in section.items() if not k.startswith("_")}
