"""
market.py  ― 市場全体ページ用の取得・蓄積・計算ロジック（Streamlit非依存）

取得元と蓄積キー（scraper.py の既存ストア = Supabase price_cache / ローカルJSON を流用）
  mkt_weekly : 週次 信用評価損益   nikkei225jp.com .../data_DAY/dailyweek2.json
  mkt_short  : 空売り比率合計     nikkei225jp.com .../data_DAY/daily2year.json  (列22+列24)
  mkt_vix    : VIX 日次終値        nikkei225jp.com .../json_DAY/S621.json  (失敗時 us.kabutan.jp)
  mkt_jpx1   : 日次 ①一般+制度     JPX「銘柄別信用取引残高」PDF 最終ページの「総合計」行
  mkt_jpx2   : 日次 ②制度のみ      同上（制度信用の列）
すべて「日付(JST, YYYY-MM-DD)」をキーに、新しい分だけ追記する（二重取りしない）。
JPXの5桁コード変換などJPX専用処理は scraper.py 側に閉じており、ここでは触れない。
"""
import re, io, json, time
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

import scraper as S

JST = timezone(timedelta(hours=9))
NK_BASE = "https://nikkei225jp.com/_data/_nfsDATA/"
URL_WEEKLY = NK_BASE + "data_DAY/dailyweek2.json"
URL_SHORT = NK_BASE + "data_DAY/daily2year.json"
URL_VIX = NK_BASE + "json_DAY/S621.json"
URL_KABUTAN_VIX = "https://us.kabutan.jp/indexes/%5EVIX/historical_prices/daily"
NK_REFERER = "https://nikkei225jp.com/data/sinyou.php"
_WEEKLY_MIN_TS = 1365351600000      # サイト側のコードと同じ（2013-04-08 以降）

KEYS = {k: f"{k}:NK" for k in ("mkt_weekly", "mkt_short", "mkt_vix", "mkt_jpx1", "mkt_jpx2")}


# ════════════════════════════════════════
# 蓄積ストア（日付キーの辞書 ⇔ DataFrame）
# ════════════════════════════════════════
def _store_df(kind: str):
    """蓄積済みデータを (DataFrame[index=date(datetime)], 生dict) で返す。"""
    raw = S._kv_load(KEYS[kind]) or {}
    rows = {}
    for d, data in raw.items():
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except Exception:
            continue
        rows[dt] = {k: (np.nan if v is None else float(v)) for k, v in (data or {}).items()}
    df = pd.DataFrame.from_dict(rows, orient="index").sort_index() if rows else pd.DataFrame()
    return df, raw


def _store_save(kind: str, new_rows: dict, raw: dict) -> int:
    """new_rows={'YYYY-MM-DD': {col: value}}。内容が変わった行だけ書き込む。戻り値=書込件数。"""
    entries = {}
    for d, data in new_rows.items():
        clean = {k: (None if (v is None or (isinstance(v, float) and np.isnan(v))) else v)
                 for k, v in data.items()}
        old = raw.get(d)
        if old is None or any(old.get(k) != clean.get(k) for k in clean):
            entries[d] = {**(old or {}), **clean}
    if entries:
        S._kv_upsert(KEYS[kind], entries)
        print(f"[蓄積:{kind}] {len(entries)}件を保存")
    return len(entries)


def _ts_to_date(ts) -> str:
    return (datetime.fromtimestamp(float(ts) / 1000, tz=timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d")


def _num(v):
    try:
        if v == "" or v is None:
            return None
        f = float(v)
        return None if np.isnan(f) else f
    except Exception:
        return None


# ════════════════════════════════════════
# nikkei225jp.com の JSON(= "var DAILY = [[...]];") を読む
# ════════════════════════════════════════
def _get_js_array(url: str, referer: str = NK_REFERER) -> list:
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers={**S.HEADERS, "Referer": referer}, timeout=30)
            if r.status_code != 200:
                last = f"status={r.status_code}"
                time.sleep(1.5 * (attempt + 1))
                continue
            txt = r.content.decode("utf-8", errors="ignore")
            m = re.search(r"=\s*(\[.*\])\s*;?\s*$", txt.strip(), re.S)
            if not m:
                raise ValueError("配列部分が見つかりません（形式変更の可能性）")
            body = m.group(1)
            while ",," in body:                      # JSの「空要素」(,,) はJSONでは不正なので null に直す
                body = body.replace(",,", ",null,")
            return json.loads(body)
        except Exception as e:
            last = str(e)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url} 取得失敗: {last}")


