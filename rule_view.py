"""
rule_view.py ― 「🔎 法則探索」タブの表示（計算は rule_miner.py。ここは見せ方だけ）
"""
import numpy as np
import pandas as pd
import streamlit as st

import rule_miner as RM
import ui

POS, NEG = "#58a6ff", "#f85149"


def _pct(v, d=0):
    return "-" if v is None or v != v else f"{v * 100:.{d}f}%"


def _num(v, d=2, sign=False):
    return "-" if v is None or v != v else f"{v:{'+' if sign else ''},.{d}f}"


def _market_frames():
    """市場ページで蓄積したデータ（無ければ None）。"""
    try:
        import market
        data = market.load_all()
        tb = market.build_tables(data, shift_vix=False)
        t1 = tb.get("t1")
        macd = tb.get("macd")
        if (t1 is None or t1.empty) and (macd is None or macd.empty):
            return None
        return {"t1": t1, "macd": macd}
    except Exception as e:      # 市場データが無くても個別の探索は動かす
        print(f"[法則探索] 市場データなし: {e}")
        return None


def _rule_df(R, on_flags, kinds):
    d = R.copy()
    d["_on"] = on_flags if len(on_flags) == len(d) else [False] * len(d)
    d = d[d["信頼度"].isin(kinds)]
    if d.empty:
        return d, None
    t = pd.DataFrame({
        "今日": ["🔔点灯中" if o else "" for o in d["_on"]],
        "信頼度": d["信頼度"].values,
        "条件": d["条件"].values,
        "先の日数": [f"{x}日後" for x in d["日数"]],
        "勝率(全体)": [_pct(x) for x in d["勝率(全体)"]],
        "件数": d["件数"].values,
        "全体の基準": [_pct(x) for x in d["全体の基準率"]],
        "差": [f"{x:+.0f}pt" for x in d["差(pt)"]],
        "前半": [f"{_pct(a)} (n={b})" for a, b in zip(d["前半勝率"], d["前半件数"])],
        "後半": [f"{_pct(a)} (n={b})" for a, b in zip(d["後半勝率"], d["後半件数"])],
        "平均騰落": [f"{x:+.2f}%" for x in d["平均騰落(%)"]],
        "偶然確率": [_pct(x) for x in d["偶然確率"]],
    })

    def sty(row):
        g = row["信頼度"]
        bg = "#0e3d1f" if g.startswith("◎") else ("#2d2a00" if g.startswith("○") else "")
        return [f"background-color:{bg}" if bg else ""] * len(row)
    return d, t.style.apply(sty, axis=1)


def _trait_table(D):
    if D is None or D.empty:
        return None
    t = pd.DataFrame({
        "共通する条件": D["共通する条件"],
        "起きた日のうち該当": [_pct(x) for x in D["起きた日のうち該当"]],
        "全営業日のうち該当": [_pct(x) for x in D["全営業日のうち該当"]],
        "該当日に起きる確率": [_pct(x) for x in D["該当日に起きる確率"]],
        "全体で起きる確率": [_pct(x) for x in D["全体で起きる確率"]],
        "倍率": [f"{x:.2f}倍" if x == x else "-" for x in D["倍率"]],
        "前半・後半とも": D["前半・後半とも"],
    })
    return t


def _streak_view(res):
    ui.section("③ 連騰・連落の確率", f"過去 約{res['full_years']:.1f}年（蓄積している全期間）の終値から計算")
    sign, k = res["cur_sign"], res["cur_k"]
    now = "連騰" if sign > 0 else ("連落" if sign < 0 else "")
    if k and now:
        tbl = res["streak_up"] if sign > 0 else res["streak_dn"]
        row = tbl[tbl["連続日数"] == k]
        if len(row):
            r = row.iloc[0]
            msg = (f"現在 **{k}{now}中**。過去に{k}{now}に到達した **{int(r['到達回数'])}回** のうち、"
                   f"次の日も続いたのは **{_pct(r['次の日も続く確率'])}**、"
                   f"5日後に反対方向（{'下落' if sign > 0 else '上昇'}）だったのは **{_pct(r['5日後に反対方向'])}**"
                   f"（5日後の平均 {_num(r['5日後平均(%)'], 2, True)}%）。")
            if int(r["到達回数"]) < 10:
                msg += " ⚠️ 回数が少ないため参考程度です。"
            st.info(msg)
        else:
            st.info(f"現在 **{k}{now}中**（過去にこの長さまで続いた例が見つからないため確率は出せません）。")
    else:
        st.caption("現在は連騰・連落ではありません（前日比が0または反転）。")
    c1, c2 = st.columns(2)
    for col, key, lab in ((c1, "streak_up", "連騰"), (c2, "streak_dn", "連落")):
        t = res[key]
        with col:
            st.markdown(f"**{lab}**（k連目に到達した日が基準）")
            if t.empty:
                st.caption("データなし")
                continue
            v = pd.DataFrame({
                "連続日数": [f"{int(x)}{lab}" for x in t["連続日数"]],
                "到達回数": t["到達回数"].astype(int),
                "全営業日のうち": [_pct(x, 1) for x in t["全営業日のうち"]],
                "次の日も続く確率": [_pct(x) for x in t["次の日も続く確率"]],
                "翌日平均": [f"{x:+.2f}%" for x in t["翌日平均(%)"]],
                "5日後に反対方向": [_pct(x) for x in t["5日後に反対方向"]],
                "5日後平均": [f"{x:+.2f}%" for x in t["5日後平均(%)"]],
            })
            st.dataframe(v, hide_index=True, use_container_width=True)


