"""
scraper.py
・貸借データ + 逆日歩 : irbank.net/{code}/nisshokin（静的HTML・ログイン不要）
・週次信用残           : irbank.net/{code}/margin（静的HTML・ログイン不要）
・株価/出来高          : yfinance（過去1ヶ月）
"""
import re, requests
import time
from bs4 import BeautifulSoup
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9",
    "Referer": "https://irbank.net/",
}

STOCKS = {
    "9432": {"name": "NTT",         "yf": "9432.T"},
    "9434": {"name": "ソフトバンク", "yf": "9434.T"},
    "6758": {"name": "ソニーG",      "yf": "6758.T"},
    "9984": {"name": "SBG",          "yf": "9984.T"},
}

# ── ユーティリティ ──────────────────────────────────────
def _safe_int_fmt(v) -> str:
    if v is None: return "-"
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"): return "-"
        return f"{int(round(f)):,}"
    except: return "-"

def _clean(txt: str) -> str:
    """セル内テキストの前処理：改行・空白・カンマ除去"""
    return re.sub(r"[\s,\u3000]", "", txt)

def _to_float(txt: str):
    s = _clean(txt).replace("▲","-").replace("－","-")
    if s in ("", "-", "―", "*****", "−"): return None
    try: return float(s)
    except: return None

def _parse_num(txt: str):
    """'1,289,600  +198,700' → 残高=1289600, 増減=+198700"""
    parts = txt.strip().split()
    residual = _to_float(parts[0]) if parts else None
    change   = _to_float(parts[1]) if len(parts) > 1 else None
    return residual, change

def _parse_shinki_hensei(txt: str):
    """'199,200   500' → 新規=199200, 返済=500"""
    parts = txt.strip().split()
    shinki = _to_float(parts[0]) if parts else None
    hensei = _to_float(parts[1]) if len(parts) > 1 else None
    return shinki, hensei


