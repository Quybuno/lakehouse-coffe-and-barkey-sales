"""
KD Bakery & Coffee — Gold Layer Dashboard

Streamlit app query Iceberg tables qua Trino.
Kiến trúc: MinIO(Iceberg) ← Trino ← Streamlit.

Chạy: `streamlit run app.py` (hoặc qua docker compose service `streamlit`).
Mở: http://localhost:8501
"""
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


def fmt_vnd(so: float | int | None) -> str:
    if so is None or pd.isna(so):
        return "—"
    return f"{float(so):,.0f} ₫".replace(",", ".")


def fmt_int(so: float | int | None) -> str:
    if so is None or pd.isna(so):
        return "—"
    return f"{int(so):,}".replace(",", ".")


@contextmanager
def safe_section(ten: str):
    """
    Bọc 1 block UI — nếu Trino chết / query fail, hiển thị cảnh báo thân thiện
    thay vì crash toàn app. 1 tab lỗi không kéo các tab khác chết theo.
    """
    try:
        yield
    except TrinoConnectionError as e:
        st.error(
            f"**{ten}** — Mất kết nối Trino. "
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
            f"**{ten}** — Trino từ chối query: `{e.error_name}`. "
            "Kiểm tra schema hoặc filter hiện tại."
        )
        with st.expander("Chi tiết lỗi"):
            st.code(e.message)
    except Exception as e:  # noqa: BLE001
        st.warning(f"**{ten}** — lỗi không mong đợi: `{type(e).__name__}`")
        with st.expander("Traceback"):
            st.code(traceback.format_exc())


# ---------------- Sidebar (filter + trạng thái) ----------------------------

with st.sidebar:
    st.title("☕ KD Bakery & Coffee")
    st.caption("Gold Layer · Iceberg · Trino")

    ok, msg = ping()
    (st.success if ok else st.error)(msg)

    if not ok:
        st.info("Kiểm tra:\n- `docker ps` có `trino` + `iceberg-rest` running?\n- DAG `spark-batch-job` đã chạy để tạo bảng `iceberg.gold.*` chưa?")
        st.stop()

    tu_min, tu_max = Q.get_date_bounds()
    if tu_min is None:
        st.warning("Chưa có dữ liệu trong `iceberg.gold.fact_orders`.")
        st.stop()

    default_from = max(tu_min, tu_max - timedelta(days=30))
    khoang_ngay = st.date_input(
        "Khoảng ngày",
        value=(default_from, tu_max),
        min_value=tu_min,
        max_value=tu_max,
    )
    if isinstance(khoang_ngay, tuple) and len(khoang_ngay) == 2:
        tu_ngay, den_ngay = khoang_ngay
    else:
        tu_ngay = den_ngay = khoang_ngay  # type: ignore[assignment]

    stores_df = Q.get_stores()
    store_map = dict(zip(stores_df["store_name"], stores_df["store_key"]))
    stores_chon = st.multiselect(
        "Cửa hàng", options=list(store_map.keys()), default=[]
    )
    store_keys = [store_map[s] for s in stores_chon] or None

    if st.button("🔄 Refresh cache"):
        st.cache_data.clear()
        st.rerun()

    st.caption(f"Khoảng dữ liệu có sẵn: {tu_min} → {tu_max}")

# ---------------- Header + Period-over-Period KPIs -----------------------

st.title("Dashboard kinh doanh — KD Bakery & Coffee")

# Kỳ so sánh: cùng độ dài, liền kề trước kỳ hiện tại (WoW/MoM tự động).
so_ngay_ky = (den_ngay - tu_ngay).days + 1
prev_den = tu_ngay - timedelta(days=1)
prev_tu = prev_den - timedelta(days=so_ngay_ky - 1)

