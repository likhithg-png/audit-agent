"""
Postgres connection. Used by the loader and by Tools._raw()/Tools.sql().
"""
import psycopg
from psycopg.rows import dict_row

from .config import DATABASE_URL, DATABASE_URL_READONLY


def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set -- check .env (docker compose up -d first)")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def connect_readonly():
    """Connects as audit_readonly (sql/008_readonly_role.sql) -- SELECT-only is a database
    permission here, not just a check on the query text. Used only for SQL an LLM writes
    itself (Tools.sql()); every other query in this codebase is developer-written and keeps
    using connect()."""
    if not DATABASE_URL_READONLY:
        raise RuntimeError("DATABASE_URL_READONLY is not set -- check .env")
    return psycopg.connect(DATABASE_URL_READONLY, row_factory=dict_row)
