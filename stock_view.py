"""
stock_view.py ― 個別銘柄ページの「頭のKPIボード」と「需給の一覧比較」（表示専用）
  ・計算の定義・色の定義は dashboard.py の既存表（株価テーブル/信用残）と同じものを参照するだけで、新しい定義は作らない。
  ・株価テーブルの行色（TR_up / TR_dn / 機関異常 / 出来高異常）の当日判定は、テクニカル表の計算結果をそのまま受け取る。
"""
import numpy as np
import pandas as pd
import streamlit as st

import ui

POS, NEG = "#58a6ff", "#f85149"
# 株価テーブルの行色と同じ（dashboard.py の sty_pt と同一の色）
ST_UP = "background:#0a1f35;color:#58a6ff;border:1px solid #1f6feb"
ST_DN = "background:#2a0d0d;color:#ff7b72;border:1px solid #f85149"
ST_INST = "background:#3d0d0d;color:#ff7b72;border:1px solid #f85149"
ST_VOL = "background:#2d1f00;color:#e3b341;border:1px solid #9e6a03"


def _num(v):
    try:
        f = float(v)
        return None if (f != f) else f
    except Exception:
        return None


def _finite_mean(s):
    s = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(s.mean()) if len(s) else None


def _last(df, col):
    if df is None or df.empty or col not in df.columns:
        return None, None
    d = df.sort_values("_dt")
    d = d[d[col].notna()]
    if d.empty:
        return None, None
    r = d.iloc[-1]
    return _num(r[col]), r["_dt"]


def _pct(df, col):
    if df is None or df.empty or col not in df.columns:
        return None
    s = df.sort_values("_dt")[col].astype(float).replace(0, np.nan).pct_change(fill_method=None) * 100
    s = s.dropna()
    return float(s.iloc[-1]) if len(s) else None


