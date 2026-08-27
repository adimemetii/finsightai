# FinSight AI - Financial Forecasting Platform

A multi-tenant Flask + MySQL web application for cleaning,
analysing, and forecasting financial data with company-based
data isolation and a per-company Power BI dashboard link.

---

## Quick start (Windows)

```bat
cd "C:\Users\Blacksn0w\Desktop\Internship(TA)\FinSight AI"
run.bat
```

`run.bat` will:

1. create a `.env` file from `.env.example` if missing,
2. install Python dependencies,
3. create the `finsightai` MySQL database and all tables/views,
4. start Flask on http://127.0.0.1:5000.

If you prefer to do it step by step:

```bat
python -m pip install -r requirements.txt
python init_db.py        :: creates finsightai + tables + views
python app.py            :: starts Flask
```

On PowerShell use `run.ps1` instead of `run.bat`.

---

## Features

- 🔐 **Authentication** - signup with company name, login, logout,
  password hashing (`werkzeug.security`), session management.
- 🏢 **Multi-tenant** - every record (uploads, financial data,
  predictions, history, Power BI link) is scoped to the user's
  `company_id`. A user from Company A can never see Company B data.
- 📤 **Robust uploads** - CSV / XLSX / XLS / JSON accepted, dragged or clicked,
  profiled, mapped, cleaned, validated, and inserted into MySQL.
- 📊 **Dashboard** - KPI cards (rows, revenue, expenses, profit),
  recent uploads, recent predictions, quick action buttons.
- 🔮 **Forecasting** - available financial targets with historical context,
  configurable horizons, model information, and persisted forecast points.
- 🕓 **Dashboard history** - every upload, prediction and signup is
  logged in `dashboard_history` and visible in the Database page.
- 📈 **Power BI Desktop integration** - each uploaded dataset gets its own
   private local `.pbix` artifact and data folder. No Power BI Service, REST API,
   or shared report is used.
- 🛡️ **Error handling** - every route has try/except wrappers,
  flash messages, and 404 / 413 / 500 handlers.

---

## Project structure

```
FinSight AI/
├── app.py                  # Flask application (auth, upload, predict, history, Power BI)
├── init_db.py              # Creates database + tables + views from zero
├── requirements.txt
├── .env                    # Local environment (auto-created by run.bat)
├── .env.example            # Template for the .env file
├── powerbi_setup.md        # Power BI integration documentation
├── run.bat / run.ps1       # One-click startup scripts
├── templates/              # Jinja2 templates
│   ├── base.html
│   ├── index.html
│   ├── login.html
│   ├── signup.html
│   ├── dashboard.html
│   ├── upload.html
│   ├── predict.html
│   ├── history.html
│   ├── powerbi.html
│   ├── 404.html
│   └── 500.html
├── static/                 # CSS / JS
└── uploads/                # Uploaded files (auto-created)
```

---

## Database schema

Created by `init_db.py`. The MySQL database is **`finsightai`**.

| Table              | Purpose                                                          |
|--------------------|------------------------------------------------------------------|
| `companies`        | One row per company (created on signup, unique `company_name`).  |
| `users`            | Users linked to a company via `company_id`; password hashed.     |
| `uploaded_files`   | Every upload (original name, stored path, rows, status).         |
| `financial_data`   | The cleaned rows, one per transaction, with `company_id`.        |
| `dataset_rows`     | JSON-preserved cleaned rows for non-standard business columns.    |
| `predictions`      | Every prediction with type, date, value, model.                  |
| `dashboard_history`| Per-company event log (uploads, predictions, signups, failures).  |
| `company_powerbi`  | One Power BI URL per company, used by the dashboard button.      |
| `powerbi_desktop_reports` | Per-user, per-dataset PBIX filename and generation status. |

Plus 7 **views** for Power BI:

* `v_company_kpis`
* `v_company_timeseries`
* `v_company_category`
* `v_company_city`
* `v_company_status`
* `v_company_payment`
* `v_company_predictions`

---

## Environment variables

Edit `.env` (or `.env.example` for the template) with your settings:

```
FLASK_ENV=development
FLASK_DEBUG=True
FLASK_APP=app.py
SECRET_KEY=change-me

DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=
DB_NAME=finsightai
DB_SSL_CA=

MAX_CONTENT_LENGTH=16777216
UPLOAD_FOLDER=uploads
ALLOWED_EXTENSIONS=csv,xlsx,xls,json

# Optional local template. It must be authored in Power BI Desktop.
POWERBI_TEMPLATE=finsightai.pbix
```

The application uses the `DB_*` names above. Existing local installations
using the legacy `MYSQL_*` names remain supported as a compatibility fallback.

---

## Power BI Desktop

The app generates user-owned CSV sources, analytics tables, an Excel
data export, and a copy of the reusable `finsightai.pbix` template.
Open the **Power BI Desktop** page, click **Generate / Refresh Power BI**,
then download the Power BI file and open it in Power BI Desktop. Refresh
the local data sources when prompted. No online account or URL is required.

Important limitation: Flask/Python cannot officially create a complete native
PBIX from zero. The app therefore copies an existing Power BI Desktop template
into a unique per-user/per-dataset path and generates that dataset's cleaned,
analysis, and prediction CSV sources. The user opens the PBIX in Desktop and
refreshes the local sources. Visual layouts, DAX, relationships, and report
pages must be authored in the template.
See [powerbi_setup.md](powerbi_setup.md) for the Desktop workflow.

---

## Tests you should run after setup

1. **Signup** - create an account, choose a company name, see the
   dashboard.
2. **Login / Logout** - log out and back in.
3. **Upload** - drop a CSV / XLSX / XLS / JSON file, review the mapping, and
   watch the cleaned rows land in
   `financial_data` with the correct `company_id`.
4. **Forecast** - choose a target, horizon, and future date, see the values
   stored in `predictions`.
5. **Database** - verify both the upload and the prediction show up.
6. **Power BI Desktop** - generate the private resources, download the
   template, open it in Power BI Desktop, and refresh the local files.
7. **Isolation** - create a second account under a different
   company, upload a file, then log in as the first user and
   confirm you cannot see it.

## Production upload behavior

FinSight AI accepts CSV, XLSX, XLS, and JSON files up to the configured upload
limit. The upload screen previews the file, normalizes common business aliases
(for example Sales Amount, Operating Costs, Customer Count, and Marketing
Spend), reports confidence, and lets the user manually map uncertain fields
before saving.

Date and Revenue are the required minimum for the complete financial flow;
Expenses, Profit, Customers, Category, Department, City, Payment Method, and
Marketing Spend are optional. Non-standard columns are retained in the
database-backed `dataset_rows` JSON store, while standard financial fields stay
available through the existing `financial_data` compatibility table.

The repository also includes `render.yaml`, which installs the pinned
requirements, runs `init_db.py`, starts Gunicorn, and checks `/healthz`. Set
`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, and `DB_SSL_CA` as
Render environment variables; existing `MYSQL_*` names remain supported.