def _tb_view(res):
    ui.section("⑤ 前日比の上位・下位5日", "前後の値動きと、その日の信用倍率など")
    for blk in (res["top"], res["bottom"]):
        st.markdown(f"**{blk['name']}**")
        t = blk["table"].copy()
        for c in t.columns:
            if c in ("前日比(%)", "直前5日(%)", "翌日(%)", "5日後(%)"):
                t[c] = [f"{x:+.2f}%" if x == x else "-" for x in t[c]]
            elif c in ("信用倍率", "制度信用倍率", "貸借倍率", "RSI", "25日乖離率(%)"):
                t[c] = [f"{x:,.2f}" if x == x else "-" for x in t[c]]
        st.dataframe(t, hide_index=True, use_container_width=True)
        if blk["summary"]:
            st.caption("📌 " + blk["summary"])
        for n in blk["notes"]:
            st.caption("🔎 共通点: " + n)


def _dev_view(res):
    d = res.get("dev")
    ui.section("⑥ 25日乖離率の上限・下限", "期間内の分布から基準を自動で決定（下位10%・上位10%）")
    if not d:
        st.caption("データ不足のため表示できません。")
        return
    st.caption(f"期間内の25日乖離率の平均 **{d['mean']:+.2f}%**")
    t = d["table"].copy()
    show = pd.DataFrame({
        "区分": t["区分"], "基準": t["基準"], "期間内の極値": t["極値"],
        "該当日数": t["該当日数"], "全営業日のうち": [_pct(x, 1) for x in t["全営業日のうち"]],
        "到達条件": t["到達条件"], "到達確率": [_pct(x) for x in t["到達確率"]],
        "（全日の到達確率）": [_pct(x) for x in t["全日の到達確率"]],
        "5日後に逆方向": [_pct(x) for x in t["5日後に逆方向へ"]],
    })
    for c in t.columns:
        if c.startswith("平均") and "(該当日)" in c:
            base = c.replace("(該当日)", "(全日)")
            show[c.replace("平均", "平均 ").replace("(該当日)", "(該当日)")] = [f"{x:,.2f}" if x == x else "-" for x in t[c]]
            show[base.replace("平均", "平均 ")] = [f"{x:,.2f}" if x == x else "-" for x in t[base]]
    st.dataframe(show, hide_index=True, use_container_width=True)


def _lit(res, side):
    """今日点灯している ◎/○ ルール（強い順）。無ければ空。"""
    R, fl = res.get("rules"), res.get("rules_on") or []
    if R is None or R.empty or len(fl) != len(R):
        return [], 0
    d = R.copy()
    d["_on"] = fl
    weak = int(((d["方向"] == side) & d["_on"] & (d["信頼度"] == "△ 偶然の可能性")).sum())
    d = d[(d["方向"] == side) & d["_on"] & (d["信頼度"] != "△ 偶然の可能性")]
    return [r for _, r in d.iterrows()], weak


