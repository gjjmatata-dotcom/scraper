"""
signal_stats.py ― 「今日のサインが出た日の、その後の実績」を数える（個別株ページ・テクニカルタブの下に表示）

考え方
  ・指標(RSI/MACD/%b/ADX/乖離率/ダイバージェンス)の「今日の状態」と同じ状態だった過去の日を全部数え、
    その翌日・5日後・10日後・20日後に上がった割合（●件中●件）と平均変化率を出す。
  ・「通常の日」の上昇率を必ず並べる（普通の日でも半分以上は上がるため）。
  ・同じ状態が続くと同じ出来事を何度も数えるので、「別々の事例(5日以上あけた)」の数も出す。
  ・件数が30件未満は「参考」。通常と差があるかは、上昇率の95%信頼区間(Wilson)が通常の率を外れているかで判定。
  ・指標はすべて「その日までのデータだけ」で計算（未来の情報は使わない）。
  ・これは過去の頻度であり、将来を保証するものではない。
"""
import math

import numpy as np
import pandas as pd

HORIZONS = [1, 5, 10, 20]
HLAB = {1: "翌日", 5: "5日後", 10: "10日後", 20: "20日後"}
MIN_N = 30            # これ未満は「参考」
GAP = 5               # この日数より離れたら別の事例


# ───────── 指標の計算（すべて過去のデータだけで計算）─────────
def _rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0), (-d.clip(upper=0))
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    r = 100 - 100 / (1 + rs)
    r[(ad == 0) & (au > 0)] = 100.0
    return r


def _macd(c):
    e12, e26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    m = e12 - e26
    s = m.ewm(span=9, adjust=False).mean()
    return m, s, m - s


def _pctb(c, n=20):
    ma, sd = c.rolling(n).mean(), c.rolling(n).std(ddof=0)
    return (c - (ma - 2 * sd)) / (4 * sd).replace(0, np.nan)


def _adx(h, l, c, n=14):
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = 1 / n
    atr = tr.ewm(alpha=a, adjust=False, min_periods=n).mean()
    pdi = 100 * pd.Series(pdm, index=c.index).ewm(alpha=a, adjust=False, min_periods=n).mean() / atr
    mdi = 100 * pd.Series(mdm, index=c.index).ewm(alpha=a, adjust=False, min_periods=n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False, min_periods=n).mean(), pdi, mdi


def _divergence(close, ind, kind, thr, peak_min=None):
    """
    弱気(kind='bear'): 今日の終値が20日高値で、直近の前回の山(5〜59日前の終値の最高日)より高いのに、
                       その日の指標より今日の指標が低い。
    強気(kind='bull'): 安値で同様に逆。
    """
    c, x = close.values, ind.values
    n = len(c)
    out = np.zeros(n, dtype=bool)
    for t in range(60, n):
        if np.isnan(x[t]):
            continue
        w = c[t - 19:t + 1]
        s, e = t - 59, t - 5
        seg = c[s:e + 1]
        if kind == "bear":
            if c[t] < np.nanmax(w):
                continue
            p = s + int(np.nanargmax(seg))
            if c[t] > c[p] and not np.isnan(x[p]) and x[t] < x[p] - thr and (peak_min is None or x[p] > peak_min):
                out[t] = True
        else:
            if c[t] > np.nanmin(w):
                continue
            p = s + int(np.nanargmin(seg))
            if c[t] < c[p] and not np.isnan(x[p]) and x[t] > x[p] + thr and (peak_min is None or x[p] < peak_min):
                out[t] = True
    return pd.Series(out, index=close.index)


# ───────── 統計 ─────────
def _wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _episodes(pos):
    if len(pos) == 0:
        return 0
    pos = np.sort(np.asarray(pos))
    return int(1 + np.sum(np.diff(pos) > GAP))


def _stat(mask, fwd, base):
    """mask: 日ごとの真偽。戻り値: {"n","ep","h":{h:{n,up,down,upp,mean,med,sig}}}"""
    m = mask.values
    res = {"n": 0, "ep": 0, "h": {}}
    pos_all = np.where(m & fwd[1].notna().values)[0]
    res["n"] = int(len(pos_all))
    res["ep"] = _episodes(pos_all)
    for h in HORIZONS:
        r = fwd[h].values
        ok = m & ~np.isnan(r)
        v = r[ok]
        n = len(v)
        up, dn = int((v > 0).sum()), int((v < 0).sum())
        d = {"n": n, "up": up, "down": dn, "upp": (up / n if n else np.nan),
             "mean": (float(np.mean(v)) if n else np.nan), "med": (float(np.median(v)) if n else np.nan),
             "sig": None}
        n_eff = n / h          # 先の日数hが重なるため、独立な標本数は約 n/h
        if n_eff >= 20:
            lo, hi = _wilson((up / n) * n_eff, n_eff, z=2.58)      # 99%区間（表示数が多く、偶然の◎を減らすため厳しめ）
            p0 = base[h]["upp"]
            d["sig"] = "high" if lo > p0 else ("low" if hi < p0 else "none")
        res["h"][h] = d
    return res


