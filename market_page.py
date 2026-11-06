"""
market_page.py ― 「🌐 市場全体」ページ（アプリ起動時のデフォルト画面）
  ・週次の信用評価損益 + 日次(JPX)の需給を1つの表に（同じ日付は週次を優先）
  ・①一般+制度 / ②制度のみ の2表、空売り比率合計、行の青系カラーリング
  ・VIX(MACD)チャート + 比較銘柄(既定: 1570) + ①②の需給チャート
データの取得・蓄積・計算は market.py。ここは表示だけ。
"""
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import market as M

POS, NEG = "#58a6ff", "#f85149"            # 通常行：プラス=青 / マイナス=赤（株価テーブルと同じ）
POS_ON_BLUE, NEG_ON_BLUE = "#bfe0ff", "#ff9d97"   # 青背景の行では読みやすい明るめの色にする
LEVEL_BG = {1: "#12304f", 2: "#1558a8", 3: "#2236d6"}   # 薄青 < 濃青 < 最濃青
LEVEL_TXT = {1: "#c9d1d9", 2: "#ffffff", 3: "#ffffff"}
PERIODS = {"1ヶ月": 31, "3ヶ月": 92, "6ヶ月": 183, "1年": 366, "2年": 731, "5年": 1827, "全期間": 0}
DEFAULT_CMP = "1570"
REFRESH_SEC = 3600                          # 起動中、この間隔より古ければ自動で取り直す


_CSS = """<style>
[data-testid="stMetricValue"],[data-testid="stMetricLabel"],[data-testid="stMetricLabel"] *,
[data-testid="stRadio"] label,[data-testid="stRadio"] p,[data-testid="stCheckbox"] label,
[data-testid="stNumberInput"] label,[data-testid="stCaptionContainer"],
[data-testid="stMarkdownContainer"],[data-testid="stExpander"] summary,
[data-testid="stSidebar"] label,[data-testid="stSidebar"] p{color:#c9d1d9!important}
[data-testid="stCaptionContainer"],[data-testid="stCaptionContainer"] p{color:#8b949e!important}
</style>"""


