"""
market_page.py ― 「🌐 市場全体」ページ（アプリ起動時のデフォルト画面）
  ・週次の信用評価損益 + 日次(JPX)の需給を1つの表に（同じ日付は週次を優先）
  ・①一般+制度 / ②制度のみ の2表、空売り比率合計、行の青系カラーリング
  ・VIX(MACD)チャート + 比較銘柄(既定: 1570) + ①②の需給チャート
データの取得・蓄積・計算は market.py。ここは表示だけ。
"""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import market as M
import ui

POS, NEG = "#58a6ff", "#f85149"            # 通常行：プラス=青 / マイナス=赤（株価テーブルと同じ）
POS_ON_BLUE, NEG_ON_BLUE = "#bfe0ff", "#ff9d97"   # 青背景の行では読みやすい明るめの色にする
LEVEL_BG = {1: "#12304f", 2: "#1558a8", 3: "#2236d6",   # 薄青 < 濃青 < 最濃青
            4: "#7a2fb5",                               # 紫：赤と青の条件が同じ日に重なった行
            5: "#a11f2e",                               # 赤
            6: "#1d7fc4"}                               # 青（好景気）：評価損益率が −3% 以上（含み損3%以下）                               # 赤：VIXゴールデンクロス日 / 評価損益率がしきい値超え
LEVEL_TXT = {1: "#c9d1d9", 2: "#ffffff", 3: "#ffffff", 4: "#ffffff", 5: "#ffffff", 6: "#ffffff"}
PERIODS = {"1ヶ月": 31, "3ヶ月": 92, "6ヶ月": 183, "1年": 366, "2年": 731, "5年": 1827, "全期間": 0}
DEFAULT_CMP = "1570"
# 自動更新はしない（『最新に更新』ボタンを押したときだけ取り直す）


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
@st.cache_resource
def _shared():
    """アプリを起動している間（サーバー側）ずっと保持する入れ物。
    iPhoneで画面が切れて再接続されても、ここにあるデータはそのまま使う（再取得しない）。"""
    return {}


def _update_now():
    """ネットワークから最新を取り直す（『最新に更新』ボタンを押したときだけ呼ぶ）。"""
    sh = _shared()
    with st.spinner("市場データを更新中…（新しい分だけ取得して蓄積します）"):
        try:
            sh["msgs"] = M.update_all()
        except Exception as e:                          # 想定外でも画面は落とさない
            sh["msgs"] = [f"更新処理で例外: {e}"]
        sh["data"] = M.load_all()
        sh["prov"] = M.fetch_vix_provisional()
        sh["t"] = time.time()
        cmp = sh.setdefault("cmp", {})                  # 比較銘柄の株価も、このボタンのときだけ取り直す
        for cc in list(cmp.keys()):
            try:
                r = _load_price(cc)
                if r:
                    cmp[cc] = r
            except Exception as e:
                print(f"[比較銘柄更新] {cc}: {e}")


def _refresh(force=False):
    """自動更新はしない。更新するのは (1)ボタン (2)保存データが空の初回だけ。"""
    ss = st.session_state
    sh = _shared()
    if force:
        _update_now()
    elif "data" not in sh:
        data = M.load_all()                             # 保存済みの蓄積データを読むだけ（スクレイピングしない）
        if data["weekly"].empty and data["jpx1"].empty and data["vix"].empty:
            _update_now()                               # まだ何も蓄積が無い初回だけ取得する
        else:
            sh.update(data=data, prov=(None, None), t=None,
                      msgs=["保存済みのデータを表示中です（最新化したいときは『🔄 最新に更新』を押してください）"])
    ss["mkt_data"], ss["mkt_prov"] = sh["data"], sh["prov"]
    ss["mkt_t"], ss["mkt_msgs"] = sh["t"], sh["msgs"]
    return sh["data"]


def _compare_state():
    sh = _shared()
    if "cmp" not in sh:
        sh["cmp"] = {}
        sh["cmp_init"] = False
    return sh["cmp"]


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
                if lv >= 3:
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