def fetch_weekly_rows() -> dict:
    """週次の信用評価損益。{'YYYY-MM-DD': {...}}  金額は百万円、枚数は千株のまま保存。"""
    arr = _get_js_array(URL_WEEKLY)
    out = {}
    for r in arr:
        if len(r) < 9 or r[0] <= _WEEKLY_MIN_TS:
            continue
        sm, sk, bm, bk = _num(r[3]), _num(r[4]), _num(r[5]), _num(r[6])
        if not (sm and sk and bm and bk):          # 公表の無い週(空欄・0)は飛ばす
            continue
        out[_ts_to_date(r[0])] = {
            "sell_m": sm, "sell_k": sk, "buy_m": bm, "buy_k": bk,
            "pl": _num(r[7]), "ratio": _num(r[8]), "nk225": _num(r[1]),
        }
    if len(out) < 50:
        raise RuntimeError(f"週次データが{len(out)}件しか読めません（形式変更の可能性）→保存しません")
    return out


def fetch_short_rows() -> dict:
    """空売り比率合計 = 列22(価格規制なし) + 列24(価格規制あり)。サイト自身の計算と同じ。"""
    arr = _get_js_array(URL_SHORT, referer="https://nikkei225jp.com/data/karauri.php")
    out = {}
    for r in arr:
        if len(r) < 25:
            continue
        a, b = _num(r[22]), _num(r[24])
        if a is None or b is None or a <= 0 or b <= 0:
            continue
        out[_ts_to_date(r[0])] = {"short": round(a + b, 2)}
    if len(out) < 100:
        raise RuntimeError(f"空売り比率が{len(out)}件しか読めません（形式変更の可能性）→保存しません")
    return out


# ════════════════════════════════════════
# VIX（S621.json → 失敗時は株探）
# ════════════════════════════════════════
def fetch_vix_nikkei() -> dict:
    arr = _get_js_array(URL_VIX, referer="https://nikkei225jp.com/data/vix.php")
    out = {}
    for r in arr:
        v = _num(r[1]) if len(r) > 1 else None
        if v and v > 0:
            out[_ts_to_date(r[0])] = {"c": v}
    if len(out) < 200:
        raise RuntimeError(f"VIXが{len(out)}件しか読めません")
    return out


def fetch_vix_kabutan(max_pages: int = 3) -> dict:
    """株探(米国) ^VIX の日足。日付は YY/MM/DD。始値・高値・安値・終値を取得。"""
    out = {}
    for p in range(1, max_pages + 1):
        url = URL_KABUTAN_VIX + ("" if p == 1 else f"?page={p}")
        html, st = S._fetch(url, referer="https://us.kabutan.jp/")
        if not html:
            break
        got = 0
        for tr in BeautifulSoup(html, "lxml").find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 5:
                continue
            m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{2})", cells[0])
            if not m:
                continue
            try:
                d = f"{2000 + int(m[1])}-{m[2]}-{m[3]}"
                o, h, l, c = (float(x.replace(",", "")) for x in cells[1:5])
            except Exception:
                continue
            if d not in out:
                out[d] = {"o": o, "h": h, "l": l, "c": c}
                got += 1
        if got == 0:
            break
    return out


def vix_completed_us_date(now=None) -> str:
    """米国の取引が確定している最新の日付。US終値は日本時間の翌朝6時前後なので、
    JST(now-6h)の日付の前日までが確定。例: 11/6 22:12 JST → 11/5、11/7 07:00 JST → 11/6。"""
    now = now or datetime.now(JST)
    return ((now - timedelta(hours=6)).date() - timedelta(days=1)).strftime("%Y-%m-%d")


def verify_vix_with_kabutan(vix_df: pd.DataFrame) -> str:
    """蓄積VIXと株探の終値を突き合わせた結果を文字列で返す。"""
    kab = fetch_vix_kabutan()
    if not kab:
        return "株探に接続できませんでした（照合できません）。"
    n = ng = 0
    bad = []
    for d, v in kab.items():
        dt = datetime.strptime(d, "%Y-%m-%d")
        if dt in vix_df.index and not np.isnan(vix_df.loc[dt, "c"]):
            n += 1
            if abs(vix_df.loc[dt, "c"] - v["c"]) > 0.011:
                ng += 1
                bad.append(f"{d}: 蓄積{vix_df.loc[dt, 'c']:.2f} / 株探{v['c']:.2f}")
    if n == 0:
        return "株探と重なる日付がありませんでした。"
    msg = f"株探との照合: 重なる{n}日中 {n - ng}日が一致（小数第2位まで）。"
    if bad:
        msg += " 不一致 → " + " / ".join(bad[:5])
    return msg