def kpi_board(code, info, tech, L_full, M, MS, pr, prc):
    """頭に大きく出す「今日の数字 ＋ 平均」。株価・制度信用需給・貸借需給は必須。"""
    ui.title(f"{code}　{info.get('name', '')}", pr.get("label", ""))
    st.markdown(f"""<div style="background:#161b22;border-left:4px solid {prc};border-radius:6px;
        padding:8px 14px;margin:2px 0 6px"><span style="color:#8b949e;font-size:0.9rem">{pr.get('detail', '')}</span></div>""",
                unsafe_allow_html=True)

    # ── 株価 ────────────────────────────────
    if tech:
        a = tech.get("avg", {})
        d = tech.get("date")
        ui.group(f"▼ 株価　{d}　※平均は表示期間")
        close, chg = tech.get("close"), tech.get("chg")
        vol, vm = tech.get("vol"), tech.get("vm")
        vol_ratio = (vol / vm) if (vol is not None and vm) else None
        sig_text, sig_bd, sig_bs, sig_acc = "—", None, None, "#484f58"
        if tech.get("tr_up"):
            sig_text, sig_bd, sig_bs, sig_acc = "上抜け", tech.get("trend"), ST_UP, POS
        elif tech.get("tr_dn"):
            sig_text, sig_bd, sig_bs, sig_acc = "下抜け", tech.get("trend"), ST_DN, NEG
        flags = []
        if tech.get("inst"):
            flags.append("大口機関")
        if tech.get("volabn"):
            flags.append("出来高異常")
        items = [
            ui.stat("終値（円）", close, a.get("close"), dec=1, accent="#e3b341"),
            ui.stat("前日比（%）", chg, None, dec=2, suffix="%", sign=True, accent="#58a6ff",
                    val_color=(POS if (chg or 0) >= 0 else NEG),
                    note=(f"平均変動幅 ±{a.get('chg_abs'):.2f}%" if a.get("chg_abs") is not None else "")),
            ui.stat("25日乖離率（%）", tech.get("dev25"), a.get("dev25"), dec=2, suffix="%", sign=True, accent="#bc8cff"),
            ui.stat("出来高（平均比）", vol_ratio, 1.0, dec=2, suffix="倍", accent="#3fb950",
                    note=(f"{vol:,.0f}株" if vol is not None else ""),
                    badge=("出来高異常（2倍超）" if tech.get("volabn") else None), badge_style=ST_VOL),
            ui.stat("トレンド通過", None, text=sig_text, accent=sig_acc, badge=sig_bd, badge_style=sig_bs,
                    note=("🚨 " + "・".join(flags) if flags else ""),
                    val_color=(POS if tech.get("tr_up") else NEG if tech.get("tr_dn") else None)),
            ui.stat("RSI(14)", tech.get("rsi"), a.get("rsi"), dec=1, accent="#79c0ff",
                    note=(f"MACDヒスト {tech.get('macd_hist'):+.2f}" if tech.get("macd_hist") is not None else "")),
        ]
        if tech.get("inst"):
            items[0]["badge"], items[0]["badge_style"] = "大口機関判定", ST_INST
        ui.card_row(items)

    # ── 需給（制度信用 / 貸借 / 信用合計 / 機関空売り）──
    ui.group("▼ 需給（制度信用・貸借・信用合計）　※平均は蓄積全期間")
    sd, sd_dt = _last(MS, "制度信用倍率")
    ld, ld_dt = _last(L_full, "貸借倍率")
    md, md_dt = _last(M, "信用倍率")
    gyaku, _ = _last(L_full, "逆日歩")
    cards = [
        ui.stat("制度信用倍率（JPX・株数）", sd, _finite_mean(MS["制度信用倍率"]) if MS is not None and not MS.empty else None,
                dec=2, suffix="倍", accent="#388bfd", note=(f"{sd_dt:%m/%d}" if sd_dt is not None else "データなし")),
        ui.stat("貸借倍率（日証金）", ld, _finite_mean(L_full["貸借倍率"]) if L_full is not None and not L_full.empty else None,
                dec=2, suffix="倍", accent="#e3b341", note=(f"{ld_dt:%m/%d}" if ld_dt is not None else "データなし")),
        ui.stat("信用倍率（株探・一般+制度）", md, _finite_mean(M["信用倍率"]) if M is not None and not M.empty else None,
                dec=2, suffix="倍", accent="#3fb950", note=(f"{md_dt:%m/%d}" if md_dt is not None else "データなし")),
    ]
    if M is not None and not M.empty:
        b, _ = _last(M, "買い残増減率")
        s_, _ = _last(M, "売り残増減率")
        if b is not None and s_ is not None:
            txt = "🟢 買い優勢" if b > s_ else "🔴 売り優勢" if s_ > b else "⚪ 拮抗"
            cards.append(ui.stat("信用需給（残高の増減）", None, text=txt, accent=(POS if b > s_ else NEG),
                                 note=f"買い残 {b:+.2f}% ／ 売り残 {s_:+.2f}%"))
    SI = info.get("short_inst") or {}
    bi = SI.get("by_institution", pd.DataFrame())
    if bi is not None and not bi.empty and "残高比率" in bi.columns:
        cards.append(ui.stat("機関の空売り残高比率（合計）", float(pd.to_numeric(bi["残高比率"], errors="coerce").sum()),
                             None, dec=2, suffix="%", accent="#bc8cff", note=f"{len(bi)}機関の合計"))
    cards.append(ui.stat("逆日歩（円）", gyaku, None, dec=2, accent="#d29922",
                         badge=("逆日歩あり" if (gyaku or 0) > 0 else None), badge_style=ST_VOL))
    ui.card_row(cards)


