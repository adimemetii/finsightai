"""Focused regression checks for the production upload and analysis seams."""

from __future__ import annotations

import io
import json

import pandas as pd

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

    workbook = io.BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame({"Metric": [1, 2], "Group": ["A", "B"]}).to_excel(writer, index=False)
    frame = app._read_upload_dataframe(workbook, "metrics.xlsx")
    assert list(frame.columns) == ["Metric", "Group"]

    visual = app._visualization_data(pd.DataFrame({"name": ["Adi", "Arben", "Adi"], "city": ["Gjilan", "Prishtina", "Gjilan"]}))
    assert visual["column_data"]
    assert visual["categorical_summary"]


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


if __name__ == "__main__":
    test_upload_readers_and_generic_analysis()
    test_cleaning_report_and_classifier_no_target_leakage()
    test_safe_http_errors()
    print("app robustness tests passed")
