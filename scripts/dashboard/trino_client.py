from __future__ import annotations
import logging
import os
import time
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
# Không set default session schema: nếu chưa chạy `gold_layer.py` thì namespace
# `iceberg.gold` chưa tồn tại — Trino lỗi SCHEMA_NOT_FOUND khi session mặc định = gold.
# Mọi query trong `queries.py` dùng tên đủ `iceberg.gold.*`.

_RETRYABLE_ERRORS = {
    "NO_NODES_AVAILABLE",
    "SERVER_STARTING_UP",
    "REMOTE_TASK_ERROR",
    "REMOTE_HOST_GONE",
}
_MAX_RETRIES = 4  
_BASE_DELAY = 0.5


def get_connection():
    return connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        user=TRINO_USER,
        catalog=TRINO_CATALOG,
    )


def _execute_once(sql: str, params: Sequence[Any] | None) -> pd.DataFrame:
    with get_connection() as conn:
        cur = conn.cursor()
        if params:
            cur.execute(sql, list(params))
        else:
            cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=300, show_spinner=False)
def run_query(sql: str, params: Sequence[Any] | None = None) -> pd.DataFrame:

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return _execute_once(sql, params)
        except TrinoQueryError as e:
            last_exc = e
            if e.error_name not in _RETRYABLE_ERRORS or attempt == _MAX_RETRIES - 1:
                raise
            delay = _BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Trino %s (attempt %d/%d) — retry sau %.1fs: %s",
                e.error_name,
                attempt + 1,
                _MAX_RETRIES,
                delay,
                e.message,
            )
            time.sleep(delay)
    # Không bao giờ chạm dòng này (raise ở vòng cuối), nhưng giữ cho mypy.
    assert last_exc is not None
    raise last_exc


def ping() -> tuple[bool, str]:
    """Quick health-check: SELECT 1 để phân biệt lỗi config vs lỗi schema."""
    try:
        run_query.clear()
        df = run_query("SELECT 1 AS ok")
        return bool(len(df)), "Trino OK"
    except TrinoQueryError as e:
        return False, f"Trino query error: {e.message}"
    except Exception as e: 
        return False, f"Connection error: {e}"
