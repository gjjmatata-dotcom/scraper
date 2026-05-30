"""
scraper.py
・貸借データ    : taisyaku.jp（期間パラメータで複数回取得→1ヶ月分マージ）
・株価/出来高   : yfinance（過去1ヶ月）
・週次信用残    : 株探 kabutan.jp（静的HTML・約29週分）
"""

import time, re, io
import requests
from bs4 import BeautifulSoup
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, date

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
    "Referer": "https://www.taisyaku.jp/",
}

STOCKS = {
    "9432": {"name": "NTT",         "yf": "9432.T"},
    "9434": {"name": "ソフトバンク", "yf": "9434.T"},
    "6758": {"name": "ソニーG",      "yf": "6758.T"},
    "9984": {"name": "SBG",          "yf": "9984.T"},
}


# ════════════════════════════════════════
# ユーティリティ
# ════════════════════════════════════════
def _safe_int_fmt(v) -> str:
    if v is None: return "-"
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"): return "-"
        return f"{int(round(f)):,}"
    except: return "-"

def _to_float(txt):
    s = re.sub(r"[,\s\u3000]", "", str(txt))
    s = s.replace("▲", "-").replace("－", "-").strip()
    if s in ("", "-", "―", "*****"): return None
    try: return float(s)
    except: return None

def _is_valid_date(txt):
    if len(txt) == 10 and txt[4] == "/" and txt[7] == "/":
        try: datetime.strptime(txt, "%Y/%m/%d"); return True
        except: pass
    return False


# ════════════════════════════════════════
# rowspan/colspan 展開
# ════════════════════════════════════════
def _expand_table(table_tag) -> list:
    rows = table_tag.find_all("tr")
    if not rows: return []
    max_cols = max(
        sum(int(c.get("colspan", 1)) for c in r.find_all(["th", "td"]))
        for r in rows
    ) + 2
    R = len(rows)
    grid     = [[""] * max_cols for _ in range(R)]
    occupied = [[False] * max_cols for _ in range(R)]
    for ri, row in enumerate(rows):
        ci = 0
        for cell in row.find_all(["th", "td"]):
            while ci < max_cols and occupied[ri][ci]: ci += 1
            if ci >= max_cols: break
            rs = int(cell.get("rowspan", 1))
            cs = int(cell.get("colspan", 1))
            txt = cell.get_text(strip=True)
            for dr in range(rs):
                for dc in range(cs):
                    r2, c2 = ri + dr, ci + dc
                    if r2 < R and c2 < max_cols:
                        grid[r2][c2] = txt; occupied[r2][c2] = True
            ci += cs
    return grid


# ════════════════════════════════════════
# 1回分の貸借HTMLを解析して dict のリストを返す
# ════════════════════════════════════════
def _parse_lending_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    target = None
    for tbl in soup.find_all("table"):
        txt = tbl.get_text()
        if "融資" in txt and "貸株" in txt and "差引残高" in txt:
            target = tbl; break
    if not target: return []

    grid = _expand_table(target)

    # 申込日行を探す
    date_row_idx = None; dates = []; date_col_start = None
    for ri, row in enumerate(grid):
        found_d, found_c = [], []
        for ci, cell in enumerate(row):
            if _is_valid_date(cell):
                found_d.append(cell); found_c.append(ci)
        if len(found_d) >= 1:
            date_row_idx = ri; dates = found_d; date_col_start = found_c[0]; break

    if not dates: return []

    n = len(dates)
    val_cols = list(range(date_col_start, date_col_start + n))

    def get_vals(row):
        return [_to_float(row[c]) if c < len(row) else None for c in val_cols]

    def find_row(k0="", k1="", k2=""):
        for ri, row in enumerate(grid):
            if ri == date_row_idx: continue
            label = "".join(row[:4])
            c1 = row[1] if len(row) > 1 else ""
            c2 = row[2] if len(row) > 2 else ""
            if k0 and k0 not in label: continue
            if k1 and k1 not in (c1 + c2): continue
            if k2 and k2 not in c2: continue
            vals = get_vals(row)
            if any(v is not None for v in vals): return vals
        return [None] * n

    yuushi_new = find_row("融資", "新規", "新規")
    yuushi_ret = find_row("融資", "返済", "返済")
    yuushi_bal = find_row("融資", "残高", "残高")
    kashi_new  = find_row("貸株", "新規", "新規")
    kashi_ret  = find_row("貸株", "返済", "返済")
    kashi_bal  = find_row("貸株", "残高", "残高")
    sashihiki  = find_row("差引残高")

    records = []
    for i, d in enumerate(dates):
        records.append({
            "申込日_dt": datetime.strptime(d, "%Y/%m/%d"),
            "申込日":    d[5:],  # MM/DD
            "融資新規":  yuushi_new[i],
            "融資返済":  yuushi_ret[i],
            "融資残高":  yuushi_bal[i],
            "貸株新規":  kashi_new[i],
            "貸株返済":  kashi_ret[i],
            "貸株残高":  kashi_bal[i],
            "差引残高":  sashihiki[i],
        })
    return records