# ════════════════════════════════════════
# JPX PDF 最終ページの「総合計」（①一般+制度 / ②制度のみ）
# ════════════════════════════════════════
_TOKEN = re.compile(r"(▲|△)?\s*([0-9][0-9,]*)")


def _jpx_nums(seg: str) -> list:
    seg = seg.replace("*", " ").replace("＊", " ")
    seg = re.sub(r"[0-9.]+\s*%", " ", seg)
    seg = re.sub(r"(?<!\S)-(?!\S)", " ", seg)             # 「上場比」欄の「-」
    out = []
    for m in _TOKEN.finditer(seg):
        v = int(m.group(2).replace(",", ""))
        out.append(-v if m.group(1) else v)
    return out


def _jpx_pairs(lines: list):
    """(ラベル, 株数12個, 金額12個) を順に返す。株数行の直後(数行以内)にある金額行と組にする。"""
    for i, ln in enumerate(lines):
        if "Shs." not in ln:
            continue
        val = None
        for j in range(i + 1, min(i + 4, len(lines))):
            if "Shs." in lines[j]:
                break
            if "Val." in lines[j]:
                val = lines[j]
                break
        if val is None:
            continue
        s = _jpx_nums(ln.split("Shs.", 1)[1])
        v = _jpx_nums(val.split("Val.", 1)[1])
        if len(s) != 12 or len(v) != 12:
            continue
        label = re.split(r"\s+[0-9,]+\s*銘柄", ln.split("Shs.", 1)[0])[0].strip()
        label = re.sub(r"^[A-Z]\s+", "", label).replace(" 小計", "").strip()
        yield label, s, v


def parse_jpx_totals(pdf_bytes: bytes):
    """
    PDF最終ページ(必要なら直前ページも)から総合計を読み、3通りの合計が一致することを確認して返す。
      ① 貸借銘柄 + 制度信用銘柄 + その他 = 総合計
      ② プライム + スタンダード + グロース + 投信等 = 総合計（市場区分別）
      ③ 一般信用 + 制度信用 = 総合計（売り・買い／株数・金額）
    戻り値: (total12株数, total12金額, 検算メモ) ／ 失敗時は (None, None, 理由)
    """
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        n = len(pdf.pages)
        lines = []
        for p in pdf.pages[max(0, n - 2):]:                # 最終ページ(+念のため1つ前)だけ読む
            lines.extend((p.extract_text() or "").splitlines())
    pairs = list(_jpx_pairs(lines))
    tot_i = next((i for i, (lb, _, _) in enumerate(pairs) if lb == "総合計"), None)
    if tot_i is None:
        return None, None, "最終ページに総合計行が見つかりません（PDFの形式変更の可能性）"
    _, ts, tv = pairs[tot_i]

    def col_sum(rows, idx):
        return [sum(r[0][idx] for r in rows), sum(r[1][idx] for r in rows)]

    notes = []
    # ③ 一般+制度
    for arr, nm in ((ts, "株数"), (tv, "金額")):
        if arr[4] + arr[6] != arr[0] or arr[8] + arr[10] != arr[2]:
            return None, None, f"③一般+制度≠合計（{nm}）"
    notes.append("一般+制度=合計 OK")
    # ① 銘柄種別（総合計行より前にある3区分）
    head = {lb: (s, v) for lb, s, v in pairs[:tot_i] if lb in ("貸借銘柄", "制度信用銘柄", "その他")}
    if len(head) == 3:
        for k in (0, 2):
            for arr_i, tot in ((0, ts), (1, tv)):
                if sum(head[lb][arr_i][k] for lb in head) != tot[k]:
                    return None, None, "①銘柄種別の合計≠総合計"
        notes.append("銘柄種別の合計 OK")
    else:
        notes.append("銘柄種別: 未確認")
    # ② 市場区分（総合計行より後ろの4区分）
    tail = [(lb, s, v) for lb, s, v in pairs[tot_i + 1:tot_i + 5]]
    if [lb for lb, _, _ in tail] == ["プライム", "スタンダード", "グロース", "投信等"]:
        for k in (0, 2):
            for arr_i, tot in ((1, ts), (2, tv)):
                if sum(t[arr_i][k] for t in tail) != tot[k]:
                    return None, None, "②市場区分の合計≠総合計"
        notes.append("市場区分の合計 OK")
    else:
        notes.append("市場区分: 未確認")
    return ts, tv, " / ".join(notes)


