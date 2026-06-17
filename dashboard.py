"""
dashboard.py  ─  streamlit run dashboard.py
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scraper import fetch_one, STOCKS, _safe_int_fmt
import socket, re

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
[data-testid="stTextInput"] input{background:#161b22!important;color:#f0f6fc!important;
  border:1px solid #30363d!important;border-radius:8px!important;font-size:16px!important}
hr{border-color:#30363d!important}
</style>""", unsafe_allow_html=True)

COLORS = ["#388bfd","#f78166","#3fb950","#bc8cff","#e3b341","#58a6ff","#ff7b72","#79c0ff"]
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

# ── セッション状態の初期化 ────────────────────────────
# watch_list: {code: info_dict} の順序付き辞書
if "watch_list" not in st.session_state:
    st.session_state.watch_list = dict(STOCKS)   # デフォルト4銘柄
if "stock_data" not in st.session_state:
    st.session_state.stock_data = {}             # コード→データのキャッシュ

# ── タイトル ──────────────────────────────────────────
st.markdown("<h2 style='text-align:center;color:#f0f6fc;font-size:20px;margin:4px 0'>"
            "📊 株式貸借・株価分析ダッシュボード</h2>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center;color:#8b949e;font-size:11px;margin:0 0 12px'>"
            "全データ出典：IRバンク(irbank.net) ／ nisshokin・margin・chart</p>",
            unsafe_allow_html=True)

# ── 検索フォーム ──────────────────────────────────────
st.markdown("<h3 style='color:#f0f6fc;font-size:14px;margin:0 0 8px'>🔍 銘柄検索・追加</h3>",
            unsafe_allow_html=True)

with st.form("search_form", clear_on_submit=True):
    sc1, sc2, sc3 = st.columns([2, 1, 1])
    with sc1:
        input_code = st.text_input(
            "銘柄コード（4桁）",
            placeholder="例：7203（トヨタ）",
            label_visibility="collapsed"
        )
    with sc2:
        add_btn = st.form_submit_button("➕ 追加して取得", use_container_width=True)
    with sc3:
        search_only = st.form_submit_button("🔄 この銘柄のみ表示", use_container_width=True)

# 入力値の検証と処理
if (add_btn or search_only) and input_code:
    code_clean = input_code.strip().zfill(4)
    if not re.fullmatch(r"\d{4}", code_clean):
        st.error("❌ 4桁の数字を入力してください（例：7203）")
    else:
        with st.spinner(f"{code_clean} のデータを取得中…（約15秒）"):
            info = fetch_one(code_clean)

        if info["lending"].empty and info["price"].empty:
            st.error(f"❌ {code_clean} のデータを取得できませんでした。銘柄コードを確認してください。")
        else:
            name = info["name"] or code_clean
            st.session_state.stock_data[code_clean] = info

            if search_only:
                # この銘柄のみ表示モード
                st.session_state.watch_list = {code_clean: {"name": name}}
                st.success(f"✅ {code_clean} {name} を表示中")
            else:
                # ウォッチリストに追加
                st.session_state.watch_list[code_clean] = {"name": name}
                st.success(f"✅ {code_clean} {name} をウォッチリストに追加しました")

# ── ウォッチリスト管理 ────────────────────────────────
wl = st.session_state.watch_list
if wl:
    st.markdown("<p style='color:#8b949e;font-size:12px;margin:8px 0 4px'>📋 現在のウォッチリスト</p>",
                unsafe_allow_html=True)
    tag_cols = st.columns(min(len(wl), 8))
    for i, (c, inf) in enumerate(list(wl.items())):
        with tag_cols[i % len(tag_cols)]:
            col_c = COLORS[i % len(COLORS)]
            remove = st.button(
                f"✕ {c} {inf.get('name','')}", key=f"rm_{c}",
                help=f"{c} をウォッチリストから削除"
            )
            if remove:
                st.session_state.watch_list.pop(c, None)
                st.session_state.stock_data.pop(c, None)
                st.rerun()

# 全更新ボタン
rc1, rc2 = st.columns([1, 4])
with rc1:
    if st.button("🔄 全銘柄を最新化", type="primary"):
        st.session_state.stock_data = {}
        st.rerun()
with rc2:
    st.caption("10分キャッシュ ｜ 全銘柄最新化は約15秒×銘柄数かかります")

st.divider()

# ── データ取得（未取得の銘柄のみ） ───────────────────
@st.cache_data(ttl=600, show_spinner=False)
def cached_fetch(code: str) -> dict:
    return fetch_one(code)

