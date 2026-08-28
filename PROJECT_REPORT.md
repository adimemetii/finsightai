# FinSight AI — Raport i plotë i projektit

**Data e raportit:** 28 gusht 2026  
**Tipi i projektit:** Web application për analizë financiare, parashikime dhe eksport Power BI  
**Gjendja:** Implementim funksional me databazë MySQL/Aiven, upload të dhënash dhe deploy në Render

---

## 1. Përmbledhje ekzekutive

FinSight AI është një platformë web e ndërtuar me Flask që lejon përdoruesin të:

- krijojë llogari dhe të identifikohet;
- ngarkojë CSV, XLSX, XLS ose JSON;
- kontrollojë dhe rishikojë mapping-un e kolonave;
- pastrojë dhe normalizojë të dhënat;
- shfaqë KPI, grafikë dhe analiza sipas dataset-it aktiv;
- gjenerojë parashikime për metrika financiare;
- kryejë klasifikim të riskut;
- shohë historikun e veprimeve;
- shkarkojë të dhënat e pastruara dhe materiale për Power BI Desktop.

Arkitektura përdor MySQL si burim të qëndrueshëm të të dhënave, një connection pool ekzistues, ruajtje JSON për kolonat jo-standarde dhe modele ML të ndara sipas përdoruesit.

---

## 2. Qëllimi i projektit

Qëllimi kryesor është të sigurojë një rrjedhë të vetme nga të dhënat e papastruara deri te analiza:

```text
Upload file
    ↓
Read and profile
    ↓
Detect columns and map business fields
    ↓
Clean, validate and deduplicate
    ↓
Store canonical + arbitrary data in MySQL
    ↓
Dashboard and analytics
    ↓
Forecasting, risk classification and Power BI export
```

Platforma nuk kërkon që përdoruesi të ketë emra të njëjtë kolonash. Ajo përdor aliases, tipin e vlerave dhe mapping manual kur një kolonë nuk mund të identifikohet me siguri.

---

## 3. Teknologjitë

| Shtresa | Teknologji |
|---|---|
| Backend | Python, Flask, Jinja2 |
| Databaza | MySQL / Aiven MySQL |
| Connection management | `mysql.connector.pooling.MySQLConnectionPool` |
| Data processing | Pandas, NumPy |
| Machine learning | scikit-learn |
| Grafikë server-side | Matplotlib |
| Grafikë në browser | Chart.js |
| Harta | Leaflet + OpenStreetMap/Nominatim |
| Excel | openpyxl |
| Frontend styling | Tabler, Bootstrap-compatible classes, CSS custom |
| Browser requests | Axios |
| Server production | Gunicorn |
| Deployment | Render |
| Desktop BI | Power BI Desktop dhe template `.pbix` |

Varësitë kryesore gjenden në `requirements.txt`.

---

## 4. Struktura e projektit

| Skedari/direktoria | Përgjegjësia |
|---|---|
| `app.py` | Aplikacioni Flask, route-t, autentikimi, DB access, upload, analytics, predictions dhe Power BI |
| `init_db.py` | Krijimi/verifikimi i databazës, tabelave, indekseve dhe view-ve |
| `main.py` | Entry point alternativ që nis Flask |
| `data_mapping.py` | Zbulimi i kolonave, mapping-u, cleaning dhe serializimi JSON |
| `ml_pipeline.py` | Feature engineering, trajnimi, krahasimi dhe ruajtja e modeleve |
| `universal_analysis.py` | Analizë e pavarur nga emrat e kolonave, target detection dhe insight-e |
| `dataset_tools.py` | Module ndihmës/legacy për profiling dhe përgatitje dataset-esh |
| `i18n.py` | Përkthime për Shqip, English, Deutsch dhe Chinese |
| `templates/` | Faqet Jinja të aplikacionit |
| `static/css/style.css` | Pamja vizuale e aplikacionit |
| `static/js/script.js` | Sjellje e përbashkët në browser |
| `models/` | Modele dhe metadata të ruajtura sipas përdoruesit |
| `users/` | Resource folders private për përdoruesit dhe Power BI |
| `uploads/` | Direktori lokale kompatibile për upload |
| `finsightai.pbix` | Template i krijuar paraprakisht në Power BI Desktop |
| `powerbi_setup.md` | Udhëzime të detajuara për workflow-in Power BI |
| `test_*.py` | Teste për mapping, ML dhe universal analysis |

