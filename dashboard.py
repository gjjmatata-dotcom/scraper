"""
dashboard.py  ─  streamlit run dashboard.py
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scraper import fetch_one, fetch_price_by_url, STOCKS, _safe_int_fmt
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
  font-weight:600;min-height:44px;font-size:14px!important}
[data-testid="stTextInput"] input{background:#161b22!important;color:#f0f6fc!important;
  border:1px solid #30363d!important;border-radius:8px!important}
hr{border-color:#30363d!important}
</style>""", unsafe_allow_html=True)

COLORS = ["#388bfd","#f78166","#3fb950","#bc8cff","#e3b341","#58a6ff","#ff7b72","#79c0ff"]
PR_COLORS = {"🔴 売り圧力優勢":"#f85149","🟢 買い圧力優勢":"#3fb950",
             "🟠 高値売り圧力":"#d29922","🔵 安値買い戻し":"#388bfd",
             "⚪ 中立":"#8b949e","データ不足":"#8b949e"}
POS, NEG = "#58a6ff", "#f85149"

def vc(v):
    try: return POS if float(v)>=0 else NEG
    except: return "#c9d1d9"

def fmt(v, dec=0, suffix=""):
    try:
        f=float(v)
        if f!=f or abs(f)==float("inf"): return "-"
        s=f"{int(round(f)):,}" if dec==0 else f"{f:,.{dec}f}"
        return s+suffix
    except: return "-"

# ── セッション初期化 ──────────────────────────────────
for k,v in [("watch_list",{}),("stock_data",{}),("search_history",{})]:
    if k not in st.session_state: st.session_state[k]=v

# ── サイドバー ────────────────────────────────────────
try: ip=socket.gethostbyname(socket.gethostname())
except: ip="取得失敗"
st.sidebar.markdown("### 📱 LAN内アクセス")
st.sidebar.code(f"http://{ip}:8501")
st.sidebar.caption("`--server.address 0.0.0.0` で起動")
st.sidebar.markdown("[🌐 Streamlit Cloud](https://share.streamlit.io/)")

st.markdown("<h2 style='text-align:center;color:#f0f6fc;font-size:20px;margin:4px 0'>"
            "📊 株式貸借・株価分析ダッシュボード</h2>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center;color:#8b949e;font-size:11px;margin:0 0 10px'>"
            "出典：IRバンク / 日証金 / Yahoo Finance Japan / kabutan.jp</p>",
            unsafe_allow_html=True)

# ── 検索フォーム ──────────────────────────────────────
st.markdown("#### 🔍 銘柄検索", unsafe_allow_html=True)
with st.form("search_form", clear_on_submit=True):
    c1,c2,c3=st.columns([3,1,1])
    with c1: inp=st.text_input("銘柄コード",placeholder="例：7203 / 998405 / 9I31115A / NDX",label_visibility="collapsed")
    with c2: add_btn=st.form_submit_button("➕ 追加",use_container_width=True)
    with c3: only_btn=st.form_submit_button("🔄 単独表示",use_container_width=True)

def _normalize(code):
    c=code.strip().upper().replace(" ","")
    return c.zfill(4) if re.fullmatch(r"\d{1,4}",c) else c

def _do_fetch(code):
    info=fetch_one(code)
    name=info["name"] or code
    st.session_state.stock_data[code]=info
    # 検索履歴（最大10件・最新先頭）
    h=st.session_state.search_history
    h.pop(code,None)
    st.session_state.search_history=dict(list({code:name,**h}.items())[:10])
    return info,name

if (add_btn or only_btn) and inp:
    code=_normalize(inp)
    with st.spinner(f"{code} 取得中…"):
        info,name=_do_fetch(code)
    if info["lending"].empty and info["price"].empty:
        st.error(f"❌ {code} のデータを取得できませんでした。")
    else:
        if only_btn: st.session_state.watch_list={code:{"name":name}}
        else: st.session_state.watch_list[code]={"name":name}
        st.success(f"✅ {code} {name}")

