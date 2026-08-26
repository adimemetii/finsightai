# Power BI Integration - FinSight AI

## Recommended Desktop-only architecture

Use one authored Power BI Desktop template plus one generated copy per upload.
The application stores cleaned and analyzed data in a private folder for the
authenticated user and dataset, then copies the template to a filename such as
`finsight_user_123_dataset_456.pbix`. No Power BI Service, REST API, workspace,
or shared report is involved.

The application enforces isolation before Power BI: every upload, cleaned row,
prediction, export, and Desktop report metadata row is scoped by `user_id` and
`uploaded_file_id`.

This document explains the local Desktop workflow and its technical limits.

> The Flask app cannot create a complete native `.pbix` report from zero.
> It copies an authored template per dataset and generates that dataset's
> Power BI-ready CSV/Excel sources. The visual layout and data model remain
> authored in Power BI Desktop.

## Export a local Power BI workbook (no account)

1. In FinSight AI, log in as the user and open **Power BI**.
2. Click **Download Excel for Power BI**.
3. In Power BI Desktop select **Get Data -> Excel** and choose the downloaded file.
4. Load `Cleaned_Data`, `Predictions`, `KPI_Summary`, `Monthly_Analysis`, and
   `Category_Analysis`. The `README` sheet describes each table.
5. Create the visuals and save the result as a `.pbix` file on your computer.

The export is scoped to the signed-in user's latest dataset; another user
cannot download its data. Each uploaded dataset gets its own generated PBIX
folder and database record.

---

## 1. What the Flask app already gives you

When you run `python init_db.py` the following SQL **views** are
created in the `finsightai` database, ready for Power BI to query:

| View                     | What it shows                                        |
|--------------------------|------------------------------------------------------|
| `v_company_kpis`         | One row per company with totals (Revenue, Expenses, Profit) - perfect for KPI cards. |
| `v_company_timeseries`   | One row per company per date with revenue/expenses/profit/amount - for line/area charts. |
| `v_company_category`     | Amount/Revenue/Expenses/Profit grouped by category - for bar/column charts. |
| `v_company_city`         | Transactions and total amount by city. |
| `v_company_status`       | Transaction count by status (Completed, Pending, etc.). |
| `v_company_payment`      | Transaction count and amount by payment method. |
| `v_company_predictions`  | All predictions (with type, date, value). |

Every view already contains a `company_id` column, so Power BI can
filter every visual down to a single company with one click.

---

## 2. Connect Power BI Desktop to MySQL

## 4. Flask Desktop workflow

1. Log in as a user and upload a CSV or XLSX file.
2. Flask validates, cleans, deduplicates, and stores the rows under the
  authenticated user's `user_id`.
3. Open **Power BI Desktop** in Flask and click **Generate / Refresh Power BI**.
4. Download **Power BI File** and open the PBIX in Power BI Desktop.
5. Refresh the local CSV sources from the user's private `users/<token>/powerbi/data`
  folder. The generated `README-PowerBI-Desktop.txt` lists the source files.

The generated data includes cleaned transactions, predictions, KPI totals,
monthly and category analysis, city, payment, company, department, status,
and prediction-versus-actual tables. The Excel download contains the same
data plus an Excel dashboard and named tables.

## 5. Technical limitation

PBIX is a proprietary Power BI Desktop format. Flask does not create or
rewrite PBIX internals. The app copies the existing local `finsightai.pbix`
template into the authenticated user's private resource folder and generates
the data files it is designed to read. This is a real PBIX template copy, not
a renamed ZIP, JSON, or CSV file.

## 6. Technical limitation

There is no official Power BI Desktop API that lets Flask create a complete
native `.pbix` from zero, including its model, relationships, DAX, pages, and
visual objects. A PBIX is a proprietary Desktop artifact. Renaming a ZIP,
editing undocumented internals, or fabricating a PBIX extension is not a
supported solution.

## 7. Practical automation

1. Author `finsightai.pbix` once in Power BI Desktop with tables and visuals
  for the canonical columns produced by FinSight AI.
2. Configure its queries to read `data/financial_data.csv`,
  `data/predictions.csv`, and the generated analysis CSV files.
3. Flask creates a private data folder and copies that template once per
  `uploaded_file_id`.
4. Flask generates dynamic analysis tables and prediction-vs-actual data;
  Power BI Desktop refreshes them when the user opens the downloaded file.
5. Dynamic visual creation for arbitrary columns cannot be guaranteed inside a
  PBIX without an unsupported Desktop automation layer. The safe fallback is
  to export all detected dimensions/measures and use a template with visuals
  that gracefully handle missing columns.

## 8. Isolation checklist

Every Power BI route uses the authenticated Flask session. Database queries
filter by `user_id`, generated folders use an opaque per-user token, and
download routes resolve resources from the current user's database row rather
than accepting a filesystem path or another user's identifier.

To test isolation, create two users, upload different files, generate each
user's resources, and confirm that each PBIX/data package contains only that
user's rows. A download URL cannot be changed to another user's resource
because the resource lookup is scoped to the current session.