---

## 5. Funksionalitetet kryesore

### 5.1 Autentikimi

Signup përdor fushat e formularit `name`, `email`, `password` dhe `company_name`. Emri i vetëm ndahet në mënyrë të sigurt në `firstName` dhe `lastName` për databazën.

Login:

- kontrollon email-in;
- lexon kolonat e tabelës `users` në mënyrë dinamike;
- verifikon password hash me Werkzeug;
- ruan `user_id`, emrin dhe kompaninë në session.

Aplikacioni nuk ruan password plaintext.

### 5.2 Dashboard

Dashboard-i shfaq:

- numrin e rreshtave;
- total Revenue, Expenses dhe Profit;
- statusin e prediction/risk;
- upload-et e fundit;
- prediction-et e fundit;
- quick actions për Upload, Analytics, Prediction dhe Power BI.

Dataset-i aktiv është upload-i i fundit me status `processed` për përdoruesin aktual.

### 5.3 Analytics

Analytics përdor vetëm dataset-in e përdoruesit aktual dhe ndërton grafikë sipas kolonave që ekzistojnë realisht:

- trend mujor kur ka datë;
- totalet për kolonat numerike;
- analizë sipas category, department, city, payment method, status ose dimensioneve të panjohura;
- hartë qytetesh kur ekziston kolonë city.

Dataset-et jo-standarde nuk hidhen. Kolonat e tyre mbahen në `dataset_rows` dhe përdoren në analizën dinamike.

### 5.4 Upload dhe cleaning

Formatet e pranuara janë:

- CSV;
- XLSX;
- XLS;
- JSON me array records ose objekt `data`/`records`.

Kufizimet kryesore janë 16 MB, maksimumi 200 kolona dhe maksimumi 1,000,000 rreshta.

Rrjedha e upload-it:

1. Browser-i dërgon file-in te `/upload/preview`.
2. Server-i e lexon dhe ndërton profilin.
3. Përdoruesi sheh kolonat, confidence dhe preview-n e pastruar.
4. Mapping-u mund të korrigjohet manualisht.
5. `/upload` e lexon file-in përsëri me të njëjtin mapping.
6. Dataset-i ruhet në MySQL dhe vendoset si aktiv.
7. Analiza, modelet dhe Power BI resources rifreskohen.

Cleaning përfshin:

- normalizim emrash në snake_case;
- heqje të rreshtave bosh;
- heqje të kolonave bosh;
- trim të vlerave tekstuale;
- konvertim të vlerave numerike;
- mbështetje për valuta, mijëshe europiane dhe accounting negatives;
- parsing të datave;
- derivim të Profit nga Revenue - Expenses kur mungon;
- heqje të rreshtave duplikatë;
- raportim të vlerave të pavlefshme dhe vlerave që mungojnë.

Excel-et pa header mbështeten gjithashtu. Nëse rreshti i parë duket si data dhe jo si emra kolonash, sistemi lexon me `header=None`, ruan rreshtin e parë dhe krijon emra teknikë si `column_1`, `column_2`, etj.

### 5.5 Mapping-u i kolonave

Fushat standarde janë:

| Fusha | Emri canonical | Roli |
|---|---|---|
| Date | `tx_date` | E nevojshme për trend/prediction |
| Revenue | `revenue` | Fushë kryesore financiare |
| Expenses | `expenses` | E rekomanduar |
| Profit | `profit` | E rekomanduar ose e derivuar |
| Amount | `amount` | Target fallback për dataset-e gjenerike |
| Customers | `customers` | Opsionale |
| Marketing Spend | `marketing_spend` | Opsionale |
| Transaction ID | `transaction_id` | Opsionale |
| Description | `description` | Opsionale |
| Category | `category` | Dimension |
| Transaction Type | `tx_type` | Dimension |
| Payment Method | `payment_method` | Dimension |
| Department | `department` | Dimension |
| City | `city` | Dimension |
| Status | `status` | Dimension |

