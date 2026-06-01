"""
scraper.py
・貸借データ + 逆日歩 : irbank.net/{code}/nisshokin
・週次信用残           : irbank.net/{code}/margin
・株価/出来高          : yfinance（過去1ヶ月）
"""
import re, time, requests
from bs4 import BeautifulSoup
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

# IRバンクに対して複数のUser-Agentを試みる
UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

STOCKS = {
    "9432": {"name": "NTT",         "yf": "9432.T"},
    "9434": {"name": "ソフトバンク", "yf": "9434.T"},
    "6758": {"name": "ソニーG",      "yf": "6758.T"},
    "9984": {"name": "SBG",          "yf": "9984.T"},
}

# 貸借テーブルの必須列（空DataFrame返却時にも保証する）
LENDING_COLS = ["_dt","申込日","買い残高","買い増減","買い新規","買い返済",
                "売り残高","売り増減","売り新規","売り返済","貸借倍率","逆日歩"]
MARGIN_COLS  = ["_dt","日付","買い残高","買い増減","売り残高","売り増減",
                "信用倍率","逆日歩","買い残増減率","売り残増減率"]

def _empty_lending() -> pd.DataFrame:
    return pd.DataFrame(columns=LENDING_COLS)

def _empty_margin() -> pd.DataFrame:
    return pd.DataFrame(columns=MARGIN_COLS)

# ── ユーティリティ ────────────────────────────────────
def _safe_int_fmt(v) -> str:
    if v is None: return "-"
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"): return "-"
        return f"{int(round(f)):,}"
    except: return "-"

def _to_float(txt: str):
    s = re.sub(r"[\s,\u3000]", "", str(txt))
    s = s.replace("▲","-").replace("－","-").replace("＊＊＊＊＊","").strip()
    if s in ("", "-", "―", "*****", "−"): return None
    try: return float(s)
    except: return None

def _parse_bal_chg(txt: str):
    """'1,289,600  +198,700' → (残高, 増減)"""
    parts = txt.strip().split()
    bal = _to_float(parts[0]) if parts else None
    chg = _to_float(parts[1]) if len(parts) > 1 else None
    return bal, chg

def _parse_two(txt: str):
    """'199,200   500' → (値1, 値2)"""
    parts = txt.strip().split()
    v1 = _to_float(parts[0]) if parts else None
    v2 = _to_float(parts[1]) if len(parts) > 1 else None
    return v1, v2

def _fetch_html(url: str) -> str:
    """複数UAでリトライしてHTMLを返す。全て失敗したら空文字。"""
    for ua in UA_LIST:
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9",
            "Referer": "https://irbank.net/",
            "Connection": "keep-alive",
        }
        for attempt in range(2):
            try:
                r = requests.get(url, headers=headers, timeout=25)
                if r.status_code == 200:
                    print(f"[OK {url[-30:]}] UA={ua[:30]}")
                    return r.text
                print(f"[HTTP {r.status_code}] {url[-30:]} attempt={attempt+1}")
            except Exception as e:
                print(f"[エラー] {url[-30:]} : {e} attempt={attempt+1}")
            time.sleep(1)
    return ""