# 検索履歴ボタン
hist=st.session_state.search_history
if hist:
    st.caption("🕐 検索履歴（クリックで追加）")
    hcols=st.columns(min(len(hist),5))
    for j,(hc,hn) in enumerate(hist.items()):
        with hcols[j%5]:
            if st.button(f"＋{hc} {hn}",key=f"h_{hc}"):
                if hc not in st.session_state.stock_data:
                    with st.spinner(f"{hc}取得中…"):
                        _do_fetch(hc)
                st.session_state.watch_list[hc]={"name":hn}
                st.rerun()

# デフォルト4銘柄ボタン
st.caption("📋 デフォルト銘柄")
dcols=st.columns(len(STOCKS)+1)
for i,(dc,di) in enumerate(STOCKS.items()):
    with dcols[i]:
        if st.button(f"＋{dc} {di['name']}",key=f"def_{dc}"):
            if dc not in st.session_state.stock_data:
                with st.spinner(f"{dc}取得中…"):
                    _do_fetch(dc)
            st.session_state.watch_list[dc]={"name":di["name"]}
            st.rerun()
with dcols[len(STOCKS)]:
    if st.button("🔄 全更新"):
        st.session_state.stock_data={}
        st.rerun()

# ウォッチリスト管理
wl=st.session_state.watch_list
if wl:
    st.caption("📌 ウォッチリスト（✕で削除）")
    wcols=st.columns(min(len(wl),6))
    for i,(wc,wi) in enumerate(list(wl.items())):
        with wcols[i%6]:
            if st.button(f"✕ {wc} {wi.get('name','')}",key=f"rm_{wc}"):
                wl.pop(wc,None); st.session_state.stock_data.pop(wc,None); st.rerun()

st.divider()

if not wl:
    st.info("上の検索フォームまたはデフォルト銘柄ボタンから銘柄を追加してください。")
    st.stop()

# 未取得銘柄を自動取得
for code in list(wl.keys()):
    if code not in st.session_state.stock_data:
        with st.spinner(f"{code} 取得中…"):
            _do_fetch(code)


# ── Plotly共通 ────────────────────────────────────────
def fig_base(fig, h=380):
    fig.update_layout(paper_bgcolor="#0d1117",plot_bgcolor="#161b22",
        font=dict(color="#c9d1d9",size=11),
        legend=dict(bgcolor="rgba(22,27,34,0.9)",bordercolor="#30363d",borderwidth=1,
                    orientation="h",yanchor="bottom",y=1.02),
        margin=dict(l=4,r=4,t=36,b=4),height=h)
    fig.update_xaxes(gridcolor="#21262d",linecolor="#30363d",
                     tickfont=dict(color="#8b949e",size=10),tickangle=-30)
    fig.update_yaxes(gridcolor="#21262d",linecolor="#30363d",
                     tickfont=dict(color="#8b949e",size=10),tickformat=",")

# ── セルスタイル ──────────────────────────────────────
def cell_style(disp,raw,dcol,num_cols,neg_red=None,thr=3.0):
    styled=pd.DataFrame("",index=disp.index,columns=disp.columns)
    avg_m=disp[dcol]=="【平均】"
    styled.loc[avg_m]="background-color:#1c2951;font-weight:700;color:#e3b341"
    neg_red=neg_red or []
    for col in num_cols:
        if col not in raw.columns: continue
        s=raw[col].replace([float("inf"),float("-inf")],float("nan")).dropna()
        if s.empty: continue
        ca=s.abs().mean()
        for idx in disp[~avg_m].index:
            dv=disp.loc[idx,dcol]
            orig=raw.loc[raw[dcol]==dv,col]
            if orig.empty or pd.isna(orig.values[0]): continue
            v=float(orig.values[0])
            if col in neg_red and v<0:
                styled.loc[idx,col]="background-color:#3d1a1a;color:#f85149;font-weight:700"
            elif ca>0 and abs(v)>=ca*thr:
                styled.loc[idx,col]="background-color:#2d1f00;color:#e3b341;font-weight:700"
            elif styled.loc[idx,col]=="":
                styled.loc[idx,col]=f"color:{vc(v)}"
    return styled

def get_latest_margin_bal(M):
    if M.empty: return None,None
    ms=M.sort_values("_dt",ascending=False)
    b=ms["買い残高"].dropna(); s=ms["売り残高"].dropna()
    return (b.iloc[0] if not b.empty else None),(s.iloc[0] if not s.empty else None)