def _rows_from_totals(ts: list, tv: list):
    one = {"sell_s": ts[0], "sell_v": tv[0], "buy_s": ts[2], "buy_v": tv[2],
           "sell_s_chg": ts[1], "sell_v_chg": tv[1], "buy_s_chg": ts[3], "buy_v_chg": tv[3]}
    two = {"sell_s": ts[6], "sell_v": tv[6], "buy_s": ts[10], "buy_v": tv[10],
           "sell_s_chg": ts[7], "sell_v_chg": tv[7], "buy_s_chg": ts[11], "buy_v_chg": tv[11]}
    return one, two


def update_jpx_daily(raw1: dict, raw2: dict, max_new: int = 15, log=print):
    """
    一覧ページに載っている日付(+ローカル保存PDF)のうち、未蓄積の日だけ総合計を取り込む。
    最終ページだけ読むので1日あたり約1〜2秒（銘柄別の全ページ解析(約70秒)は行わない）。
    """
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        return {}, {}, ["pdfplumber 未インストール → pip install pdfplumber"]
    avail = {}                                             # {YYYYMMDD: url or None}
    for d in S._jpx_archived_dates():
        avail[d] = None
    try:
        for d, u in S._jpx_list_pdfs().items():
            avail[d] = u
    except Exception as e:
        log(f"[JPX日次] 一覧取得失敗 {e}")
    new1, new2, msgs = {}, {}, []
    todo = [d for d in sorted(avail, reverse=True)
            if f"{d[:4]}-{d[4:6]}-{d[6:]}" not in raw1][:max_new]
    for d in todo:
        key = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        try:
            content, _ = S._jpx_pdf_bytes(d, avail[d])
            if content is None:
                msgs.append(f"{key}: PDF取得不可")
                continue
            ts, tv, note = parse_jpx_totals(content)
            if ts is None:
                msgs.append(f"{key}: {note}")               # 検算NGの日は保存しない
                continue
            one, two = _rows_from_totals(ts, tv)
            new1[key], new2[key] = one, two
            log(f"[JPX日次] {key}: 総合計を取得（{note}）")
            time.sleep(0.5)
        except Exception as e:
            msgs.append(f"{key}: 例外 {e}")
    return new1, new2, msgs


# ════════════════════════════════════════
# 更新（取得→蓄積）と読み出し
# ════════════════════════════════════════
def update_all(log=print) -> list:
    """全データ源を更新して蓄積する。失敗したものは握りつぶさずメッセージで返し、他は続行する。"""
    msgs = []

    def step(name, fn):
        try:
            fn()
        except Exception as e:
            msgs.append(f"{name}: {e}")
            log(f"[市場更新] {name} 失敗: {e}")

    def do_weekly():
        _, raw = _store_df("mkt_weekly")
        _store_save("mkt_weekly", fetch_weekly_rows(), raw)

    def do_short():
        _, raw = _store_df("mkt_short")
        _store_save("mkt_short", fetch_short_rows(), raw)

    def do_vix():
        _, raw = _store_df("mkt_vix")
        try:
            rows = fetch_vix_nikkei()
            src = "nikkei225jp"
        except Exception as e:
            log(f"[VIX] nikkei225jp 失敗→株探に切替: {e}")
            rows = fetch_vix_kabutan(max_pages=3)
            src = "株探"
            if not rows:
                raise RuntimeError("nikkei225jp・株探のどちらからも取得できませんでした")
            msgs.append("VIX: nikkei225jpが取得できなかったため株探(直近約1年)を使用しました")
        # 確定していない米国当日(取引中)の値は蓄積しない
        done = vix_completed_us_date()
        rows = {d: v for d, v in rows.items() if d <= done}
        _store_save("mkt_vix", rows, raw)
        log(f"[VIX] {src}: {len(rows)}日分")

    def do_jpx():
        _, r1 = _store_df("mkt_jpx1")
        _, r2 = _store_df("mkt_jpx2")
        n1, n2, m = update_jpx_daily(r1, r2, log=log)
        msgs.extend(m)
        _store_save("mkt_jpx1", n1, r1)
        _store_save("mkt_jpx2", n2, r2)

    step("週次信用評価損益", do_weekly)
    step("空売り比率", do_short)
    step("VIX", do_vix)
    step("JPX日次", do_jpx)
    return msgs


