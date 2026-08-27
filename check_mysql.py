"""
check_mysql.py
==============
Diagnostic helper for MySQL connection problems.

Reads the Aiven connection settings from the environment / .env file
and performs a non-mutating connection check.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

import mysql.connector
from mysql.connector import errorcode


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _db_env(name: str, default: str = "") -> str:
    aliases = {
        "DB_HOST": ("DB_HOST", "MYSQL_HOST"),
        "DB_PORT": ("DB_PORT", "MYSQL_PORT"),
        "DB_USER": ("DB_USER", "MYSQL_USER"),
        "DB_PASSWORD": ("DB_PASSWORD", "MYSQL_PASSWORD"),
        "DB_NAME": ("DB_NAME", "MYSQL_DATABASE"),
        "DB_SSL_CA": ("DB_SSL_CA", "MYSQL_SSL_CA"),
    }
    for candidate in aliases.get(name, (name,)):
        value = _env(candidate)
        if value:
            return value
    return default


def _try(host: str, port: int, user: str, password: str, database: str, ssl_ca: str) -> tuple[bool, str]:
    try:
        config = dict(
            host=host, port=port, user=user, password=password, database=database,
            ssl_verify_cert=True, ssl_verify_identity=True,
            connection_timeout=5,
        )
        if ssl_ca:
            config["ssl_ca"] = ssl_ca
        conn = mysql.connector.connect(**config)
        cur = conn.cursor()
        cur.execute("SELECT VERSION(), CURRENT_USER()")
        v, u = cur.fetchone()
        cur.close()
        conn.close()
        return True, f"OK (server={v}, user={u})"
    except mysql.connector.Error as exc:
        if exc.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            return False, "Access denied (wrong user or password)"
        if exc.errno == errorcode.ER_BAD_DB_ERROR:
            return False, "Database does not exist"
        if exc.errno in (errorcode.CR_CONN_HOST_ERROR, errorcode.CR_UNKNOWN_HOST):
            return False, "Cannot reach MySQL host"
        return False, str(exc)
    except Exception as exc:
        return False, f"Network error: {exc}"


def main() -> int:
    required = ("DB_HOST", "DB_USER", "DB_NAME")
    missing = [name for name in required if not _db_env(name)]
    if missing:
        print("Missing required database environment variable(s): " + ", ".join(missing))
        return 1
    host = _db_env("DB_HOST")
    port = int(_db_env("DB_PORT", "3306"))
    user = _db_env("DB_USER")
    password = _db_env("DB_PASSWORD")
    database = _db_env("DB_NAME")
    ssl_ca = _db_env("DB_SSL_CA")

    print("=" * 60)
    print(" FinSight AI - MySQL diagnostic")
    print("=" * 60)
    print(f"  Host:     {host}")
    print(f"  Port:     {port}")
    print(f"  User:     {user}")
    print(f"  Database: {database}")
    print("  SSL:      certificate and hostname verification enabled")
    print()
    print("Step 1 - trying the credentials from .env ...")
    ok, msg = _try(host, port, user, password, database, ssl_ca)
    print(f"  -> {msg}")
    if ok:
        print()
        print("Connection works. You can start the Flask app with `python app.py`.")
        return 0
    print("Check DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, and DB_NAME; optionally set DB_SSL_CA.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
