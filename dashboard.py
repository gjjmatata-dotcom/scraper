"""
dashboard.py  v4
変更点:
  - Supabase クラウドキャッシュ対応（SUPABASE_URL/KEY 環境変数で自動切替）
  - AI要約機能追加（ANTHROPIC_API_KEY 環境変数で有効化）
    要約対象: 買い/売り圧力判定・信用残推移・最新テクニカル指標・株価動向
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scraper import (fetch_one, fetch_price_by_url, fetch_price_long,
                     calc_technicals, STOCKS, _safe_int_fmt)
import socket, re, json, os, requests as _requests
from pathlib import Path
from datetime import datetime, timedelta

# ── 永続化 ────────────────────────────────────────────
PERSIST_FILE = Path("persist_state.json")

def load_persist() -> dict:
    if PERSIST_FILE.exists():
        try: return json.loads(PERSIST_FILE.read_text(encoding="utf-8"))
        except: pass
    return {"history":{}, "watch_codes":[], "watch_names":{}}

def save_persist(history, watch_codes, watch_names):
    try:
        PERSIST_FILE.write_text(
            json.dumps({"history":history,"watch_codes":watch_codes,
                        "watch_names":watch_names},ensure_ascii=False,indent=2),
            encoding="utf-8")
    except Exception as e:
        print(f"[永続化保存エラー] {e}")

# ── ページ設定 ────────────────────────────────────────
st.set_page_config(page_title="株式貸借分析", page_icon="📊", layout="wide")
st.markdown("""<style>
[data-testid="stAppViewContainer"],[data-testid="stHeader"],
section[data-testid="stMain"]{background:#0d1117!important}
[data-testid="stSidebar"]{background:#161b22!important}
html,body,[class*="css"]{color:#c9d1d9!important}
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

COLORS   = ["#388bfd","#f78166","#3fb950","#bc8cff","#e3b341","#58a6ff","#ff7b72","#79c0ff"]
PR_COLORS= {"🔴 売り圧力優勢":"#f85149","🟢 買い圧力優勢":"#3fb950",
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
        return (f"{int(round(f)):,}" if dec==0 else f"{f:,.{dec}f}")+suffix
    except: return "-"

# ── セッション初期化 ──────────────────────────────────
_persist = load_persist()
for k,v in [
    ("watch_list",{c:{"name":_persist["watch_names"].get(c,c)}
                   for c in _persist.get("watch_codes",[])}),
    ("stock_data",{}),
    ("search_history",_persist.get("history",{})),
]:
    if k not in st.session_state: st.session_state[k]=v

# ── サイドバー ────────────────────────────────────────
try: ip=socket.gethostbyname(socket.gethostname())
except: ip="取得失敗"
st.sidebar.markdown("### 📱 LAN内アクセス")
st.sidebar.code(f"http://{ip}:8501")
st.sidebar.caption("`--server.address 0.0.0.0` で起動")

st.sidebar.markdown("---")
st.sidebar.markdown("### ℹ️ 長期データについて")
st.sidebar.caption(
    "株価は yfinance（日本株・米国株・指数）と "
    "Yahoo Finance スクレイピング（投資信託）で自動取得します。"
    "DB・Playwrightは不要です。")

st.markdown(
    "<h2 style='text-align:center;color:#f0f6fc;font-size:20px;margin:4px 0'>"
    "📊 株式貸借・株価分析ダッシュボード v3</h2>",
    unsafe_allow_html=True)
st.markdown(
    "<p style='text-align:center;color:#8b949e;font-size:11px;margin:0 0 10px'>"
    "出典：IRバンク / 日証金 / Yahoo Finance Japan / kabutan.jp</p>",
    unsafe_allow_html=True)

# ── 検索フォーム ──────────────────────────────────────
st.markdown("#### 🔍 銘柄検索")
with st.form("search_form", clear_on_submit=True):
    c1,c2,c3=st.columns([3,1,1])
    with c1: inp=st.text_input("銘柄コード",placeholder="例：7203 / 998405.T / 9I31115A / NDX",
                                label_visibility="collapsed")
    with c2: add_btn =st.form_submit_button("➕ 追加",  use_container_width=True)
    with c3: only_btn=st.form_submit_button("🔄 単独表示",use_container_width=True)

def _normalize(code):
    c=code.strip().upper().replace(" ","")
    return c.zfill(4) if re.fullmatch(r"\d{1,4}",c) else c

def _do_fetch(code):
    info=fetch_one(code); name=info["name"] or code
    st.session_state.stock_data[code]=info
    h=st.session_state.search_history
    h.pop(code,None)
    st.session_state.search_history=dict(list({code:name,**h}.items())[:10])
    wl=st.session_state.watch_list
    save_persist(st.session_state.search_history,list(wl.keys()),
                 {c:wl[c].get("name",c) for c in wl})
    return info,name

if (add_btn or only_btn) and inp:
    code=_normalize(inp)
    with st.spinner(f"{code} 取得中…"):
        info,name=_do_fetch(code)
    if info["lending"].empty and info["price"].empty and info.get("price_long",pd.DataFrame()).empty:
        st.error(f"❌ {code} のデータを取得できませんでした。")
    else:
        if only_btn: st.session_state.watch_list={code:{"name":name}}
        else:        st.session_state.watch_list[code]={"name":name}
        st.success(f"✅ {code} {name}")

hist=st.session_state.search_history
if hist:
    st.caption("🕐 検索履歴（クリックで追加）")
    hcols=st.columns(min(len(hist),5))
    for j,(hc,hn) in enumerate(hist.items()):
        with hcols[j%5]:
            if st.button(f"＋{hc} {hn}",key=f"h_{hc}"):
                if hc not in st.session_state.stock_data:
                    with st.spinner(f"{hc}取得中…"): _do_fetch(hc)
                st.session_state.watch_list[hc]={"name":hn}; st.rerun()

st.caption("📋 デフォルト銘柄")
dcols=st.columns(len(STOCKS)+1)
for i,(dc,di) in enumerate(STOCKS.items()):
    with dcols[i]:
        if st.button(f"＋{dc} {di['name']}",key=f"def_{dc}"):
            if dc not in st.session_state.stock_data:
                with st.spinner(f"{dc}取得中…"): _do_fetch(dc)
            st.session_state.watch_list[dc]={"name":di["name"]}; st.rerun()
with dcols[len(STOCKS)]:
    if st.button("🔄 全更新"):
        st.session_state.stock_data={}; st.rerun()

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
    st.info("銘柄を追加してください。"); st.stop()

for code in list(wl.keys()):
    if code not in st.session_state.stock_data:
        with st.spinner(f"{code} 取得中…"): _do_fetch(code)

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
    b=ms["買い残高"].dropna(); s=ms["売い残高"].dropna() if "売い残高" in ms.columns else ms["売り残高"].dropna()
    return (b.iloc[0] if not b.empty else None),(s.iloc[0] if not s.empty else None)

PERIOD_OPTIONS={"1ヶ月":30,"3ヶ月":90,"6ヶ月":180,"1年":365,"2年":730,"3年":1095,"5年":1825,"全期間":0}

TECH_OPTIONS=[
    "移動平均(MA5/25/75/200)",
    "ボリンジャーバンド",
    "パラボリック(SAR)",
    "RSI",
    "スロー・ストキャス",
    "ファスト・ストキャス",
    "MACD",
    "DMI/ADX",
    "モメンタム",
    "ROC",
]
# チャートのみに使う指標（テーブルには出さない）
CHART_ONLY = {"移動平均(MA5/25/75/200)","ボリンジャーバンド","パラボリック(SAR)"}

# 指標→テーブルカラムのマッピング
TECH_TABLE_COLS = {
    "RSI":              ["RSI"],
    "スロー・ストキャス":["SlowK","SlowD"],
    "ファスト・ストキャス":["FastK","FastD"],
    "MACD":             ["MACD","MACD_signal","MACD_hist"],
    "DMI/ADX":          ["DI_plus","DI_minus","ADX"],
    "モメンタム":        ["Momentum"],
    "ROC":              ["ROC"],
}

# ════════════════════════════════════════
# AI 分析（Groq 優先 / Claude フォールバック）
# ════════════════════════════════════════
# 環境変数:
#   GROQ_API_KEY      : https://console.groq.com で無料取得
#   ANTHROPIC_API_KEY : https://console.anthropic.com （どちらか一方で可）

GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_AI_AVAILABLE     = bool(GROQ_API_KEY or ANTHROPIC_API_KEY)

def _call_ai(prompt: str, max_tokens: int = 2000) -> str:
    """
    Groq優先でAI APIを呼び出す。なければClaudeにフォールバック。
    json= パラメータではなく data= に手動UTF-8エンコードを使うことで
    'latin-1 codec can't encode' エラーを回避。
    """
    import json as _json

    if GROQ_API_KEY:
        try:
            payload = _json.dumps({
                "model": "llama-3.3-70b-versatile",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]
            }, ensure_ascii=False).encode("utf-8")
            r = _requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                data=payload,
                timeout=60)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            print(f"[Groq] {r.status_code}: {r.text[:150]}")
        except Exception as e:
            print(f"[Groq例外] {e}")

    if ANTHROPIC_API_KEY:
        try:
            payload = _json.dumps({
                "model": "claude-sonnet-4-6",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]
            }, ensure_ascii=False).encode("utf-8")
            r = _requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json; charset=utf-8",
                },
                data=payload,
                timeout=60)
            if r.status_code == 200:
                return r.json()["content"][0]["text"]
            print(f"[Claude] {r.status_code}: {r.text[:150]}")
        except Exception as e:
            print(f"[Claude例外] {e}")

    return "AIへの接続に失敗しました。APIキーと通信環境を確認してください。"


