# FinSight AI — Final Project Report

## Executive Summary

FinSight AI is a multi-tenant Flask application for importing business data,
cleaning it, exploring financial performance, forecasting supported metrics,
classifying financial risk, and preparing Power BI Desktop data. The final
implementation keeps Aiven MySQL as the durable database and adds a secure,
backend-mediated Groq conversational assistant.

## Objectives

- Provide reliable CSV, Excel, and JSON upload and cleaning.
- Keep user/company data isolated in MySQL.
- Surface dashboard KPIs, trends, visualizations, forecasts, and risk results.
- Export clean, Power BI-ready workbooks and CSV sources.
- Add natural-language analysis without exposing API credentials.
- Preserve the existing multilingual navbar and application design.

## Problem Statement

Users need to turn inconsistent business files into useful financial insight,
but real files commonly contain aliases, currency strings, Excel serial dates,
blank columns, duplicate rows, and invalid values. Forecasting also needs
enough dated history to produce a meaningful train/test evaluation.

## Solution

The application detects common business aliases, lets users review mappings,
normalizes dates and financial numbers, stores canonical financial fields plus
preserved cleaned row JSON, and renders warnings when a model cannot be used.
The dashboard and analytics remain available even when forecasting is not.

## Architecture

The application uses Flask routes and Jinja templates for the web layer,
MySQL Connector/Python with a connection pool for Aiven MySQL, pandas for data
processing, scikit-learn for time-aware models, matplotlib for forecast charts,
and openpyxl for Excel exports. Render starts `app:app` with Gunicorn. The
existing `financial_data` table supports standard fields while `dataset_rows`
preserves additional business columns.

## Technologies

Python, Flask, Jinja2, pandas, NumPy, scikit-learn, SciPy, matplotlib,
openpyxl, MySQL Connector/Python, Werkzeug security, Gunicorn, Aiven MySQL,
MySQL Workbench, Render, GitHub, Power BI Desktop, and Groq.

## Database

`init_db.py` creates/upgrades the existing MySQL schema and views. Tables cover
companies, users, uploaded files, canonical financial data, preserved dataset
rows, predictions, risk classifications, history, and private Power BI
resource metadata. Queries are scoped by authenticated `user_id`; company IDs
are also retained for reporting and isolation.

## Data Cleaning

The upload pipeline supports CSV, semicolon-delimited CSV, XLSX, XLS, and JSON.
It recognizes Date, Transaction Date, Created Date, Timestamp, Time, Revenue,
Sales, Sales Amount, Income, Expenses, Cost, Profit, Amount, Customers, and
Marketing Spend aliases. It handles timestamps, common Excel serial dates,
numeric strings, currency symbols, thousands separators, accounting negatives,
blank rows/columns, synthetic Excel indexes, missing values, and exact
duplicates. Invalid dates remain represented as missing values and are counted
in the upload warning/cleaning summary.

## EDA

The analytics route selects available numeric measures and dimensions from the
active cleaned dataset. It supports monthly trends when dates exist, grouped
category/dimension summaries, totals, and dataset-appropriate empty states.

## Dashboard

The dashboard shows transaction count, revenue, expenses, profit, date range,
recent uploads, recent predictions, risk status, and quick links. It uses only
the signed-in user's data and keeps upload/model failures from becoming raw
browser stack traces.

## Power BI

Power BI exports include cleaned data, predictions, prediction-vs-actual
comparisons, KPI summaries, monthly analysis, category analysis, and available
dimension summaries. Export cleanup removes synthetic indexes and application
processing columns, converts dates to proper date values, converts financial
fields to numeric values, uses UTF-8 CSV output, and writes Excel workbooks
with frozen headers, filters, and tables. The app copies the existing authored
PBIX template per user/dataset; it does not create a PBIX from scratch or use
Power BI Service.

## Machine Learning

The existing ML pipeline trains supported regression models with chronological
train/test separation, temporal features, lag/rolling features, imputation,
model comparison, metrics, and persisted model metadata. Native ML imports are
optional at application startup so health checks and non-ML workflows can
remain available if a host has a broken native wheel.

## Forecasting