def build(df):
    """
    df: 日付昇順/降順どちらでも可（列: _dt, 終値, 高値, 安値）。
    戻り値: dict(ok, why, n_days, d0, d1, base, today, groups)
      groups = [{"name", "rows":[{"label","today":bool,"stat":...}]}]
    """
    if df is None or len(df) < 120 or "終値" not in df.columns:
        return {"ok": False, "why": "株価データが120営業日未満のため、過去実績は出せません。"}
    d = df.copy()
    d["_dt"] = pd.to_datetime(d["_dt"])
    d = d.sort_values("_dt").drop_duplicates("_dt").reset_index(drop=True)
    c = pd.to_numeric(d["終値"], errors="coerce").astype(float)
    if c.isna().mean() > 0.2:
        return {"ok": False, "why": "終値のデータに欠けが多いため、過去実績は出せません。"}
    c = c.ffill()
    has_hl = all(k in d.columns for k in ("高値", "安値")) and d["高値"].notna().mean() > 0.8
    h = pd.to_numeric(d["高値"], errors="coerce").astype(float).ffill() if has_hl else None
    l = pd.to_numeric(d["安値"], errors="coerce").astype(float).ffill() if has_hl else None

    fwd = {k: c.shift(-k) / c - 1.0 for k in HORIZONS}
    allm = pd.Series(True, index=c.index)
    base = {}
    for k in HORIZONS:
        v = fwd[k].dropna()
        base[k] = {"n": int(len(v)), "upp": float((v > 0).mean()) if len(v) else np.nan,
                   "mean": float(v.mean()) if len(v) else np.nan}

    rsi = _rsi(c)
    macd, sig, hist = _macd(c)
    pb = _pctb(c)
    ma25 = c.rolling(25).mean()
    dev = (c - ma25) / ma25 * 100
    dev_sd = float(dev.std()) if dev.notna().sum() > 50 else np.nan

    G = []        # (グループ名, [(ラベル, mask)])

    # RSI 水準
    z = [("30未満（売られすぎ）", rsi < 30), ("30〜45", (rsi >= 30) & (rsi < 45)),
         ("45〜55（中立）", (rsi >= 45) & (rsi < 55)), ("55〜70", (rsi >= 55) & (rsi < 70)),
         ("70以上（買われすぎ）", rsi >= 70)]
    G.append(("RSI（14日）の水準", z))
    dr = rsi.diff()
    sd = float(dr.std())
    G.append(("RSIの今日の変化", [(f"大きく低下（{-sd:.1f}pt以下）", dr <= -sd), ("小幅", dr.abs() < sd),
                               (f"大きく上昇（+{sd:.1f}pt以上）", dr >= sd)]))
    # MACD
    dh = hist.diff()
    cross_up = (macd > sig) & (macd.shift() <= sig.shift())
    cross_dn = (macd < sig) & (macd.shift() >= sig.shift())
    G.append(("MACD（ヒストグラム）", [
        ("プラス圏で拡大中", (hist > 0) & (dh > 0)), ("プラス圏で縮小中", (hist > 0) & (dh <= 0)),
        ("マイナス圏で縮小中（底打ち気味）", (hist < 0) & (dh > 0)), ("マイナス圏で拡大中", (hist < 0) & (dh <= 0)),
        ("今日ゴールデンクロス", cross_up), ("今日デッドクロス", cross_dn)]))
    # %b
    G.append(("ボリンジャー%b", [
        ("下限の外（0未満）", pb < 0), ("0〜0.2（下限近く）", (pb >= 0) & (pb < 0.2)),
        ("0.2〜0.8（中ほど）", (pb >= 0.2) & (pb < 0.8)), ("0.8〜1（上限近く）", (pb >= 0.8) & (pb <= 1)),
        ("上限の外（1超）", pb > 1)]))
    # ADX
    if has_hl:
        adx, pdi, mdi = _adx(h, l, c)
        upd = pdi > mdi
        rows = []
        for nm, mk in [("弱（20未満）", adx < 20), ("中（20〜25）", (adx >= 20) & (adx < 25)), ("強（25以上）", adx >= 25)]:
            rows.append((f"トレンド{nm}・買い方向優勢", mk & upd))
            rows.append((f"トレンド{nm}・売り方向優勢", mk & ~upd))
        G.append(("ADX／DMI（トレンドの強さ）", rows))
    else:
        adx = None
    # 乖離率
    if dev_sd == dev_sd and dev_sd > 0:
        a, b = 0.5 * dev_sd, 1.5 * dev_sd
        G.append(("25日乖離率", [
            (f"{-b:.1f}%未満（大きく下に離れ）", dev < -b), (f"{-b:.1f}〜{-a:.1f}%", (dev >= -b) & (dev < -a)),
            (f"{-a:.1f}〜{a:.1f}%（平均付近）", (dev >= -a) & (dev <= a)), (f"{a:.1f}〜{b:.1f}%", (dev > a) & (dev <= b)),
            (f"{b:.1f}%超（大きく上に離れ）", dev > b)]))
    # ダイバージェンス
    dv_rb = _divergence(c, rsi, "bear", 2.0, peak_min=55)
    dv_rl = _divergence(c, rsi, "bull", 2.0, peak_min=45)
    dv_mb = _divergence(c, hist, "bear", 0.0, peak_min=0)
    dv_ml = _divergence(c, hist, "bull", 0.0, peak_min=0)
    G.append(("ダイバージェンス（株価と指標の逆行）", [
        ("弱気（株価は新高値・RSIは低下）", dv_rb), ("強気（株価は新安値・RSIは上昇）", dv_rl),
        ("弱気（株価は新高値・MACDは縮小）", dv_mb), ("強気（株価は新安値・MACDは縮小から回復）", dv_ml)]))
    # 組み合わせ（種類の違うサインが同時に出た数）
    zdev = dev / dev_sd if (dev_sd == dev_sd and dev_sd > 0) else pd.Series(0.0, index=c.index)
    bear = ((rsi >= 70).astype(int) + (pb > 1).astype(int) + (zdev >= 1.5).astype(int)
            + (dv_rb | dv_mb).astype(int) + ((hist > 0) & (dh < 0)).astype(int))
    bull = ((rsi < 30).astype(int) + (pb < 0).astype(int) + (zdev <= -1.5).astype(int)
            + (dv_rl | dv_ml).astype(int) + ((hist < 0) & (dh > 0)).astype(int))
    G.append(("複数サイン（弱気）",
              [("弱気サイン 2個以上", bear >= 2), ("弱気サイン 3個以上", bear >= 3)]))
    G.append(("複数サイン（強気）",
              [("強気サイン 2個以上", bull >= 2), ("強気サイン 3個以上", bull >= 3)]))

    groups = []
    for name, rows in G:
        out = []
        for label, mk in rows:
            mk = mk.fillna(False).astype(bool)
            out.append({"label": label, "today": bool(mk.iloc[-1]), "stat": _stat(mk, fwd, base)})
        groups.append({"name": name, "rows": out})
    return {"ok": True, "n_days": int(len(c)), "d0": d["_dt"].iloc[0], "d1": d["_dt"].iloc[-1],
            "base": base, "groups": groups,
            "today": {"rsi": float(rsi.iloc[-1]) if rsi.iloc[-1] == rsi.iloc[-1] else None,
                      "pctb": float(pb.iloc[-1]) if pb.iloc[-1] == pb.iloc[-1] else None,
                      "adx": float(adx.iloc[-1]) if adx is not None and adx.iloc[-1] == adx.iloc[-1] else None,
                      "dev": float(dev.iloc[-1]) if dev.iloc[-1] == dev.iloc[-1] else None,
                      "bear": int(bear.iloc[-1]), "bull": int(bull.iloc[-1])}}