def _legend(sell_thr, short_thr, pl_thr, plb_thr):
    sw = lambda c, t: (f"<span style='display:inline-block;width:14px;height:14px;background:{c};"
                       f"border:1px solid #484f58;vertical-align:middle;margin-right:4px'></span>{t}")
    st.markdown(
        "<div style='font-size:12px;color:#8b949e'>"
        + sw(LEVEL_BG[1], f"薄青：①売り残金額 &gt; {sell_thr:,.0f}億円") + "　"
        + sw(LEVEL_BG[2], f"濃青：上記 かつ 空売り比率 ≥ {short_thr:g}%") + "　"
        + sw(LEVEL_BG[3], "最濃青：VIXのMACDがデッドクロス") + "　"
        + sw(LEVEL_BG[5], f"赤：VIXのMACDがゴールデンクロス／評価損益率 &lt; −{pl_thr:g}%（含み損が{pl_thr:g}%超）") + "　"
        + sw(LEVEL_BG[6], f"青：評価損益率 ≥ −{plb_thr:g}%（含み損{plb_thr:g}%以下＝好景気）") + "　"
        + sw(LEVEL_BG[4], "紫：赤と青の条件が重なった日") + "　"
        + f"<span style='color:{POS}'>青字=プラス</span> / <span style='color:{NEG}'>赤字=マイナス</span>"
        + "</div>", unsafe_allow_html=True)


# ────────────────────────────────────────────
# ページ本体
# ────────────────────────────────────────────
def render():
    st.markdown(_CSS, unsafe_allow_html=True)
    ui.inject()
    ui.title("🌐 市場全体", "信用需給 × VIX ― 今日の現在地")
    st.markdown("<p style='color:#8b949e;font-size:11px;margin:0 0 8px'>"
                "出典：nikkei225jp.com（週次信用評価損益・空売り比率・VIX）/ JPX 銘柄別信用取引残高（日次）/ 株探（VIX代替）"
                "</p>", unsafe_allow_html=True)

    c0, c1 = st.columns([1, 5])
    with c0:
        if st.button("🔄 最新に更新", key="mkt_refresh"):
            _refresh(force=True)
    data = _refresh()
    ss = st.session_state
    _t = ss.get("mkt_t")
    st.caption("🕒 最終更新: " + (datetime.fromtimestamp(_t, ZoneInfo("Asia/Tokyo")).strftime("%m/%d %H:%M") if _t else "このアプリ起動後は未更新（保存済みデータを表示中）")
               + "　※自動更新はしません。最新にしたいときだけ「🔄 最新に更新」を押してください。")

    with c1:
        period_label = st.radio("表示期間", list(PERIODS), index=3, horizontal=True, key="mkt_period")
    days = PERIODS[period_label]
    cutoff = datetime.today() - timedelta(days=days) if days else None

    with st.expander("⚙️ 判定のしきい値・表示設定", expanded=False):
        a, b, c = st.columns(3)
        sell_thr = a.number_input("売り残金額のしきい値（億円・①一般+制度）", value=8000.0, step=100.0, key="mkt_sell_thr")
        short_thr = b.number_input("空売り比率合計のしきい値（%）", value=40.0, step=1.0, key="mkt_short_thr")
        pl_thr = st.number_input("評価損益率の含み損しきい値（%・マイナスがこれを超える日を赤）", value=10.0, step=1.0, key="mkt_pl_thr")
        plb_thr = st.number_input("評価損益率の含み損しきい値（%・これ以下の日を青＝好景気）", value=3.0, step=1.0, key="mkt_plb_thr")
        shift = c.checkbox("VIXを日本の翌営業日にずらす（米国終値→日本市場への反映日）", value=False, key="mkt_shift")

    if data["weekly"].empty and data["jpx1"].empty:
        st.error("信用残のデータを取得できていません。『🔄 最新に更新』を押すか、下の『データ状況』を確認してください。")

    build = M.build_tables(data, shift_vix=shift)
    t1, t2, macd = build["t1"], build["t2"], build["macd"]
    lv_all = M.row_levels(t1, sell_thr, short_thr)
    red_all = ((t1["cross"] == "GC") | (t1["pl"] < -pl_thr)) if (t1 is not None and not t1.empty) else pd.Series(dtype=bool)

    plb_all = (t1["pl"] >= -plb_thr) if (t1 is not None and not t1.empty) else pd.Series(dtype=bool)

    # ── 概況（今日の数字 ＋ 平均）───────────────
    _summary(t1, macd, ss.get("mkt_prov"), data, sell_thr, short_thr, pl_thr, plb_thr)

    tab_t, tab_c = st.tabs(["📋 需給テーブル", "📈 VIX × 株価 × 信用需給チャート"])

    def cut(t):
        return t[t.index >= cutoff] if (cutoff is not None and not t.empty) else t

    with tab_t:
        _legend(sell_thr, short_thr, pl_thr, plb_thr)
        ui.section("① 一般信用 ＋ 制度信用", "週次＝日経の表をそのまま／日次＝JPX。同じ日付は週次を優先")
        _show_table(cut(t1), lv_all, red_all, plb_all, "mkt_t1", "market_overall_1")
        ui.section("② 制度信用のみ", "日次・JPX")
        st.caption("行の色は同じ日付の①の判定（売り残金額・空売り比率）とVIXクロスに連動します。評価損益率は日次では算出できないため空欄です。")
        _show_table(cut(t2), lv_all, red_all, plb_all, "mkt_t2", "market_overall_2")
        st.caption(
            "※ 日次の『変化(金額%)』はJPXの前日比、週次はひとつ前の週次との比です。"
            "※ 週次(日経: 東証+名証)と日次(JPX: 東証のみ)は対象が少し違うため、切替日に0.1%前後の段差が出ます。"
            "※ 日次の履歴はJPXの公開(直近約5営業日)から蓄積した分だけ表示されます。")

    with tab_c:
        _chart(data, t1, t2, macd, cutoff, shift, ss.get("mkt_prov"), sell_thr, short_thr)

    _status(data, ss.get("mkt_msgs", []))


