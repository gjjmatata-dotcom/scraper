"""
rule_miner.py ― 実データから「勝率の高い条件」を自動で探す統計エンジン（AI/API/課金は不要）

方針（再現性のために）
  ・同じデータなら必ず同じ結果（乱数は固定シード）。
  ・未来のデータを使わない：信用残・市場指標は「公表されて見られる日」まで遅らせて結合（LAG_*）。
  ・前半(2/3)で条件を探し、後半(1/3)で当たるか確かめる。
  ・「条件をたくさん試せば偶然60%超が出る」問題は、予測を時間方向にずらした“ニセの答え”で
    同じ探索を200回やり、偶然で出る水準と比べて p値（偶然確率）を出す。
  ・指標は候補約20個からデータで7つ（似た指標の重複は避ける）に絞る。
"""
import itertools
import math

import numpy as np
import pandas as pd

SEED = 20260101
N_PERM = 200
HORIZONS = (1, 3, 5)
MIN_ROWS_RULES = 100          # ルール探索に必要な最低営業日数
N_SELECT = 7                  # 使う指標の数
WIN_MIN = 0.60                # 勝率の下限
CORR_CAP = 0.92               # ほぼ同じ動きの指標だけ片方にする

# 公表ラグ（営業日）：その日の時点で実際に見られるデータだけを使うため
LAG_VIX = 1       # 米国の終値は日本の翌営業日朝に確定
LAG_MKT_DAILY = 1  # JPX日次・空売り比率
LAG_MKT_WEEKLY = 3  # 週次の信用評価損益（翌週水曜公表）
LAG_MARGIN = 2    # 個別の信用残（株探・週次）
LAG_SEIDO = 1     # JPX制度信用（個別・日次）
LAG_LEND = 1      # 貸借（日証金）

FEATURE_NOTE = {
    "RSI": "RSI(14)", "MACDヒスト": "MACDヒストグラム", "SAR乖離率(%)": "(終値−SAR)÷SAR",
    "%B": "ボリンジャー%B(×100)", "25日乖離率(%)": "25日平均線からの乖離", "前日比(%)": "前日比",
    "出来高比": "出来高÷20日平均", "ADX": "ADX(14)", "5日騰落(%)": "5営業日前比", "20日騰落(%)": "20営業日前比",
    "連騰連落日数": "連騰＋/連落−", "MACDヒスト連続上昇": "ヒストの上昇＋/下降−連続日数",
    "信用倍率": "信用倍率(株探)", "制度信用倍率": "制度信用倍率(JPX)", "貸借倍率": "貸借倍率",
    "信用買い残増減率(%)": "信用買い残の前週比", "信用売り残増減率(%)": "信用売り残の前週比", "逆日歩": "逆日歩",
    "VIX MACDヒスト": "VIXのMACDヒスト", "VIX GC後5日": "VIXのMACDがGC後5日以内(1/0)",
    "VIX DC後5日": "VIXのMACDがDC後5日以内(1/0)",
    "市場 信用評価損益率(%)": "市場全体の信用評価損益率", "市場 空売り比率(%)": "市場全体の空売り比率合計",
    "市場 売り残金額(億円)": "市場全体の信用売り残(一般+制度)", "市場 信用倍率": "市場全体の信用倍率",
}


# ───────────────────────── 特徴量づくり ─────────────────────────
def _asof_series(dates, s, tol_days=14):
    """s.index=『その値を見られる日』。dates の各日に対し、その日以前で最も新しい値を返す。"""
    dates = pd.DatetimeIndex(dates).normalize()
    if s is None or len(s) == 0:
        return np.full(len(dates), np.nan)
    s = pd.Series(pd.to_numeric(s, errors="coerce").values, index=pd.DatetimeIndex(s.index).normalize()).dropna()
    if s.empty:
        return np.full(len(dates), np.nan)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    left = pd.DataFrame({"_k": dates})
    order = np.argsort(left["_k"].values, kind="stable")
    left_sorted = left.iloc[order]
    right = pd.DataFrame({"_k": s.index, "v": s.values})
    m = pd.merge_asof(left_sorted, right, on="_k", direction="backward", tolerance=pd.Timedelta(days=tol_days))
    out = np.full(len(dates), np.nan)
    out[order] = m["v"].values
    return out


def _lag_index(idx, n):
    idx = pd.DatetimeIndex(idx).normalize()
    return idx + pd.offsets.BDay(n) if n else idx