def _get_table_rows(html: str, keywords: list) -> list:
    """keywordsをすべて含むテーブルのtr行リストを返す。"""
    if not html: return []
    soup = BeautifulSoup(html, "lxml")
    for tbl in soup.find_all("table"):
        if all(kw in tbl.get_text() for kw in keywords):
            return [[td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
                    for tr in tbl.find_all("tr")]
    return []

def _parse_yaku(txt: str):
    """'0.05  1日' → 0.05、'-' → None"""
    s = txt.strip()
    if s in ("", "-", "―"): return None
    m = re.search(r"[\d.]+", s.replace(",",""))
    return float(m.group()) if m else None

def _rows_to_records(rows: list, ncols_min: int, col_map_fn) -> list:
    """
    年ラベル行（4桁数字）から西暦を引き継ぎつつ各行を辞書に変換。
    col_map_fn(row, dt) -> dict | None
    """
    records = []
    year = datetime.today().year
    for row in rows:
        if not row: continue
        cell0 = row[0].strip()
        # 年ラベル行
        if re.fullmatch(r"\d{4}", cell0):
            year = int(cell0); continue
        # データ行：M/DD または MM/DD
        if not re.fullmatch(r"\d{1,2}/\d{2}", cell0): continue
        if len(row) < ncols_min: continue
        try:
            dt = datetime.strptime(f"{year}/{cell0.zfill(5)}", "%Y/%m/%d")
        except: continue
        rec = col_map_fn(row, dt)
        if rec: records.append(rec)
    return records


# ════════════════════════════════════════
# 貸借データ取得（IRバンク nisshokin）
# ════════════════════════════════════════
def fetch_lending(code: str) -> pd.DataFrame:
    url  = f"https://irbank.net/{code}/nisshokin"
    html = _fetch_html(url)
    rows = _get_table_rows(html, ["買い残高", "売り残高", "倍率"])

    if not rows:
        print(f"[{code}] 貸借テーブル未検出")
        return _empty_lending()

    def col_map(row, dt):
        buy_bal, buy_chg = _parse_bal_chg(row[1]) if len(row)>1 else (None,None)
        buy_new, buy_ret = _parse_two(row[2])      if len(row)>2 else (None,None)
        sel_bal, sel_chg = _parse_bal_chg(row[3]) if len(row)>3 else (None,None)
        sel_new, sel_ret = _parse_two(row[4])      if len(row)>4 else (None,None)
        ratio  = _to_float(row[5]) if len(row)>5 else None
        yaku   = _parse_yaku(row[6]) if len(row)>6 else None
        return {
            "_dt":dt, "申込日":dt.strftime("%Y/%m/%d"),
            "買い残高":buy_bal, "買い増減":buy_chg,
            "買い新規":buy_new, "買い返済":buy_ret,
            "売り残高":sel_bal, "売り増減":sel_chg,
            "売り新規":sel_new, "売り返済":sel_ret,
            "貸借倍率":ratio,   "逆日歩":yaku,
        }

    records = _rows_to_records(rows, 5, col_map)
    if not records:
        print(f"[{code}] 貸借レコード0件")
        return _empty_lending()

    df = pd.DataFrame(records)
    for c in [c for c in LENDING_COLS if c not in ("_dt","申込日")]:
        df[c] = pd.to_numeric(df.get(c, float("nan")), errors="coerce")
    df = df.sort_values("_dt", ascending=True).reset_index(drop=True)
    print(f"[{code}] 貸借: {len(df)}行 {df['申込日'].iloc[0]}〜{df['申込日'].iloc[-1]}")
    return df


# ════════════════════════════════════════
# 週次信用残取得（IRバンク margin）
# ════════════════════════════════════════
def fetch_margin(code: str) -> pd.DataFrame:
    url  = f"https://irbank.net/{code}/margin"
    html = _fetch_html(url)
    rows = _get_table_rows(html, ["買い残高", "売り残高", "倍率"])

    if not rows:
        print(f"[{code}] 信用残テーブル未検出")
        return _empty_margin()

    def col_map(row, dt):
        buy_bal, buy_chg = _parse_bal_chg(row[1]) if len(row)>1 else (None,None)
        _buy_a, _buy_b   = _parse_two(row[2])      if len(row)>2 else (None,None)
        sel_bal, sel_chg = _parse_bal_chg(row[3]) if len(row)>3 else (None,None)
        _sel_a, _sel_b   = _parse_two(row[4])      if len(row)>4 else (None,None)
        ratio = _to_float(row[5]) if len(row)>5 else None
        yaku  = _parse_yaku(row[6]) if len(row)>6 else None
        return {
            "_dt":dt, "日付":dt.strftime("%Y/%m/%d"),
            "買い残高":buy_bal, "買い増減":buy_chg,
            "売り残高":sel_bal, "売り増減":sel_chg,
            "信用倍率":ratio,   "逆日歩":yaku,
        }

    records = _rows_to_records(rows, 4, col_map)
    if not records:
        print(f"[{code}] 信用残レコード0件")
        return _empty_margin()

    df = pd.DataFrame(records)
    for c in ["買い残高","買い増減","売り残高","売り増減","信用倍率","逆日歩"]:
        df[c] = pd.to_numeric(df.get(c, float("nan")), errors="coerce")
    df = df.sort_values("_dt", ascending=True).reset_index(drop=True)
    df["買い残増減率"] = df["買い残高"].pct_change() * 100
    df["売り残増減率"] = df["売り残高"].pct_change() * 100
    print(f"[{code}] 信用残: {len(df)}件 {df['日付'].iloc[0]}〜{df['日付'].iloc[-1]}")
    return df


# ════════════════════════════════════════
# 株価・出来高（yfinance・過去1ヶ月）
# ════════════════════════════════════════
def fetch_price(ticker: str, days: int = 35) -> pd.DataFrame:
    end = datetime.today(); start = end - timedelta(days=days)
    try:
        df = yf.Ticker(ticker).history(
            start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=True)
    except Exception as e:
        print(f"[株価エラー {ticker}] {e}"); return pd.DataFrame()
    if df.empty: return pd.DataFrame()

    df = df.reset_index()
    dc = "Date" if "Date" in df.columns else "Datetime"
    df["_dt"] = pd.to_datetime(df[dc])
    df["日付"] = df["_dt"].dt.strftime("%Y/%m/%d")
    df = df.rename(columns={"Open":"始値","High":"高値","Low":"安値",
                             "Close":"終値","Volume":"出来高"})
    for c in ["始値","高値","安値","終値","出来高"]:
        if c not in df.columns: df[c] = float("nan")
    df = df[["_dt","日付","始値","高値","安値","終値","出来高"]].reset_index(drop=True)

    df["前日比%"]   = df["終値"].pct_change() * 100
    vm = df["出来高"].mean()
    df["出来高平均"] = vm
    df["日中幅"]    = df["高値"] - df["安値"]
    rm = df["日中幅"].rolling(5,min_periods=1).mean().shift(1).fillna(df["日中幅"].mean())
    vol = df["出来高"]; ret = df["前日比%"].abs()
    df["機関異常"]   = ((vol>vm*2.0)&(ret>=1.5)) | ((ret>=4.0)&(vol>vm*1.5)) | (df["日中幅"]>rm*2.0)
    df["出来高異常"]  = vol > vm*2.0
    return df.sort_values("_dt", ascending=False).reset_index(drop=True)


# ════════════════════════════════════════
# 買い/売り圧力判定
# ════════════════════════════════════════
def judge_pressure(lending: pd.DataFrame, price: pd.DataFrame) -> dict:
    if lending.empty or "貸借倍率" not in lending.columns or price.empty:
        return {"label":"データ不足","detail":"-","color":"gray"}
    pc = price["終値"].iloc[0] - price["終値"].iloc[-1]
    r  = lending["貸借倍率"].iloc[-1]
    lr = 0.0 if (r!=r or abs(r)==float("inf")) else r
    if pc<0 and lr<1: return {"label":"🔴 売り圧力優勢","detail":"株価下落＋売り残高>買い残高","color":"#f85149"}
    if pc>0 and lr>2: return {"label":"🟢 買い圧力優勢","detail":"株価上昇＋買い残高大","color":"#3fb950"}
    if pc<0 and lr>2: return {"label":"🟠 高値売り圧力","detail":"株価下落＋買い残多（高値圏）","color":"#d29922"}
    if pc>0 and lr<1: return {"label":"🔵 安値買い戻し","detail":"株価上昇＋売り残多（買い戻し）","color":"#388bfd"}
    return {"label":"⚪ 中立","detail":"方向性なし","color":"#8b949e"}


# ════════════════════════════════════════
# 全銘柄まとめて取得
# ════════════════════════════════════════
def fetch_all() -> dict:
    result = {}
    for code, info in STOCKS.items():
        print(f"\n{'='*40}\n{code} {info['name']}")
        l = fetch_lending(code); time.sleep(2)
        p = fetch_price(info["yf"]); time.sleep(1)
        m = fetch_margin(code); time.sleep(2)
        result[code] = {
            "name":     info["name"],
            "lending":  l,
            "price":    p,
            "margin":   m,
            "pressure": judge_pressure(l, p),
        }
    return result
