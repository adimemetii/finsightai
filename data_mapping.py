"""Business-oriented column detection and upload cleaning for FinSight AI.

The web layer deliberately keeps persistence and authentication concerns out of
this module.  All functions operate on in-memory dataframes and return plain
Python values so they can be used by both the preview and final upload flows.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


FIELD_SPECS: dict[str, dict[str, Any]] = {
    "tx_date": {
        "label": "Date",
        "kind": "date",
        "priority": "required",
        "aliases": [
            "date", "transaction_date", "sale_date", "invoice_date",
            "order_date", "created_at", "created_date", "timestamp", "period",
        ],
    },
    "revenue": {
        "label": "Revenue",
        "kind": "numeric",
        "priority": "required",
        "aliases": [
            "revenue", "sales", "sales_amount", "total_sales", "income",
            "turnover", "gross_sales", "sale_amount", "total_revenue",
            "net_sales", "total_income", "billing", "billings", "billed_amount",
        ],
    },
    "expenses": {
        "label": "Expenses",
        "kind": "numeric",
        "priority": "recommended",
        "aliases": [
            "expenses", "expense", "cost", "costs", "total_expenses",
            "operating_expenses", "spending", "expenditure", "cogs",
            "cost_of_goods_sold", "operating_costs",
        ],
    },
    "profit": {
        "label": "Profit",
        "kind": "numeric",
        "priority": "recommended",
        "aliases": [
            "profit", "net_profit", "gross_profit", "earnings", "net_income",
            "operating_profit", "profit_loss", "pnl",
        ],
    },
    "customers": {
        "label": "Customers",
        "kind": "numeric",
        "priority": "recommended",
        "aliases": [
            "customers", "customer", "customer_count", "clients",
            "number_of_customers", "number_customers", "client_count",
            "customers_count", "customer_number",
        ],
    },
    "marketing_spend": {
        "label": "Marketing spend",
        "kind": "numeric",
        "priority": "optional",
        "aliases": [
            "marketing_spend", "marketing", "advertising", "ad_spend",
            "advertising_spend", "promotion_spend", "marketing_cost",
        ],
    },
    "amount": {
        "label": "Amount",
        "kind": "numeric",
        "priority": "optional",
        "aliases": [
            "amount", "transaction_amount", "order_amount", "total_amount",
            "value", "total_value", "price", "order_value",
        ],
    },
    "transaction_id": {
        "label": "Transaction ID",
        "kind": "text",
        "priority": "optional",
        "aliases": ["transaction_id", "tx_id", "invoice_id", "order_id", "id"],
    },
    "description": {
        "label": "Description",
        "kind": "text",
        "priority": "optional",
        "aliases": ["description", "desc", "details", "memo", "notes"],
    },
    "category": {
        "label": "Category",
        "kind": "categorical",
        "priority": "optional",
        "aliases": ["category", "product_category", "service_category", "segment"],
    },
    "tx_type": {
        "label": "Transaction type",
        "kind": "categorical",
        "priority": "optional",
        "aliases": ["type", "transaction_type", "tx_type"],
    },
    "department": {
        "label": "Department",
        "kind": "categorical",
        "priority": "optional",
        "aliases": ["department", "business_unit", "division", "team"],
    },
    "payment_method": {
        "label": "Payment method",
        "kind": "categorical",
        "priority": "optional",
        "aliases": ["payment_method", "payment", "payment_type", "method"],
    },
    "city": {
        "label": "City",
        "kind": "categorical",
        "priority": "optional",
        "aliases": ["city", "location", "town", "municipality"],
    },
    "status": {
        "label": "Status",
        "kind": "categorical",
        "priority": "optional",
        "aliases": ["status", "order_status", "transaction_status", "state"],
    },
}

CANONICAL_NUMERIC_FIELDS = (
    "amount", "revenue", "expenses", "profit", "customers", "marketing_spend"
)
CANONICAL_TEXT_FIELDS = (
    "transaction_id", "description", "tx_type", "category", "department",
    "payment_method", "city", "status"
)


def normalize_column_name(value: Any) -> str:
    """Normalize a human column label to a stable snake_case identifier."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "column"


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        count = seen.get(name, 0)
        result.append(name if count == 0 else f"{name}_{count + 1}")
        seen[name] = count + 1
    return result


