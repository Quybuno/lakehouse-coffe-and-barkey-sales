"""Trino client wrapper + cached DataFrame loader."""
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
TRINO_SCHEMA = os.getenv("TRINO_SCHEMA", "gold")

# Các lỗi transient xảy ra khi coordinator vừa start / đang GC dài —
# không phải lỗi query, cứ retry là qua.
#   NO_NODES_AVAILABLE: worker chưa register vào node-manager (race startup).
#   SERVER_STARTING_UP: Trino nhận request quá sớm.
#   REMOTE_TASK_ERROR / REMOTE_HOST_GONE: node ping lag do GC / network.
_RETRYABLE_ERRORS = {
    "NO_NODES_AVAILABLE",
    "SERVER_STARTING_UP",
    "REMOTE_TASK_ERROR",
    "REMOTE_HOST_GONE",
}
_MAX_RETRIES = 4  # tổng 4 lần thử, backoff 0.5s → 1s → 2s → 4s ≈ 7.5s
_BASE_DELAY = 0.5


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
    """
    Execute SQL và trả về DataFrame. Cache 5 phút theo (sql, params).

    `params` là list/tuple positional — trino-python-client chỉ hỗ trợ
    positional placeholder `?` (KHÔNG hỗ trợ named-style `%(name)s`).

    Tự retry các lỗi transient (NO_NODES_AVAILABLE / SERVER_STARTING_UP / …)
    với exponential backoff — đây là các lỗi hay gặp ngay sau khi container
    `trino` restart: `/v1/info` đã báo starting=false nhưng node-manager
    chưa register xong worker local (~2–5s race). Streamlit không cache
    exception nên retry ở đây là đủ để user không bao giờ thấy lỗi này.
    """
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
    except Exception as e:  # noqa: BLE001
        return False, f"Connection error: {e}"
