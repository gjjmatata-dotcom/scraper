"""
dashboard.py  ─  streamlit run dashboard.py
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scraper import fetch_all, STOCKS, _safe_int_fmt
import socket

st.set_page_config(page_title="株式貸借分析", page_icon="📊", layout="wide")
st.markdown("""
<style>
[data-testid="stAppViewContainer"],[data-testid="stHeader"],
section[data-testid="stMain"]{background:#0d1117!important}
[data-testid="stSidebar"]{background:#161b22!important}
html,body,[class*="css"]{color:#c9d1d9!important;-webkit-text-size-adjust:100%}
[data-testid="stTabs"] button{color:#8b949e!important;background:#161b22!important;
  border-radius:6px 6px 0 0!important;padding:8px 12px!important;font-weight:600}
[data-testid="stTabs"] button[aria-selected="true"]{color:#f0f6fc!important;
  background:#21262d!important;border-bottom:2px solid #388bfd!important}
[data-testid="stDataFrame"] thead th{background:#161b22!important;color:#8b949e!important;
  font-size:11px!important;border-bottom:1px solid #30363d!important;white-space:nowrap}
[data-testid="stDataFrame"] tbody td{color:#c9d1d9!important;font-size:12px!important;
  border-bottom:1px solid #21262d!important;white-space:nowrap}
[data-testid="stButton"] button{background:linear-gradient(135deg,#1f6feb,#388bfd)!important;
  color:#fff!important;border:none!important;border-radius:8px!important;
  font-weight:600;min-height:44px;font-size:15px!important}
hr{border-color:#30363d!important}
</style>""", unsafe_allow_html=True)

COLORS    = {"9432":"#388bfd","9434":"#f78166","6758":"#3fb950","9984":"#bc8cff"}
PR_COLORS = {"🔴 売り圧力優勢":"#f85149","🟢 買い圧力優勢":"#3fb950",
             "🟠 高値売り圧力":"#d29922","🔵 安値買い戻し":"#388bfd",
             "⚪ 中立":"#8b949e","データ不足":"#8b949e"}
POS, NEG = "#58a6ff", "#f85149"

def vc(v):
    try: return POS if float(v) >= 0 else NEG
    except: return "#c9d1d9"

def fmt(v, dec=0, suffix=""):
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"): return "-"
        s = f"{int(round(f)):,}" if dec == 0 else f"{f:,.{dec}f}"
        return s + suffix
    except: return "-"

# ── サイドバー ────────────────────────────────────────
try: ip = socket.gethostbyname(socket.gethostname())
except: ip = "取得失敗"
st.sidebar.markdown("### 📱 LAN内アクセス")
st.sidebar.code(f"http://{ip}:8501")
st.sidebar.caption("`--server.address 0.0.0.0` で起動")
st.sidebar.markdown("[🌐 Streamlit Cloud](https://share.streamlit.io/)")

# ── データ取得 ────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def load_data(): return fetch_all()

c1, c2 = st.columns([1,4])
with c1:
    if st.button("🔄 最新化", type="primary"):
        st.cache_data.clear(); st.rerun()
with c2: st.caption("10分キャッシュ")

with st.spinner("取得中… 初回約30秒"): data = load_data()
st.success("✅ 取得完了")
st.markdown("<h2 style='text-align:center;color:#f0f6fc;font-size:20px;margin:4px 0'>"
            "📊 株式貸借・株価分析ダッシュボード</h2>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center;color:#8b949e;font-size:11px;margin:0 0 12px'>"
            "全データ出典：IRバンク(irbank.net) ／ nisshokin・margin・chart</p>",
            unsafe_allow_html=True)

# ── Plotly共通 ────────────────────────────────────────
def fig_base(fig, h=380):
    fig.update_layout(paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        font=dict(color="#c9d1d9", size=11),
        legend=dict(bgcolor="rgba(22,27,34,0.9)", bordercolor="#30363d", borderwidth=1,
                    orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=4, r=4, t=36, b=4), height=h)
    fig.update_xaxes(gridcolor="#21262d", linecolor="#30363d",
                     tickfont=dict(color="#8b949e", size=10), tickangle=-30)
    fig.update_yaxes(gridcolor="#21262d", linecolor="#30363d",
                     tickfont=dict(color="#8b949e", size=10), tickformat=",")

# ── セルスタイル ──────────────────────────────────────
def cell_style(disp, raw, dcol, num_cols, neg_red=None, thr=3.0):
    styled = pd.DataFrame("", index=disp.index, columns=disp.columns)
    avg_m = disp[dcol] == "【平均】"
    styled.loc[avg_m] = "background-color:#1c2951;font-weight:700;color:#e3b341"
    neg_red = neg_red or []
    for col in num_cols:
        if col not in raw.columns: continue
        s = raw[col].replace([float("inf"), float("-inf")], float("nan")).dropna()
        if s.empty: continue
        ca = s.abs().mean()
        for idx in disp[~avg_m].index:
            dv = disp.loc[idx, dcol]
            orig = raw.loc[raw[dcol] == dv, col]
            if orig.empty or pd.isna(orig.values[0]): continue
            v = float(orig.values[0])
            if col in neg_red and v < 0:
                styled.loc[idx, col] = "background-color:#3d1a1a;color:#f85149;font-weight:700"
            elif ca > 0 and abs(v) >= ca * thr:
                styled.loc[idx, col] = "background-color:#2d1f00;color:#e3b341;font-weight:700"
            elif styled.loc[idx, col] == "":
                styled.loc[idx, col] = f"color:{vc(v)}"
    return styled


# ════════════════════════════════════════
# 銘柄タブ
# ════════════════════════════════════════
tabs = st.tabs([f"{code} {STOCKS[code]['name']}" for code in STOCKS])

for tab, (code, info) in zip(tabs, data.items()):
    L = info["lending"]; P = info["price"]; M = info["margin"]
    pr = info["pressure"]
    col = COLORS.get(code, "#388bfd")
    prc = PR_COLORS.get(pr["label"], "#8b949e")

    with tab:
        # 圧力バナー
        st.markdown(f"""<div style="background:#161b22;border-left:4px solid {prc};
            border-radius:6px;padding:10px 14px;margin-bottom:14px">
          <span style="font-size:1.05rem;font-weight:700;color:{prc}">{pr['label']}</span>
          <span style="color:#8b949e;font-size:0.82rem;margin-left:10px">{pr['detail']}</span>
        </div>""", unsafe_allow_html=True)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ① 貸借取引残高
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        st.markdown("<h3 style='color:#f0f6fc;font-size:14px;margin:0 0 4px'>"
                    "① 貸借取引残高 + 逆日歩（直近順）</h3>", unsafe_allow_html=True)
        st.caption("🔴赤=差引マイナス ／ 🟡橙=平均3倍超 ／ 🟠逆日歩発生 ／ 青=プラス・赤=マイナス")

        if L.empty:
            st.warning("貸借データを取得できませんでした。")
        else:
            La = L.sort_values("_dt", ascending=True)

            # グラフ：買い残高・売り残高（左軸）＋ 株価ライン（右軸）
            fig1 = make_subplots(rows=2, cols=1, shared_xaxes=True,
                row_heights=[0.65, 0.35], vertical_spacing=0.05,
                subplot_titles=["買い残高・売り残高 ＋ 株価（右軸）",
                                "資金フロー（買い新規－売り新規）"],
                specs=[[{"secondary_y": True}], [{"secondary_y": False}]])

            # 買い残高・売り残高
            fig1.add_trace(go.Scatter(x=La["申込日"], y=La["買い残高"], name="買い残高",
                line=dict(color="#388bfd", width=2), fill="tozeroy",
                fillcolor="rgba(56,139,253,0.08)",
                hovertemplate="%{x}<br>買い残高:%{y:,.0f}<extra></extra>"),
                row=1, col=1, secondary_y=False)
            fig1.add_trace(go.Scatter(x=La["申込日"], y=La["売り残高"], name="売り残高",
                line=dict(color="#f85149", width=2), fill="tozeroy",
                fillcolor="rgba(248,81,73,0.08)",
                hovertemplate="%{x}<br>売り残高:%{y:,.0f}<extra></extra>"),
                row=1, col=1, secondary_y=False)

            # 株価ラインを右軸に重ねる
            if not P.empty:
                Pa_asc = P.sort_values("_dt", ascending=True)
                fig1.add_trace(go.Scatter(x=Pa_asc["日付"], y=Pa_asc["終値"], name="株価",
                    line=dict(color="#e3b341", width=1.5, dash="dot"),
                    hovertemplate="%{x}<br>株価:¥%{y:,.1f}<extra></extra>"),
                    row=1, col=1, secondary_y=True)
                fig1.update_yaxes(title_text="株価(円)", secondary_y=True,
                    gridcolor="#21262d", tickfont=dict(color="#e3b341", size=9),
                    tickformat=",", row=1, col=1)

            # 資金フロー
            flow = La["買い新規"].fillna(0) - La["売り新規"].fillna(0)
            fig1.add_trace(go.Bar(x=La["申込日"], y=flow, name="資金フロー",
                marker_color=["#388bfd" if v >= 0 else "#f85149" for v in flow],
                opacity=0.85,
                hovertemplate="%{x}<br>フロー:%{y:,.0f}<extra></extra>"),
                row=2, col=1)
            fig1.add_hline(y=0, line_dash="solid", line_color="#484f58",
                           line_width=1, row=2, col=1)

            fig_base(fig1, 400); st.plotly_chart(fig1, use_container_width=True)

            # テーブル（貸借倍率を含む・直近が上）
            LCOLS = ["申込日","買い残高","買い増減","買い新規","買い返済",
                     "売り残高","売り増減","売り新規","売り返済","貸借倍率","逆日歩"]
            LNUM  = ["買い残高","買い増減","買い新規","買い返済",
                     "売り残高","売り増減","売り新規","売り返済"]
            Ld = L.sort_values("_dt", ascending=False).reset_index(drop=True)
            Ld["差引残高"] = Ld["買い残高"].fillna(0) - Ld["売り残高"].fillna(0)

            dp = pd.DataFrame()
            dp["申込日"] = Ld["申込日"]
            for c in LNUM: dp[c] = Ld[c].apply(fmt) if c in Ld.columns else "-"
            dp["貸借倍率"] = Ld["貸借倍率"].apply(
                lambda v: "-" if pd.isna(v) else "∞" if abs(v)==float("inf") else f"{v:.2f}倍")
            dp["逆日歩"] = Ld["逆日歩"].apply(
                lambda v: f"{v:.2f}" if pd.notna(v) and v > 0 else "-")

            av = {c: "" for c in LCOLS}; av["申込日"] = "【平均】"
            for c in LNUM: av[c] = fmt(Ld[c].mean(skipna=True))
            av["貸借倍率"] = fmt(Ld["貸借倍率"].replace([float("inf"),float("-inf")],
                               float("nan")).mean(skipna=True), dec=2, suffix="倍")
            av["逆日歩"] = fmt(Ld["逆日歩"].mean(skipna=True), dec=2)
            disp_l = pd.concat([dp[LCOLS], pd.DataFrame([av])], ignore_index=True)
            st_l   = cell_style(disp_l, Ld, "申込日", LNUM, neg_red=["差引残高"])

            for idx in disp_l[disp_l["申込日"] != "【平均】"].index:
                dv = disp_l.loc[idx, "申込日"]
                orig = Ld.loc[Ld["申込日"] == dv, "逆日歩"]
                if not orig.empty and pd.notna(orig.values[0]) and orig.values[0] > 0:
                    st_l.loc[idx, "逆日歩"] = "background-color:#2d1f00;color:#e3b341;font-weight:700"

            st.dataframe(disp_l.style.apply(lambda _: st_l, axis=None),
                use_container_width=True, hide_index=True,
                height=min(38*(len(disp_l)+1)+38, 560))

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ② 週次信用残
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        st.markdown("<h3 style='color:#f0f6fc;font-size:14px;margin:14px 0 4px'>"
                    "② 週次信用残（直近順）</h3>", unsafe_allow_html=True)
        st.caption("🔴赤=信用倍率1倍未満 ／ 🟡橙=平均3倍超 ／ 🟠逆日歩発生 ／ 青=プラス・赤=マイナス")

        if M.empty:
            st.warning("信用残データを取得できませんでした。")
        else:
            Ma = M.sort_values("_dt", ascending=True)
            fig2 = make_subplots(rows=1, cols=1)
            fig2.add_trace(go.Scatter(x=Ma["日付"], y=Ma["買い残高"], name="買い残高",
                line=dict(color="#388bfd", width=2), fill="tozeroy",
                fillcolor="rgba(56,139,253,0.12)",
                hovertemplate="%{x}<br>買い残高:%{y:,.0f}<extra></extra>"))
            fig2.add_trace(go.Scatter(x=Ma["日付"], y=Ma["売り残高"], name="売り残高",
                line=dict(color="#f85149", width=2), fill="tozeroy",
                fillcolor="rgba(248,81,73,0.12)",
                hovertemplate="%{x}<br>売り残高:%{y:,.0f}<extra></extra>"))
            fig_base(fig2, 260); st.plotly_chart(fig2, use_container_width=True)

            MCOLS = ["日付","買い残高","買い増減","売り残高","売り増減",
                     "信用倍率","買い残増減率","売り残増減率","逆日歩"]
            MNUM  = ["買い残高","買い増減","売り残高","売り増減","信用倍率",
                     "買い残増減率","売り残増減率"]
            Md = M.sort_values("_dt", ascending=False).reset_index(drop=True)
            dm = pd.DataFrame(); dm["日付"] = Md["日付"]
            dm["買い残高"]    = Md["買い残高"].apply(fmt)
            dm["買い増減"]    = Md["買い増減"].apply(fmt)
            dm["売り残高"]    = Md["売り残高"].apply(fmt)
            dm["売り増減"]    = Md["売り増減"].apply(fmt)
            dm["信用倍率"]    = Md["信用倍率"].apply(lambda v: f"{v:.2f}倍" if pd.notna(v) else "-")
            dm["買い残増減率"] = Md["買い残増減率"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")
            dm["売り残増減率"] = Md["売り残増減率"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")
            dm["逆日歩"]      = Md["逆日歩"].apply(lambda v: f"{v:.2f}" if pd.notna(v) and v > 0 else "-")

            av_m = {c: "" for c in MCOLS}; av_m["日付"] = "【平均】"
            for c in ["買い残高","売り残高"]: av_m[c] = fmt(Md[c].mean(skipna=True))
            av_m["信用倍率"] = fmt(Md["信用倍率"].mean(skipna=True), dec=2, suffix="倍")
            for c in ["買い残増減率","売り残増減率"]:
                v2 = Md[c].mean(skipna=True)
                av_m[c] = f"{v2:+.2f}%" if pd.notna(v2) else "-"

            disp_m = pd.concat([dm[MCOLS], pd.DataFrame([av_m])], ignore_index=True)
            st_m   = cell_style(disp_m, Md, "日付", MNUM, thr=3.0)

            for idx in disp_m[disp_m["日付"] != "【平均】"].index:
                dv = disp_m.loc[idx, "日付"]
                orig = Md.loc[Md["日付"] == dv, "信用倍率"]
                if not orig.empty and pd.notna(orig.values[0]) and orig.values[0] < 1:
                    st_m.loc[idx, "信用倍率"] = "background-color:#3d1a1a;color:#f85149;font-weight:700"
                orig2 = Md.loc[Md["日付"] == dv, "逆日歩"]
                if not orig2.empty and pd.notna(orig2.values[0]) and orig2.values[0] > 0:
                    st_m.loc[idx, "逆日歩"] = "background-color:#2d1f00;color:#e3b341;font-weight:700"
                for c in ["買い残増減率","売り残増減率"]:
                    if st_m.loc[idx, c] != "": continue
                    orig3 = Md.loc[Md["日付"] == dv, c]
                    if not orig3.empty and pd.notna(orig3.values[0]):
                        st_m.loc[idx, c] = f"color:{vc(orig3.values[0])}"

            st.dataframe(disp_m.style.apply(lambda _: st_m, axis=None),
                use_container_width=True, hide_index=True,
                height=min(38*(len(disp_m)+1)+38, 520))

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ③ 株価急変 ＋ 出来高（IRバンク chart）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        st.markdown("<h3 style='color:#f0f6fc;font-size:14px;margin:14px 0 4px'>"
                    "③ 株価急変 ＋ 出来高（直近順）</h3>", unsafe_allow_html=True)
        st.caption("🔴★=大口機関の可能性 ／ 🟡=出来高月平均×2超 ／ 青=プラス・赤=マイナス ／ 25日乖離・PER・PBR付き")

        if P.empty:
            st.warning("株価データを取得できませんでした。")
        else:
            vm  = P["出来高平均"].iloc[0]
            Pa  = P.sort_values("_dt", ascending=True)

            # 株価＋出来高グラフ
            fig3 = make_subplots(rows=2, cols=1, shared_xaxes=True,
                row_heights=[0.65, 0.35], vertical_spacing=0.04)

            mc2 = ["#f85149" if a else col for a in Pa["機関異常"]]
            ms2 = [10 if a else 5 for a in Pa["機関異常"]]
            sym = ["star" if a else "circle" for a in Pa["機関異常"]]
            fig3.add_trace(go.Scatter(x=Pa["日付"], y=Pa["終値"],
                mode="lines+markers", name="終値",
                line=dict(color=col, width=2),
                marker=dict(size=ms2, color=mc2, symbol=sym,
                    line=dict(width=1.5, color="rgba(248,81,73,0.4)")),
                hovertemplate="日付:%{x}<br>終値:¥%{y:,.1f}<extra></extra>"), row=1, col=1)

            for _, r in Pa[Pa["機関異常"]].iterrows():
                chg = r.get("前日比%", float("nan"))
                fig3.add_annotation(x=r["日付"], y=r["終値"],
                    text=f"<b>{chg:+.1f}%</b>" if pd.notna(chg) else "<b>⚠</b>",
                    showarrow=True, arrowhead=2, arrowcolor="#f85149",
                    font=dict(color="#f85149", size=10),
                    bgcolor="#0d1117", bordercolor="#f85149")

            bc3 = ["#f85149" if r["機関異常"] else "#e3b341" if r["出来高異常"] else col
                   for _, r in Pa.iterrows()]
            fig3.add_trace(go.Bar(x=Pa["日付"], y=Pa["出来高"], name="出来高",
                marker_color=bc3, opacity=0.85,
                hovertemplate="日付:%{x}<br>出来高:%{y:,.0f}<extra></extra>"), row=2, col=1)
            fig3.add_hline(y=vm, line_dash="dot", line_color="#8b949e",
                annotation_text=f"月平均 {vm/1e6:.1f}M",
                annotation_font_color="#8b949e", row=2, col=1)
            fig3.add_hline(y=vm*2, line_dash="dash", line_color="#e3b341",
                annotation_text="×2", annotation_font_color="#e3b341", row=2, col=1)
            fig_base(fig3, 420); st.plotly_chart(fig3, use_container_width=True)

            # 株価テーブル（直近が上・25日乖離・PER・PBR付き）
            raw_p = {c: P[c].copy() for c in ["終値","出来高","前日比%","25日乖離率","PER","PBR"]}
            pt = pd.DataFrame(); pt["日付"] = P["日付"]
            pt["終値"]      = raw_p["終値"].apply(lambda v: f"¥{v:,.1f}" if pd.notna(v) else "-")
            pt["前日比%"]   = raw_p["前日比%"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")
            pt["25日乖離率"] = raw_p["25日乖離率"].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")
            pt["PER"]       = raw_p["PER"].apply(lambda v: f"{v:.2f}倍" if pd.notna(v) else "-")
            pt["PBR"]       = raw_p["PBR"].apply(lambda v: f"{v:.2f}倍" if pd.notna(v) else "-")
            pt["株価判定"]   = P["機関異常"].map({True:"🔴 機関", False:"✅ 通常"})
            pt["出来高"]     = raw_p["出来高"].apply(lambda v: f"{int(v):,}" if pd.notna(v) else "-")
            pt["出来高判定"]  = P["出来高異常"].map({True:"🟠 急増", False:"✅ 通常"})

            sc = ["日付","終値","前日比%","25日乖離率","PER","PBR","株価判定","出来高","出来高判定"]
            ar = {"日付":"【平均】",
                  "終値": f"¥{raw_p['終値'].mean():,.1f}",
                  "前日比%": f"{raw_p['前日比%'].mean(skipna=True):+.2f}%",
                  "25日乖離率": f"{raw_p['25日乖離率'].mean(skipna=True):+.2f}%",
                  "PER": f"{raw_p['PER'].mean(skipna=True):.2f}倍",
                  "PBR": f"{raw_p['PBR'].mean(skipna=True):.2f}倍",
                  "株価判定": "",
                  "出来高": f"{int(raw_p['出来高'].mean()):,}",
                  "出来高判定": f"月平均 {vm/1e6:.1f}M"}
            pt_show = pd.concat([pt[sc], pd.DataFrame([ar])], ignore_index=True)

            def sty_p(row):
                if row["日付"] == "【平均】":
                    return ["background-color:#1c2951;font-weight:700;color:#e3b341"] * len(row)
                if row.get("株価判定") == "🔴 機関":
                    return ["background-color:#2d1014;color:#ffa198"] * len(row)
                if row.get("出来高判定") == "🟠 急増":
                    return ["background-color:#2d1f00;color:#e3b341"] * len(row)
                styles = [""] * len(row); cols_l = list(row.index)
                for col_name, raw_col in [("前日比%","前日比%"), ("25日乖離率","25日乖離率")]:
                    if col_name in cols_l:
                        i = cols_l.index(col_name)
                        orig = P.loc[P["日付"] == row["日付"], raw_col]
                        if not orig.empty and pd.notna(orig.values[0]):
                            styles[i] = f"color:{vc(orig.values[0])};font-weight:600"
                return styles

            st.dataframe(pt_show.style.apply(sty_p, axis=1),
                use_container_width=True, hide_index=True)

# ── サマリー ──────────────────────────────────────────
st.divider()
st.markdown("<h3 style='color:#f0f6fc;font-size:14px;margin-bottom:10px'>④ 全銘柄サマリー</h3>",
            unsafe_allow_html=True)
cols4 = st.columns(2)
for i, (code, info) in enumerate(data.items()):
    L=info["lending"]; P=info["price"]; M=info["margin"]
    pr=info["pressure"]; c=COLORS.get(code,"#388bfd")
    prc=PR_COLORS.get(pr["label"],"#8b949e")

    pct="-"; pc="#c9d1d9"
    if not P.empty and len(P) >= 2:
        chg = (P["終値"].iloc[0]-P["終値"].iloc[-1])/P["終値"].iloc[-1]*100
        pct = f"{chg:+.2f}%"; pc = POS if chg >= 0 else NEG

    inst = f"{P['機関異常'].sum()}日/{len(P)}日" if not P.empty else "-"

    per_v = pbr_v = ma25_v = "-"
    if not P.empty:
        per_v  = fmt(P["PER"].dropna().iloc[0]  if not P["PER"].dropna().empty  else float("nan"), dec=2, suffix="倍")
        pbr_v  = fmt(P["PBR"].dropna().iloc[0]  if not P["PBR"].dropna().empty  else float("nan"), dec=2, suffix="倍")
        ma25_v = fmt(P["25日乖離率"].iloc[0]     if not P.empty else float("nan"), dec=2, suffix="%")

    lr = buy_a = sel_a = smr = "-"
    if not L.empty:
        r = L["貸借倍率"].iloc[-1]
        lr = "-" if pd.isna(r) else "∞" if abs(r)==float("inf") else f"{r:.2f}倍"
        buy_a = fmt(L["買い残高"].mean(skipna=True))
        sel_a = fmt(L["売い残高"].mean(skipna=True)) if "売い残高" in L.columns else fmt(L["売り残高"].mean(skipna=True))
    if not M.empty and "信用倍率" in M.columns:
        lm = M["信用倍率"].dropna()
        if not lm.empty: v=lm.iloc[-1]; smr=f"{v:.2f}倍"+(" 🔴" if v<1 else "")

    ma25_color = vc(P["25日乖離率"].iloc[0]) if not P.empty and pd.notna(P["25日乖離率"].iloc[0]) else "#c9d1d9"

    with cols4[i % 2]:
        st.markdown(f"""
        <div style="background:#161b22;border-top:3px solid {c};border-radius:8px;
            padding:12px;border:1px solid #30363d;margin-bottom:10px">
          <div style="color:{c};font-weight:700;font-size:14px;margin-bottom:3px">
              {code} {STOCKS[code]['name']}</div>
          <div style="color:{prc};font-weight:600;font-size:12px;margin-bottom:8px">
              {pr['label']}</div>
          <table style="width:100%;font-size:12px;border-collapse:collapse">
            <tr><td style="color:#8b949e;padding:2px 0">月間株価変化</td>
                <td style="color:{pc};font-weight:700;text-align:right">{pct}</td></tr>
            <tr><td style="color:#8b949e;padding:2px 0">機関異常検知</td>
                <td style="color:#f85149;text-align:right">{inst}</td></tr>
            <tr><td style="color:#8b949e;padding:2px 0">PER</td>
                <td style="color:#c9d1d9;text-align:right">{per_v}</td></tr>
            <tr><td style="color:#8b949e;padding:2px 0">PBR</td>
                <td style="color:#c9d1d9;text-align:right">{pbr_v}</td></tr>
            <tr><td style="color:#8b949e;padding:2px 0">25日乖離率(最新)</td>
                <td style="color:{ma25_color};font-weight:600;text-align:right">{ma25_v}</td></tr>
            <tr><td style="color:#8b949e;padding:2px 0">最新貸借倍率</td>
                <td style="color:#c9d1d9;text-align:right">{lr}</td></tr>
            <tr><td style="color:#8b949e;padding:2px 0">最新信用倍率</td>
                <td style="color:#c9d1d9;text-align:right">{smr}</td></tr>
            <tr><td style="color:#8b949e;padding:2px 0">買い残高 平均</td>
                <td style="color:{POS};text-align:right">{buy_a}</td></tr>
            <tr><td style="color:#8b949e;padding:2px 0">売り残高 平均</td>
                <td style="color:{NEG};text-align:right">{sel_a}</td></tr>
          </table>
        </div>""", unsafe_allow_html=True)

st.markdown("<p style='text-align:center;font-size:10px;color:#484f58;margin-top:8px'>"
    "全データ出典：IRバンク(irbank.net) ｜ 投資勧誘を目的としません</p>",
    unsafe_allow_html=True)