def _as_text(series: pd.Series) -> pd.Series:
    return series.dropna().astype(str).str.strip()


def _numeric_ratio(series: pd.Series) -> float:
    sample = _as_text(series)
    if sample.empty:
        return 0.0
    if series.dtype.kind in "biufc":
        return 1.0
    cleaned = sample.str.replace(r"[^0-9+\-.()]+", "", regex=True)
    return float(pd.to_numeric(cleaned, errors="coerce").notna().mean())


def _date_ratio(series: pd.Series) -> float:
    sample = _as_text(series)
    sample = sample[sample != ""]
    if len(sample) < 2:
        return 0.0
    try:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        return 0.0
    return float(parsed.notna().mean())


def _value_type(series: pd.Series) -> str:
    if series.dropna().empty:
        return "empty"
    if _numeric_ratio(series) >= 0.85:
        return "numeric"
    if _date_ratio(series) >= 0.85:
        return "date"
    unique = int(series.nunique(dropna=True))
    if unique <= 1:
        return "constant"
    if unique >= max(20, int(len(series.dropna()) * 0.85)):
        return "text"
    return "categorical"


def _alias_score(normalized: str, aliases: list[str]) -> float:
    if normalized in aliases:
        # Earlier aliases are more specific, but all exact aliases are high
        # confidence because they are intentionally curated.
        return 120.0 - aliases.index(normalized)
    tokens = set(normalized.split("_"))
    best = 0.0
    for alias in aliases:
        alias_tokens = set(alias.split("_"))
        if not alias_tokens:
            continue
        overlap = len(tokens & alias_tokens) / len(alias_tokens)
        if overlap >= 0.5:
            best = max(best, 65.0 * overlap)
    return best


def detect_columns(df: pd.DataFrame) -> dict[str, Any]:
    """Return type information, candidates, and conservative auto-mappings.

    Type-only matches are never auto-assigned.  A source column needs a strong
    semantic name match before it is mapped, preventing a random numeric field
    from being silently treated as revenue or expenses.
    """
    if df is None:
        return {"columns": [], "fields": {}, "mapping": {}, "warnings": []}

    raw_names = [str(column).strip() for column in df.columns]
    normalized_names = _unique_names([normalize_column_name(name) for name in raw_names])
    column_info: list[dict[str, Any]] = []
    for index, raw_name in enumerate(raw_names):
        series = df.iloc[:, index]
        column_info.append({
            "source": raw_name,
            "normalized": normalized_names[index],
            "type": _value_type(series),
            "non_empty": int(series.notna().sum()),
        })

    candidates_by_field: dict[str, list[dict[str, Any]]] = {}
    for field, spec in FIELD_SPECS.items():
        candidates: list[dict[str, Any]] = []
        for info in column_info:
            score = _alias_score(info["normalized"], spec["aliases"])
            if spec["kind"] == "date":
                score += 22.0 * _date_ratio(df.iloc[:, column_info.index(info)])
                if info["type"] == "numeric":
                    score -= 20.0
            elif spec["kind"] == "numeric":
                score += 22.0 * _numeric_ratio(df.iloc[:, column_info.index(info)])
                if info["type"] == "date":
                    score -= 40.0
            elif spec["kind"] == "categorical" and info["type"] in {"categorical", "text"}:
                score += 10.0
            elif spec["kind"] == "text" and info["type"] in {"categorical", "text"}:
                score += 8.0
            if score > 0:
                confidence = "high" if score >= 105 else "medium" if score >= 75 else "low"
                candidates.append({
                    "source": info["source"],
                    "normalized": info["normalized"],
                    "score": round(score, 1),
                    "confidence": confidence,
                })
        candidates.sort(key=lambda item: (-item["score"], item["source"]))
        candidates_by_field[field] = candidates[:5]

    # Assign each source at most once.  Exact semantic matches win over fuzzy
    # matches, with required/recommended fields considered first.
    field_order = sorted(
        FIELD_SPECS,
        key=lambda field: (0 if FIELD_SPECS[field]["priority"] == "required" else
                           1 if FIELD_SPECS[field]["priority"] == "recommended" else 2,
                           -candidates_by_field[field][0]["score"]
                           if candidates_by_field[field] else 0),
    )
    used_sources: set[str] = set()
    fields: dict[str, dict[str, Any]] = {}
    mapping: dict[str, str] = {}
    warnings: list[str] = []
    for field in field_order:
        candidates = candidates_by_field[field]
        selected = None
        if candidates:
            top = candidates[0]
            second_score = candidates[1]["score"] if len(candidates) > 1 else 0
            # Strong exact aliases are safe even if another field has a similar
            # fuzzy candidate.  Lower-confidence results require a clear margin.
            is_safe = top["score"] >= 105 and (top["score"] - second_score >= 3 or len(candidates) == 1)
            is_safe = is_safe or (top["score"] >= 88 and top["score"] - second_score >= 12)
            if is_safe and top["source"] not in used_sources:
                selected = top
        if selected:
            mapping[field] = selected["source"]
            used_sources.add(selected["source"])
        fields[field] = {
            "label": FIELD_SPECS[field]["label"],
            "priority": FIELD_SPECS[field]["priority"],
            "kind": FIELD_SPECS[field]["kind"],
            "source": selected["source"] if selected else None,
            "confidence": selected["confidence"] if selected else "missing",
            "candidates": candidates,
        }
        if not selected and FIELD_SPECS[field]["priority"] in {"required", "recommended"}:
            warnings.append(f"{FIELD_SPECS[field]['label']} was not confidently detected.")

    return {
        "columns": column_info,
        "fields": fields,
        "mapping": mapping,
        "warnings": warnings,
    }