def _show_table(t, lv_all, red_all, plb_all, key, fname):
    if t is None or t.empty:
        st.info("表示できるデータがありません。")
        return
    disp = _display(t)
    levels = lv_all.reindex(t.index).fillna(0).values
    levels = np.where(t["cross"].values == "DC", 3, levels).astype(int)
    red = (red_all.reindex(t.index).fillna(False).values.astype(bool) | (t["cross"].values == "GC"))
    plb = plb_all.reindex(t.index).fillna(False).values.astype(bool)
    blue_any = (levels > 0) | plb
    levels = np.where(levels > 0, levels, np.where(plb, 6, 0))            # 青は他の青条件があればそちらを優先
    levels = np.where(red & blue_any, 4, np.where(red, 5, levels))        # 赤×青の重複は紫
    st.dataframe(_styler(disp, levels), use_container_width=True, hide_index=True,
                 height=min(38 * (len(disp) + 1) + 3, 520))
    st.download_button("⬇️ CSV", disp.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{fname}.csv", mime="text/csv", key=f"{key}_dl")


def _macd_board(macd, prov):
    """VIXのMACDを一目で：3つの数字・ヒストの推移・クロスまでの距離。"""
    pv = prov[1] if (prov and prov[0]) else None
    o = M.macd_outlook(macd, pv)
    if not o:
        return
    HC_POS, HC_NEG = "#d1495b", "#3b82f6"           # ヒスト＋(赤=GC側) / −(青=DC側)
    h = o["hist"]
    up = h > 0
    trend = abs(h) < abs(o["hist_prev"])
    chip = ("MACD ＞ シグナル（上向き）" if up else "MACD ＜ シグナル（下向き）")
    chip2 = ("ヒスト縮小中（デッドクロスに近づく）" if (up and trend) else
             "ヒスト拡大中（上向きが強まる）" if up else
             "ヒスト縮小中（ゴールデンクロスに近づく）" if trend else "ヒスト拡大中（下向きが強まる）")
    # ヒストの棒グラフ（直近25営業日）
    t = macd.tail(25)
    vals = t["hist"].values
    mx = max(abs(vals).max(), 1e-9)
    W, H, bw = 300, 84, 300 / 25
    bars = []
    for i, (v, cr) in enumerate(zip(vals, t["cross"].values)):
        hh = abs(v) / mx * (H / 2 - 12)
        y = H / 2 - hh if v >= 0 else H / 2
        col = HC_POS if v >= 0 else HC_NEG
        op = 1.0 if i == len(vals) - 1 else 0.55
        bars.append(f"<rect x='{i * bw + 1:.1f}' y='{y:.1f}' width='{bw - 2:.1f}' height='{max(hh, 1):.1f}' fill='{col}' opacity='{op}'/>")
        if cr:
            ty = 10 if v >= 0 else H - 2
            bars.append(f"<text x='{i * bw + bw / 2:.1f}' y='{ty}' fill='#e6edf3' font-size='9' text-anchor='middle'>{cr}</text>")
    svg = (f"<svg viewBox='0 0 {W} {H}' width='100%' height='{H}' preserveAspectRatio='none' "
           f"style='background:#0d1117;border-radius:6px'>"
           f"<line x1='0' x2='{W}' y1='{H / 2}' y2='{H / 2}' stroke='#484f58' stroke-width='1'/>" + "".join(bars) + "</svg>")
    # クロスまでの距離
    tgt = o["target"]
    tname = "デッドクロス" if tgt == "DC" else "ゴールデンクロス"
    need = "以下" if tgt == "DC" else "以上"
    c0, x = o["c"], o["x_cross"]
    lo, hi = min(c0, x) * 0.97, max(c0, x) * 1.03
    pos = lambda v: (v - lo) / (hi - lo) * 100
    gauge = (f"<div style='position:relative;height:14px;background:#0d1117;border-radius:7px;margin:14px 0 4px'>"
             f"<div style='position:absolute;left:{pos(x):.1f}%;top:-4px;width:3px;height:22px;background:#e3b341'></div>"
             f"<div style='position:absolute;left:calc({pos(c0):.1f}% - 6px);top:0;width:14px;height:14px;border-radius:7px;background:#f0f6fc'></div></div>"
             f"<div style='display:flex;justify-content:space-between;font-size:11px;color:#8b949e'>"
             f"<span>● 今日のVIX {c0:.2f}</span><span style='color:#e3b341'>▌ {tname}の水準 {x:.2f}</span></div>")
    days = (f"<b>{o['days_flat']}営業日後</b>に{tname}" if o["days_flat"] else f"{o['horizon']}営業日以内には{tname}しません")
    lines = [f"明日のVIX終値が <b style='color:#e3b341'>{x:.2f}{need}</b>（今日比 {o['pct']:+.1f}%）なら、明日{tname}",
             f"VIXが今日の終値（{c0:.2f}）のまま<b>横ばい</b>なら、{days}"]
    if "prov" in o:
        lines.append(f"暫定値 {o['prov']:.2f} のまま確定すると → " +
                     (f"<b style='color:#e3b341'>{tname}になります</b>" if o["prov_cross"] else "クロスしません（現状維持）"))

    def box(label, v, color="#f0f6fc"):
        return (f"<div style='flex:1;min-width:90px'><div style='font-size:11px;color:#8b949e'>{label}</div>"
                f"<div style='font-size:24px;font-weight:800;color:{color}'>{v:+.2f}</div></div>")
    html = (
        "<div style='background:#161b22;border:1px solid #30363d;border-left:5px solid #f0883e;border-radius:8px;padding:10px 14px;margin:8px 0'>"
        f"<div style='font-size:15px;font-weight:800;color:#f0f6fc;margin-bottom:6px'>🌡 VIX の MACD　<span style='font-size:12px;color:#8b949e;font-weight:500'>{o['date']:%m/%d}確定ベース</span></div>"
        "<div style='display:flex;flex-wrap:wrap;gap:10px;margin-bottom:6px'>"
        + box("MACD", o["macd"]) + box("シグナル", o["signal"]) + box("ヒスト（差）", h, HC_POS if up else HC_NEG) + "</div>"
        f"<div style='font-size:12px;color:#c9d1d9;margin-bottom:6px'>{chip}／{chip2}</div>"
        f"<div style='font-size:11px;color:#8b949e'>ヒストの推移（直近25営業日・<span style='color:{HC_POS}'>赤=＋</span> <span style='color:{HC_NEG}'>青=−</span>）</div>"
        + svg + gauge +
        "<div style='font-size:13px;color:#e6edf3;line-height:1.7;margin-top:6px'>" + "<br>".join("・" + l for l in lines) + "</div>"
        "<div style='font-size:10px;color:#6e7681;margin-top:4px'>指数平滑の式から逆算した算術値です（予測ではありません）。クロス＝ヒストの符号が変わること。</div>"
        "</div>")
    st.markdown(html, unsafe_allow_html=True)


def _jpx_notice(data):
    """JPX日次の掲載状況。最新日と、次の掲載予定（目安16時）を表示する。"""
    j = data.get("jpx1")
    if j is None or j.empty:
        return
    last = pd.Timestamp(j.index.max()).normalize()
    nxt = last + pd.offsets.BDay(1)                  # 次に出るはずの取引日（祝日は考慮しない）
    pub = (nxt + pd.offsets.BDay(1)).normalize() + pd.Timedelta(hours=16)
    now = pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None)
    if now >= pub:
        st.caption(f"⏳ JPX日次の取り込み済み最新は **{last:%m/%d}**。{nxt:%m/%d}分は {pub:%m/%d} 16時ごろ（目安）に掲載予定ですが、"
                   "まだ取り込めていません。JPX側の掲載待ちの可能性が高く、掲載後に「最新に更新」を押すと取り込みます"
                   "（祝日は考慮していません）。")
    else:
        st.caption(f"ℹ️ JPX日次の取り込み済み最新は **{last:%m/%d}**。{nxt:%m/%d}分は {pub:%m/%d} 16時ごろ（目安）に掲載予定です。")