def compare_board(L_full, M, MS, period_days):
    """貸借・信用合計・制度信用を『指標 × ソース』の一覧と、日付をそろえた履歴表で並べる。"""
    ui.section("⚖️ 需給の一覧比較", "貸借（日証金）｜信用合計（株探：一般+制度）｜制度信用（JPX）を同じ並びで比較")

    def rowset(df, buy, sell, ratio, chg_buy=None, chg_sell=None):
        if df is None or df.empty:
            return {k: "-" for k in ("基準日", "買い残", "売り残", "倍率", "買い残 前日比", "売り残 前日比", "倍率の平均", "平均との差")}
        b, dt = _last(df, buy)
        s_, _ = _last(df, sell)
        r, _ = _last(df, ratio)
        avg = _finite_mean(df[ratio]) if ratio in df.columns else None
        cb = _num(df.sort_values("_dt")[chg_buy].dropna().iloc[-1]) if chg_buy and chg_buy in df.columns and df[chg_buy].notna().any() else _pct(df, buy)
        cs = _num(df.sort_values("_dt")[chg_sell].dropna().iloc[-1]) if chg_sell and chg_sell in df.columns and df[chg_sell].notna().any() else _pct(df, sell)
        f = lambda v, d=0, sfx="", sg=False: ("-" if v is None else f"{v:{'+' if sg else ''},.{d}f}{sfx}")
        return {"基準日": (f"{dt:%Y/%m/%d}" if dt is not None else "-"),
                "買い残": f(b), "売り残": f(s_), "倍率": f(r, 2, "倍"),
                "買い残 前日比": f(cb, 2, "%", True), "売り残 前日比": f(cs, 2, "%", True),
                "倍率の平均": f(avg, 2, "倍"),
                "平均との差": (f(r - avg, 2, "", True) if (r is not None and avg is not None) else "-")}

    tbl = pd.DataFrame({
        "貸借（日証金）": rowset(L_full, "買い残高", "売り残高", "貸借倍率"),
        "信用合計（株探）": rowset(M, "買い残高", "売り残高", "信用倍率", "買い残増減率", "売り残増減率"),
        "制度信用（JPX・株数）": rowset(MS, "制度買い残高", "制度売り残高", "制度信用倍率"),
    })
    tbl.index.name = "指標"

    def css(df):
        out = pd.DataFrame("", index=df.index, columns=df.columns)
        for r in ("買い残 前日比", "売り残 前日比", "平均との差"):
            for c in df.columns:
                v = str(df.loc[r, c])
                if v.startswith("+"):
                    out.loc[r, c] = f"color:{POS};font-weight:700"
                elif v.startswith("-") and v != "-":
                    out.loc[r, c] = f"color:{NEG};font-weight:700"
        out.loc["倍率"] = "font-weight:800;font-size:15px"
        return out

    st.dataframe(tbl.style.apply(css, axis=None), use_container_width=True, height=38 * (len(tbl) + 1) + 3)
    st.caption("買い残＝貸借は融資残高／信用合計・制度は買い残高、売り残＝貸借は貸株残高／信用合計・制度は売り残高。"
               "貸借・信用合計は株数、制度（JPX）は株数ベースです。前日比は直近2回の記録の変化率で、公表間隔（日次/週次）により意味が異なります。")

    # 日付をそろえた履歴表
    frames = []

    def add(df, mp):
        if df is not None and not df.empty:
            g = df[["_dt"] + list(mp)].copy()
            g["_dt"] = pd.to_datetime(g["_dt"]).dt.normalize()
            frames.append(g.rename(columns=mp).set_index("_dt"))

    add(L_full, {"買い残高": "貸借 融資残", "売り残高": "貸借 貸株残", "貸借倍率": "貸借 倍率"})
    add(M, {"買い残高": "信用 買い残", "売り残高": "信用 売り残", "信用倍率": "信用 倍率"})
    add(MS, {"制度買い残高": "制度 買い残", "制度売り残高": "制度 売り残", "制度信用倍率": "制度 倍率"})
    if not frames:
        st.info("比較できる需給データがありません。")
        return
    h = pd.concat(frames, axis=1).sort_index(ascending=False)
    if period_days > 0:
        h = h[h.index >= pd.Timestamp.today().normalize() - pd.Timedelta(days=period_days)]
    h = h[~h.index.duplicated()]
    disp = h.copy()
    for c in disp.columns:
        if c.endswith("倍率"):
            disp[c] = disp[c].apply(lambda v: "-" if pd.isna(v) else "∞" if abs(v) == np.inf else f"{v:.2f}倍")
        else:
            disp[c] = disp[c].apply(lambda v: "-" if pd.isna(v) else f"{v:,.0f}")
    disp.insert(0, "日付", disp.index.strftime("%Y/%m/%d"))
    st.markdown("**日付をそろえた履歴（同じ日に3つを並べて比較。未公表の日は「-」）**")
    st.dataframe(disp.reset_index(drop=True), use_container_width=True, hide_index=True,
                 height=min(38 * (len(disp) + 1) + 3, 460))
