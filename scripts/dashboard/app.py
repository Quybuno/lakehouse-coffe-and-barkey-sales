from __future__ import annotations

import traceback
from contextlib import contextmanager
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from trino.exceptions import TrinoConnectionError, TrinoQueryError

import queries as Q
from trino_client import ping

st.set_page_config(
    page_title="KD Bakery & Coffee — Gold Dashboard",
    page_icon="☕",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------- Helpers ---------------------------------------------------

DOW_LABELS = {1: "CN", 2: "T2", 3: "T3", 4: "T4", 5: "T5", 6: "T6", 7: "T7"}


def fmt_vnd(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.0f} ₫".replace(",", ".")


def fmt_int(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{int(value):,}".replace(",", ".")


_STORE_PREFIX = "KD Bakery Coffee "


def short_store_name(full: str) -> str:
    if isinstance(full, str) and full.startswith(_STORE_PREFIX):
        return full[len(_STORE_PREFIX):]
    return full


@contextmanager
def safe_section(name: str):
    """
    Bọc 1 block UI — nếu Trino chết / query fail, hiển thị cảnh báo thân thiện
    thay vì crash toàn app. 1 tab lỗi không kéo các tab khác chết theo.
    """
    try:
        yield
    except TrinoConnectionError as e:
        st.error(
            f"**{name}** — Mất kết nối Trino. "
            "Coordinator có thể đã OOM/chết. Thử:\n"
            "1. `docker compose restart trino` rồi chờ healthcheck xanh.\n"
            "2. Nếu tái diễn → tăng RAM container (`docker-compose.yml` → "
            "`trino.deploy.resources.limits.memory`) và chỉnh `-Xmx` "
            "trong `configs/trino/etc/jvm.config` (Xmx ~70% cap).\n"
            "3. Bấm **🔄 Refresh cache** ở sidebar sau khi Trino xanh lại."
        )
        with st.expander("Chi tiết lỗi kỹ thuật"):
            st.code(str(e))
    except TrinoQueryError as e:
        st.warning(
            f"**{name}** — Trino từ chối query: `{e.error_name}`. "
            "Kiểm tra schema hoặc filter hiện tại."
        )
        with st.expander("Chi tiết lỗi"):
            st.code(e.message)
    except Exception as e:  # noqa: BLE001
        st.warning(f"**{name}** — lỗi không mong đợi: `{type(e).__name__}`")
        with st.expander("Traceback"):
            st.code(traceback.format_exc())


# ---------------- Sidebar (filter + status) ----------------------------

with st.sidebar:
    st.title("☕ KD Bakery & Coffee")
    st.caption("Gold Layer · Iceberg · Trino")

    ok, msg = ping()
    (st.success if ok else st.error)(msg)

    if not ok:
        st.info("Kiểm tra:\n- `docker ps` có `trino` + `iceberg-rest` running?\n- DAG `spark-batch-job` đã chạy để tạo bảng `iceberg.gold.*` chưa?")
        st.stop()

    try:
        date_min, date_max = Q.get_date_bounds()
    except TrinoQueryError as e:
        if e.error_name in ("SCHEMA_NOT_FOUND", "TABLE_NOT_FOUND"):
            st.error(
                "Gold layer chưa sẵn sàng — chưa có `iceberg.gold.fact_orders`."
            )
            st.info(
                "Trigger DAG **`spark-batch-job`** trên Airflow để chạy "
                "`bronze_raw.py` → `silver_layer.py` → `gold_layer.py`, "
                "sau đó quay lại và bấm **🔄 Refresh cache**."
            )
            with st.expander("Chi tiết lỗi"):
                st.code(f"{e.error_name}: {e.message}")
            st.stop()
        raise
    if date_min is None:
        st.warning("Chưa có dữ liệu trong `iceberg.gold.fact_orders`.")
        st.stop()

    default_from = max(date_min, date_max - timedelta(days=30))
    date_range = st.date_input(
        "Khoảng ngày",
        value=(default_from, date_max),
        min_value=date_min,
        max_value=date_max,
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range  # type: ignore[assignment]

    stores_df = Q.get_stores()
    store_map = dict(zip(stores_df["store_name"], stores_df["store_key"]))
    stores_selected = st.multiselect(
        "Cửa hàng", options=list(store_map.keys()), default=[]
    )
    store_keys = [store_map[s] for s in stores_selected] or None

    if st.button("🔄 Refresh cache"):
        st.cache_data.clear()
        st.rerun()

    st.caption(f"Khoảng dữ liệu có sẵn: {date_min} → {date_max}")

# ---------------- Header + Period-over-Period KPIs -----------------------

st.title("Dashboard kinh doanh — KD Bakery & Coffee")

period_days = (end_date - start_date).days + 1
prev_end = start_date - timedelta(days=1)
prev_start = prev_end - timedelta(days=period_days - 1)

st.caption(
    f"**Kỳ phân tích:** {start_date:%d/%m/%Y} → {end_date:%d/%m/%Y} ({period_days} ngày) · "
    f"so sánh với **kỳ trước** {prev_start:%d/%m/%Y} → {prev_end:%d/%m/%Y} · "
    f"phạm vi: {len(stores_selected) or 'tất cả'} cửa hàng"
)


def delta_pct(cur: float | None, prev: float | None) -> str | None:
    """Tạo chuỗi delta cho st.metric — Streamlit tự tô xanh/đỏ theo dấu."""
    if cur is None or prev is None or pd.isna(cur) or pd.isna(prev) or prev == 0:
        return None
    pct = (float(cur) - float(prev)) / float(prev) * 100
    return f"{pct:+.1f}% vs kỳ trước"


kp: dict = {}
with safe_section("KPI tổng hợp"):
    kpi_pop = Q.kpis_compare(start_date, end_date, prev_start, prev_end, store_keys)
    kp = kpi_pop.iloc[0].to_dict() if not kpi_pop.empty else {}

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "Doanh thu",
        fmt_vnd(kp.get("revenue")),
        delta=delta_pct(kp.get("revenue"), kp.get("prev_revenue")),
    )
    c2.metric(
        "Số đơn",
        fmt_int(kp.get("orders")),
        delta=delta_pct(kp.get("orders"), kp.get("prev_orders")),
    )
    c3.metric(
        "Khách unique",
        fmt_int(kp.get("customers")),
        delta=delta_pct(kp.get("customers"), kp.get("prev_customers")),
    )
    c4.metric(
        "AOV (giá trị đơn TB)",
        fmt_vnd(kp.get("aov")),
        delta=delta_pct(kp.get("aov"), kp.get("prev_aov")),
    )
    c5.metric(
        "SP bán ra",
        fmt_int(kp.get("products")),
        delta=delta_pct(kp.get("products"), kp.get("prev_products")),
    )

st.divider()

# ---------------- Tabs ------------------------------------------------------

(
    tab_overview,
    tab_product,
    tab_store,
    tab_customer,
    tab_behavior,
    tab_suggestion,
) = st.tabs(
    [
        "📊 Tổng quan",
        "🛍️ Sản phẩm",
        "🏪 Cửa hàng",
        "👥 Khách hàng",
        "⏰ Hành vi mua",
        "✨ Gợi ý",
    ]
)

# ---- Tab 1: Tổng quan (Executive) -----------------------------------------
with tab_overview, safe_section("Tab Tổng quan"):
    # Pre-khai báo để block "Insight nhanh" bên dưới luôn có biến ref,
    # bất kể nhánh 1-ngày (df_hour) hay nhiều-ngày (df_trend) được chạy.
    df_trend = pd.DataFrame()
    df_hour = pd.DataFrame()
    if period_days == 1:
        # Kỳ = 1 ngày: group theo order_date chỉ ra 1 điểm → line chart xấu.
        # Switch granularity sang theo giờ (0..23) cho cả ngày này + ngày trước.
        st.subheader(
            f"Doanh thu theo giờ — {start_date:%d/%m} vs {prev_start:%d/%m}"
        )
        df_hour = Q.revenue_by_hour_compare(start_date, prev_start, store_keys)
        two_day_total = (
            df_hour[["revenue_now", "revenue_prev"]].sum().sum()
            if not df_hour.empty
            else 0
        )
        if df_hour.empty or two_day_total == 0:
            st.info("Không có dữ liệu trong ngày đã chọn.")
        else:
            fig_trend = go.Figure()
            fig_trend.add_trace(
                go.Bar(
                    x=df_hour["hour"],
                    y=df_hour["revenue_prev"],
                    name=f"Kỳ trước ({prev_start:%d/%m})",
                    marker=dict(color="rgba(209,213,219,0.85)"),
                    hovertemplate=(
                        f"Kỳ trước ({prev_start:%d/%m}) · %{{x}}h<br>"
                        "%{y:,.0f} ₫<extra></extra>"
                    ),
                )
            )
            fig_trend.add_trace(
                go.Scatter(
                    x=df_hour["hour"],
                    y=df_hour["revenue_now"],
                    name=f"Kỳ này ({start_date:%d/%m})",
                    mode="lines+markers",
                    line=dict(color="#6f4e37", width=2.5, shape="spline"),
                    marker=dict(size=7),
                    fill="tozeroy",
                    fillcolor="rgba(111,78,55,0.18)",
                    hovertemplate=(
                        f"Kỳ này ({start_date:%d/%m}) · %{{x}}h<br>"
                        "%{y:,.0f} ₫<extra></extra>"
                    ),
                )
            )
            # Highlight peak hour của kỳ này để user nhìn phát thấy ngay.
            idx_peak = int(df_hour["revenue_now"].idxmax())
            peak_hour = int(df_hour.loc[idx_peak, "hour"])
            peak_revenue = float(df_hour.loc[idx_peak, "revenue_now"])
            if peak_revenue > 0:
                fig_trend.add_annotation(
                    x=peak_hour,
                    y=peak_revenue,
                    text=f"Peak {peak_hour}h · {fmt_vnd(peak_revenue)}",
                    showarrow=True,
                    arrowhead=2,
                    arrowcolor="#6f4e37",
                    ax=0,
                    ay=-35,
                    font=dict(size=11, color="#6f4e37"),
                    bgcolor="rgba(255,255,255,0.9)",
                    bordercolor="#6f4e37",
                    borderwidth=1,
                    borderpad=4,
                )
            fig_trend.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                yaxis=dict(title="Doanh thu (₫)", gridcolor="rgba(0,0,0,0.06)"),
                xaxis=dict(
                    title="Giờ trong ngày",
                    tickmode="array",
                    tickvals=list(range(0, 24, 2)),
                    ticktext=[f"{h:02d}h" for h in range(0, 24, 2)],
                    range=[-0.5, 23.5],
                ),
                plot_bgcolor="white",
                bargap=0.25,
            )
            st.plotly_chart(fig_trend, use_container_width=True)
    else:
        st.subheader("Xu hướng doanh thu — Kỳ này vs Kỳ trước")
        df_trend = Q.revenue_by_day_compare(
            start_date, end_date, prev_start, prev_end, store_keys
        )
        if (
            df_trend.empty
            or df_trend[["revenue_now", "revenue_prev"]].sum().sum() == 0
        ):
            st.info("Không có dữ liệu trong khoảng đã chọn.")
        else:
            # MA 7 ngày giúp khử noise daily để nhìn xu hướng thật —
            # chỉ có ý nghĩa khi kỳ đủ dài.
            show_ma7 = period_days >= 7
            if show_ma7:
                df_trend["ma7_now"] = (
                    df_trend["revenue_now"].rolling(7, min_periods=1).mean()
                )

            fig_trend = go.Figure()
            fig_trend.add_trace(
                go.Scatter(
                    x=df_trend["day_now"],
                    y=df_trend["revenue_prev"],
                    name="Kỳ trước",
                    mode="lines",
                    line=dict(color="#d1d5db", width=2, dash="dot"),
                    hovertemplate="Kỳ trước (%{customdata|%d/%m})<br>%{y:,.0f} ₫<extra></extra>",
                    customdata=df_trend["day_prev"],
                )
            )
            fig_trend.add_trace(
                go.Scatter(
                    x=df_trend["day_now"],
                    y=df_trend["revenue_now"],
                    name="Kỳ này",
                    mode="lines+markers",
                    line=dict(color="#6f4e37", width=2.5),
                    marker=dict(size=5),
                    fill="tozeroy",
                    fillcolor="rgba(111,78,55,0.12)",
                    hovertemplate="Kỳ này (%{x|%d/%m})<br>%{y:,.0f} ₫<extra></extra>",
                )
            )
            if show_ma7:
                fig_trend.add_trace(
                    go.Scatter(
                        x=df_trend["day_now"],
                        y=df_trend["ma7_now"],
                        name="MA 7 ngày",
                        mode="lines",
                        line=dict(color="#d97706", width=2, dash="dash"),
                        hovertemplate="MA7 (%{x|%d/%m})<br>%{y:,.0f} ₫<extra></extra>",
                    )
                )
            fig_trend.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                yaxis=dict(title="Doanh thu (₫)"),
                xaxis=dict(title=None),
            )
            st.plotly_chart(fig_trend, use_container_width=True)

    colL, colR = st.columns([3, 2])

    with colL:
        st.subheader("Cơ cấu doanh thu theo cửa hàng")
        df_store_overview = Q.revenue_by_store(start_date, end_date, store_keys)
        if df_store_overview.empty:
            st.info("Không có dữ liệu cửa hàng.")
        else:
            # Tên ngắn cho label ngoài slice (tránh Plotly truncate khi
            # container hẹp); tên đầy đủ giữ ở customdata cho hover.
            df_store_overview = df_store_overview.copy()
            df_store_overview["short_name"] = df_store_overview["store"].map(
                short_store_name
            )
            fig_donut = px.pie(
                df_store_overview,
                names="short_name",
                values="revenue",
                hole=0.55,
                color_discrete_sequence=px.colors.sequential.Oranges_r,
                custom_data=["store"],
            )
            fig_donut.update_traces(
                textposition="outside",
                textinfo="label+percent",
                hovertemplate=(
                    "%{customdata[0]}<br>%{value:,.0f} ₫ (%{percent})"
                    "<extra></extra>"
                ),
            )
            fig_donut.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                showlegend=False,
            )
            st.plotly_chart(fig_donut, use_container_width=True)

    with colR:
        st.subheader("Insight: ")
        insights_overview: list[str] = []
        rev_now = kp.get("revenue") or 0
        rev_prev = kp.get("prev_revenue") or 0
        if rev_prev > 0:
            growth = (rev_now - rev_prev) / rev_prev * 100
            direction = "tăng" if growth >= 0 else "giảm"
            insights_overview.append(
                f"Doanh thu **{direction} {abs(growth):.1f}%** vs kỳ trước "
                f"({fmt_vnd(rev_now)} vs {fmt_vnd(rev_prev)})."
            )
        aov_now = kp.get("aov") or 0
        aov_prev = kp.get("prev_aov") or 0
        if aov_prev > 0:
            d = (aov_now - aov_prev) / aov_prev * 100
            if abs(d) >= 0.5:
                insights_overview.append(
                    f"AOV **{d:+.1f}%** — {'khách chi tiêu nhiều hơn' if d > 0 else 'khách đang mua nhỏ hơn'}."
                )
        if period_days == 1:
            if not df_hour.empty and df_hour["revenue_now"].sum() > 0:
                idx_peak = int(df_hour["revenue_now"].idxmax())
                peak_hour = int(df_hour.loc[idx_peak, "hour"])
                insights_overview.append(
                    f"Khung giờ bán tốt nhất: **{peak_hour:02d}h–{peak_hour + 1:02d}h** "
                    f"({fmt_vnd(df_hour.loc[idx_peak, 'revenue_now'])})."
                )
        else:
            if not df_trend.empty and df_trend["revenue_now"].sum() > 0:
                idx_peak = int(df_trend["revenue_now"].idxmax())
                peak_day = df_trend.loc[idx_peak, "day_now"]
                insights_overview.append(
                    f"Ngày bán tốt nhất kỳ: **{pd.to_datetime(peak_day):%d/%m}** "
                    f"({fmt_vnd(df_trend.loc[idx_peak, 'revenue_now'])})."
                )
        if not df_store_overview.empty:
            top_s = df_store_overview.iloc[0]
            pct = top_s["revenue"] / df_store_overview["revenue"].sum() * 100
            insights_overview.append(
                f"Cửa hàng **{top_s['store']}** dẫn đầu — chiếm {pct:.1f}% tổng DT."
            )
            if len(df_store_overview) > 1:
                bot_s = df_store_overview.iloc[-1]
                insights_overview.append(
                    f"Cửa hàng cuối bảng: **{bot_s['store']}** "
                    f"({fmt_vnd(bot_s['revenue'])}) — cần xem xét lý do."
                )
        for line in insights_overview or ["*Không đủ dữ liệu để sinh insight.*"]:
            st.markdown(f"- {line}")

