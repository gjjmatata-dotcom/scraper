"""
dashboard.py  ─  起動: streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scraper import fetch_all, STOCKS, _safe_int_fmt
import socket

# ────────────────────────────────────────
# ページ設定
# ────────────────────────────────────────
st.set_page_config(page_title="株式貸借・株価分析", page_icon="📊", layout="wide")

st.markdown("""
<style>
[data-testid="stAppViewContainer"],[data-testid="stHeader"],
section[data-testid="stMain"] { background-color:#0d1117 !important; }
[data-testid="stSidebar"]     { background-color:#161b22 !important; }
html,body,[class*="css"]      { color:#c9d1d9 !important; }
[data-testid="stTabs"] button {
    color:#8b949e !important; background:#161b22 !important;
    border-radius:6px 6px 0 0 !important; font-weight:500; }
[data-testid="stTabs"] button[aria-selected="true"] {
    color:#f0f6fc !important; background:#21262d !important;
    border-bottom:2px solid #388bfd !important; }
[data-testid="stDataFrame"] thead th {
    background-color:#161b22 !important; color:#8b949e !important;
    font-size:11px !important; border-bottom:1px solid #30363d !important; }
[data-testid="stDataFrame"] tbody td {
    color:#c9d1d9 !important; font-size:12px !important;
    border-bottom:1px solid #21262d !important; }
[data-testid="stDataFrame"] tbody tr:hover td { background-color:#1c2128 !important; }
[data-testid="stButton"] button {
    background:linear-gradient(135deg,#1f6feb,#388bfd) !important;
    color:#fff !important; border:none !important;
    border-radius:6px !important; font-weight:600; }
hr { border-color:#30363d !important; }
</style>
""", unsafe_allow_html=True)

# ────────────────────────────────────────
# 定数
# ────────────────────────────────────────
COLORS    = {"9432":"#388bfd","9434":"#f78166","6758":"#3fb950","9984":"#bc8cff"}
PR_COLORS = {
    "🔴 売り圧力優勢":"#f85149","🟢 買い圧力優勢":"#3fb950",
    "🟠 高値売り圧力":"#d29922","🔵 安値買い戻し":"#388bfd",
    "⚪ 中立":"#8b949e","データ不足":"#8b949e",
}
POS  = "#58a6ff"   # プラス → 青
NEG  = "#f85149"   # マイナス → 赤
NEUT = "#c9d1d9"   # 中立

# ────────────────────────────────────────
# サイドバー
# ────────────────────────────────────────
try:    host_ip = socket.gethostbyname(socket.gethostname())
except: host_ip = "取得失敗"
st.sidebar.markdown("### 📱 iPhoneアクセス")
st.sidebar.code(f"http://{host_ip}:8501")
st.sidebar.caption("同じWi-Fi接続が必要\n`--server.address 0.0.0.0` で起動")
st.sidebar.divider()
st.sidebar.markdown("[🌐 Streamlit Cloud（外出先対応・無料）](https://share.streamlit.io/)")

# ────────────────────────────────────────
# データ取得
# ────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def load_data(): return fetch_all()

c1, c2 = st.columns([1, 5])
with c1:
    if st.button("🔄 最新化", type="primary"):
        st.cache_data.clear(); st.rerun()
with c2:
    st.caption("キャッシュ10分 ｜ ボタンで即時更新")

with st.spinner("データ取得中… 初回は約30秒"):
    data = load_data()
st.success("✅ 取得完了")

st.markdown("<h2 style='text-align:center;color:#f0f6fc;margin:8px 0 2px;font-size:22px'>"
            "📊 株式貸借・株価分析ダッシュボード</h2>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center;color:#8b949e;font-size:12px;margin:0 0 16px'>"
            "貸借：日証金 ／ 信用残：株探 ／ 株価：yfinance ／ 過去1ヶ月</p>",
            unsafe_allow_html=True)


# ════════════════════════════════════════
# ユーティリティ
# ════════════════════════════════════════
def _vcolor(v):
    """数値の正負で色を返す"""
    if v is None: return NEUT
    try:
        f = float(v)
        if f != f: return NEUT
        return POS if f >= 0 else NEG
    except: return NEUT

def _fmt_num(v, decimals=0, unit=""):
    """カンマ区切り数値文字列"""
    if v is None: return "-"
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"): return "-"
        if decimals == 0:
            return f"{int(round(f)):,}{unit}"
        return f"{f:,.{decimals}f}{unit}"
    except: return "-"


# ════════════════════════════════════════
# セル単位スタイル（3倍乖離・差引マイナス）
# ════════════════════════════════════════
def cell_style(df_disp: pd.DataFrame, raw: pd.DataFrame,
               date_col: str, num_cols: list,
               neg_red_cols: list = None, threshold: float = 3.0) -> pd.DataFrame:
    """
    ・avg行 → 紺背景・黄文字
    ・neg_red_cols のセル値 < 0 → 赤背景（セルのみ）
    ・|値| >= 列平均 × threshold → 橙背景（セルのみ）
    """
    styled = pd.DataFrame("", index=df_disp.index, columns=df_disp.columns)
    avg_mask  = df_disp[date_col] == "【平均】"
    data_mask = ~avg_mask
    neg_red_cols = neg_red_cols or []

    # 平均行
    styled.loc[avg_mask] = "background-color:#1c2951;font-weight:700;color:#e3b341"

    for col in num_cols:
        if col not in raw.columns: continue
        s = raw[col].replace([float("inf"), float("-inf")], float("nan")).dropna()
        if s.empty: continue
        col_avg = s.abs().mean()

        for idx in df_disp[data_mask].index:
            dv = df_disp.loc[idx, date_col]
            orig = raw.loc[raw[date_col] == dv, col]
            if orig.empty or pd.isna(orig.values[0]): continue
            v = float(orig.values[0])

            if col in neg_red_cols and v < 0:
                styled.loc[idx, col] = "background-color:#3d1a1a;color:#f85149;font-weight:700"
            elif col_avg > 0 and abs(v) >= col_avg * threshold:
                styled.loc[idx, col] = "background-color:#2d1f00;color:#e3b341;font-weight:700"

    return styled


# ════════════════════════════════════════
# Plotly 共通レイアウト
# ════════════════════════════════════════
def base_layout(height=380):
    return dict(
        paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        font=dict(color="#c9d1d9", size=11),
        legend=dict(bgcolor="#161b22", bordercolor="#30363d", borderwidth=1),
        margin=dict(l=0, r=0, t=30, b=0), height=height,
    )

def update_axes(fig):
    fig.update_xaxes(gridcolor="#21262d", linecolor="#30363d",
                     tickfont=dict(color="#8b949e"))
    fig.update_yaxes(gridcolor="#21262d", linecolor="#30363d",
                     tickfont=dict(color="#8b949e"), tickformat=",")


# ════════════════════════════════════════
# ① 貸借テーブル整形（直近が上＝降順）
# ════════════════════════════════════════
LEND_NUM = ["融資新規","融資返済","融資残高","貸株新規","貸株返済","貸株残高","差引残高"]
LEND_COLS = LEND_NUM + ["貸借倍率","貸株÷出来高","踏み上げ判定"]

def build_lending(lending: pd.DataFrame, price: pd.DataFrame):
    """(disp_df, raw_df_降順) を返す"""
    # 直近が上（降順）
    raw = lending.sort_values("申込日", ascending=False).reset_index(drop=True)

    disp = pd.DataFrame()
    disp["申込日"] = raw["申込日"]

    for c in LEND_NUM:
        if c not in raw.columns:
            disp[c] = "-"; continue
        disp[c] = raw[c].apply(lambda v: _fmt_num(v))

    # 貸借倍率
    disp["貸借倍率"] = raw["貸借倍率"].apply(
        lambda v: "∞" if v == float("inf") else "-" if v != v else f"{v:.2f}倍"
    ) if "貸借倍率" in raw.columns else "-"

    # 貸株残高 ÷ 当日出来高（当日：申込日==出来高日付で照合）
    price_map = {} if price.empty else dict(zip(price["日付"], price["出来高"]))
    kd_list, fumage = [], []
    for _, row in raw.iterrows():
        kashi = row.get("貸株残高")
        vol   = price_map.get(row["申込日"])
        if (kashi is not None and not pd.isna(kashi)
                and vol is not None and not pd.isna(vol) and vol > 0):
            r = kashi / vol
            kd_list.append(f"{r:.4f}")
            sashi = row.get("差引残高", 0) or 0
            if r > 0.3 and sashi < 0:   fumage.append("🔴 踏み上げリスク")
            elif r > 0.15:               fumage.append("🟡 上値重い")
            else:                        fumage.append("✅ 中立")
        else:
            kd_list.append("-"); fumage.append("-")

    disp["貸株÷出来高"] = kd_list
    disp["踏み上げ判定"] = fumage

    # 平均行
    avg = {c: "" for c in LEND_COLS}
    avg["申込日"] = "【平均】"
    for c in LEND_NUM:
        if c in raw.columns:
            avg[c] = _fmt_num(raw[c].mean(skipna=True))
    if "貸借倍率" in raw.columns:
        v_r = raw["貸借倍率"].replace([float("inf"), float("-inf")], float("nan"))
        m = v_r.mean(skipna=True)
        avg["貸借倍率"] = f"{m:.2f}倍" if pd.notna(m) else "-"

    disp_f = pd.concat([disp[LEND_COLS], pd.DataFrame([avg])], ignore_index=True)
    return disp_f, raw


# ════════════════════════════════════════
# 貸借テーブルのスタイル（正負カラー付き）
# ════════════════════════════════════════
def style_lending_df(df_disp: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    base = cell_style(df_disp, raw, "申込日", LEND_NUM,
                      neg_red_cols=["差引残高"], threshold=3.0)
    # 差引残高・融資/貸株の数値列に正負カラー追加
    signed_cols = {"差引残高": None, "融資新規": None, "貸株新規": None}
    for idx in df_disp[df_disp["申込日"] != "【平均】"].index:
        for col in signed_cols:
            if col not in raw.columns: continue
            dv = df_disp.loc[idx, "申込日"]
            orig = raw.loc[raw["申込日"] == dv, col]
            if orig.empty or pd.isna(orig.values[0]): continue
            v = float(orig.values[0])
            # 既に背景色が付いていれば上書きしない
            if base.loc[idx, col] != "":  continue
            c = POS if v >= 0 else NEG
            base.loc[idx, col] = f"color:{c}"
    return base


# ════════════════════════════════════════
# ② 信用残テーブル整形（直近が上）
# ════════════════════════════════════════
MARGIN_NUM  = ["売り残","買い残","信用倍率","買い残増減率","売り残増減率"]
MARGIN_COLS = ["日付","終値","前週比率","売買高","売り残","買い残",
               "信用倍率","買い残増減率","売り残増減率"]

def build_margin(margin: pd.DataFrame):
    if margin.empty: return pd.DataFrame(), pd.DataFrame()
    raw = margin.sort_values("日付", ascending=False).reset_index(drop=True)

    disp = pd.DataFrame()
    disp["日付"]    = raw["日付"]
    disp["終値"]    = raw["終値"].apply(lambda v: f"¥{v:,.1f}" if pd.notna(v) else "-")
    disp["前週比率"] = raw["前週比率"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")
    disp["売買高"]  = raw["売買高"].apply(lambda v: _fmt_num(v))
    disp["売り残"]  = raw["売り残"].apply(lambda v: _fmt_num(v))
    disp["買い残"]  = raw["買い残"].apply(lambda v: _fmt_num(v))
    disp["信用倍率"] = raw["信用倍率"].apply(lambda v: f"{v:.2f}倍" if pd.notna(v) else "-")
    disp["買い残増減率"] = raw["買い残増減率"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")
    disp["売り残増減率"] = raw["売り残増減率"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")

    avg = {c: "" for c in MARGIN_COLS}
    avg["日付"] = "【平均】"
    for c in ["売り残","買い残","売買高"]:
        if c in raw.columns: avg[c] = _fmt_num(raw[c].mean(skipna=True))
    for c in ["前週比率","買い残増減率","売り残増減率"]:
        if c in raw.columns:
            m = raw[c].mean(skipna=True)
            avg[c] = f"{m:+.2f}%" if pd.notna(m) else "-"
    if "信用倍率" in raw.columns:
        m = raw["信用倍率"].mean(skipna=True)
        avg["信用倍率"] = f"{m:.2f}倍" if pd.notna(m) else "-"
    if "終値" in raw.columns:
        m = raw["終値"].mean(skipna=True)
        avg["終値"] = f"¥{m:,.1f}" if pd.notna(m) else "-"

    disp_f = pd.concat([disp[MARGIN_COLS], pd.DataFrame([avg])], ignore_index=True)
    return disp_f, raw


def style_margin_df(df_disp: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    base = cell_style(df_disp, raw, "日付", MARGIN_NUM,
                      neg_red_cols=[], threshold=3.0)
    # 信用倍率 < 1 → 赤セル
    for idx in df_disp[df_disp["日付"] != "【平均】"].index:
        dv = df_disp.loc[idx, "日付"]
        orig = raw.loc[raw["日付"] == dv, "信用倍率"]
        if not orig.empty and pd.notna(orig.values[0]) and orig.values[0] < 1:
            base.loc[idx, "信用倍率"] = "background-color:#3d1a1a;color:#f85149;font-weight:700"
        # 増減率の正負カラー
        for col in ["買い残増減率","売り残増減率","前週比率"]:
            if base.loc[idx, col] != "": continue
            orig2 = raw.loc[raw["日付"] == dv, col]
            if orig2.empty or pd.isna(orig2.values[0]): continue
            v = float(orig2.values[0])
            base.loc[idx, col] = f"color:{POS if v >= 0 else NEG}"
    return base


# ════════════════════════════════════════
# ③ 株価テーブル整形（直近が上）
# ════════════════════════════════════════
def build_price_table(price: pd.DataFrame) -> pd.DataFrame:
    if price.empty: return pd.DataFrame()
    # price は scraper で降順に返される
    pt   = price.copy()
    raws = {c: pt[c].copy() for c in ["始値","高値","安値","終値","出来高","前日比%"]}
    vol_mean = pt["出来高平均"].iloc[0]

    pt["株価判定"]  = pt["機関異常"].map({True:"🔴 機関", False:"✅ 通常"})
    pt["出来高判定"] = pt["出来高異常"].map({True:"🟠 急増", False:"✅ 通常"})
    for c in ["終値","始値","高値","安値"]:
        pt[c] = raws[c].apply(lambda v: f"¥{v:,.1f}" if pd.notna(v) else "-")
    pt["出来高"] = raws["出来高"].apply(lambda v: f"{int(v):,}" if pd.notna(v) else "-")
    pt["前日比%"] = raws["前日比%"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")

    show = ["日付","始値","高値","安値","終値","前日比%","株価判定","出来高","出来高判定"]
    avg_row = {
        "日付": "【平均】",
        "始値": f"¥{raws['始値'].mean():,.1f}",
        "高値": f"¥{raws['高値'].mean():,.1f}",
        "安値": f"¥{raws['安値'].mean():,.1f}",
        "終値": f"¥{raws['終値'].mean():,.1f}",
        "前日比%": f"{raws['前日比%'].mean(skipna=True):+.2f}%",
        "株価判定": "",
        "出来高":   f"{int(raws['出来高'].mean()):,}",
        "出来高判定": f"月平均 {vol_mean/1e6:.1f}M",
    }
    return pd.concat([pt[show], pd.DataFrame([avg_row])], ignore_index=True)


def style_price_table(row, price_raw: pd.DataFrame):
    if row["日付"] == "【平均】":
        return ["background-color:#1c2951;font-weight:700;color:#e3b341"] * len(row)
    if row.get("株価判定") == "🔴 機関":
        return ["background-color:#2d1014;color:#ffa198"] * len(row)
    if row.get("出来高判定") == "🟠 急増":
        return ["background-color:#2d1f00;color:#e3b341"] * len(row)
    # 前日比%の正負カラー
    styles = [""] * len(row)
    cols_list = row.index.tolist()
    if "前日比%" in cols_list:
        i = cols_list.index("前日比%")
        orig = price_raw.loc[price_raw["日付"] == row["日付"], "前日比%"]
        if not orig.empty and pd.notna(orig.values[0]):
            styles[i] = f"color:{POS if orig.values[0] >= 0 else NEG};font-weight:600"
    return styles


# ════════════════════════════════════════
# 銘柄タブ
# ════════════════════════════════════════
tabs = st.tabs([f"{code}  {STOCKS[code]['name']}" for code in STOCKS])

for tab, (code, info) in zip(tabs, data.items()):
    lending  = info["lending"]
    price    = info["price"]
    margin   = info["margin"]
    pressure = info["pressure"]
    color    = COLORS.get(code, "#388bfd")
    pr_color = PR_COLORS.get(pressure["label"], "#8b949e")

    with tab:

        # ── 圧力バナー ─────────────────────────────
        st.markdown(f"""
        <div style="background:#161b22;border-left:4px solid {pr_color};
            border-radius:6px;padding:10px 16px;margin-bottom:16px">
          <span style="font-size:1.1rem;font-weight:700;color:{pr_color}">{pressure['label']}</span>
          <span style="color:#8b949e;font-size:0.85rem;margin-left:12px">{pressure['detail']}</span>
        </div>""", unsafe_allow_html=True)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ① 貸借取引残高
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        st.markdown("### ① 貸借取引残高（日証金・過去1ヶ月・直近順）")
        st.caption("🔴赤セル＝差引残高マイナス ／ 🟡橙セル＝平均値の3倍超 ／ 青字=プラス・赤字=マイナス")

        if lending.empty:
            st.warning("貸借データを取得できませんでした。")
        else:
            disp_l, raw_l = build_lending(lending, price)

            # ── 貸借グラフ（融資残高・貸株残高＋貸株÷出来高）──
            lend_asc = lending.sort_values("申込日", ascending=True)
            price_map = {} if price.empty else dict(zip(price["日付"], price["出来高"]))
            kd_vals = []
            for _, row in lend_asc.iterrows():
                kashi = row.get("貸株残高")
                vol   = price_map.get(row["申込日"])
                if (kashi is not None and not pd.isna(kashi)
                        and vol is not None and not pd.isna(vol) and vol > 0):
                    kd_vals.append(kashi / vol)
                else:
                    kd_vals.append(None)

            fig_l = make_subplots(
                rows=3, cols=1, shared_xaxes=True,
                row_heights=[0.45, 0.3, 0.25], vertical_spacing=0.05,
                subplot_titles=["融資残高・貸株残高", "貸借倍率", "貸株÷出来高（踏み上げ指標）"],
            )
            # 融資残高
            fig_l.add_trace(go.Scatter(
                x=lend_asc["申込日"], y=lend_asc["融資残高"],
                name="融資残高", line=dict(color="#388bfd", width=2),
                fill="tozeroy", fillcolor="rgba(56,139,253,0.12)",
                hovertemplate="%{x}<br>融資残高:%{y:,.0f}<extra></extra>"
            ), row=1, col=1)
            # 貸株残高
            fig_l.add_trace(go.Scatter(
                x=lend_asc["申込日"], y=lend_asc["貸株残高"],
                name="貸株残高", line=dict(color="#f85149", width=2),
                fill="tozeroy", fillcolor="rgba(248,81,73,0.12)",
                hovertemplate="%{x}<br>貸株残高:%{y:,.0f}<extra></extra>"
            ), row=1, col=1)
            # 貸借倍率バー
            valid_r = lend_asc["貸借倍率"].replace([float("inf"), float("-inf")], float("nan"))
            fig_l.add_trace(go.Bar(
                x=lend_asc["申込日"], y=valid_r, name="貸借倍率",
                marker_color=["#f85149" if (pd.notna(v) and v < 1) else color for v in valid_r],
                opacity=0.8,
                hovertemplate="%{x}<br>貸借倍率:%{y:.2f}倍<extra></extra>"
            ), row=2, col=1)
            fig_l.add_hline(y=1, line_dash="dash", line_color="#f85149", line_width=1,
                            annotation_text="1倍", annotation_font_color="#f85149", row=2, col=1)
            # 貸株÷出来高ライン
            fig_l.add_trace(go.Scatter(
                x=lend_asc["申込日"], y=kd_vals, name="貸株÷出来高",
                mode="lines+markers",
                line=dict(color="#e3b341", width=2),
                marker=dict(size=5, color="#e3b341"),
                hovertemplate="%{x}<br>貸株÷出来高:%{y:.4f}<extra></extra>"
            ), row=3, col=1)
            fig_l.add_hline(y=0.3, line_dash="dash", line_color="#f85149", line_width=1,
                            annotation_text="0.30（踏み上げリスク）",
                            annotation_font_color="#f85149", row=3, col=1)
            fig_l.add_hline(y=0.15, line_dash="dot", line_color="#e3b341", line_width=1,
                            annotation_text="0.15（上値重い）",
                            annotation_font_color="#e3b341", row=3, col=1)

            fig_l.update_layout(**base_layout(440))
            update_axes(fig_l)
            st.plotly_chart(fig_l, use_container_width=True)

            # テーブル（降順）
            styled_l = style_lending_df(disp_l, raw_l)
            st.dataframe(
                disp_l.style.apply(lambda _: styled_l, axis=None),
                use_container_width=True, hide_index=True,
                height=min(38 * (len(disp_l) + 1) + 38, 620),
            )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ② 週次信用残（直近が上）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        st.markdown("### ② 週次信用残時系列（株探・約29週・直近順）")
        st.caption("🔴赤セル＝信用倍率1倍未満 ／ 🟡橙セル＝平均値の3倍超 ／ 青字=プラス・赤字=マイナス")

        if margin.empty:
            st.warning("信用残データを取得できませんでした。")
        else:
            m_asc = margin.sort_values("日付", ascending=True)
            fig_m = make_subplots(
                rows=2, cols=1, shared_xaxes=True,
                row_heights=[0.6, 0.4], vertical_spacing=0.06,
                subplot_titles=["買い残・売り残（株）", "信用倍率"],
            )
            fig_m.add_trace(go.Scatter(
                x=m_asc["日付"], y=m_asc["買い残"], name="買い残",
                line=dict(color="#388bfd", width=2),
                fill="tozeroy", fillcolor="rgba(56,139,253,0.15)",
                hovertemplate="%{x}<br>買い残:%{y:,.0f}<extra></extra>"
            ), row=1, col=1)
            fig_m.add_trace(go.Scatter(
                x=m_asc["日付"], y=m_asc["売り残"], name="売り残",
                line=dict(color="#f85149", width=2),
                fill="tozeroy", fillcolor="rgba(248,81,73,0.15)",
                hovertemplate="%{x}<br>売り残:%{y:,.0f}<extra></extra>"
            ), row=1, col=1)
            bar_cm = ["#f85149" if (pd.notna(v) and v < 1) else color for v in m_asc["信用倍率"]]
            fig_m.add_trace(go.Bar(
                x=m_asc["日付"], y=m_asc["信用倍率"], name="信用倍率",
                marker_color=bar_cm, opacity=0.85,
                hovertemplate="%{x}<br>信用倍率:%{y:.2f}倍<extra></extra>"
            ), row=2, col=1)
            fig_m.add_hline(y=1, line_dash="dash", line_color="#f85149", line_width=1,
                            annotation_text="1.0倍", annotation_font_color="#f85149", row=2, col=1)
            fig_m.update_layout(**base_layout(360))
            update_axes(fig_m)
            st.plotly_chart(fig_m, use_container_width=True)

            # テーブル（降順）
            disp_m, raw_m = build_margin(margin)
            styled_m = style_margin_df(disp_m, raw_m)
            st.dataframe(
                disp_m.style.apply(lambda _: styled_m, axis=None),
                use_container_width=True, hide_index=True,
                height=min(38 * (len(disp_m) + 1) + 38, 620),
            )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ③ 株価急変 ＋ 出来高（直近が上）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        st.markdown("### ③ 株価急変 ＋ 出来高（過去1ヶ月・直近順）")
        st.caption("🔴★＝大口機関投資家の可能性 ／ 🟠出来高月平均×2超 ／ 青字=前日比プラス・赤字=マイナス")

        if price.empty:
            st.warning("株価データを取得できませんでした。")
        else:
            vol_mean = price["出来高平均"].iloc[0]
            price_asc = price.sort_values("日付", ascending=True)

            fig_p = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                  row_heights=[0.62, 0.38], vertical_spacing=0.04)
            m_c   = ["#f85149" if a else color for a in price_asc["機関異常"]]
            m_sz  = [11 if a else 5 for a in price_asc["機関異常"]]
            m_sym = ["star" if a else "circle" for a in price_asc["機関異常"]]

            fig_p.add_trace(go.Scatter(
                x=price_asc["日付"], y=price_asc["終値"],
                mode="lines+markers", name="終値",
                line=dict(color=color, width=2),
                marker=dict(size=m_sz, color=m_c, symbol=m_sym,
                            line=dict(width=1.5, color="rgba(248,81,73,0.4)")),
                hovertemplate="日付:%{x}<br>終値:¥%{y:,.1f}<extra></extra>",
            ), row=1, col=1)

            for _, r in price_asc[price_asc["機関異常"]].iterrows():
                chg = r.get("前日比%", float("nan"))
                lbl = f"<b>{chg:+.1f}%</b>" if pd.notna(chg) else "<b>⚠</b>"
                fig_p.add_annotation(
                    x=r["日付"], y=r["終値"], text=lbl,
                    showarrow=True, arrowhead=2, arrowcolor="#f85149",
                    font=dict(color="#f85149", size=10),
                    bgcolor="#0d1117", bordercolor="#f85149")

            bar_c2 = [
                "#f85149" if r["機関異常"] else
                "#e3b341" if r["出来高異常"] else color
                for _, r in price_asc.iterrows()
            ]
            fig_p.add_trace(go.Bar(
                x=price_asc["日付"], y=price_asc["出来高"],
                name="出来高", marker_color=bar_c2, opacity=0.85,
                hovertemplate="日付:%{x}<br>出来高:%{y:,.0f}<extra></extra>",
            ), row=2, col=1)
            fig_p.add_hline(y=vol_mean, line_dash="dot", line_color="#8b949e",
                            annotation_text=f"月平均 {vol_mean/1e6:.1f}M",
                            annotation_font_color="#8b949e", row=2, col=1)
            fig_p.add_hline(y=vol_mean * 2, line_dash="dash", line_color="#e3b341",
                            annotation_text="×2（異常ライン）",
                            annotation_font_color="#e3b341", row=2, col=1)
            fig_p.update_layout(**base_layout(450))
            update_axes(fig_p)
            st.plotly_chart(fig_p, use_container_width=True)

            # テーブル（降順）
            pt_show = build_price_table(price)
            st.dataframe(
                pt_show.style.apply(lambda row: style_price_table(row, price), axis=1),
                use_container_width=True, hide_index=True,
            )


# ════════════════════════════════════════
# ④ 全銘柄サマリー
# ════════════════════════════════════════
st.divider()
st.markdown("### ④ 全銘柄サマリー")

cols4 = st.columns(4)
for col, (code, info) in zip(cols4, data.items()):
    lending  = info["lending"]
    price    = info["price"]
    margin   = info["margin"]
    pressure = info["pressure"]
    color    = COLORS.get(code, "#388bfd")
    pr_color = PR_COLORS.get(pressure["label"], "#8b949e")

    pct_str, pct_color = "-", "#8b949e"
    if not price.empty and len(price) >= 2:
        chg = (price["終値"].iloc[0] - price["終値"].iloc[-1]) / price["終値"].iloc[-1] * 100
        pct_str   = f"{chg:+.2f}%"
        pct_color = POS if chg >= 0 else NEG

    inst = f"{price['機関異常'].sum()}日/{len(price)}日" if not price.empty else "-"

    lr = ym = km = yn = kn = "-"
    if not lending.empty:
        r = lending["貸借倍率"].iloc[-1]
        lr = "∞" if r == float("inf") else "-" if r != r else f"{r:.2f}倍"
        ym = _fmt_num(lending["融資残高"].mean(skipna=True))
        km = _fmt_num(lending["貸株残高"].mean(skipna=True))
        yn = _fmt_num(lending["融資新規"].mean(skipna=True))
        kn = _fmt_num(lending["貸株新規"].mean(skipna=True))

    mr = "-"
    if not margin.empty and "信用倍率" in margin.columns:
        lm = margin["信用倍率"].dropna()
        if not lm.empty:
            v = lm.iloc[-1]
            mr = f"{v:.2f}倍" + (" 🔴" if v < 1 else "")

    with col:
        st.markdown(f"""
        <div style="background:#161b22;border-top:3px solid {color};border-radius:8px;
            padding:14px;border:1px solid #30363d;margin-bottom:8px">
          <div style="color:{color};font-weight:700;font-size:14px;margin-bottom:4px">
              {code} {STOCKS[code]['name']}</div>
          <div style="color:{pr_color};font-weight:600;font-size:12px;margin-bottom:10px">
              {pressure['label']}</div>
          <table style="width:100%;font-size:12px;border-collapse:collapse">
            <tr><td style="color:#8b949e;padding:3px 0">月間株価</td>
                <td style="color:{pct_color};font-weight:700;text-align:right">{pct_str}</td></tr>
            <tr><td style="color:#8b949e;padding:3px 0">機関異常</td>
                <td style="color:#f85149;font-weight:600;text-align:right">{inst}</td></tr>
            <tr><td style="color:#8b949e;padding:3px 0">最新貸借倍率</td>
                <td style="color:#c9d1d9;text-align:right">{lr}</td></tr>
            <tr><td style="color:#8b949e;padding:3px 0">最新信用倍率</td>
                <td style="color:#c9d1d9;text-align:right">{mr}</td></tr>
            <tr><td style="color:#8b949e;padding:3px 0">融資残高 平均</td>
                <td style="color:{POS};text-align:right">{ym}</td></tr>
            <tr><td style="color:#8b949e;padding:3px 0">融資新規 平均</td>
                <td style="color:{POS};text-align:right">{yn}</td></tr>
            <tr><td style="color:#8b949e;padding:3px 0">貸株残高 平均</td>
                <td style="color:{NEG};text-align:right">{km}</td></tr>
            <tr><td style="color:#8b949e;padding:3px 0">貸株新規 平均</td>
                <td style="color:{NEG};text-align:right">{kn}</td></tr>
          </table>
        </div>""", unsafe_allow_html=True)

st.markdown(
    "<p style='text-align:center;font-size:11px;color:#484f58;margin-top:12px'>"
    "出典：日証金(taisyaku.jp) ／ 株探(kabutan.jp) ／ Yahoo Finance ｜ 本ツールは投資勧誘を目的としません</p>",
    unsafe_allow_html=True)