def _build_analysis_prompt(code: str, name: str, df_all: pd.DataFrame,
                            M: pd.DataFrame, period_label: str) -> str:
    if df_all.empty:
        return "データが不足しているため分析できません。"

    def sc(col):
        return df_all[col] if col in df_all.columns else pd.Series(dtype=float)

    top5   = df_all.nlargest(5,  "前日比%")[["日付","終値","前日比%","25日乖離率"]].dropna(subset=["前日比%"])
    worst5 = df_all.nsmallest(5, "前日比%")[["日付","終値","前日比%","25日乖離率"]].dropna(subset=["前日比%"])

    df_s = df_all.sort_values("_dt").reset_index(drop=True)
    df_s["next1"] = df_s["前日比%"].shift(-1)
    df_s["next2"] = df_s["前日比%"].shift(-2)
    df_s["next5"] = df_s["終値"].pct_change(5).shift(-5) * 100

    dev = sc("25日乖離率").dropna()
    dev_mean = dev.mean() if not dev.empty else float("nan")
    dev_min  = dev.min()  if not dev.empty else float("nan")
    dev_max  = dev.max()  if not dev.empty else float("nan")
    dev_lt   = int((dev < -10).sum())
    dev_gt   = int((dev > 10).sum())

    has_ohlc = all(c in df_all.columns for c in ["始値","高値","安値","終値"])
    candle_info = "OHLC列なし"
    if has_ohlc:
        o = df_s["始値"]; h = df_s["高値"]; l = df_s["安値"]; c2 = df_s["終値"]
        body   = (c2 - o).abs()
        range_ = h - l
        lw     = pd.concat([o,c2],axis=1).min(axis=1) - l
        bull   = (c2>o) & (body>range_*0.6)
        bear   = (c2<o) & (body>range_*0.6)
        lwlong = lw > body*1.5
        cross  = body < range_*0.1
        pats = []
        for lbl, mask in [("大陽線",bull),("大陰線",bear),("下ヒゲ長",lwlong),("十字線",cross)]:
            n = int(mask.sum())
            if n < 3: continue
            vals = df_s["next1"][mask].dropna()
            if len(vals) < 3: continue
            up_r = float((vals>0).mean()*100)
            pats.append(f"{lbl}(N={n}): 翌日上昇率{up_r:.0f}% 平均{float(vals.mean()):+.2f}%")
        candle_info = "\n".join(pats) if pats else "件数不足"

    cur_up=0; cur_dn=0; max_up=0; max_dn=0; up3=0; dn3=0
    for v in df_s["前日比%"].dropna():
        if v>0: cur_up+=1; cur_dn=0
        else:   cur_dn+=1; cur_up=0
        if cur_up>=3: up3+=1
        if cur_dn>=3: dn3+=1
        max_up=max(max_up,cur_up); max_dn=max(max_dn,cur_dn)

    margin_info = "信用残データなし"
    if not M.empty and "信用倍率" in M.columns:
        mg = M["信用倍率"].dropna()
        if not mg.empty:
            margin_info = f"平均{float(mg.mean()):.2f}倍 最小{float(mg.min()):.2f}倍 最大{float(mg.max()):.2f}倍"

    def stat_block(mask):
        cnt = int(mask.sum())
        if cnt < 3: return f"件数{cnt}件(不足)"
        vals = df_s["next1"][mask].dropna()
        if len(vals) < 3: return "翌日データ不足"
        return f"件数{cnt}件 翌日平均{float(vals.mean()):+.2f}% 上昇率{float((vals>0).mean()*100):.0f}%"

    def dev_stat(cond):
        mask = cond
        if not isinstance(mask, pd.Series): return ""
        cnt = int(mask.sum())
        if cnt < 3: return ""
        vals = df_s["next5"][mask].dropna()
        return f"(N={cnt}) 翌5日平均{float(vals.mean()):+.2f}%" if not vals.empty else ""

    n   = len(df_s)
    p0  = df_s["日付"].iloc[0]  if n>0 else "-"
    p1  = df_s["日付"].iloc[-1] if n>0 else "-"

    prompt = f"""あなたは株式テクニカル分析の専門家です。
「{code} {name}」の{period_label}分（{p0}〜{p1}、全{n}営業日）の実データ統計から
再現性60%以上の法則・売買ルール候補を日本語で報告してください。

## 指示
- 指標は各項目で最大3つに絞る
- 確率は実データから計算した数値を使う（推測禁止）
- データが少ない場合は「データ不足」と記載し過剰な結論を避ける
- 投資推奨ではなく法則の整理として記述

## 実データ統計

【前日比トップ5（急騰日）】
{top5.to_string(index=False)}

【前日比ワースト5（急落日）】
{worst5.to_string(index=False)}

【急騰翌日（前日比+3%超）】{stat_block(df_s["前日比%"]>3)}
【急落翌日（前日比-3%以下）】{stat_block(df_s["前日比%"]<-3)}

【25日乖離率】平均{dev_mean:+.2f}% 下限{dev_min:+.2f}% 上限{dev_max:+.2f}%
乖離-10%以下: {dev_lt}日 {dev_stat(sc("25日乖離率")<-10)}
乖離+10%以上: {dev_gt}日 {dev_stat(sc("25日乖離率")>10)}

【ローソク足パターン翌日統計】
{candle_info}

【連騰・連落】最大連騰{max_up}日 最大連落{max_dn}日 3日連騰{up3}回 3日連落{dn3}回

【信用倍率】{margin_info}

## 出力形式

**① 急騰・急落パターン（前日比%）**
トップ5・ワースト5の前後リターンと信用倍率の関係。再現性の高い法則を最大3指標で。

**② 25日乖離率の法則**
下限・上限それぞれの後のリターン。信用倍率との関連も言及。

**③ ローソク足パターン**
60%以上の再現性があるパターンのみ記述。

**④ 連騰・連落条件**
3連騰/3連落が起きやすい条件を最大3指標で。

**⑤ 総合売買ルール候補（再現性最高）**
買いシグナル: [指標A]●● かつ [指標B]●● → 確率●%で●日後に●%上昇 (N=●)
売りシグナル: 同様に記述
"""
    return prompt