# ---- Tab 2: Sản phẩm (Pareto + Top/Bottom + Matrix) -----------------------
with tab_product, safe_section("Tab Sản phẩm"):
    df_par = Q.pareto_products(start_date, end_date, store_keys)
    if df_par.empty:
        st.info("Không có dữ liệu sản phẩm.")
    else:
        df_par = df_par.sort_values("revenue", ascending=False).reset_index(drop=True)
        df_par["rank"] = df_par.index + 1
        df_par["cum_revenue"] = df_par["revenue"].cumsum()
        df_par["cum_pct"] = df_par["cum_revenue"] / df_par["revenue"].sum()
        n_products_80 = int((df_par["cum_pct"] < 0.8).sum()) + 1
        share_products_80 = n_products_80 / len(df_par) * 100

        # ---- Row 1: Pareto ----
        st.subheader("Phân tích Pareto — 80/20")
        st.caption(
            f"Bao nhiêu SKU tạo ra 80% doanh thu? → **{n_products_80}/{len(df_par)} SP "
            f"({share_products_80:.1f}%)** đóng góp 80%. "
            "Càng ít SP càng tập trung → rủi ro phụ thuộc; càng nhiều → portfolio dàn trải."
        )
        fig_par = go.Figure()
        fig_par.add_trace(
            go.Bar(
                x=df_par["product"],
                y=df_par["revenue"],
                name="Doanh thu",
                marker_color="#b08968",
                hovertemplate="%{x}<br>DT: %{y:,.0f} ₫<extra></extra>",
            )
        )
        fig_par.add_trace(
            go.Scatter(
                x=df_par["product"],
                y=df_par["cum_pct"],
                name="% tích lũy",
                mode="lines+markers",
                yaxis="y2",
                line=dict(color="#d97706", width=2),
                hovertemplate="%{x}<br>Tích lũy: %{y:.1%}<extra></extra>",
            )
        )
        fig_par.add_hline(
            y=0.8,
            line_dash="dash",
            line_color="#ef4444",
            yref="y2",
            annotation_text="80%",
            annotation_position="top right",
        )
        fig_par.update_layout(
            height=420,
            margin=dict(l=10, r=10, t=10, b=10),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
            xaxis=dict(title=None, tickangle=-40),
            yaxis=dict(title="Doanh thu (₫)"),
            yaxis2=dict(
                title="% tích lũy",
                overlaying="y",
                side="right",
                tickformat=".0%",
                range=[0, 1.05],
                showgrid=False,
            ),
        )
        st.plotly_chart(fig_par, use_container_width=True)

        # ---- Row 2: Top / Bottom ----
        st.subheader("Best sellers vs Cần chú ý")
        top_n = st.slider("Số SP hiển thị mỗi bảng", 5, 15, 10, key="sp_top_n")
        top_df = df_par.head(top_n)
        bot_df = df_par.tail(top_n).iloc[::-1]

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Top {top_n} — Best sellers**")
            fig_tp = px.bar(
                top_df.sort_values("revenue"),
                x="revenue",
                y="product",
                orientation="h",
                color="revenue",
                color_continuous_scale="Oranges",
                text="quantity",
                labels={"revenue": "Doanh thu (₫)", "product": ""},
            )
            fig_tp.update_traces(
                texttemplate="SL %{text:,}", textposition="outside"
            )
            fig_tp.update_layout(
                height=420,
                margin=dict(l=10, r=10, t=10, b=10),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig_tp, use_container_width=True)

        with col2:
            st.markdown(f"**Bottom {top_n} — Cần rà soát**")
            st.caption(
                "Doanh thu thấp → xem lại vị trí trưng bày, giá hoặc cân nhắc ngừng bán."
            )
            fig_bt = px.bar(
                bot_df.sort_values("revenue", ascending=True),
                x="revenue",
                y="product",
                orientation="h",
                color="revenue",
                color_continuous_scale="Greys",
                text="quantity",
                labels={"revenue": "Doanh thu (₫)", "product": ""},
            )
            fig_bt.update_traces(
                texttemplate="SL %{text:,}", textposition="outside"
            )
            fig_bt.update_layout(
                height=420,
                margin=dict(l=10, r=10, t=10, b=10),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig_bt, use_container_width=True)

        # ---- Row 3: Price × Volume matrix ----
        st.subheader("Ma trận Giá × Sản lượng")
        st.caption(
            "Bubble = doanh thu. **Góc trên-phải** = stars (giá cao + bán nhiều). "
            "**Góc trên-trái** = premium niche. **Góc dưới-phải** = volume drivers (giá rẻ, bán ồ ạt)."
        )
        fig_mx = px.scatter(
            df_par,
            x="quantity",
            y="unit_price",
            size="revenue",
            color="revenue",
            color_continuous_scale="Oranges",
            hover_name="product",
            labels={
                "quantity": "Số lượng bán",
                "unit_price": "Đơn giá (₫)",
                "revenue": "Doanh thu",
            },
            size_max=40,
        )
        fig_mx.update_layout(
            height=460, margin=dict(l=10, r=10, t=10, b=10)
        )
        st.plotly_chart(fig_mx, use_container_width=True)