def _col_series(df, col):
    """_dt列 or DatetimeIndex を持つ DataFrame から col を Series(index=日付) で返す。"""
    if df is None or len(df) == 0 or col not in df.columns:
        return None
    d = df
    if "_dt" in d.columns:
        d = d.set_index("_dt")
    s = pd.to_numeric(d[col], errors="coerce")
    s.index = pd.DatetimeIndex(d.index).normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def _signed_streak(flag_up, flag_dn):
    out = np.zeros(len(flag_up))
    cur = 0
    for i, (u, d) in enumerate(zip(flag_up, flag_dn)):
        if u:
            cur = cur + 1 if cur > 0 else 1
        elif d:
            cur = cur - 1 if cur < 0 else -1
        else:
            cur = 0
        out[i] = cur
    return out


def build_frame(price_long, M=None, L=None, MS=None, mkt=None, tech=None):
    """
    日付昇順の DataFrame（index=日付）。列: 終値 / 各特徴量 / fwd1,3,5,10,20 (%) / イベント列。
    tech を渡さなければ scraper.calc_technicals を使う。
    mkt = {"t1": 市場①表(index日付), "macd": VIX MACD表} か None。
    """
    p = price_long.sort_values("_dt").drop_duplicates("_dt", keep="last").reset_index(drop=True)
    if tech is None:
        from scraper import calc_technicals
        tech = calc_technicals(p.copy())
    t = tech.sort_values("_dt").reset_index(drop=True)
    dates = pd.DatetimeIndex(t["_dt"]).normalize()
    c = t["終値"].astype(float)
    F = pd.DataFrame(index=dates)
    F["終値"] = c.values
    ret = c.pct_change() * 100
    F["前日比(%)"] = ret.values
    ma25 = c.rolling(25).mean()
    F["25日乖離率(%)"] = ((c - ma25) / ma25 * 100).values
    F["RSI"] = t["RSI"].values if "RSI" in t else np.nan
    F["MACDヒスト"] = t["MACD_hist"].values if "MACD_hist" in t else np.nan
    if "SAR" in t:
        F["SAR乖離率(%)"] = ((c - t["SAR"]) / t["SAR"] * 100).values
    if "BB_%B" in t:
        F["%B"] = (t["BB_%B"] * 100).values
    F["ADX"] = t["ADX"].values if "ADX" in t else np.nan
    F["5日騰落(%)"] = (c.pct_change(5) * 100).values
    F["20日騰落(%)"] = (c.pct_change(20) * 100).values
    if "出来高" in t and t["出来高"].notna().any():
        v = t["出来高"].astype(float)
        F["出来高比"] = (v / v.rolling(20).mean()).values
    F["連騰連落日数"] = _signed_streak((ret > 0).values, (ret < 0).values)
    if "MACD_hist" in t:
        dh = t["MACD_hist"].diff()
        F["MACDヒスト連続上昇"] = _signed_streak((dh > 0).values, (dh < 0).values)
    F.iloc[:30, F.columns.get_indexer([x for x in F.columns if x not in ("終値", "前日比(%)", "連騰連落日数")])] = np.nan

    # ── 個別の需給（公表ラグを考慮） ──
    def add(df, col, name, lag):
        s = _col_series(df, col)
        if s is not None and s.notna().any():
            F[name] = _asof_series(dates, pd.Series(s.values, index=_lag_index(s.index, lag)))
    add(M, "信用倍率", "信用倍率", LAG_MARGIN)
    add(M, "買い残増減率", "信用買い残増減率(%)", LAG_MARGIN)
    add(M, "売り残増減率", "信用売り残増減率(%)", LAG_MARGIN)
    add(MS, "制度信用倍率", "制度信用倍率", LAG_SEIDO)
    add(L, "貸借倍率", "貸借倍率", LAG_LEND)
    add(L, "逆日歩", "逆日歩", LAG_LEND)

    # ── 市場全体 ──
    if mkt:
        macd = mkt.get("macd")
        if macd is not None and len(macd):
            hist = pd.to_numeric(macd["hist"], errors="coerce")
            eff = _lag_index(hist.index, LAG_VIX)
            F["VIX MACDヒスト"] = _asof_series(dates, pd.Series(hist.values, index=eff))
            for kind, name in (("GC", "VIX GC後5日"), ("DC", "VIX DC後5日")):
                ev = macd.index[macd["cross"] == kind]
                if len(ev):
                    e_eff = _lag_index(ev, LAG_VIX)
                    last = _asof_series(dates, pd.Series(e_eff.values.astype("int64").astype(float), index=e_eff), tol_days=60)
                    flag = (dates.values.astype("int64") - last) <= 7 * 86400e9
                    F[name] = np.where(np.isnan(last), np.nan, flag.astype(float))
        t1 = mkt.get("t1")
        if t1 is not None and len(t1):
            t1 = t1.sort_index()
            lag_row = np.where(t1["kind"].values == "週次", LAG_MKT_WEEKLY, LAG_MKT_DAILY)
            eff = pd.DatetimeIndex([i + pd.offsets.BDay(int(l)) for i, l in zip(pd.DatetimeIndex(t1.index).normalize(), lag_row)])
            for col, name in (("pl", "市場 信用評価損益率(%)"), ("short", "市場 空売り比率(%)"),
                              ("sell_k", "市場 売り残金額(億円)"), ("ratio", "市場 信用倍率")):
                if col in t1.columns and t1[col].notna().any():
                    F[name] = _asof_series(dates, pd.Series(t1[col].values, index=eff))

    # ── 先の騰落（答え） ──
    for h in (1, 3, 5, 10, 20):
        F[f"fwd{h}"] = (c.shift(-h) / c - 1).values * 100
    arr = c.values
    n = len(arr)
    mx = np.full(n, np.nan)
    mn = np.full(n, np.nan)
    for i in range(n - 10):
        w = arr[i + 1:i + 11]
        mx[i] = (w.max() / arr[i] - 1) * 100
        mn[i] = (w.min() / arr[i] - 1) * 100
    F["max10"], F["min10"] = mx, mn
    r1 = F["fwd1"]
    nxt = pd.concat([r1.shift(-k) for k in range(3)], axis=1)  # 翌日,翌々日,3日後の日次騰落
    valid3 = nxt.notna().all(axis=1)
    F["next3_up"] = np.where(valid3, (nxt > 0).all(axis=1).astype(float), np.nan)
    F["next3_dn"] = np.where(valid3, (nxt < 0).all(axis=1).astype(float), np.nan)
    return F