for code in list(st.session_state.watch_list.keys()):
    if code not in st.session_state.stock_data:
        with st.spinner(f"{code} {st.session_state.watch_list[code].get('name','')} を取得中…"):
            result = cached_fetch(code)
            st.session_state.stock_data[code] = result
            # 社名をウォッチリストに反映
            if result["name"] and result["name"] != code:
                st.session_state.watch_list[code]["name"] = result["name"]

if not st.session_state.stock_data:
    st.info("銘柄コードを入力して検索するか、上の「全銘柄を最新化」ボタンを押してください。")
    st.stop()

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

def get_latest_margin_bal(M):
    if M.empty: return None, None
    ms = M.sort_values("_dt", ascending=False)
    buy = ms["買い残高"].dropna()
    sel = ms["売り残高"].dropna()
    return (buy.iloc[0] if not buy.empty else None,
            sel.iloc[0] if not sel.empty else None)


# ════════════════════════════════════════
# 銘柄タブ描画関数
# ════════════════════════════════════════
def render_stock(code: str, info: dict, col_hex: str):
    L = info["lending"]; P = info["price"]; M = info["margin"]
    pr = info["pressure"]
    prc = PR_COLORS.get(pr["label"], "#8b949e")
    latest_margin_buy, latest_margin_sel = get_latest_margin_bal(M)

    # 圧力バナー
    st.markdown(f"""<div style="background:#161b22;border-left:4px solid {prc};
        border-radius:6px;padding:10px 14px;margin-bottom:14px">
      <span style="font-size:1.05rem;font-weight:700;color:{prc}">{pr['label']}</span>
      <span style="color:#8b949e;font-size:0.82rem;margin-left:10px">{pr['detail']}</span>
    </div>""", unsafe_allow_html=True)

    # ── ① 貸借取引残高 ──────────────────────────────
    st.markdown("<h3 style='color:#f0f6fc;font-size:14px;margin:0 0 4px'>"
                "① 貸借取引残高 + 逆日歩（直近順）</h3>", unsafe_allow_html=True)
    if latest_margin_buy and latest_margin_sel:
        st.caption(
            f"🔴赤=差引マイナス ／ 🟡橙=平均3倍超 ／ 🟠逆日歩発生 ／ 青=プラス・赤=マイナス\n"
            f"信用%：最新週次信用残（買い:{int(latest_margin_buy):,} / 売り:{int(latest_margin_sel):,}）に占める割合"
        )
    else:
        st.caption("🔴赤=差引マイナス ／ 🟡橙=平均3倍超 ／ 🟠逆日歩発生")

    if L.empty:
        st.warning("貸借データを取得できませんでした。")
    else:
        La = L.sort_values("_dt", ascending=True)
        fig1 = make_subplots(rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.65, 0.35], vertical_spacing=0.05,
            subplot_titles=["買い残高・売り残高 ＋ 株価（右軸）","資金フロー（買い新規－売り新規）"],
            specs=[[{"secondary_y": True}],[{"secondary_y": False}]])
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
        if not P.empty:
            Pa_asc = P.sort_values("_dt", ascending=True)
            fig1.add_trace(go.Scatter(x=Pa_asc["日付"], y=Pa_asc["終値"], name="株価",
                line=dict(color="#e3b341", width=1.5, dash="dot"),
                hovertemplate="%{x}<br>株価:¥%{y:,.1f}<extra></extra>"),
                row=1, col=1, secondary_y=True)
            fig1.update_yaxes(title_text="株価(円)", secondary_y=True,
                gridcolor="#21262d", tickfont=dict(color="#e3b341", size=9),
                tickformat=",", row=1, col=1)
        flow = La["買い新規"].fillna(0) - La["売り新規"].fillna(0)
        fig1.add_trace(go.Bar(x=La["申込日"], y=flow, name="資金フロー",
            marker_color=["#388bfd" if v>=0 else "#f85149" for v in flow], opacity=0.85,
            hovertemplate="%{x}<br>フロー:%{y:,.0f}<extra></extra>"), row=2, col=1)
        fig1.add_hline(y=0, line_dash="solid", line_color="#484f58", line_width=1, row=2, col=1)
        fig_base(fig1, 400); st.plotly_chart(fig1, use_container_width=True)

        LCOLS = ["申込日","買い残高","買い残高(信用%)","買い増減","買い新規","買い返済",
                 "売り残高","売り残高(信用%)","売り増減","売り新規","売り返済","貸借倍率","逆日歩"]
        LNUM  = ["買い残高","買い増減","買い新規","買い返済","売り残高","売り増減","売い新規","売り返済"]
        LNUM  = ["買い残高","買い増減","買い新規","買い返済","売り残高","売り増減","売り新規","売り返済"]
        Ld = L.sort_values("_dt", ascending=False).reset_index(drop=True)
        Ld["差引残高"] = Ld["買い残高"].fillna(0) - Ld["売り残高"].fillna(0)
        dp = pd.DataFrame(); dp["申込日"] = Ld["申込日"]
        dp["買い残高"] = Ld["買い残高"].apply(fmt)
        dp["買い残高(信用%)"] = Ld["買い残高"].apply(
            lambda v: f"{float(v)/latest_margin_buy*100:.1f}%"
            if pd.notna(v) and latest_margin_buy and latest_margin_buy > 0 else "-")
        dp["買い増減"] = Ld["買い増減"].apply(fmt)
        dp["買い新規"] = Ld["買い新規"].apply(fmt)
        dp["買い返済"] = Ld["買い返済"].apply(fmt)
        dp["売り残高"] = Ld["売り残高"].apply(fmt)
        dp["売り残高(信用%)"] = Ld["売り残高"].apply(
            lambda v: f"{float(v)/latest_margin_sel*100:.1f}%"
            if pd.notna(v) and latest_margin_sel and latest_margin_sel > 0 else "-")
        dp["売り増減"] = Ld["売り増減"].apply(fmt)
        dp["売り新規"] = Ld["売り新規"].apply(fmt)
        dp["売り返済"] = Ld["売り返済"].apply(fmt)
        dp["貸借倍率"] = Ld["貸借倍率"].apply(
            lambda v: "-" if pd.isna(v) else "∞" if abs(v)==float("inf") else f"{v:.2f}倍")
        dp["逆日歩"] = Ld["逆日歩"].apply(
            lambda v: f"{v:.2f}" if pd.notna(v) and v > 0 else "-")
        av = {c:"" for c in LCOLS}; av["申込日"]="【平均】"
        for c in LNUM: av[c]=fmt(Ld[c].mean(skipna=True))
        av["貸借倍率"]=fmt(Ld["貸借倍率"].replace([float("inf"),float("-inf")],float("nan")).mean(skipna=True),dec=2,suffix="倍")
        av["逆日歩"]=fmt(Ld["逆日歩"].mean(skipna=True),dec=2)
        if latest_margin_buy and latest_margin_buy>0:
            av["買い残高(信用%)"]=f"{Ld['買い残高'].mean(skipna=True)/latest_margin_buy*100:.1f}%"
        if latest_margin_sel and latest_margin_sel>0:
            av["売り残高(信用%)"]=f"{Ld['売り残高'].mean(skipna=True)/latest_margin_sel*100:.1f}%"
        disp_l=pd.concat([dp[LCOLS],pd.DataFrame([av])],ignore_index=True)
        st_l=cell_style(disp_l,Ld,"申込日",LNUM,neg_red=["差引残高"])
        for idx in disp_l[disp_l["申込日"]!="【平均】"].index:
            dv=disp_l.loc[idx,"申込日"]
            orig=Ld.loc[Ld["申込日"]==dv,"逆日歩"]
            if not orig.empty and pd.notna(orig.values[0]) and orig.values[0]>0:
                st_l.loc[idx,"逆日歩"]="background-color:#2d1f00;color:#e3b341;font-weight:700"
            for c in ["買い残高(信用%)","売り残高(信用%)"]:
                if st_l.loc[idx,c]=="": st_l.loc[idx,c]="color:#8b949e;font-size:11px"
        st.dataframe(disp_l.style.apply(lambda _:st_l,axis=None),
            use_container_width=True,hide_index=True,height=min(38*(len(disp_l)+1)+38,560))

    # ── ② 週次信用残 ─────────────────────────────────
    st.markdown("<h3 style='color:#f0f6fc;font-size:14px;margin:14px 0 4px'>"
                "② 週次信用残（直近順）</h3>", unsafe_allow_html=True)
    st.caption("🔴赤=信用倍率1倍未満 ／ 🟡橙=平均3倍超 ／ 🟠逆日歩発生 ／ 消化日数：残高÷日次平均出来高")

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

        daily_vol_avg = P["出来高"].mean() if not P.empty else None
        MCOLS=["日付","買い残高","買い残消化日数","買い増減",
               "売り残高","売り残消化日数","売り増減","信用需給ネット",
               "信用倍率","買い残増減率","売り残増減率","逆日歩"]
        MNUM=["買い残高","買い増減","売り残高","売り増減","信用倍率","買い残増減率","売り残増減率"]
        Md=M.sort_values("_dt",ascending=False).reset_index(drop=True)
        Md["買い残消化日数_num"]=Md["買い残高"].apply(
            lambda v: float(v)/daily_vol_avg if pd.notna(v) and daily_vol_avg else float("nan"))
        Md["売り残消化日数_num"]=Md["売り残高"].apply(
            lambda v: float(v)/daily_vol_avg if pd.notna(v) and daily_vol_avg else float("nan"))
        Md["信用需給ネット_num"]=Md["買い残消化日数_num"]-Md["売り残消化日数_num"]
        dm=pd.DataFrame(); dm["日付"]=Md["日付"]
        dm["買い残高"]=Md["買い残高"].apply(fmt)
        dm["買い残消化日数"]=Md["買い残消化日数_num"].apply(lambda v:f"{v:.1f}日" if pd.notna(v) else "-")
        dm["買い増減"]=Md["買い増減"].apply(fmt)
        dm["売り残高"]=Md["売り残高"].apply(fmt)
        dm["売り残消化日数"]=Md["売り残消化日数_num"].apply(lambda v:f"{v:.1f}日" if pd.notna(v) else "-")
        dm["売り増減"]=Md["売り増減"].apply(fmt)
        dm["信用需給ネット"]=Md["信用需給ネット_num"].apply(lambda v:f"{v:+.1f}日" if pd.notna(v) else "-")
        dm["信用倍率"]=Md["信用倍率"].apply(lambda v:f"{v:.2f}倍" if pd.notna(v) else "-")
        dm["買い残増減率"]=Md["買い残増減率"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")
        dm["売り残増減率"]=Md["売り残増減率"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")
        dm["逆日歩"]=Md["逆日歩"].apply(lambda v:f"{v:.2f}" if pd.notna(v) and v>0 else "-")
        av_m={c:"" for c in MCOLS}; av_m["日付"]="【平均】"
        for c in ["買い残高","売り残高"]: av_m[c]=fmt(Md[c].mean(skipna=True))
        av_m["信用倍率"]=fmt(Md["信用倍率"].mean(skipna=True),dec=2,suffix="倍")
        for c in ["買い残増減率","売り残増減率"]:
            v2=Md[c].mean(skipna=True); av_m[c]=f"{v2:+.2f}%" if pd.notna(v2) else "-"
        if daily_vol_avg:
            av_m["買い残消化日数"]=f"{Md['買い残消化日数_num'].mean(skipna=True):.1f}日"
            av_m["売り残消化日数"]=f"{Md['売り残消化日数_num'].mean(skipna=True):.1f}日"
            av_m["信用需給ネット"]=f"{Md['信用需給ネット_num'].mean(skipna=True):+.1f}日"
        disp_m=pd.concat([dm[MCOLS],pd.DataFrame([av_m])],ignore_index=True)
        st_m=cell_style(disp_m,Md,"日付",MNUM,thr=3.0)
        for idx in disp_m[disp_m["日付"]!="【平均】"].index:
            dv=disp_m.loc[idx,"日付"]
            orig=Md.loc[Md["日付"]==dv,"信用倍率"]
            if not orig.empty and pd.notna(orig.values[0]) and orig.values[0]<1:
                st_m.loc[idx,"信用倍率"]="background-color:#3d1a1a;color:#f85149;font-weight:700"
            orig2=Md.loc[Md["日付"]==dv,"逆日歩"]
            if not orig2.empty and pd.notna(orig2.values[0]) and orig2.values[0]>0:
                st_m.loc[idx,"逆日歩"]="background-color:#2d1f00;color:#e3b341;font-weight:700"
            for c in ["買い残増減率","売り残増減率"]:
                if st_m.loc[idx,c]!="": continue
                o3=Md.loc[Md["日付"]==dv,c]
                if not o3.empty and pd.notna(o3.values[0]):
                    st_m.loc[idx,c]=f"color:{vc(o3.values[0])}"
            for col_n,num_col in [("買い残消化日数","買い残消化日数_num"),("売り残消化日数","売り残消化日数_num")]:
                bd=Md.loc[Md["日付"]==dv,num_col]
                if not bd.empty and pd.notna(bd.values[0]):
                    bv=bd.values[0]
                    if bv>100: st_m.loc[idx,col_n]="background-color:#2d1f00;color:#e3b341;font-weight:700"
                    elif bv>20: st_m.loc[idx,col_n]="background-color:#3d1a1a;color:#f85149;font-weight:700"
            nd=Md.loc[Md["日付"]==dv,"信用需給ネット_num"]
            if not nd.empty and pd.notna(nd.values[0]) and st_m.loc[idx,"信用需給ネット"]=="":
                st_m.loc[idx,"信用需給ネット"]=f"color:{NEG if nd.values[0]>0 else POS};font-weight:600"
        st.dataframe(disp_m.style.apply(lambda _:st_m,axis=None),
            use_container_width=True,hide_index=True,height=min(38*(len(disp_m)+1)+38,520))
        if daily_vol_avg:
            st.caption(f"※消化日数=残高÷日次平均出来高({daily_vol_avg/1e6:.1f}M株/日)　"
                       "🔴赤=20日超 ／ 🟡橙=100日超　信用需給ネット：プラス=売り圧力 / マイナス=踏み上げ余地")

    # ── ③ 株価急変 ＋ 出来高 ──────────────────────────
    st.markdown("<h3 style='color:#f0f6fc;font-size:14px;margin:14px 0 4px'>"
                "③ 株価急変 ＋ 出来高（直近順）</h3>", unsafe_allow_html=True)
    st.caption("🔴★=大口機関の可能性 ／ 🟡=出来高月平均×2超 ／ 25日乖離・PER・PBR付き")
    with st.expander("🔍 大口機関の可能性（🔴★）の判定定義", expanded=False):
        st.markdown("""
| 条件 | 内容 |
|---|---|
| ① 大量売買＋価格インパクト | 出来高が月平均の **2倍超** かつ 前日比 **±1.5%以上** |
| ② 急騰・急落＋出来高急増 | 前日比 **±4%以上** かつ 出来高が月平均の **1.5倍超** |
| ③ 日中値幅急拡大 | 当日の高値−安値が過去5日平均値幅の **2倍超** |
> ①〜③のいずれかを満たした場合に 🔴機関 と判定。確定的な判断には別途分析が必要です。
        """)

    if P.empty:
        st.warning("株価データを取得できませんでした。")
    else:
        vm=P["出来高平均"].iloc[0]; Pa=P.sort_values("_dt",ascending=True)
        fig3=make_subplots(rows=2,cols=1,shared_xaxes=True,
            row_heights=[0.65,0.35],vertical_spacing=0.04)
        mc2=["#f85149" if a else col_hex for a in Pa["機関異常"]]
        ms2=[10 if a else 5 for a in Pa["機関異常"]]
        sym=["star" if a else "circle" for a in Pa["機関異常"]]
        fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["終値"],mode="lines+markers",name="終値",
            line=dict(color=col_hex,width=2),
            marker=dict(size=ms2,color=mc2,symbol=sym,
                line=dict(width=1.5,color="rgba(248,81,73,0.4)")),
            hovertemplate="日付:%{x}<br>終値:¥%{y:,.1f}<extra></extra>"),row=1,col=1)
        for _,r in Pa[Pa["機関異常"]].iterrows():
            chg=r.get("前日比%",float("nan"))
            fig3.add_annotation(x=r["日付"],y=r["終値"],
                text=f"<b>{chg:+.1f}%</b>" if pd.notna(chg) else "<b>⚠</b>",
                showarrow=True,arrowhead=2,arrowcolor="#f85149",
                font=dict(color="#f85149",size=10),bgcolor="#0d1117",bordercolor="#f85149")
        bc3=["#f85149" if r["機関異常"] else "#e3b341" if r["出来高異常"] else col_hex
             for _,r in Pa.iterrows()]
        fig3.add_trace(go.Bar(x=Pa["日付"],y=Pa["出来高"],name="出来高",
            marker_color=bc3,opacity=0.85,
            hovertemplate="日付:%{x}<br>出来高:%{y:,.0f}<extra></extra>"),row=2,col=1)
        fig3.add_hline(y=vm,line_dash="dot",line_color="#8b949e",
            annotation_text=f"月平均 {vm/1e6:.1f}M",annotation_font_color="#8b949e",row=2,col=1)
        fig3.add_hline(y=vm*2,line_dash="dash",line_color="#e3b341",
            annotation_text="×2",annotation_font_color="#e3b341",row=2,col=1)
        fig_base(fig3,420); st.plotly_chart(fig3,use_container_width=True)

        raw_p={c:P[c].copy() for c in ["始値","高値","安値","終値","出来高","前日比%","25日乖離率","PER","PBR"]}
        pt=pd.DataFrame(); pt["日付"]=P["日付"]
        pt["始値"]=raw_p["始値"].apply(lambda v:f"¥{v:,.1f}" if pd.notna(v) else "-")
        pt["高値"]=raw_p["高値"].apply(lambda v:f"¥{v:,.1f}" if pd.notna(v) else "-")
        pt["安値"]=raw_p["安値"].apply(lambda v:f"¥{v:,.1f}" if pd.notna(v) else "-")
        pt["終値"]=raw_p["終値"].apply(lambda v:f"¥{v:,.1f}" if pd.notna(v) else "-")
        pt["前日比%"]=raw_p["前日比%"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")
        pt["25日乖離率"]=raw_p["25日乖離率"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")
        pt["PER"]=raw_p["PER"].apply(lambda v:f"{v:.2f}倍" if pd.notna(v) else "-")
        pt["PBR"]=raw_p["PBR"].apply(lambda v:f"{v:.2f}倍" if pd.notna(v) else "-")
        pt["株価判定"]=P["機関異常"].map({True:"🔴 機関",False:"✅ 通常"})
        pt["出来高"]=raw_p["出来高"].apply(lambda v:f"{int(v):,}" if pd.notna(v) else "-")
        pt["出来高判定"]=P["出来高異常"].map({True:"🟠 急増",False:"✅ 通常"})
        sc=["日付","始値","高値","安値","終値","前日比%","25日乖離率","PER","PBR","株価判定","出来高","出来高判定"]
        ar={"日付":"【平均】",
            "始値":f"¥{raw_p['始値'].mean():,.1f}","高値":f"¥{raw_p['高値'].mean():,.1f}",
            "安値":f"¥{raw_p['安値'].mean():,.1f}","終値":f"¥{raw_p['終値'].mean():,.1f}",
            "前日比%":f"{raw_p['前日比%'].mean(skipna=True):+.2f}%",
            "25日乖離率":f"{raw_p['25日乖離率'].mean(skipna=True):+.2f}%",
            "PER":f"{raw_p['PER'].mean(skipna=True):.2f}倍",
            "PBR":f"{raw_p['PBR'].mean(skipna=True):.2f}倍",
            "株価判定":"","出来高":f"{int(raw_p['出来高'].mean()):,}",
            "出来高判定":f"月平均 {vm/1e6:.1f}M"}
        pt_show=pd.concat([pt[sc],pd.DataFrame([ar])],ignore_index=True)
        def sty_p(row):
            if row["日付"]=="【平均】":
                return ["background-color:#1c2951;font-weight:700;color:#e3b341"]*len(row)
            if row.get("株価判定")=="🔴 機関":
                return ["background-color:#2d1014;color:#ffa198"]*len(row)
            if row.get("出来高判定")=="🟠 急増":
                return ["background-color:#2d1f00;color:#e3b341"]*len(row)
            styles=[""]*len(row); cols_l=list(row.index)
            for cn,rc in [("前日比%","前日比%"),("25日乖離率","25日乖離率")]:
                if cn in cols_l:
                    i=cols_l.index(cn)
                    orig=P.loc[P["日付"]==row["日付"],rc]
                    if not orig.empty and pd.notna(orig.values[0]):
                        styles[i]=f"color:{vc(orig.values[0])};font-weight:600"
            return styles
        st.dataframe(pt_show.style.apply(sty_p,axis=1),use_container_width=True,hide_index=True)


# ════════════════════════════════════════
# メイン：タブを動的生成
# ════════════════════════════════════════
codes  = list(st.session_state.watch_list.keys())
labels = [f"{c} {st.session_state.watch_list[c].get('name','')}" for c in codes]

if not codes:
    st.info("ウォッチリストが空です。上の検索フォームから銘柄を追加してください。")
    st.stop()

tabs = st.tabs(labels)
for tab, code in zip(tabs, codes):
    col_hex = COLORS[codes.index(code) % len(COLORS)]
    info    = st.session_state.stock_data.get(code)
    with tab:
        if not info:
            st.warning(f"{code} のデータがありません。最新化ボタンを押してください。")
        else:
            render_stock(code, info, col_hex)

# ── フッター ──────────────────────────────────────────
st.markdown("<p style='text-align:center;font-size:10px;color:#484f58;margin-top:8px'>"
    "全データ出典：IRバンク(irbank.net) ｜ 投資勧誘を目的としません</p>",
    unsafe_allow_html=True)
