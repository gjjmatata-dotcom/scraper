"""
stock_head.py ― 個別銘柄ページの「ヘッド 第2案」（表示専用）
  ・数字の色＝動いた向き（青＋/赤−）、バッジ・枠＝株価への材料（緑◎好材料/橙✕悪材料/灰－中立）の2段構え
  ・「あとどれくらいで届くか」は指数平滑の式から正確に逆算（予測ではなく算術）。VIXと同じ式を株価・各倍率に使う
  ・株価テーブルの定義（TR_up/TR_dn・機関異常・出来高異常）は dashboard.py と同じ。ここでは新しい定義を作らない
  ・どこかで例外が出ても、呼び出し側（dashboard.py）が従来のKPIボードに切り替える
"""
import html as _html
import numpy as np
import pandas as pd
import streamlit as st

UP, DN, NE, GOLD = "#58a6ff", "#f85149", "#8b949e", "#e3b341"
GOOD, BAD, MID = "#3fb950", "#f0883e", "#8b949e"
A12, A26, A9 = 2 / 13, 2 / 27, 2 / 10

CSS = """<style>
.sh-wrap *{box-sizing:border-box}
.sh-sec{font-size:21px;font-weight:800;color:#f0f6fc;border-left:6px solid #388bfd;padding:2px 0 2px 12px;margin:26px 0 12px}
.sh-sec small{font-size:12px;color:#8b949e;font-weight:500;margin-left:10px}
.sh-sec2{font-size:12px;color:#8b949e;margin:-6px 0 10px}
.sh-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}
.sh-tile{border:2px solid;border-radius:12px;padding:12px 16px}
.sh-tile .tl{font-size:13px;color:#8b949e;font-weight:700}.sh-tile .tv{font-size:26px;font-weight:900;margin:2px 0}.sh-tile .ts{font-size:13px;color:#c9d1d9;line-height:1.5}
.sh-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.sh-box{border:2px solid;border-radius:12px;padding:12px 14px;min-width:0}
.sh-bt{font-size:15px;font-weight:800;color:#e6edf3}
.sh-bv{font-size:40px;font-weight:900;line-height:1.1;margin:2px 0 4px}.sh-bv small{font-size:15px;margin-left:5px;font-weight:700}
.sh-sub{font-size:13px;color:#a8b3c0;margin:3px 0}
.sh-vis{margin:8px 0 4px}
.sh-goal{font-size:14px;font-weight:800;color:#e3b341;background:rgba(227,179,65,.10);border:1px dashed #e3b34188;border-radius:8px;padding:7px 10px;line-height:1.55;margin-top:5px}
.sh-mat{display:inline-block;font-size:13px;font-weight:900;border-radius:6px;padding:2px 10px;margin:2px 6px 2px 0}
.sh-star{color:#e3b341;font-size:13px;letter-spacing:1px}
.sh-hero{display:grid;grid-template-columns:minmax(260px,1fr) minmax(300px,1.2fr);gap:18px;align-items:center}
.sh-hero>div{min-width:0}
.sh-pill{display:inline-block;font-weight:900;border-radius:8px;padding:2px 12px;font-size:22px;margin-left:8px}
.sh-speed{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}
.sh-sp{flex:1;min-width:120px;border:1px solid #30363d;border-radius:8px;padding:6px 10px;background:#0d1117}
.sh-sp .a{font-size:11px;color:#8b949e}.sh-sp .b{font-size:19px;font-weight:900}
.sh-bal{display:flex;height:26px;border-radius:13px;overflow:hidden;margin:8px 0;border:1px solid #30363d}
.sh-bal div{display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:900;color:#0d1117}
.sh-legend{font-size:12px;color:#8b949e;margin-top:22px;line-height:1.9;border-top:1px solid #21262d;padding-top:10px}
table.sh-tb{width:100%;border-collapse:collapse;font-size:12px;margin:2px 0}
table.sh-tb th{color:#8b949e;font-weight:600;text-align:right;padding:2px 4px;border-bottom:1px solid #30363d}
table.sh-tb th:first-child,table.sh-tb td:first-child{text-align:left}
table.sh-tb td{color:#c9d1d9;text-align:right;padding:3px 4px;border-bottom:1px solid #21262d;white-space:nowrap}
.sh-ext{margin-top:8px;border-top:1px dashed #30363d;padding-top:6px}
.sh-ext .t{font-size:12px;color:#8b949e;font-weight:700;margin-bottom:2px}
.sh-ext .l{font-size:12px;color:#c9d1d9;line-height:1.6}
.sh-brd{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.sh-row{display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:center;padding:5px 2px;border-bottom:1px solid #21262d;font-size:13px}
.sh-row .n{color:#c9d1d9}.sh-row .v{font-weight:800;text-align:right}.sh-row .c{font-size:12px;font-weight:900;border-radius:4px;padding:1px 7px;min-width:34px;text-align:center}
@media (max-width:700px){.sh-hero{grid-template-columns:1fr}.sh-bv{font-size:34px}}
</style>"""


# ───────────────────────── 小さな部品 ─────────────────────────
def _f(v):
    try:
        x = float(v)
        return None if (x != x or abs(x) == float("inf")) else x
    except Exception:
        return None


def _s(v, d=2, sign=False, sfx=""):
    x = _f(v)
    return "-" if x is None else f"{x:{'+' if sign else ''},.{d}f}{sfx}"


def _col(v):
    return UP if (_f(v) or 0) >= 0 else DN


def _mat(kind, text=None):
    c = {"good": GOOD, "bad": BAD, "mid": MID}[kind]
    t = text or {"good": "◎ 株価に好材料", "bad": "✕ 株価に悪材料", "mid": "－ 中立"}[kind]
    return f"<span class='sh-mat' style='background:{c}26;color:{c};border:1.5px solid {c}'>{t}</span>"


def _bars(vals, labels, w=300, h=96, fmt="{:+.2f}"):
    n = len(vals)
    if n == 0:
        return ""
    mx = max(abs(v) for v in vals) or 1
    bw = w / n
    mid = h / 2 + 4
    o = [f"<svg viewBox='0 0 {w} {h+22}' width='100%' style='display:block'>",
         f"<line x1='0' x2='{w}' y1='{mid}' y2='{mid}' stroke='#484f58'/>"]
    for i, (v, lb) in enumerate(zip(vals, labels)):
        hh = abs(v) / mx * (h / 2 - 16)
        y = mid - hh if v >= 0 else mid
        c = UP if v >= 0 else DN
        last = i == n - 1
        o.append(f"<rect x='{i*bw+5:.1f}' y='{y:.1f}' width='{bw-10:.1f}' height='{max(hh,2):.1f}' rx='3' fill='{c}' opacity='{1 if last else .55}'"
                 + (" stroke='#fff' stroke-width='2'" if last else "") + "/>")
        ty = y - 4 if v >= 0 else y + hh + 12
        o.append(f"<text x='{i*bw+bw/2:.1f}' y='{ty:.1f}' fill='{'#f0f6fc' if last else '#c9d1d9'}' font-size='{13 if last else 11}' font-weight='{800 if last else 600}' text-anchor='middle'>{fmt.format(v)}</text>")
        o.append(f"<text x='{i*bw+bw/2:.1f}' y='{h+18}' fill='#8b949e' font-size='10' text-anchor='middle'>{lb}</text>")
    o.append("</svg>")
    return "".join(o)


def _hist_bars(vals, w=300, h=70):
    vals = [v for v in vals if _f(v) is not None]
    n = len(vals)
    if n == 0:
        return ""
    mx = max(abs(v) for v in vals) or 1
    bw = w / n
    mid = h / 2
    o = [f"<svg viewBox='0 0 {w} {h}' width='100%' style='display:block;background:#0d1117;border-radius:6px'>",
         f"<line x1='0' x2='{w}' y1='{mid}' y2='{mid}' stroke='#484f58'/>"]
    for i, v in enumerate(vals):
        hh = abs(v) / mx * (h / 2 - 4)
        y = mid - hh if v >= 0 else mid
        o.append(f"<rect x='{i*bw+1:.1f}' y='{y:.1f}' width='{bw-2:.1f}' height='{max(hh,1):.1f}' fill='{UP if v>=0 else DN}' opacity='{1 if i==n-1 else .5}'/>")
    o.append("</svg>")
    return "".join(o)


def _spark(vals, avg, color=UP, w=300, h=64):
    vals = [v for v in vals if _f(v) is not None]
    if len(vals) < 2:
        return "<div style='font-size:12px;color:#6e7681;padding:10px 0'>推移グラフは2日分以上たまると表示されます</div>"
    a = _f(avg)
    lo, hi = min(vals + ([a] if a is not None else [])), max(vals + ([a] if a is not None else []))
    rg = (hi - lo) or 1
    pts = [(i / (len(vals) - 1) * (w - 16) + 8, h - 8 - (v - lo) / rg * (h - 16)) for i, v in enumerate(vals)]
    p = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    avg_line = ""
    if a is not None:
        ya = h - 8 - (a - lo) / rg * (h - 16)
        avg_line = (f"<line x1='0' x2='{w}' y1='{ya:.1f}' y2='{ya:.1f}' stroke='{GOLD}' stroke-dasharray='5 4'/>"
                    f"<text x='{w-4}' y='{ya-4:.1f}' fill='{GOLD}' font-size='10' text-anchor='end'>平均</text>")
    return (f"<svg viewBox='0 0 {w} {h}' width='100%' style='display:block;background:#0d1117;border-radius:6px'>{avg_line}"
            f"<polyline points='{p}' fill='none' stroke='{color}' stroke-width='3'/>"
            f"<circle cx='{pts[-1][0]:.1f}' cy='{pts[-1][1]:.1f}' r='5' fill='#fff'/></svg>")