def _summary(t1, macd, prov, data, sell_thr, short_thr, pl_thr, plb_thr):
    """今日の数字（大）＋ 平均 ＋ 差。色の条件は表と同じ定義。"""
    BLUE = "background:#1558a8;color:#fff"
    RED = "background:#a11f2e;color:#fff"
    DEEP = "background:#2236d6;color:#fff"
    if t1 is not None and not t1.empty:
        r = t1.iloc[0]
        ui.group(f"▼ 最新（①一般+制度）　{t1.index[0].strftime('%Y/%m/%d')}（{r['kind']}）　※平均は蓄積した全期間")
        wk = t1[t1["kind"] == "週次"]
        d0 = t1.index[0].strftime("%m/%d")
        # 空売り比率は当日分が出るため、表(信用残のある日だけ)ではなく取得した系列そのものから最新を取る
        shs = data.get("short")
        sh = shs["short"].dropna().sort_index(ascending=False) if shs is not None and not shs.empty else t1["short"].dropna()
        sh_v = sh.iloc[0] if not sh.empty else None
        sh_d = sh.index[0].strftime("%m/%d") if not sh.empty else "-"
        items = [
            ui.stat(f"信用倍率（金額） {d0}{r['kind']}", r["ratio"], t1["ratio"].mean(), dec=2, accent="#e3b341"),
            ui.stat(f"売り残（億円） {d0}{r['kind']}", r["sell_k"], t1["sell_k"].mean(), dec=0, accent="#f85149",
                    badge=(f"{sell_thr:,.0f}億円超" if r["sell_k"] > sell_thr else None), badge_style=BLUE),
            ui.stat(f"空売り比率合計（%） {sh_d}時点", sh_v, sh.mean() if not sh.empty else None, dec=1, accent="#bc8cff",
                    badge=(f"{short_thr:g}%以上" if sh_v is not None and sh_v >= short_thr else None),
                    badge_style=DEEP),
        ]
        if not wk.empty and pd.notna(wk.iloc[0]["pl"]):
            v = wk.iloc[0]["pl"]
            bd, bs = (None, None)
            if v < -pl_thr:
                bd, bs = f"含み損{pl_thr:g}%超（下落基調）", RED
            elif v >= -plb_thr:
                bd, bs = f"含み損{plb_thr:g}%以下（好景気）", "background:#1d7fc4;color:#fff"
            items.append(ui.stat(f"評価損益率（週次 {wk.index[0].strftime('%m/%d')}）", v, wk["pl"].mean(),
                                 dec=2, suffix="%", sign=True, accent="#58a6ff", badge=bd, badge_style=bs,
                                 val_color=(NEG if v < 0 else POS)))
        vix = data.get("vix")
        if macd is not None and not macd.empty:
            last = macd.iloc[-1]
            ev = macd[macd["cross"] != ""]
            note = "MACD＞シグナル（上向き）" if last["hist"] > 0 else "MACD＜シグナル（下向き）"
            bd = bs = None
            if not ev.empty:
                e, d_ = ev.iloc[-1]["cross"], ev.index[-1].strftime("%m/%d")
                bd = f"直近 {'デッドクロス' if e == 'DC' else 'ゴールデンクロス'} {d_}"
                bs = DEEP if e == "DC" else RED
            if prov and prov[0]:
                note += f"<br>暫定 {prov[1]:.2f}（{prov[0][5:].replace('-', '/')}米国取引中）"
            items.append(ui.stat(f"VIX（{macd.index[-1].strftime('%m/%d')}確定）", last["c"], macd["c"].mean(), dec=2,
                                 accent="#f0883e", note=note, badge=bd, badge_style=bs))
        ui.card_row(items)
        if not sh.empty and sh.index[0] > t1.index[0]:
            st.caption(f"ℹ️ 空売り比率は当日分（{sh.index[0]:%m/%d}）まで取得済みです。信用残・評価損益は"
                       f"{t1.index[0]:%m/%d}分までの掲載です（空売り比率の最新日は、表には信用残のある日だけが並ぶため出ません。チャートには出ます）。")
        _jpx_notice(data)
        if macd is not None and not macd.empty:
            _macd_board(macd, prov)


# ────────────────────────────────────────────
# チャート
# ────────────────────────────────────────────
def _chart(data, t1, t2, macd, cutoff, shift, prov, sell_thr, short_thr):
    if macd is None or macd.empty:
        st.warning("VIXのデータがありません。")
        return
    cmp_data = _compare_state()
    ss = st.session_state
    if not _shared().get("cmp_init"):                   # 既定で 1570（日経平均レバレッジ）を比較に入れる（起動後の最初の1回だけ）
        _shared()["cmp_init"] = True
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