# ───────────────────────── 指標の選定 ─────────────────────────
def _feature_cols(F):
    skip = {"終値"}
    return [c for c in F.columns if c not in skip and not c.startswith(("fwd", "max", "min", "next3"))]


def _best_single(F, f, tr_rows):
    """前半で、その指標1つの条件（上位/下位10〜30%）が『上昇/下落』をどれだけ偏らせるか（最大のz値）を返す。"""
    conds, Mx = _make_singles(F, [f], tr_rows)
    if Mx.shape[1] == 0:
        return 0.0, ""
    zs, best = [], (0.0, "")
    for h in HORIZONS:
        fwd = F[f"fwd{h}"].to_numpy(float)
        ok = tr_rows[~np.isnan(fwd[tr_rows])]
        if len(ok) < 40:
            continue
        C = Mx[ok].astype(float)
        n = C.sum(0)
        zb = 0.0
        for d in ("up", "dn"):
            y = ((fwd > 0) if d == "up" else (fwd < 0))[ok].astype(float)
            b = y.mean()
            if b <= 0 or b >= 1:
                continue
            with np.errstate(invalid="ignore", divide="ignore"):
                z = (C.T @ y / n - b) / np.sqrt(b * (1 - b) / n)
            z[n < 8] = 0
            z = np.nan_to_num(z)
            j = int(np.argmax(z))
            if z[j] > zb:
                zb = float(z[j])
                if z[j] > best[0]:
                    op = conds[j][1]
                    best = (float(z[j]), ("高い" if op == "≥" else "低い") + "と" + ("上昇" if d == "up" else "下落") + "しやすい")
        zs.append(zb)
    return (float(np.mean(zs)) if zs else 0.0), best[1]


def select_features(F, tr_rows, n_select=N_SELECT, cover=0.7):
    """
    前半だけで、各指標の『一番偏りが大きい条件の強さ(z)』が大きい順に、似た指標を除いて選ぶ。
    （順位相関だけだと「極端なときだけ効く」指標を見逃すため、条件の偏りで測る）
    """
    cand = []
    Ftr = F.iloc[tr_rows]
    for f in _feature_cols(F):
        x = Ftr[f]
        if x.notna().mean() < cover or x.nunique() < 2:
            continue
        if set(x.dropna().unique().tolist()) <= {0.0, 1.0} and (x == 1).sum() < 5:
            continue
        z, way = _best_single(F, f, tr_rows)
        cand.append((f, z, way))
    cand.sort(key=lambda t: -t[1])
    chosen, info = [], []
    cm = Ftr[[c[0] for c in cand]].rank().corr() if cand else pd.DataFrame()
    for f, z, way in cand:
        if len(chosen) >= n_select:
            break
        if any(abs(cm.loc[f, g]) > CORR_CAP for g in chosen
               if f in cm.index and g in cm.columns and cm.loc[f, g] == cm.loc[f, g]):
            continue
        chosen.append(f)
        info.append({"指標": f, "偏りの強さ(z)": round(z, 2), "傾向": way})
    return chosen, pd.DataFrame(info), [c[0] for c in cand]


# ───────────────────────── 条件の探索 ─────────────────────────
def _fmt_thr(t):
    return f"{t:,.3g}"


