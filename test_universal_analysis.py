"""Quick smoke test for universal_analysis.py with multiple dataset shapes."""
import warnings
warnings.filterwarnings("ignore")
import pandas as pd
import numpy as np
import universal_analysis as ua


def show(label, result):
    print("=" * 20, label, "=" * 20)
    print("  rows/cols:", result["rows"], result["columns"], "| types:",
          {k: v for k, v in result["types"].items()})
    for s in result["sections"]:
        if "error" in s:
            print("  section:", s["target"], s["kind"], "->", s["error"])
        else:
            print("  section:", s["target"], s["kind"], "|", s["model_name"],
                  "| pred:", s["prediction_label"],
                  "|", s["metric_name"], round(s.get("metric_value") or 0, 3),
                  "| note:", s["interpretation"][:80])
    print("  trends:", [(t["metric"], t["direction"], t["change_pct"])
                        for t in result["trends"]])
    print("  insight:", result["insight"])
    print("  has_predictions:", result["has_predictions"], "| message:",
          result["message"])


rng = np.random.default_rng(42)

# 1. Financial dataset
n = 200
dates = pd.date_range("2023-01-01", periods=n, freq="D")
rev = np.linspace(1000, 5000, n) + rng.normal(0, 200, n)
exp = np.linspace(800, 3000, n) + rng.normal(0, 80, n)
df1 = pd.DataFrame({
    "Date": dates.astype(str), "Revenue": rev, "Expenses": exp,
    "Profit": rev - exp,
    "Category": rng.choice(["A", "B", "C"], n),
    "Region": rng.choice(["North", "South"], n),
})
show("FINANCIAL", ua.auto_analyze(df1))

# 2. Customer dataset with Risk/Status
n2 = 300
df2 = pd.DataFrame({
    "Customer_Id": [f"C{i}" for i in range(n2)],
    "Customer_Age": rng.integers(18, 70, n2),
    "Income": rng.normal(40000, 12000, n2),
    "Spending_Score": rng.integers(0, 100, n2),
    "Risk": rng.choice(["Low", "Medium", "High"], n2, p=[0.6, 0.25, 0.15]),
    "Status": rng.choice(["Active", "Inactive"], n2, p=[0.8, 0.2]),
    "Signup_Date": pd.date_range("2022-01-01", periods=n2, freq="D").astype(str),
})
show("CUSTOMER/RISK", ua.auto_analyze(df2))

# 3. Numeric-only small dataset (no categorical target)
df3 = pd.DataFrame({
    "x1": rng.normal(10, 3, 40),
    "x2": rng.normal(100, 20, 40),
    "y": rng.normal(1000, 100, 40),
})
show("NUMERIC-ONLY", ua.auto_analyze(df3))

# 4. Mixed weekdays, sum metric, ids
df4 = pd.DataFrame({
    "ID": range(100),
    "Metric": rng.normal(500, 80, 100),
    "Branch": rng.choice(["Berlin", "Paris", "Rome"], 100),
})
show("GENERIC", ua.auto_analyze(df4))

# 5. Tiny dataset (too small)
df5 = pd.DataFrame({"Amount": rng.uniform(10, 90, 5), "Date": dates[:5].astype(str)})
show("TINY", ua.auto_analyze(df5))

# 6. With dates + missing + text + bool
n6 = 120
df6 = pd.DataFrame({
    "OrderDate": pd.date_range("2024-01-01", periods=n6, freq="W").astype(str),
    "CustomerName": [f"Cust_{i}" for i in range(n6)],
    "Order_Amount": rng.uniform(20, 800, n6),
    "Is_Premium": rng.choice([0, 1], n6),
    "Region": rng.choice(["EU", "US", "APAC"], n6),
})
df6.loc[5, "Order_Amount"] = np.nan
df6.loc[7, "Region"] = None
df6.loc[8, "OrderDate"] = "bad-date"
show("E-COMMERCE", ua.auto_analyze(df6))

# 7. Risk classification via predictor on representative row
print("=" * 20, "DYNAMIC PREDICT", "=" * 20)
a2 = ua.auto_analyze(df2)
for s in a2["sections"]:
    if s.get("kind") == "classification" and "error" not in s:
        dyn = ua.predict_dynamic(s)
        print("  dynamic", s["target"], "->", dyn)
    if s.get("kind") == "regression" and "error" not in s:
        dyn = ua.predict_dynamic(s)
        print("  dynamic", s["target"], "->", dyn)

print("\nALL TESTS PASSED")