def _gauge(pos, zones, marks=(), ends=("", "")):
    pos = min(max(pos, 0), 100)
    z = "".join(f"<div style='position:absolute;left:{a}%;width:{b-a}%;height:100%;background:{c}'></div>" for a, b, c in zones)
    m = "".join(f"<div style='position:absolute;left:{p}%;top:-4px;width:3px;height:26px;background:{GOLD}'></div>"
                f"<div style='position:absolute;left:{p}%;top:26px;transform:translateX(-50%);font-size:11px;color:{GOLD};white-space:nowrap'>{t}</div>"
                for p, t in marks)
    return (f"<div style='position:relative;height:18px;background:#0d1117;border-radius:9px;margin:10px 4px 30px;overflow:visible'>"
            f"<div style='position:absolute;inset:0;border-radius:9px;overflow:hidden'>{z}</div>{m}"
            f"<div style='position:absolute;left:calc({pos}% - 9px);top:-1px;width:20px;height:20px;border-radius:10px;background:#f0f6fc;border:3px solid #0d1117;box-shadow:0 0 0 2px #f0f6fc'></div></div>"
            f"<div style='display:flex;justify-content:space-between;font-size:11px;color:#6e7681;margin-top:-26px;padding:0 4px'><span>{ends[0]}</span><span>{ends[1]}</span></div>")


def _rail(items, lo, hi):
    """items=[(価格, ラベルHTML, 色, 上側に置くか)]"""
    pos = lambda v: min(max((v - lo) / (hi - lo) * 100, 0), 100)
    lab = lambda v: min(max(pos(v), 7), 93)
    o = ["<div style='position:relative;height:132px;margin:6px 18px 0'>",
         "<div style='position:absolute;left:0;right:0;top:58px;height:8px;border-radius:4px;background:linear-gradient(90deg,#1c2331,#2a3347)'></div>"]
    for v, lb, c, up in items:
        ty = 0 if up else 80
        o.append(f"<div style='position:absolute;left:{pos(v):.1f}%;top:50px;width:4px;height:24px;background:{c};border-radius:2px;transform:translateX(-2px)'></div>")
        o.append(f"<div style='position:absolute;left:{lab(v):.1f}%;top:{ty}px;transform:translateX(-50%);text-align:center;font-size:12px;color:{c};white-space:nowrap;font-weight:700;line-height:1.35'>{lb}</div>")
    o.append("</div>")
    return "".join(o)


def _box(title, big, bigcolor, chip, kind, visual, goals, sub="", stars=None, dated="", extra=""):
    c = {"good": GOOD, "bad": BAD, "mid": MID}[kind]
    bg = {"good": "rgba(63,185,80,.08)", "bad": "rgba(240,136,62,.09)", "mid": "rgba(139,148,158,.06)"}[kind]
    st_ = f"<span class='sh-star'>{stars}</span>" if stars else ""
    g = "".join(f"<div class='sh-goal'>{x}</div>" for x in goals)
    return (f"<div class='sh-box' style='border-color:{c};background:{bg}'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center'><div class='sh-bt'>{title}</div>{st_}</div>"
            f"<div class='sh-bv' style='color:{bigcolor}'>{big}</div>"
            f"<div>{_mat(kind)} <span style='font-size:13px;color:#c9d1d9;font-weight:700'>{chip}</span></div>"
            + (f"<div class='sh-sub'>{sub}</div>" if sub else "") + f"<div class='sh-vis'>{visual}</div>{g}"
            + (f"<div class='sh-ext'>{extra}</div>" if extra else "")
            + (f"<div style='font-size:11px;color:#6e7681;margin-top:4px'>{dated}</div>" if dated else "") + "</div>")


def _tile(label, val, sub, c):
    return (f"<div class='sh-tile' style='border-color:{c};background:{c}1a'><div class='tl'>{label}</div>"
            f"<div class='tv' style='color:{c}'>{val}</div><div class='ts'>{sub}</div></div>")