def render_ai_summary(code: str, info: dict, period_label: str = "全期間"):
    """AI分析UIを描画する。period_labelに応じてデータを絞り込んで分析する。"""
    name = info.get("name", code)

    with st.expander("🤖 AI売買ルール分析（Groq / Claude）", expanded=False):
        if not _AI_AVAILABLE:
            st.info("""**AIを有効にする方法（どちらか一方でOK・両方とも無料）**

**① Groq（推奨・完全無料・高速）**
1. https://console.groq.com でアカウント作成
2. API Keys → Create API Key
3. Renderの環境変数: `GROQ_API_KEY` = `gsk_...`

**② Claude（Anthropic）**
1. https://console.anthropic.com でアカウント作成
2. API Keys → Create Key
3. Renderの環境変数: `ANTHROPIC_API_KEY` = `sk-ant-...`

**ローカルPCの場合:**
コマンドプロンプトで実行前に
`set GROQ_API_KEY=gsk_...` を実行してから
`python -m streamlit run dashboard.py`""")
            return

        # ── 表示期間でキーを分ける（期間変更→ボタン再押しで再分析） ──
        result_key = f"ai_result_{code}_{period_label}"
        btn_key    = f"ai_btn_{code}_{period_label}"

        st.caption(f"📊 **{period_label}** のデータから統計的法則を探索します（再現性60%以上の売買ルール候補）")
        run = st.button("🔍 分析実行", key=btn_key)

        if run:
            pl = info.get("price_long", pd.DataFrame())
            M  = info.get("margin",     pd.DataFrame())
            if pl.empty:
                st.warning("長期データが不足しています。データ蓄積後に再実行してください。")
                return

            with st.spinner(f"【{period_label}】統計計算中 → AI分析中（10〜30秒）..."):
                from scraper import calc_technicals
                from datetime import datetime, timedelta

                # ── 表示期間でフィルタ ──
                period_days = PERIOD_OPTIONS.get(period_label, 0)
                pl_sorted = pl.sort_values("_dt", ascending=True).reset_index(drop=True)
                if period_days > 0:
                    cutoff = datetime.today() - timedelta(days=period_days)
                    pl_filtered = pl_sorted[pl_sorted["_dt"] >= cutoff].reset_index(drop=True)
                else:
                    pl_filtered = pl_sorted

                if pl_filtered.empty or len(pl_filtered) < 10:
                    st.warning(f"【{period_label}】の期間にデータが少なすぎます（{len(pl_filtered)}件）。期間を広げてください。")
                    return

                # 信用残も同期間に絞る
                M_filtered = pd.DataFrame()
                if not M.empty and "_dt" in M.columns and period_days > 0:
                    cutoff = datetime.today() - timedelta(days=period_days)
                    M_filtered = M[M["_dt"] >= cutoff].reset_index(drop=True)
                elif not M.empty:
                    M_filtered = M

                df_tech = calc_technicals(pl_filtered)
                prompt  = _build_analysis_prompt(code, name, df_tech, M_filtered, period_label)
                result  = _call_ai(prompt, max_tokens=2000)
                st.session_state[result_key] = result

        if result_key in st.session_state:
            st.markdown(st.session_state[result_key])
            provider = "Groq (LLaMA-3.3-70b)" if GROQ_API_KEY else "Claude (claude-sonnet-4-6)"
            st.caption(f"※ {provider} による統計分析（対象期間: {period_label}）。投資勧誘ではありません。")
            st.download_button(
                "💾 分析結果をダウンロード",
                data=st.session_state[result_key].encode("utf-8"),
                file_name=f"ai_analysis_{code}_{period_label}_{pd.Timestamp.now().strftime('%Y%m%d')}.txt",
                mime="text/plain",
                key=f"dl_{code}")


