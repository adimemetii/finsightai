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
        # A date is useful for time-series analysis, but uploads are not
        # restricted to time-series data.
        "priority": "recommended",
        "aliases": [
            "date", "transaction_date", "sale_date", "invoice_date",
            "order_date", "created_at", "created_date", "timestamp", "time", "period",
            "datetime", "date_time", "transaction_datetime", "order_datetime",
            "year", "fiscal_year", "month",
        ],
    },
    "revenue": {
        "label": "Revenue",
        "kind": "numeric",
        # Revenue is a helpful business alias, not a schema requirement.
        "priority": "recommended",
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


def _date_ratio(series: pd.Series, name: Any = None) -> float:
    sample = _as_text(series)
    sample = sample[sample != ""]
    if len(sample) < 2:
        return 0.0
    normalized = normalize_column_name(name) if name is not None else ""
    # Year and month are useful period columns even when spreadsheets store
    # them as numbers. They are only converted into a full date when the
    # cleaner has enough information to do so (see clean_dataframe).
    numeric = pd.to_numeric(sample, errors="coerce")
    if normalized in {"year", "fiscal_year"}:
        return float(numeric.between(1900, 2100).mean())
    if normalized in {"month", "month_number"}:
        if numeric.notna().any():
            return float(numeric.between(1, 12).mean())
        if not sample.str.contains(r"\b(?:19|20)\d{2}\b", regex=True).any():
            return 0.0
        return float(_parse_dates(sample).notna().mean())
    parsed = _parse_dates(sample)
    return float(parsed.notna().mean())


def _unique_count(series: pd.Series) -> int:
    """Count distinct values without rejecting list/dict JSON fields."""
    try:
        return int(series.nunique(dropna=True))
    except (TypeError, ValueError):
        return int(series.astype("string").nunique(dropna=True))


def _parse_dates(series: pd.Series, column_name: Any = None) -> pd.Series:
    """Parse text, timestamps, and common Excel serial dates safely."""
    if series is None:
        return pd.Series(dtype="datetime64[ns]")
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    def assign_valid(parsed: pd.Series) -> None:
        """Assign only representable timestamps; bad values stay NaT."""
        for index, value in parsed.items():
            try:
                if pd.notna(value):
                    result.at[index] = pd.Timestamp(value)
            except (TypeError, ValueError, OverflowError, pd.errors.OutOfBoundsDatetime):
                continue

    if pd.api.types.is_datetime64_any_dtype(series):
        assign_valid(series)
        return result

    numeric = pd.to_numeric(series, errors="coerce")
    normalized_name = normalize_column_name(column_name) if column_name is not None else ""
    year_mask = pd.Series(False, index=series.index)
    if normalized_name in {"year", "fiscal_year"}:
        year_mask = numeric.between(1900, 2100, inclusive="both")
        if year_mask.any():
            try:
                assign_valid(pd.to_datetime(
                    {"year": numeric.loc[year_mask].astype("Int64"),
                     "month": 1, "day": 1}, errors="coerce"
                ))
            except (TypeError, ValueError, OverflowError):
                pass
    # Excel's 1900 date system is represented by serial days. Avoid
    # interpreting ordinary amounts as dates by requiring a date-like range.
    excel_mask = numeric.between(2000, 100000, inclusive="both") & ~year_mask
    if excel_mask.any():
        assign_valid(pd.to_datetime(
            numeric.loc[excel_mask], unit="D", origin="1899-12-30", errors="coerce"
        ))
    text = series.astype("string").str.strip()
    ymd_mask = text.str.fullmatch(r"\d{8}", na=False)
    if ymd_mask.any():
        assign_valid(pd.to_datetime(
            text.loc[ymd_mask], format="%Y%m%d", errors="coerce"
        ))
    # Do not let ordinary numeric measures be interpreted as years/dates by
    # pandas' scalar parser. Excel serials and explicit YYYYMMDD strings were
    # handled above; all other numeric values remain non-dates.
    remaining = result.isna() & ~excel_mask & ~ymd_mask & numeric.isna()
    if remaining.any():
        try:
            parsed = pd.to_datetime(series.loc[remaining], errors="coerce", format="mixed")
        except (TypeError, ValueError):
            parsed = pd.to_datetime(series.loc[remaining], errors="coerce")
        assign_valid(parsed)
    return result


def _value_type(series: pd.Series, name: Any = None) -> str:
    if series.dropna().empty:
        return "empty"
    if _numeric_ratio(series) >= 0.6:
        return "numeric"
    if _date_ratio(series, name) >= 0.6:
        return "date"
    unique = _unique_count(series)
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
            "type": _value_type(series, raw_name),
            "non_empty": int(series.notna().sum()),
        })

    candidates_by_field: dict[str, list[dict[str, Any]]] = {}
    for field, spec in FIELD_SPECS.items():
        candidates: list[dict[str, Any]] = []
        for column_index, info in enumerate(column_info):
            # A semantic name alone must not turn text such as a customer name
            # into a numeric business field and erase its real values.
            if spec["kind"] == "numeric" and _numeric_ratio(df.iloc[:, column_index]) < 0.6:
                continue
            score = _alias_score(info["normalized"], spec["aliases"])
            if spec["kind"] == "date":
                score += 22.0 * _date_ratio(df.iloc[:, column_index], info["source"])
                if info["type"] == "numeric":
                    score -= 20.0
            elif spec["kind"] == "numeric":
                score += 22.0 * _numeric_ratio(df.iloc[:, column_index])
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
            is_safe = is_safe or (
                field == "tx_date" and top.get("normalized") in {"year", "fiscal_year", "month", "month_number"}
                and top["score"] >= 105
            )
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
    # Support common currency symbols, whitespace, thousands separators, and
    # accounting negatives without imposing one locale on all uploads.
    negative = text.str.startswith("(") & text.str.endswith(")")
    text = text.str.replace(r"[$€£¥₹]", "", regex=True)
    text = text.str.replace(r"\s+", "", regex=True)
    text = text.str.replace("(", "", regex=False).str.replace(")", "", regex=False)

    both = text.str.contains(",", na=False) & text.str.contains(".", regex=False, na=False)
    last_comma = text.str.rfind(",")
    last_dot = text.str.rfind(".")
    european = both & (last_comma > last_dot)
    text.loc[european] = (
        text.loc[european].str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    remaining_comma = text.str.contains(",", na=False)
    comma_decimal = remaining_comma & text.str.match(r"^-?\d+,\d{1,2}$", na=False)
    text.loc[remaining_comma & ~comma_decimal] = text.loc[remaining_comma & ~comma_decimal].str.replace(",", "", regex=False)
    text.loc[comma_decimal] = text.loc[comma_decimal].str.replace(",", ".", regex=False)
    values = pd.to_numeric(text, errors="coerce")
    return values.where(~negative, -values.abs())


def _looks_like_identifier(name: Any) -> bool:
    """Avoid converting identifier strings such as ``000123`` to numbers."""
    normalized = normalize_column_name(name)
    return (
        normalized in {"id", "index", "number", "code", "key", "reference"}
        or normalized.endswith(("_id", "_code", "_key", "_number", "_reference"))
    )


def clean_dataframe(df: pd.DataFrame, mapping: dict[str, str] | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Clean an upload and return the cleaned frame plus factual metrics."""
    if df is None or df.empty:
        raise ValueError("File is empty or has no data.")

    original_rows = int(len(df))
    if mapping is None:
        mapping = detect_columns(df).get("mapping", {})
    work = apply_mapping(df, mapping)
    # Excel often carries a synthetic index as ``Unnamed: 0``. Remove only
    # clearly synthetic columns; a real business index is preserved unless it
    # is exactly the default 0..n-1 sequence.
    synthetic = []
    for column in work.columns:
        normalized = str(column).casefold()
        values = work[column]
        if normalized.startswith("unnamed_") or normalized in {"__index_level_0__", "level_0"}:
            synthetic.append(column)
        elif normalized in {"index", "row_number"}:
            numeric = pd.to_numeric(values, errors="coerce")
            expected = pd.Series(range(len(values)), index=numeric.index, dtype="float64")
            if numeric.notna().all() and numeric.astype("float64").equals(expected):
                synthetic.append(column)
    if synthetic:
        work = work.drop(columns=synthetic)
    # Treat whitespace-only cells as empty before removing blank rows/columns.
    # This is especially important for Excel exports with formatting beyond
    # the actual data range.
    work = work.replace(r"^\s*$", np.nan, regex=True)
    work = work.dropna(how="all").copy()
    blank_rows_removed = original_rows - int(len(work))
    work = work.dropna(axis=1, how="all").copy()

    # Normalize whitespace for every uploaded text-like column, including
    # columns that are not part of the optional business alias list.  Missing
    # values remain missing; the cleaner never invents replacements for them.
    for column in work.columns:
        if pd.api.types.is_object_dtype(work[column]) or pd.api.types.is_string_dtype(work[column]):
            work[column] = work[column].map(
                lambda value: value.strip() if isinstance(value, str) else value
            )
    work = work.replace([np.inf, -np.inf], np.nan)

    numeric_invalid = 0
    invalid_dates = 0
    for column in CANONICAL_NUMERIC_FIELDS:
        if column in work.columns:
            before = work[column].notna()
            converted = _numeric_series(work[column])
            numeric_invalid += int((before & converted.isna()).sum())
            work[column] = converted

    # A number of business exports split a period into Year and Month. Keep
    # those source columns, but add a real date for time-series analysis when
    # both components are present. No date is invented when either component
    # is missing or invalid.
    period_year = next((column for column in work.columns
                        if normalize_column_name(column) in {"year", "fiscal_year"}), None)
    if period_year is None and "tx_date" in work.columns:
        candidate_year = pd.to_numeric(work["tx_date"], errors="coerce")
        if float(candidate_year.between(1900, 2100).mean()) >= 0.6:
            period_year = "tx_date"
    period_month = next((column for column in work.columns
                         if normalize_column_name(column) in {"month", "month_number"}), None)
    if period_year is not None and period_month is not None:
        year_values = pd.to_numeric(work[period_year], errors="coerce")
        month_values = pd.to_numeric(work[period_month], errors="coerce")
        month_names = work[period_month].astype("string").str.strip().str.casefold()
        month_names_full = ("January", "February", "March", "April", "May", "June",
                            "July", "August", "September", "October", "November", "December")
        month_lookup = {name.casefold(): index for index, name in enumerate(month_names_full, 1)}
        month_lookup.update({name[:3].casefold(): index for index, name in enumerate(month_names_full, 1)})
        month_values = month_values.fillna(month_names.map(month_lookup))
        month_dates = pd.to_datetime(work[period_month], errors="coerce", format="mixed")
        month_values = month_values.fillna(month_dates.dt.month)
        valid_period = year_values.between(1900, 2100) & month_values.between(1, 12)
        invalid_dates += int((year_values.notna() & month_values.notna() & ~valid_period).sum())
        if valid_period.any():
            work["tx_date"] = pd.to_datetime(
                {"year": year_values.where(valid_period).astype("Int64"),
                 "month": month_values.where(valid_period).astype("Int64"),
                 "day": 1}, errors="coerce"
            )

    # Apply the same type normalization to non-standard numeric/date columns.
    # Identifier-looking columns are deliberately excluded so values such as
    # customer codes with leading zeroes are preserved exactly.
    for column in list(work.columns):
        if column in CANONICAL_NUMERIC_FIELDS or column in CANONICAL_TEXT_FIELDS or column == "tx_date":
            continue
        series = work[column]
        if pd.api.types.is_bool_dtype(series) or _looks_like_identifier(column):
            continue
        try:
            value_type = _value_type(series, column)
            if value_type == "numeric" and _numeric_ratio(series) >= 0.6:
                before = series.notna()
                converted = _numeric_series(series)
                numeric_invalid += int((before & converted.isna()).sum())
                work[column] = converted
            elif (value_type == "date"
                  and normalize_column_name(column) not in {"year", "fiscal_year", "month", "month_number"}
                  and _date_ratio(series, column) >= 0.6):
                non_empty = series.notna() & series.astype("string").str.strip().ne("")
                parsed = _parse_dates(series, column)
                invalid_dates += int((non_empty & parsed.isna()).sum())
                work[column] = parsed
        except (TypeError, ValueError):
            # An unusual column remains available in its original form rather
            # than causing an otherwise valid dataset to fail cleaning.
            continue

    if "tx_date" in work.columns:
        raw_date = work["tx_date"]
        non_empty = raw_date.notna() & raw_date.astype("string").str.strip().ne("")
        date_name = "tx_date"
        numeric_date = pd.to_numeric(raw_date, errors="coerce")
        if float(numeric_date.between(1900, 2100).mean()) >= 0.6:
            date_name = "year"
        parsed = _parse_dates(raw_date, date_name)
        invalid_dates += int((non_empty & parsed.isna()).sum())
        work["tx_date"] = parsed

    # Resolve missing business values after type conversion. Dates are the
    # deliberate exception: filling a missing/invalid date would fabricate
    # historical observations and could leak false chronology into a model.
    missing_before_fill = int(work.isna().sum().sum())
    for column in list(work.columns):
        if column == "tx_date":
            continue
        series = work[column]
        if not series.isna().any():
            continue
        is_numeric = pd.api.types.is_numeric_dtype(series)
        if not is_numeric and not _looks_like_identifier(column):
            is_numeric = _numeric_ratio(series) >= 0.6
        if is_numeric and not _looks_like_identifier(column):
            values = _numeric_series(series)
            median = values.dropna().median()
            if pd.notna(median):
                work[column] = values.fillna(float(median))
            else:
                # An all-invalid numeric field has no defensible replacement;
                # keep it numeric/NaN so persistence and analysis can report
                # the missing signal without turning it into text.
                work[column] = values
            continue
        try:
            mode = series.dropna().mode()
            replacement = mode.iloc[0] if len(mode) else "Unknown"
        except (TypeError, ValueError):
            replacement = "Unknown"
        work[column] = series.fillna(replacement)

    # Compare normalized values so formatting differences such as "$1,200"
    # versus "1200" do not allow duplicate business rows through.
    before_duplicates = int(len(work))
    try:
        work = work.drop_duplicates().copy()
    except TypeError:
        # Nested JSON/list values are uncommon but valid; compare their
        # stable string representation when pandas cannot hash them.
        work = work.loc[~work.astype("string").duplicated()].copy()
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
        "missing_values_filled": max(0, missing_before_fill - missing_values),
        "missing_values_remaining": missing_values,
        # Kept in the response for compatibility with existing clients.  It is
        # always false because the cleaner must not invent business columns.
        "derived_profit": False,
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
        if not data or any(not isinstance(item, dict) for item in data):
            raise ValueError("JSON arrays must contain objects with named fields.")
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
