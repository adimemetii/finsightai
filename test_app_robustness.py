"""Focused regression checks for the production upload and analysis seams."""

from __future__ import annotations

import io
import json

import pandas as pd
from flask import render_template, session

import app
import universal_analysis as ua


def test_upload_readers_and_generic_analysis() -> None:
    semicolon = b"name;city\nAdi;Gjilan\nArben;Prishtina\n"
    frame = app._read_upload_dataframe(io.BytesIO(semicolon), "locations.csv")
    assert list(frame.columns) == ["name", "city"]
    assert len(frame) == 2

    malformed = b"name;city\nAdi;Gjilan;unexpected\nArben;Prishtina\n"
    frame = app._read_upload_dataframe(io.BytesIO(malformed), "malformed.csv")
    assert len(frame) == 1
    assert frame.attrs["finsight_ingest_warnings"]

    jsonl = b'{"name":"Adi","city":"Gjilan"}\n{"name":"Arben","city":"Prishtina"}\n'
    frame = app._read_upload_dataframe(io.BytesIO(jsonl), "locations.json")
    assert set(frame.columns) == {"name", "city"}

    raw_json_matrix = b'[[950,12800,"Income"],[1600,10200,"expense"]]'
    frame = app._read_upload_dataframe(io.BytesIO(raw_json_matrix), "raw.json")
    assert list(frame.columns) == ["column_1", "column_2", "column_3"]
    assert len(frame) == 2

    raw_csv = b'950;12800;Income\n1600;10200;expense\n'
    frame = app._read_upload_dataframe(io.BytesIO(raw_csv), "raw.csv")
    assert list(frame.columns) == ["column_1", "column_2", "column_3"]
    assert len(frame) == 2

    workbook = io.BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame({"Metric": [1, 2], "Group": ["A", "B"]}).to_excel(writer, index=False)
    frame = app._read_upload_dataframe(workbook, "metrics.xlsx")
    assert list(frame.columns) == ["Metric", "Group"]

    visual = app._visualization_data(pd.DataFrame({"name": ["Adi", "Arben", "Adi"], "city": ["Gjilan", "Prishtina", "Gjilan"]}))
    assert visual["column_data"]
    assert visual["categorical_summary"]

    nested = app.json_safe_record({
        "payload": {"values": [1, 2], "created": pd.Timestamp("2026-01-01")},
        "invalid_number": ["not", "a", "number"],
    })
    json.dumps(nested, allow_nan=False)
    assert nested["payload"]["created"] == "2026-01-01T00:00:00"
    assert app._db_number(["not-a-number"]) is None
    assert len(app._db_text("x" * 200, 80)) == 80


def test_cleaning_report_and_classifier_no_target_leakage() -> None:
    raw = pd.DataFrame({"amount": ["1,000", "1,000", None], "status": ["new", "new", "done"], "blank": [None, None, None]})
    cleaned, report = app.clean_dataframe(raw, {})
    assert len(cleaned) == 2
    assert report["duplicate_rows_detected"] >= 1
    assert report["duplicates_removed"] >= 1
    assert report["missing_values_detected"] >= 1
    assert report["missing_values_handled"] >= 1
    assert report["empty_columns_removed"] == 1

    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    data = pd.DataFrame({"date": dates, "value": range(20), "status": ["new", "done"] * 10})
    analysis = ua.auto_analyze(data)
    classifier = next(section for section in analysis["sections"] if section.get("target") == "status")
    assert "status" not in classifier.get("feature_names", [])
    assert classifier.get("metrics", {}).get("f1_weighted") is not None


def test_safe_http_errors() -> None:
    client = app.app.test_client()
    missing = client.get("/does-not-exist")
    assert missing.status_code == 404
    api_missing = client.get("/api/does-not-exist")
    assert api_missing.status_code == 404
    assert api_missing.is_json
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.headers["X-Content-Type-Options"] == "nosniff"


def test_predict_template_renders_primary_target_in_script() -> None:
    """The content and extra_js blocks must not rely on shared local scope."""
    with app.app.test_request_context("/predict"):
        session["user_id"] = 1
        session["company_name"] = "Test Company"
        session["user_name"] = "Test User"
        html = render_template(
            "predict.html",
            has_data=True,
            forecast_targets=["revenue", "expenses"],
            expense_targets=["expenses"],
            risk_min_date="2026-01-01",
            risk_max_date="",
            risk_default_date="2026-02-01",
            risk_available=False,
            analysis={},
            insight="",
            section_types={},
            date_column="tx_date",
            forecast_status={},
        )
    assert 'const primaryTarget = "revenue";' in html


if __name__ == "__main__":
    test_upload_readers_and_generic_analysis()
    test_cleaning_report_and_classifier_no_target_leakage()
    test_safe_http_errors()
    print("app robustness tests passed")