# ════════════════════════════════════════
# ③ テクニカル分析セクション
# ════════════════════════════════════════
def render_technical_section(code, info, col_hex, period_days):
    st.markdown("**③ 株価急変 ＋ テクニカル分析 ＋ 信用残連動 ＋ 比較分析**")

    with st.expander("🔍 大口機関判定定義", expanded=False):
        st.markdown(
            "① 出来高×**1.5**超 かつ 前日比±**1.5**%以上  \n"
            "② 前日比±**4**%以上 かつ 出来高×**1.2**超")

    # ── 長期データ取得 ─────────────────────────
    p_long=info.get("price_long",pd.DataFrame())
    if p_long.empty: p_long=info.get("price",pd.DataFrame()).copy()
    if p_long.empty: st.warning("株価データを取得できませんでした。"); return

    p_all=p_long.sort_values("_dt",ascending=True).reset_index(drop=True)
    p_all=calc_technicals(p_all)

    # 期間フィルタ
    if period_days>0:
        cutoff=datetime.today()-timedelta(days=period_days)
        Pa=p_all[p_all["_dt"]>=cutoff].reset_index(drop=True)
    else:
        Pa=p_all.copy()
    if Pa.empty: st.warning("選択期間にデータがありません。"); return

    # ── 指標選択チェックボックス ───────────────
    sel_key=f"tech_sel_{code}"
    if sel_key not in st.session_state:
        st.session_state[sel_key]=["移動平均(MA5/25/75/200)","ボリンジャーバンド","RSI","MACD"]

    st.markdown("**📐 表示する指標を選択（チャート・テーブル共通）**")
    sel_cols=st.columns(5); selected=[]
    for i,opt in enumerate(TECH_OPTIONS):
        with sel_cols[i%5]:
            if st.checkbox(opt,value=(opt in st.session_state[sel_key]),key=f"chk_{code}_{i}"):
                selected.append(opt)
    st.session_state[sel_key]=selected

    # ── 出来高平均 ──────────────────────────────
    vm=Pa["出来高"].fillna(0).mean() if "出来高" in Pa.columns else 0

    # ── 比較銘柄管理 ────────────────────────────
    cmp_key=f"cmp_{code}"
    if cmp_key not in st.session_state: st.session_state[cmp_key]={}
    cmp_data=st.session_state[cmp_key]

    PRESET=[
        ("998407.O","日経平均","https://finance.yahoo.co.jp/quote/998407.O/history"),
        ("998405.T","TOPIX",   "https://finance.yahoo.co.jp/quote/998405.T/history"),
        ("NDX",     "NASDAQ100","https://us.kabutan.jp/indexes/%5ENDX/historical_prices/daily"),
        ("SOX",     "SOX",     "https://us.kabutan.jp/indexes/%5ESOX/historical_prices/daily"),
        ("IXIC",    "NASDAQ",  "https://us.kabutan.jp/indexes/%5EIXIC/historical_prices/daily"),
        ("DJI",     "NYダウ",  "https://us.kabutan.jp/indexes/%5EDJI/historical_prices/daily"),
        ("03311187","S&P500(eMAXIS)","https://finance.yahoo.co.jp/quote/03311187/history"),
        ("0331418A","全世界株",      "https://finance.yahoo.co.jp/quote/0331418A/history"),
    ]
    st.caption("📌 クイック比較追加")
    btn_cols=st.columns(4)
    for bi,(pcode,pname,purl) in enumerate(PRESET):
        with btn_cols[bi%4]:
            if st.button(f"＋{pname}",key=f"preset_{code}_{pcode}"):
                if pcode not in cmp_data:
                    with st.spinner(f"{pname}取得中…"):
                        df_tmp=fetch_price_by_url(purl,pname,days=period_days)
                    if not df_tmp.empty:
                        cmp_data[pcode]={"name":pname,"price":df_tmp}
                    else: st.error(f"❌ {pname}")
                st.rerun()

    st.caption("🔍 その他の銘柄を比較追加")
    with st.form(f"compare_form_{code}",clear_on_submit=True):
        cc1,cc2,cc3=st.columns([2,2,1])
        with cc1: cmp_inp=st.text_input("コード",placeholder="例：9I31115A",label_visibility="collapsed")
        with cc2: cmp_url=st.text_input("URL（省略可）",label_visibility="collapsed")
        with cc3: cmp_btn=st.form_submit_button("📈 追加",use_container_width=True)
    if cmp_btn and (cmp_inp or cmp_url):
        cmp_code_raw=cmp_inp.strip().upper() if cmp_inp else ""
        with st.spinner("取得中…"):
            if cmp_url:
                df_tmp=fetch_price_by_url(cmp_url.strip(),days=period_days)
                cmp_label=cmp_inp.strip() or re.search(r"/quote/([^/]+)/",cmp_url or "")
                if hasattr(cmp_label,"group"): cmp_label=cmp_label.group(1)
            else:
                info_tmp=fetch_one(cmp_code_raw)
                df_tmp=info_tmp.get("price_long",info_tmp["price"])
                cmp_label=info_tmp["name"] or cmp_code_raw
        if not df_tmp.empty:
            key_label=cmp_url.strip() if (cmp_url and not cmp_code_raw) else cmp_code_raw
            cmp_data[key_label]={"name":str(cmp_label),"price":df_tmp}
        else: st.error("❌ データ取得失敗")

    if cmp_data:
        rc=st.columns(min(len(cmp_data),5))
        for i,(cc,ci) in enumerate(list(cmp_data.items())):
            with rc[i%5]:
                if st.button(f"✕ {ci['name']}",key=f"rmcmp_{code}_{cc}"):
                    cmp_data.pop(cc,None); st.rerun()

    use_relative=len(cmp_data)>0
    sub_inds=[s for s in selected if s not in CHART_ONLY]
    n_rows=2+len(sub_inds)
    row_heights=[0.45,0.15]+[0.10]*(n_rows-2)

    fig3=make_subplots(rows=n_rows,cols=1,shared_xaxes=True,
                       row_heights=row_heights,vertical_spacing=0.03,
                       subplot_titles=["価格","出来高"]+sub_inds,
                       specs=[[{"secondary_y":True}]]+[[{"secondary_y":False}]]*(n_rows-1))

    base_val=Pa["終値"].iloc[0] if not Pa.empty else None
    def to_rel(s,bv): return (s/bv*100).round(4) if (use_relative and bv) else s

    main_s=Pa.set_index("日付")["終値"]
    y_main=to_rel(main_s,base_val)
    mc2=["#f85149" if a else col_hex for a in Pa.get("機関異常",[False]*len(Pa))]
    ms2=[10 if a else 4               for a in Pa.get("機関異常",[False]*len(Pa))]
    sym=["star" if a else "circle"    for a in Pa.get("機関異常",[False]*len(Pa))]

    fig3.add_trace(go.Scatter(x=y_main.index,y=y_main.values,
        mode="lines+markers",name=f"{code} {info['name']}",
        line=dict(color=col_hex,width=2),
        marker=dict(size=ms2,color=mc2,symbol=sym,
            line=dict(width=1.5,color="rgba(248,81,73,0.4)")),
        hovertemplate="%{x}<br>%{y:,.2f}<extra></extra>"),row=1,col=1,secondary_y=False)

    if "移動平均(MA5/25/75/200)" in selected:
        for ma_col,ma_color,dash in [("MA5","#e3b341","dot"),("MA25","#f78166","dash"),
                                      ("MA75","#3fb950","solid"),("MA200","#bc8cff","longdash")]:
            if ma_col in Pa.columns:
                s=to_rel(Pa.set_index("日付")[ma_col],base_val)
                fig3.add_trace(go.Scatter(x=s.index,y=s.values,mode="lines",name=ma_col,
                    line=dict(color=ma_color,width=1.2,dash=dash)),row=1,col=1,secondary_y=False)

    if "ボリンジャーバンド" in selected and "BB_upper" in Pa.columns:
        bbu=to_rel(Pa.set_index("日付")["BB_upper"],base_val)
        bbl=to_rel(Pa.set_index("日付")["BB_lower"],base_val)
        fig3.add_trace(go.Scatter(x=bbu.index,y=bbu.values,mode="lines",name="BB上限",
            line=dict(color="rgba(88,166,255,0.4)",width=1),fill=None),row=1,col=1,secondary_y=False)
        fig3.add_trace(go.Scatter(x=bbl.index,y=bbl.values,mode="lines",name="BB下限",
            line=dict(color="rgba(88,166,255,0.4)",width=1),
            fill="tonexty",fillcolor="rgba(88,166,255,0.07)",showlegend=False),
            row=1,col=1,secondary_y=False)

    if "パラボリック(SAR)" in selected and "SAR" in Pa.columns:
        sar_s=to_rel(Pa.set_index("日付")["SAR"],base_val)
        sar_col=["#3fb950" if b else "#f85149" for b in Pa.get("SAR_bull",[True]*len(Pa))]
        fig3.add_trace(go.Scatter(x=Pa["日付"],y=sar_s.values,mode="markers",name="SAR",
            marker=dict(color=sar_col,size=4,symbol="diamond")),row=1,col=1,secondary_y=False)

    for idx_c,(cc,ci) in enumerate(cmp_data.items()):
        cp=ci["price"].sort_values("_dt",ascending=True)
        if cp.empty: continue
        if period_days>0:
            cutoff=datetime.today()-timedelta(days=period_days)
            cp=cp[cp["_dt"]>=cutoff]
        if cp.empty: continue
        cp_s=cp.set_index("日付")["終値"]
        y_c=to_rel(cp_s,cp_s.iloc[0])
        cc_color=COLORS[(idx_c+1)%len(COLORS)]
        fig3.add_trace(go.Scatter(x=y_c.index,y=y_c.values,mode="lines",
            name=f"{cc} {ci['name']}",line=dict(color=cc_color,width=1.5,dash="dash")),
            row=1,col=1,secondary_y=False)

    if "機関異常" in Pa.columns:
        for _,r in Pa[Pa["機関異常"]].iterrows():
            chg=r.get("前日比%",float("nan"))
            fig3.add_annotation(x=r["日付"],y=y_main.get(r["日付"],r["終値"]),
                text=f"<b>{chg:+.1f}%</b>" if pd.notna(chg) else "<b>⚠</b>",
                showarrow=True,arrowhead=2,arrowcolor="#f85149",
                font=dict(color="#f85149",size=10),bgcolor="#0d1117",bordercolor="#f85149")

    if "出来高" in Pa.columns:
        bc3=["#f85149" if r.get("機関異常",False) else "#e3b341" if r.get("出来高異常",False)
             else col_hex for _,r in Pa.iterrows()]
        fig3.add_trace(go.Bar(x=Pa["日付"],y=Pa["出来高"],name="出来高",
            marker_color=bc3,opacity=0.85,
            hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>"),row=2,col=1)
        if vm>0:
            fig3.add_hline(y=vm,line_dash="dot",line_color="#8b949e",
                annotation_text=f"平均{vm/1e6:.1f}M",annotation_font_color="#8b949e",row=2,col=1)
            fig3.add_hline(y=vm*2,line_dash="dash",line_color="#e3b341",
                annotation_text="×2",annotation_font_color="#e3b341",row=2,col=1)

    for si,ind in enumerate(sub_inds):
        row_n=3+si
        if ind=="RSI" and "RSI" in Pa.columns:
            fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["RSI"],mode="lines",name="RSI",
                line=dict(color="#bc8cff",width=1.5)),row=row_n,col=1)
            fig3.add_hline(y=70,line_dash="dash",line_color="#f85149",
                annotation_text="70",annotation_font_color="#f85149",row=row_n,col=1)
            fig3.add_hline(y=30,line_dash="dash",line_color="#3fb950",
                annotation_text="30",annotation_font_color="#3fb950",row=row_n,col=1)
            fig3.update_yaxes(range=[0,100],row=row_n,col=1)
        elif ind=="スロー・ストキャス" and "SlowK" in Pa.columns:
            fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["SlowK"],mode="lines",name="SlowK",
                line=dict(color="#388bfd",width=1.5)),row=row_n,col=1)
            fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["SlowD"],mode="lines",name="SlowD",
                line=dict(color="#f78166",width=1.5,dash="dash")),row=row_n,col=1)
            fig3.add_hline(y=80,line_dash="dash",line_color="#f85149",row=row_n,col=1)
            fig3.add_hline(y=20,line_dash="dash",line_color="#3fb950",row=row_n,col=1)
            fig3.update_yaxes(range=[0,100],row=row_n,col=1)
        elif ind=="ファスト・ストキャス" and "FastK" in Pa.columns:
            fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["FastK"],mode="lines",name="FastK",
                line=dict(color="#58a6ff",width=1.5)),row=row_n,col=1)
            fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["FastD"],mode="lines",name="FastD",
                line=dict(color="#ff7b72",width=1.5,dash="dash")),row=row_n,col=1)
            fig3.add_hline(y=80,line_dash="dash",line_color="#f85149",row=row_n,col=1)
            fig3.add_hline(y=20,line_dash="dash",line_color="#3fb950",row=row_n,col=1)
            fig3.update_yaxes(range=[0,100],row=row_n,col=1)
        elif ind=="MACD" and "MACD" in Pa.columns:
            hist_colors=["#3fb950" if v>=0 else "#f85149" for v in Pa["MACD_hist"].fillna(0)]
            fig3.add_trace(go.Bar(x=Pa["日付"],y=Pa["MACD_hist"],name="MACDヒスト",
                marker_color=hist_colors,opacity=0.7),row=row_n,col=1)
            fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["MACD"],mode="lines",name="MACD",
                line=dict(color="#388bfd",width=1.5)),row=row_n,col=1)
            fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["MACD_signal"],mode="lines",name="Signal",
                line=dict(color="#f78166",width=1.5,dash="dash")),row=row_n,col=1)
            fig3.add_hline(y=0,line_dash="solid",line_color="#484f58",row=row_n,col=1)
        elif ind=="DMI/ADX" and "DI_plus" in Pa.columns:
            fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["DI_plus"],mode="lines",name="DI+",
                line=dict(color="#3fb950",width=1.5)),row=row_n,col=1)
            fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["DI_minus"],mode="lines",name="DI-",
                line=dict(color="#f85149",width=1.5,dash="dash")),row=row_n,col=1)
            fig3.add_trace(go.Scatter(x=Pa["日付"],y=Pa["ADX"],mode="lines",name="ADX",
                line=dict(color="#e3b341",width=2)),row=row_n,col=1)
            fig3.add_hline(y=25,line_dash="dot",line_color="#8b949e",
                annotation_text="25",annotation_font_color="#8b949e",row=row_n,col=1)
        elif ind=="モメンタム" and "Momentum" in Pa.columns:
            mc2b=["#3fb950" if v>=0 else "#f85149" for v in Pa["Momentum"].fillna(0)]
            fig3.add_trace(go.Bar(x=Pa["日付"],y=Pa["Momentum"],name="モメンタム",
                marker_color=mc2b,opacity=0.8),row=row_n,col=1)
            fig3.add_hline(y=0,line_dash="solid",line_color="#484f58",row=row_n,col=1)
        elif ind=="ROC" and "ROC" in Pa.columns:
            rc2=["#3fb950" if v>=0 else "#f85149" for v in Pa["ROC"].fillna(0)]
            fig3.add_trace(go.Bar(x=Pa["日付"],y=Pa["ROC"],name="ROC(%)",
                marker_color=rc2,opacity=0.8),row=row_n,col=1)
            fig3.add_hline(y=0,line_dash="solid",line_color="#484f58",row=row_n,col=1)

    if use_relative: fig3.update_yaxes(title_text="相対値(基準=100)",row=1,col=1)
    fig_base(fig3,420+130*len(sub_inds))
    st.plotly_chart(fig3,use_container_width=True)

    # ── 株価テーブル（信用残カラム含む） ──────
    st.markdown("**📋 株価テーブル**")

    M=info.get("margin",pd.DataFrame())

    # ── 表示期間ベースで全指標を再計算 ──────────
    Pa_asc = Pa.sort_values("_dt", ascending=True).reset_index(drop=True)
    has_ohlc = all(c in Pa_asc.columns for c in ["高値","安値","始値","終値"])
    n_period = len(Pa_asc)

    # 日中幅（高値-安値）と表示期間内平均・3倍閾値
    if has_ohlc:
        range_series       = (Pa_asc["高値"] - Pa_asc["安値"]).fillna(0)
        range_mean_period  = range_series.mean()
        range_3x           = range_mean_period * 3
    else:
        range_series = pd.Series([0.0]*n_period)
        range_mean_period = 0.0; range_3x = 0.0

    # 出来高平均（表示期間）
    vol_period = Pa_asc["出来高"].fillna(0) if "出来高" in Pa_asc.columns else pd.Series([0.0]*n_period)
    vm_period  = vol_period.mean()
    ret_period = Pa_asc["前日比%"].abs().fillna(0) if "前日比%" in Pa_asc.columns else pd.Series([0.0]*n_period)

    # 機関異常・出来高異常フラグ（表示期間ベース）
    Pa_asc["機関異常_p"]  = ((vol_period > vm_period*1.5) & (ret_period >= 1.5)) | \
                            ((ret_period >= 4.0) & (vol_period > vm_period*1.2))
    Pa_asc["出来高異常_p"]= vol_period > vm_period * 2

    # BB乖離率（±1σ / ±2σ）と バンドウォーク判定
    # BB_upper=+2σ, BB_lower=-2σ は calc_technicals 済み
    if all(c in Pa_asc.columns for c in ["終値","BB_upper","BB_lower","BB_mid"]):
        c_  = Pa_asc["終値"]
        mid = Pa_asc["BB_mid"]
        sig = (Pa_asc["BB_upper"] - mid) / 2   # 1σ = (2σライン - 中心) / 2
        sig = sig.replace(0, float("nan"))
        Pa_asc["BB_1s_dev"] = ((c_ - mid) / sig).round(2)      # +1σ=1.0, -1σ=-1.0
        Pa_asc["BB_2s_dev"] = ((c_ - mid) / (sig*2)).round(2)  # +2σ=1.0, -2σ=-1.0
        # バンドウォーク: 3日連続で+2σ超 or -2σ超
        above2 = c_ > Pa_asc["BB_upper"]
        below2 = c_ < Pa_asc["BB_lower"]
        Pa_asc["BW_up"]   = above2 & above2.shift(1).fillna(False) & above2.shift(2).fillna(False)
        Pa_asc["BW_dn"]   = below2 & below2.shift(1).fillna(False) & below2.shift(2).fillna(False)
    else:
        Pa_asc["BB_1s_dev"] = float("nan")
        Pa_asc["BB_2s_dev"] = float("nan")
        Pa_asc["BW_up"]     = False
        Pa_asc["BW_dn"]     = False

    # SAR乖離率（終値とSARの差 / 終値）
    if "SAR" in Pa_asc.columns and "終値" in Pa_asc.columns:
        sar_safe = Pa_asc["SAR"].replace(0, float("nan"))
        Pa_asc["SAR_dev%"] = ((Pa_asc["終値"] - Pa_asc["SAR"]) / Pa_asc["終値"] * 100).round(2)
    else:
        Pa_asc["SAR_dev%"] = float("nan")

    # MACD_hist 前日差
    if "MACD_hist" in Pa_asc.columns:
        Pa_asc["MACD_hist_chg"] = Pa_asc["MACD_hist"].diff().round(2)
    else:
        Pa_asc["MACD_hist_chg"] = float("nan")

    # 降順に変換
    Pa_desc2 = Pa_asc.sort_values("_dt", ascending=False).reset_index(drop=True)

    # ── ローソク足記号生成（日中幅3倍超の日のみ） ──
    def _candle_symbol(row) -> str:
        if range_3x <= 0: return ""
        try:
            h=float(row.get("高値",float("nan"))); l=float(row.get("安値",float("nan")))
            o=float(row.get("始値",float("nan"))); c=float(row.get("終値",float("nan")))
        except (TypeError,ValueError): return ""
        if any(v!=v for v in [h,l,o,c]): return ""
        total=h-l
        if total<=0 or total<=range_3x: return ""
        body=abs(c-o); up_w=h-max(c,o); dn_w=min(c,o)-l
        is_bull=c>=o
        body_r=body/total; up_r=up_w/total; dn_r=dn_w/total
        if body_r>=0.7:   return "▲大陽" if is_bull else "▼大陰"
        if body_r<0.15:
            if dn_r>=0.4:  return "⊥下ヒゲ十字"
            if up_r>=0.4:  return "⊤上ヒゲ十字"
            return "＋十字"
        if dn_r>=0.4 and up_r<0.15: return "↑下ヒゲ陽" if is_bull else "↑下ヒゲ陰"
        if up_r>=0.4 and dn_r<0.15: return "↓上ヒゲ陽" if is_bull else "↓上ヒゲ陰"
        if up_r>=0.25 and dn_r>=0.25: return "↕コマ陽" if is_bull else "↕コマ陰"
        return "△陽" if is_bull else "▽陰"

    # ── テーブル構築 ─────────────────────────────
    # 選択されたテクニカル列
    tech_cols=[]
    for opt in selected:
        if opt not in CHART_ONLY:
            tech_cols+=TECH_TABLE_COLS.get(opt,[])
    tech_cols=[c for c in tech_cols if c in Pa_desc2.columns]

    pt=pd.DataFrame()
    pt["日付"]=Pa_desc2["日付"]

    # ローソク足（終値の直前に配置）
    if has_ohlc:
        pt["ローソク"]=Pa_desc2.apply(_candle_symbol,axis=1)

    # 価格列
    for c in ["始値","高値","安値","終値","基準価額"]:
        if c in Pa_desc2.columns:
            pt[c]=Pa_desc2[c].apply(lambda v:f"¥{v:,.1f}" if pd.notna(v) else "-")
    if "出来高" in Pa_desc2.columns:
        pt["出来高"]=Pa_desc2["出来高"].apply(lambda v:f"{int(v):,}" if pd.notna(v) else "-")
    if "前日比%" in Pa_desc2.columns:
        pt["前日比%"]=Pa_desc2["前日比%"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")
    if "25日乖離率" in Pa_desc2.columns:
        pt["25日乖離率"]=Pa_desc2["25日乖離率"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")

    # BB乖離率
    if "BB_1s_dev" in Pa_desc2.columns:
        pt["BB±1σ"]=Pa_desc2["BB_1s_dev"].apply(lambda v:f"{v:+.2f}σ" if pd.notna(v) else "-")
        pt["BB±2σ"]=Pa_desc2["BB_2s_dev"].apply(lambda v:f"{v:+.2f}σ" if pd.notna(v) else "-")

    # SAR乖離率
    if "SAR_dev%" in Pa_desc2.columns:
        pt["SAR乖離%"]=Pa_desc2["SAR_dev%"].apply(lambda v:f"{v:+.2f}%" if pd.notna(v) else "-")

    # 信用残カラム
    if not M.empty:
        M_d=M.sort_values("_dt").set_index("_dt")
        def _find_margin(dt,col):
            try:
                past=M_d[M_d.index<=dt]
                return past[col].iloc[-1] if not past.empty else None
            except: return None
        pt["信用倍率"]=Pa_desc2["_dt"].apply(
            lambda dt: fmt(_find_margin(dt,"信用倍率"),dec=2,suffix="倍"))
        pt["買い残増減率"]=Pa_desc2["_dt"].apply(
            lambda dt: (lambda v:f"{v:+.2f}%" if pd.notna(v) and v==v else "-")
                       (_find_margin(dt,"買い残増減率")))
        pt["売り残増減率"]=Pa_desc2["_dt"].apply(
            lambda dt: (lambda v:f"{v:+.2f}%" if pd.notna(v) and v==v else "-")
                       (_find_margin(dt,"売り残増減率")))
        def _credit_side(dt):
            b=_find_margin(dt,"買い残増減率"); s=_find_margin(dt,"売り残増減率")
            if b is None or s is None or b!=b or s!=s: return "-"
            return "🟢買い優勢" if b>s else "🔴売り優勢" if s>b else "⚪中立"
        pt["信用需給"]=Pa_desc2["_dt"].apply(_credit_side)

    # テクニカル列（MACD_histの直後にMACD_hist前日差を挿入）
    for tc in tech_cols:
        if tc not in Pa_desc2.columns: continue
        pt[tc]=Pa_desc2[tc].apply(lambda v:f"{v:.2f}" if pd.notna(v) else "-")
        if tc=="MACD_hist" and "MACD_hist_chg" in Pa_desc2.columns:
            pt["MACD_hist差"]=Pa_desc2["MACD_hist_chg"].apply(
                lambda v:f"{v:+.2f}" if pd.notna(v) else "-")

    # ── スタイリング ─────────────────────────────
    def sty_pt(row):
        cl=list(row.index); styles=[""]*len(cl)

        # ── 行単位背景（優先順位順） ──
        # バンドウォーク上抜け（+2σ超×3日連続）→ 青背景
        bw_idx = Pa_desc2.index[Pa_desc2["日付"]==row.get("日付","")] if "日付" in row else []
        if len(bw_idx)>0:
            idx0=bw_idx[0]
            if Pa_desc2.loc[idx0,"BW_up"]:
                return ["background-color:#0d2137;color:#79c0ff"]*len(cl)
            if Pa_desc2.loc[idx0,"BW_dn"]:
                return ["background-color:#2d0f1a;color:#ffa198"]*len(cl)
            # 機関異常（大口）→ 濃赤背景
            if Pa_desc2.loc[idx0,"機関異常_p"]:
                return ["background-color:#3d0d0d;color:#ffa198"]*len(cl)
            # 出来高異常 → 黄背景
            if Pa_desc2.loc[idx0,"出来高異常_p"]:
                return ["background-color:#2d1f00;color:#e3b341"]*len(cl)

        # ── 列単位の色付け ──
        for cn,suffix in [("前日比%","%"),("25日乖離率","%"),("ROC",""),
                          ("買い残増減率","%"),("売り残増減率","%"),("MACD_hist差",""),
                          ("SAR乖離%","%")]:
            if cn in cl:
                i=cl.index(cn)
                try:
                    v=float(str(row[cn]).replace("%","").replace("+","").replace("σ",""))
                    styles[i]=f"color:{vc(v)};font-weight:600"
                except: pass

        # ローソク足色（陽線=青 / 陰線=赤）
        if "ローソク" in cl:
            i=cl.index("ローソク"); sym=str(row.get("ローソク",""))
            if any(k in sym for k in ["陽","↑","コマ陽","△"]):
                styles[i]="color:#58a6ff;font-weight:700"
            elif any(k in sym for k in ["陰","↓","コマ陰","▽","⊥","⊤","＋"]):
                styles[i]="color:#f85149;font-weight:700"

        # BB乖離率（±1σ超=ハイライト / ±2σ近辺=強調）
        for bb_col in ["BB±1σ","BB±2σ"]:
            if bb_col in cl:
                i=cl.index(bb_col)
                try:
                    v=float(str(row[bb_col]).replace("σ","").replace("+",""))
                    if abs(v)>=1.8:
                        styles[i]=f"color:{'#f85149' if v>0 else '#58a6ff'};font-weight:700"
                    elif abs(v)>=0.8:
                        styles[i]=f"color:{'#e3b341' if v>0 else '#79c0ff'}"
                except: pass

        return styles

    # ── 脚注（定義まとめ）─────────────────────────
    caption_lines = [
        "**【カラーリング定義】**",
        "🔵 青行(BW上昇) = +2σ超を3日連続 → バンドウォーク上昇中（強い上昇トレンド）",
        "🔴 赤行(BW下降) = -2σ超を3日連続 → バンドウォーク下降中（強い下降トレンド）",
        "🟥 濃赤行 = 大口機関異常: ①出来高×1.5超かつ前日比±1.5%以上 または ②前日比±4%以上かつ出来高×1.2超",
        "🟨 黄行  = 出来高急増: 表示期間内の出来高平均×2倍超（需給の急変シグナル）",
        "ローソク列 🔵文字=陽線系 / 🔴文字=陰線系 ※日中幅が表示期間平均×3倍超の日のみ表示",
        f"　ローソク足閾値（現在の表示期間）: 日中幅平均 {range_mean_period:,.0f} × 3 = {range_3x:,.0f}",
        "**【指標定義】**",
        "BB±1σ: (終値-BB中心)÷1σ。±0.8以上でトレンドに乗っている目安 / ±1.8以上で強いトレンド",
        "BB±2σ: (終値-BB中心)÷2σ。1.0=+2σライン上、-1.0=-2σライン上",
        "SAR乖離%: (終値-パラボリックSAR)÷終値×100。プラス=SAR下方(上昇トレンド) / マイナス=SAR上方(下降トレンド)",
        "MACD_hist差: 当日MACDヒストグラム－前日。プラス=モメンタム増加 / マイナス=減少",
    ]
    if not M.empty:
        caption_lines += [
            "**【信用残定義】**",
            "信用需給ネット = 買い残高÷日次平均出来高(日) - 売り残高÷日次平均出来高(日)",
            "　プラス=買い残が厚い(上昇余地・需給重い両面あり) / マイナス=売り残が厚い(下落圧力・買い戻し余地)",
            "信用需給列: 買い残増減率 > 売り残増減率 → 🟢買い優勢 / 逆 → 🔴売り優勢",
        ]

    st.dataframe(pt.style.apply(sty_pt,axis=1),
        use_container_width=True,hide_index=True,
        height=min(38*(min(len(pt),60)+1)+38,620))

    with st.expander("📌 カラーリング・指標定義", expanded=False):
        for line in caption_lines:
            if line.startswith("**"):
                st.markdown(line)
            else:
                st.caption(line)

    # ── 比較テーブル ─────────────────────────────
    if cmp_data:
        st.markdown("**📊 比較テーブル**")
        main_ret=Pa.set_index("日付")["終値"].pct_change().dropna()
        for cc,ci in cmp_data.items():
            cp=ci["price"].sort_values("_dt",ascending=True)
            if cp.empty: continue
            if period_days>0:
                cutoff=datetime.today()-timedelta(days=period_days)
                cp=cp[cp["_dt"]>=cutoff]
            if cp.empty: continue
            cp_idx=cp.set_index("日付")
            st.markdown(f"##### {cc} {ci['name']}")
            cp_base=cp_idx["終値"].iloc[0]; main_base2=Pa["終値"].iloc[0]
            rows_cmp=[]
            for _,crow in cp.sort_values("_dt",ascending=False).iterrows():
                d=crow["日付"]
                main_row=Pa.loc[Pa["日付"]==d]
                main_close=main_row["終値"].values[0] if not main_row.empty else None
                main_rel=(main_close/main_base2*100) if main_close else None
                cmp_rel=(crow["終値"]/cp_base*100) if pd.notna(crow["終値"]) else None
                drift=(cmp_rel-main_rel) if (cmp_rel is not None and main_rel is not None) else None
                def fv(col): return crow.get(col,float("nan"))
                rows_cmp.append({
                    "日付":d,
                    "終値":f"¥{fv('終値'):,.1f}" if pd.notna(fv("終値")) else "-",
                    "前日比%":f"{fv('前日比%'):+.2f}%" if pd.notna(fv("前日比%")) else "-",
                    "25日乖離率":f"{fv('25日乖離率'):+.2f}%" if pd.notna(fv("25日乖離率")) else "-",
                    "始値":f"¥{fv('始値'):,.1f}" if pd.notna(fv("始値")) else "-",
                    "高値":f"¥{fv('高値'):,.1f}" if pd.notna(fv("高値")) else "-",
                    "安値":f"¥{fv('安値'):,.1f}" if pd.notna(fv("安値")) else "-",
                    "出来高":f"{int(fv('出来高')):,}" if pd.notna(fv("出来高")) else "-",
                    "相対値":f"{cmp_rel:.2f}" if cmp_rel is not None else "-",
                    f"乖離(vs {code})":f"{drift:+.2f}" if drift is not None else "-",
                })
            if not rows_cmp: continue
            df_cmp=pd.DataFrame(rows_cmp)
            cmp_ret=cp_idx["終値"].pct_change().dropna()
            common=main_ret.index.intersection(cmp_ret.index)
            if len(common)>=5:
                mr=main_ret[common]; cr=cmp_ret[common]
                beta=cr.cov(mr)/mr.var() if mr.var()>0 else float("nan")
                corr=cr.corr(mr)
                st.caption(f"β:{beta:.3f} 相関:{corr:.3f}")
            def sty_cmp(row):
                styles=[""]*len(row); cl=list(row.index)
                for cn in ["前日比%","25日乖離率",f"乖離(vs {code})"]:
                    if cn in cl:
                        i=cl.index(cn)
                        try:
                            v=float(str(row[cn]).replace("%","").replace("+",""))
                            styles[i]=f"color:{POS if v>=0 else NEG};font-weight:600"
                        except: pass
                return styles
            st.dataframe(df_cmp.style.apply(sty_cmp,axis=1),
                use_container_width=True,hide_index=True,
                height=min(38*(len(df_cmp)+1)+38,420))

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

    # ── 表示期間選択 ─────────────────────────────
    period_key=f"period_{code}"
    if period_key not in st.session_state: st.session_state[period_key]="6ヶ月"
    period_label=st.selectbox("📅 表示期間",options=list(PERIOD_OPTIONS.keys()),
        index=list(PERIOD_OPTIONS.keys()).index(st.session_state[period_key]),
        key=f"sel_period_{code}")
    st.session_state[period_key]=period_label
    period_days=PERIOD_OPTIONS[period_label]

    p_long=info.get("price_long",pd.DataFrame())
    if not p_long.empty:
        d_min=p_long["_dt"].min(); d_max=p_long["_dt"].max()
        st.caption(f"📂 長期データ: {len(p_long)}営業日 / {d_min.strftime('%Y/%m/%d')} ～ {d_max.strftime('%Y/%m/%d')}")

    # ── AI要約（period_label定義後に呼ぶ）────────
    render_ai_summary(code, info, period_label=period_label)

    # ① 貸借取引残高
    if not L.empty:
        st.markdown("**① 貸借取引残高 + 逆日歩**")
        La=L.sort_values("_dt",ascending=True)
        fig1=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[0.65,0.35],
            vertical_spacing=0.05,
            subplot_titles=["買い残高・売り残高＋株価（右軸）","資金フロー（買い新規－売り新規）"],
            specs=[[{"secondary_y":True}],[{"secondary_y":False}]])
        fig1.add_trace(go.Scatter(x=La["申込日"],y=La["買い残高"],name="買い残高",
            line=dict(color="#388bfd",width=2),fill="tozeroy",fillcolor="rgba(56,139,253,0.08)"),
            row=1,col=1,secondary_y=False)
        fig1.add_trace(go.Scatter(x=La["申込日"],y=La["売り残高"],name="売り残高",
            line=dict(color="#f85149",width=2),fill="tozeroy",fillcolor="rgba(248,81,73,0.08)"),
            row=1,col=1,secondary_y=False)
        if not P.empty:
            Pa2=P.sort_values("_dt",ascending=True)
            fig1.add_trace(go.Scatter(x=Pa2["日付"],y=Pa2["終値"],name="株価",
                line=dict(color="#e3b341",width=1.5,dash="dot")),row=1,col=1,secondary_y=True)
            fig1.update_yaxes(title_text="株価",secondary_y=True,gridcolor="#21262d",
                tickfont=dict(color="#e3b341",size=9),tickformat=",",row=1,col=1)
        flow=La["買い新規"].fillna(0)-La.get("売り新規",pd.Series([0]*len(La))).fillna(0)
        fig1.add_trace(go.Bar(x=La["申込日"],y=flow,name="資金フロー",
            marker_color=["#388bfd" if v>=0 else "#f85149" for v in flow],opacity=0.85),
            row=2,col=1)
        fig1.add_hline(y=0,line_dash="solid",line_color="#484f58",line_width=1,row=2,col=1)
        fig_base(fig1,400); st.plotly_chart(fig1,use_container_width=True)

        LCOLS=["申込日","買い残高","買い残高(信用%)","買い増減","買い新規","買い返済",
               "売り残高","売り残高(信用%)","売り増減","売り新規","売り返済","貸借倍率","逆日歩"]
        LNUM =["買い残高","買い増減","買い新規","買い返済","売り残高","売り増減","売り新規","売り返済"]
        Ld=L.sort_values("_dt",ascending=False).reset_index(drop=True)
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
        avail_lcols=[c for c in LCOLS if c in dp.columns]
        disp_l=pd.concat([dp[avail_lcols],pd.DataFrame([{c:av.get(c,"") for c in avail_lcols}])],ignore_index=True)
        st_l=cell_style(disp_l,Ld,"申込日",LNUM,neg_red=["差引残高"])
        for idx in disp_l[disp_l["申込日"]!="【平均】"].index:
            dv=disp_l.loc[idx,"申込日"]
            orig=Ld.loc[Ld["申込日"]==dv,"逆日歩"]
            if not orig.empty and pd.notna(orig.values[0]) and orig.values[0]>0:
                st_l.loc[idx,"逆日歩"]="background-color:#2d1f00;color:#e3b341;font-weight:700"
        st.dataframe(disp_l.style.apply(lambda _:st_l,axis=None),
            use_container_width=True,hide_index=True,height=min(38*(len(disp_l)+1)+38,520))
    else:
        st.info("① 貸借データなし")

    # ② 週次信用残（グラフ削除・テーブルのみ）
    if not M.empty:
        st.markdown("**② 週次信用残**")
        dv_avg=P["出来高"].mean() if not P.empty and "出来高" in P.columns else None
        MCOLS=["日付","買い残高","買い残消化日数","買い増減","売り残高","売り残消化日数",
               "売り増減","信用需給ネット","信用倍率","買い残増減率","売り残増減率","逆日歩"]
        MNUM =["買い残高","買い増減","売り残高","売り増減","信用倍率","買い残増減率","売り残増減率"]
        Md=M.sort_values("_dt",ascending=False).reset_index(drop=True)
        if dv_avg:
            Md["買い残消化日数_n"]=Md["買い残高"].apply(
                lambda v:float(v)/dv_avg if pd.notna(v) and dv_avg else float("nan"))
            Md["売り残消化日数_n"]=Md["売り残高"].apply(
                lambda v:float(v)/dv_avg if pd.notna(v) and dv_avg else float("nan"))
            Md["信用需給ネット_n"]=Md["買い残消化日数_n"]-Md["売り残消化日数_n"]
        dm=pd.DataFrame(); dm["日付"]=Md["日付"]
        dm["買い残高"]=Md["買い残高"].apply(fmt)
        if dv_avg: dm["買い残消化日数"]=Md["買い残消化日数_n"].apply(
            lambda v:f"{v:.1f}日" if pd.notna(v) else "-")
        dm["買い増減"]=Md["買い増減"].apply(fmt)
        dm["売り残高"]=Md["売り残高"].apply(fmt)
        if dv_avg: dm["売り残消化日数"]=Md["売り残消化日数_n"].apply(
            lambda v:f"{v:.1f}日" if pd.notna(v) else "-")
        dm["売り増減"]=Md["売り増減"].apply(fmt)
        if dv_avg: dm["信用需給ネット"]=Md["信用需給ネット_n"].apply(
            lambda v:f"{v:+.1f}日" if pd.notna(v) else "-")
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
        avail_mcols=[c for c in MCOLS if c in dm.columns]
        disp_m=pd.concat([dm[avail_mcols],pd.DataFrame([{c:av_m.get(c,"") for c in avail_mcols}])],ignore_index=True)
        st_m=cell_style(disp_m,Md,"日付",MNUM,thr=3.0)
        st.dataframe(disp_m.style.apply(lambda _:st_m,axis=None),
            use_container_width=True,hide_index=True,height=min(38*(len(disp_m)+1)+38,480))
        st.caption(
            "📌 **信用需給ネット** = 買い残高÷日次平均出来高（日）－ 売り残高÷日次平均出来高（日）  \n"
            "プラス＝買い残が厚い（上昇余地・需給重い両面あり）/ マイナス＝売り残が厚い（下落圧力・買い戻し余地）"
            + (f"  ※出来高平均={dv_avg/1e6:.1f}M" if dv_avg else ""))
    else:
        st.info("② 週次信用残データなし")

    # ③ テクニカル分析
    render_technical_section(code,info,col_hex,period_days)

# ════════════════════════════════════════
# メイン
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

st.markdown(
    "<p style='text-align:center;font-size:10px;color:#484f58;margin-top:8px'>"
    "出典：IRバンク / 日証金 / Yahoo Finance Japan / kabutan.jp ｜ 投資勧誘を目的としません</p>",
    unsafe_allow_html=True)