# ───────── 表示 ─────────
_CSS = """<style>
.ss-w{overflow-x:auto;margin:4px 0 10px}
.ss-t{min-width:600px;width:100%;border-collapse:collapse;font-size:12px}
.ss-t td.l{min-width:150px}
.ss-t th{background:#1c2128;color:#8b949e;font-weight:600;padding:4px 6px;border:1px solid #30363d;text-align:center;white-space:nowrap}
.ss-t td{padding:4px 6px;border:1px solid #30363d;text-align:center;color:#c9d1d9;vertical-align:middle}
.ss-t td.l{text-align:left}
.ss-t tr.td td{background:#1f2937}
.ss-t small{color:#8b949e;font-size:10px}
.ss-h{font-size:13px;font-weight:700;color:#f0f6fc;margin:10px 0 2px}
.ss-note{font-size:11px;color:#8b949e;line-height:1.5}
</style>"""
_BLUE, _RED, _GRY = "#58a6ff", "#f85149", "#c9d1d9"


def _cell(d, base_p):
    if d["n"] == 0:
        return "<td>-</td>"
    col, w, mark = _GRY, "400", ""
    if d["sig"] == "high":
        col, w, mark = _BLUE, "800", "◎"
    elif d["sig"] == "low":
        col, w, mark = _RED, "800", "◎"
    return (f"<td><span style='color:{col};font-weight:{w}'>↑{d['upp']*100:.0f}%{mark}</span>"
            f"<br><small>{d['up']}/{d['n']}件</small></td>")