def _make_singles(F, feats, tr_rows):
    conds, cols = [], []
    for f in feats:
        v = F[f].to_numpy(float)
        tr = v[tr_rows]
        tr = tr[~np.isnan(tr)]
        if len(tr) < 20:
            continue
        if set(np.unique(tr).tolist()) <= {0.0, 1.0}:
            if (tr == 1).sum() < 5:
                continue
            ths = [("≥", 1.0)]
        else:
            ps = (10, 20, 30, 70, 80, 90)
            ths = [("≤" if p < 50 else "≥", float(f"{q:.3g}")) for q, p in zip(np.percentile(tr, ps), ps)]
        seen = set()
        for op, t in ths:
            if (op, t) in seen:
                continue
            seen.add((op, t))
            with np.errstate(invalid="ignore"):
                m = (v <= t) if op == "≤" else (v >= t)
            conds.append((f, op, t))
            cols.append(m)
    if not cols:
        return [], np.zeros((len(F), 0), bool)
    return conds, np.column_stack(cols)


def _combine(conds, Mx, max_k=3):
    by = {}
    for i, (f, _, _) in enumerate(conds):
        by.setdefault(f, []).append(i)
    feats = list(by)
    n = Mx.shape[0]
    combos = [(i,) for i in range(len(conds))]
    blocks = [Mx]
    for k in range(2, max_k + 1):
        for fs in itertools.combinations(feats, k):
            groups = [by[f] for f in fs]
            blk = Mx[:, groups[0]]
            for g in groups[1:]:
                blk = (blk[:, :, None] & Mx[:, g][:, None, :]).reshape(n, -1)
            blocks.append(blk)
            combos.extend(itertools.product(*groups))
    allm = np.concatenate(blocks, axis=1)
    # 完全に同じ日の集合になる条件は、短いほうだけ残す
    seen, keep = set(), []
    packed = np.packbits(allm, axis=0)
    for j in range(allm.shape[1]):
        key = packed[:, j].tobytes()
        if key in seen:
            continue
        seen.add(key)
        keep.append(j)
    return [combos[j] for j in keep], allm[:, keep]


def _wilson_low(w, n, z=1.645):
    if n <= 0:
        return np.nan
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - a) / d


def _cond_text(conds, ids):
    parts = []
    for i in ids:
        f, op, t = conds[i]
        parts.append(f"{f}が該当(1)" if (op == "≥" and t == 1.0 and f.startswith("VIX") and "後5日" in f)
                     else f"{f} {op} {_fmt_thr(t)}")
    return " かつ ".join(parts)


