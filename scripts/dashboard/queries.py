"""
SQL queries cho dashboard. Tách khỏi UI để dễ test + đọc plan trong Trino UI.

Mọi query đều dùng Iceberg table `iceberg.gold.*`. Tên cột khớp với
gold_layer.py (fact_orders + dim_*).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from trino_client import run_query

# ---------- Filter helpers --------------------------------------------------
#
# Trino DBAPI bản hiện tại không ổn định với placeholder `?` (coordinator
# không nhận được header PREPARE → "mismatched input '?'"). Vì các filter
# ở đây đều là giá trị nội bộ đã validate (date.isoformat() → YYYY-MM-DD,
# store_key là int), ta inline literal an toàn vào SQL thay vì bind params.


def _date_clause(tu_ngay: date | None, den_ngay: date | None) -> str:
    """Build mệnh đề WHERE theo order_date; bỏ qua None."""
    dieu_kien: list[str] = []
    if tu_ngay:
        dieu_kien.append(f"f.order_date >= DATE '{tu_ngay.isoformat()}'")
    if den_ngay:
        dieu_kien.append(f"f.order_date <= DATE '{den_ngay.isoformat()}'")
    return " AND ".join(dieu_kien)


def _store_clause(cac_store_key: list[int] | None) -> str:
    """IN filter theo store_key. Inline literal int (đã cast) — không bind params."""
    if not cac_store_key:
        return ""
    ds = ",".join(str(int(k)) for k in cac_store_key)
    return f"f.store_key IN ({ds})"


def _where(tu_ngay, den_ngay, stores, extra: str = "") -> str:
    parts = [
        x
        for x in (_date_clause(tu_ngay, den_ngay), _store_clause(stores), extra)
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
        "SELECT MIN(order_date) AS tu_ngay, MAX(order_date) AS den_ngay "
        "FROM iceberg.gold.fact_orders"
    )
    if df.empty or df.iloc[0]["tu_ngay"] is None:
        return None, None
    return df.iloc[0]["tu_ngay"], df.iloc[0]["den_ngay"]


def get_stores() -> pd.DataFrame:
    return run_query(
        "SELECT CAST(id AS INTEGER) AS store_key, name AS store_name "
        "FROM iceberg.gold.dim_store ORDER BY name"
    )


# ---------- KPI + biểu đồ ---------------------------------------------------


def kpis(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      COUNT(DISTINCT f.order_id)            AS so_don,
      COUNT(DISTINCT f.customer_id)         AS so_khach,
      COALESCE(SUM(f.subtotal), 0)          AS doanh_thu,
      COALESCE(SUM(f.quantity), 0)          AS so_san_pham,
      CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
           ELSE COALESCE(SUM(f.subtotal), 0) * 1.0 / COUNT(DISTINCT f.order_id)
      END                                   AS gia_tri_don_tb
    FROM iceberg.gold.fact_orders f
    {where_sql}
    """
    return run_query(sql)


