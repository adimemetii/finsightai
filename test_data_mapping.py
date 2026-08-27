"""Focused tests for business column detection and upload cleaning."""

import json

import pandas as pd

from data_mapping import clean_dataframe, detect_columns, profile_json_payload


def test_alias_detection_and_cleaning() -> None:
    frame = pd.DataFrame([
        {"Transaction Date": "2026-01-01", "Sales Amount": "1,200.50", "Operating Costs": 700, "Customer Count": 10, "Marketing Spend": 50, "Category": "Retail"},
        {"Transaction Date": "not-a-date", "Sales Amount": 900, "Operating Costs": 500, "Customer Count": 8, "Marketing Spend": 35, "Category": "Retail"},
        {"Transaction Date": "2026-01-01", "Sales Amount": "1,200.50", "Operating Costs": 700, "Customer Count": 10, "Marketing Spend": 50, "Category": "Retail"},
    ])
    detection = detect_columns(frame)
    mapping = detection["mapping"]
    assert mapping["tx_date"] == "Transaction Date"
    assert mapping["revenue"] == "Sales Amount"
    assert mapping["expenses"] == "Operating Costs"
    assert mapping["customers"] == "Customer Count"
    assert mapping["marketing_spend"] == "Marketing Spend"

    cleaned, summary = clean_dataframe(frame, mapping)
    assert list(cleaned["revenue"].dropna()) == [1200.50, 900.0]
    assert summary["duplicates_removed"] == 1
    assert summary["invalid_dates"] == 1
    assert "category" in cleaned.columns


def test_common_json_shapes() -> None:
    records = [{"date": "2026-01-01", "revenue": 100}, {"date": "2026-01-02", "revenue": 120}]
    assert list(profile_json_payload(records).columns) == ["date", "revenue"]
    wrapped = profile_json_payload({"records": records})
    assert len(wrapped) == 2
    assert json.loads(json.dumps(wrapped.to_dict(orient="records"))) == records


if __name__ == "__main__":
    test_alias_detection_and_cleaning()
    test_common_json_shapes()
    print("data_mapping tests passed")
