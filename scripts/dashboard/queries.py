from __future__ import annotations
from datetime import date
import pandas as pd
from trino_client import run_query



def _date_clause(start_date: date | None, end_date: date | None) -> str:
    """Build mệnh đề WHERE theo order_date; bỏ qua None."""
    conditions: list[str] = []
    if start_date:
        conditions.append(f"f.order_date >= DATE '{start_date.isoformat()}'")
    if end_date:
        conditions.append(f"f.order_date <= DATE '{end_date.isoformat()}'")
    return " AND ".join(conditions)


def _store_clause(store_keys: list[int] | None) -> str:
    """IN filter theo store_key. Inline literal int (đã cast) — không bind params."""
    if not store_keys:
        return ""
    ids = ",".join(str(int(k)) for k in store_keys)
    return f"f.store_key IN ({ids})"


def _where(start_date, end_date, stores, extra: str = "") -> str:
    parts = [
        x
        for x in (_date_clause(start_date, end_date), _store_clause(stores), extra)
        if x
    ]
    return ("WHERE " + " AND ".join(parts)) if parts else ""


# ---------- Catalog metadata ------------------------------------------------


def list_tables() -> pd.DataFrame:
    return run_query(
        "SELECT table_name FROM iceberg.information_schema.tables "
        "WHERE table_schema = 'gold' ORDER BY table_name"
    )


def get_date_bounds() -> tuple[date | None, date | None]:
    df = run_query(
        "SELECT MIN(order_date) AS start_date, MAX(order_date) AS end_date "
        "FROM iceberg.gold.fact_orders"
    )
    if df.empty or df.iloc[0]["start_date"] is None:
        return None, None
    return df.iloc[0]["start_date"], df.iloc[0]["end_date"]


def get_stores() -> pd.DataFrame:
    return run_query(
        "SELECT CAST(id AS INTEGER) AS store_key, name AS store_name "
        "FROM iceberg.gold.dim_store ORDER BY name"
    )


# ---------- KPI + biểu đồ ---------------------------------------------------


def kpis(start_date, end_date, stores) -> pd.DataFrame:
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      COUNT(DISTINCT f.order_id)            AS orders,
      COUNT(DISTINCT f.customer_id)         AS customers,
      COALESCE(SUM(f.subtotal), 0)          AS revenue,
      COALESCE(SUM(f.quantity), 0)          AS products,
      CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
           ELSE COALESCE(SUM(f.subtotal), 0) * 1.0 / COUNT(DISTINCT f.order_id)
      END                                   AS aov
    FROM iceberg.gold.fact_orders f
    {where_sql}
    """
    return run_query(sql)


def revenue_by_day(start_date, end_date, stores) -> pd.DataFrame:
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      f.order_date                       AS day,
      SUM(f.subtotal)                    AS revenue,
      COUNT(DISTINCT f.order_id)         AS orders
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_date
    ORDER BY f.order_date
    """
    return run_query(sql)


def top_products(start_date, end_date, stores, top_n: int = 10) -> pd.DataFrame:
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      p.name                     AS product,
      SUM(f.quantity)            AS quantity,
      SUM(f.subtotal)            AS revenue
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_products p ON p.id = f.product_key
    {where_sql}
    GROUP BY p.name
    ORDER BY revenue DESC
    LIMIT {int(top_n)}
    """
    return run_query(sql)


def revenue_by_store(start_date, end_date, stores) -> pd.DataFrame:
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      s.name                               AS store,
      SUM(f.subtotal)                      AS revenue,
      COUNT(DISTINCT f.order_id)           AS orders,
      CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
           ELSE SUM(f.subtotal) * 1.0 / COUNT(DISTINCT f.order_id)
      END                                  AS aov
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_store s ON s.id = f.store_key
    {where_sql}
    GROUP BY s.name
    ORDER BY revenue DESC
    """
    return run_query(sql)