# ════════════════════════════════════════
# IRバンク共通：テーブル行パーサー
# ════════════════════════════════════════
def _fetch_irbank_table(url: str) -> tuple[list, str]:
    """
    IRバンクのページを取得してテーブルの全行（tr）と生HTMLを返す。
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.text, True
    except Exception as e:
        print(f"[IRバンク取得エラー] {url} : {e}")
        return "", False

def _extract_rows(html: str, required_cols: list) -> list[list[str]]:
    """
    HTMLからテーブル行を抽出。required_cols のキーワードを含む列ヘッダー行を探し
    それ以降のデータ行を返す。年ラベル行（例：2026）も含む。
    """
    soup = BeautifulSoup(html, "lxml")
    tgt = None
    for tbl in soup.find_all("table"):
        hdr = tbl.get_text()
        if all(kw in hdr for kw in required_cols):
            tgt = tbl; break
    if not tgt: return []

    rows = []
    for tr in tgt.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
        if cells: rows.append(cells)
    return rows


# ════════════════════════════════════════
# 貸借データ取得（IRバンク nisshokin）
# ════════════════════════════════════════
def fetch_lending(code: str) -> pd.DataFrame:
    """
    irbank.net/{code}/nisshokin から日証金速報を取得。
    列: 日付 / 買い残高 / 買い増減 / 買い新規 / 買い返済 /
        売り残高 / 売り増減 / 売り新規 / 売り返済 / 倍率 / 逆日歩
    ページ1枚分（約3ヶ月）を取得。
    """
    url = f"https://irbank.net/{code}/nisshokin"
    html, ok = _fetch_irbank_table(url)
    if not ok: return pd.DataFrame()

    rows = _extract_rows(html, ["買い残高", "売り残高", "倍率"])
    if not rows: return pd.DataFrame()

    recs = []
    current_year = datetime.today().year

    for row in rows:
        if not row: continue
        date_str = row[0].strip()

        # 年ラベル行（"2026" "2025" など数字4桁）
        if re.fullmatch(r"\d{4}", date_str):
            current_year = int(date_str)
            continue

        # データ行：MM/DD 形式
        if not re.fullmatch(r"\d{1,2}/\d{2}", date_str):
            continue
        if len(row) < 5:
            continue

        # 日付を YYYY/MM/DD に変換
        try:
            dt = datetime.strptime(f"{current_year}/{date_str.zfill(5)}", "%Y/%m/%d")
        except: continue

        # col1: 買い残高  +増減
        buy_bal, buy_chg = _parse_num(row[1]) if len(row) > 1 else (None, None)
        # col2: 買い新規  返済
        buy_new, buy_ret = _parse_shinki_hensei(row[2]) if len(row) > 2 else (None, None)
        # col3: 売り残高  +増減
        sel_bal, sel_chg = _parse_num(row[3]) if len(row) > 3 else (None, None)
        # col4: 売り新規  返済
        sel_new, sel_ret = _parse_shinki_hensei(row[4]) if len(row) > 4 else (None, None)
        # col5: 倍率
        ratio = _to_float(row[5]) if len(row) > 5 else None
        # col6: 逆日歩（例：「0.05  1日」→ 数値部分のみ取得）
        yakunitobu = None
        if len(row) > 6:
            m = re.search(r"[\d.]+", _clean(row[6]))
            raw_yaku = row[6].strip()
            if m and raw_yaku not in ("-", ""):
                yakunitobu = float(m.group())

        recs.append({
            "_dt":    dt,
            "申込日":  dt.strftime("%Y/%m/%d"),
            "買い残高": buy_bal, "買い増減":  buy_chg,
            "買い新規": buy_new, "買い返済":  buy_ret,
            "売り残高": sel_bal, "売り増減":  sel_chg,
            "売り新規": sel_new, "売り返済":  sel_ret,
            "貸借倍率": ratio,
            "逆日歩":  yakunitobu,
        })

    if not recs: return pd.DataFrame()
    df = pd.DataFrame(recs)
    num_cols = ["買い残高","買い増減","買い新規","買い返済",
                "売り残高","売り増減","売り新規","売り返済","貸借倍率","逆日歩"]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.sort_values("_dt", ascending=True).reset_index(drop=True)
    print(f"[{code}] 貸借(IRバンク): {len(df)}行 "
          f"{df['申込日'].iloc[0]}〜{df['申込日'].iloc[-1]}")
    return df


# ════════════════════════════════════════
# 週次信用残取得（IRバンク margin）
# ════════════════════════════════════════
def fetch_margin(code: str) -> pd.DataFrame:
    """
    irbank.net/{code}/margin から週次信用残を取得。
    列: 日付 / 買い残高 / 買い増減 / 一般買い / 制度買い /
        売り残高 / 売り増減 / 一般売り / 制度売り / 倍率 / 逆日歩
    """
    url = f"https://irbank.net/{code}/margin"
    html, ok = _fetch_irbank_table(url)
    if not ok: return pd.DataFrame()

    rows = _extract_rows(html, ["買い残高", "売り残高", "倍率"])
    if not rows: return pd.DataFrame()

    recs = []
    current_year = datetime.today().year

    for row in rows:
        if not row: continue
        date_str = row[0].strip()

        if re.fullmatch(r"\d{4}", date_str):
            current_year = int(date_str)
            continue
        if not re.fullmatch(r"\d{1,2}/\d{2}", date_str):
            continue
        if len(row) < 4:
            continue

        try:
            dt = datetime.strptime(f"{current_year}/{date_str.zfill(5)}", "%Y/%m/%d")
        except: continue

        buy_bal, buy_chg = _parse_num(row[1]) if len(row) > 1 else (None, None)
        buy_gen, buy_sei = _parse_shinki_hensei(row[2]) if len(row) > 2 else (None, None)
        sel_bal, sel_chg = _parse_num(row[3]) if len(row) > 3 else (None, None)
        sel_gen, sel_sei = _parse_shinki_hensei(row[4]) if len(row) > 4 else (None, None)
        ratio = _to_float(row[5]) if len(row) > 5 else None
        yakunitobu = None
        if len(row) > 6:
            m = re.search(r"[\d.]+", _clean(row[6]))
            raw_yaku = row[6].strip()
            if m and raw_yaku not in ("-", ""):
                yakunitobu = float(m.group())

        recs.append({
            "_dt":    dt,
            "日付":   dt.strftime("%Y/%m/%d"),
            "買い残高": buy_bal, "買い増減":  buy_chg,
            "売り残高": sel_bal, "売り増減":  sel_chg,
            "信用倍率": ratio,
            "逆日歩":  yakunitobu,
        })

    if not recs: return pd.DataFrame()
    df = pd.DataFrame(recs)
    for c in ["買い残高","買い増減","売り残高","売り増減","信用倍率","逆日歩"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.sort_values("_dt", ascending=True).reset_index(drop=True)
    df["買い残増減率"] = df["買い残高"].pct_change() * 100
    df["売り残増減率"] = df["売り残高"].pct_change() * 100
    print(f"[{code}] 信用残(IRバンク): {len(df)}件 "
          f"{df['日付'].iloc[0]}〜{df['日付'].iloc[-1]}")
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
    rm = df["日中幅"].rolling(5, min_periods=1).mean().shift(1).fillna(df["日中幅"].mean())
    vol = df["出来高"]; ret = df["前日比%"].abs()
    df["機関異常"]   = ((vol > vm*2.0) & (ret >= 1.5)) | ((ret >= 4.0) & (vol > vm*1.5)) | (df["日中幅"] > rm*2.0)
    df["出来高異常"]  = vol > vm*2.0
    return df.sort_values("_dt", ascending=False).reset_index(drop=True)


# ════════════════════════════════════════
# 買い/売り圧力判定
# ════════════════════════════════════════
def judge_pressure(lending: pd.DataFrame, price: pd.DataFrame) -> dict:
    if lending.empty or price.empty:
        return {"label":"データ不足","detail":"-","color":"gray"}
    pc = price["終値"].iloc[0] - price["終値"].iloc[-1]
    r  = lending["貸借倍率"].iloc[-1]
    lr = 0.0 if r != r else (0.0 if abs(r) == float("inf") else r)
    if pc < 0 and lr < 1: return {"label":"🔴 売り圧力優勢","detail":"株価下落＋売り残高>買い残高","color":"#f85149"}
    if pc > 0 and lr > 2: return {"label":"🟢 買い圧力優勢","detail":"株価上昇＋買い残高大","color":"#3fb950"}
    if pc < 0 and lr > 2: return {"label":"🟠 高値売り圧力","detail":"株価下落＋買い残多（高値圏）","color":"#d29922"}
    if pc > 0 and lr < 1: return {"label":"🔵 安値買い戻し","detail":"株価上昇＋売り残多（買い戻し）","color":"#388bfd"}
    return {"label":"⚪ 中立","detail":"方向性なし","color":"#8b949e"}


# ════════════════════════════════════════
# 全銘柄まとめて取得
# ════════════════════════════════════════
def fetch_all() -> dict:
    result = {}
    for code, info in STOCKS.items():
        print(f"\n{'='*40}\n{code} {info['name']}")
        l = fetch_lending(code); time.sleep(1)
        p = fetch_price(info["yf"]); time.sleep(1)
        m = fetch_margin(code); time.sleep(1)
        result[code] = {
            "name":     info["name"],
            "lending":  l,
            "price":    p,
            "margin":   m,
            "pressure": judge_pressure(l, p),
        }
    return result