def mine_rules(F, feats, split, n_perm=N_PERM, seed=SEED, all_feats=None):
    """
    F: build_frame の期間切り出し（昇順）。split: 前半/後半の境の行番号。
    戻り値: DataFrame（1行=1ルール）と、現在(最終行)で点灯しているか。
    """
    n = len(F)
    rng = np.random.default_rng(seed)
    out = []
    for h in HORIZONS:
        tr_rows = np.where((np.arange(n) + h) < split)[0]          # 前半(答えが後半に食い込まない行)
        te_rows = np.where(np.arange(n) >= split)[0]
        conds, Mx = _make_singles(F, feats, tr_rows)
        if Mx.shape[1] == 0:
            continue
        combos, C = _combine(conds, Mx, 3)
        fwd = F[f"fwd{h}"].to_numpy(float)
        ok_tr = tr_rows[~np.isnan(fwd[tr_rows])]
        ok_te = te_rows[~np.isnan(fwd[te_rows])]
        if len(ok_tr) < 40 or len(ok_te) < 15:
            continue
        n_min = max(8, int(round(len(ok_tr) * 0.05)))
        extra = None
        if all_feats:
            _, Ex = _make_singles(F, [f for f in all_feats if f not in feats], tr_rows)
            if Ex.shape[1]:
                extra = Ex[ok_tr].astype(np.float32)
        Ctr = C[ok_tr].astype(np.float32)
        ntr = Ctr.sum(0)
        live = ntr >= n_min
        if not live.any():
            continue
        for direction in ("up", "dn"):
            y = (fwd > 0) if direction == "up" else (fwd < 0)
            ytr = y[ok_tr].astype(np.float32)
            b = float(ytr.mean())
            if b <= 0 or b >= 1:
                continue
            wtr = (Ctr.T @ ytr)
            with np.errstate(invalid="ignore", divide="ignore"):
                rate_tr = wtr / ntr
                z_tr = (rate_tr - b) / np.sqrt(b * (1 - b) / ntr)
            # 偶然の水準：yを時間方向に循環シフトして同じ探索を n_perm 回
            lo, hi = 5, max(6, len(ok_tr) - 5)
            offs = rng.choice(np.arange(lo, hi), size=n_perm, replace=(hi - lo) < n_perm)
            Y = np.stack([np.roll(ytr, int(o)) for o in offs], axis=1)
            Wn = Ctr[:, live].T @ Y
            with np.errstate(invalid="ignore", divide="ignore"):
                Zn = (Wn / ntr[live][:, None] - b) / np.sqrt(b * (1 - b) / ntr[live])[:, None]
            zmax = np.nanmax(Zn, axis=0)
            if extra is not None:
                ne = extra.sum(0)
                okc = ne >= n_min
                if okc.any():
                    We = extra[:, okc].T @ Y
                    with np.errstate(invalid="ignore", divide="ignore"):
                        Ze = (We / ne[okc][:, None] - b) / np.sqrt(b * (1 - b) / ne[okc])[:, None]
                    zmax = np.maximum(zmax, np.nanmax(Ze, axis=0))
            cand = np.where(live & (rate_tr >= WIN_MIN))[0]
            if len(cand) == 0:
                continue
            # 部分条件より3pt以上よくなっていない組み合わせは捨てる（無意味な条件の積み増しを防ぐ）
            index_of = {c: j for j, c in enumerate(combos)}
            for j in cand:
                combo = combos[j]
                if len(combo) > 1:
                    subs = [index_of.get(tuple(s)) for k in range(1, len(combo))
                            for s in itertools.combinations(combo, k)]
                    best_sub = max([rate_tr[s] for s in subs if s is not None and live[s]] or [0])
                    if rate_tr[j] < best_sub + 0.03:
                        continue
                p_fw = float((1 + (zmax >= z_tr[j]).sum()) / (1 + n_perm))
                mask = C[:, j]
                te = mask[ok_te]
                n_te = int(te.sum())
                w_te = float(y[ok_te][te].sum()) if n_te else 0.0
                allr = np.concatenate([ok_tr, ok_te])
                m_all = mask[allr]
                n_all = int(m_all.sum())
                w_all = float(y[allr][m_all].sum())
                rate_all = w_all / n_all if n_all else np.nan
                rate_te = w_te / n_te if n_te else np.nan
                b_all = float(y[allr].mean())
                mean_ret = float(np.nanmean(fwd[allr][m_all])) if n_all else np.nan
                ntr_j = int(ntr[j])
                if (n_all >= 20 and n_te >= 5 and rate_te >= WIN_MIN and rate_all >= WIN_MIN and p_fw <= 0.10):
                    grade = "◎ 検証済み"
                elif (n_te >= 3 and rate_te >= 0.55 and rate_all >= WIN_MIN and p_fw <= 0.30):
                    grade = "○ 参考"
                else:
                    grade = "△ 偶然の可能性"
                z_all = (rate_all - b_all) / math.sqrt(b_all * (1 - b_all) / n_all) if n_all else 0.0
                out.append({
                    "方向": "買い(上昇)" if direction == "up" else "売り(下落)",
                    "日数": h, "条件": _cond_text(conds, combo), "_combo": [conds[i] for i in combo],
                    "信頼度": grade, "勝率(全体)": rate_all, "件数": n_all, "前半勝率": rate_tr[j], "前半件数": ntr_j,
                    "後半勝率": rate_te, "後半件数": n_te, "全体の基準率": b_all, "差(pt)": (rate_all - b_all) * 100,
                    "勝率下限(90%)": _wilson_low(w_all, n_all), "平均騰落(%)": mean_ret,
                    "偶然確率": p_fw, "_z": z_all,
                })
    if not out:
        return pd.DataFrame()
    R = pd.DataFrame(out)
    order = {"◎ 検証済み": 0, "○ 参考": 1, "△ 偶然の可能性": 2}
    R["_g"] = R["信頼度"].map(order)
    R = R.sort_values(["_g", "_z"], ascending=[True, False])
    # 同じ(方向,日数,信頼度)は上位6件まで
    R = R.groupby(["方向", "日数", "信頼度"], sort=False).head(6).reset_index(drop=True)
    return R.drop(columns=["_g"])


def rules_on_now(R, F):
    """最終行の指標で、各ルールが今点灯しているか。"""
    if R is None or R.empty:
        return []
    last = F.iloc[-1]
    flags = []
    for combo in R["_combo"]:
        ok = True
        for f, op, t in combo:
            v = last.get(f, np.nan)
            if v != v or not ((v <= t) if op == "≤" else (v >= t)):
                ok = False
                break
        flags.append(ok)
    return flags