def revenue_by_payment(start_date, end_date, stores) -> pd.DataFrame:
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      pm.method_name                      AS method,
      SUM(f.subtotal)                     AS revenue,
      COUNT(DISTINCT f.order_id)          AS orders
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_payment pm ON pm.id = f.payment_method_key
    {where_sql}
    GROUP BY pm.method_name
    ORDER BY revenue DESC
    """
    return run_query(sql)


def heatmap_hour_dow(start_date, end_date, stores) -> pd.DataFrame:
    """Heatmap doanh thu theo (thứ trong tuần × giờ)."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      f.order_dow    AS dow,
      f.order_hour   AS hour,
      SUM(f.subtotal) AS revenue
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_dow, f.order_hour
    ORDER BY f.order_dow, f.order_hour
    """
    return run_query(sql)


def suggestion_impact(start_date, end_date, stores) -> pd.DataFrame:
    """So sánh doanh thu từ dòng là gợi ý vs không phải gợi ý."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      CASE WHEN f.is_suggestion THEN 'Gợi ý' ELSE 'Khách tự chọn' END AS source,
      SUM(f.subtotal)            AS revenue,
      SUM(f.quantity)            AS quantity,
      COUNT(DISTINCT f.order_id) AS orders,
      AVG(f.subtotal)            AS avg_line_value
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.is_suggestion
    ORDER BY revenue DESC
    """
    return run_query(sql)


def suggestion_kpis(start_date, end_date, stores) -> pd.DataFrame:
    """KPI tổng hợp cho tab gợi ý — một row duy nhất với tỉ lệ đóng góp và uplift."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    WITH agg AS (
      SELECT
        COUNT(DISTINCT f.order_id)                                                    AS total_orders,
        COUNT(DISTINCT CASE WHEN f.is_suggestion THEN f.order_id END)                 AS sugg_orders,
        SUM(f.subtotal)                                                               AS total_revenue,
        SUM(CASE WHEN f.is_suggestion THEN f.subtotal ELSE 0 END)                     AS sugg_revenue,
        SUM(CASE WHEN f.is_suggestion THEN f.quantity ELSE 0 END)                     AS sugg_qty,
        SUM(f.quantity)                                                               AS total_qty,
        AVG(CASE WHEN f.is_suggestion THEN f.subtotal END)                            AS avg_sugg_line,
        AVG(CASE WHEN NOT f.is_suggestion THEN f.subtotal END)                        AS avg_self_line
      FROM iceberg.gold.fact_orders f
      {where_sql}
    )
    SELECT
      total_orders,
      sugg_orders,
      total_revenue,
      sugg_revenue,
      sugg_qty,
      total_qty,
      avg_sugg_line,
      avg_self_line,
      -- Trino: `x * 1.0 / y` trả về DECIMAL với scale chỉ 1 (vì 1.0 là
      -- DECIMAL(2,1)) → tỷ lệ nhỏ như 0.00035 bị làm tròn thành 0.0.
      -- CAST(AS DOUBLE) tránh tận gốc: division trả về DOUBLE giữ full precision.
      CASE WHEN total_revenue = 0 THEN 0
           ELSE CAST(sugg_revenue AS DOUBLE) / total_revenue
      END                                                                             AS sugg_revenue_share,
      CASE WHEN total_orders = 0 THEN 0
           ELSE CAST(sugg_orders AS DOUBLE) / total_orders
      END                                                                             AS sugg_order_share,
      CASE WHEN total_qty = 0 THEN 0
           ELSE CAST(sugg_qty AS DOUBLE) / total_qty
      END                                                                             AS sugg_qty_share,
      CASE WHEN avg_self_line IS NULL OR avg_self_line = 0 THEN 0
           ELSE CAST(avg_sugg_line - avg_self_line AS DOUBLE) / avg_self_line
      END                                                                             AS line_value_uplift
    FROM agg
    """
    return run_query(sql)


def suggestion_by_day(start_date, end_date, stores) -> pd.DataFrame:
    """Doanh thu theo ngày, tách theo is_suggestion."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      f.order_date                                                  AS day,
      SUM(CASE WHEN f.is_suggestion THEN f.subtotal ELSE 0 END)     AS sugg_revenue,
      SUM(CASE WHEN NOT f.is_suggestion THEN f.subtotal ELSE 0 END) AS self_revenue,
      SUM(f.subtotal)                                               AS total_revenue
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_date
    ORDER BY f.order_date
    """
    return run_query(sql)


def top_suggested_products(
    start_date, end_date, stores, top_n: int = 10
) -> pd.DataFrame:
    """Top sản phẩm đến từ gợi ý (chỉ lấy dòng is_suggestion = true)."""
    where_sql = _where(start_date, end_date, stores, extra="f.is_suggestion = TRUE")
    sql = f"""
    SELECT
      p.name                     AS product,
      SUM(f.quantity)            AS quantity,
      SUM(f.subtotal)            AS revenue,
      COUNT(DISTINCT f.order_id) AS orders
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_products p ON p.id = f.product_key
    {where_sql}
    GROUP BY p.name
    ORDER BY revenue DESC
    LIMIT {int(top_n)}
    """
    return run_query(sql)


def suggestion_by_store(start_date, end_date, stores) -> pd.DataFrame:
    """% doanh thu đến từ gợi ý theo từng cửa hàng — đo hiệu quả upsell."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      s.name                                                       AS store,
      SUM(CASE WHEN f.is_suggestion THEN f.subtotal ELSE 0 END)    AS sugg_revenue,
      SUM(f.subtotal)                                              AS total_revenue,
      CASE WHEN SUM(f.subtotal) = 0 THEN 0
           ELSE CAST(SUM(CASE WHEN f.is_suggestion THEN f.subtotal ELSE 0 END) AS DOUBLE)
                / SUM(f.subtotal)
      END                                                          AS sugg_share
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_store s ON s.id = f.store_key
    {where_sql}
    GROUP BY s.name
    ORDER BY sugg_share DESC
    """
    return run_query(sql)


def suggestion_by_hour(start_date, end_date, stores) -> pd.DataFrame:
    """Doanh thu gợi ý theo giờ trong ngày — tìm khung giờ vàng."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      f.order_hour                                                  AS hour,
      SUM(CASE WHEN f.is_suggestion THEN f.subtotal ELSE 0 END)     AS sugg_revenue,
      SUM(CASE WHEN NOT f.is_suggestion THEN f.subtotal ELSE 0 END) AS self_revenue,
      SUM(f.subtotal)                                               AS total_revenue
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_hour
    ORDER BY f.order_hour
    """
    return run_query(sql)


# ---------- Period-over-Period (so sánh kỳ trước) ---------------------------
#
# Logic kỳ trước: cùng độ dài (end - start) ngay liền kề trước kỳ hiện tại.
# Ví dụ kỳ hiện 01–31/04 → kỳ trước 01–31/03. Streamlit app tính 2 cặp ngày
# và truyền vào các hàm *_compare bên dưới.


def kpis_compare(
    start_date, end_date, prev_start, prev_end, stores
) -> pd.DataFrame:
    """KPI hiện tại + kỳ trước + delta % trên 1 row để vẽ card PoP."""
    cur_where = _where(start_date, end_date, stores)
    prev_where = _where(prev_start, prev_end, stores)
    sql = f"""
    WITH cur AS (
      SELECT
        COALESCE(SUM(f.subtotal), 0)     AS revenue,
        COUNT(DISTINCT f.order_id)       AS orders,
        COUNT(DISTINCT f.customer_id)    AS customers,
        COALESCE(SUM(f.quantity), 0)     AS products
      FROM iceberg.gold.fact_orders f
      {cur_where}
    ),
    prev AS (
      SELECT
        COALESCE(SUM(f.subtotal), 0)     AS revenue,
        COUNT(DISTINCT f.order_id)       AS orders,
        COUNT(DISTINCT f.customer_id)    AS customers,
        COALESCE(SUM(f.quantity), 0)     AS products
      FROM iceberg.gold.fact_orders f
      {prev_where}
    )
    SELECT
      cur.revenue            AS revenue,
      cur.orders             AS orders,
      cur.customers          AS customers,
      cur.products           AS products,
      CASE WHEN cur.orders = 0 THEN 0
           ELSE cur.revenue * 1.0 / cur.orders END                AS aov,
      prev.revenue           AS prev_revenue,
      prev.orders            AS prev_orders,
      prev.customers         AS prev_customers,
      prev.products          AS prev_products,
      CASE WHEN prev.orders = 0 THEN 0
           ELSE prev.revenue * 1.0 / prev.orders END              AS prev_aov
    FROM cur, prev
    """
    return run_query(sql)


def revenue_by_day_compare(
    start_date, end_date, prev_start, prev_end, stores
) -> pd.DataFrame:
    """
    Hai series doanh thu theo index ngày-trong-kỳ (0..N-1) để vẽ overlay
    "Kỳ này vs Kỳ trước". Trả về cột revenue_now, revenue_prev align theo
    offset từ ngày đầu mỗi kỳ — kể cả khi 1 trong 2 kỳ rỗng.
    """
    import numpy as np  # local import tránh overhead khi module import

    cur = revenue_by_day(start_date, end_date, stores).copy()
    prev = revenue_by_day(prev_start, prev_end, stores).copy()

    # Đảm bảo cột "i" luôn tồn tại (kể cả df rỗng) để tránh KeyError ở merge.
    if cur.empty:
        cur = pd.DataFrame({"i": [], "revenue": []})
    else:
        cur["i"] = (pd.to_datetime(cur["day"]) - pd.to_datetime(start_date)).dt.days
    if prev.empty:
        prev = pd.DataFrame({"i": [], "revenue": []})
    else:
        prev["i"] = (pd.to_datetime(prev["day"]) - pd.to_datetime(prev_start)).dt.days

    # Độ dài khung = kỳ hiện tại (end - start + 1); đảm bảo >= 1 để không vẽ rỗng.
    period_days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days + 1
    max_i = max(
        period_days - 1,
        int(cur["i"].max()) if not cur.empty else 0,
        int(prev["i"].max()) if not prev.empty else 0,
    )
    idx = np.arange(max_i + 1)
    out = pd.DataFrame({"i": idx})
    out["day_now"] = pd.to_datetime(start_date) + pd.to_timedelta(idx, unit="D")
    out["day_prev"] = pd.to_datetime(prev_start) + pd.to_timedelta(idx, unit="D")
    out = out.merge(
        cur[["i", "revenue"]].rename(columns={"revenue": "revenue_now"}),
        on="i",
        how="left",
    )
    out = out.merge(
        prev[["i", "revenue"]].rename(columns={"revenue": "revenue_prev"}),
        on="i",
        how="left",
    )
    return out.fillna({"revenue_now": 0, "revenue_prev": 0})


def revenue_by_hour_compare(
    day_now: date, day_prev: date, stores
) -> pd.DataFrame:
    """
    Doanh thu theo giờ (0..23) cho 2 ngày — dùng khi kỳ phân tích chỉ còn
    đúng 1 ngày. Granularity ngày -> giờ để trend chart có 24 điểm/series
    thay vì 1 điểm → line chart trông mượt, có ý nghĩa hơn.

    Trả về: hour (0..23), revenue_now, revenue_prev (missing hour → 0).
    """
    import numpy as np  # local import, cùng convention với hàm ở trên

    store_sql = _store_clause(stores)
    filter_store = f" AND {store_sql}" if store_sql else ""
    sql = f"""
    SELECT
      f.order_date                       AS day,
      f.order_hour                       AS hour,
      SUM(f.subtotal)                    AS revenue
    FROM iceberg.gold.fact_orders f
    WHERE f.order_date IN (DATE '{day_now.isoformat()}', DATE '{day_prev.isoformat()}')
          {filter_store}
    GROUP BY f.order_date, f.order_hour
    """
    df = run_query(sql)

    out = pd.DataFrame({"hour": np.arange(24)})
    if df.empty:
        out["revenue_now"] = 0
        out["revenue_prev"] = 0
        return out

    # Trino DATE có thể về như pd.Timestamp hoặc datetime.date tuỳ driver —
    # chuẩn hoá về date để so khớp an toàn.
    df["day"] = pd.to_datetime(df["day"]).dt.date
    now_df = df[df["day"] == day_now][["hour", "revenue"]].rename(
        columns={"revenue": "revenue_now"}
    )
    prev_df = df[df["day"] == day_prev][["hour", "revenue"]].rename(
        columns={"revenue": "revenue_prev"}
    )
    out = out.merge(now_df, on="hour", how="left").merge(prev_df, on="hour", how="left")
    return out.fillna({"revenue_now": 0, "revenue_prev": 0})


# ---------- Sản phẩm — Pareto, scatter --------------------------------------


def pareto_products(start_date, end_date, stores) -> pd.DataFrame:
    """Toàn bộ SP có bán trong kỳ (sorted desc) — dùng build Pareto 80/20."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      p.name                     AS product,
      SUM(f.quantity)            AS quantity,
      SUM(f.subtotal)            AS revenue,
      p.unit_price               AS unit_price
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_products p ON p.id = f.product_key
    {where_sql}
    GROUP BY p.name, p.unit_price
    ORDER BY revenue DESC
    """
    return run_query(sql)


# ---------- Khách hàng — tier, top, tần suất --------------------------------


def customers_by_tier(start_date, end_date, stores) -> pd.DataFrame:
    """Doanh thu + số KH unique theo tier (regular/silver/gold/...)."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      COALESCE(c.tier, 'unknown')           AS tier,
      SUM(f.subtotal)                       AS revenue,
      COUNT(DISTINCT f.customer_id)         AS customers,
      COUNT(DISTINCT f.order_id)            AS orders,
      CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
           ELSE SUM(f.subtotal) * 1.0 / COUNT(DISTINCT f.order_id)
      END                                   AS aov
    FROM iceberg.gold.fact_orders f
    LEFT JOIN iceberg.gold.dim_customers c ON c.id = f.customer_id
    {where_sql}
    GROUP BY COALESCE(c.tier, 'unknown')
    ORDER BY revenue DESC
    """
    return run_query(sql)


def top_customers(start_date, end_date, stores, top_n: int = 10) -> pd.DataFrame:
    """Top KH theo doanh thu (whale list)."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      c.name                               AS customer,
      COALESCE(c.tier, 'unknown')          AS tier,
      SUM(f.subtotal)                      AS revenue,
      COUNT(DISTINCT f.order_id)           AS orders,
      SUM(f.quantity)                      AS quantity,
      CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
           ELSE SUM(f.subtotal) * 1.0 / COUNT(DISTINCT f.order_id)
      END                                  AS aov
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_customers c ON c.id = f.customer_id
    {where_sql}
    GROUP BY c.name, c.tier
    ORDER BY revenue DESC
    LIMIT {int(top_n)}
    """
    return run_query(sql)


def purchase_frequency(start_date, end_date, stores) -> pd.DataFrame:
    """
    Phân bố số đơn mỗi KH trong kỳ → đo loyalty.
    Nhóm 1/2/3/4-5/6-10/11+ để histogram gọn.
    """
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    WITH customer_orders AS (
      SELECT f.customer_id, COUNT(DISTINCT f.order_id) AS orders
      FROM iceberg.gold.fact_orders f
      {where_sql}
      GROUP BY f.customer_id
    )
    SELECT
      CASE
        WHEN orders = 1 THEN '1 đơn'
        WHEN orders = 2 THEN '2 đơn'
        WHEN orders = 3 THEN '3 đơn'
        WHEN orders BETWEEN 4 AND 5 THEN '4–5 đơn'
        WHEN orders BETWEEN 6 AND 10 THEN '6–10 đơn'
        ELSE '11+ đơn'
      END                         AS bucket,
      CASE
        WHEN orders = 1 THEN 1
        WHEN orders = 2 THEN 2
        WHEN orders = 3 THEN 3
        WHEN orders BETWEEN 4 AND 5 THEN 4
        WHEN orders BETWEEN 6 AND 10 THEN 5
        ELSE 6
      END                         AS bucket_order,
      COUNT(*)                    AS customers
    FROM customer_orders
    GROUP BY 1, 2
    ORDER BY bucket_order
    """
    return run_query(sql)


# ---------- Hành vi — DOW, hour ---------------------------------------------


def revenue_by_dow(start_date, end_date, stores) -> pd.DataFrame:
    """Doanh thu + số đơn theo thứ trong tuần (1=CN ... 7=T7)."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      f.order_dow                          AS dow,
      SUM(f.subtotal)                      AS revenue,
      COUNT(DISTINCT f.order_id)           AS orders,
      CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
           ELSE SUM(f.subtotal) * 1.0 / COUNT(DISTINCT f.order_id)
      END                                  AS aov
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_dow
    ORDER BY f.order_dow
    """
    return run_query(sql)


def revenue_by_hour(start_date, end_date, stores) -> pd.DataFrame:
    """Doanh thu + số đơn theo giờ trong ngày — tìm peak/dead hours."""
    where_sql = _where(start_date, end_date, stores)
    sql = f"""
    SELECT
      f.order_hour                         AS hour,
      SUM(f.subtotal)                      AS revenue,
      COUNT(DISTINCT f.order_id)           AS orders
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_hour
    ORDER BY f.order_hour
    """
    return run_query(sql)
