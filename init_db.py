"""
init_db.py
==========
Creates the FinSight AI database (finsightai) and ALL required tables
from scratch. Safe to re-run: every CREATE uses IF NOT EXISTS.

Usage:
    python init_db.py

Reads connection settings from environment / .env file. No secrets
are hard-coded.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    # Load local development variables without overriding deployment variables.
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    # python-dotenv is optional; the app still works without it
    pass

import mysql.connector
from mysql.connector import errorcode


# ---------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _db_config() -> dict[str, object]:
    required = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")
    missing = [name for name in required if not _env(name)]
    if missing:
        raise RuntimeError("Missing required database environment variable(s): " + ", ".join(missing))
    config = {
        "host": _env("DB_HOST"),
        "port": int(_env("DB_PORT")),
        "user": _env("DB_USER"),
        "password": _env("DB_PASSWORD"),
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    }
    ssl_ca = _env("DB_SSL_CA")
    if ssl_ca:
        config["ssl_ca"] = ssl_ca
    return config


DB_NAME = _env("DB_NAME")


# ---------------------------------------------------------------
# Step 1 - ensure database exists
# ---------------------------------------------------------------
def ensure_database() -> None:
    """Connect to the MySQL server (no specific database) and create the
    finsightai database if it does not yet exist."""
    db_config = _db_config()
    print(f"[init_db] Connecting to MySQL at {db_config['host']}:{db_config['port']} ...")
    conn = mysql.connector.connect(**db_config)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
            f"DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
        )
        conn.commit()
        print(f"[init_db] Database `{DB_NAME}` is ready.")
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------
# Step 2 - create all tables
# ---------------------------------------------------------------
TABLES: list[str] = [
    # ---------------- companies ----------------
    """
    CREATE TABLE IF NOT EXISTS companies (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        company_name  VARCHAR(255) NOT NULL UNIQUE,
        created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_companies_name (company_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ---------------- users ----------------
    """
    CREATE TABLE IF NOT EXISTS users (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        name            VARCHAR(120)  NOT NULL,
        email           VARCHAR(190)  NOT NULL UNIQUE,
        password_hash   VARCHAR(255)  NOT NULL,
        company_id      INT           NOT NULL,
        created_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_users_company FOREIGN KEY (company_id)
            REFERENCES companies(id) ON DELETE CASCADE,
        INDEX idx_users_company (company_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ---------------- uploaded_files ----------------
    """
    CREATE TABLE IF NOT EXISTS uploaded_files (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        user_id         INT NOT NULL,
        company_id      INT NOT NULL,
        version         INT          NOT NULL DEFAULT 1,
        original_name   VARCHAR(255) NOT NULL,
        stored_name     VARCHAR(255) NOT NULL,
        file_size       BIGINT       NOT NULL DEFAULT 0,
        rows_imported   INT          NOT NULL DEFAULT 0,
        status          VARCHAR(40)  NOT NULL DEFAULT 'uploaded',
        created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_files_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE,
        CONSTRAINT fk_files_company FOREIGN KEY (company_id)
            REFERENCES companies(id) ON DELETE CASCADE,
        INDEX idx_files_company (company_id),
        INDEX idx_files_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ---------------- financial_data (flexible columns) ----------------
    """
    CREATE TABLE IF NOT EXISTS financial_data (
        id                INT AUTO_INCREMENT PRIMARY KEY,
        user_id           INT NOT NULL,
        company_id        INT NOT NULL,
        uploaded_file_id  INT NOT NULL,
        tx_date           DATE            NULL,
        transaction_id    VARCHAR(120)    NULL,
        description       TEXT            NULL,
        amount            DECIMAL(18, 2)  NULL,
        revenue           DECIMAL(18, 2)  NULL,
        expenses          DECIMAL(18, 2)  NULL,
        profit            DECIMAL(18, 2)  NULL,
        tx_type           VARCHAR(80)     NULL,
        category          VARCHAR(120)    NULL,
        payment_method    VARCHAR(80)     NULL,
        department        VARCHAR(120)    NULL,
        city              VARCHAR(120)    NULL,
        status            VARCHAR(80)     NULL,
        created_at        TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_fin_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE,
        CONSTRAINT fk_fin_company FOREIGN KEY (company_id)
            REFERENCES companies(id) ON DELETE CASCADE,
        CONSTRAINT fk_fin_file FOREIGN KEY (uploaded_file_id)
            REFERENCES uploaded_files(id) ON DELETE CASCADE,
        INDEX idx_fin_company_date (company_id, tx_date),
        INDEX idx_fin_file (uploaded_file_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ---------------- predictions ----------------
    """
    CREATE TABLE IF NOT EXISTS predictions (
        prediction_id     INT AUTO_INCREMENT PRIMARY KEY,
        user_id           INT NOT NULL,
        company_id        INT NOT NULL,
        uploaded_file_id  INT NULL,
        prediction_type   VARCHAR(40) NOT NULL,
        prediction_date   DATE        NOT NULL,
        actual_value      DECIMAL(18, 2) NULL,
        predicted_value   DECIMAL(18, 2) NOT NULL,
        prediction_error  DECIMAL(18, 2) NULL,
        model_name        VARCHAR(80)  NOT NULL DEFAULT 'linear_regression',
        created_at        TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_pred_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE,
        CONSTRAINT fk_pred_company FOREIGN KEY (company_id)
            REFERENCES companies(id) ON DELETE CASCADE,
        CONSTRAINT fk_pred_file FOREIGN KEY (uploaded_file_id)
            REFERENCES uploaded_files(id) ON DELETE SET NULL,
        INDEX idx_pred_company (company_id),
        INDEX idx_pred_type (prediction_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ---------------- dashboard_history ----------------
    # ---------------- risk_classifications ----------------
    """
    CREATE TABLE IF NOT EXISTS risk_classifications (
        risk_id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL, company_id INT NOT NULL, uploaded_file_id INT NOT NULL,
        classification_date DATE NOT NULL, risk_level VARCHAR(20) NOT NULL,
        revenue DECIMAL(18,2) NULL, expenses DECIMAL(18,2) NULL,
        profit DECIMAL(18,2) NULL, amount DECIMAL(18,2) NULL,
        explanation TEXT NULL, model_name VARCHAR(80) NOT NULL DEFAULT 'decision_tree_classifier',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_risk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        CONSTRAINT fk_risk_file FOREIGN KEY (uploaded_file_id) REFERENCES uploaded_files(id) ON DELETE CASCADE,
        INDEX idx_risk_user_file_date (user_id, uploaded_file_id, classification_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ---------------- dashboard_history ----------------
    """
    CREATE TABLE IF NOT EXISTS dashboard_history (
        id                INT AUTO_INCREMENT PRIMARY KEY,
        user_id           INT NOT NULL,
        company_id        INT NOT NULL,
        event_type        VARCHAR(40) NOT NULL,
        event_title       VARCHAR(255) NOT NULL,
        file_id           INT NULL,
        prediction_id     INT NULL,
        status            VARCHAR(40) NOT NULL DEFAULT 'ok',
        details           TEXT        NULL,
        created_at        TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_hist_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE,
        CONSTRAINT fk_hist_company FOREIGN KEY (company_id)
            REFERENCES companies(id) ON DELETE CASCADE,
        INDEX idx_hist_company_time (company_id, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ---------------- company_powerbi (per-company link) ----------------
    """
    CREATE TABLE IF NOT EXISTS company_powerbi (
        company_id      INT PRIMARY KEY,
        powerbi_url     VARCHAR(1024) NOT NULL,
        report_name     VARCHAR(255) NULL,
        created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
        CONSTRAINT fk_pbi_company FOREIGN KEY (company_id)
            REFERENCES companies(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # ---------------- user_powerbi_resources (local Desktop workflow) ----------------
    """
    CREATE TABLE IF NOT EXISTS user_powerbi_resources (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        user_id         INT NOT NULL UNIQUE,
        folder_token    VARCHAR(64) NOT NULL UNIQUE,
        financial_csv   VARCHAR(255) NULL,
        predictions_csv VARCHAR(255) NULL,
        pbix_filename   VARCHAR(255) NULL,
        generated_at    TIMESTAMP NULL,
        created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        CONSTRAINT fk_pbi_resource_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE,
        INDEX idx_pbi_resource_token (folder_token)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # ---------------- per-dataset Power BI Desktop artifacts ----------------
    """
    CREATE TABLE IF NOT EXISTS powerbi_desktop_reports (
        report_id         INT AUTO_INCREMENT PRIMARY KEY,
        user_id           INT NOT NULL,
        uploaded_file_id  INT NOT NULL,
        pbix_filename     VARCHAR(255) NOT NULL,
        status             VARCHAR(40) NOT NULL DEFAULT 'generated',
        created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        CONSTRAINT fk_desktop_report_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE,
        CONSTRAINT fk_desktop_report_file FOREIGN KEY (uploaded_file_id)
            REFERENCES uploaded_files(id) ON DELETE CASCADE,
        UNIQUE KEY uq_desktop_report_file (uploaded_file_id),
        INDEX idx_desktop_report_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

# Safe to re-run upgrades for installations created by earlier versions.
MIGRATIONS: list[str] = [
    # Column migrations are applied through `add_column_if_missing` below so
    # this also works on MySQL versions without ADD COLUMN IF NOT EXISTS.
    "CREATE INDEX idx_fin_user_date ON financial_data (user_id, tx_date)",
    "CREATE INDEX idx_pred_user_created ON predictions (user_id, created_at)",
    "CREATE INDEX idx_hist_user_time ON dashboard_history (user_id, created_at)",
]


# SQL views that Power BI can read directly.
# Each view is pre-filtered to expect a company_id parameter via the URL filter.
VIEWS: list[str] = [
    # Single-row view per company with aggregate KPIs - perfect for KPI cards.
    """
    CREATE OR REPLACE VIEW v_company_kpis AS
    SELECT
        c.id                                            AS company_id,
        c.company_name,
        COALESCE(SUM(fd.revenue),  0)                  AS total_revenue,
        COALESCE(SUM(fd.expenses), 0)                  AS total_expenses,
        COALESCE(SUM(fd.profit),   0)                  AS total_profit,
        COUNT(fd.id)                                    AS total_rows
    FROM companies c
    LEFT JOIN financial_data fd ON fd.company_id = c.id
    GROUP BY c.id, c.company_name
    """,

    # Time-series view (one row per company per date) for trend visuals.
    """
    CREATE OR REPLACE VIEW v_company_timeseries AS
    SELECT
        fd.company_id,
        c.company_name,
        fd.tx_date,
        COALESCE(SUM(fd.revenue),  0) AS revenue,
        COALESCE(SUM(fd.expenses), 0) AS expenses,
        COALESCE(SUM(fd.profit),   0) AS profit,
        COALESCE(SUM(fd.amount),   0) AS amount
    FROM financial_data fd
    JOIN companies c ON c.id = fd.company_id
    GROUP BY fd.company_id, c.company_name, fd.tx_date
    """,

    # By-category view (Amount, Revenue, Expenses, Profit grouped by category).
    """
    CREATE OR REPLACE VIEW v_company_category AS
    SELECT
        fd.company_id,
        c.company_name,
        COALESCE(fd.category, 'Uncategorized')           AS category,
        COALESCE(SUM(fd.amount),  0)                     AS amount,
        COALESCE(SUM(fd.revenue), 0)                     AS revenue,
        COALESCE(SUM(fd.expenses),0)                     AS expenses,
        COALESCE(SUM(fd.profit),  0)                     AS profit
    FROM financial_data fd
    JOIN companies c ON c.id = fd.company_id
    GROUP BY fd.company_id, c.company_name, fd.category
    """,

    # By-city / status / payment_method distribution views.
    """
    CREATE OR REPLACE VIEW v_company_city AS
    SELECT
        fd.company_id,
        c.company_name,
        COALESCE(fd.city, 'Unknown') AS city,
        COUNT(*) AS transactions,
        COALESCE(SUM(fd.amount), 0) AS amount
    FROM financial_data fd
    JOIN companies c ON c.id = fd.company_id
    GROUP BY fd.company_id, c.company_name, fd.city
    """,

    """
    CREATE OR REPLACE VIEW v_company_status AS
    SELECT
        fd.company_id,
        c.company_name,
        COALESCE(fd.status, 'Unknown') AS status,
        COUNT(*) AS transactions
    FROM financial_data fd
    JOIN companies c ON c.id = fd.company_id
    GROUP BY fd.company_id, c.company_name, fd.status
    """,

    """
    CREATE OR REPLACE VIEW v_company_payment AS
    SELECT
        fd.company_id,
        c.company_name,
        COALESCE(fd.payment_method, 'Unknown') AS payment_method,
        COUNT(*) AS transactions,
        COALESCE(SUM(fd.amount), 0) AS amount
    FROM financial_data fd
    JOIN companies c ON c.id = fd.company_id
    GROUP BY fd.company_id, c.company_name, fd.payment_method
    """,

    # Predictions (prediction vs actual = financial_data on same date).
    """
    CREATE OR REPLACE VIEW v_company_predictions AS
    SELECT
        p.company_id,
        c.company_name,
        p.prediction_id,
        p.prediction_type,
        p.prediction_date,
        p.predicted_value,
        p.model_name,
        p.created_at,
        p.uploaded_file_id
    FROM predictions p
    JOIN companies c ON c.id = p.company_id
    """,
]


def create_tables() -> None:
    """Create every table and view inside the finsightai database."""
    conn = mysql.connector.connect(database=DB_NAME, **_db_config())
    try:
        cursor = conn.cursor()
        for ddl in TABLES:
            cursor.execute(ddl)
        add_column_if_missing(cursor, "uploaded_files", "version",
                              "INT NOT NULL DEFAULT 1 AFTER company_id")
        add_column_if_missing(cursor, "predictions", "actual_value",
                              "DECIMAL(18, 2) NULL AFTER prediction_date")
        add_column_if_missing(cursor, "predictions", "prediction_error",
                              "DECIMAL(18, 2) NULL AFTER predicted_value")
        for ddl in MIGRATIONS:
            try:
                cursor.execute(ddl)
            except mysql.connector.Error as exc:
                # MySQL reports a duplicate index when upgrading an existing DB.
                if exc.errno != errorcode.ER_DUP_KEYNAME:
                    raise
        for ddl in VIEWS:
            cursor.execute(ddl)
        conn.commit()
        print(f"[init_db] {len(TABLES)} tables and {len(VIEWS)} views created/verified.")
    finally:
        cursor.close()
        conn.close()


def add_column_if_missing(cursor, table: str, column: str, definition: str) -> None:
    """Apply a column migration on both MySQL and older MariaDB versions."""
    cursor.execute(
        """SELECT COUNT(*) AS present
           FROM information_schema.columns
           WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s""",
        (table, column),
    )
    if not cursor.fetchone()[0]:
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")


# ---------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------
def main() -> int:
    try:
        ensure_database()
        create_tables()
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print(
                "[init_db] ERROR: MySQL access denied. "
                "Check DB_USER and DB_PASSWORD in your environment configuration."
            )
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print(f"[init_db] ERROR: Database `{DB_NAME}` does not exist and could not be created.")
        else:
            print(f"[init_db] ERROR: {err}")
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"[init_db] Unexpected error: {exc}")
        return 1

    print("[init_db] DONE. You can now start the Flask app with: python app.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