# ---- Tab 3: Cửa hàng (Leaderboard + Efficiency) ---------------------------
with tab_store, safe_section("Tab Cửa hàng"):
    df_st = Q.revenue_by_store(start_date, end_date, store_keys)
    if df_st.empty:
        st.info("Không có dữ liệu cửa hàng.")
    else:
        df_st = df_st.sort_values("revenue", ascending=False).reset_index(drop=True)
        df_st["rank"] = df_st.index + 1
        df_st["share"] = df_st["revenue"] / df_st["revenue"].sum()

        # ---- Leaderboard ----
        st.subheader("Bảng xếp hạng cửa hàng")
        leaderboard = df_st[
            ["rank", "store", "revenue", "orders", "aov", "share"]
        ].rename(
            columns={
                "rank": "#",
                "store": "Cửa hàng",
                "revenue": "Doanh thu",
                "orders": "Số đơn",
                "aov": "AOV",
                "share": "Tỷ trọng",
            }
        )
        st.dataframe(
            leaderboard,
            use_container_width=True,
            hide_index=True,
            column_config={
                "#": st.column_config.NumberColumn(width="small"),
                "Doanh thu": st.column_config.NumberColumn(format="%d ₫"),
                "Số đơn": st.column_config.NumberColumn(format="%d"),
                "AOV": st.column_config.NumberColumn(format="%d ₫"),
                "Tỷ trọng": st.column_config.ProgressColumn(
                    format="%.1f%%", min_value=0.0, max_value=1.0
                ),
            },
        )

        # ---- Biểu đồ so sánh + efficiency scatter ----
        c_l, c_r = st.columns([3, 2])
        with c_l:
            st.subheader("So sánh doanh thu")
            # Tên ngắn cho tick X (tránh truncate); tên đầy đủ ở hover.
            df_st = df_st.copy()
            df_st["short_name"] = df_st["store"].map(short_store_name)
            fig_bar = px.bar(
                df_st,
                x="short_name",
                y="revenue",
                labels={"short_name": "", "revenue": "Doanh thu (₫)"},
                color="revenue",
                color_continuous_scale="Oranges",
                text="revenue",
                hover_data={"store": True, "short_name": False},
            )
            fig_bar.update_traces(
                texttemplate="%{text:,.0f}", textposition="outside"
            )
            fig_bar.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        with c_r:
            st.subheader("Hiệu quả — Traffic × AOV")
            st.caption(
                "Bubble = DT. Góc trên-phải = vừa đông vừa mua lớn (ngôi sao)."
            )
            fig_sc = px.scatter(
                df_st,
                x="orders",
                y="aov",
                size="revenue",
                color="store",
                hover_name="store",
                labels={
                    "orders": "Số đơn (traffic)",
                    "aov": "AOV (₫)",
                },
                size_max=45,
            )
            # Đường trung vị để phân 4 góc
            fig_sc.add_hline(
                y=df_st["aov"].median(),
                line_dash="dot",
                line_color="#9ca3af",
            )
            fig_sc.add_vline(
                x=df_st["orders"].median(),
                line_dash="dot",
                line_color="#9ca3af",
            )
            fig_sc.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                showlegend=False,
            )
            st.plotly_chart(fig_sc, use_container_width=True)