Confidence paraqitet si `HIGH`, `MEDIUM`, `LOW` ose `MISSING`. Mapping-u gjenerik mund të zgjedhë një kolonë numerike si `amount` kur file-i nuk përdor emra standardë.

### 5.6 Predictions

Faqja `/predict` mbështet forecast për:

- Revenue;
- Expenses;
- Profit;
- Amount.

Për forecast-in standard kërkohet kolonë datë dhe të paktën 12 observime të vlefshme me variacion numerik. Sistemi:

- zgjedh cadence-in e historisë;
- krijon forecast points për horizon 1–24 periudha;
- shfaq vlerën e parashikuar, baseline dhe estimated error;
- ruan rezultatet në `predictions`;
- ndërton grafik historik + forecast.

Nëse dataset-i nuk ka datë, ai mund të analizohet dhe vizualizohet si dataset gjenerik, por nuk krijohet një forecast kohor duke shpikur data.

### 5.7 Risk classification

Risk-u bazohet në rregulla transparente:

- `HIGH RISK`: Profit < 0 ose Expenses > Revenue;
- `MEDIUM RISK`: Revenue > 0 dhe Profit Margin < 8%;
- `LOW RISK`: rastet e tjera.

Rezultati ruhet me datë, vlera financiare, nivel risku dhe shpjegim.

### 5.8 Dashboard history dhe Database page

Veprimet kryesore regjistrohen në `dashboard_history`, përfshirë:

- signup;
- upload të suksesshëm ose të dështuar;
- prediction;
- klasifikim risku;
- gjenerim/eksport Power BI.

Kjo lejon auditim të thjeshtë të aktivitetit të përdoruesit.

---

## 6. Arkitektura e databazës

### 6.1 Tabelat

| Tabela | Përmbajtja |
|---|---|
| `companies` | Kompanitë dhe emri unik |
| `users` | Llogaritë e përdoruesve dhe password-i i hash-uar |
| `uploaded_files` | Metadata e file-it, mapping-u, cleaning summary dhe statusi |
| `financial_data` | Fushat canonical financiare për compatibility dhe query të shpejta |
| `dataset_rows` | Rreshtat e pastruar si JSON, për të ruajtur kolona arbitrare |
| `predictions` | Vlerat e parashikuara dhe metadata e modelit |
| `risk_classifications` | Rezultatet e risk classification |
| `dashboard_history` | Eventet e përdoruesit |
| `company_powerbi` | Link/configuration për Power BI sipas kompanisë |
| `user_powerbi_resources` | Token dhe file paths private për Power BI Desktop |
| `powerbi_desktop_reports` | PBIX report për dataset dhe statusi i gjenerimit |

### 6.2 Skema e users në deploy

Sipas databazës Aiven të përdorur nga aplikacioni, fushat relevante janë:

```text
id
firstName
lastName
email
password
role
createdAt
updatedAt
```

Nuk përdoret `users.name`. Kur nevojitet emri i shfaqur, login-i ndërton:

```sql
CONCAT(u.firstName, ' ', u.lastName) AS name
```

Signup përdor këtë INSERT:

```sql
INSERT INTO users (
    firstName,
    lastName,
    email,
    password,
    role
) VALUES (%s, %s, %s, %s, %s)
```

### 6.3 View-t për Power BI

`init_db.py` përmban view-t:

- `v_company_kpis`;
- `v_company_timeseries`;
- `v_company_category`;
- `v_company_city`;
- `v_company_status`;
- `v_company_payment`;
- `v_company_predictions`.

Këto ofrojnë agregime për KPI, seri kohore, kategori, qytete, status, pagesa dhe prediction-e.

### 6.4 Connection pool dhe cursor handling

`app.py` përdor pool me pesë connections. `run_query()`:

1. merr connection nga pool;
2. krijon cursor dictionary;
3. ekzekuton query-n;
4. lexon `fetchone()` ose `fetchall()` sipas nevojës;
5. mbyll cursor-in;
6. kthen connection-in me `conn.close()`.

Në `_load_user_columns()`, query metadata është:

```sql
SELECT * FROM users LIMIT 0
```

Rezultati lexohet plotësisht me `cursor.fetchall()` para `cursor.close()`. Kjo eliminon gabimin MySQL `Unread result found` dhe ruan arkitekturën ekzistuese të pool-it.