def _map_html(rows):
    out = ["<div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 12px'>"]
    out.append("<div style='display:flex;font-size:11px;color:#8b949e;padding:2px 0 6px'>"
               "<div style='width:26%'>指標</div><div style='width:14%;text-align:right;padding-right:12px'>今日の値</div>"
               "<div style='width:40%'>期間内の位置（左=低い／右=高い、帯=中間60%）</div><div style='width:20%'>判定</div></div>")
    for r in rows:
        pct = max(0, min(100, r["pct"]))
        hot = pct >= 80 or pct <= 20
        way = r["way"]
        sig, col = "", "#8b949e"
        if hot and way:
            hi = pct >= 80
            dn_when_high = "高い" in way and "下落" in way
            up_when_high = "高い" in way and "上昇" in way
            dn_when_low = "低い" in way and "下落" in way
            up_when_low = "低い" in way and "上昇" in way
            if (hi and dn_when_high) or ((not hi) and dn_when_low):
                sig, col = "下落に偏りやすい水準", "#f85149"
            elif (hi and up_when_high) or ((not hi) and up_when_low):
                sig, col = "上昇に偏りやすい水準", "#58a6ff"
        if not sig:
            sig = ("高水準" if pct >= 80 else "低水準") if hot else "中間"
            col = "#e3b341" if hot else "#8b949e"
        mk = "#f0f6fc" if not hot else col
        star = "" if r["selected"] else " <span style='color:#6e7681;font-size:10px'>(参考)</span>"
        v = f"{r['value']:,.2f}"
        out.append(
            "<div style='display:flex;align-items:center;padding:4px 0;border-top:1px solid #21262d'>"
            f"<div style='width:26%;font-size:13px;font-weight:700;color:#e6edf3'>{r['name']}{star}</div>"
            f"<div style='width:14%;text-align:right;padding-right:12px;font-size:15px;font-weight:800;color:#f0f6fc'>{v}</div>"
            "<div style='width:40%'><div style='position:relative;height:12px;background:#0d1117;border-radius:6px'>"
            "<div style='position:absolute;left:20%;width:60%;height:12px;background:#1c2331'></div>"
            f"<div style='position:absolute;left:calc({pct:.1f}% - 5px);top:0;width:10px;height:12px;border-radius:6px;background:{mk}'></div>"
            f"</div><div style='font-size:10px;color:#6e7681;margin-top:1px'>位置 {pct:.0f}%</div></div>"
            f"<div style='width:20%;font-size:12px;font-weight:700;color:{col}'>{sig}</div></div>")
    out.append("</div>")
    return "".join(out)


def _summary(res):
    ui.section("📍 今日の要約", f"{res['end']:%Y/%m/%d}時点／探索期間 {res['n']}営業日（{res['start']:%Y/%m/%d}～）")
    items = []
    for side, lab, acc, colr in (("買い(上昇)", "買いサイン", "#388bfd", POS), ("売り(下落)", "売りサイン", "#f85149", NEG)):
        lit, weak = _lit(res, side)
        if lit:
            r = lit[0]
            g = "◎" if r["信頼度"].startswith("◎") else "○"
            txt = f"{len(lit)}件 点灯"
            note = (f"{r['条件']}<br><b>{r['日数']}日後に{'上昇' if side.startswith('買') else '下落'} "
                    f"{r['勝率(全体)'] * 100:.0f}%</b>（n={int(r['件数'])}／基準 {r['全体の基準率'] * 100:.0f}%）")
            items.append(ui.stat(lab, None, text=txt, accent=acc, val_color=colr, note=note,
                                 badge=f"{g} 信頼度{'高' if g == '◎' else '中'}",
                                 badge_style=f"background:#0e3d1f;color:{colr};border:1px solid {colr}"))
        else:
            note = "◎・○の条件は成り立っていません" + (f"<br>（参考外の△が{weak}件点灯）" if weak else "")
            items.append(ui.stat(lab, None, text="なし", accent="#484f58", val_color="#8b949e", note=note))
    ud = res.get("updown", {})
    er = res.get("event_rates", {})
    if 1 in ud and 5 in ud:
        a, b = ud[1], ud[5]
        items.append(ui.stat("短期の上下（過去統計）", None,
                             text=f"上昇 {a['up'] * 100:.0f}% / {b['up'] * 100:.0f}%", accent="#a371f7",
                             note=f"翌日 / 5日後（下落 {a['dn'] * 100:.0f}% / {b['dn'] * 100:.0f}%）<br>"
                                  f"平均 {a['mean']:+.2f}% / {b['mean']:+.2f}%"))
    if 20 in ud:
        c = ud[20]
        ex = ""
        defs = res.get("event_defs", {})
        if "月間上昇" in er and "月間下落" in er:
            ex = (f"<br>{defs.get('月間上昇', '')}: {er['月間上昇'] * 100:.0f}%／"
                  f"{defs.get('月間下落', '')}: {er['月間下落'] * 100:.0f}%")
        items.append(ui.stat("長期の上下（過去統計）", None, text=f"上昇 {c['up'] * 100:.0f}%", accent="#a371f7",
                             note=f"20営業日後（下落 {c['dn'] * 100:.0f}%）／平均 {c['mean']:+.2f}%{ex}"))
    ui.card_row(items)
    sg, k = res.get("cur_sign", 0), res.get("cur_k", 0)
    if k and sg:
        lab = "連騰" if sg > 0 else "連落"
        t = res["streak_up"] if sg > 0 else res["streak_dn"]
        row = t[t["連続日数"] == k]
        if len(row):
            r = row.iloc[0]
            st.caption(f"📈 現在 **{k}{lab}中**: 過去に到達した{int(r['到達回数'])}回のうち、次の日も続いた確率 "
                       f"{_pct(r['次の日も続く確率'])}／5日後に反対方向だった確率 {_pct(r['5日後に反対方向'])}"
                       + ("（回数が少なく参考程度）" if int(r["到達回数"]) < 10 else ""))
    mp = res.get("map") or []
    if mp:
        st.markdown("**🧭 重要指標の現在地マップ**")
        st.markdown(_map_html(mp), unsafe_allow_html=True)
        st.caption("位置＝探索期間内で今日の値が下から何%の位置か。20%以下・80%以上は端（帯の外）です。"
                   "「偏りやすい水準」は、その指標が自動選定で見つかった傾向（①の表）に沿って端にあるときだけ表示します。")