# ---- Tab 4: Khách hàng (Tier mix + Whales + Loyalty) ----------------------
with tab_customer, safe_section("Tab Khách hàng"):
    df_tier = Q.customers_by_tier(start_date, end_date, store_keys)
    df_top_cust = Q.top_customers(start_date, end_date, store_keys, top_n=10)
    df_freq = Q.purchase_frequency(start_date, end_date, store_keys)

    if df_tier.empty and df_freq.empty:
        st.info("Không có dữ liệu khách hàng.")
    else:
        # ---- KPI loyalty ----
        total_customers = int(df_freq["customers"].sum()) if not df_freq.empty else 0
        returning_customers = (
            int(df_freq.loc[df_freq["bucket_order"] > 1, "customers"].sum())
            if not df_freq.empty
            else 0
        )
        return_rate = (
            returning_customers / total_customers if total_customers else 0
        )
        aov_max = df_tier["aov"].max() if not df_tier.empty else 0
        top_aov_tier = (
            df_tier.loc[df_tier["aov"].idxmax(), "tier"]
            if not df_tier.empty and aov_max > 0
            else "—"
        )

        kcol1, kcol2, kcol3, kcol4 = st.columns(4)
        kcol1.metric("Tổng khách unique", fmt_int(total_customers))
        kcol2.metric(
            "Khách quay lại (≥2 đơn)",
            fmt_int(returning_customers),
            delta=f"{return_rate*100:.1f}%" if total_customers else None,
            delta_color="off",
        )
        kcol3.metric(
            "Tier AOV cao nhất",
            str(top_aov_tier),
            delta=fmt_vnd(aov_max) if aov_max else None,
            delta_color="off",
        )
        kcol4.metric(
            "Số đơn / khách TB",
            f"{(df_freq['customers'] * df_freq['bucket_order']).sum() / total_customers:.2f}"
            if total_customers
            else "—",
        )

        st.divider()

        # ---- Tier analysis ----
        col1, col2 = st.columns([3, 2])
        with col1:
            st.subheader("Đóng góp doanh thu theo hạng khách")
            if df_tier.empty:
                st.info("Chưa có dữ liệu tier.")
            else:
                df_tier_s = df_tier.sort_values("revenue", ascending=True)
                fig_tier = go.Figure()
                fig_tier.add_trace(
                    go.Bar(
                        x=df_tier_s["revenue"],
                        y=df_tier_s["tier"],
                        orientation="h",
                        marker=dict(
                            color=df_tier_s["revenue"],
                            colorscale="Oranges",
                            showscale=False,
                        ),
                        text=df_tier_s["aov"].apply(fmt_vnd),
                        textposition="outside",
                        hovertemplate=(
                            "%{y}<br>"
                            "DT: %{x:,.0f} ₫<br>"
                            "%{customdata[0]:,} khách · %{customdata[1]:,} đơn<extra></extra>"
                        ),
                        customdata=df_tier_s[["customers", "orders"]].values,
                        name="Doanh thu",
                    )
                )
                fig_tier.update_layout(
                    height=380,
                    margin=dict(l=10, r=10, t=10, b=10),
                    xaxis=dict(title="Doanh thu (₫)"),
                    yaxis=dict(title=None),
                )
                st.plotly_chart(fig_tier, use_container_width=True)

        with col2:
            st.subheader("Tần suất mua — Loyalty")
            if df_freq.empty:
                st.info("Chưa có dữ liệu tần suất.")
            else:
                fig_fr = px.bar(
                    df_freq.sort_values("bucket_order"),
                    x="bucket",
                    y="customers",
                    color="bucket_order",
                    color_continuous_scale="Oranges",
                    labels={"bucket": "", "customers": "Số khách"},
                    text="customers",
                )
                fig_fr.update_traces(
                    texttemplate="%{text:,}", textposition="outside"
                )
                fig_fr.update_layout(
                    height=380,
                    margin=dict(l=10, r=10, t=10, b=10),
                    coloraxis_showscale=False,
                )
                st.plotly_chart(fig_fr, use_container_width=True)

        # ---- Top whales ----
        st.subheader("Top 10 khách hàng VIP")
        if df_top_cust.empty:
            st.info("Chưa có dữ liệu top khách.")
        else:
            top_show = df_top_cust.rename(
                columns={
                    "customer": "Khách hàng",
                    "tier": "Hạng",
                    "revenue": "Doanh thu",
                    "orders": "Số đơn",
                    "quantity": "SL SP",
                    "aov": "AOV",
                }
            )
            st.dataframe(
                top_show,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Doanh thu": st.column_config.NumberColumn(format="%d ₫"),
                    "AOV": st.column_config.NumberColumn(format="%d ₫"),
                    "Số đơn": st.column_config.NumberColumn(format="%d"),
                    "SL SP": st.column_config.NumberColumn(format="%d"),
                },
            )