---

## 7. Machine learning

### 7.1 Pipeline kryesor

`ml_pipeline.py` realizon:

- validim të input-it;
- ndarje kronologjike train/test;
- imputim median;
- standardizim kur nevojitet;
- feature engineering nga data;
- lag features 1, 7 dhe 30;
- rolling features 7 dhe 30;
- trainim të disa modeleve;
- vlerësim me MAE, MSE, RMSE dhe R²;
- TimeSeriesSplit cross-validation;
- kontroll të overfitting-ut;
- ruajtje modeli dhe metadata.

Modelet e krahasuara janë:

- Linear Regression;
- Ridge Regression;
- Lasso Regression;
- Decision Tree Regressor;
- Random Forest Regressor;
- Gradient Boosting Regressor.

Modeli me performancën më të mirë në test ruhet për target-in përkatës.

### 7.2 Universal analysis

`universal_analysis.py` nuk supozon emra të fiksuar kolonash. Ai:

- dallon numeric, date, categorical, boolean, text dhe id;
- zgjedh target-e numerike me të paktën variacion të mjaftueshëm;
- ndërton regression/classification sections;
- analizon trendet kur ka datë;
- gjeneron insight-e të bazuara në të dhënat reale.

Kjo shtresë është e rëndësishme për file-et jashtë formatit financiar standard.

### 7.3 Ruajtja e modeleve

Modelet ruhen sipas përdoruesit në formën:

```text
models/user_<user_id>_<model_type>_model.pkl
models/user_<user_id>_<model_type>_meta.json
```

Nëse modeli mungon pas restart-it, aplikacioni përpiqet ta ritrajnojë nga dataset-i aktiv.

---

## 8. Power BI Desktop

Power BI përdor workflow lokal:

1. Përdoruesi ngarkon dataset-in.
2. Aplikacioni krijon data tables të pastruara dhe analiza CSV.
3. Aplikacioni kopjon template-in ekzistues `.pbix` në folder privat.
4. Përdoruesi shkarkon PBIX-in dhe/ose workbook-un Excel.
5. Power BI Desktop rifreskon burimet lokale.

Burimet përmbajnë zakonisht:

- `Cleaned_Data`;
- `Predictions`;
- `Prediction_vs_Actual`;
- `KPI_Summary`;
- `Monthly_Analysis`;
- `Category_Analysis`;
- analiza sipas City, Payment, Company, Department dhe Status;
- `README` me udhëzime.

Izolimi bëhet me `user_id`, `uploaded_file_id` dhe folder token të padepërtueshëm. Një përdorues nuk duhet të marrë resource të një përdoruesi tjetër.

### Kufizim teknik

Flask/Python nuk krijon nga zero një raport native `.pbix` me modelin, DAX-in, relationships dhe faqet e visualizimeve. Projekti kopjon një template të krijuar paraprakisht në Power BI Desktop dhe gjeneron burimet e dataset-it.

---

## 9. Siguria dhe izolimi i të dhënave

Masat e implementuara janë:

- `login_required` në route-t private;
- session cookies `HttpOnly` dhe `SameSite=Lax`;
- `Secure` në production sipas konfigurimit;
- CSRF token për kërkesat state-changing;
- password hash me Werkzeug;
- validim bazë të email-it;
- `secure_filename()` për file names;
- kufizim madhësie dhe extensions;
- query-t e dataset-it filtrohen me `user_id` dhe dataset ID;
- resource folder-i i Power BI përdor token random;
- TLS verification për MySQL/Aiven kur jepet CA;
- error handlers për 404, 413, database error dhe 500.

`SECRET_KEY` duhet të vendoset si environment variable në production. Nëse mungon, aplikacioni krijon një vlerë të përkohshme dhe session-et mund të invalidizohen pas restart-it.

---

## 10. Konfigurimi dhe deploy-i

Environment variables kryesore:

```text
SECRET_KEY
DB_HOST
DB_PORT
DB_USER
DB_PASSWORD
DB_NAME
DB_SSL_CA
MAX_CONTENT_LENGTH
ALLOWED_EXTENSIONS
POWERBI_TEMPLATE
```