def render(code, info, period_days, period_label):
    p_long = info.get("price_long", pd.DataFrame())
    if p_long is None or p_long.empty:
        p_long = info.get("price", pd.DataFrame())
    ui.section("🔎 法則探索", "実データから勝率の高い条件を自動で探します（APIキー・課金は不要／同じデータなら同じ結果）")
    if p_long is None or p_long.empty or "終値" not in p_long.columns:
        st.warning("株価データがありません。")
        return

    with st.expander("📖 このタブの読み方（クリックで開く）", expanded=False):
        st.markdown(
            "- **勝率** … その条件が成り立った日に、N日後の終値が上がった（下がった）割合。\n"
            "- **全体の基準** … 条件を付けない場合の同じ割合。上昇相場だと買いの基準が高くなるので、**差(pt)** で見ます。\n"
            "- **前半 / 後半** … 探索期間の前2/3で条件を探し、後ろ1/3で当たるか確かめた結果です。\n"
            "- **偶然確率** … 意味のないデータでも同じ探索をして、これ以上の成績が偶然出る確率。小さいほど信頼できます。\n"
            "- **◎検証済み** … 件数20以上・後半でも60%以上・偶然確率10%以下。 **○参考** … 後半でも55%以上・偶然確率30%以下。"
            " **△** … 偶然の可能性が高い（初期状態では折りたたみ）。\n"
            "- **◎が出ない＝この期間に偶然を超える法則は見つからなかった**という結果です（無理に探しません）。\n"
            "- 信用残・市場指標は、実際に公表されて見られる日まで遅らせて使います（未来のデータを使わない）。"
            f" 個別の信用残{RM.LAG_MARGIN}営業日／制度信用・貸借・日次の市場指標{RM.LAG_SEIDO}営業日／週次の評価損益{RM.LAG_MKT_WEEKLY}営業日。\n"
            "- 過去の傾向の整理であり、将来の値動きや売買を保証するものではありません。")

    c1, c2 = st.columns([2, 3])
    with c1:
        basis = st.radio("探索に使う期間", [f"表示期間（{period_label}）", "蓄積全期間"],
                         horizontal=True, key=f"rm_basis_{code}")
    use_full = basis == "蓄積全期間"
    if not use_full and period_days == 0:
        use_full = True
    with c2:
        st.caption("期間が長いほど◎が出やすくなります（目安: 250営業日以上）。表示期間が短いときは『蓄積全期間』も試してください。")

    key = f"rm_res_{code}_{'full' if use_full else period_days}_{len(p_long)}_{p_long['_dt'].max():%Y%m%d}"
    run = st.button("🔎 法則を探す", key=f"rm_run_{code}", type="primary")
    if run:
        with st.spinner("特徴量を計算 → 指標を7つに絞る → 条件を総当たり → 偶然の水準と比較中…"):
            try:
                mkt = _market_frames()
                F = RM.build_frame(p_long, M=info.get("margin"), L=info.get("lending"),
                                   MS=info.get("margin_seido"), mkt=mkt)
                start = None
                if not use_full:
                    start = pd.Timestamp(p_long["_dt"].max()).normalize() - pd.Timedelta(days=period_days)
                res = RM.analyze(F, period_start=start)
                res["has_market"] = mkt is not None
                st.session_state[key] = res
            except Exception as e:
                st.error(f"探索中にエラーが発生しました: {e}")
                return
    res = st.session_state.get(key)
    if res is None:
        st.info("「🔎 法則を探す」を押すと、この期間のデータから自動で法則を探します（数秒）。")
        return

    # ── 概要 ──
    for w in res["warn"]:
        st.warning(w)
    if res["n"] < 40:
        return
    _summary(res)
    ui.card_row([
        ui.stat("探索期間", res["n"], dec=0, suffix="営業日", note=f"{res['start']:%Y/%m/%d} ～ {res['end']:%Y/%m/%d}", accent="#388bfd"),
        ui.stat("期間の騰落", res["period_ret"], dec=1, suffix="%", sign=True,
                val_color=POS if res["period_ret"] >= 0 else NEG,
                note="上昇相場だと『買い』が有利に出ます"),
        ui.stat("上昇した日の割合", res["base"][5] * 100, dec=0, suffix="%",
                note=f"5日後に上昇していた割合（1日後 {res['base'][1] * 100:.0f}% ／ 3日後 {res['base'][3] * 100:.0f}%）"),
        ui.stat("前半で探索→後半で検証", None, text=f"{res['split_date']:%m/%d}", note="この日より前で探し、この日以降で確認"),
    ])
    if not res.get("has_market"):
        st.caption("ℹ️ 市場全体の指標（VIX・信用評価損益・空売り比率）は、市場ページの蓄積データが無いため今回は使っていません。")

    # ── 使う7指標 ──
    ui.section("① 自動で選ばれた7指標", f"候補{len(res['cand'])}個からデータで選定（似た動きの指標は重複を避けます）")
    fdf = res["features_df"]
    if not fdf.empty:
        t = fdf.copy()
        t["意味"] = [RM.FEATURE_NOTE.get(f, "") for f in t["指標"]]
        t["今日の値"] = [_num(res["last"].get(f, np.nan), 2) for f in t["指標"]]
        st.dataframe(t[["指標", "意味", "傾向", "偏りの強さ(z)", "今日の値"]], hide_index=True, use_container_width=True)
    ex = [f for f in res["excluded"] if f.startswith(("市場", "VIX"))]
    if ex:
        st.caption("使えなかった市場指標（蓄積期間が足りない等）: " + "、".join(ex))

    # ── ルール ──
    ui.section("② 勝ち筋の高い条件（買い／売り）", "前半で見つけ、後半で検証した結果。🔔は今日その条件が成り立っているもの")
    R = res["rules"]
    flags = res["rules_on"]
    if R is None or R.empty:
        st.info("この期間では条件を検討するだけのデータがありません。")
    else:
        for side, lab in (("買い(上昇)", "🟦 買いの法則（上がりやすい条件）"), ("売り(下落)", "🟥 売りの法則（下がりやすい条件）")):
            sub = R[R["方向"] == side].reset_index()
            sflags = [flags[i] for i in sub["index"]] if flags else []
            sub = sub.drop(columns=["index"])
            good, sty = _rule_df(sub, sflags, ["◎ 検証済み", "○ 参考"])
            st.markdown(f"**{lab}**")
            if good is None or good.empty:
                st.caption("◎・○に当てはまる条件は見つかりませんでした（この期間では偶然を超える法則なし）。")
            else:
                on = [s for s, o in zip(good["条件"], good["_on"]) if o]
                if on:
                    st.success("🔔 今日点灯中: " + " ／ ".join(on[:3]))
                st.dataframe(sty, hide_index=True, use_container_width=True)
            weak, wsty = _rule_df(sub, sflags, ["△ 偶然の可能性"])
            if weak is not None and not weak.empty:
                with st.expander(f"△ 偶然の可能性が高い条件（{len(weak)}件・参考にしないでください）", expanded=False):
                    st.dataframe(wsty, hide_index=True, use_container_width=True)

    # ── 連騰連落 ──
    _streak_view(res)

    # ── 週間・月間の共通点 ──
    ui.section("④ 週間・月間で大きく動く前の共通点", "基準は期間内の上位20%／下位20%の動きから自動決定。起きる“前日まで”の指標で比べます")
    tabs = st.tabs(list(res["event_defs"].keys()))
    for tb, k in zip(tabs, res["event_defs"].keys()):
        with tb:
            st.caption(f"対象: **{res['event_defs'][k]}**（該当 {res['event_counts'][k]}日）")
            t = _trait_table(res["traits"].get(k))
            if t is None:
                st.caption("該当日が少ないため共通点は出せません。")
            else:
                st.dataframe(t, hide_index=True, use_container_width=True)
                st.caption("読み方: 『倍率』が1より大きいほど、その条件が成り立つ日に起きやすいことを示します。"
                           "『前半・後半とも ✔』は、探索期間の前半・後半の両方で同じ傾向だったものです。")

    _tb_view(res)
    _dev_view(res)