# ───────────────────────── 計算（MACDの逆算） ─────────────────────────
def macd_calc(values, horizon=30):
    """
    任意の系列に MACD(12,26,9) を当てて「あとどれくらいでクロスするか」を厳密に逆算する。
    hist は次の値 x の一次式なので、hist=0 になる x を代数で解ける。
    4点未満なら None。35点未満でも計算はするが、助走が短いので呼び出し側で『参考』と表示する。
    """
    s = pd.to_numeric(pd.Series(list(values)), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().astype(float)
    n = len(s)
    if n < 4:
        return None
    e12 = s.ewm(span=12, adjust=False).mean()
    e26 = s.ewm(span=26, adjust=False).mean()
    macd = e12 - e26
    sig = macd.ewm(span=9, adjust=False).mean()
    hist = macd - sig
    E12, E26 = float(e12.iloc[-1]), float(e26.iloc[-1])
    m, sg, h = float(macd.iloc[-1]), float(sig.iloc[-1]), float(hist.iloc[-1])
    hp = float(hist.iloc[-2])
    x_cross = (sg - m + A12 * E12 - A26 * E26) / (A12 - A26)
    c0 = float(s.iloc[-1])
    pos = h > 0
    days, st_ = None, (E12, E26, sg)
    for k in range(1, horizon + 1):
        n12 = st_[0] + A12 * (c0 - st_[0])
        n26 = st_[1] + A26 * (c0 - st_[1])
        mn = n12 - n26
        sn = st_[2] + A9 * (mn - st_[2])
        st_ = (n12, n26, sn)
        if ((mn - sn) > 0) != pos:
            days = k
            break
    return {"n": n, "c0": c0, "hist": h, "hist_prev": hp, "x_cross": x_cross,
            "pct": (x_cross / c0 - 1) * 100 if c0 else None, "days_flat": days,
            "target": "DC" if pos else "GC", "horizon": horizon, "series": hist.tolist()}


def _score_to_text(score):
    if score >= 4:
        return "◎ 好材料", "good", GOOD
    if score >= 1:
        return "◎ やや好材料", "good", GOOD
    if score <= -4:
        return "✕ 悪材料", "bad", BAD
    if score <= -1:
        return "✕ やや悪材料", "bad", BAD
    return "－ 中立", "mid", MID



# ───────────────────────── 期間内の統計 ─────────────────────────
def _lstat(vals):
    arr = [float(x) for x in vals if _f(x) is not None]
    if len(arr) < 2:
        return None
    return {"lo": min(arr), "hi": max(arr), "avg": sum(arr) / len(arr), "n": len(arr)}


def _pos(cur, st_):
    if st_ is None or cur is None or st_["hi"] <= st_["lo"]:
        return None
    return (cur - st_["lo"]) / (st_["hi"] - st_["lo"]) * 100


def _stat_table(rows, dec=2):
    """rows=[(ラベル, 値の系列, 現在値, 単位)] → 最低/平均/最高/現在位置の表"""
    body = ""
    for r_ in rows:
        lab, vals, cur, unit = r_[:4]
        dec = r_[4] if len(r_) > 4 else 2
        st_ = _lstat(vals)
        if st_ is None:
            continue
        ps = _pos(cur, st_)
        f = lambda v: f"{v:,.{dec}f}{unit}"
        body += (f"<tr><td>{lab}</td><td>{f(st_['lo'])}</td><td>{f(st_['avg'])}</td><td>{f(st_['hi'])}</td>"
                 f"<td style='color:#f0f6fc;font-weight:800'>{'-' if cur is None else f(cur)}</td>"
                 f"<td>{'-' if ps is None else f'{ps:.0f}%'}</td></tr>")
    if not body:
        return ""
    return ("<table class='sh-tb'><tr><th></th><th>最低</th><th>平均</th><th>最高</th><th>今</th><th>位置</th></tr>" + body +
            "</table><div style='font-size:11px;color:#6e7681'>位置＝期間の最低を0%・最高を100%としたとき、今がどのあたりか（上がる確率や期待値ではありません）</div>")


def _streaks(pcts):
    """日次変化率(%)の列 → 2日以上つづいた連続上昇／連続下落ごとの (累積率%, 日数)"""
    ups, dns, cur, sign = [], [], [], 0

    def flush():
        if len(cur) >= 2:
            r = (float(np.prod([1 + x / 100 for x in cur])) - 1) * 100
            (ups if sign > 0 else dns).append((r, len(cur)))
    for x in pcts:
        if x is None or x != x:
            flush(); cur, sign = [], 0
            continue
        sg = 1 if x > 0 else -1 if x < 0 else 0
        if sg != 0 and sg == sign:
            cur.append(x)
        else:
            flush()
            cur, sign = ([x] if sg != 0 else []), sg
    flush()
    return ups, dns


def _streak_line(lst, word):
    if not lst:
        return f"{word}：該当なし（2日以上つづいた区間がありません）"
    mx = max(lst, key=lambda t: abs(t[0]))
    av = sum(t[0] for t in lst) / len(lst)
    ad = sum(t[1] for t in lst) / len(lst)
    return f"{word}：最大 <b>{mx[0]:+.1f}%</b>（{mx[1]}日）／平均 <b>{av:+.1f}%</b>（{ad:.1f}日）／{len(lst)}回"


def _streak_html(pcts, up_word="連騰", dn_word="連落"):
    ups, dns = _streaks(pcts)
    return (f"<div class='l'>{_streak_line(ups, up_word)}</div><div class='l'>{_streak_line(dns, dn_word)}</div>"
            "<div style='font-size:11px;color:#6e7681'>2日以上つづいた区間の累積変化率</div>")


def _mfe_mae(closes, n):
    """どの日に買っても、その後n営業日のあいだの『最大含み益(MFE)』『最大含み損(MAE)』と、n日後の損益。全日を起点に平均する。"""
    c = np.array([float(x) for x in closes if _f(x) is not None], float)
    if len(c) < n + 20:
        return None
    mfe, mae, end = [], [], []
    for i in range(len(c) - n):
        w = c[i + 1:i + n + 1]
        mfe.append((w.max() / c[i] - 1) * 100)
        mae.append((w.min() / c[i] - 1) * 100)
        end.append((w[-1] / c[i] - 1) * 100)
    return np.array(mfe), np.array(mae), np.array(end)


def _mfe_html(closes, close, ns=(14, 20)):
    out = ""
    for n in ns:
        r = _mfe_mae(closes, n)
        wd = "約3週間・RSIの期間" if n == 14 else "約1か月"
        if r is None:
            out += f"<div class='l'>{n}営業日：期間が短いため計算できません（{n + 20}日分以上必要）</div>"
            continue
        mfe, mae, end = r
        k = len(mfe)
        tgt_up = close * (1 + mfe.mean() / 100) if close else None
        tgt_dn = close * (1 + mae.mean() / 100) if close else None
        rows = (f"<tr><td>いちばん上がった時</td><td style='color:{UP}'>{mfe.mean():+.1f}%</td><td>{np.percentile(mfe, 90):+.1f}%</td><td>{mfe.max():+.1f}%</td></tr>"
                f"<tr><td>いちばん下がった時</td><td style='color:{DN}'>{mae.mean():+.1f}%</td><td>{np.percentile(mae, 10):+.1f}%</td><td>{mae.min():+.1f}%</td></tr>")
        reach = "／".join(f"+{t}%：<b>{(mfe >= t).mean() * 100:.0f}%</b>" for t in (5, 10, 15))
        reach2 = "／".join(f"−{t}%：<b>{(mae <= -t).mean() * 100:.0f}%</b>" for t in (5, 10))
        give = ""
        for t in (10, 15):
            m = mfe >= t
            if m.sum() >= 5:
                give += (f"<div class='l'>＋{t}%まで上がったことがある {int(m.sum())}回では、{n}日後の損益は平均 <b>{end[m].mean():+.1f}%</b>"
                         f"（ピークから平均 <b>{abs((end[m] - mfe[m]).mean()):.1f}pt</b> 下がっていた）</div>")
        out += (f"<div class='t' style='margin-top:8px;font-size:13px;color:#e6edf3'>■ 買ってから {n}営業日（{wd}）のあいだ</div>"
                f"<div class='l' style='font-size:13px'>どの日に買っても、{n}営業日のあいだに最大で <b style='color:{UP}'>平均 {mfe.mean():+.1f}%</b> まで含み益が出て、"
                f"最大で <b style='color:{DN}'>平均 {mae.mean():+.1f}%</b> まで含み損になりました（買った日の数：{k}日）</div>"
                "<table class='sh-tb'><tr><th></th><th>平均</th><th>10回に1回</th><th>過去最大</th></tr>" + rows + "</table>"
                f"<div class='l'>含み益がここまで届いた割合　{reach}</div><div class='l'>含み損がここまで届いた割合　{reach2}</div>{give}"
                + (f"<div class='l'>今の株価からの目安：上は <b style='color:{UP}'>{tgt_up:,.0f}円</b>（{mfe.mean():+.1f}%）／下は <b style='color:{DN}'>{tgt_dn:,.0f}円</b>（{mae.mean():+.1f}%）</div>" if tgt_up else ""))
    return out


# ───────────────────────── 需給データの整形 ─────────────────────────
def _ratio_series(df, col=None, num=None, den=None):
    if df is None or len(df) == 0 or "_dt" not in df.columns:
        return pd.Series(dtype=float)
    d = df.copy()
    d["_d"] = pd.to_datetime(d["_dt"]).dt.normalize()
    if col is not None:
        if col not in d.columns:
            return pd.Series(dtype=float)
        v = pd.to_numeric(d[col], errors="coerce")
    else:
        if num not in d.columns or den not in d.columns:
            return pd.Series(dtype=float)
        a = pd.to_numeric(d[num], errors="coerce")
        b = pd.to_numeric(d[den], errors="coerce")
        v = a / b.replace(0, np.nan)
    s = pd.Series(v.values, index=d["_d"].values).replace([np.inf, -np.inf], np.nan).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def _ratio_box(items, board, title, short, s, stars, weight, group, cut=None, plabel=""):
    """倍率の箱。倍率は下がるほど好材料（ショートカバー余地・株価上昇の裏返し）。平均・最低・最高は表示期間のぶん。"""
    if s is None or len(s) == 0:
        items.append(dict(kind="mid", w=0, group=group))
        return _box(title, "—", NE, "データなし", "mid", "", ["蓄積データがありません"], stars=stars)
    sp = s[s.index >= cut] if cut is not None else s
    if len(sp) < 2:
        sp = s
    v = float(s.iloc[-1])
    n = len(s)
    avg = float(sp.mean())
    back = s.iloc[-6] if n >= 6 else s.iloc[0]
    ch = v - float(back)
    chp = (ch / float(back) * 100) if back else 0.0
    kind = "good" if chp <= -1 else "bad" if chp >= 1 else "mid"
    items.append(dict(kind=kind, w=weight, group=group))
    k = min(5, n - 1)
    if k >= 1:
        board.append(dict(side="L", name=short, val=ch, text=f"{ch:+.2f}（{k}日）"))
    arrow = "↘" if ch < 0 else "↗" if ch > 0 else "→"
    rel = "平均を下回り" if v < avg else "平均を上回り"
    chip = f"{rel}、{k}日で {arrow} {ch:+.2f}" if k >= 1 else "比較できる過去データなし"
    goals = []
    mc = macd_calc(s.values)
    if mc is None:
        goals.append(f"<b>倍率のMACD</b>：データ不足（いま{n}日分／4日分以上で表示）")
    elif abs(mc["hist"]) < 1e-4 * max(abs(v), 1e-9) and abs(mc["hist_prev"]) < 1e-4 * max(abs(v), 1e-9):
        goals.append(f"<b>倍率のMACD</b>：ほぼ横ばい（倍率がほとんど動いていません・蓄積{n}日分）")
    else:
        h = mc["hist"]
        acc = mc["hist"] - mc["hist_prev"]
        if h < 0:
            state = "低下が加速中" if acc < 0 else "低下は鈍化中"
        else:
            state = "上昇が加速中" if acc > 0 else "上昇は鈍化中"
        tail = "（好材料が強まる）" if (h < 0 and acc < 0) else "（悪材料が強まる）" if (h > 0 and acc > 0) else ""
        ref = "" if n >= 35 else f"　<span style='color:#a8b3c0;font-weight:500'>※蓄積{n}日分のため参考値</span>"
        if mc["target"] == "GC":
            cond = f"明日の倍率が <b>{mc['x_cross']:.2f}以上</b>に戻ると下げ止まり(GC)"
        else:
            cond = f"明日の倍率が <b>{mc['x_cross']:.2f}以下</b>に下がると上昇一服(DC)"
        fl = (f"／横ばいなら <b>{mc['days_flat']}営業日後</b>" if mc["days_flat"]
              else f"／横ばいなら{mc['horizon']}営業日以内はクロスしません")
        goals.append(f"<b>倍率のMACD</b>：ヒスト {h:+.3f} → {state}{tail}<br>{cond}{fl}{ref}")
    lo_p = float(sp.min())
    goals.append(f"平均({avg:,.2f})まで <b>{avg - v:+,.2f}</b>／期間の最低({lo_p:,.2f})まで <b>{lo_p - v:+,.2f}</b>")
    dt = pd.Timestamp(s.index[-1])
    dated = f"{dt:%m/%d} 時点 ／ 平均 {avg:,.2f} ／ 差 {v - avg:+,.2f} ／ 表示期間 {len(sp)}日分{plabel}"
    extra = ("<div class='t'>表示期間の統計</div>" + _stat_table([("倍率", sp.values, v, "")]) +
             _streak_html(sp.pct_change().dropna().mul(100).tolist(), "連続上昇（悪化）", "連続低下（良化）"))
    return _box(title, f"{v:,.2f}<small style='font-size:15px'>倍</small>", _col(ch), chip, kind,
                _spark(sp.values[-30:].tolist(), avg, color=_col(ch)), goals, stars=stars, dated=dated, extra=extra)


def _rec_gyaku(L_full, price_long):
    """この銘柄で逆日歩が出た日の、その後5営業日の上昇率を全体と比べる（件数が少ないときは参考）。"""
    try:
        if L_full is None or L_full.empty or "逆日歩" not in L_full.columns or price_long is None or price_long.empty:
            return None
        g = pd.to_numeric(L_full["逆日歩"], errors="coerce")
        days = pd.to_datetime(L_full.loc[g > 0, "_dt"]).dt.normalize().tolist()
        p = price_long.sort_values("_dt")
        c = pd.Series(pd.to_numeric(p["終値"], errors="coerce").values, index=pd.to_datetime(p["_dt"]).dt.normalize().values)
        c = c[~c.index.duplicated(keep="last")].dropna()
        fwd = (c.shift(-5) > c).where(c.shift(-5).notna())
        base = float(fwd.dropna().mean() * 100) if fwd.notna().any() else None
        ev = [fwd.get(d) for d in days if d in fwd.index]
        ev = [bool(x) for x in ev if x is not None and x == x]
        if not ev or base is None:
            return {"n": len(days), "n_ok": 0}
        return {"n": len(days), "n_ok": len(ev), "rate": sum(ev) / len(ev) * 100, "base": base}
    except Exception:
        return None


def _streak_before_today(chg):
    """今日より前の連続日数（前日が−なら何連落か）。"""
    if len(chg) < 2:
        return 0, 0
    sign = 1 if chg[-2] >= 0 else -1
    k = 0
    for v in reversed(chg[:-1]):
        if (1 if v >= 0 else -1) == sign:
            k += 1
        else:
            break
    return k, sign


def _board_html(board, plabel=""):
    R = [b for b in board if b["side"] == "R"]
    L = [b for b in board if b["side"] == "L"]

    def judge(b):
        v = b["val"]
        if abs(v) < 1e-9:
            return "mid"
        return "good" if ((v > 0) if b["side"] == "R" else (v < 0)) else "bad"

    def row(b):
        v = b["val"]
        col = UP if v > 0 else DN if v < 0 else NE
        j = judge(b)
        cc = {"good": GOOD, "bad": BAD, "mid": MID}[j]
        mk = {"good": "◎", "bad": "✕", "mid": "－"}[j]
        return (f"<div class='sh-row'><span class='n'>{b['name']}</span><span class='v' style='color:{col}'>{b['text']}</span>"
                f"<span class='c' style='background:{cc}26;color:{cc};border:1.5px solid {cc}'>{mk}</span></div>")

    rg = sum(1 for b in R if judge(b) == "good")
    lg = sum(1 for b in L if judge(b) == "good")
    tot = len(R) + len(L)
    g = rg + lg
    ratio = g / tot if tot else 0
    c = GOOD if ratio >= 0.65 else BAD if ratio <= 0.35 else MID
    head = (f"<div style='display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;align-items:center'>"
            f"<div class='sh-bt'>🧭 方向の早見表　<span style='color:#8b949e;font-weight:500;font-size:12px'>右が全部「青」・左が全部「赤」なら、株価に最も良い形</span></div>"
            f"<div style='font-size:15px;font-weight:900;color:{c}'>好条件 {g} / {tot}　<span style='font-size:12px;color:#8b949e;font-weight:600'>（右の青 {rg}/{len(R)}・左の赤 {lg}/{len(L)}）</span></div></div>")
    colL = ("<div class='sh-box' style='border-color:#f8514966'><div class='sh-bt' style='color:#f85149'>◀ 下がる（赤）と好材料</div>"
            "<div style='font-size:11px;color:#8b949e;margin-bottom:4px'>各倍率・買い残・VIX など「軽くなるほど良い」項目</div>" + "".join(row(b) for b in L) + "</div>")
    colR = ("<div class='sh-box' style='border-color:#58a6ff66'><div class='sh-bt' style='color:#58a6ff'>▶ 上がる（青）と好材料</div>"
            "<div style='font-size:11px;color:#8b949e;margin-bottom:4px'>株価・RSI・MACD・売り残 など「増えるほど良い」項目</div>" + "".join(row(b) for b in R) + "</div>")
    foot = ("<div style='font-size:11px;color:#6e7681;margin-top:6px'>数字は直近の変化（前日比・前回比・5日変化など。項目名の括弧のとおり）。◎＝その列の向きに合っている／✕＝逆向き。"
            "機関の空売りだけは、増える＝赤（悪）／減る（買い戻し）＝青（好）で表示するため右列に置いています。" + (f" 統計は表示期間{plabel}。" if plabel else "") + "</div>")
    return f"<div class='sh-box' style='border-color:#30363d;margin-top:12px'>{head}<div class='sh-brd' style='margin-top:8px'>{colL}{colR}</div>{foot}</div>"


# ───────────────────────── 描画本体 ─────────────────────────
def render(code, info, tech, L_full, M, MS, pr, prc, market_data=None, sell_thr=8000.0, short_thr=40.0,
           pl_thr=-10.0, plb_thr=-3.0, period_days=0, period_label=""):
    items = []          # 好悪バランス用 {kind, w, group}
    board = []          # 方向の早見表用 {side: R/L, name, val, text}
    plabel = f"（{period_label}）" if period_label else ""
    cut = (pd.Timestamp.today().normalize() - pd.Timedelta(days=int(period_days))) if period_days and period_days > 0 else None
    out_price, out_sup, out_mkt = [], [], []
    name = _html.escape(str(info.get("name", "")))
    tech = tech or {}
    chg5 = [float(v) for v in tech.get("chg5", []) if _f(v) is not None]
    close = _f(tech.get("close"))
    chg = _f(tech.get("chg"))
    avg_close = _f((tech.get("avg") or {}).get("close"))

    # ───── 株価 ─────
    hero = ""
    tile1 = _tile("① 株価の向き", "—", "株価データが不足しています", NE)
    if close is not None and len(chg5) >= 1:
        today = chg5[-1] if chg is None else chg
        k, sg = _streak_before_today(chg5)
        if len(chg5) >= 2:
            prev = chg5[-2]
            acc = today - prev
            if today >= 0 and prev < 0:
                head = "↗ 反発の初動"
            elif today >= 0:
                head = "↗ 上昇継続"
            elif prev >= 0:
                head = "↘ 下落の初動"
            else:
                head = "↘ 下落継続"
            word = "連落" if sg < 0 else "連騰"
            sub = (f"{k}{word}のあと {today:+.2f}%。" if k >= 1 else f"今日 {today:+.2f}%。")
            sub += f"前日より <b>{acc:+.2f}pt {'加速' if acc > 0 else '減速' if acc < 0 else ''}</b>"
        else:
            head, sub, acc = ("↗ 上昇" if today >= 0 else "↘ 下落"), f"今日 {today:+.2f}%", None
        tile1 = _tile("① 株価の向き", head, sub, _col(today))
        board.append(dict(side="R", name="株価（前日比）", val=today, text=f"{today:+.2f}%"))
        dlab = [d[-5:] for d in tech.get("dates", [])][-len(chg5):]
        if len(dlab) != len(chg5):
            dlab = [""] * len(chg5)
        s5 = sum(chg5)
        d_avg = (f"期間平均 {avg_close:,.0f} ／ 差 <b style='color:{_col(close - avg_close)}'>{close - avg_close:+,.0f}（{(close/avg_close-1)*100:+.1f}%）</b>"
                 if avg_close else "")
        hero = ("<div class='sh-box' style='border-color:#58a6ff;background:rgba(56,139,253,.08)'><div class='sh-hero'><div>"
                "<div class='sh-bt'>終値（円）</div>"
                f"<div class='sh-bv' style='font-size:50px;color:#f0f6fc'>{close:,.0f}"
                f"<span class='sh-pill' style='background:{_col(today)}22;color:{_col(today)};border:2px solid {_col(today)}'>{today:+.2f}%</span></div>"
                f"<div class='sh-sub' style='font-size:14px'>{d_avg}</div>"
                "<div class='sh-speed'>"
                f"<div class='sh-sp'><div class='a'>{len(chg5)}日の合計</div><div class='b' style='color:{_col(s5)}'>{s5:+.1f}%</div></div>"
                + (f"<div class='sh-sp'><div class='a'>勢い（今日−昨日）</div><div class='b' style='color:{_col(acc)}'>{'▲' if acc >= 0 else '▼'} {acc:+.2f}pt</div></div>" if acc is not None else "")
                + "</div></div>"
                "<div><div style='font-size:13px;color:#8b949e;font-weight:700;margin-bottom:2px'>直近の前日比（%）　"
                "<span style='color:#6e7681;font-weight:500'>昨日までの動きもひと目で</span></div>"
                + _bars(chg5, dlab) + "</div></div></div>")

    # トレンド転換ライン（株価テーブルの TR_up / TR_dn と同じ定義）
    bbu, bbl, ma25, sar = (_f(tech.get(k)) for k in ("bb_u", "bb_l", "ma25", "sar"))
    if close is not None and None not in (bbu, bbl, ma25):
        s1u, s1l = ma25 + (bbu - ma25) / 2, ma25 - (bbu - ma25) / 2
        cand = [("+2σ", bbu, "up"), ("+1σ", s1u, "up"), ("25日線", ma25, "both"), ("SAR", sar, "both"),
                ("−1σ", s1l, "dn"), ("−2σ", bbl, "dn")]
        lines = []
        for nm, val, side in cand:
            if val is None:
                continue
            if val > close and side in ("up", "both"):
                lines.append((nm, val, "up"))
            elif val < close and side in ("dn", "both"):
                lines.append((nm, val, "dn"))
        ups = sorted([x for x in lines if x[2] == "up"], key=lambda x: x[1])
        dns = sorted([x for x in lines if x[2] == "dn"], key=lambda x: -x[1])
        pts = [(close, "● 今日<br>" + f"{close:,.0f}", "#f0f6fc")]
        for nm, val, sd in lines:
            pts.append((val, f"{nm}<br>{val:,.0f}<br><span style='font-weight:500'>{(val/close-1)*100:+.1f}%</span>",
                        UP if sd == "up" else DN))
        pts.sort(key=lambda x: x[0])
        lo_, hi_ = pts[0][0], pts[-1][0]
        pad = (hi_ - lo_) * 0.10 or close * 0.02
        rail_items = [(v, lb, c, i % 2 == 1) for i, (v, lb, c) in enumerate(pts)]
        goals = []
        if ups:
            u = ups[0]
            pace = sum(chg5) / len(chg5) if chg5 else 0
            est = ""
            if pace > 0:
                need = (u[1] / close - 1) * 100
                est = f"<br>直近の平均ペース（{pace:+.2f}%/日）が続けば 約<b>{max(1, int(np.ceil(need / pace)))}営業日</b>で到達（目安）"
            goals.append(f"<b style='color:{UP}'>上に抜けるとTR_up(薄青)</b>：いちばん近いのは <b>{u[0]}（{u[1]:,.0f}円・{(u[1]/close-1)*100:+.1f}%）</b>"
                         + (f"、次に{ups[1][0]}（{(ups[1][1]/close-1)*100:+.1f}%）" if len(ups) > 1 else "") + est)
        else:
            goals.append(f"<b style='color:{UP}'>上に抜けるとTR_up</b>：対象ライン（+2σ・+1σ・25日線・SAR）はすべて下側です")
        if dns:
            d_ = dns[0]
            goals.append(f"<b style='color:{DN}'>下に抜けるとTR_dn(赤)</b>：いちばん近いのは <b>{d_[0]}（{d_[1]:,.0f}円・{(d_[1]/close-1)*100:+.1f}%）</b>"
                         + (f"、次に{dns[1][0]}（{(dns[1][1]/close-1)*100:+.1f}%）" if len(dns) > 1 else ""))
        else:
            goals.append(f"<b style='color:{DN}'>下に抜けるとTR_dn</b>：対象ライン（−2σ・−1σ・25日線・SAR）はすべて上側です")
        if tech.get("tr_up"):
            kind, chip = "good", "今日 TR_up 点灯：" + str(tech.get("trend") or "")
        elif tech.get("tr_dn"):
            kind, chip = "bad", "今日 TR_dn 点灯：" + str(tech.get("trend") or "")
        else:
            kind, chip = "mid", "今日はトレンド通過なし"
        items.append(dict(kind=kind, w=1, group="price"))
        dev = _f(tech.get("dev25"))
        out_price.append(_box("🧭 トレンド転換ライン（株価テーブルの色の元）", _s(dev, 2, True, "<small style='font-size:15px'>%</small>") if dev is not None else "—",
                              _col(dev), "25日乖離率　" + chip, kind, _rail(rail_items, lo_ - pad, hi_ + pad), goals,
                              sub="今日から右へ何%でそのラインに届くか（青＝上抜け対象／赤＝下抜け対象）"))

    pd25 = [v for v in tech.get("p_dev25", []) if v is not None]
    if len(pd25) >= 2:
        board.append(dict(side="R", name="25日乖離率（前日差）", val=pd25[-1] - pd25[-2], text=f"{pd25[-1] - pd25[-2]:+.2f}pt"))

    # MACDヒストグラム（株価）
    closes = tech.get("closes_all") or []
    mc = macd_calc(closes) if len(closes) >= 4 else None
    h15 = [v for v in tech.get("hist15", []) if _f(v) is not None]
    mh = _f(tech.get("macd_hist"))
    if mh is not None and len(h15) >= 2:
        hp = h15[-2]
        kind = "good" if mh > hp else "bad" if mh < hp else "mid"
        items.append(dict(kind=kind, w=1, group="price"))
        board.append(dict(side="R", name="MACDヒスト（前日差）", val=mh - hp, text=f"{mh - hp:+.2f}"))
        run = 1
        for a, b in zip(reversed(h15[:-1]), reversed(h15[1:])):
            if (b > a) == (mh > hp) and b != a:
                run += 1
            else:
                break
        run -= 1
        if mh < 0:
            chip = (f"縮小{max(run,1)}日連続 → GCに接近" if mh > hp else f"拡大{max(run,1)}日連続 → 下落圧力が強まる")
        else:
            chip = (f"拡大{max(run,1)}日連続 → 上昇の勢い" if mh > hp else f"縮小{max(run,1)}日連続 → DCに接近")
        goals = []
        if mc is not None and close:
            if mc["target"] == "GC":
                goals.append(f"明日の終値が <b>{mc['x_cross']:,.0f}円以上（{mc['pct']:+.1f}%）</b>でゴールデンクロス")
            else:
                goals.append(f"明日の終値が <b>{mc['x_cross']:,.0f}円以下（{mc['pct']:+.1f}%）</b>でデッドクロス")
            goals.append(f"横ばいなら <b>{mc['days_flat']}営業日後</b>に{mc['target']}" if mc["days_flat"]
                         else f"横ばいなら{mc['horizon']}営業日以内はクロスしません")
        out_price.append(_box("📉 MACDヒストグラム", _s(mh, 2), _col(mh), chip, kind, _hist_bars(h15), goals,
                              sub="棒＝直近のヒスト（青＋ 赤−）"))

    # RSI
    rsi = _f(tech.get("rsi"))
    r4 = [v for v in tech.get("rsi4", []) if _f(v) is not None]
    if rsi is not None:
        d3 = (rsi - r4[0]) if len(r4) >= 2 else 0.0
        if rsi >= 70:
            kind, chip = "bad", "買われすぎ（過熱）"
        elif rsi <= 30:
            kind, chip = "good", "売られすぎ（反発余地）"
        elif d3 >= 3:
            kind, chip = "good", "上昇中"
        elif d3 <= -3:
            kind, chip = "bad", "低下中"
        else:
            kind, chip = "mid", "中立圏"
        chip += f"／{max(len(r4)-1,1)}日で {d3:+.1f}"
        items.append(dict(kind=kind, w=1, group="price"))
        board.append(dict(side="R", name=f"RSI（{max(len(r4)-1,1)}日変化）", val=d3, text=f"{d3:+.1f}pt"))
        out_price.append(_box("🌡 RSI(14)", f"{rsi:.1f}", _col(d3), chip, kind,
                              _gauge(rsi, [(0, 30, "#1c3d63"), (30, 70, "#1c2331"), (70, 100, "#5a1f24")], [(50, "50")], ("0 売られすぎ", "買われすぎ 100")),
                              [f"中立の50まで <b>{50 - rsi:+.1f}</b>／売られすぎ(30)まで <b>{30 - rsi:+.1f}</b>／買われすぎ(70)まで <b>{70 - rsi:+.1f}</b>"],
                              extra="<div class='t'>表示期間の統計</div>" + _stat_table([("RSI", tech.get("p_rsi", []), rsi, "", 1)])
                              + f"<div class='l'>期間中 RSI70以上の日 <b>{sum(1 for v in tech.get('p_rsi', []) if v is not None and v >= 70)}日</b>／30以下の日 <b>{sum(1 for v in tech.get('p_rsi', []) if v is not None and v <= 30)}日</b></div>"))

    # パラボリックSAR（陽転／陰転）
    sb = tech.get("p_sar_bull") or []
    if sb and sar is not None and close:
        bull = bool(sb[-1])
        run = 1
        for v_ in reversed(sb[:-1]):
            if bool(v_) == bull:
                run += 1
            else:
                break
        dist = (sar / close - 1) * 100
        kind = "good" if bull else "bad"
        items.append(dict(kind=kind, w=1, group="price"))
        board.append(dict(side="R", name="パラボリックSAR", val=(1 if bull else -1), text=("陽転中" if bull else "陰転中")))
        since = f"{run}日目" if run < len(sb) else f"{run}日以上（表示期間の初めから）"
        if bull:
            g = (f"株価が <b>SAR {sar:,.0f}円（{dist:+.1f}%）</b>まで下がると <b style='color:{DN}'>陰転</b>（株価テーブルのTR_dn）。"
                 "SARは日がたつほど株価に近づいてきます")
        else:
            g = f"株価が <b>SAR {sar:,.0f}円（{dist:+.1f}%）</b>を上に抜けると <b style='color:{UP}'>陽転</b>（株価テーブルのTR_up）"
        out_price.append(_box("🪂 パラボリックSAR（標準 0.02／0.2）", "陽転中" if bull else "陰転中", UP if bull else DN,
                              ("上昇トレンド側（SARが株価の下）" if bull else "下降トレンド側（SARが株価の上）") + f"／{since}", kind,
                              "<div style='height:4px'></div>", [g], sub=f"SAR {sar:,.0f}円 ／ 株価との距離 {dist:+.1f}%"))

    # 25日平均線の現在地
    pma = [v for v in tech.get("p_ma25", []) if v is not None]
    pdv = [v for v in tech.get("p_dev25", []) if v is not None]
    pcl = tech.get("p_close") or []
    if ma25 is not None and close and len(pma) >= 2 and len(pdv) >= 2:
        base_ = pma[-6] if len(pma) >= 6 else pma[0]
        slope = (pma[-1] / base_ - 1) * 100 if base_ else 0.0
        upm = slope > 0
        dev_now = (close / ma25 - 1) * 100
        kind = "good" if (close >= ma25 and upm) else "bad" if (close < ma25 and not upm) else "mid"
        items.append(dict(kind=kind, w=1, group="price"))
        board.append(dict(side="R", name="25日線の傾き（5日）", val=slope, text=f"{slope:+.2f}%"))
        dlo, dhi, dav = min(pdv), max(pdv), sum(pdv) / len(pdv)
        lv_lo, lv_av = ma25 * (1 + dlo / 100), ma25 * (1 + dav / 100)
        posd = (dev_now - dlo) / (dhi - dlo) * 100 if dhi > dlo else 50
        posa = (dav - dlo) / (dhi - dlo) * 100 if dhi > dlo else 50
        goals = [f"株価は25日線より <b>{dev_now:+.2f}%</b>（{close - ma25:+,.0f}円）。25日線まで <b>{(ma25 / close - 1) * 100:+.1f}%</b>",
                 f"乖離率が<b>期間の最低（{dlo:+.2f}%）</b>になる株価＝<b>{lv_lo:,.0f}円</b>（今から {(lv_lo / close - 1) * 100:+.1f}%）"
                 f"／<b>平均（{dav:+.2f}%）</b>なら <b>{lv_av:,.0f}円</b>（{(lv_av / close - 1) * 100:+.1f}%）"]
        out_price.append(_box("📏 25日平均線の現在地", f"{ma25:,.0f}<small style='font-size:15px'>円</small>", _col(slope),
                              f"傾き {'上向き' if upm else '下向き'}（5日で {slope:+.2f}%）", kind,
                              _gauge(posd, [(0, 100, "#1c2331")], [(posa, "平均")], (f"最低 {dlo:+.1f}%", f"最高 {dhi:+.1f}%")),
                              goals, sub="帯＝表示期間の乖離率（左端が最低・右端が最高）／白丸＝今の乖離率",
                              extra="<div class='t'>表示期間の統計</div>" + _stat_table([("25日線(円)", pma, ma25, "", 0), ("乖離率(%)", pdv, dev_now, "%"), ("終値(円)", pcl, close, "", 0)])))

    # 出来高・機関異常
    vol, vm = _f(tech.get("vol")), _f(tech.get("vm"))
    if vol is not None and vm:
        vr = vol / vm
        if tech.get("inst") or tech.get("volabn"):
            kind = "good" if (chg or 0) >= 0 else "bad"
            chip = ("大口機関判定" if tech.get("inst") else "出来高異常") + ("・上昇を伴う" if (chg or 0) >= 0 else "・下落を伴う")
        else:
            kind, chip = "mid", ("平常より少ない" if vr < 0.9 else "平常並み" if vr < 1.2 else "やや多い")
        items.append(dict(kind=kind, w=1, group="price"))
        goals = []
        goals.append("<b>機関異常</b>の条件＝出来高1.5倍超 かつ 値動き±1.5%以上" + (
            f" → 出来高があと <b>×{1.5 / vr:.2f}</b> 必要" if vr < 1.5 else "（出来高は条件を満たしています）"))
        goals.append("<b>出来高異常</b>(2倍超)まで " + (f"あと <b>×{2 / vr:.2f}</b>" if vr < 2 else "<b>到達済み</b>"))
        out_price.append(_box("📊 出来高・機関異常", f"{vr:.2f}<small style='font-size:15px'>倍</small>", "#c9d1d9", chip + f"（{vol:,.0f}株）", kind,
                              _gauge(min(vr / 2 * 100, 100), [(0, 75, "#1c2331"), (75, 100, "#3d2d00")], [(75, "1.5倍")], ("0倍", "2倍(異常)")),
                              goals, sub=f"トレンド通過：{tech.get('trend') or '—'} ／機関異常：{'あり' if tech.get('inst') else 'なし'} ／出来高異常：{'あり' if tech.get('volabn') else 'なし'}"))

    # 値動きの目安（表示期間の実績）
    pch = tech.get("p_chg") or []
    if len(pcl) >= 20:
        body = ("<div class='sh-bt'>🎯 値動きの目安（" + (period_label or "表示期間") + "の実績から）</div>"
                "<div class='sh-sub'>期間内のどの日に買っても、その後しばらくのあいだに「最大でどれだけ上がり（含み益）、どれだけ下がったか（含み損）」の平均です。"
                "期間を変えると数字も変わります。過去の傾向で、将来の保証ではありません。</div>"
                "<div class='sh-ext' style='border-top:none'><div class='t'>連騰・連落（株価の前日比）</div>" + _streak_html([v for v in pch]) + "</div>"
                "<div class='sh-ext'><div class='t'>買ったあとの値動き（最大の含み益・含み損）</div>" + _mfe_html(pcl, close) + "</div>")
        out_price.append(f"<div class='sh-box' style='border-color:#30363d;grid-column:1/-1'>{body}</div>")

    # ───── 需給 ─────
    s_seido = _ratio_series(MS, col="制度信用倍率")
    s_ippan = _ratio_series(MS, num="一般買い残高", den="一般売り残高")
    s_lend = _ratio_series(L_full, col="貸借倍率")
    s_kab = _ratio_series(M, col="信用倍率")
    out_sup.append(_ratio_box(items, board, "制度信用取引倍率（JPX・株数）", "制度信用取引倍率", s_seido, "★★★", 3, "sup", cut, plabel))
    out_sup.append(_ratio_box(items, board, "一般信用取引倍率（JPX・株数）", "一般信用取引倍率", s_ippan, "★★", 2, "sup", cut, plabel))
    out_sup.append(_ratio_box(items, board, "貸借取引倍率（日証金）", "貸借取引倍率", s_lend, "★", 1, "sup", cut, plabel))
    out_sup.append(_ratio_box(items, board, "信用倍率（一般＋制度の合計・株探・日次）", "信用倍率（一般＋制度・株探）", s_kab, "★", 1, "sup", cut, plabel))

    # 信用需給（残高の増減）… 制度(JPX)を優先、無ければ株探
    def _chg_last(df, col):
        if df is None or len(df) == 0 or col not in df.columns:
            return None
        v = pd.to_numeric(df.sort_values("_dt")[col], errors="coerce").replace(0, np.nan).dropna()
        return float((v.iloc[-1] / v.iloc[-2] - 1) * 100) if len(v) >= 2 else None

    bch, sch, basis, st_stars, wt = _chg_last(MS, "制度買い残高"), _chg_last(MS, "制度売り残高"), "制度信用取引ベース（JPX）", "★★★", 3
    if bch is None or sch is None:
        bch = _f(pd.to_numeric(M.sort_values("_dt")["買い残増減率"], errors="coerce").dropna().iloc[-1]) if (M is not None and len(M) and "買い残増減率" in M.columns and M["買い残増減率"].notna().any()) else None
        sch = _f(pd.to_numeric(M.sort_values("_dt")["売り残増減率"], errors="coerce").dropna().iloc[-1]) if (M is not None and len(M) and "売り残増減率" in M.columns and M["売り残増減率"].notna().any()) else None
        basis, st_stars, wt = "株探（一般＋制度の合計）", "★", 1
    if bch is not None and sch is not None:
        if sch > bch and sch > 0:
            kind, big, chip = "good", "売り残 増加", "倍率を押し下げる動き（ショートカバー予備軍）"
        elif bch > sch and bch > 0:
            kind, big, chip = "bad", "買い残 増加", "倍率を押し上げる動き（将来の売り圧力）"
        elif sch < 0 and bch < 0:
            kind, big, chip = ("bad", "売り残 減少", "買い戻しが進み、倍率は上がりやすい") if sch < bch else ("good", "買い残 減少", "買い方が整理され、需給は軽くなる")
        else:
            kind, big, chip = "mid", "拮抗", "買い残・売り残の増減がほぼ同じ"
        items.append(dict(kind=kind, w=wt, group="sup"))
        board.append(dict(side="R", name="売り残の増加（前回比）", val=sch, text=f"{sch:+.2f}%"))
        board.append(dict(side="L", name="買い残の増加（前回比）", val=bch, text=f"{bch:+.2f}%"))
        bw = lambda v: min(abs(v) / 20 * 100, 100)
        two = ("<div style='margin:6px 0'><div style='font-size:12px;color:#8b949e'>買い残の増加（前回比）</div>"
               f"<div style='background:#0d1117;border-radius:6px;height:22px;position:relative'><div style='width:{max(bw(bch),3):.0f}%;height:100%;background:{_col(bch)};border-radius:6px'></div>"
               f"<span style='position:absolute;left:{min(max(bw(bch),3)+3,70):.0f}%;top:2px;font-size:13px;font-weight:800;color:{_col(bch)}'>{bch:+.2f}%</span></div>"
               "<div style='font-size:12px;color:#8b949e;margin-top:6px'>売り残の増加（前回比）</div>"
               f"<div style='background:#0d1117;border-radius:6px;height:22px;position:relative'><div style='width:{max(bw(sch),3):.0f}%;height:100%;background:{_col(sch)};border-radius:6px'></div>"
               f"<span style='position:absolute;left:{min(max(bw(sch),3)+3,70):.0f}%;top:2px;font-size:13px;font-weight:800;color:{_col(sch)}'>{sch:+.2f}%</span></div></div>")
        bigcol = _col(sch if kind == "good" else bch)
        out_sup.append(_box("信用残高の増減（前回比）", big, bigcol, chip, kind, two,
                            ["売り残が増えるほど、いつか買い戻される量が増える（倍率の低下圧力）。買い残が増えるほど、将来の売り圧力"],
                            sub=basis, stars=st_stars))

    # 機関投資家の空売り（増加＝赤・悪材料／減少＝買い戻し＝青・好材料）
    SI = info.get("short_inst") or {}
    raw = SI.get("raw", pd.DataFrame()) if isinstance(SI, dict) else pd.DataFrame()
    bi = SI.get("by_institution", pd.DataFrame()) if isinstance(SI, dict) else pd.DataFrame()
    tot, pv = None, None
    try:
        if raw is not None and len(raw) and {"_dt", "空売り機関", "残高比率"} <= set(raw.columns):
            pv = raw.pivot_table(index="_dt", columns="空売り機関", values="残高比率", aggfunc="last").sort_index().ffill()
            pv.index = pd.to_datetime(pv.index)
            tot = pv.sum(axis=1)
    except Exception:
        tot, pv = None, None
    if tot is not None and len(tot) >= 1:
        tp = tot[tot.index >= cut] if cut is not None else tot
        if len(tp) < 2:
            tp = tot
        cur = float(tot.iloc[-1])
        chg_ = float(tot.iloc[-1] - tot.iloc[-2]) if len(tot) >= 2 else None
        if chg_ is None or abs(chg_) < 0.005:
            kind, col_, chip = "mid", NE, "ほぼ変化なし" if chg_ is not None else "比較できる過去データなし"
        elif chg_ > 0:
            kind, col_, chip = "bad", DN, f"空売り増加 {chg_:+.2f}pt（売り圧力↑）"
        else:
            kind, col_, chip = "good", UP, f"空売り減少 {chg_:+.2f}pt ＝ 買い戻し（好材料）"
        items.append(dict(kind=kind, w=2, group="sup"))
        if chg_ is not None:
            if abs(chg_) < 0.005:
                board.append(dict(side="R", name="機関の空売り（減＝青）", val=0.0, text="変化なし"))
            else:
                board.append(dict(side="R", name="機関の空売り（減＝青）", val=-chg_, text=f"{'減少' if chg_ < 0 else '増加'} {chg_:+.2f}pt"))
        goals = []
        try:
            if pv is not None and len(pv) >= 2:
                dd = (pv.iloc[-1] - pv.iloc[-2]).dropna()
                dd = dd[dd.abs() >= 0.005]
                if len(dd):
                    inc = dd.sort_values(ascending=False).head(3)
                    dec = dd.sort_values().head(3)
                    parts_ = [f"<span style='color:{DN}'>{k}{v:+.2f}pt</span>" for k, v in inc.items() if v > 0] + \
                             [f"<span style='color:{UP}'>{k}{v:+.2f}pt</span>" for k, v in dec.items() if v < 0]
                    goals.append("直近の報告で動いた機関：" + "／".join(parts_))
        except Exception:
            pass
        hi_t, lo_t = float(tp.max()), float(tp.min())
        goals.append(f"表示期間の最高 {hi_t:.2f}%（{cur - hi_t:+.2f}pt）／最低 {lo_t:.2f}%（{cur - lo_t:+.2f}pt）。減るほど好材料")
        extra = ("<div class='t'>表示期間の統計</div>" + _stat_table([("合計残高比率", tp.values, cur, "%")]) +
                 _streak_html(tp.pct_change().replace([np.inf, -np.inf], np.nan).dropna().mul(100).tolist(), "連続増加（売り圧力↑）", "連続減少（買い戻し）"))
        out_sup.append(_box("機関投資家の空売り残高（報告ベースの合計）", f"{cur:.2f}<small style='font-size:15px'>%</small>", col_, chip, kind,
                            _spark(tp.values[-30:].tolist(), float(tp.mean()), color=col_), goals, stars="★★",
                            dated=f"{tot.index[-1]:%m/%d} 時点（各機関の最新の報告を足し合わせた目安）", extra=extra,
                            sub="増加＝赤（悪材料）／減少＝買い戻し＝青（好材料）"))
    elif bi is not None and len(bi) and "残高比率" in bi.columns:
        tot_ = float(pd.to_numeric(bi["残高比率"], errors="coerce").sum())
        items.append(dict(kind="mid", w=0, group="sup"))
        out_sup.append(_box("機関投資家の空売り残高", f"{tot_:.2f}<small style='font-size:15px'>%</small>", NE, f"{len(bi)}機関の合計", "mid",
                            "<div style='height:6px'></div>", ["時系列データがまだ無いため増減は判定できません"], stars="★★"))

    # 逆日歩（格下げ：明確なラインがないため、この銘柄の過去実績で判定）
    gy = None
    try:
        if L_full is not None and len(L_full) and "逆日歩" in L_full.columns:
            gv = pd.to_numeric(L_full.sort_values("_dt")["逆日歩"], errors="coerce").dropna()
            gy = float(gv.iloc[-1]) if len(gv) else None
    except Exception:
        gy = None
    rec = _rec_gyaku(L_full, info.get("price_long") if len(info.get("price_long", [])) else info.get("price"))
    kind, rtxt, wtg = "mid", "<span style='color:#6e7681'>過去実績を計算できませんでした</span>", 0
    if rec:
        if rec.get("n_ok", 0) == 0:
            rtxt = f"この銘柄で逆日歩が出た日は蓄積上 <b>{rec['n']}回</b>。その後5日の値動きを比べられるデータがまだありません"
        else:
            diff = rec["rate"] - rec["base"]
            ref = "" if rec["n_ok"] >= 5 else "（件数が少ないため参考）"
            rtxt = (f"この銘柄で逆日歩が出た過去 <b>{rec['n_ok']}回</b>：その後5日で上昇 "
                    f"<b style='color:{UP}'>{rec['rate']:.0f}%</b>（全体の上昇率 {rec['base']:.0f}%）{ref}")
            if rec["n_ok"] >= 5 and abs(diff) >= 10:
                kind, wtg = ("good" if diff > 0 else "bad"), 1
    items.append(dict(kind=kind, w=wtg, group="sup"))
    out_sup.append(_box("逆日歩（円）", _s(gy, 2) if gy is not None else "—", NE,
                        "あり（売り方にコスト）" if (gy or 0) > 0 else "なし", kind,
                        f"<div style='font-size:13px;color:#c9d1d9;line-height:1.7'>{rtxt}</div>",
                        ["明確なラインがない補助指標のため小さなフラグに格下げ。代わりに<b>この銘柄の実績</b>で材料を判定します"],
                        sub="件数が少ないときは「参考」表示"))

    # ───── 市場の地合い ─────
    mkt_note = ""
    try:
        import market as _M
        data = market_data if market_data is not None else _M.load_all()
        tb = _M.build_tables(data, shift_vix=False)
        t1, macd = tb.get("t1"), tb.get("macd")
        wk = data.get("weekly")
        # 評価損益率（週次）
        if wk is not None and len(wk) and "pl" in wk.columns and wk["pl"].notna().any():
            w2 = wk["pl"].dropna()
            pl, pdt = float(w2.iloc[-1]), w2.index[-1]
            if len(w2) >= 2:
                board.append(dict(side="R", name="評価損益率（週次）", val=pl - float(w2.iloc[-2]), text=f"{pl - float(w2.iloc[-2]):+.2f}pt"))
            kind = "bad" if pl < pl_thr else "good" if pl >= plb_thr else "mid"
            items.append(dict(kind=kind, w=3, group="mkt"))
            chip = ("含み損10%超 ＝ 投げ売りが出やすい下落基調" if kind == "bad" else
                    f"損失が小さい好景気ゾーン（{plb_thr:g}%以上）" if kind == "good" else "中間ゾーン")
            span = 30.0
            pos = lambda v: min(max((v + span) / span * 100, 0), 100)
            avg_pl = float(w2.mean())
            out_mkt.append(_box(f"市場の信用評価損益率（週次 {pdt:%m/%d}）", _s(pl, 2, False, "<small style='font-size:15px'>%</small>"), _col(pl), chip, kind,
                                _gauge(pos(pl), [(0, pos(pl_thr), "#5a1f24"), (pos(pl_thr), pos(plb_thr), "#1c2331"), (pos(plb_thr), 100, "#1c3d63")],
                                       [(pos(pl_thr), f"{pl_thr:g}%"), (pos(plb_thr), f"{plb_thr:g}%")], (f"−{span:g}%", "0%")),
                                [f"{pl_thr:g}%ラインまで <b>{pl_thr - pl:+.2f}pt</b>／{plb_thr:g}%ラインまで <b>{plb_thr - pl:+.2f}pt</b>",
                                 f"平均 {avg_pl:.2f}% ／ 差 {pl - avg_pl:+.2f}pt"], stars="★★★", dated=f"週次（{pdt:%m/%d}）"))
        # VIX のMACD
        vix = data.get("vix")
        if macd is not None and len(macd) >= 40:
            o = _M.macd_outlook(macd)
            if o:
                h = o["hist"]
                _cc = macd["c"].dropna()
                if len(_cc) >= 2:
                    board.append(dict(side="L", name="VIX（前日比）", val=(float(_cc.iloc[-1]) / float(_cc.iloc[-2]) - 1) * 100,
                                      text=f"{(float(_cc.iloc[-1]) / float(_cc.iloc[-2]) - 1) * 100:+.1f}%"))
                kind = "bad" if h > 0 else "good"
                items.append(dict(kind=kind, w=3, group="mkt"))
                sel = macd[macd["cross"] != ""]
                lastx = f"{sel.index[-1]:%m/%d}に{'GC' if sel['cross'].iloc[-1]=='GC' else 'DC'}" if len(sel) else "クロスなし"
                chip = f"{lastx} → " + ("下落基調（MACD＞シグナル）" if h > 0 else "上昇基調（MACD＜シグナル）")
                if o["target"] == "DC":
                    g1 = f"明日のVIXが <b>{o['x_cross']:.2f}以下（{o['pct']:+.1f}%）</b>でデッドクロス＝上昇転換の目安"
                else:
                    g1 = f"明日のVIXが <b>{o['x_cross']:.2f}以上（{o['pct']:+.1f}%）</b>でゴールデンクロス＝下落転換の注意"
                g1 += (f"<br>横ばいなら <b>{o['days_flat']}営業日後</b>" if o["days_flat"] else f"<br>横ばいなら{o['horizon']}営業日以内はクロスしません")
                out_mkt.append(_box(f"VIX（{o['date']:%m/%d}確定）のMACD", _s(h, 2, True), _col(h), chip, kind,
                                    _hist_bars(macd["hist"].tolist()[-15:]), [g1], stars="★★★"))
        # 空売り比率 40%
        shd = data.get("short")
        if shd is not None and len(shd) and "short" in shd.columns and shd["short"].notna().any():
            sv = shd["short"].dropna()
            v, vd = float(sv.iloc[-1]), sv.index[-1]
            if len(sv) >= 2:
                board.append(dict(side="R", name="空売り比率（全市場・前日差）", val=v - float(sv.iloc[-2]), text=f"{v - float(sv.iloc[-2]):+.1f}pt"))
            hit = v >= short_thr
            kind = "good" if hit else "mid"
            items.append(dict(kind=kind, w=3, group="mkt"))
            hi_s = max(60.0, short_thr * 1.5)
            p = lambda x: min(max(x / hi_s * 100, 0), 100)
            out_mkt.append(_box(f"空売り比率（全市場・{vd:%m/%d}）", _s(v, 1, False, "<small style='font-size:15px'>%</small>"), "#c9d1d9",
                                f"{short_thr:g}%以上＝売られすぎ水準（買い戻し余地・さらに好材料）" if hit else f"{short_thr:g}%未満（通常ゾーン）", kind,
                                _gauge(p(v), [(0, p(short_thr), "#1c2331"), (p(short_thr), 100, "#1c3d63")], [(p(short_thr), f"{short_thr:g}%")], ("0%", f"{hi_s:g}%")),
                                [(f"{short_thr:g}%ラインを <b>{v - short_thr:+.1f}pt</b> 超えています" if hit else f"{short_thr:g}%ラインまで <b>あと {short_thr - v:+.1f}pt</b>")],
                                stars="★★★", dated=f"{vd:%m/%d} 時点（当日分）"))
        # 売り残金額 8000億
        if t1 is not None and len(t1) and "sell_k" in t1.columns and t1["sell_k"].notna().any():
            tt = t1["sell_k"].dropna().sort_index()
            v, vd = float(tt.iloc[-1]), tt.index[-1]
            if len(tt) >= 2 and float(tt.iloc[-2]):
                board.append(dict(side="R", name="市場の売り残（前回比）", val=(v / float(tt.iloc[-2]) - 1) * 100, text=f"{(v / float(tt.iloc[-2]) - 1) * 100:+.1f}%"))
            hit = v > sell_thr
            kind = "good" if hit else "mid"
            items.append(dict(kind=kind, w=3, group="mkt"))
            hi_s = max(sell_thr * 1.5, v * 1.1)
            p = lambda x: min(max(x / hi_s * 100, 0), 100)
            out_mkt.append(_box(f"市場の売り残（①一般+制度・{vd:%m/%d}）", f"{v:,.0f}<small style='font-size:15px'>億円</small>", "#c9d1d9",
                                f"{sell_thr:,.0f}億円超＝売り残が積み上がった水準（ショートカバー余地・さらに好材料）" if hit else f"{sell_thr:,.0f}億円未満（通常ゾーン）", kind,
                                _gauge(p(v), [(0, p(sell_thr), "#1c2331"), (p(sell_thr), 100, "#1c3d63")], [(p(sell_thr), f"{sell_thr:,.0f}")], ("0", f"{hi_s:,.0f}億")),
                                [(f"{sell_thr:,.0f}億円ラインを <b>{v - sell_thr:+,.0f}億円</b> 超えています" if hit else f"{sell_thr:,.0f}億円ラインまで <b>あと {sell_thr - v:+,.0f}億円</b>")],
                                stars="★★★", dated=f"{vd:%m/%d} 時点"))
    except Exception as e:
        print(f"[新ヘッド 市場の地合い] {e}")
        mkt_note = "市場データを読み込めませんでした（市場全体ページで一度更新すると表示されます）"

    # ───── 総括 ─────
    def grp_score(g):
        return sum((1 if i["kind"] == "good" else -1 if i["kind"] == "bad" else 0) * i["w"] for i in items if i["group"] == g)

    t2txt, _, t2c = _score_to_text(grp_score("sup"))
    sup_good = [i for i in items if i["group"] == "sup" and i["kind"] == "good" and i["w"] >= 2]
    sup_bad = [i for i in items if i["group"] == "sup" and i["kind"] == "bad"]
    tile2 = _tile("② 需給の材料（データの信頼度で加重）", t2txt,
                  "制度信用取引・一般信用取引（★★★/★★）が材料の中心。貸借取引・株探（★）は参考として軽く数えます", t2c)
    t3txt, _, t3c = _score_to_text(grp_score("mkt"))
    m_hit = [i for i in items if i["group"] == "mkt" and i["kind"] == "good"]
    tile3 = _tile("③ 市場の地合い", t3txt if any(i["group"] == "mkt" for i in items) else "—",
                  (f"好材料サイン {len(m_hit)}件（評価損益率・VIX・空売り比率{short_thr:g}%・売り残{sell_thr:,.0f}億）"
                   if any(i["group"] == "mkt" for i in items) else (mkt_note or "市場データなし")), t3c if any(i["group"] == "mkt" for i in items) else NE)

    cnt = {k: sum(1 for i in items if i["kind"] == k) for k in ("good", "mid", "bad")}
    wt = {k: sum(max(i["w"], 0.5) for i in items if i["kind"] == k) for k in ("good", "mid", "bad")}
    tw = sum(wt.values()) or 1
    bal = ("<div class='sh-box' style='border-color:#30363d;margin-top:12px'>"
           "<div style='display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px'><div class='sh-bt'>⚖ 好悪バランス（データの信頼度で重み付け）</div>"
           f"<div style='font-size:13px;color:#8b949e'>好材料 <b style='color:{GOOD}'>{cnt['good']}</b> ／ 中立 <b>{cnt['mid']}</b> ／ 悪材料 <b style='color:{BAD}'>{cnt['bad']}</b>（全{len(items)}項目）</div></div>"
           "<div class='sh-bal'>"
           f"<div style='width:{wt['good']/tw*100:.1f}%;background:{GOOD}'>{'好材料 ' + str(cnt['good']) if cnt['good'] else ''}</div>"
           f"<div style='width:{wt['mid']/tw*100:.1f}%;background:{MID}'>{'中立 ' + str(cnt['mid']) if cnt['mid'] else ''}</div>"
           f"<div style='width:{wt['bad']/tw*100:.1f}%;background:{BAD}'>{'悪材料 ' + str(cnt['bad']) if cnt['bad'] else ''}</div></div>"
           "<div style='font-size:12px;color:#8b949e'>判定は各項目の「◎／✕／－」バッジと同じ。制度信用取引・市場は×3、一般信用取引は×2、それ以外は×1で、帯の長さに反映しています（件数は上の数字）。</div></div>")

    legend_top = ("<div class='sh-box' style='border-color:#30363d;padding:8px 14px;margin-bottom:12px'><div style='font-size:13px;line-height:1.9'>"
                  "<b>色の読み方（2段構え）</b>　<b>数字の色</b>＝動いた向き：<span style='color:#58a6ff;font-weight:800'>青＝＋（上がった）</span> ／ "
                  "<span style='color:#f85149;font-weight:800'>赤＝−（下がった）</span>　　<b>バッジ・枠</b>＝株価への材料：<span style='color:#3fb950;font-weight:800'>◎緑＝好材料</span> ／ "
                  "<span style='color:#f0883e;font-weight:800'>✕橙＝悪材料</span><br><span style='color:#a8b3c0'>株価・RSI・MACDは「青(＋)＝好材料」。各倍率（制度信用・一般信用・貸借）は逆で"
                  "「<b style='color:#f85149'>赤(−)＝倍率が下がる＝ショートカバー余地＝好材料</b>」。数字が赤でも緑バッジなら好材料です。</span></div></div>")

    ui_title = (f"<div style='font-size:30px;font-weight:800;color:#f0f6fc;margin:2px 0 4px'>{_html.escape(str(code))}　{name}"
                f"<small style='font-size:14px;font-weight:600;color:#8b949e;margin-left:10px'>{_html.escape(str(pr.get('label', '')))}</small></div>"
                f"<div style='background:#161b22;border-left:4px solid {prc};border-radius:6px;padding:8px 14px;margin:2px 0 8px'>"
                f"<span style='color:#8b949e;font-size:0.9rem'>{_html.escape(str(pr.get('detail', '')))}</span></div>")

    parts = [CSS, "<div class='sh-wrap'>", ui_title, legend_top,
             "<div class='sh-tiles'>" + tile1 + tile2 + tile3 + "</div>", _board_html(board, plabel), bal]
    if hero or out_price:
        parts.append(f"<div class='sh-sec'>📈 株価の現在地<small>▸ 青＝＋／赤＝−　※平均は表示期間</small></div>")
        if hero:
            parts.append(hero + "<div style='height:14px'></div>")
        if out_price:
            parts.append("<div class='sh-grid'>" + "".join(out_price) + "</div>")
    if out_sup:
        parts.append("<div class='sh-sec'>🏦 需給の現在地<small>▸ データの信頼度が高い順：制度信用取引（★★★）≧ 一般信用取引（★★）≧ 貸借取引（★）</small></div>"
                     "<div class='sh-sec2'>倍率が下がる＝赤(−)でも<b style='color:#3fb950'>好材料</b>（ショートカバー余地・株価上昇の裏返し）。"
                     "倍率が上がる＝青(＋)でも<b style='color:#f0883e'>悪材料</b>。平均・最低・最高は表示期間" + plabel + "、MACDは蓄積されたデータだけで計算しています。</div>")
        parts.append("<div class='sh-grid'>" + "".join(out_sup) + "</div>")
    if out_mkt:
        parts.append("<div class='sh-sec'>🌐 市場の地合い<small>▸ 市場全体ページの蓄積データと連動（明確なラインがある指標だけ）</small></div>"
                     "<div class='sh-grid'>" + "".join(out_mkt) + "</div>")
    elif mkt_note:
        parts.append(f"<div class='sh-sec'>🌐 市場の地合い</div><div class='sh-sub'>{mkt_note}</div>")
    parts.append("<div class='sh-legend'><b>見方</b>　数字の色：<span style='color:#58a6ff'>■青＝＋</span>　<span style='color:#f85149'>■赤＝−</span>　／　"
                 "バッジ・枠：<span style='color:#3fb950'>■緑＝好材料</span>　<span style='color:#f0883e'>■橙＝悪材料</span>　<span style='color:#8b949e'>■灰＝中立</span>　／　"
                 "<span style='color:#e3b341'>■黄の枠＝あとどれくらいで届くか</span>　／　白い丸・白枠の棒＝今日　／　点線＝平均　／　★＝データの信頼度（★が多いほど信頼できる）</div></div>")
    st.markdown("".join(parts), unsafe_allow_html=True)