def apply_mapping(df: pd.DataFrame, mapping: dict[str, str] | None = None) -> pd.DataFrame:
    """Rename mapped fields to canonical names and normalize all other labels."""
    if df is None:
        return pd.DataFrame()
    work = df.copy()
    raw_names = [str(column).strip() for column in work.columns]
    work.columns = raw_names
    mapping = mapping or {}
    unknown_fields = set(mapping) - set(FIELD_SPECS)
    if unknown_fields:
        raise ValueError(f"Unknown mapped field(s): {sorted(unknown_fields)}")
    valid_mapping: dict[str, str] = {}
    seen_sources: set[str] = set()
    for field in FIELD_SPECS:
        source = mapping.get(field)
        if not source:
            continue
        source = str(source).strip()
        if source not in raw_names:
            raise ValueError(f"Mapped source column '{source}' does not exist.")
        if source in seen_sources:
            raise ValueError(f"Source column '{source}' was mapped more than once.")
        seen_sources.add(source)
        valid_mapping[field] = source

    rename: dict[str, str] = {}
    for source in raw_names:
        field = next((candidate for candidate, selected in valid_mapping.items()
                      if selected == source), None)
        rename[source] = field or normalize_column_name(source)

    # Avoid collisions between unmapped labels and canonical fields.
    new_names = _unique_names([rename[source] for source in raw_names])
    work.columns = new_names
    return work


def _numeric_series(series: pd.Series) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float)
    text = series.astype("string").str.strip()
    # Support common currency symbols and accounting negatives without adding
    # a locale-specific parser dependency.
    negative = text.str.startswith("(") & text.str.endswith(")")
    text = text.str.replace(r"[\$€£¥]", "", regex=True)
    text = text.str.replace(",", "", regex=False).str.replace("(", "", regex=False).str.replace(")", "", regex=False)
    values = pd.to_numeric(text, errors="coerce")
    return values.where(~negative, -values.abs())