# ════════════════════════════════════════
# 銘柄描画関数
# ════════════════════════════════════════
def render_stock(code, info, col_hex):
    L=info["lending"]; P=info["price"]; M=info["margin"]
    pr=info["pressure"]; prc=PR_COLORS.get(pr["label"],"#8b949e")
    lmb,lms=get_latest_margin_bal(M)

    st.markdown(f"""<div style="background:#161b22;border-left:4px solid {prc};
        border-radius:6px;padding:10px 14px;margin-bottom:12px">
      <span style="font-size:1.05rem;font-weight:700;color:{prc}">{pr['label']}</span>
      <span style="color:#8b949e;font-size:0.82rem;margin-left:10px">{pr['detail']}</span>
    </div>""", unsafe_allow_html=True)

    # ① 貸借取引残高
    if not L.empty:
        st.markdown("**① 貸借取引残高 + 逆日歩**")
        if lmb and lms:
            st.caption(f"信用%：最新週次信用残（買:{int(lmb):,} / 売:{int(lms):,}）に占める割合 ／ 🔴差引マイナス ／ 🟡平均3倍超")
        La=L.sort_values("_dt",ascending=True)
        fig1=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[0.65,0.35],
            vertical_spacing=0.05,
            subplot_titles=["買い残高・売り残高＋株価（右軸）","資金フロー（買い新規－売い新規）"],
            specs=[[{"secondary_y":True}],[{"secondary_y":False}]])
        fig1.add_trace(go.Scatter(x=La["申込日"],y=La["買い残高"],name="買い残高",
            line=dict(color="#388bfd",width=2),fill="tozeroy",fillcolor="rgba(56,139,253,0.08)",
            hovertemplate="%{x}<br>買い残高:%{y:,.0f}<extra></extra>"),row=1,col=1,secondary_y=False)
        fig1.add_trace(go.Scatter(x=La["申込日"],y=La["売り残高"],name="売り残高",
            line=dict(color="#f85149",width=2),fill="tozeroy",fillcolor="rgba(248,81,73,0.08)",
            hovertemplate="%{x}<br>売り残高:%{y:,.0f}<extra></extra>"),row=1,col=1,secondary_y=False)
        if not P.empty:
            Pa=P.sort_values("_dt",ascending=True)
            fig1.add_trace(go.Scatter(x=Pa["日付"],y=Pa["終値"],name="株価",
                line=dict(color="#e3b341",width=1.5,dash="dot"),
                hovertemplate="%{x}<br>¥%{y:,.1f}<extra></extra>"),row=1,col=1,secondary_y=True)
            fig1.update_yaxes(title_text="株価",secondary_y=True,gridcolor="#21262d",
                tickfont=dict(color="#e3b341",size=9),tickformat=",",row=1,col=1)
        flow=La["買い新規"].fillna(0)-La["売り新規"].fillna(0)
        fig1.add_trace(go.Bar(x=La["申込日"],y=flow,name="資金フロー",
            marker_color=["#388bfd" if v>=0 else "#f85149" for v in flow],opacity=0.85,
            hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>"),row=2,col=1)
        fig1.add_hline(y=0,line_dash="solid",line_color="#484f58",line_width=1,row=2,col=1)
        fig_base(fig1,400); st.plotly_chart(fig1,use_container_width=True)

        LCOLS=["申込日","買い残高","買い残高(信用%)","買い増減","買い新規","買い返済",
               "売り残高","売り残高(信用%)","売り増減","売り新規","売り返済","貸借倍率","逆日歩"]
        LNUM=["買い残高","買い増減","買い新規","買い返済","売り残高","売り増減","売り新規","売り返済"]
        Ld=L.sort_values("_dt",ascending=False).reset_index(drop=True)
        Ld["差引残高"]=Ld["買い残高"].fillna(0)-Ld["売り残高"].fillna(0)
        dp=pd.DataFrame(); dp["申込日"]=Ld["申込日"]
        for c in LNUM: dp[c]=Ld[c].apply(fmt) if c in Ld.columns else "-"
        dp["買い残高(信用%)"]=Ld["買い残高"].apply(
            lambda v:f"{float(v)/lmb*100:.1f}%" if pd.notna(v) and lmb and lmb>0 else "-")
        dp["売り残高(信用%)"]=Ld["売り残高"].apply(
            lambda v:f"{float(v)/lms*100:.1f}%" if pd.notna(v) and lms and lms>0 else "-")
        dp["貸借倍率"]=Ld["貸借倍率"].apply(
            lambda v:"-" if pd.isna(v) else "∞" if abs(v)==float("inf") else f"{v:.2f}倍")
        dp["逆日歩"]=Ld["逆日歩"].apply(lambda v:f"{v:.2f}" if pd.notna(v) and v>0 else "-")
        av={c:"" for c in LCOLS}; av["申込日"]="【平均】"
        for c in LNUM: av[c]=fmt(Ld[c].mean(skipna=True))
        av["貸借倍率"]=fmt(Ld["貸借倍率"].replace([float("inf"),float("-inf")],float("nan")).mean(skipna=True),dec=2,suffix="倍")
        av["逆日歩"]=fmt(Ld["逆日歩"].mean(skipna=True),dec=2)
        if lmb and lmb>0: av["買い残高(信用%)"]=f"{Ld['買い残高'].mean(skipna=True)/lmb*100:.1f}%"
        if lms and lms>0: av["売り残高(信用%)"]=f"{Ld['売り残高'].mean(skipna=True)/lms*100:.1f}%"
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
            use_container_width=True,hide_index=True,height=min(38*(len(disp_l)+1)+38,520))
    else:
        st.info("① 貸借データなし（投資信託・指数はスキップ）")

    # ② 週次信用残
    if not M.empty:
        st.markdown("**② 週次信用残**")
        Ma=M.sort_values("_dt",ascending=True)
        fig2=make_subplots(rows=1,cols=1)
        fig2.add_trace(go.Scatter(x=Ma["日付"],y=Ma["買い残高"],name="買い残高",
            line=dict(color="#388bfd",width=2),fill="tozeroy",fillcolor="rgba(56,139,253,0.12)",
            hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>"))
        fig2.add_trace(go.Scatter(x=Ma["日付"],y=Ma["売り残高"],name="売り残高",
            line=dict(color="#f85149",width=2),fill="tozeroy",fillcolor="rgba(248,81,73,0.12)",
            hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>"))
        fig_base(fig2,240); st.plotly_chart(fig2,use_container_width=True)

        dv_avg=P["出来高"].mean() if not P.empty else None
        MCOLS=["日付","買い残高","買い残消化日数","買い増減","売り残高","売り残消化日数",
               "売り増減","信用需給ネット","信用倍率","買い残増減率","売り残増減率","逆日歩"]
        MNUM=["買い残高","買い増減","売り残高","売り増減","信用倍率","買い残増減率","売り残増減率"]
        Md=M.sort_values("_dt",ascending=False).reset_index(drop=True)
        Md["買い残消化日数_n"]=Md["買い残高"].apply(lambda v:float(v)/dv_avg if pd.notna(v) and dv_avg else float("nan"))
        Md["売り残消化日数_n"]=Md["売り残高"].apply(lambda v:float(v)/dv_avg if pd.notna(v) and dv_avg else float("nan"))
        Md["信用需給ネット_n"]=Md["買い残消化日数_n"]-Md["売り残消化日数_n"]
        dm=pd.DataFrame(); dm["日付"]=Md["日付"]
        dm["買い残高"]=Md["買い残高"].apply(fmt)
        dm["買い残消化日数"]=Md["買い残消化日数_n"].apply(lambda v:f"{v:.1f}日" if pd.notna(v) else "-")
        dm["買い増減"]=Md["買い増減"].apply(fmt)
        dm["売り残高"]=Md["売り残高"].apply(fmt)
        dm["売り残消化日数"]=Md["売り残消化日数_n"].apply(lambda v:f"{v:.1f}日" if pd.notna(v) else "-")
        dm["売り増減"]=Md["売り増減"].apply(fmt)
        dm["信用需給ネット"]=Md["信用需給ネット_n"].apply(lambda v:f"{v:+.1f}日" if pd.notna(v) else "-")
        dm["信用倍率"]=Md["信用倍率"].apply(lambda v:f"{v:.2f}倍" if pd.notna(v) else "-")
        dm["買い残増減率"]=Md["買い残増減率"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")
        dm["売り残増減率"]=Md["売り残増減率"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")
        dm["逆日歩"]=Md["逆日歩"].apply(lambda v:f"{v:.2f}" if pd.notna(v) and v>0 else "-")
        av_m={c:"" for c in MCOLS}; av_m["日付"]="【平均】"
        for c in ["買い残高","売り残高"]: av_m[c]=fmt(Md[c].mean(skipna=True))
        av_m["信用倍率"]=fmt(Md["信用倍率"].mean(skipna=True),dec=2,suffix="倍")
        for c in ["買い残増減率","売り残増減率"]:
            v2=Md[c].mean(skipna=True); av_m[c]=f"{v2:+.2f}%" if pd.notna(v2) else "-"
        if dv_avg:
            av_m["買い残消化日数"]=f"{Md['買い残消化日数_n'].mean(skipna=True):.1f}日"
            av_m["売り残消化日数"]=f"{Md['売り残消化日数_n'].mean(skipna=True):.1f}日"
            av_m["信用需給ネット"]=f"{Md['信用需給ネット_n'].mean(skipna=True):+.1f}日"
        disp_m=pd.concat([dm[MCOLS],pd.DataFrame([av_m])],ignore_index=True)
        st_m=cell_style(disp_m,Md,"日付",MNUM,thr=3.0)
        for idx in disp_m[disp_m["日付"]!="【平均】"].index:
            dv2=disp_m.loc[idx,"日付"]
            orig=Md.loc[Md["日付"]==dv2,"信用倍率"]
            if not orig.empty and pd.notna(orig.values[0]) and orig.values[0]<1:
                st_m.loc[idx,"信用倍率"]="background-color:#3d1a1a;color:#f85149;font-weight:700"
            orig2=Md.loc[Md["日付"]==dv2,"逆日歩"]
            if not orig2.empty and pd.notna(orig2.values[0]) and orig2.values[0]>0:
                st_m.loc[idx,"逆日歩"]="background-color:#2d1f00;color:#e3b341;font-weight:700"
            for c in ["買い残増減率","売い残増減率","買い残消化日数","売り残消化日数"]:
                if c not in disp_m.columns: continue
                if c in ["買い残消化日数","売り残消化日数"]:
                    nc=c+"_n" if c+"_n" in Md.columns else c.replace("消化日数","残消化日数_n")
                    nc="買い残消化日数_n" if "買い" in c else "売り残消化日数_n"
                    bd=Md.loc[Md["日付"]==dv2,nc]
                    if not bd.empty and pd.notna(bd.values[0]):
                        bv=bd.values[0]
                        if bv>100: st_m.loc[idx,c]="background-color:#2d1f00;color:#e3b341;font-weight:700"
                        elif bv>20: st_m.loc[idx,c]="background-color:#3d1a1a;color:#f85149;font-weight:700"
                elif st_m.loc[idx,c]=="":
                    o3=Md.loc[Md["日付"]==dv2,c]
                    if not o3.empty and pd.notna(o3.values[0]):
                        st_m.loc[idx,c]=f"color:{vc(o3.values[0])}"
            nd=Md.loc[Md["日付"]==dv2,"信用需給ネット_n"]
            if not nd.empty and pd.notna(nd.values[0]) and st_m.loc[idx,"信用需給ネット"]=="":
                st_m.loc[idx,"信用需給ネット"]=f"color:{NEG if nd.values[0]>0 else POS};font-weight:600"
        st.dataframe(disp_m.style.apply(lambda _:st_m,axis=None),
            use_container_width=True,hide_index=True,height=min(38*(len(disp_m)+1)+38,480))
        if dv_avg: st.caption(f"※消化日数=残高÷日次平均出来高({dv_avg/1e6:.1f}M) 🔴20日超 🟡100日超")
    else:
        st.info("② 週次信用残データなし（投資信託・指数はスキップ）")

    # ③ 株価急変＋出来高（比較機能付き）
    st.markdown("**③ 株価急変 ＋ 出来高 ＋ 比較分析**")
    with st.expander("🔍 大口機関判定定義",expanded=False):
        st.markdown("① 出来高×2超かつ前日比±1.5%以上 ② 前日比±4%以上かつ出来高×1.5超 ③ 日中値幅が5日平均の2倍超")

    if P.empty:
        st.warning("株価データを取得できませんでした。"); return

    # 比較銘柄 検索フォーム
    with st.form(f"compare_form_{code}",clear_on_submit=True):
        cc1,cc2=st.columns([3,1])
        with cc1: cmp_inp=st.text_input("比較銘柄コード",placeholder="例：998407 / NDX / 0431720A",label_visibility="collapsed")
        with cc2: cmp_btn=st.form_submit_button("📈 比較追加",use_container_width=True)

    cmp_key=f"cmp_{code}"
    if cmp_key not in st.session_state: st.session_state[cmp_key]={}

    if cmp_btn and cmp_inp:
        cmp_code=_normalize(cmp_inp)
        with st.spinner(f"{cmp_code} 取得中…"):
            cmp_info=fetch_one(cmp_code)
        if not cmp_info["price"].empty:
            st.session_state[cmp_key][cmp_code]=cmp_info
            st.success(f"✅ {cmp_code} {cmp_info['name']} を比較に追加")
        else:
            st.error(f"❌ {cmp_code} の株価データが取得できませんでした")

    cmp_data=st.session_state.get(cmp_key,{})
    # 比較銘柄削除ボタン
    if cmp_data:
        rc=st.columns(len(cmp_data))
        for i,(cc,ci) in enumerate(list(cmp_data.items())):
            with rc[i]:
                if st.button(f"✕ {cc} {ci['name']}",key=f"rmcmp_{code}_{cc}"):
                    cmp_data.pop(cc,None); st.rerun()

    vm=P["出来高平均"].iloc[0]
    Pa=P.sort_values("_dt",ascending=True)

    # グラフ描画（比較あり: 2段、比較なし: 2段）
    fig3=make_subplots(rows=2,cols=1,shared_xaxes=True,
        row_heights=[0.65,0.35],vertical_spacing=0.04)

    # 主銘柄の終値を基準値化（比較時は基準日=最古の共通日を100とする相対値）
    all_codes=[code]+list(cmp_data.keys())
    use_relative=len(cmp_data)>0

    def normalize_series(df_p, base_val=None):
        s=df_p.set_index("日付")["終値"].sort_index()
        if use_relative and base_val is not None:
            return (s/base_val*100).round(4)
        return s

    # 主銘柄の基準値（最古日の終値）
    base_val=Pa["終値"].iloc[0] if not Pa.empty else None
    y_main=normalize_series(Pa,base_val) if use_relative else Pa.set_index("日付")["終値"].sort_index()

    mc2=["#f85149" if a else col_hex for a in Pa["機関異常"]]
    ms2=[10 if a else 5 for a in Pa["機関異常"]]
    sym=["star" if a else "circle" for a in Pa["機関異常"]]
    y_label="相対値（基準=100）" if use_relative else "終値"
    fig3.add_trace(go.Scatter(x=y_main.index,y=y_main.values,
        mode="lines+markers",name=f"{code} {info['name']}",
        line=dict(color=col_hex,width=2),
        marker=dict(size=ms2,color=mc2,symbol=sym,line=dict(width=1.5,color="rgba(248,81,73,0.4)")),
        hovertemplate=f"%{{x}}<br>{y_label}:%{{y:,.2f}}<extra></extra>"),row=1,col=1)

    # 異常値アノテーション
    for _,r in Pa[Pa["機関異常"]].iterrows():
        chg=r.get("前日比%",float("nan"))
        fig3.add_annotation(x=r["日付"],y=y_main.get(r["日付"],r["終値"]),
            text=f"<b>{chg:+.1f}%</b>" if pd.notna(chg) else "<b>⚠</b>",
            showarrow=True,arrowhead=2,arrowcolor="#f85149",
            font=dict(color="#f85149",size=10),bgcolor="#0d1117",bordercolor="#f85149")

    # 比較銘柄を重ねる
    for ci_idx,(cc,ci) in enumerate(cmp_data.items()):
        cp=ci["price"].sort_values("_dt",ascending=True)
        cbase=cp["終値"].iloc[0] if not cp.empty else None
        yc=normalize_series(cp,cbase) if use_relative else cp.set_index("日付")["終値"].sort_index()
        cc_color=COLORS[(ci_idx+1)%len(COLORS)]
        fig3.add_trace(go.Scatter(x=yc.index,y=yc.values,
            mode="lines",name=f"{cc} {ci['name']}",
            line=dict(color=cc_color,width=1.5,dash="dash"),
            hovertemplate=f"%{{x}}<br>{y_label}:%{{y:,.2f}}<extra></extra>"),row=1,col=1)

    # 出来高バー
    bc3=["#f85149" if r["機関異常"] else "#e3b341" if r["出来高異常"] else col_hex
         for _,r in Pa.iterrows()]
    fig3.add_trace(go.Bar(x=Pa["日付"],y=Pa["出来高"],name="出来高",
        marker_color=bc3,opacity=0.85,
        hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>"),row=2,col=1)
    fig3.add_hline(y=vm,line_dash="dot",line_color="#8b949e",
        annotation_text=f"月平均 {vm/1e6:.1f}M",annotation_font_color="#8b949e",row=2,col=1)
    fig3.add_hline(y=vm*2,line_dash="dash",line_color="#e3b341",
        annotation_text="×2",annotation_font_color="#e3b341",row=2,col=1)
    if use_relative:
        fig3.update_yaxes(title_text="相対値(基準=100)",row=1,col=1)
    fig_base(fig3,440); st.plotly_chart(fig3,use_container_width=True)

    # 株価テーブル（主銘柄）
    cols_p=["始値","高値","安値","終値","出来高","前日比%","25日乖離率","PER","PBR","基準価額"]
    raw_p={c:P[c].copy() for c in cols_p}
    pt=pd.DataFrame(); pt["日付"]=P["日付"]
    pt["始値"]=raw_p["始値"].apply(lambda v:f"¥{v:,.1f}" if pd.notna(v) else "-")
    pt["高値"]=raw_p["高値"].apply(lambda v:f"¥{v:,.1f}" if pd.notna(v) else "-")
    pt["安値"]=raw_p["安値"].apply(lambda v:f"¥{v:,.1f}" if pd.notna(v) else "-")
    pt["終値"]=raw_p["終値"].apply(lambda v:f"¥{v:,.1f}" if pd.notna(v) else "-")
    pt["基準価額"]=raw_p["基準価額"].apply(lambda v:f"¥{v:,.1f}" if pd.notna(v) else "-")
    pt["前日比%"]=raw_p["前日比%"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")
    pt["25日乖離率"]=raw_p["25日乖離率"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")
    pt["PER"]=raw_p["PER"].apply(lambda v:f"{v:.2f}倍" if pd.notna(v) else "-")
    pt["PBR"]=raw_p["PBR"].apply(lambda v:f"{v:.2f}倍" if pd.notna(v) else "-")
    pt["株価判定"]=P["機関異常"].map({True:"🔴 機関",False:"✅ 通常"})
    pt["出来高"]=raw_p["出来高"].apply(lambda v:f"{int(v):,}" if pd.notna(v) else "-")
    pt["出来高判定"]=P["出来高異常"].map({True:"🟠 急増",False:"✅ 通常"})
    sc=["日付","始値","高値","安値","終値","基準価額","前日比%","25日乖離率","PER","PBR","株価判定","出来高","出来高判定"]
    def safe_avg(col): m=raw_p[col].mean(skipna=True); return f"¥{m:,.1f}" if pd.notna(m) else "-"
    ar={"日付":"【平均】","始値":safe_avg("始値"),"高値":safe_avg("高値"),
        "安値":safe_avg("安値"),"終値":safe_avg("終値"),"基準価額":safe_avg("基準価額"),
        "前日比%":f"{raw_p['前日比%'].mean(skipna=True):+.2f}%" if pd.notna(raw_p['前日比%'].mean(skipna=True)) else "-",
        "25日乖離率":f"{raw_p['25日乖離率'].mean(skipna=True):+.2f}%" if pd.notna(raw_p['25日乖離率'].mean(skipna=True)) else "-",
        "PER":f"{raw_p['PER'].mean(skipna=True):.2f}倍" if pd.notna(raw_p['PER'].mean(skipna=True)) else "-",
        "PBR":f"{raw_p['PBR'].mean(skipna=True):.2f}倍" if pd.notna(raw_p['PBR'].mean(skipna=True)) else "-",
        "株価判定":"","出来高":f"{int(raw_p['出来高'].mean()):,}" if pd.notna(raw_p['出来高'].mean()) else "-",
        "出来高判定":f"月平均 {vm/1e6:.1f}M"}
    pt_show=pd.concat([pt[sc],pd.DataFrame([ar])],ignore_index=True)
    def sty_p(row):
        if row["日付"]=="【平均】": return ["background-color:#1c2951;font-weight:700;color:#e3b341"]*len(row)
        if row.get("株価判定")=="🔴 機関": return ["background-color:#2d1014;color:#ffa198"]*len(row)
        if row.get("出来高判定")=="🟠 急増": return ["background-color:#2d1f00;color:#e3b341"]*len(row)
        styles=[""]*len(row); cl=list(row.index)
        for cn,rc in [("前日比%","前日比%"),("25日乖離率","25日乖離率")]:
            if cn in cl:
                i=cl.index(cn); orig=P.loc[P["日付"]==row["日付"],rc]
                if not orig.empty and pd.notna(orig.values[0]):
                    styles[i]=f"color:{vc(orig.values[0])};font-weight:600"
        return styles
    st.dataframe(pt_show.style.apply(sty_p,axis=1),use_container_width=True,hide_index=True)

    # 比較テーブル（乖離率）
    if cmp_data:
        st.markdown("**📊 比較テーブル（乖離率）**")
        st.caption("各銘柄の最古共通日を100とした相対値。乖離率＝比較銘柄の相対値－主銘柄の相対値")
        # 全銘柄の価格を日付インデックスで合わせる
        main_s=Pa.set_index("日付")["終値"]
        main_base=main_s.iloc[0]
        cmp_rows=[]
        for cc,ci in cmp_data.items():
            cp=ci["price"].sort_values("_dt",ascending=True).set_index("日付")["終値"]
            cp_base=cp.iloc[0] if not cp.empty else None
            if cp_base is None: continue
            # 共通日付
            common=main_s.index.intersection(cp.index)
            if common.empty: continue
            for d in sorted(common,reverse=True):
                main_rel=main_s[d]/main_base*100
                cmp_rel =cp[d]/cp_base*100
                diff    =cmp_rel-main_rel
                cmp_rows.append({
                    "日付":d,
                    f"{code}(主)相対値":f"{main_rel:.2f}",
                    f"{cc}({ci['name']})相対値":f"{cmp_rel:.2f}",
                    "乖離率":f"{diff:+.2f}",
                })
        if cmp_rows:
            df_cmp=pd.DataFrame(cmp_rows)
            def sty_cmp(row):
                styles=[""]*len(row)
                if "乖離率" in row.index:
                    i=list(row.index).index("乖離率")
                    try:
                        v=float(row["乖離率"])
                        styles[i]=f"color:{POS if v>=0 else NEG};font-weight:700"
                    except: pass
                return styles
            st.dataframe(df_cmp.style.apply(sty_cmp,axis=1),use_container_width=True,hide_index=True)


# ════════════════════════════════════════
# メイン：タブ描画
# ════════════════════════════════════════
codes=list(wl.keys())
labels=[f"{c} {wl[c].get('name','')}" for c in codes]
tabs=st.tabs(labels)
for tab,code in zip(tabs,codes):
    col_hex=COLORS[codes.index(code)%len(COLORS)]
    info=st.session_state.stock_data.get(code)
    with tab:
        if not info: st.warning(f"{code} のデータがありません。")
        else: render_stock(code,info,col_hex)

st.markdown("<p style='text-align:center;font-size:10px;color:#484f58;margin-top:8px'>"
    "出典：IRバンク / 日証金 / Yahoo Finance Japan / kabutan.jp ｜ 投資勧誘を目的としません</p>",
    unsafe_allow_html=True)