def load_all() -> dict:
    """蓄積済みの全データ。"""
    return {
        "weekly": _store_df("mkt_weekly")[0],
        "short": _store_df("mkt_short")[0],
        "vix": _store_df("mkt_vix")[0],
        "jpx1": _store_df("mkt_jpx1")[0],
        "jpx2": _store_df("mkt_jpx2")[0],
    }


def fetch_vix_provisional() -> tuple:
    """確定前の最新VIX（米国取引中の値）を (日付, 値) で返す。取れなければ (None, None)。表示専用。"""
    try:
        arr = _get_js_array(URL_VIX, referer="https://nikkei225jp.com/data/vix.php")
        last = arr[-1]
        d, v = _ts_to_date(last[0]), _num(last[1])
        if v and d > vix_completed_us_date():
            return d, v
    except Exception:
        pass
    return None, None


# ════════════════════════════════════════
# 計算：VIXのMACD、表の組み立て
# ════════════════════════════════════════
def vix_macd(vix: pd.DataFrame) -> pd.DataFrame:
    """VIX終値(確定分)から MACD(12,26,9) を計算。cross: 'GC'(ゴールデン) / 'DC'(デッド) / ''。"""
    if vix is None or vix.empty or "c" not in vix.columns:
        return pd.DataFrame(columns=["c", "macd", "signal", "hist", "cross"])
    df = vix[["c"]].dropna().sort_index().copy()
    c = df["c"].astype(float)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["hist"] = df["macd"] - df["signal"]
    pos = df["hist"] > 0
    prev = pos.shift(1)
    cross = pd.Series("", index=df.index)
    cross[(prev == True) & (~pos)] = "DC"     # noqa: E712
    cross[(prev == False) & pos] = "GC"       # noqa: E712
    cross.iloc[:35] = ""                      # 助走期間(EMA26+シグナル9)は判定しない
    df["cross"] = cross
    return df


def macd_outlook(macd, prov_close=None, horizon: int = 30):
    """
    VIXのMACDが「あとどれくらいでクロスするか」を、指数平滑の式から正確に逆算する（予測ではなく算術）。
      ・次の終値がいくつ以下(以上)ならクロスするか: hist'=0 となる終値 x*（式は線形なので厳密に解ける）
      ・VIXが今日の終値のまま横ばいなら、何営業日後にクロスするか（horizon日まで）
      ・暫定値(米国取引中)が確定値になった場合の判定（任意）
    """
    if macd is None or len(macd) < 40:
        return None
    c = macd["c"].astype(float)
    e12 = float(c.ewm(span=12, adjust=False).mean().iloc[-1])
    e26 = float(c.ewm(span=26, adjust=False).mean().iloc[-1])
    m, s, h = (float(macd[k].iloc[-1]) for k in ("macd", "signal", "hist"))
    a12, a26, a9 = 2 / 13, 2 / 27, 2 / 10

    def step(x, E12, E26, S):
        n12 = E12 + a12 * (x - E12)
        n26 = E26 + a26 * (x - E26)
        mn = n12 - n26
        sn = S + a9 * (mn - S)
        return n12, n26, sn, mn - sn

    x_cross = (s - m + a12 * e12 - a26 * e26) / (a12 - a26)
    c0 = float(c.iloc[-1])
    pos = h > 0
    days, st_ = None, (e12, e26, s)
    for k in range(1, horizon + 1):
        n12, n26, sn, hn = step(c0, *st_)
        st_ = (n12, n26, sn)
        if (hn > 0) != pos:
            days = k
            break
    out = {"date": macd.index[-1], "c": c0, "macd": m, "signal": s, "hist": h,
           "hist_prev": float(macd["hist"].iloc[-2]), "x_cross": x_cross, "pct": (x_cross / c0 - 1) * 100,
           "days_flat": days, "target": "DC" if pos else "GC", "horizon": horizon}
    if prov_close:
        _, _, _, hp = step(float(prov_close), e12, e26, s)
        out.update(prov=float(prov_close), prov_hist=hp, prov_cross=((hp > 0) != pos))
    return out