Revenue, expenses, profit, and amount are eligible when they contain at least
12 valid dated numeric observations with variation. Twelve is retained because
the current pipeline requires a viable training partition, test partition, and
time-series validation. The forecast page now explains the actual valid count,
invalid-date count, missing date column, or insufficient variation. Uploads are
still successful and remain available to the dashboard and analytics.

## Prediction

The `/predict` route supports future-date forecasts, configurable horizons up
to 24 periods, saved prediction rows, optional historical/forecast charts, and
the existing risk classification workflow. Prediction failures are logged on
the server and returned as safe user-facing messages.

## AI Chatbot

Authenticated users can open the FinSight AI assistant from the shared
application shell. The browser sends a question and CSRF token to Flask at
`/api/chat`; Flask calls Groq using `GROQ_API_KEY` and the optional
`GROQ_MODEL`. The key never reaches HTML, JavaScript, Git, or logs.

The assistant keeps a bounded conversation in the current user session and
receives compact context: row count, date range, financial totals/averages,
small category rankings, detected trends, and recent predictions. Full raw
datasets are not sent to Groq. If no upload exists, it still works as a general
financial/data-analysis assistant. The existing navbar locale controls the
chat UI, suggestions, and requested answer language.

## Multilingual Support

The existing selector supports Shqip, English, Deutsch, and Chinese. Chat copy
uses the same i18n catalog and Groq is instructed to answer in the selected
locale. No second chatbot language selector was introduced.

## Security

Passwords use Werkzeug hashing. Sessions use an environment-backed Flask
secret, HTTP-only/SameSite cookies, and CSRF protection for browser writes.
Uploads are given secure server-side names and download routes verify ownership.
SQL values use parameters; dynamic identifiers are quoted only after discovery
from the live schema. Database TLS verification remains enabled for Aiven.

## Error Handling

Upload parsing, invalid files, missing data, database failures, model failures,
Groq failures, missing exports, 404s, 413s, and 500s return useful messages or
empty states. Detailed exceptions are retained in server logs without sending
stack traces or secrets to users.

## Render Deployment

`render.yaml` installs the requirements, starts one Gunicorn web service on
Render's `$PORT`, exposes `/healthz`, and declares MySQL and Groq settings.
The Flask startup path initializes/upgrades the existing MySQL schema. The
default Render filesystem is ephemeral, so Power BI artifacts and source upload
files may need regeneration after a redeploy; cleaned dataset rows remain in
MySQL.

## Testing

- Python compilation: passed for application, database, mapping, ML, and
  analysis modules.
- Business mapping tests: passed.
- Jinja template syntax scan: passed for all templates.
- Application import/route registration: passed; `/api/chat` and
  `/visualizations` are registered.
- ML and universal-analysis smoke tests: blocked in this workspace because the
  installed SciPy native DLL is denied by Windows Application Control.
- Live login, signup, upload, database, Groq, and Power BI integration tests
  require reachable Aiven MySQL/Groq configuration and were not executed
  against an external service from this workspace.

## Challenges and Solutions

- Inconsistent source fields: added curated aliases and reviewable mapping.
- Excel/locale number formats: added safe currency, separator, and accounting
  parsing.
- Excel date serials: added bounded serial-date conversion.
- Strict forecast failure: retained model quality requirements while turning
  failures into precise, non-blocking warnings.
- Schema drift: signup now builds its insert from discovered supported users
  columns instead of assuming a legacy column set.
- Power BI artifacts: added export sanitization and UTF-8 CSV generation.
- AI privacy: send compact aggregates and model results through a backend-only
  Groq call.

## Limitations

- Forecast quality depends on the number, regularity, and quality of dated
  observations.
- Chat context is bounded and process-local; a Render restart clears current
  chat history, while database data remains durable.
- The application copies an authored PBIX template but cannot generate native
  Power BI report layouts from zero.
- A live Aiven MySQL connection and a configured Groq key are required for
  their respective production workflows.

## Future Improvements

Background model training, persistent encrypted chat sessions, automated
dataset-specific forecast frequency detection, richer test fixtures, and
Power BI template version management could be added without changing the
current Aiven MySQL architecture.

## Conclusion

FinSight AI now provides a safer and more professional path from messy business
files to dashboard analysis, forecasts, risk signals, Power BI outputs, and
natural-language explanations while retaining the existing Flask, Aiven
MySQL, Render, GitHub, and Power BI Desktop workflow.