st.caption(
    f"**Kỳ phân tích:** {tu_ngay:%d/%m/%Y} → {den_ngay:%d/%m/%Y} ({so_ngay_ky} ngày) · "
    f"so sánh với **kỳ trước** {prev_tu:%d/%m/%Y} → {prev_den:%d/%m/%Y} · "
    f"phạm vi: {len(stores_chon) or 'tất cả'} cửa hàng"
)


def delta_pct(cur: float | None, prev: float | None) -> str | None:
    """Tạo chuỗi delta cho st.metric — Streamlit tự tô xanh/đỏ theo dấu."""
    if cur is None or prev is None or pd.isna(cur) or pd.isna(prev) or prev == 0:
        return None
    pct = (float(cur) - float(prev)) / float(prev) * 100
    return f"{pct:+.1f}% vs kỳ trước"


kp: dict = {}
with safe_section("KPI tổng hợp"):
    kpi_pop = Q.kpis_so_sanh(tu_ngay, den_ngay, prev_tu, prev_den, store_keys)
    kp = kpi_pop.iloc[0].to_dict() if not kpi_pop.empty else {}

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "Doanh thu",
        fmt_vnd(kp.get("doanh_thu")),
        delta=delta_pct(kp.get("doanh_thu"), kp.get("prev_doanh_thu")),
    )
    c2.metric(
        "Số đơn",
        fmt_int(kp.get("so_don")),
        delta=delta_pct(kp.get("so_don"), kp.get("prev_so_don")),
    )
    c3.metric(
        "Khách unique",
        fmt_int(kp.get("so_khach")),
        delta=delta_pct(kp.get("so_khach"), kp.get("prev_so_khach")),
    )
    c4.metric(
        "AOV (giá trị đơn TB)",
        fmt_vnd(kp.get("aov")),
        delta=delta_pct(kp.get("aov"), kp.get("prev_aov")),
    )
    c5.metric(
        "SP bán ra",
        fmt_int(kp.get("so_san_pham")),
        delta=delta_pct(kp.get("so_san_pham"), kp.get("prev_so_san_pham")),
    )

st.divider()

# ---------------- Tabs ------------------------------------------------------