Për instalim lokal:

```powershell
python -m pip install -r requirements.txt
python init_db.py
python app.py
```

Aplikacioni hapet zakonisht në:

```text
http://127.0.0.1:5000
```

Render përdor:

```text
Build:  pip install -r requirements.txt
Start:  gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120 app:app
Health: /healthz
```

Worker-i i vetëm është zgjedhur sepse cache-i i session/model state është process-local. Për shkallëzim real duhet cache e përbashkët ose ruajtje e plotë e state-it jashtë procesit.

---

## 11. Testimi dhe verifikimi

Testet e repository-t përfshijnë:

```powershell
python test_data_mapping.py
python test_ml_pipeline.py
python test_universal_analysis.py
python verify_ml_integration.py
python check_mysql.py
```

Kontroll sintakse:

```powershell
python -m py_compile app.py init_db.py data_mapping.py ml_pipeline.py universal_analysis.py
```

Checklist manuale e rekomanduar:

1. Signup me kompani të re.
2. Logout dhe login përsëri.
3. Upload CSV/XLSX/JSON standard.
4. Upload Excel pa header, si `uncleaned.xlsx`.
5. Kontrollo preview-n dhe confidence badges.
6. Kontrollo Analytics dhe totalet.
7. Kontrollo `financial_data` dhe `dataset_rows` në MySQL.
8. Testo prediction me të paktën 12 data të vlefshme.
9. Testo risk classification.
10. Gjenero Power BI resources dhe rifresko template-in në Desktop.
11. Krijo përdorues të dytë dhe verifiko izolimin e dataset-eve.

Në kontrollin e fundit statik të ndryshimeve, `git diff --check` kaloi pa gabime. Ekzekutimi lokal i testeve Python duhet të bëhet në një interpreter/virtual environment të aksesueshëm.

---

## 12. Kufizime dhe çështje për t’u adresuar

### 12.1 Dallimi i skemës së databazës

`init_db.py` përmban ende një DDL historike për variantin e vjetër të tabelës `users` me fusha si `name`, `password_hash` dhe `company_id`, ndërsa databaza Aiven e deploy-it përdor variantin me `firstName`, `lastName`, `password`, `role`, `createdAt` dhe `updatedAt`.

Ky dallim duhet të harmonizohet për instalime të reja. Nuk duhet të bëhet ndryshim destruktiv në databazën ekzistuese pa backup dhe migration plan.

### 12.2 Prediction pa datë

Analytics mund të funksionojë për dataset-e gjenerike pa datë, por forecast-i kohor kërkon një date column. Kjo është sjellje e qëllimshme për të mos prodhuar parashikime me data të shpikura.

### 12.3 Storage në Render

Rreshtat ruhen në MySQL, por file-t e upload-it, modelet dhe resource-t Power BI ruhen në filesystem lokal. Në platforma me disk ephemeral, këto file mund të humbasin pas redeploy/restart. Për production të plotë rekomandohet object storage dhe/ose persistent disk.

### 12.4 PBIX

Visual layouts dhe DAX duhet të mirëmbahen në template-in Power BI Desktop. Aplikacioni gjeneron të dhënat dhe e kopjon template-in, por nuk manipulon internals proprietary të PBIX-it.

---

## 13. Përfundim

FinSight AI mbulon ciklin kryesor të një platforme moderne të analizës financiare: autentikim, ingest të dhënash, pastrim, mapping, ruajtje, dashboard, forecasting, risk analysis dhe Power BI export.

Pikat më të rëndësishme të implementimit janë:

- të dhënat standarde dhe jo-standarde ruhen pa humbje;
- dataset-et filtrohen sipas përdoruesit;
- cursor-at MySQL konsumojnë rezultatet para mbylljes;
- Excel-et pa header trajtohen pa humbur rreshtin e parë;
- confidence status është i dukshëm në UI;
- dataset-et pa datë mund të vizualizohen, ndërsa forecast-i kohor aktivizohet vetëm kur ka histori të vlefshme;
- Power BI përdor template lokal dhe resources private për dataset.

Dokumentet plotësuese janë `README.md` për instalimin e shpejtë dhe `powerbi_setup.md` për workflow-in Power BI Desktop.