# ---- Tab 5: Hành vi mua (DOW + Hour + Heatmap + Payment) -----------------
with tab_behavior, safe_section("Tab Hành vi mua"):
    df_dow = Q.revenue_by_dow(start_date, end_date, store_keys)
    df_hour = Q.revenue_by_hour(start_date, end_date, store_keys)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Doanh thu theo thứ trong tuần")
        if df_dow.empty:
            st.info("Không có dữ liệu.")
        else:
            df_dow["dow_label"] = df_dow["dow"].map(DOW_LABELS)
            df_dow = df_dow.set_index("dow").reindex([2, 3, 4, 5, 6, 7, 1]).reset_index()
            fig_dow = px.bar(
                df_dow,
                x="dow_label",
                y="revenue",
                labels={"dow_label": "", "revenue": "Doanh thu (₫)"},
                color="revenue",
                color_continuous_scale="Oranges",
                text="orders",
            )
            fig_dow.update_traces(
                texttemplate="%{text:,} đơn", textposition="outside"
            )
            fig_dow.update_layout(
                height=360,
                margin=dict(l=10, r=10, t=10, b=10),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig_dow, use_container_width=True)

    with c2:
        st.subheader("Traffic theo giờ")
        if df_hour.empty:
            st.info("Không có dữ liệu.")
        else:
            fig_hr = go.Figure()
            fig_hr.add_trace(
                go.Bar(
                    x=df_hour["hour"],
                    y=df_hour["orders"],
                    name="Số đơn",
                    marker_color="#b08968",
                    yaxis="y",
                    hovertemplate="%{x}h<br>%{y:,} đơn<extra></extra>",
                )
            )
            fig_hr.add_trace(
                go.Scatter(
                    x=df_hour["hour"],
                    y=df_hour["revenue"],
                    name="Doanh thu",
                    mode="lines+markers",
                    yaxis="y2",
                    line=dict(color="#d97706", width=2),
                    hovertemplate="%{x}h<br>%{y:,.0f} ₫<extra></extra>",
                )
            )
            fig_hr.update_layout(
                height=360,
                margin=dict(l=10, r=10, t=10, b=10),
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                xaxis=dict(
                    title="Giờ",
                    tickmode="linear",
                    tick0=0,
                    dtick=1,
                    tickformat="d",
                ),
                yaxis=dict(title="Số đơn"),
                yaxis2=dict(
                    title="Doanh thu (₫)",
                    overlaying="y",
                    side="right",
                    showgrid=False,
                ),
            )
            st.plotly_chart(fig_hr, use_container_width=True)

    # ---- Heatmap Thứ × Giờ ----
    st.subheader("Heatmap doanh thu — Thứ × Giờ")
    hm = Q.heatmap_hour_dow(start_date, end_date, store_keys)
    if hm.empty:
        st.info("Không có dữ liệu heatmap.")
    else:
        hm["dow_label"] = hm["dow"].map(DOW_LABELS)
        pivot = hm.pivot_table(
            index="dow_label",
            columns="hour",
            values="revenue",
            aggfunc="sum",
            fill_value=0,
        ).reindex(index=[DOW_LABELS[i] for i in (2, 3, 4, 5, 6, 7, 1)])
        fig_hm = go.Figure(
            data=go.Heatmap(
                z=pivot.values,
                x=[f"{h:02d}h" for h in pivot.columns],
                y=pivot.index,
                colorscale="Oranges",
                hovertemplate="%{y} %{x}<br>Doanh thu: %{z:,.0f} ₫<extra></extra>",
            )
        )
        fig_hm.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig_hm, use_container_width=True)

    # ---- Payment ----
    st.subheader("Phương thức thanh toán")
    df_pm = Q.revenue_by_payment(start_date, end_date, store_keys)
    if df_pm.empty:
        st.info("Không có dữ liệu thanh toán.")
    else:
        pc1, pc2 = st.columns(2)
        with pc1:
            fig_pm = px.pie(
                df_pm,
                names="method",
                values="revenue",
                hole=0.55,
                color_discrete_sequence=px.colors.sequential.Oranges_r,
            )
            fig_pm.update_traces(
                textposition="outside",
                textinfo="label+percent",
                hovertemplate="%{label}<br>%{value:,.0f} ₫<extra></extra>",
            )
            fig_pm.update_layout(
                height=340, margin=dict(l=10, r=10, t=10, b=10), showlegend=False
            )
            st.plotly_chart(fig_pm, use_container_width=True)
        with pc2:
            fig_pm2 = px.bar(
                df_pm.sort_values("orders"),
                x="orders",
                y="method",
                orientation="h",
                labels={"orders": "Số đơn", "method": ""},
                color="orders",
                color_continuous_scale="Greens",
            )
            fig_pm2.update_layout(
                height=340,
                margin=dict(l=10, r=10, t=10, b=10),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig_pm2, use_container_width=True)