(
    tab_tong_quan,
    tab_sp,
    tab_store,
    tab_khach,
    tab_hanh_vi,
    tab_goi_y,
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
with tab_tong_quan, safe_section("Tab Tổng quan"):
    st.subheader("Xu hướng doanh thu — Kỳ này vs Kỳ trước")
    df_trend = Q.doanh_thu_ngay_so_sanh(
        tu_ngay, den_ngay, prev_tu, prev_den, store_keys
    )
    if df_trend.empty or df_trend[["doanh_thu_now", "doanh_thu_prev"]].sum().sum() == 0:
        st.info("Không có dữ liệu trong khoảng đã chọn.")
    else:
        # MA 7 ngày giúp khử noise daily để nhìn xu hướng thật.
        df_trend["ma7_now"] = (
            df_trend["doanh_thu_now"].rolling(7, min_periods=1).mean()
        )

        fig_trend = go.Figure()
        fig_trend.add_trace(
            go.Scatter(
                x=df_trend["ngay_now"],
                y=df_trend["doanh_thu_prev"],
                name="Kỳ trước",
                mode="lines",
                line=dict(color="#d1d5db", width=2, dash="dot"),
                hovertemplate="Kỳ trước (%{customdata|%d/%m})<br>%{y:,.0f} ₫<extra></extra>",
                customdata=df_trend["ngay_prev"],
            )
        )
        fig_trend.add_trace(
            go.Scatter(
                x=df_trend["ngay_now"],
                y=df_trend["doanh_thu_now"],
                name="Kỳ này",
                mode="lines+markers",
                line=dict(color="#6f4e37", width=2.5),
                marker=dict(size=5),
                fill="tozeroy",
                fillcolor="rgba(111,78,55,0.12)",
                hovertemplate="Kỳ này (%{x|%d/%m})<br>%{y:,.0f} ₫<extra></extra>",
            )
        )
        fig_trend.add_trace(
            go.Scatter(
                x=df_trend["ngay_now"],
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
        df_st_tq = Q.doanh_thu_theo_store(tu_ngay, den_ngay, store_keys)
        if df_st_tq.empty:
            st.info("Không có dữ liệu cửa hàng.")
        else:
            fig_donut = px.pie(
                df_st_tq,
                names="cua_hang",
                values="doanh_thu",
                hole=0.55,
                color_discrete_sequence=px.colors.sequential.Oranges_r,
            )
            fig_donut.update_traces(
                textposition="outside",
                textinfo="label+percent",
                hovertemplate="%{label}<br>%{value:,.0f} ₫ (%{percent})<extra></extra>",
            )
            fig_donut.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                showlegend=False,
            )
            st.plotly_chart(fig_donut, use_container_width=True)

    with colR:
        st.subheader("Insight nhanh")
        insights_tq: list[str] = []
        dt_now = kp.get("doanh_thu") or 0
        dt_prev = kp.get("prev_doanh_thu") or 0
        if dt_prev > 0:
            growth = (dt_now - dt_prev) / dt_prev * 100
            chieu = "tăng" if growth >= 0 else "giảm"
            insights_tq.append(
                f"Doanh thu **{chieu} {abs(growth):.1f}%** vs kỳ trước "
                f"({fmt_vnd(dt_now)} vs {fmt_vnd(dt_prev)})."
            )
        aov_now = kp.get("aov") or 0
        aov_prev = kp.get("prev_aov") or 0
        if aov_prev > 0:
            d = (aov_now - aov_prev) / aov_prev * 100
            if abs(d) >= 0.5:
                insights_tq.append(
                    f"AOV **{d:+.1f}%** — {'khách chi tiêu nhiều hơn' if d > 0 else 'khách đang mua nhỏ hơn'}."
                )
        if not df_trend.empty and df_trend["doanh_thu_now"].sum() > 0:
            idx_peak = int(df_trend["doanh_thu_now"].idxmax())
            peak_day = df_trend.loc[idx_peak, "ngay_now"]
            insights_tq.append(
                f"Ngày bán tốt nhất kỳ: **{pd.to_datetime(peak_day):%d/%m}** "
                f"({fmt_vnd(df_trend.loc[idx_peak, 'doanh_thu_now'])})."
            )
        if not df_st_tq.empty:
            top_s = df_st_tq.iloc[0]
            pct = top_s["doanh_thu"] / df_st_tq["doanh_thu"].sum() * 100
            insights_tq.append(
                f"Cửa hàng **{top_s['cua_hang']}** dẫn đầu — chiếm {pct:.1f}% tổng DT."
            )
            if len(df_st_tq) > 1:
                bot_s = df_st_tq.iloc[-1]
                insights_tq.append(
                    f"Cửa hàng cuối bảng: **{bot_s['cua_hang']}** "
                    f"({fmt_vnd(bot_s['doanh_thu'])}) — cần xem xét lý do."
                )
        for line in insights_tq or ["*Không đủ dữ liệu để sinh insight.*"]:
            st.markdown(f"- {line}")

# ---- Tab 2: Sản phẩm (Pareto + Top/Bottom + Matrix) -----------------------
with tab_sp, safe_section("Tab Sản phẩm"):
    df_par = Q.pareto_san_pham(tu_ngay, den_ngay, store_keys)
    if df_par.empty:
        st.info("Không có dữ liệu sản phẩm.")
    else:
        df_par = df_par.sort_values("doanh_thu", ascending=False).reset_index(drop=True)
        df_par["rank"] = df_par.index + 1
        df_par["cum_dt"] = df_par["doanh_thu"].cumsum()
        df_par["cum_pct"] = df_par["cum_dt"] / df_par["doanh_thu"].sum()
        so_sp_80 = int((df_par["cum_pct"] < 0.8).sum()) + 1
        ty_le_sp_80 = so_sp_80 / len(df_par) * 100

        # ---- Row 1: Pareto ----
        st.subheader("Phân tích Pareto — 80/20")
        st.caption(
            f"Bao nhiêu SKU tạo ra 80% doanh thu? → **{so_sp_80}/{len(df_par)} SP "
            f"({ty_le_sp_80:.1f}%)** đóng góp 80%. "
            "Càng ít SP càng tập trung → rủi ro phụ thuộc; càng nhiều → portfolio dàn trải."
        )
        fig_par = go.Figure()
        fig_par.add_trace(
            go.Bar(
                x=df_par["san_pham"],
                y=df_par["doanh_thu"],
                name="Doanh thu",
                marker_color="#b08968",
                hovertemplate="%{x}<br>DT: %{y:,.0f} ₫<extra></extra>",
            )
        )
        fig_par.add_trace(
            go.Scatter(
                x=df_par["san_pham"],
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
                top_df.sort_values("doanh_thu"),
                x="doanh_thu",
                y="san_pham",
                orientation="h",
                color="doanh_thu",
                color_continuous_scale="Oranges",
                text="so_luong",
                labels={"doanh_thu": "Doanh thu (₫)", "san_pham": ""},
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
                bot_df.sort_values("doanh_thu", ascending=True),
                x="doanh_thu",
                y="san_pham",
                orientation="h",
                color="doanh_thu",
                color_continuous_scale="Greys",
                text="so_luong",
                labels={"doanh_thu": "Doanh thu (₫)", "san_pham": ""},
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
            x="so_luong",
            y="don_gia",
            size="doanh_thu",
            color="doanh_thu",
            color_continuous_scale="Oranges",
            hover_name="san_pham",
            labels={
                "so_luong": "Số lượng bán",
                "don_gia": "Đơn giá (₫)",
                "doanh_thu": "Doanh thu",
            },
            size_max=40,
        )
        fig_mx.update_layout(
            height=460, margin=dict(l=10, r=10, t=10, b=10)
        )
        st.plotly_chart(fig_mx, use_container_width=True)

# ---- Tab 3: Cửa hàng (Leaderboard + Efficiency) ---------------------------
with tab_store, safe_section("Tab Cửa hàng"):
    df_st = Q.doanh_thu_theo_store(tu_ngay, den_ngay, store_keys)
    if df_st.empty:
        st.info("Không có dữ liệu cửa hàng.")
    else:
        df_st = df_st.sort_values("doanh_thu", ascending=False).reset_index(drop=True)
        df_st["xep_hang"] = df_st.index + 1
        df_st["ty_trong"] = df_st["doanh_thu"] / df_st["doanh_thu"].sum()

        # ---- Leaderboard ----
        st.subheader("Bảng xếp hạng cửa hàng")
        leaderboard = df_st[
            ["xep_hang", "cua_hang", "doanh_thu", "so_don", "gia_tri_don_tb", "ty_trong"]
        ].rename(
            columns={
                "xep_hang": "#",
                "cua_hang": "Cửa hàng",
                "doanh_thu": "Doanh thu",
                "so_don": "Số đơn",
                "gia_tri_don_tb": "AOV",
                "ty_trong": "Tỷ trọng",
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
            fig_bar = px.bar(
                df_st,
                x="cua_hang",
                y="doanh_thu",
                labels={"cua_hang": "", "doanh_thu": "Doanh thu (₫)"},
                color="doanh_thu",
                color_continuous_scale="Oranges",
                text="doanh_thu",
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
                x="so_don",
                y="gia_tri_don_tb",
                size="doanh_thu",
                color="cua_hang",
                hover_name="cua_hang",
                labels={
                    "so_don": "Số đơn (traffic)",
                    "gia_tri_don_tb": "AOV (₫)",
                },
                size_max=45,
            )
            # Đường trung vị để phân 4 góc
            fig_sc.add_hline(
                y=df_st["gia_tri_don_tb"].median(),
                line_dash="dot",
                line_color="#9ca3af",
            )
            fig_sc.add_vline(
                x=df_st["so_don"].median(),
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
with tab_khach, safe_section("Tab Khách hàng"):
    df_tier = Q.khach_theo_tier(tu_ngay, den_ngay, store_keys)
    df_top_kh = Q.top_khach_hang(tu_ngay, den_ngay, store_keys, top_n=10)
    df_freq = Q.tan_suat_mua_khach(tu_ngay, den_ngay, store_keys)

    if df_tier.empty and df_freq.empty:
        st.info("Không có dữ liệu khách hàng.")
    else:
        # ---- KPI loyalty ----
        tong_kh = int(df_freq["so_khach"].sum()) if not df_freq.empty else 0
        kh_quay_lai = (
            int(df_freq.loc[df_freq["thu_tu"] > 1, "so_khach"].sum())
            if not df_freq.empty
            else 0
        )
        ty_le_quay_lai = kh_quay_lai / tong_kh if tong_kh else 0
        aov_max = df_tier["aov"].max() if not df_tier.empty else 0
        tier_cao_nhat = (
            df_tier.loc[df_tier["aov"].idxmax(), "tier"]
            if not df_tier.empty and aov_max > 0
            else "—"
        )

        kcol1, kcol2, kcol3, kcol4 = st.columns(4)
        kcol1.metric("Tổng khách unique", fmt_int(tong_kh))
        kcol2.metric(
            "Khách quay lại (≥2 đơn)",
            fmt_int(kh_quay_lai),
            delta=f"{ty_le_quay_lai*100:.1f}%" if tong_kh else None,
            delta_color="off",
        )
        kcol3.metric(
            "Tier AOV cao nhất",
            str(tier_cao_nhat),
            delta=fmt_vnd(aov_max) if aov_max else None,
            delta_color="off",
        )
        kcol4.metric(
            "Số đơn / khách TB",
            f"{(df_freq['so_khach'] * df_freq['thu_tu']).sum() / tong_kh:.2f}"
            if tong_kh
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
                df_tier_s = df_tier.sort_values("doanh_thu", ascending=True)
                fig_tier = go.Figure()
                fig_tier.add_trace(
                    go.Bar(
                        x=df_tier_s["doanh_thu"],
                        y=df_tier_s["tier"],
                        orientation="h",
                        marker=dict(
                            color=df_tier_s["doanh_thu"],
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
                        customdata=df_tier_s[["so_khach", "so_don"]].values,
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
                    df_freq.sort_values("thu_tu"),
                    x="nhom",
                    y="so_khach",
                    color="thu_tu",
                    color_continuous_scale="Oranges",
                    labels={"nhom": "", "so_khach": "Số khách"},
                    text="so_khach",
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
        if df_top_kh.empty:
            st.info("Chưa có dữ liệu top khách.")
        else:
            top_show = df_top_kh.rename(
                columns={
                    "khach_hang": "Khách hàng",
                    "tier": "Hạng",
                    "doanh_thu": "Doanh thu",
                    "so_don": "Số đơn",
                    "so_luong": "SL SP",
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
with tab_hanh_vi, safe_section("Tab Hành vi mua"):
    df_dow = Q.doanh_thu_theo_dow(tu_ngay, den_ngay, store_keys)
    df_hour = Q.doanh_thu_theo_gio(tu_ngay, den_ngay, store_keys)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Doanh thu theo thứ trong tuần")
        if df_dow.empty:
            st.info("Không có dữ liệu.")
        else:
            df_dow["thu_label"] = df_dow["dow"].map(DOW_LABELS)
            df_dow = df_dow.set_index("dow").reindex([2, 3, 4, 5, 6, 7, 1]).reset_index()
            fig_dow = px.bar(
                df_dow,
                x="thu_label",
                y="doanh_thu",
                labels={"thu_label": "", "doanh_thu": "Doanh thu (₫)"},
                color="doanh_thu",
                color_continuous_scale="Oranges",
                text="so_don",
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
                    x=df_hour["gio"],
                    y=df_hour["so_don"],
                    name="Số đơn",
                    marker_color="#b08968",
                    yaxis="y",
                    hovertemplate="%{x}h<br>%{y:,} đơn<extra></extra>",
                )
            )
            fig_hr.add_trace(
                go.Scatter(
                    x=df_hour["gio"],
                    y=df_hour["doanh_thu"],
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
    hm = Q.heatmap_gio_ngay(tu_ngay, den_ngay, store_keys)
    if hm.empty:
        st.info("Không có dữ liệu heatmap.")
    else:
        hm["thu_label"] = hm["thu"].map(DOW_LABELS)
        pivot = hm.pivot_table(
            index="thu_label",
            columns="gio",
            values="doanh_thu",
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
    df_pm = Q.doanh_thu_theo_payment(tu_ngay, den_ngay, store_keys)
    if df_pm.empty:
        st.info("Không có dữ liệu thanh toán.")
    else:
        pc1, pc2 = st.columns(2)
        with pc1:
            fig_pm = px.pie(
                df_pm,
                names="phuong_thuc",
                values="doanh_thu",
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
                df_pm.sort_values("so_don"),
                x="so_don",
                y="phuong_thuc",
                orientation="h",
                labels={"so_don": "Số đơn", "phuong_thuc": ""},
                color="so_don",
                color_continuous_scale="Greens",
            )
            fig_pm2.update_layout(
                height=340,
                margin=dict(l=10, r=10, t=10, b=10),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig_pm2, use_container_width=True)

# ---- Tab 6: Tác động gợi ý ------------------------------------------------
with tab_goi_y, safe_section("Tab Gợi ý"):
    st.caption(
        "Đo lường tác động của **hệ thống gợi ý sản phẩm** "
        "(silver gắn `is_suggestion = true` cho dòng bán ra từ gợi ý). "
        "So sánh gợi ý vs khách tự chọn — tìm dấu hiệu upsell hiệu quả."
    )

    kpi_gy = Q.goi_y_kpi(tu_ngay, den_ngay, store_keys)

    if kpi_gy.empty or (kpi_gy.iloc[0].get("tong_dt") or 0) == 0:
        st.info("Không có dữ liệu trong khoảng đã chọn.")
    else:
        k = kpi_gy.iloc[0].to_dict()
        SUGG_COLOR = "#d97706"
        SELF_COLOR = "#78716c"

        # ---- Row 1: 5 KPI cards ------------------------------------------
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric(
            "Doanh thu từ gợi ý",
            fmt_vnd(k["dt_goi_y"]),
            delta=f"{(k['ty_le_dt_goi_y'] or 0)*100:.1f}% tổng DT",
            delta_color="off",
        )
        k2.metric(
            "Đơn có gợi ý",
            fmt_int(k["don_co_goi_y"]),
            delta=f"{(k['ty_le_don_goi_y'] or 0)*100:.1f}% tổng đơn",
            delta_color="off",
        )
        k3.metric(
            "SP gợi ý bán ra",
            fmt_int(k["sl_goi_y"]),
            delta=f"{(k['ty_le_sl_goi_y'] or 0)*100:.1f}% tổng SL",
            delta_color="off",
        )
        uplift = k.get("uplift_gia_tri_dong") or 0
        k4.metric(
            "Giá trị TB dòng gợi ý",
            fmt_vnd(k["tb_dong_goi_y"]),
            delta=f"{uplift*100:+.1f}% vs tự chọn",
        )
        k5.metric(
            "Giá trị TB dòng tự chọn",
            fmt_vnd(k["tb_dong_tu_chon"]),
        )

        st.divider()

        # ---- Row 2: Xu hướng theo ngày (toggle absolute / %) -------------
        header_col, toggle_col = st.columns([4, 2])
        header_col.subheader("Xu hướng doanh thu — Gợi ý vs Tự chọn")
        che_do = toggle_col.radio(
            "Hiển thị",
            ["Giá trị tuyệt đối", "Tỷ lệ %"],
            horizontal=True,
            label_visibility="collapsed",
            key="xu_huong_mode",
        )

        df_gy_ngay = Q.goi_y_theo_ngay(tu_ngay, den_ngay, store_keys)
        if not df_gy_ngay.empty:
            df_long = df_gy_ngay.melt(
                id_vars="ngay",
                value_vars=["dt_goi_y", "dt_tu_chon"],
                var_name="nguon",
                value_name="doanh_thu",
            )
            df_long["nguon"] = df_long["nguon"].map(
                {"dt_goi_y": "Gợi ý", "dt_tu_chon": "Khách tự chọn"}
            )

            la_ty_le = che_do == "Tỷ lệ %"
            # groupnorm='fraction' → plotly tự normalize thành 0–1 per x. Gợi ý
            # chiếm đúng phần đáng chú ý thay vì bị đè bởi dải "Tự chọn".
            fig_area = px.area(
                df_long,
                x="ngay",
                y="doanh_thu",
                color="nguon",
                groupnorm="fraction" if la_ty_le else None,
                labels={
                    "ngay": "Ngày",
                    "doanh_thu": "% doanh thu" if la_ty_le else "Doanh thu (₫)",
                    "nguon": "",
                },
                color_discrete_map={"Gợi ý": SUGG_COLOR, "Khách tự chọn": SELF_COLOR},
                category_orders={"nguon": ["Khách tự chọn", "Gợi ý"]},
            )
            if la_ty_le:
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
            st.plotly_chart(fig_area, use_container_width=True)

        # ---- Row 3: Top SP gợi ý + Store breakdown -----------------------
        col_l, col_r = st.columns(2)

        with col_l:
            st.subheader("Top 10 sản phẩm bán từ gợi ý")
            df_top = Q.top_san_pham_goi_y(tu_ngay, den_ngay, store_keys, top_n=10)
            if df_top.empty:
                st.info("Chưa có SP bán ra từ gợi ý.")
            else:
                fig_top = px.bar(
                    df_top.sort_values("doanh_thu"),
                    x="doanh_thu",
                    y="san_pham",
                    orientation="h",
                    labels={"doanh_thu": "Doanh thu từ gợi ý (₫)", "san_pham": ""},
                    color="doanh_thu",
                    color_continuous_scale="Oranges",
                    text="so_luong",
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
            df_store_gy = Q.goi_y_theo_store(tu_ngay, den_ngay, store_keys)
            if df_store_gy.empty:
                st.info("Chưa có dữ liệu theo cửa hàng.")
            else:
                df_store_gy = df_store_gy.sort_values("ty_le_goi_y")
                fig_store = px.bar(
                    df_store_gy,
                    x="ty_le_goi_y",
                    y="cua_hang",
                    orientation="h",
                    labels={"ty_le_goi_y": "% DT từ gợi ý", "cua_hang": ""},
                    color="ty_le_goi_y",
                    color_continuous_scale="Oranges",
                    text="ty_le_goi_y",
                    hover_data={
                        "dt_goi_y": ":,.0f",
                        "dt_tong": ":,.0f",
                        "ty_le_goi_y": False,
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
        df_gio = Q.goi_y_theo_gio(tu_ngay, den_ngay, store_keys)
        if not df_gio.empty:
            df_gio["ty_le"] = (
                df_gio["dt_goi_y"] / df_gio["dt_tong"].replace(0, pd.NA)
            ).fillna(0)

            fig_gio = go.Figure()
            fig_gio.add_trace(
                go.Bar(
                    x=df_gio["gio"],
                    y=df_gio["dt_goi_y"],
                    name="Gợi ý",
                    marker_color=SUGG_COLOR,
                    hovertemplate="%{x}h<br>Gợi ý: %{y:,.0f} ₫<extra></extra>",
                )
            )
            fig_gio.add_trace(
                go.Bar(
                    x=df_gio["gio"],
                    y=df_gio["dt_tu_chon"],
                    name="Khách tự chọn",
                    marker_color=SELF_COLOR,
                    hovertemplate="%{x}h<br>Tự chọn: %{y:,.0f} ₫<extra></extra>",
                )
            )
            fig_gio.add_trace(
                go.Scatter(
                    x=df_gio["gio"],
                    y=df_gio["ty_le"],
                    name="% gợi ý",
                    mode="lines+markers",
                    yaxis="y2",
                    line=dict(color="#0ea5e9", width=2),
                    marker=dict(size=7),
                    hovertemplate="%{x}h<br>%{y:.1%} DT từ gợi ý<extra></extra>",
                )
            )
            fig_gio.update_layout(
                # Grouped (side-by-side) thay vì stack — cột "Gợi ý" không
                # bị "nuốt" bởi "Tự chọn" khi tỉ trọng nhỏ.
                barmode="group",
                bargap=0.25,
                bargroupgap=0.05,
                height=360,
                margin=dict(l=10, r=10, t=10, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                xaxis=dict(
                    title="Giờ trong ngày",
                    tickmode="linear",
                    tick0=0,
                    dtick=1,
                    tickformat="d",
                ),
                yaxis=dict(title="Doanh thu (₫)"),
                yaxis2=dict(
                    title="% gợi ý",
                    overlaying="y",
                    side="right",
                    tickformat=".0%",
                    range=[0, max(0.5, df_gio["ty_le"].max() * 1.2 or 0.5)],
                    showgrid=False,
                ),
                hovermode="x unified",
            )
            st.plotly_chart(fig_gio, use_container_width=True)

        # ---- Row 5: Auto insight callout ---------------------------------
        insights: list[str] = []
        ty_le_dt = (k.get("ty_le_dt_goi_y") or 0) * 100
        ty_le_don = (k.get("ty_le_don_goi_y") or 0) * 100
        insights.append(
            f"**{ty_le_dt:.1f}%** doanh thu trong kỳ đến từ gợi ý "
            f"(≈ {fmt_vnd(k['dt_goi_y'])} / {fmt_vnd(k['tong_dt'])})."
        )
        insights.append(
            f"**{ty_le_don:.1f}%** số đơn có ít nhất một SP được gợi ý "
            f"({fmt_int(k['don_co_goi_y'])} / {fmt_int(k['tong_don'])} đơn)."
        )
        if uplift != 0:
            chieu = "cao hơn" if uplift > 0 else "thấp hơn"
            insights.append(
                f"Giá trị trung bình một dòng bán ra từ gợi ý {chieu} dòng khách tự chọn "
                f"**{abs(uplift)*100:.1f}%** ({fmt_vnd(k['tb_dong_goi_y'])} vs {fmt_vnd(k['tb_dong_tu_chon'])})."
            )
        if not df_store_gy.empty:
            top_store = df_store_gy.iloc[-1]
            insights.append(
                f"Cửa hàng **{top_store['cua_hang']}** tận dụng gợi ý tốt nhất — "
                f"{top_store['ty_le_goi_y']*100:.1f}% doanh thu đến từ gợi ý."
            )
        if not df_gio.empty and df_gio["dt_goi_y"].sum() > 0:
            best = df_gio.loc[df_gio["dt_goi_y"].idxmax()]
            insights.append(
                f"Khung giờ gợi ý bán chạy nhất: **{int(best['gio']):02d}h** "
                f"({fmt_vnd(best['dt_goi_y'])})."
            )

        with st.container():
            st.markdown("#### Insight")
            for line in insights:
                st.markdown(f"- {line}")
