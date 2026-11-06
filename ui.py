"""
ui.py ― 市場全体ページと個別銘柄ページで共通に使う見た目の部品（中身の計算は一切しない）
  ・section(): 大きな見出し
  ・card_row(): 「今日の数字（大）＋ 平均 ＋ 差」を並べるカード行
"""
import streamlit as st

CSS = """<style>
.ui-title{font-size:30px;font-weight:800;color:#f0f6fc;letter-spacing:.02em;margin:2px 0 4px}
.ui-title small{font-size:14px;font-weight:600;color:#8b949e;margin-left:10px}
.ui-sec{font-size:20px;font-weight:800;color:#f0f6fc;border-left:5px solid #388bfd;
  padding:2px 0 2px 10px;margin:18px 0 8px}
.ui-sec small{font-size:12px;font-weight:500;color:#8b949e;margin-left:8px}
.ui-card{background:#161b22;border:1px solid #30363d;border-left:5px solid #484f58;border-radius:8px;
  padding:10px 12px;min-height:104px}
.ui-lab{font-size:12px;color:#8b949e;font-weight:600;white-space:nowrap}
.ui-val{font-size:30px;font-weight:800;line-height:1.15;color:#f0f6fc;margin:2px 0}
.ui-val small{font-size:14px;font-weight:600;margin-left:4px}
.ui-sub{font-size:12px;color:#8b949e;line-height:1.35}
.ui-sub b{color:#c9d1d9}
.ui-badge{display:inline-block;font-size:11px;font-weight:700;border-radius:4px;padding:1px 6px;margin-top:3px}
.ui-grp{font-size:13px;font-weight:700;color:#8b949e;margin:10px 0 4px;letter-spacing:.08em}
</style>"""

POS, NEG = "#58a6ff", "#f85149"


def inject():
    st.markdown(CSS, unsafe_allow_html=True)


def title(text, sub=""):
    st.markdown(f"<div class='ui-title'>{text}<small>{sub}</small></div>", unsafe_allow_html=True)


def section(text, sub=""):
    st.markdown(f"<div class='ui-sec'>{text}<small>{sub}</small></div>", unsafe_allow_html=True)


def group(text):
    st.markdown(f"<div class='ui-grp'>{text}</div>", unsafe_allow_html=True)


def _fmt(v, dec=2, suffix="", sign=False):
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"):
            return "-"
        return f"{f:{'+' if sign else ''},.{dec}f}{suffix}"
    except Exception:
        return "-"


def stat(label, value, avg=None, dec=2, suffix="", avg_label="平均", sign=False, note="",
         accent="#484f58", badge=None, badge_style=None, val_color=None, text=None):
    """カード1枚分の辞書。value/avg は数値（または None）。差 = value - avg を併記する。"""
    d = {"label": label, "value": _fmt(value, dec, suffix, sign), "accent": accent, "note": note,
         "badge": badge, "badge_style": badge_style, "val_color": val_color, "avg": None, "diff": None}
    if text is not None:
        d["value"] = text
    try:
        if avg is not None and value is not None and float(avg) == float(avg) and float(value) == float(value):
            d["avg"] = f"{avg_label} <b>{_fmt(avg, dec, suffix)}</b>"
            diff = float(value) - float(avg)
            col = POS if diff >= 0 else NEG
            d["diff"] = f"差 <b style='color:{col}'>{_fmt(diff, dec, suffix, sign=True)}</b>"
    except Exception:
        pass
    return d


def card_row(items):
    """items = [stat(...) | dict, ...] を横一列のカードで表示。"""
    cols = st.columns(len(items))
    for c, it in zip(cols, items):
        sub = " ／ ".join(x for x in (it.get("avg"), it.get("diff")) if x)
        if it.get("note"):
            sub = (sub + "<br>" if sub else "") + it["note"]
        badge = (f"<div><span class='ui-badge' style='{it['badge_style'] or ''}'>{it['badge']}</span></div>"
                 if it.get("badge") else "")
        vc = f"color:{it['val_color']};" if it.get("val_color") else ""
        c.markdown(
            f"<div class='ui-card' style='border-left-color:{it['accent']}'>"
            f"<div class='ui-lab'>{it['label']}</div>"
            f"<div class='ui-val' style='{vc}'>{it['value']}</div>"
            f"<div class='ui-sub'>{sub}</div>{badge}</div>", unsafe_allow_html=True)