def _fig_base(fig, h):
    fig.update_layout(paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
                      font=dict(color="#c9d1d9", size=11), height=h,
                      legend=dict(bgcolor="rgba(22,27,34,0.9)", bordercolor="#30363d", borderwidth=1,
                                  orientation="h", yanchor="bottom", y=1.07, font=dict(size=10, color="#c9d1d9")),
                      margin=dict(l=4, r=4, t=150, b=4), hovermode="x unified")
    fig.update_xaxes(gridcolor="#21262d", linecolor="#30363d", tickfont=dict(color="#8b949e", size=10),
                     rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_yaxes(gridcolor="#21262d", linecolor="#30363d", tickfont=dict(color="#8b949e", size=10))


# ────────────────────────────────────────────
# データの更新と読み出し
# ────────────────────────────────────────────
def _refresh(force=False):
    ss = st.session_state
    stale = (time.time() - ss.get("mkt_t", 0)) > REFRESH_SEC
    if force or stale or "mkt_data" not in ss:
        with st.spinner("市場データを更新中…（新しい分だけ取得して蓄積します）"):
            try:
                ss["mkt_msgs"] = M.update_all()
            except Exception as e:                      # 想定外でも画面は落とさない
                ss["mkt_msgs"] = [f"更新処理で例外: {e}"]
            ss["mkt_data"] = M.load_all()
            ss["mkt_prov"] = M.fetch_vix_provisional()
            ss["mkt_t"] = time.time()
    return ss["mkt_data"]


def _compare_state():
    ss = st.session_state
    if "mkt_cmp" not in ss:
        ss["mkt_cmp"] = {}
        ss["mkt_cmp_init"] = False
    return ss["mkt_cmp"]


def _load_price(code):
    from scraper import fetch_price_long, resolve_name
    df = fetch_price_long(code)
    if df is None or df.empty:
        return None
    try:
        name = resolve_name(code)
    except Exception:
        name = code
    return {"name": name, "df": df.sort_values("_dt").reset_index(drop=True)}


# ────────────────────────────────────────────
# 表
# ────────────────────────────────────────────
COLS = ["日付", "区分", "売り残 枚数(千株)", "売り残 金額(億円)", "売り残 変化(金額%)", "空売り比率合計(%)",
        "買い残 枚数(千株)", "買い残 金額(億円)", "買い残 変化(金額%)", "信用倍率", "信用評価損益率(%)", "VIXクロス"]
SIGNED = ["売り残 変化(金額%)", "買い残 変化(金額%)", "信用評価損益率(%)"]


def _display(t: pd.DataFrame) -> pd.DataFrame:
    d = pd.DataFrame({
        "日付": t.index.strftime("%Y/%m/%d"), "区分": t["kind"].values,
        "売り残 枚数(千株)": t["sell_s"].values, "売り残 金額(億円)": t["sell_k"].values,
        "売り残 変化(金額%)": t["sell_chg"].values, "空売り比率合計(%)": t["short"].values,
        "買い残 枚数(千株)": t["buy_s"].values, "買い残 金額(億円)": t["buy_k"].values,
        "買い残 変化(金額%)": t["buy_chg"].values, "信用倍率": t["ratio"].values,
        "信用評価損益率(%)": t["pl"].values,
        "VIXクロス": [{"DC": "🔻DC", "GC": "🔺GC"}.get(c, "") for c in t["cross"].values],
    })
    return d


def _styler(disp: pd.DataFrame, levels: np.ndarray):
    def css(_):
        out = pd.DataFrame("", index=disp.index, columns=disp.columns)
        for i, lv in enumerate(levels):
            lv = int(lv)
            if lv:
                out.iloc[i, :] = f"background-color:{LEVEL_BG[lv]};color:{LEVEL_TXT[lv]}"
                if lv == 3:
                    out.iloc[i, :] += ";font-weight:700"
            for c in SIGNED:
                v = disp.iloc[i][c]
                if pd.notna(v) and v != 0:
                    pos, neg = (POS_ON_BLUE, NEG_ON_BLUE) if lv else (POS, NEG)
                    out.iloc[i, disp.columns.get_loc(c)] += f";color:{pos if v > 0 else neg};font-weight:600"
        return out

    fmt = {"売り残 枚数(千株)": "{:,.0f}", "買い残 枚数(千株)": "{:,.0f}",
           "売り残 金額(億円)": "{:,.2f}", "買い残 金額(億円)": "{:,.2f}",
           "売り残 変化(金額%)": "{:+.2f}", "買い残 変化(金額%)": "{:+.2f}",
           "空売り比率合計(%)": "{:.1f}", "信用倍率": "{:.2f}", "信用評価損益率(%)": "{:+.2f}"}
    sty = disp.style.apply(css, axis=None).format(fmt, na_rep="-")
    sty = sty.format(fmt["信用評価損益率(%)"], subset=["信用評価損益率(%)"], na_rep="")   # 日次は空欄
    return sty


def _legend(sell_thr, short_thr):
    sw = lambda c, t: (f"<span style='display:inline-block;width:14px;height:14px;background:{c};"
                       f"border:1px solid #484f58;vertical-align:middle;margin-right:4px'></span>{t}")
    st.markdown(
        "<div style='font-size:12px;color:#8b949e'>"
        + sw(LEVEL_BG[1], f"薄青：①売り残金額 &gt; {sell_thr:,.0f}億円") + "　"
        + sw(LEVEL_BG[2], f"濃青：上記 かつ 空売り比率 ≥ {short_thr:g}%") + "　"
        + sw(LEVEL_BG[3], "最濃青：VIXのMACDがデッドクロス（最優先）") + "　"
        + f"<span style='color:{POS}'>青字=プラス</span> / <span style='color:{NEG}'>赤字=マイナス</span>"
        + "</div>", unsafe_allow_html=True)


# ────────────────────────────────────────────
# ページ本体
# ────────────────────────────────────────────
def render():
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown("<h2 style='text-align:center;color:#f0f6fc;font-size:20px;margin:4px 0'>"
                "🌐 市場全体 ― 信用需給 × VIX</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align:center;color:#8b949e;font-size:11px;margin:0 0 8px'>"
                "出典：nikkei225jp.com（週次信用評価損益・空売り比率・VIX）/ JPX 銘柄別信用取引残高（日次）/ 株探（VIX代替）"
                "</p>", unsafe_allow_html=True)

    c0, c1 = st.columns([1, 5])
    with c0:
        if st.button("🔄 最新に更新", key="mkt_refresh"):
            _refresh(force=True)
    data = _refresh()
    ss = st.session_state

    with c1:
        period_label = st.radio("表示期間", list(PERIODS), index=3, horizontal=True, key="mkt_period")
    days = PERIODS[period_label]
    cutoff = datetime.today() - timedelta(days=days) if days else None

    with st.expander("⚙️ 判定のしきい値・表示設定", expanded=False):
        a, b, c = st.columns(3)
        sell_thr = a.number_input("売り残金額のしきい値（億円・①一般+制度）", value=8000.0, step=100.0, key="mkt_sell_thr")
        short_thr = b.number_input("空売り比率合計のしきい値（%）", value=40.0, step=1.0, key="mkt_short_thr")
        shift = c.checkbox("VIXを日本の翌営業日にずらす（米国終値→日本市場への反映日）", value=False, key="mkt_shift")

    if data["weekly"].empty and data["jpx1"].empty:
        st.error("信用残のデータを取得できていません。『🔄 最新に更新』を押すか、下の『データ状況』を確認してください。")

    build = M.build_tables(data, shift_vix=shift)
    t1, t2, macd = build["t1"], build["t2"], build["macd"]
    lv_all = M.row_levels(t1, sell_thr, short_thr)

    # ── 概況 ───────────────────────────────
    _summary(t1, macd, ss.get("mkt_prov"))

    tab_t, tab_c = st.tabs(["📋 需給テーブル", "📈 VIX × 株価 × 信用需給チャート"])

    def cut(t):
        return t[t.index >= cutoff] if (cutoff is not None and not t.empty) else t

    with tab_t:
        _legend(sell_thr, short_thr)
        st.markdown("**① 一般信用 + 制度信用**（週次＝日経の表をそのまま／日次＝JPX。同じ日付は週次を優先）")
        _show_table(cut(t1), lv_all, "mkt_t1", "market_overall_1")
        st.markdown("**② 制度信用のみ**（日次・JPX）")
        st.caption("行の色は同じ日付の①の判定（売り残金額・空売り比率）とVIXクロスに連動します。評価損益率は日次では算出できないため空欄です。")
        _show_table(cut(t2), lv_all, "mkt_t2", "market_overall_2")
        st.caption(
            "※ 日次の『変化(金額%)』はJPXの前日比、週次はひとつ前の週次との比です。"
            "※ 週次(日経: 東証+名証)と日次(JPX: 東証のみ)は対象が少し違うため、切替日に0.1%前後の段差が出ます。"
            "※ 日次の履歴はJPXの公開(直近約5営業日)から蓄積した分だけ表示されます。")

    with tab_c:
        _chart(data, t1, t2, macd, cutoff, shift, ss.get("mkt_prov"), sell_thr, short_thr)

    _status(data, ss.get("mkt_msgs", []))


def _show_table(t, lv_all, key, fname):
    if t is None or t.empty:
        st.info("表示できるデータがありません。")
        return
    disp = _display(t)
    levels = lv_all.reindex(t.index).fillna(0).values
    levels = np.where(t["cross"].values == "DC", 3, levels)
    st.dataframe(_styler(disp, levels), use_container_width=True, hide_index=True,
                 height=min(38 * (len(disp) + 1) + 3, 520))
    st.download_button("⬇️ CSV", disp.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{fname}.csv", mime="text/csv", key=f"{key}_dl")


def _summary(t1, macd, prov):
    cols = st.columns(5)
    if t1 is not None and not t1.empty:
        r = t1.iloc[0]
        cols[0].metric("最新（①合算）", t1.index[0].strftime("%m/%d") + f"（{r['kind']}）")
        cols[1].metric("信用倍率", f"{r['ratio']:.2f}")
        cols[2].metric("売り残", f"{r['sell_k']:,.0f}億円")
        if pd.notna(r.get("short")):
            cols[3].metric("空売り比率合計", f"{r['short']:.1f}%")
        wk = t1[t1["kind"] == "週次"]
        if not wk.empty and pd.notna(wk.iloc[0]["pl"]):
            cols[4].metric(f"評価損益率（週次 {wk.index[0].strftime('%m/%d')}）", f"{wk.iloc[0]['pl']:+.2f}%")
    if macd is not None and not macd.empty:
        last = macd.iloc[-1]
        state = "MACD＞シグナル（上向き）" if last["hist"] > 0 else "MACD＜シグナル（下向き）"
        ev = macd[macd["cross"] != ""]
        ev_txt = ""
        if not ev.empty:
            e = ev.iloc[-1]
            ev_txt = f"　直近クロス: {'デッド' if e['cross'] == 'DC' else 'ゴールデン'} {ev.index[-1].strftime('%m/%d')}"
        msg = f"VIX {last['c']:.2f}（{macd.index[-1].strftime('%m/%d')}確定）｜{state}{ev_txt}"
        if prov and prov[0]:
            msg += f"｜暫定 {prov[1]:.2f}（{prov[0][5:].replace('-', '/')}米国取引中・MACD計算には未使用）"
        st.caption("🌡️ " + msg)


# ────────────────────────────────────────────
# チャート
# ────────────────────────────────────────────
def _chart(data, t1, t2, macd, cutoff, shift, prov, sell_thr, short_thr):
    if macd is None or macd.empty:
        st.warning("VIXのデータがありません。")
        return
    cmp_data = _compare_state()
    ss = st.session_state
    if not ss.get("mkt_cmp_init"):                      # 既定で 1570（日経平均レバレッジ）を比較に入れる
        ss["mkt_cmp_init"] = True
        with st.spinner(f"比較銘柄 {DEFAULT_CMP} を取得中…"):
            try:
                r = _load_price(DEFAULT_CMP)
                if r:
                    cmp_data[DEFAULT_CMP] = r
                else:
                    st.warning(f"{DEFAULT_CMP} の株価を取得できませんでした（VIXと信用需給のみ表示します）。")
            except Exception as e:
                st.warning(f"{DEFAULT_CMP} の取得で例外: {e}")

    with st.form("mkt_cmp_form", clear_on_submit=True):
        f1, f2 = st.columns([4, 1])
        code = f1.text_input("比較銘柄コード", placeholder="例：1568 / 7203 / 998407.O", label_visibility="collapsed")
        add = f2.form_submit_button("＋比較追加", use_container_width=True)
    if add and code.strip():
        cc = code.strip().upper()
        with st.spinner(f"{cc} 取得中…"):
            try:
                r = _load_price(cc)
            except Exception as e:
                r = None
                st.error(f"取得で例外: {e}")
        if r:
            cmp_data[cc] = r
        elif cc not in cmp_data:
            st.error(f"❌ {cc} のデータを取得できませんでした。")
    if cmp_data:
        rc = st.columns(min(len(cmp_data), 6))
        for i, (cc, ci) in enumerate(list(cmp_data.items())):
            if rc[i % 6].button(f"✕ {cc} {ci['name']}", key=f"mkt_rm_{cc}"):
                cmp_data.pop(cc, None)
                st.rerun()
    mode = st.radio("比較の表示方法", ["実値（比較銘柄は右軸）", "相対（期間初=100）"], horizontal=True, key="mkt_cmp_mode")
    relative = mode.startswith("相対")

    def cut(df):
        return df[df.index >= cutoff] if cutoff is not None else df

    mc = macd.copy()
    mc.index = M.shift_to_jp(mc.index, shift)
    mc = cut(mc)
    if mc.empty:
        st.warning("選択期間にVIXデータがありません。")
        return
    t1a = cut(t1.sort_index()) if t1 is not None and not t1.empty else t1
    t2a = cut(t2.sort_index()) if t2 is not None and not t2.empty else t2
    sh = data["short"]
    sh = cut(sh.sort_index()) if sh is not None and not sh.empty else sh

    fig = make_subplots(rows=6, cols=1, shared_xaxes=True, vertical_spacing=0.025,
                        row_heights=[0.27, 0.13, 0.17, 0.15, 0.14, 0.14],
                        subplot_titles=["VIX（左軸）と比較銘柄", "VIX の MACD (12,26,9)",
                                        "① 一般+制度 信用残・金額（億円）：売り残=左軸 / 買い残=右軸",
                                        "② 制度信用のみ 信用残・金額（億円）：売り残=左軸 / 買い残=右軸",
                                        "信用倍率（金額ベース）", "空売り比率合計（%）"],
                        specs=[[{"secondary_y": True}], [{"secondary_y": False}],
                               [{"secondary_y": True}], [{"secondary_y": True}],
                               [{"secondary_y": False}], [{"secondary_y": False}]])
    base_v = mc["c"].iloc[0]
    vy = (mc["c"] / base_v * 100) if relative else mc["c"]
    fig.add_trace(go.Scatter(x=mc.index, y=vy, mode="lines", name="VIX",
                             line=dict(color="#f0883e", width=2),
                             hovertemplate="%{x|%Y/%m/%d}<br>VIX %{y:.2f}<extra></extra>"), row=1, col=1)
    dc, gc = mc[mc["cross"] == "DC"], mc[mc["cross"] == "GC"]
    mk = (lambda s: (s["c"] / base_v * 100) if relative else s["c"])
    fig.add_trace(go.Scatter(x=dc.index, y=mk(dc), mode="markers", name="デッドクロス（株価は上昇に転じやすい）",
                             marker=dict(symbol="triangle-down", size=9, color=LEVEL_BG[3],
                                         line=dict(width=1, color="#ffffff"))), row=1, col=1)
    fig.add_trace(go.Scatter(x=gc.index, y=mk(gc), mode="markers", name="ゴールデンクロス（株価下落に注意）",
                             marker=dict(symbol="triangle-up", size=9, color="#f85149",
                                         line=dict(width=1, color="#ffffff"))), row=1, col=1)
    if prov and prov[0]:
        pd_ = pd.Timestamp(prov[0])
        pd_ = M.shift_to_jp([pd_], shift)[0]
        py = prov[1] / base_v * 100 if relative else prov[1]
        fig.add_trace(go.Scatter(x=[pd_], y=[py], mode="markers", name="VIX暫定（取引中）",
                                 marker=dict(symbol="circle-open", size=10, color="#f0883e")), row=1, col=1)

    palette = ["#388bfd", "#3fb950", "#bc8cff", "#e3b341", "#79c0ff", "#ff7b72"]
    for i, (cc, ci) in enumerate(cmp_data.items()):
        p = ci["df"].set_index("_dt")["終値"].astype(float)
        p = cut(p.to_frame("終値"))["終値"]
        if p.empty:
            continue
        y = (p / p.iloc[0] * 100) if relative else p
        fig.add_trace(go.Scatter(x=p.index, y=y, mode="lines", name=f"{cc} {ci['name']}",
                                 line=dict(color=palette[i % 6], width=1.8),
                                 hovertemplate=f"{cc} " + "%{y:,.2f}<extra></extra>"),
                      row=1, col=1, secondary_y=(not relative))

    # MACD
    colors = np.where(mc["hist"] >= 0, "rgba(88,166,255,0.55)", "rgba(248,81,73,0.55)")
    fig.add_trace(go.Bar(x=mc.index, y=mc["hist"], marker_color=colors, name="ヒストグラム", showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=mc.index, y=mc["macd"], mode="lines", name="MACD", line=dict(color="#58a6ff", width=1.5)), row=2, col=1)
    fig.add_trace(go.Scatter(x=mc.index, y=mc["signal"], mode="lines", name="シグナル", line=dict(color="#e3b341", width=1.5)), row=2, col=1)
    fig.add_hline(y=0, line=dict(color="#484f58", width=1, dash="dot"), row=2, col=1)

    def margin_panel(t, row, with_thr):
        if t is None or t.empty:
            return
        fig.add_trace(go.Scatter(x=t.index, y=t["sell_k"], mode="lines", name="売り残(金額)", line=dict(color="#f85149", width=1.6),
                                 showlegend=(row == 3)), row=row, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(x=t.index, y=t["buy_k"], mode="lines", name="買い残(金額)", line=dict(color="#58a6ff", width=1.6),
                                 showlegend=(row == 3)), row=row, col=1, secondary_y=True)
        if with_thr:
            fig.add_hline(y=sell_thr, line=dict(color="#f85149", width=1, dash="dash"), row=row, col=1,
                          annotation_text=f"{sell_thr:,.0f}億円", annotation_position="top left")

    margin_panel(t1a, 3, True)
    margin_panel(t2a, 4, False)
    for t_, nm, colr in ((t1a, "信用倍率 ①一般+制度", "#e3b341"), (t2a, "信用倍率 ②制度のみ", "#3fb950")):
        if t_ is not None and not t_.empty:
            fig.add_trace(go.Scatter(x=t_.index, y=t_["ratio"], mode="lines", name=nm, line=dict(color=colr, width=1.6)), row=5, col=1)
    if sh is not None and not sh.empty:
        fig.add_trace(go.Scatter(x=sh.index, y=sh["short"], mode="lines", name="空売り比率合計", line=dict(color="#bc8cff", width=1.6)), row=6, col=1)
        fig.add_hline(y=short_thr, line=dict(color="#bc8cff", width=1, dash="dash"), row=6, col=1,
                      annotation_text=f"{short_thr:g}%", annotation_position="top left")

    _fig_base(fig, 1150)
    fig.update_yaxes(title_text="相対(100)" if relative else "VIX", row=1, col=1, secondary_y=False)
    if not relative:
        fig.update_yaxes(title_text="比較銘柄", row=1, col=1, secondary_y=True, showgrid=False)
    for r_ in (3, 4):
        fig.update_yaxes(showgrid=False, row=r_, col=1, secondary_y=True)
    st.plotly_chart(fig, use_container_width=True)
    st.caption("🔻/🔺 はVIXのMACDクロス。『VIXを翌営業日にずらす』をONにすると、米国終値を日本市場の反映日へ移して重ねます。"
               " 信用残の線は、週次(日経)と日次(JPX)を同じ日付なら週次優先でつないだものです。")


# ────────────────────────────────────────────
# データ状況
# ────────────────────────────────────────────
def _status(data, msgs):
    with st.expander("🗂️ データ状況・取得メッセージ", expanded=bool(msgs)):
        names = {"weekly": "週次 信用評価損益", "jpx1": "日次① 一般+制度（JPX）", "jpx2": "日次② 制度のみ（JPX）",
                 "short": "空売り比率合計", "vix": "VIX（確定分）"}
        rows = []
        for k, nm in names.items():
            d = data.get(k)
            if d is None or d.empty:
                rows.append({"データ": nm, "件数": 0, "最古": "-", "最新": "-"})
            else:
                rows.append({"データ": nm, "件数": len(d), "最古": d.index.min().strftime("%Y/%m/%d"),
                             "最新": d.index.max().strftime("%Y/%m/%d")})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        for m in msgs:
            st.warning(m)
        if st.button("株探のVIXと照合する", key="mkt_verify"):
            with st.spinner("株探を取得中…"):
                try:
                    st.info(M.verify_vix_with_kabutan(data["vix"]))
                except Exception as e:
                    st.error(f"照合に失敗: {e}")
        st.caption("取得したデータは起動のたびに新しい分だけ追記され、Supabase（未設定ならローカルJSON）に蓄積されます。")