def _table(rows, base, only_today=False, group=None):
    h = ["状態", "件数<br><small>(別事例)</small>"] + [HLAB[k] + "<br><small>上昇率</small>" for k in HORIZONS] + ["20日後<br><small>平均変化</small>"]
    s = "<table class='ss-t'><tr>" + "".join(f"<th>{x}</th>" for x in h) + "</tr>"
    s += ("<tr><td class='l'><b>通常の日（全期間）</b></td><td>-</td>" +
          "".join(f"<td><b>↑{base[k]['upp']*100:.0f}%</b><br><small>{base[k]['n']:,}日</small></td>" for k in HORIZONS) +
          f"<td>{base[20]['mean']*100:+.1f}%</td></tr>")
    for r in rows:
        if only_today and not r["today"]:
            continue
        st_ = r["stat"]
        ref = " <small style='color:#d29922'>参考</small>" if st_["n"] < MIN_N else ""
        cls = " class='td'" if r["today"] else ""
        mark = "◀今日 " if r["today"] else ""
        lab = (f"<b style='color:#f0f6fc'>{r['label']}</b>" if r["today"] else r["label"])
        m20 = st_["h"][20]["mean"]
        m20s = f"{m20*100:+.1f}%" if m20 == m20 else "-"
        s += (f"<tr{cls}><td class='l'>{mark}{lab}{ref}</td><td>{st_['n']}<br><small>({st_['ep']}回)</small></td>" +
              "".join(_cell(st_["h"][k], base[k]["upp"]) for k in HORIZONS) + f"<td>{m20s}</td></tr>")
    return "<div class='ss-w'>" + s + "</table></div>"


def render(code, info):
    import streamlit as st
    df = info.get("price_long")
    if df is None or len(df) == 0:
        df = info.get("price")
    try:
        r = build(df)
    except Exception as e:
        st.caption(f"過去実績の計算でエラー: {e}")
        return
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown("<div class='ui-sec' style='font-size:18px;font-weight:800;color:#f0f6fc;border-left:5px solid #388bfd;"
                "padding:2px 0 2px 10px;margin:18px 0 8px'>📊 今日のサインと過去の実績</div>", unsafe_allow_html=True)
    if not r["ok"]:
        st.caption(r["why"])
        return
    t = r["today"]
    st.markdown(
        f"<div class='ss-note'>この銘柄の過去 <b>{r['n_days']:,}営業日</b>（{r['d0']:%Y/%m/%d}〜{r['d1']:%Y/%m/%d}）で、"
        f"<b>今日と同じ状態だった日</b>の、その後の上昇率です。「通常の日」と並べて見てください。<br>"
        f"<b>◎</b>＝通常の日との差が、偶然とは考えにくい（青=通常より上がりやすい／赤=下がりやすい）。"
        f"先の日数が重なる分は割り引いて判定しているため、5日後・10日後・20日後は◎が付きにくくなります。"
        f"<b>参考</b>＝件数が30件未満。（○回）＝5日以上あけた別々の事例の数。"
        f"表示数が多いため、偶然の◎が少し混ざります。<b>複数の期間・指標で同じ向きが揃うか</b>を重視してください。"
        f"過去の頻度であり、将来を保証するものではありません。<br>"
        f"複数サインの内訳（各5項目）: 弱気＝RSI70以上／%b1超／乖離が+1.5σ超／弱気ダイバージェンス／MACDのプラス圏で縮小。"
        f"強気＝RSI30未満／%b0未満／乖離が−1.5σ未満／強気ダイバージェンス／MACDのマイナス圏で縮小。</div>", unsafe_allow_html=True)
    # 今日の状態だけの一覧
    today_rows = []
    for g in r["groups"]:
        for row in g["rows"]:
            if row["today"]:
                today_rows.append((g["name"], row))
    if today_rows:
        html = _table([x[1] for x in today_rows], r["base"])
        for gname, row in today_rows:        # 状態名の上に、どの指標かを小さく添える
            html = html.replace(f"<b style='color:#f0f6fc'>{row['label']}</b>",
                                f"<small>{gname}</small><br><b style='color:#f0f6fc'>{row['label']}</b>", 1)
        st.markdown(html, unsafe_allow_html=True)
    st.caption(f"今日同時に出ているサイン: 弱気 {t['bear']}個 / 強気 {t['bull']}個（各5項目）。")
    with st.expander("すべての状態の実績を見る（指標ごと）"):
        for g in r["groups"]:
            st.markdown(f"<div class='ss-h'>{g['name']}</div>", unsafe_allow_html=True)
            st.markdown(_table(g["rows"], r["base"]), unsafe_allow_html=True)