# ───────────────────────── 共通点（週間・月間・連騰） ─────────────────────────
def _auc(v, ev):
    d = pd.DataFrame({"v": v, "e": ev}).dropna()
    n1 = int((d["e"] == 1).sum())
    n0 = int((d["e"] == 0).sum())
    if n1 < 5 or n0 < 5:
        return np.nan
    r = d["v"].rank()
    return float((r[d["e"] == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def common_traits(F, event_col, feats, split, top=5):
    """event_col が1の日（先の動きが起きた日）の『その前日までの指標』に共通する条件を上位から返す。"""
    ev = F[event_col].to_numpy(float)
    nrow = len(F)
    idx = np.arange(nrow)
    rows = []
    for f in feats:
        v = F[f].to_numpy(float)
        valid = ~np.isnan(v) & ~np.isnan(ev)
        if valid.sum() < 40 or (valid & (ev == 1)).sum() < 8:
            continue
        auc = _auc(v, ev)
        if auc != auc:
            continue
        med_ev, med_all = np.median(v[valid & (ev == 1)]), np.median(v[valid])
        op = "≥" if med_ev >= med_all else "≤"
        thr = float(f"{med_ev:.3g}")
        with np.errstate(invalid="ignore"):
            cond = (v >= thr) if op == "≥" else (v <= thr)
        base = float((ev[valid] == 1).mean())
        share_ev = float(cond[valid & (ev == 1)].mean())
        share_all = float(cond[valid].mean())
        c_ok = valid & cond
        p_cond = float((ev[c_ok] == 1).mean()) if c_ok.sum() else np.nan

        def lift(sel):
            vv = valid & sel
            bb = (ev[vv] == 1).mean() if vv.sum() else np.nan
            cc = vv & cond
            pc = (ev[cc] == 1).mean() if cc.sum() >= 5 else np.nan
            return pc / bb if (bb == bb and bb > 0 and pc == pc) else np.nan
        l1, l2 = lift(idx < split), lift(idx >= split)
        consistent = (l1 == l1 and l2 == l2 and l1 > 1.1 and l2 > 1.1)
        rows.append({"指標": f, "共通する条件": f"{f} {op} {_fmt_thr(thr)}", "起きた日のうち該当": share_ev,
                     "全営業日のうち該当": share_all, "該当日に起きる確率": p_cond, "全体で起きる確率": base,
                     "倍率": p_cond / base if base > 0 and p_cond == p_cond else np.nan,
                     "前半・後半とも": "✔" if consistent else "－", "_auc": abs(auc - 0.5)})
    if not rows:
        return pd.DataFrame()
    D = pd.DataFrame(rows).sort_values("_auc", ascending=False).head(top).reset_index(drop=True)
    return D.drop(columns=["_auc"])


def make_events(F, qhi=80, qlo=20):
    """表示期間の動きから基準を自動で決めて、イベント列を F に追加する。基準値の辞書も返す。"""
    th = {}
    for h, lab in ((5, "週間"), (20, "月間")):
        x = F[f"fwd{h}"].dropna()
        if len(x) < 30:
            continue
        up = max(round(float(np.percentile(x, qhi)) * 2) / 2, 1.0)
        dn = min(round(float(np.percentile(x, qlo)) * 2) / 2, -1.0)
        F[f"ev_{lab}上昇"] = np.where(F[f"fwd{h}"].isna(), np.nan, (F[f"fwd{h}"] >= up).astype(float))
        F[f"ev_{lab}下落"] = np.where(F[f"fwd{h}"].isna(), np.nan, (F[f"fwd{h}"] <= dn).astype(float))
        th[f"{lab}上昇"] = f"{h}営業日で {up:+.1f}% 以上上昇"
        th[f"{lab}下落"] = f"{h}営業日で {dn:+.1f}% 以下の下落"
    F["ev_3連騰"] = F["next3_up"]
    F["ev_3連落"] = F["next3_dn"]
    th["3連騰"] = "この先3営業日連続で上昇"
    th["3連落"] = "この先3営業日連続で下落"
    return th


# ───────────────────────── 連騰・連落の確率（長期） ─────────────────────────
def streak_table(close, up=True, kmax=10):
    """
    終値系列(昇順)から k連騰(連落)に到達した回数と、その次の日・5日後の動き。
    『k連騰に到達した日』を基準にする（その日の終値で判断する想定）。
    """
    c = pd.Series(np.asarray(close, float))
    r = c.pct_change()
    flag = (r > 0).values if up else (r < 0).values
    n = len(c)
    run = np.zeros(n, int)
    for i in range(n):
        run[i] = (run[i - 1] + 1 if i and flag[i] else (1 if flag[i] else 0))
    fwd1 = (c.shift(-1) / c - 1).values * 100
    fwd5 = (c.shift(-5) / c - 1).values * 100
    rows = []
    for k in range(1, kmax + 1):
        days = np.where(run == k)[0]            # ちょうどk連目の日
        reach = int(len(days))
        if reach == 0:
            break
        nxt_ok = days[days + 1 < n]
        cont = int((flag[nxt_ok + 1]).sum()) if len(nxt_ok) else 0
        f5 = fwd5[days]
        f5 = f5[~np.isnan(f5)]
        f1 = fwd1[days]
        f1 = f1[~np.isnan(f1)]
        rows.append({"連続日数": k, "到達回数": reach, "全営業日のうち": reach / n,
                     "次の日も続く確率": cont / len(nxt_ok) if len(nxt_ok) else np.nan,
                     "翌日平均(%)": float(f1.mean()) if len(f1) else np.nan,
                     "5日後に反対方向": float(((f5 < 0) if up else (f5 > 0)).mean()) if len(f5) else np.nan,
                     "5日後平均(%)": float(f5.mean()) if len(f5) else np.nan})
    return pd.DataFrame(rows)


def current_streak(close):
    c = pd.Series(np.asarray(close, float))
    r = c.pct_change().values
    k = 0
    sign = 0
    for x in r[::-1]:
        if x != x:
            break
        s = 1 if x > 0 else (-1 if x < 0 else 0)
        if s == 0:
            break
        if sign == 0:
            sign = s
        if s != sign:
            break
        k += 1
    return sign, k


# ───────────────────────── 上位・下位5日／乖離率 ─────────────────────────
def top_bottom(F, feats, k=5):
    """前日比の上位k日・下位k日。前後の動きと、その日の指標。共通する指標があれば注記を返す。"""
    d = F.dropna(subset=["前日比(%)"]).copy()
    c = F["終値"]
    d["直前5日(%)"] = (c.shift(1) / c.shift(6) - 1).reindex(d.index) * 100

    def block(sub, name):
        t = pd.DataFrame({"日付": sub.index.strftime("%Y/%m/%d"), "前日比(%)": sub["前日比(%)"].values,
                          "直前5日(%)": sub["直前5日(%)"].values, "翌日(%)": sub["fwd1"].values,
                          "5日後(%)": sub["fwd5"].values})
        for f in ("信用倍率", "制度信用倍率", "貸借倍率", "RSI", "25日乖離率(%)"):
            if f in sub.columns:
                t[f] = sub[f].values
        notes = []
        for f in [x for x in feats if x != "前日比(%)"]:
            if f in sub.columns and F[f].notna().sum() > 30:
                v = sub[f].dropna()
                if len(v) >= 4:
                    med = F[f].median()
                    hi = int((v > med).sum())
                    if hi >= len(v) - 1 or hi <= 1:
                        notes.append(f"{f}は{len(v)}日中{hi if hi >= len(v) - 1 else len(v) - hi}日が期間の中央値"
                                     f"({_fmt_thr(med)})より{'高' if hi >= len(v) - 1 else '低'}い")
        n1 = sub["fwd1"].dropna()
        n5 = sub["fwd5"].dropna()
        summ = (f"翌日に上昇した割合 {float((n1 > 0).mean()) * 100:.0f}%（平均{float(n1.mean()):+.2f}%）／"
                f"5日後に上昇していた割合 {float((n5 > 0).mean()) * 100:.0f}%（平均{float(n5.mean()):+.2f}%）") if len(n1) and len(n5) else ""
        return {"name": name, "table": t, "notes": notes, "summary": summ}
    return (block(d.nlargest(k, "前日比(%)").sort_index(), f"前日比 上位{k}日（急騰）"),
            block(d.nsmallest(k, "前日比(%)").sort_index(), f"前日比 下位{k}日（急落）"))


def deviation_stats(F):
    d = F.dropna(subset=["25日乖離率(%)"])
    if len(d) < 30:
        return None
    dev = d["25日乖離率(%)"]
    x = d["max10"].dropna()
    X = max(round(float(np.median(x)) * 2) / 2, 1.0) if len(x) else 3.0
    rows = []
    for side in ("下限側", "上限側"):
        if side == "下限側":
            thr = float(np.floor(np.percentile(dev, 10)))
            sel = d[dev <= thr]
            hit = (sel["max10"] >= X)
            lab = f"10日以内に+{X:.1f}%到達"
            up5 = float((sel["fwd5"].dropna() > 0).mean())
            extreme_date, extreme = dev.idxmin(), dev.min()
        else:
            thr = float(np.ceil(np.percentile(dev, 90)))
            sel = d[dev >= thr]
            hit = (sel["min10"] <= -X)
            lab = f"10日以内に−{X:.1f}%到達"
            up5 = float((sel["fwd5"].dropna() < 0).mean())
            extreme_date, extreme = dev.idxmax(), dev.max()
        base_hit = float(((d["max10"] >= X) if side == "下限側" else (d["min10"] <= -X)).mean())
        h = hit[sel["max10"].notna()] if len(sel) else hit
        row = {"区分": side, "基準": f"乖離率 {'≤' if side == '下限側' else '≥'} {thr:+.0f}%",
               "極値": f"{extreme_date:%Y/%m/%d} {extreme:+.1f}%", "該当日数": int(len(sel)),
               "全営業日のうち": len(sel) / len(d), "到達条件": lab,
               "到達確率": float(h.mean()) if len(h) else np.nan, "全日の到達確率": base_hit,
               "5日後に逆方向へ": up5}
        for f in ("信用倍率", "制度信用倍率"):
            if f in d.columns and sel[f].notna().any():
                row[f"平均{f}(該当日)"] = float(sel[f].mean())
                row[f"平均{f}(全日)"] = float(d[f].mean())
        rows.append(row)
    return {"mean": float(dev.mean()), "table": pd.DataFrame(rows), "x": X}


# ───────────────────────── まとめ ─────────────────────────
def analyze(F_full, period_start=None, n_perm=N_PERM):
    """
    F_full: build_frame の結果（全履歴）。period_start 以降を『探索期間』にする（None=全部）。
    重い処理はここに集約。結果は dict。
    """
    res = {"warn": []}
    F = F_full if period_start is None else F_full[F_full.index >= pd.Timestamp(period_start)]
    F = F.copy()
    n = len(F)
    res["n"] = n
    res["start"], res["end"] = (F.index[0], F.index[-1]) if n else (None, None)
    res["close_full"] = F_full["終値"].values
    # 連騰・連落は長期の全履歴で数える（多いほど確率が安定）
    res["streak_up"] = streak_table(F_full["終値"].values, True)
    res["streak_dn"] = streak_table(F_full["終値"].values, False)
    res["cur_sign"], res["cur_k"] = current_streak(F_full["終値"].values)
    res["full_years"] = (F_full.index[-1] - F_full.index[0]).days / 365.25 if len(F_full) > 1 else 0
    if n < 40:
        res["warn"].append("営業日が少なすぎるため、探索は行いません。表示期間を長くしてください。")
        return res
    if n < MIN_ROWS_RULES:
        res["warn"].append(f"探索期間が{n}営業日と短いため、売買ルールの探索は行いません"
                           f"（{MIN_ROWS_RULES}営業日以上が目安。表示期間を延ばすか『蓄積全期間』を選んでください）。")
    split = int(n * 2 / 3)
    res["split_date"] = F.index[split] if split < n else None
    tr_rows = np.arange(0, max(split - 5, 1))
    feats, fsel, cand = select_features(F, tr_rows)
    res["features"], res["features_df"], res["cand"] = feats, fsel, cand
    excluded = [f for f in FEATURE_NOTE if f not in cand]
    res["excluded"] = excluded
    f1 = F["fwd1"].dropna()
    res["base"] = {h: float((F[f"fwd{h}"].dropna() > 0).mean()) for h in HORIZONS}
    res["period_ret"] = float((F["終値"].iloc[-1] / F["終値"].iloc[0] - 1) * 100)
    th = make_events(F)
    res["event_defs"] = th
    res["traits"] = {k: common_traits(F, f"ev_{k}", feats, split) for k in th}
    res["event_counts"] = {k: int(np.nansum(F[f"ev_{k}"].values)) for k in th}
    res["top"], res["bottom"] = top_bottom(F, feats)
    res["dev"] = deviation_stats(F)
    if n >= MIN_ROWS_RULES and feats:
        R = mine_rules(F, feats, split, n_perm=n_perm, all_feats=cand)
        res["rules"] = R
        res["rules_on"] = rules_on_now(R, F)
    else:
        res["rules"], res["rules_on"] = pd.DataFrame(), []
    res["last"] = F.iloc[-1]
    # ── 今日の要約用：現在地マップと、期間内の上昇・下落の統計 ──
    way = {r["指標"]: r["傾向"] for _, r in fsel.iterrows()} if len(fsel) else {}
    names = list(feats) + [f for f in ("RSI", "25日乖離率(%)", "信用倍率", "制度信用倍率", "貸借倍率", "前日比(%)")
                           if f in F.columns and f not in feats]
    mp = []
    for f in names:
        sr = F[f].dropna()
        v = res["last"].get(f, np.nan)
        if len(sr) < 30 or v != v:
            continue
        pct = float(((sr < v).sum() + 0.5 * (sr == v).sum()) / len(sr) * 100)
        mp.append({"name": f, "value": float(v), "pct": pct, "lo": float(sr.quantile(0.2)), "hi": float(sr.quantile(0.8)),
                   "way": way.get(f, ""), "selected": f in feats})
    res["map"] = mp
    res["updown"] = {}
    for h in (1, 3, 5, 20):
        x = F[f"fwd{h}"].dropna()
        if len(x):
            res["updown"][h] = {"up": float((x > 0).mean()), "dn": float((x < 0).mean()), "mean": float(x.mean()), "n": int(len(x))}
    res["event_rates"] = {k: float(np.nanmean(F[f"ev_{k}"].values)) for k in th}
    return res
