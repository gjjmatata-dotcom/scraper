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


# ───────── 転換サインの段階表示（画面の一番上のバナー）─────────
#   ① 敏感サイン（早いが、だましも多い）が2つ以上 → 薄い色
#   ② ①に加えて、確認サイン（遅いが、確からしい）が1つ以上 → 濃い色（2つ以上で最も濃い）
#   ③ ②の後に、実際に株価が予想の方向へ動いた（日次ボラの2倍以上）→ 塗りつぶし
#   外れ: ②の後に、逆方向へ日次ボラの2倍以上動いた → グレー
SENS_WIN, CONF_WIN, CONFIRM_WIN, MOVE_K, SHOW_WIN = 5, 3, 10, 1.5, 7
K_OPTIONS = [0.5, 1.0, 1.5, 1.7, 2.0, 2.5, 3.0]


def _sar(h, l, af0=0.02, afm=0.2):
    """パラボリックSAR（標準 0.02/0.2）。戻り値: (sar, 上昇中かどうか)"""
    h, l = h.values, l.values
    n = len(h)
    sar = np.zeros(n)
    bull = np.ones(n, dtype=bool)
    sar[0], ep, af, up = l[0], h[0], af0, True
    for i in range(1, n):
        v = sar[i - 1] + af * (ep - sar[i - 1])
        if up:
            v = min(v, l[i - 1], l[i - 2] if i >= 2 else l[i - 1])
            if l[i] < v:
                up, v, ep, af = False, ep, l[i], af0
            elif h[i] > ep:
                ep, af = h[i], min(af + af0, afm)
        else:
            v = max(v, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > v:
                up, v, ep, af = True, ep, h[i], af0
            elif l[i] < ep:
                ep, af = l[i], min(af + af0, afm)
        sar[i], bull[i] = v, up
    return sar, bull


def _sticky(flag, w):
    return flag.astype(int).rolling(w, min_periods=1).max().astype(bool)


def _ago(flag, w):
    """直近w日以内で最後に点灯した日の「何日前か」(0=今日)。無ければ None。"""
    v = flag.values[-w:]
    idx = np.where(v)[0]
    return None if len(idx) == 0 else int(len(v) - 1 - idx[-1])


def _make_items(df):
    """価格データから、敏感サイン・確認サインの日次の点灯表を作る。戻り値: (ok, why, d, c, items)"""
    if df is None or len(df) < 120 or "終値" not in df.columns:
        return False, "株価データが120営業日未満", None, None, None
    d = df.copy()
    d["_dt"] = pd.to_datetime(d["_dt"])
    d = d.sort_values("_dt").drop_duplicates("_dt").reset_index(drop=True)
    c = pd.to_numeric(d["終値"], errors="coerce").astype(float).ffill()
    if c.isna().mean() > 0.2:
        return False, "終値の欠けが多い", None, None, None
    has_hl = all(k in d.columns for k in ("高値", "安値")) and d["高値"].notna().mean() > 0.8
    h = pd.to_numeric(d["高値"], errors="coerce").astype(float).ffill() if has_hl else None
    l = pd.to_numeric(d["安値"], errors="coerce").astype(float).ffill() if has_hl else None

    rsi = _rsi(c)
    macd, sig, hist = _macd(c)
    pb = _pctb(c)
    ma25 = c.rolling(25).mean()
    dev = (c - ma25) / ma25 * 100
    dsd = float(dev.std()) if dev.notna().sum() > 50 else np.nan
    zdev = dev / dsd if (dsd == dsd and dsd > 0) else pd.Series(0.0, index=c.index)
    dv = {("bear", "rsi"): _divergence(c, rsi, "bear", 2.0, peak_min=55),
          ("bull", "rsi"): _divergence(c, rsi, "bull", 2.0, peak_min=45),
          ("bear", "macd"): _divergence(c, hist, "bear", 0.0, peak_min=0),
          ("bull", "macd"): _divergence(c, hist, "bull", 0.0, peak_min=0)}
    cross_dn = (macd < sig) & (macd.shift() >= sig.shift())
    cross_up = (macd > sig) & (macd.shift() <= sig.shift())
    ma_dn = (c < ma25) & (c.shift() >= ma25.shift())
    ma_up = (c > ma25) & (c.shift() <= ma25.shift())
    _, sbull = _sar(h, l) if has_hl else (None, None)
    if has_hl:
        sb = pd.Series(sbull, index=c.index)
        sar_dn, sar_up = (~sb) & sb.shift().fillna(True).astype(bool), sb & (~sb.shift().fillna(False).astype(bool))
        _, pdi, mdi = _adx(h, l, c)
        di_dn = (mdi > pdi) & (mdi.shift() <= pdi.shift())
        di_up = (pdi > mdi) & (pdi.shift() <= mdi.shift())

    items = {
        "bear": {
            "sens": [("RSI 70以上", rsi >= 70), ("%bが上限の外", pb > 1), ("25日乖離が+1.5σ超", zdev >= 1.5),
                     ("弱気ダイバージェンス(RSI)", dv[("bear", "rsi")]), ("弱気ダイバージェンス(MACD)", dv[("bear", "macd")])],
            "conf": [("MACDデッドクロス", cross_dn)]
                    + ([("SAR陰転", sar_dn), ("DMI 売り方向に逆転", di_dn)] if has_hl else [])},
        "bull": {
            "sens": [("RSI 30未満", rsi < 30), ("%bが下限の外", pb < 0), ("25日乖離が−1.5σ未満", zdev <= -1.5),
                     ("強気ダイバージェンス(RSI)", dv[("bull", "rsi")]), ("強気ダイバージェンス(MACD)", dv[("bull", "macd")])],
            "conf": [("MACDゴールデンクロス", cross_up)]
                    + ([("SAR陽転", sar_up), ("DMI 買い方向に逆転", di_up)] if has_hl else [])}}

    return True, "", d, c, items


def status(df, K=None):
    """
    転換サインの現在の段階（弱気=天井警戒 / 強気=底打ち期待）と、同じ条件の過去実績を返す。
    戻り値: {ok, why, dirs:{"bear":{...},"bull":{...}}}
    """
    K = MOVE_K if K is None else K
    ok, why, d, c, items = _make_items(df)
    if not ok:
        return {"ok": False, "why": why}
    ret = c.pct_change()
    sd_d = ret.rolling(60, min_periods=30).std()
    fwd = {k: c.shift(-k) / c - 1.0 for k in (5, 10)}
    base_dn = {k: float((fwd[k].dropna() < 0).mean()) for k in (5, 10)}
    base_up = {k: float((fwd[k].dropna() > 0).mean()) for k in (5, 10)}
    out = {}
    for dr in ("bear", "bull"):
        sgn = -1.0 if dr == "bear" else 1.0
        S = sum(_sticky(m, SENS_WIN).astype(int) for _, m in items[dr]["sens"])
        C = sum(_sticky(m, CONF_WIN).astype(int) for _, m in items[dr]["conf"])
        L1 = S >= 2
        L2 = L1 & (C >= 1)
        # ③ 確認: 直近CONFIRM_WIN日以内の②点灯日から、予想方向へ日次ボラの2倍以上動いた
        L3 = pd.Series(False, index=c.index)
        MISS = pd.Series(False, index=c.index)
        for k in range(0, CONFIRM_WIN + 1):
            mv = sgn * (c / c.shift(k) - 1.0) if k > 0 else pd.Series(0.0, index=c.index)
            thr = K * sd_d * math.sqrt(max(k, 1))
            L3 |= L2.shift(k, fill_value=False) & (mv >= thr) & (k > 0)
            MISS |= L2.shift(k, fill_value=False) & (mv <= -thr) & (k > 0)
        MISS &= ~L3
        recent = _sticky(L2, SHOW_WIN)
        t = len(c) - 1
        if bool(L3.iloc[t]):
            lv = 3
        elif bool(MISS.iloc[t]):
            lv = -1
        elif bool(recent.iloc[t]):
            lv = 2
        elif bool(L1.iloc[t]):
            lv = 1
        else:
            lv = 0
        # 点灯日（直近のL2）と、そこからの動き
        sdate, move, nd = None, None, None
        v = L2.values[-(CONFIRM_WIN + 1):]
        idx = np.where(v)[0]
        if len(idx):
            k = len(v) - 1 - idx[-1]
            sdate = d["_dt"].iloc[t - k]
            move = float(c.iloc[t] / c.iloc[t - k] - 1.0) * 100
            nd = int(k)
        # 各段階（①/②/③）の過去実績
        def _st(m):
            mm = m.values & fwd[5].notna().values
            st_ = {}
            for hh in (5, 10):
                r = fwd[hh].values
                ok = m.values & ~np.isnan(r)
                n = int(ok.sum())
                k_ = int((r[ok] < 0).sum()) if dr == "bear" else int((r[ok] > 0).sum())
                st_[hh] = {"n": n, "k": k_, "p": (k_ / n if n else np.nan),
                           "base": base_dn[hh] if dr == "bear" else base_up[hh]}
            st_["ep"] = _episodes(np.where(mm)[0])
            return st_
        stats_by = {1: _st(L1), 2: _st(L2), 3: _st(L3)}
        stats = dict(stats_by[lv if lv > 0 else 2])
        stats["lv_used"] = lv if lv > 0 else 2
        lit_s = [(nm, _ago(f, SENS_WIN)) for nm, f in items[dr]["sens"] if _ago(f, SENS_WIN) is not None]
        lit_c = [(nm, _ago(f, CONF_WIN)) for nm, f in items[dr]["conf"] if _ago(f, CONF_WIN) is not None]
        out[dr] = {"level": lv, "sens": lit_s, "conf": lit_c, "sdate": sdate, "move": move, "days": nd,
                   "stats": stats, "stats_by": stats_by, "s_now": bool(L1.iloc[t]), "n_sens_total": len(items[dr]["sens"]), "n_conf_total": len(items[dr]["conf"]),
                   "thr_pct": float(K * (sd_d.iloc[t] if sd_d.iloc[t] == sd_d.iloc[t] else 0.0) * 100)}
    return {"ok": True, "dirs": out, "d1": d["_dt"].iloc[-1]}



def calibration(df, ks=None):
    """
    ③「実際に動いた」の基準（日次ボラの何倍か）を変えると、どうなるかを過去の②点灯で数える。
      事例 = ②が新しく点灯した日（連続は1回と数える）
      確認 = 10営業日以内に、予想方向へ K×日次ボラ×√日数 以上動いた（先に起きた方を採用）
      外れ = 先に逆方向へ同じだけ動いた ／ 未決 = 10日以内にどちらにも動かなかった
      確認後の継続 = 確認した日の終値から、さらに5営業日後も予想方向だった割合
    """
    ks = ks or K_OPTIONS
    ok, why, d, c, items = _make_items(df)
    if not ok:
        return None
    ret = c.pct_change()
    sd = ret.rolling(60, min_periods=30).std().values
    cv = c.values
    n = len(cv)
    ev = []      # (開始日index, 向き符号)
    for dr, sgn in (("bear", -1.0), ("bull", 1.0)):
        S = sum(_sticky(m, SENS_WIN).astype(int) for _, m in items[dr]["sens"])
        C = sum(_sticky(m, CONF_WIN).astype(int) for _, m in items[dr]["conf"])
        L2 = ((S >= 2) & (C >= 1)).values
        first = L2 & ~np.concatenate([[False], L2[:-1]])
        for s0 in np.where(first)[0]:
            if s0 + CONFIRM_WIN < n:            # 10日先まで観測できる事例だけ
                ev.append((int(s0), sgn))
    rows = []
    for K in ks:
        conf = miss = pend = 0
        days, cont, cont_n = [], 0, 0
        for s0, sgn in ev:
            res = None
            for j in range(1, CONFIRM_WIN + 1):
                mv = sgn * (cv[s0 + j] / cv[s0] - 1.0)
                thr = K * sd[s0 + j] * math.sqrt(j) if sd[s0 + j] == sd[s0 + j] else np.inf
                if mv >= thr:
                    res = ("c", j); break
                if mv <= -thr:
                    res = ("m", j); break
            if res is None:
                pend += 1
            elif res[0] == "c":
                conf += 1
                days.append(res[1])
                t = s0 + res[1]
                if t + 5 < n:
                    cont_n += 1
                    cont += int(sgn * (cv[t + 5] / cv[t] - 1.0) > 0)
            else:
                miss += 1
        N = len(ev)
        rows.append({"K": K, "n": N, "conf": conf, "miss": miss, "pend": pend,
                     "med_days": (float(np.median(days)) if days else None),
                     "cont": cont, "cont_n": cont_n})
    return rows

_BCSS = """<style>
.sb{border:1px solid #30363d;border-radius:10px;background:#0d1117;padding:10px 12px;margin:6px 0 10px}
.sb-ttl{font-size:13px;font-weight:800;color:#f0f6fc;margin-bottom:6px}
.sb-ttl small{font-weight:500;color:#8b949e;margin-left:8px;font-size:11px}
.sb-row{display:flex;align-items:stretch;gap:8px;margin:6px 0;flex-wrap:wrap}
.sb-name{min-width:96px;font-size:13px;font-weight:800;display:flex;align-items:center}
.sb-pill{flex:1;min-width:92px;border-radius:6px;padding:5px 7px;font-size:11px;text-align:center;border:1px solid #30363d;color:#6e7681;background:#161b22;line-height:1.35}
.sb-pill b{display:block;font-size:12px}
.sb-det{font-size:11px;color:#8b949e;line-height:1.55;margin:0 0 2px 2px}
.sb-det b{color:#c9d1d9}
.sb-chip{display:inline-block;border-radius:6px;padding:3px 8px;margin:2px 6px 2px 0;font-size:12px;border:1px solid #30363d;line-height:1.4}
.sb-chip small{font-size:10px;opacity:.9}
.sb-perf{margin:4px 0 2px}
.sb-cond{font-size:11px;color:#8b949e;display:block;margin-bottom:1px}
</style>"""


def _rgb(dr):
    return "248,81,73" if dr == "bear" else "88,166,255"


def _count_style(dr, step, n, s_ok=True):
    """① ② のピル。点灯数が多いほど濃くする。②は①が未成立なら薄い破線（確認サインだけ点灯）。"""
    rgb = _rgb(dr)
    if step == 1:
        a = {0: 0.0, 1: 0.07, 2: 0.22, 3: 0.40, 4: 0.58, 5: 0.74}.get(n, 0.74)
    else:
        a = {0: 0.0, 1: 0.40, 2: 0.62, 3: 0.80}.get(n, 0.80)
        if n >= 1 and not s_ok:
            a = 0.12
    if a <= 0:
        return ""
    dashed = "border-style:dashed;" if (step == 1 and n < 2) or (step == 2 and not s_ok) else ""
    fg = "#ffffff" if a >= 0.38 else ("#ffb4ae" if dr == "bear" else "#a5d0ff")
    return f"background:rgba({rgb},{a});border-color:rgba({rgb},{min(1.0, a + .35)});{dashed}color:{fg}"


def _step3_style(dr, level):
    rgb = _rgb(dr)
    if level == 3:
        return f"background:rgba({rgb},.92);border-color:rgba({rgb},1);color:#fff"
    if level == -1:
        return "background:#21262d;color:#8b949e;border-color:#484f58"
    if level == 2:
        return f"border-color:rgba({rgb},.6);color:#c9d1d9"
    return ""


def _dots(n, total):
    return "●" * n + "○" * (total - n)


def _chip(dr, h, s_, ref):
    """過去実績の1期間分。通常の日との差(pt)で色を付ける: 差が大きい=方向の濃い色／差なし=グレー／逆=橙。"""
    what = "下落" if dr == "bear" else "上昇"
    if s_["n"] == 0:
        return f"<span class='sb-chip' style='color:#6e7681'>{h}日後 該当なし</span>"
    p, base = s_["p"] * 100, s_["base"] * 100
    edge = p - base
    rgb = _rgb(dr)
    if edge >= 10:
        st, tag = f"background:rgba({rgb},.55);color:#fff;font-weight:800;border-color:rgba({rgb},.9)", "通常より大きく高い"
    elif edge >= 5:
        st, tag = f"background:rgba({rgb},.26);color:#e6edf3;border-color:rgba({rgb},.6)", "やや高い"
    elif edge > -5:
        st, tag = "background:#21262d;color:#8b949e", "差なし"
    else:
        st, tag = "background:rgba(210,153,34,.28);color:#f0c674;border-color:#d29922", "逆に出ている"
    if ref:
        st += ";border-style:dashed;opacity:.85"
    return (f"<span class='sb-chip' style='{st}'>{h}日後 {what}<b>{p:.0f}%</b> "
            f"<small>{s_['k']}/{s_['n']}件・通常{base:.0f}%・{edge:+.0f}pt　{tag}</small></span>")


def banner(code, info):
    import streamlit as st
    df = info.get("price_long")
    if df is None or len(df) == 0:
        df = info.get("price")
    try:
        r = status(df, K=float(st.session_state.get(f"ss_move_k_{code}", MOVE_K)))
    except Exception as e:
        st.caption(f"転換サインの計算でエラー: {e}")
        return
    if not r["ok"]:
        return
    html = _BCSS + "<div class='sb'><div class='sb-ttl'>🎯 転換サイン<small>" \
        f"{r['d1']:%m/%d}の終値時点　①敏感サイン(早いがだましも多い) → ②確認サイン(遅いが確か) → ③実際に動いた</small></div>"
    names = {"bear": ("🔴 天井警戒", "売り転換の兆し"), "bull": ("🔵 底打ち期待", "買い転換の兆し")}
    for dr in ("bear", "bull"):
        x = r["dirs"][dr]
        lv = x["level"]
        sens = "、".join(f"{n}({'今日' if a == 0 else str(a) + '日前'})" for n, a in x["sens"]) or "なし"
        conf = "、".join(f"{n}({'今日' if a == 0 else str(a) + '日前'})" for n, a in x["conf"]) or "なし"
        mv = x["move"]
        step3 = ("外れ" if lv == -1 else ("確認済み" if lv == 3 else "待ち"))
        mv_txt = (f"<br>点灯日{x['sdate']:%m/%d}から{mv:+.1f}%" if mv is not None and x["sdate"] is not None else "")
        ns, nc = len(x["sens"]), len(x["conf"])
        s_ok = ns >= 2
        pills = (
            f"<div class='sb-pill' style='{_count_style(dr, 1, ns)}'><b>① 敏感サイン {ns}/{x['n_sens_total']}個</b>"
            f"{_dots(ns, x['n_sens_total'])}{'　（2個以上で点灯）' if ns < 2 else ''}</div>"
            f"<div class='sb-pill' style='{_count_style(dr, 2, nc, s_ok)}'><b>② 確認サイン {nc}/{x['n_conf_total']}個</b>"
            f"{_dots(nc, x['n_conf_total'])}{('　（①が2個以上のとき有効）' if (nc and not s_ok) else '')}</div>"
            f"<div class='sb-pill' style='{_step3_style(dr, lv)}'><b>③ 実際に動いた：{step3}</b>"
            f"{('±' + format(x['thr_pct'], '.1f') + '%で確認') if lv in (2,) else ''}{mv_txt if lv in (2, 3, -1) else ''}</div>")
        html += (f"<div class='sb-row'><div class='sb-name' style='color:{'#f85149' if dr == 'bear' else '#58a6ff'}'>"
                 f"{names[dr][0]}</div>{pills}</div>")
        # 詳細と過去実績
        det = f"①の内訳: <b>{sens}</b><br>②の内訳: <b>{conf}</b>"
        what = "下落" if dr == "bear" else "上昇"
        show = []
        if x["s_now"]:
            show.append((1, "今日の①と同じ状態＝敏感サイン2個以上"))
        if lv == 3:
            show.append((3, "②のあと予想方向に動いた"))
        elif lv in (2, -1):
            show.append((2, "②点灯時と同じ状態＝敏感サイン2個以上＋確認サイン"))
        for lvu, cond in show:
            stt = x["stats_by"][lvu]
            ref = stt[5]["n"] < MIN_N or stt["ep"] < 20
            chips = "".join(_chip(dr, hh, stt[hh], ref) for hh in (5, 10))
            det += (f"<div class='sb-perf'><span class='sb-cond'>過去実績［{cond}］　別事例{stt['ep']}回"
                    f"{'　<span style=color:#d29922>参考(件数少)</span>' if ref else ''}</span>{chips}</div>")
        if not show and lv == 0:
            pass
        if lv == 0 and not x["s_now"]:
            det += "<br>今は点灯していません。"
        if lv == -1:
            det += "<br>※②の点灯後に、逆方向へ大きく動きました（今回のサインは外れ）。"
        html += f"<div class='sb-det'>{det}</div>"
    html += ("<div class='sb-det' style='margin-top:6px'>色が濃いほど確からしい段階です。"
             f"③は日次ボラティリティの{float(st.session_state.get(f'ss_move_k_{code}', MOVE_K)):g}倍以上（日数が増えるほど基準も大きくなります）、予想方向へ動いた時に点灯します。基準は『📊 今日のサインと過去の実績』の下で変えられます。過去の頻度であり、将来を保証するものではありません。</div></div>")
    st.markdown(html, unsafe_allow_html=True)


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
    with st.expander("③の基準を比べる・変える（日次ボラの何倍で『実際に動いた』とするか）"):
        key = f"ss_move_k_{code}"
        cur = float(st.session_state.get(key, MOVE_K))
        st.selectbox("基準（日次ボラの何倍）", K_OPTIONS, index=(K_OPTIONS.index(cur) if cur in K_OPTIONS else K_OPTIONS.index(MOVE_K)),
                     key=key, help="小さくすると③が点灯しやすくなりますが、小さな値動きでも「確認済み」になります。")
        try:
            cal = calibration(info.get("price_long") if info.get("price_long") is not None and len(info.get("price_long")) else info.get("price"))
        except Exception as e:
            cal = None
            st.caption(f"計算でエラー: {e}")
        if cal:
            h = ["基準<br><small>(ボラの○倍)</small>", "②の事例", "確認<br><small>予想どおり</small>", "外れ<br><small>逆に動いた</small>",
                 "未決<br><small>10日動かず</small>", "確認まで<br><small>中央値</small>", "確認後5日も<br><small>同方向</small>"]
            t = "<div class='ss-w'><table class='ss-t'><tr>" + "".join(f"<th>{x}</th>" for x in h) + "</tr>"
            for x in cal:
                n = max(x["n"], 1)
                sel = " class='td'" if abs(x["K"] - float(st.session_state.get(key, MOVE_K))) < 1e-9 else ""
                cont = f"{x['cont']}/{x['cont_n']}件（{x['cont']/x['cont_n']*100:.0f}%）" if x["cont_n"] else "-"
                md = f"{x['med_days']:.0f}日" if x["med_days"] is not None else "-"
                t += (f"<tr{sel}><td><b>{x['K']:g}倍</b></td><td>{x['n']}</td>"
                      f"<td>{x['conf']}件（{x['conf']/n*100:.0f}%）</td><td>{x['miss']}件（{x['miss']/n*100:.0f}%）</td>"
                      f"<td>{x['pend']}件（{x['pend']/n*100:.0f}%）</td><td>{md}</td><td>{cont}</td></tr>")
            t += "</table></div>"
            st.markdown(t, unsafe_allow_html=True)
            st.caption("②が点灯した過去の各事例を、その後10営業日見た結果です。基準を小さくすると『確認』は増えますが、"
                       "『外れ』も増え、確認の意味が薄くなります。「確認」が多く「外れ」が少なく、「確認後5日も同方向」が高い基準が良い設定です。"
                       "事例が少ない銘柄は参考程度に見てください。")
        else:
            st.caption("データが足りないため比較できません。")
    with st.expander("すべての状態の実績を見る（指標ごと）"):
        for g in r["groups"]:
            st.markdown(f"<div class='ss-h'>{g['name']}</div>", unsafe_allow_html=True)
            st.markdown(_table(g["rows"], r["base"]), unsafe_allow_html=True)