def shift_to_jp(dates, enable: bool):
    """VIXの日付(米国)を日本の翌営業日(土日は飛ばす)へずらす。"""
    if not enable:
        return pd.DatetimeIndex(dates)
    out = []
    for d in pd.DatetimeIndex(dates):
        n = d + timedelta(days=1)
        while n.weekday() >= 5:
            n += timedelta(days=1)
        out.append(n)
    return pd.DatetimeIndex(out)


def build_tables(data: dict, shift_vix: bool = False) -> dict:
    """
    ①(一般+制度: 週次と日次を1つに、同じ日付は週次が優先) と ②(制度のみ: 日次) を作る。
    戻り値: {"t1": DataFrame, "t2": DataFrame}  index=日付(降順)
    列: kind(週次/日次), sell_s(千株), sell_k(億円), sell_chg(%), short(%), buy_s, buy_k, buy_chg, ratio, pl, cross
    """
    w, s, j1, j2 = data["weekly"], data["short"], data["jpx1"], data["jpx2"]
    macd = vix_macd(data["vix"])

    # 週次: 千株/百万円→千株/億円、変化%は週次系列どうしの前週比（サイトと同じ計算）
    wk = pd.DataFrame()
    if not w.empty:
        wk = pd.DataFrame(index=w.index)
        wk["kind"] = "週次"
        wk["sell_s"] = w["sell_m"]
        wk["sell_k"] = w["sell_k"] / 100.0
        wk["buy_s"] = w["buy_m"]
        wk["buy_k"] = w["buy_k"] / 100.0
        wk["sell_chg"] = w["sell_k"].pct_change() * 100
        wk["buy_chg"] = w["buy_k"].pct_change() * 100
        wk["ratio"] = w["ratio"]
        wk["pl"] = w["pl"]

    def daily(j, with_pl=False):
        if j is None or j.empty:
            return pd.DataFrame()
        d = pd.DataFrame(index=j.index)
        d["kind"] = "日次"
        d["sell_s"] = j["sell_s"] / 1000.0
        d["sell_k"] = j["sell_v"] / 1e8
        d["buy_s"] = j["buy_s"] / 1000.0
        d["buy_k"] = j["buy_v"] / 1e8
        # 変化% = 前日比(金額) ÷ 前日の金額
        d["sell_chg"] = j["sell_v_chg"] / (j["sell_v"] - j["sell_v_chg"]) * 100
        d["buy_chg"] = j["buy_v_chg"] / (j["buy_v"] - j["buy_v_chg"]) * 100
        d["ratio"] = j["buy_v"] / j["sell_v"]        # 日経の表と同じ「金額ベース」の信用倍率
        d["pl"] = np.nan                              # 日次の評価損益率は出せないため空欄
        return d

    d1, d2 = daily(j1), daily(j2)

    parts = [p for p in (d1, wk) if not p.empty]      # 後ろに置いた週次が同日の日次を上書き
    t1 = pd.concat(parts) if parts else pd.DataFrame()
    if not t1.empty:
        t1 = t1[~t1.index.duplicated(keep="last")].sort_index()
    t2 = d2.sort_index() if not d2.empty else pd.DataFrame()

    # 空売り比率・VIXクロスを日付で結合
    cross_map = {}
    if not macd.empty:
        sel = macd[macd["cross"] != ""]
        for d, c in zip(shift_to_jp(sel.index, shift_vix), sel["cross"]):
            cross_map[d] = c
    for t in (t1, t2):
        if t.empty:
            continue
        t["short"] = s["short"].reindex(t.index) if not s.empty else np.nan
        t["cross"] = [cross_map.get(d, "") for d in t.index]

    return {"t1": t1.sort_index(ascending=False) if not t1.empty else t1,
            "t2": t2.sort_index(ascending=False) if not t2.empty else t2,
            "macd": macd}


def row_levels(t1: pd.DataFrame, sell_thr: float = 8000.0, short_thr: float = 40.0) -> pd.Series:
    """
    行の強調レベル（index=日付）: 0=なし / 1=薄青 / 2=濃青 / 3=最濃青
      1: ①(一般+制度)の売り残金額 > sell_thr 億円
      2: 1 かつ 空売り比率合計 >= short_thr %
      3: VIXのMACDがデッドクロスした日（最優先）
    """
    if t1 is None or t1.empty:
        return pd.Series(dtype=int)
    lv = pd.Series(0, index=t1.index)
    over = t1["sell_k"] > sell_thr
    lv[over] = 1
    lv[over & (t1["short"] >= short_thr)] = 2
    lv[t1["cross"] == "DC"] = 3
    return lv
