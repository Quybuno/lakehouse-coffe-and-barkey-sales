"""Trino client wrapper + cached DataFrame loader."""
from __future__ import annotations

import logging
import os
from typing import Any, Sequence

import pandas as pd
import streamlit as st
from trino.dbapi import connect
from trino.exceptions import TrinoQueryError

logger = logging.getLogger(__name__)

TRINO_HOST = os.getenv("TRINO_HOST", "trino")
TRINO_PORT = int(os.getenv("TRINO_PORT", "8080"))
TRINO_USER = os.getenv("TRINO_USER", "dashboard")
TRINO_CATALOG = os.getenv("TRINO_CATALOG", "iceberg")
TRINO_SCHEMA = os.getenv("TRINO_SCHEMA", "gold")


def get_connection():
    """
    Tạo connection mỗi lần query — Trino REST-over-HTTP rất nhẹ; connection pool
    không cần thiết ở scale đồ án và đơn giản hóa retry khi coordinator restart.
    """
    return connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        user=TRINO_USER,
        catalog=TRINO_CATALOG,
        schema=TRINO_SCHEMA,
    )


@st.cache_data(ttl=300, show_spinner=False)
def run_query(sql: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
    """
    Execute SQL và trả về DataFrame. Cache 5 phút theo (sql, params).

    `params` là list/tuple positional — trino-python-client chỉ hỗ trợ
    positional placeholder `?` (KHÔNG hỗ trợ named-style `%(name)s`).
    """
    with get_connection() as conn:
        cur = conn.cursor()
        if params:
            cur.execute(sql, list(params))
        else:
            cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=cols)


def ping() -> tuple[bool, str]:
    """Quick health-check: SELECT 1 để phân biệt lỗi config vs lỗi schema."""
    try:
        run_query.clear()
        df = run_query("SELECT 1 AS ok")
        return bool(len(df)), "Trino OK"
    except TrinoQueryError as e:
        return False, f"Trino query error: {e.message}"
    except Exception as e:  # noqa: BLE001
        return False, f"Connection error: {e}"