# ════════════════════════════════════════
# 貸借データ取得（期間パラメータで複数回→1ヶ月分）
# ════════════════════════════════════════
def fetch_lending(code: str) -> pd.DataFrame:
    """
    taisyaku.jp の期間指定パラメータを使い複数回リクエストして
    過去1ヶ月（約22営業日）分を取得・マージする。

    URLパラメータ:
      ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
    または
      デフォルト（直近7営業日）を複数回取得して合算
    """
    today = date.today()
    month_ago = today - timedelta(days=35)

    # まず期間パラメータ付きで試みる
    start_str = month_ago.strftime("%Y-%m-%d")
    end_str   = today.strftime("%Y-%m-%d")
    url_with_param = (
        f"https://www.taisyaku.jp/app/stock/detail/{code}-01"
        f"?start_date={start_str}&end_date={end_str}"
    )

    all_records: list[dict] = []

    for url in [url_with_param,
                f"https://www.taisyaku.jp/app/stock/detail/{code}-01"]:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            records = _parse_lending_html(resp.text)
            if records:
                all_records.extend(records)
                print(f"[{code}] {url[-40:]} → {len(records)}件取得")
            time.sleep(0.5)
        except Exception as e:
            print(f"[貸借取得エラー {code}] {e}")

    if not all_records:
        print(f"[{code}] 貸借データ取得失敗"); return pd.DataFrame()

    df = pd.DataFrame(all_records)
    # 重複除去・期間フィルタ・ソート
    df = df.drop_duplicates(subset=["申込日_dt"])
    cutoff_dt = datetime.combine(month_ago, datetime.min.time())
    df = df[df["申込日_dt"] >= cutoff_dt]
    df = df.sort_values("申込日_dt", ascending=True).reset_index(drop=True)

    for c in ["融資新規","融資返済","融資残高","貸株新規","貸株返済","貸株残高","差引残高"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # 申込日を MM/DD（dt付き）
    df["申込日"] = df["申込日_dt"].dt.strftime("%m/%d")

    def calc_ratio(row):
        y, k = row["融資残高"], row["貸株残高"]
        if pd.isna(y) or pd.isna(k): return float("nan")
        return float("inf") if k == 0 else round(y / k, 2)
    df["貸借倍率"] = df.apply(calc_ratio, axis=1)

    print(f"[{code}] 貸借最終: {len(df)}行 ({df['申込日'].iloc[0]}〜{df['申込日'].iloc[-1]})")
    return df


# ════════════════════════════════════════
# 週次信用残（株探）
# ════════════════════════════════════════
def fetch_margin(code: str) -> pd.DataFrame:
    url = f"https://kabutan.jp/stock/kabuka?code={code}&ashi=shin"
    headers = {**HEADERS, "Referer": "https://kabutan.jp/"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[信用残取得エラー {code}] {e}"); return pd.DataFrame()

    soup = BeautifulSoup(resp.text, "lxml")
    target = None
    for tbl in soup.find_all("table"):
        if "売り残" in tbl.get_text() and "買い残" in tbl.get_text():
            target = tbl; break
    if not target:
        print(f"[信用残テーブル未検出 {code}]"); return pd.DataFrame()

    records = []
    for tr in target.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
        if len(cells) < 8: continue
        if not re.match(r"\d{2}/\d{2}/\d{2}", cells[0]): continue
        yr = 2000 + int(cells[0][:2])
        dt_str = f"{yr}/{cells[0][3:]}"
        records.append({
            "日付_dt":   datetime.strptime(dt_str, "%Y/%m/%d"),
            "日付":      cells[0],
            "終値":      _to_float(cells[1]),
            "前週比率":  _to_float(cells[2]),
            "売買単価":  _to_float(cells[3]),
            "売買高":    _to_float(cells[4]),
            "売り残":    _to_float(cells[5]),
            "買い残":    _to_float(cells[6]),
            "信用倍率":  _to_float(cells[7]),
        })

    if not records:
        print(f"[信用残レコードなし {code}]"); return pd.DataFrame()

    df = pd.DataFrame(records)
    for c in ["終値","前週比率","売買単価","売買高","売り残","買い残","信用倍率"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.sort_values("日付_dt", ascending=True).reset_index(drop=True)
    df["買い残増減率"] = df["買い残"].pct_change() * 100
    df["売り残増減率"] = df["売り残"].pct_change() * 100
    # 表示用日付を MM/DD に統一
    df["日付"] = df["日付_dt"].dt.strftime("%m/%d")
    df = df.drop(columns=["日付_dt"])
    print(f"[{code}] 信用残: {len(df)}週分")
    return df


# ════════════════════════════════════════
# 株価・出来高（yfinance・過去1ヶ月）
# ════════════════════════════════════════
def fetch_price(yf_ticker: str, days: int = 35) -> pd.DataFrame:
    end   = datetime.today()
    start = end - timedelta(days=days)
    try:
        tk = yf.Ticker(yf_ticker)
        df = tk.history(start=start.strftime("%Y-%m-%d"),
                        end=end.strftime("%Y-%m-%d"),
                        interval="1d", auto_adjust=True)
    except Exception as e:
        print(f"[株価取得エラー {yf_ticker}] {e}"); return pd.DataFrame()
    if df.empty: return pd.DataFrame()

    df = df.reset_index()
    date_col = "Date" if "Date" in df.columns else "Datetime"
    df["日付_dt"] = pd.to_datetime(df[date_col])
    df["日付"]    = df["日付_dt"].dt.strftime("%m/%d")
    df = df.rename(columns={"Open":"始値","High":"高値","Low":"安値",
                             "Close":"終値","Volume":"出来高"})
    for c in ["日付","始値","高値","安値","終値","出来高"]:
        if c not in df.columns: df[c] = float("nan")

    df = df[["日付_dt","日付","始値","高値","安値","終値","出来高"]].reset_index(drop=True)
    df["前日比%"]  = df["終値"].pct_change() * 100
    vol_mean = df["出来高"].mean()
    df["出来高平均"] = vol_mean
    df["日中幅"]   = df["高値"] - df["安値"]
    range_mean = df["日中幅"].rolling(5, min_periods=1).mean().shift(1).fillna(df["日中幅"].mean())

    vol = df["出来高"]
    ret = df["前日比%"].abs()
    cond_a = (vol > vol_mean * 2.0) & (ret >= 1.5)
    cond_c = (ret >= 4.0) & (vol > vol_mean * 1.5)
    cond_d = (df["日中幅"] > range_mean * 2.0)
    df["機関異常"]  = cond_a | cond_c | cond_d
    df["出来高異常"] = vol > vol_mean * 2.0

    # 新しい順（降順）でソートして返す
    df = df.sort_values("日付_dt", ascending=False).reset_index(drop=True)
    return df


# ════════════════════════════════════════
# 買い/売り圧力判定
# ════════════════════════════════════════
def judge_pressure(lending: pd.DataFrame, price: pd.DataFrame) -> dict:
    if lending.empty or price.empty:
        return {"label": "データ不足", "detail": "-", "color": "gray"}
    # price は降順なので最新=先頭、最古=末尾
    price_chg = price["終値"].iloc[0] - price["終値"].iloc[-1]
    r = lending["貸借倍率"].iloc[-1]
    last_ratio = 0.0 if (r != r) else r
    if price_chg < 0 and last_ratio < 1:
        return {"label":"🔴 売り圧力優勢","detail":"株価下落 ＋ 貸株残高>融資残高（空売り優勢）","color":"#ef4444"}
    elif price_chg > 0 and last_ratio > 2:
        return {"label":"🟢 買い圧力優勢","detail":"株価上昇 ＋ 融資残高大（信用買い優勢）","color":"#22c55e"}
    elif price_chg < 0 and last_ratio > 2:
        return {"label":"🟠 高値売り圧力","detail":"株価下落 ＋ 融資多（高値圏で利益確定売り）","color":"#f97316"}
    elif price_chg > 0 and last_ratio < 1:
        return {"label":"🔵 安値買い戻し","detail":"株価上昇 ＋ 貸株多（安値から空売り買い戻し）","color":"#3b82f6"}
    return {"label":"⚪ 中立","detail":"明確な方向性なし","color":"#6b7280"}


# ════════════════════════════════════════
# 全銘柄まとめて取得
# ════════════════════════════════════════
def fetch_all() -> dict:
    result = {}
    for code, info in STOCKS.items():
        print(f"\n{'='*50}\n取得中: {code} {info['name']}")
        lending = fetch_lending(code);  time.sleep(1)
        price   = fetch_price(info["yf"]); time.sleep(1)
        margin  = fetch_margin(code);   time.sleep(1)
        result[code] = {
            "name":     info["name"],
            "lending":  lending,
            "price":    price,
            "margin":   margin,
            "pressure": judge_pressure(lending, price),
        }
    return result