def doanh_thu_theo_ngay(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      f.order_date                       AS ngay,
      SUM(f.subtotal)                    AS doanh_thu,
      COUNT(DISTINCT f.order_id)         AS so_don
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_date
    ORDER BY f.order_date
    """
    return run_query(sql)


def top_san_pham(tu_ngay, den_ngay, stores, top_n: int = 10) -> pd.DataFrame:
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      p.name                     AS san_pham,
      SUM(f.quantity)            AS so_luong,
      SUM(f.subtotal)            AS doanh_thu
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_products p ON p.id = f.product_key
    {where_sql}
    GROUP BY p.name
    ORDER BY doanh_thu DESC
    LIMIT {int(top_n)}
    """
    return run_query(sql)


def doanh_thu_theo_store(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      s.name                               AS cua_hang,
      SUM(f.subtotal)                      AS doanh_thu,
      COUNT(DISTINCT f.order_id)           AS so_don,
      CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
           ELSE SUM(f.subtotal) * 1.0 / COUNT(DISTINCT f.order_id)
      END                                  AS gia_tri_don_tb
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_store s ON s.id = f.store_key
    {where_sql}
    GROUP BY s.name
    ORDER BY doanh_thu DESC
    """
    return run_query(sql)


def doanh_thu_theo_payment(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      pm.method_name                      AS phuong_thuc,
      SUM(f.subtotal)                     AS doanh_thu,
      COUNT(DISTINCT f.order_id)          AS so_don
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_payment pm ON pm.id = f.payment_method_key
    {where_sql}
    GROUP BY pm.method_name
    ORDER BY doanh_thu DESC
    """
    return run_query(sql)


def heatmap_gio_ngay(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """Heatmap doanh thu theo (thứ trong tuần × giờ)."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      f.order_dow    AS thu,
      f.order_hour   AS gio,
      SUM(f.subtotal) AS doanh_thu
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_dow, f.order_hour
    ORDER BY f.order_dow, f.order_hour
    """
    return run_query(sql)


def tac_dong_goi_y(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """So sánh doanh thu từ dòng là gợi ý vs không phải gợi ý."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      CASE WHEN f.is_suggestion THEN 'Gợi ý' ELSE 'Khách tự chọn' END AS nguon,
      SUM(f.subtotal)            AS doanh_thu,
      SUM(f.quantity)            AS so_luong,
      COUNT(DISTINCT f.order_id) AS so_don,
      AVG(f.subtotal)            AS gia_tri_dong_tb
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.is_suggestion
    ORDER BY doanh_thu DESC
    """
    return run_query(sql)


def goi_y_kpi(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """KPI tổng hợp cho tab gợi ý — một row duy nhất với tỉ lệ đóng góp và uplift."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    WITH agg AS (
      SELECT
        COUNT(DISTINCT f.order_id)                                                    AS tong_don,
        COUNT(DISTINCT CASE WHEN f.is_suggestion THEN f.order_id END)                 AS don_co_goi_y,
        SUM(f.subtotal)                                                               AS tong_dt,
        SUM(CASE WHEN f.is_suggestion THEN f.subtotal ELSE 0 END)                     AS dt_goi_y,
        SUM(CASE WHEN f.is_suggestion THEN f.quantity ELSE 0 END)                     AS sl_goi_y,
        SUM(f.quantity)                                                               AS tong_sl,
        AVG(CASE WHEN f.is_suggestion THEN f.subtotal END)                            AS tb_dong_goi_y,
        AVG(CASE WHEN NOT f.is_suggestion THEN f.subtotal END)                        AS tb_dong_tu_chon
      FROM iceberg.gold.fact_orders f
      {where_sql}
    )
    SELECT
      tong_don,
      don_co_goi_y,
      tong_dt,
      dt_goi_y,
      sl_goi_y,
      tong_sl,
      tb_dong_goi_y,
      tb_dong_tu_chon,
      CASE WHEN tong_dt = 0 THEN 0 ELSE dt_goi_y * 1.0 / tong_dt END         AS ty_le_dt_goi_y,
      CASE WHEN tong_don = 0 THEN 0 ELSE don_co_goi_y * 1.0 / tong_don END   AS ty_le_don_goi_y,
      CASE WHEN tong_sl = 0 THEN 0 ELSE sl_goi_y * 1.0 / tong_sl END         AS ty_le_sl_goi_y,
      CASE WHEN tb_dong_tu_chon IS NULL OR tb_dong_tu_chon = 0 THEN 0
           ELSE (tb_dong_goi_y - tb_dong_tu_chon) * 1.0 / tb_dong_tu_chon
      END                                                                    AS uplift_gia_tri_dong
    FROM agg
    """
    return run_query(sql)


def goi_y_theo_ngay(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """Doanh thu theo ngày, tách theo is_suggestion."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      f.order_date                                                AS ngay,
      SUM(CASE WHEN f.is_suggestion THEN f.subtotal ELSE 0 END)   AS dt_goi_y,
      SUM(CASE WHEN NOT f.is_suggestion THEN f.subtotal ELSE 0 END) AS dt_tu_chon,
      SUM(f.subtotal)                                             AS dt_tong
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_date
    ORDER BY f.order_date
    """
    return run_query(sql)


def top_san_pham_goi_y(tu_ngay, den_ngay, stores, top_n: int = 10) -> pd.DataFrame:
    """Top sản phẩm đến từ gợi ý (chỉ lấy dòng is_suggestion = true)."""
    where_sql = _where(tu_ngay, den_ngay, stores, extra="f.is_suggestion = TRUE")
    sql = f"""
    SELECT
      p.name                     AS san_pham,
      SUM(f.quantity)            AS so_luong,
      SUM(f.subtotal)            AS doanh_thu,
      COUNT(DISTINCT f.order_id) AS so_don
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_products p ON p.id = f.product_key
    {where_sql}
    GROUP BY p.name
    ORDER BY doanh_thu DESC
    LIMIT {int(top_n)}
    """
    return run_query(sql)


def goi_y_theo_store(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """% doanh thu đến từ gợi ý theo từng cửa hàng — đo hiệu quả upsell."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      s.name                                                       AS cua_hang,
      SUM(CASE WHEN f.is_suggestion THEN f.subtotal ELSE 0 END)    AS dt_goi_y,
      SUM(f.subtotal)                                              AS dt_tong,
      CASE WHEN SUM(f.subtotal) = 0 THEN 0
           ELSE SUM(CASE WHEN f.is_suggestion THEN f.subtotal ELSE 0 END) * 1.0 / SUM(f.subtotal)
      END                                                          AS ty_le_goi_y
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_store s ON s.id = f.store_key
    {where_sql}
    GROUP BY s.name
    ORDER BY ty_le_goi_y DESC
    """
    return run_query(sql)


def goi_y_theo_gio(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """Doanh thu gợi ý theo giờ trong ngày — tìm khung giờ vàng."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      f.order_hour                                                 AS gio,
      SUM(CASE WHEN f.is_suggestion THEN f.subtotal ELSE 0 END)    AS dt_goi_y,
      SUM(CASE WHEN NOT f.is_suggestion THEN f.subtotal ELSE 0 END) AS dt_tu_chon,
      SUM(f.subtotal)                                              AS dt_tong
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_hour
    ORDER BY f.order_hour
    """
    return run_query(sql)


# ---------- Period-over-Period (so sánh kỳ trước) ---------------------------
#
# Logic kỳ trước: cùng độ dài (den - tu) ngay liền kề trước kỳ hiện tại.
# Ví dụ kỳ hiện 01–31/04 → kỳ trước 01–31/03. Streamlit app tính 2 cặp ngày
# và truyền vào các hàm *_so_sanh bên dưới.


def kpis_so_sanh(tu_ngay, den_ngay, prev_tu, prev_den, stores) -> pd.DataFrame:
    """KPI hiện tại + kỳ trước + delta % trên 1 row để vẽ card PoP."""
    cur_where = _where(tu_ngay, den_ngay, stores)
    prev_where = _where(prev_tu, prev_den, stores)
    sql = f"""
    WITH cur AS (
      SELECT
        COALESCE(SUM(f.subtotal), 0)     AS doanh_thu,
        COUNT(DISTINCT f.order_id)       AS so_don,
        COUNT(DISTINCT f.customer_id)    AS so_khach,
        COALESCE(SUM(f.quantity), 0)     AS so_san_pham
      FROM iceberg.gold.fact_orders f
      {cur_where}
    ),
    prev AS (
      SELECT
        COALESCE(SUM(f.subtotal), 0)     AS doanh_thu,
        COUNT(DISTINCT f.order_id)       AS so_don,
        COUNT(DISTINCT f.customer_id)    AS so_khach,
        COALESCE(SUM(f.quantity), 0)     AS so_san_pham
      FROM iceberg.gold.fact_orders f
      {prev_where}
    )
    SELECT
      cur.doanh_thu          AS doanh_thu,
      cur.so_don             AS so_don,
      cur.so_khach           AS so_khach,
      cur.so_san_pham        AS so_san_pham,
      CASE WHEN cur.so_don = 0 THEN 0
           ELSE cur.doanh_thu * 1.0 / cur.so_don END            AS aov,
      prev.doanh_thu         AS prev_doanh_thu,
      prev.so_don            AS prev_so_don,
      prev.so_khach          AS prev_so_khach,
      prev.so_san_pham       AS prev_so_san_pham,
      CASE WHEN prev.so_don = 0 THEN 0
           ELSE prev.doanh_thu * 1.0 / prev.so_don END          AS prev_aov
    FROM cur, prev
    """
    return run_query(sql)


def doanh_thu_ngay_so_sanh(tu_ngay, den_ngay, prev_tu, prev_den, stores) -> pd.DataFrame:
    """
    Hai series doanh thu theo index ngày-trong-kỳ (0..N-1) để vẽ overlay
    "Kỳ này vs Kỳ trước". Trả về cột doanh_thu_now, doanh_thu_prev align theo
    offset từ ngày đầu mỗi kỳ — kể cả khi 1 trong 2 kỳ rỗng.
    """
    import numpy as np  # local import tránh overhead khi module import

    cur = doanh_thu_theo_ngay(tu_ngay, den_ngay, stores).copy()
    prev = doanh_thu_theo_ngay(prev_tu, prev_den, stores).copy()

    # Đảm bảo cột "i" luôn tồn tại (kể cả df rỗng) để tránh KeyError ở merge.
    if cur.empty:
        cur = pd.DataFrame({"i": [], "doanh_thu": []})
    else:
        cur["i"] = (pd.to_datetime(cur["ngay"]) - pd.to_datetime(tu_ngay)).dt.days
    if prev.empty:
        prev = pd.DataFrame({"i": [], "doanh_thu": []})
    else:
        prev["i"] = (pd.to_datetime(prev["ngay"]) - pd.to_datetime(prev_tu)).dt.days

    # Độ dài khung = kỳ hiện tại (den - tu + 1); đảm bảo >= 1 để không vẽ rỗng.
    so_ngay_ky = (pd.Timestamp(den_ngay) - pd.Timestamp(tu_ngay)).days + 1
    max_i = max(
        so_ngay_ky - 1,
        int(cur["i"].max()) if not cur.empty else 0,
        int(prev["i"].max()) if not prev.empty else 0,
    )
    idx = np.arange(max_i + 1)
    out = pd.DataFrame({"i": idx})
    out["ngay_now"] = pd.to_datetime(tu_ngay) + pd.to_timedelta(idx, unit="D")
    out["ngay_prev"] = pd.to_datetime(prev_tu) + pd.to_timedelta(idx, unit="D")
    out = out.merge(
        cur[["i", "doanh_thu"]].rename(columns={"doanh_thu": "doanh_thu_now"}),
        on="i",
        how="left",
    )
    out = out.merge(
        prev[["i", "doanh_thu"]].rename(columns={"doanh_thu": "doanh_thu_prev"}),
        on="i",
        how="left",
    )
    return out.fillna({"doanh_thu_now": 0, "doanh_thu_prev": 0})


# ---------- Sản phẩm — Pareto, scatter --------------------------------------


def pareto_san_pham(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """Toàn bộ SP có bán trong kỳ (sorted desc) — dùng build Pareto 80/20."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      p.name                     AS san_pham,
      SUM(f.quantity)            AS so_luong,
      SUM(f.subtotal)            AS doanh_thu,
      p.unit_price               AS don_gia
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_products p ON p.id = f.product_key
    {where_sql}
    GROUP BY p.name, p.unit_price
    ORDER BY doanh_thu DESC
    """
    return run_query(sql)


# ---------- Khách hàng — tier, top, tần suất --------------------------------


def khach_theo_tier(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """Doanh thu + số KH unique theo tier (regular/silver/gold/...)."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      COALESCE(c.tier, 'unknown')           AS tier,
      SUM(f.subtotal)                       AS doanh_thu,
      COUNT(DISTINCT f.customer_id)         AS so_khach,
      COUNT(DISTINCT f.order_id)            AS so_don,
      CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
           ELSE SUM(f.subtotal) * 1.0 / COUNT(DISTINCT f.order_id)
      END                                   AS aov
    FROM iceberg.gold.fact_orders f
    LEFT JOIN iceberg.gold.dim_customers c ON c.id = f.customer_id
    {where_sql}
    GROUP BY COALESCE(c.tier, 'unknown')
    ORDER BY doanh_thu DESC
    """
    return run_query(sql)


def top_khach_hang(tu_ngay, den_ngay, stores, top_n: int = 10) -> pd.DataFrame:
    """Top KH theo doanh thu (whale list)."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      c.name                               AS khach_hang,
      COALESCE(c.tier, 'unknown')          AS tier,
      SUM(f.subtotal)                      AS doanh_thu,
      COUNT(DISTINCT f.order_id)           AS so_don,
      SUM(f.quantity)                      AS so_luong,
      CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
           ELSE SUM(f.subtotal) * 1.0 / COUNT(DISTINCT f.order_id)
      END                                  AS aov
    FROM iceberg.gold.fact_orders f
    JOIN iceberg.gold.dim_customers c ON c.id = f.customer_id
    {where_sql}
    GROUP BY c.name, c.tier
    ORDER BY doanh_thu DESC
    LIMIT {int(top_n)}
    """
    return run_query(sql)


def tan_suat_mua_khach(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """
    Phân bố số đơn mỗi KH trong kỳ → đo loyalty.
    Nhóm 1/2/3/4-5/6-10/11+ để histogram gọn.
    """
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    WITH don_khach AS (
      SELECT f.customer_id, COUNT(DISTINCT f.order_id) AS so_don
      FROM iceberg.gold.fact_orders f
      {where_sql}
      GROUP BY f.customer_id
    )
    SELECT
      CASE
        WHEN so_don = 1 THEN '1 đơn'
        WHEN so_don = 2 THEN '2 đơn'
        WHEN so_don = 3 THEN '3 đơn'
        WHEN so_don BETWEEN 4 AND 5 THEN '4–5 đơn'
        WHEN so_don BETWEEN 6 AND 10 THEN '6–10 đơn'
        ELSE '11+ đơn'
      END                         AS nhom,
      CASE
        WHEN so_don = 1 THEN 1
        WHEN so_don = 2 THEN 2
        WHEN so_don = 3 THEN 3
        WHEN so_don BETWEEN 4 AND 5 THEN 4
        WHEN so_don BETWEEN 6 AND 10 THEN 5
        ELSE 6
      END                         AS thu_tu,
      COUNT(*)                    AS so_khach
    FROM don_khach
    GROUP BY 1, 2
    ORDER BY thu_tu
    """
    return run_query(sql)


# ---------- Hành vi — DOW, hour ---------------------------------------------


def doanh_thu_theo_dow(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """Doanh thu + số đơn theo thứ trong tuần (1=CN ... 7=T7)."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      f.order_dow                          AS dow,
      SUM(f.subtotal)                      AS doanh_thu,
      COUNT(DISTINCT f.order_id)           AS so_don,
      CASE WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
           ELSE SUM(f.subtotal) * 1.0 / COUNT(DISTINCT f.order_id)
      END                                  AS aov
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_dow
    ORDER BY f.order_dow
    """
    return run_query(sql)


def doanh_thu_theo_gio(tu_ngay, den_ngay, stores) -> pd.DataFrame:
    """Doanh thu + số đơn theo giờ trong ngày — tìm peak/dead hours."""
    where_sql = _where(tu_ngay, den_ngay, stores)
    sql = f"""
    SELECT
      f.order_hour                         AS gio,
      SUM(f.subtotal)                      AS doanh_thu,
      COUNT(DISTINCT f.order_id)           AS so_don
    FROM iceberg.gold.fact_orders f
    {where_sql}
    GROUP BY f.order_hour
    ORDER BY f.order_hour
    """
    return run_query(sql)