# ---- Tab 6: Tác động gợi ý ------------------------------------------------
with tab_suggestion, safe_section("Tab Gợi ý"):
    st.caption(
        "Đo lường tác động của **hệ thống gợi ý sản phẩm** "
        "(silver gắn `is_suggestion = true` cho dòng bán ra từ gợi ý). "
        "So sánh gợi ý vs khách tự chọn — tìm dấu hiệu upsell hiệu quả."
    )

    kpi_sugg = Q.suggestion_kpis(start_date, end_date, store_keys)

    if kpi_sugg.empty or (kpi_sugg.iloc[0].get("total_revenue") or 0) == 0:
        st.info("Không có dữ liệu trong khoảng đã chọn.")
    else:
        k = kpi_sugg.iloc[0].to_dict()
        SUGG_COLOR = "#d97706"
        SELF_COLOR = "#78716c"

        # ---- Row 1: 5 KPI cards ------------------------------------------
        def fmt_ratio(r: float | None) -> str:
            """Adaptive precision: tỷ lệ ≥ 1% → 1 số thập phân, < 1% → 2 số
            thập phân. Tránh "0.0%" khi con số thật là 0.04% (làm tròn .1f)."""
            r = float(r or 0) * 100
            if r >= 1 or r == 0:
                return f"{r:.1f}%"
            return f"{r:.2f}%"

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric(
            "Doanh thu từ gợi ý",
            fmt_vnd(k["sugg_revenue"]),
            delta=f"{fmt_ratio(k['sugg_revenue_share'])} tổng DT",
            delta_color="off",
        )
        k2.metric(
            "Đơn có gợi ý",
            fmt_int(k["sugg_orders"]),
            delta=f"{fmt_ratio(k['sugg_order_share'])} tổng đơn",
            delta_color="off",
        )
        k3.metric(
            "SP gợi ý bán ra",
            fmt_int(k["sugg_qty"]),
            delta=f"{fmt_ratio(k['sugg_qty_share'])} tổng SL",
            delta_color="off",
        )
        uplift = k.get("line_value_uplift") or 0
        k4.metric(
            "Giá trị TB dòng gợi ý",
            fmt_vnd(k["avg_sugg_line"]),
            delta=f"{uplift*100:+.1f}% vs tự chọn",
        )
        k5.metric(
            "Giá trị TB dòng tự chọn",
            fmt_vnd(k["avg_self_line"]),
        )

        st.divider()

        # ---- Row 2: Xu hướng (toggle absolute / %) -----------------------
        # Kỳ = 1 ngày → suggestion_by_day chỉ trả 1 row, area chart thành 1 điểm
        # rỗng. Switch granularity sang theo GIỜ trong ngày (0..23) — ăn khớp
        # với pattern ở tab "Tổng quan" và dùng chung data với Row 4 bên dưới.
        header_col, toggle_col = st.columns([4, 2])
        header_col.subheader(
            "Xu hướng doanh thu theo giờ — Gợi ý vs Tự chọn"
            if period_days == 1
            else "Xu hướng doanh thu — Gợi ý vs Tự chọn"
        )
        mode = toggle_col.radio(
            "Hiển thị",
            ["Giá trị tuyệt đối", "Tỷ lệ %"],
            horizontal=True,
            label_visibility="collapsed",
            key="trend_mode",
        )

        if period_days == 1:
            df_sugg_src = Q.suggestion_by_hour(start_date, end_date, store_keys)
            x_col, x_label = "hour", "Giờ trong ngày"
        else:
            df_sugg_src = Q.suggestion_by_day(start_date, end_date, store_keys)
            x_col, x_label = "day", "Ngày"

        if not df_sugg_src.empty:
            df_long = df_sugg_src.melt(
                id_vars=x_col,
                value_vars=["sugg_revenue", "self_revenue"],
                var_name="source",
                value_name="revenue",
            )
            df_long["source"] = df_long["source"].map(
                {"sugg_revenue": "Gợi ý", "self_revenue": "Khách tự chọn"}
            )

            as_ratio = mode == "Tỷ lệ %"
            # groupnorm='fraction' → plotly tự normalize thành 0–1 per x. Gợi ý
            # chiếm đúng phần đáng chú ý thay vì bị đè bởi dải "Tự chọn".
            fig_area = px.area(
                df_long,
                x=x_col,
                y="revenue",
                color="source",
                groupnorm="fraction" if as_ratio else None,
                labels={
                    x_col: x_label,
                    "revenue": "% doanh thu" if as_ratio else "Doanh thu (₫)",
                    "source": "",
                },
                color_discrete_map={"Gợi ý": SUGG_COLOR, "Khách tự chọn": SELF_COLOR},
                category_orders={"source": ["Khách tự chọn", "Gợi ý"]},
            )
            if as_ratio:
                fig_area.update_yaxes(tickformat=".0%", range=[0, 1])
                fig_area.update_traces(hovertemplate="%{y:.1%}<extra>%{fullData.name}</extra>")
            else:
                fig_area.update_traces(
                    hovertemplate="%{y:,.0f} ₫<extra>%{fullData.name}</extra>"
                )
            fig_area.update_layout(
                height=340,
                margin=dict(l=10, r=10, t=10, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                hovermode="x unified",
            )
            if period_days == 1:
                # Force tick mỗi 2 giờ cho dễ đọc, range full 0..23.
                fig_area.update_xaxes(
                    tickmode="array",
                    tickvals=list(range(0, 24, 2)),
                    ticktext=[f"{h:02d}h" for h in range(0, 24, 2)],
                    range=[-0.5, 23.5],
                )
            st.plotly_chart(fig_area, use_container_width=True)

        # ---- Row 3: Top SP gợi ý + Store breakdown -----------------------
        col_l, col_r = st.columns(2)

        with col_l:
            st.subheader("Top 10 sản phẩm bán từ gợi ý")
            df_top = Q.top_suggested_products(start_date, end_date, store_keys, top_n=10)
            if df_top.empty:
                st.info("Chưa có SP bán ra từ gợi ý.")
            else:
                fig_top = px.bar(
                    df_top.sort_values("revenue"),
                    x="revenue",
                    y="product",
                    orientation="h",
                    labels={"revenue": "Doanh thu từ gợi ý (₫)", "product": ""},
                    color="revenue",
                    color_continuous_scale="Oranges",
                    text="quantity",
                )
                fig_top.update_traces(
                    texttemplate="SL %{text:,}", textposition="outside"
                )
                fig_top.update_layout(
                    height=420,
                    margin=dict(l=10, r=10, t=10, b=10),
                    coloraxis_showscale=False,
                )
                st.plotly_chart(fig_top, use_container_width=True)

        with col_r:
            st.subheader("Tỉ lệ đóng góp gợi ý theo cửa hàng")
            df_store_sugg = Q.suggestion_by_store(start_date, end_date, store_keys)
            if df_store_sugg.empty:
                st.info("Chưa có dữ liệu theo cửa hàng.")
            else:
                df_store_sugg = df_store_sugg.sort_values("sugg_share").copy()
                # Tên ngắn cho tick Y — tránh bị Plotly cắt đuôi tỉnh/TP.
                df_store_sugg["short_name"] = df_store_sugg["store"].map(
                    short_store_name
                )
                fig_store = px.bar(
                    df_store_sugg,
                    x="sugg_share",
                    y="short_name",
                    orientation="h",
                    labels={"sugg_share": "% DT từ gợi ý", "short_name": ""},
                    color="sugg_share",
                    color_continuous_scale="Oranges",
                    text="sugg_share",
                    hover_data={
                        "store": True,
                        "short_name": False,
                        "sugg_revenue": ":,.0f",
                        "total_revenue": ":,.0f",
                        "sugg_share": False,
                    },
                )
                fig_store.update_traces(
                    texttemplate="%{text:.1%}", textposition="outside"
                )
                fig_store.update_layout(
                    height=420,
                    margin=dict(l=10, r=10, t=10, b=10),
                    coloraxis_showscale=False,
                    xaxis_tickformat=".0%",
                )
                st.plotly_chart(fig_store, use_container_width=True)

        # ---- Row 4: Heatmap giờ trong ngày -------------------------------
        st.subheader("Khung giờ gợi ý hoạt động mạnh")
        df_hour_sugg = Q.suggestion_by_hour(start_date, end_date, store_keys)
        if not df_hour_sugg.empty:
            df_hour_sugg["share"] = (
                df_hour_sugg["sugg_revenue"]
                / df_hour_sugg["total_revenue"].replace(0, pd.NA)
            ).fillna(0)

            fig_hour = go.Figure()
            fig_hour.add_trace(
                go.Bar(
                    x=df_hour_sugg["hour"],
                    y=df_hour_sugg["sugg_revenue"],
                    name="Gợi ý",
                    marker_color=SUGG_COLOR,
                    hovertemplate="%{x}h<br>Gợi ý: %{y:,.0f} ₫<extra></extra>",
                )
            )
            fig_hour.add_trace(
                go.Bar(
                    x=df_hour_sugg["hour"],
                    y=df_hour_sugg["self_revenue"],
                    name="Khách tự chọn",
                    marker_color=SELF_COLOR,
                    hovertemplate="%{x}h<br>Tự chọn: %{y:,.0f} ₫<extra></extra>",
                )
            )
            fig_hour.add_trace(
                go.Scatter(
                    x=df_hour_sugg["hour"],
                    y=df_hour_sugg["share"],
                    name="% gợi ý",
                    mode="lines+markers",
                    yaxis="y2",
                    line=dict(color="#0ea5e9", width=2),
                    marker=dict(size=7),
                    hovertemplate="%{x}h<br>%{y:.1%} DT từ gợi ý<extra></extra>",
                )
            )
            fig_hour.update_layout(
                # Grouped (side-by-side) thay vì stack — cột "Gợi ý" không
                # bị "nuốt" bởi "Tự chọn" khi tỉ trọng nhỏ.
                barmode="group",
                bargap=0.25,
                bargroupgap=0.05,
                height=360,
                margin=dict(l=10, r=10, t=10, b=10),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.0,
                    x=0,
                    # itemclick=toggle giữ nguyên mặc định — user click 1
                    # trace là ẩn; doubleclick="toggleothers" cho phép
                    # isolate (ẩn tất cả trace khác) → so sánh trace bé dễ.
                    itemdoubleclick="toggleothers",
                ),
                xaxis=dict(
                    title="Giờ trong ngày",
                    tickmode="linear",
                    tick0=0,
                    dtick=1,
                    tickformat="d",
                ),
                # Cả 2 trục đều autorange=True để khi user toggle legend
                # ẩn trace giá trị lớn, trục tự co lại theo trace còn lại
                # → trace nhỏ "nở" ra, đọc variation rõ hơn.
                # rangemode="tozero" giữ gốc toạ độ ở 0 để bar không bị
                # âm chân, đồng thời line % vẫn bắt đầu từ 0%.
                yaxis=dict(
                    title="Doanh thu (₫)",
                    autorange=True,
                    rangemode="tozero",
                ),
                yaxis2=dict(
                    title="% gợi ý",
                    overlaying="y",
                    side="right",
                    tickformat=".0%",
                    autorange=True,
                    rangemode="tozero",
                    showgrid=False,
                ),
                hovermode="x unified",
                # Giữ trạng thái zoom / legend khi Streamlit rerun (đổi
                # filter không liên quan) — user thao tác isolate xong
                # không bị reset về full-view sau 1 rerun ngẫu nhiên.
                uirevision="suggestion_by_hour",
            )
            st.plotly_chart(fig_hour, use_container_width=True)

        # ---- Row 5: Auto insight callout ---------------------------------
        insights: list[str] = []
        rev_share = (k.get("sugg_revenue_share") or 0) * 100
        order_share = (k.get("sugg_order_share") or 0) * 100
        insights.append(
            f"**{rev_share:.1f}%** doanh thu trong kỳ đến từ gợi ý "
            f"(≈ {fmt_vnd(k['sugg_revenue'])} / {fmt_vnd(k['total_revenue'])})."
        )
        insights.append(
            f"**{order_share:.1f}%** số đơn có ít nhất một SP được gợi ý "
            f"({fmt_int(k['sugg_orders'])} / {fmt_int(k['total_orders'])} đơn)."
        )
        if uplift != 0:
            direction = "cao hơn" if uplift > 0 else "thấp hơn"
            insights.append(
                f"Giá trị trung bình một dòng bán ra từ gợi ý {direction} dòng khách tự chọn "
                f"**{abs(uplift)*100:.1f}%** ({fmt_vnd(k['avg_sugg_line'])} vs {fmt_vnd(k['avg_self_line'])})."
            )
        if not df_store_sugg.empty:
            top_store = df_store_sugg.iloc[-1]
            insights.append(
                f"Cửa hàng **{top_store['store']}** tận dụng gợi ý tốt nhất — "
                f"{top_store['sugg_share']*100:.1f}% doanh thu đến từ gợi ý."
            )
        if not df_hour_sugg.empty and df_hour_sugg["sugg_revenue"].sum() > 0:
            best = df_hour_sugg.loc[df_hour_sugg["sugg_revenue"].idxmax()]
            insights.append(
                f"Khung giờ gợi ý bán chạy nhất: **{int(best['hour']):02d}h** "
                f"({fmt_vnd(best['sugg_revenue'])})."
            )

        with st.container():
            st.markdown("#### Insight")
            for line in insights:
                st.markdown(f"- {line}")