def clean_dataframe(df: pd.DataFrame, mapping: dict[str, str] | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Clean an upload and return the cleaned frame plus factual metrics."""
    if df is None or df.empty:
        raise ValueError("File is empty or has no data.")

    original_rows = int(len(df))
    work = apply_mapping(df, mapping)
    work = work.dropna(how="all").copy()
    blank_rows_removed = original_rows - int(len(work))

    numeric_invalid = 0
    for column in CANONICAL_NUMERIC_FIELDS:
        if column in work.columns:
            before = work[column].notna()
            converted = _numeric_series(work[column])
            numeric_invalid += int((before & converted.isna()).sum())
            work[column] = converted

    invalid_dates = 0
    if "tx_date" in work.columns:
        raw_date = work["tx_date"]
        non_empty = raw_date.notna() & raw_date.astype("string").str.strip().ne("")
        parsed = pd.to_datetime(raw_date, errors="coerce", format="mixed")
        invalid_dates = int((non_empty & parsed.isna()).sum())
        work["tx_date"] = parsed

    derived_profit = False
    if {"revenue", "expenses"}.issubset(work.columns):
        if "profit" not in work.columns:
            work["profit"] = np.nan
        if work["profit"].isna().all():
            available = work["revenue"].notna() | work["expenses"].notna()
            work.loc[available, "profit"] = (
                work.loc[available, "revenue"].fillna(0)
                - work.loc[available, "expenses"].fillna(0)
            )
            derived_profit = bool(available.any())

    # Compare normalized values so formatting differences such as "$1,200"
    # versus "1200" do not allow duplicate business rows through.
    before_duplicates = int(len(work))
    work = work.drop_duplicates().copy()
    duplicates_removed = before_duplicates - int(len(work))

    missing_values = int(work.isna().sum().sum())
    if work.empty:
        raise ValueError("No usable rows remain after cleaning.")

    summary = {
        "original_rows": original_rows,
        "rows_after_cleaning": int(len(work)),
        "columns_after_cleaning": int(len(work.columns)),
        "blank_rows_removed": blank_rows_removed,
        "duplicates_removed": duplicates_removed,
        "invalid_dates": invalid_dates,
        "invalid_numeric_values": numeric_invalid,
        "missing_values_remaining": missing_values,
        "derived_profit": derived_profit,
    }
    return work, summary


def json_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    """Convert a dataframe row into a JSON-safe record for durable storage."""
    result: dict[str, Any] = {}
    for key, value in record.items():
        if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
            result[str(key)] = None
        elif isinstance(value, (pd.Timestamp, np.datetime64, date, datetime)):
            result[str(key)] = pd.Timestamp(value).isoformat()
        elif isinstance(value, (np.integer, np.floating, np.bool_)):
            result[str(key)] = value.item()
        else:
            result[str(key)] = value
    return result


def profile_dataframe(df: pd.DataFrame, mapping: dict[str, str] | None = None) -> dict[str, Any]:
    """Build a preview profile without persisting anything."""
    detection = detect_columns(df)
    selected_mapping = mapping if mapping is not None else detection["mapping"]
    cleaned, cleaning = clean_dataframe(df, selected_mapping)
    preview = []
    for record in cleaned.head(8).to_dict(orient="records"):
        preview.append(json_safe_record(record))
    return {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "column_names": [str(column) for column in df.columns],
        "detected": detection,
        "mapping": selected_mapping,
        "cleaning": cleaning,
        "preview": preview,
        "warnings": detection["warnings"],
    }


def profile_json_payload(payload: Any) -> pd.DataFrame:
    """Read common JSON business-data shapes into a dataframe."""
    if isinstance(payload, list):
        data = payload
    elif isinstance(payload, dict):
        data = payload.get("data", payload.get("records", payload))
    else:
        raise ValueError("JSON must contain an array of records or an object.")
    if isinstance(data, list):
        frame = pd.json_normalize(data)
    elif isinstance(data, dict):
        try:
            frame = pd.DataFrame(data)
        except ValueError:
            frame = pd.DataFrame([data])
    else:
        raise ValueError("JSON data must be an array of records or an object of columns.")
    if frame.empty or len(frame.columns) == 0:
        raise ValueError("JSON is empty or does not contain tabular data.")
    return frame


def profile_to_json(profile: dict[str, Any]) -> str:
    return json.dumps(profile, ensure_ascii=False, default=str)
