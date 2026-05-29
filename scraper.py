"""
scraper.py
・貸借データ + 最高料率 : taisyaku.jp（過去1ヶ月）
・株価/出来高            : yfinance（過去1ヶ月）
・週次信用残             : Yahoo Finance Japan（静的HTML・ログイン不要）
"""
import time, re, requests
from bs4 import BeautifulSoup
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, date

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9",
}

STOCKS = {
    "9432": {"name": "NTT",         "yf": "9432.T"},
    "9434": {"name": "ソフトバンク", "yf": "9434.T"},
    "6758": {"name": "ソニーG",      "yf": "6758.T"},
    "9984": {"name": "SBG",          "yf": "9984.T"},
}

# ── ユーティリティ ──────────────────────────────────
def _safe_int_fmt(v) -> str:
    if v is None: return "-"
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"): return "-"
        return f"{int(round(f)):,}"
    except: return "-"

def _to_float(txt):
    s = re.sub(r"[,\s\u3000]","",str(txt)).replace("▲","-").replace("－","-").strip()
    if s in ("","-","―","*****","＊＊＊＊＊"): return None
    try: return float(s)
    except: return None

def _is_ymd(txt):
    if len(txt)==10 and txt[4]=="/" and txt[7]=="/":
        try: datetime.strptime(txt,"%Y/%m/%d"); return True
        except: pass
    return False

def _expand_table(tbl):
    rows = tbl.find_all("tr")
    if not rows: return []
    mc = max(sum(int(c.get("colspan",1)) for c in r.find_all(["th","td"])) for r in rows)+2
    R = len(rows)
    grid     = [[""] * mc for _ in range(R)]
    occupied = [[False]*mc for _ in range(R)]
    for ri, row in enumerate(rows):
        ci = 0
        for cell in row.find_all(["th","td"]):
            while ci < mc and occupied[ri][ci]: ci += 1
            if ci >= mc: break
            rs,cs = int(cell.get("rowspan",1)), int(cell.get("colspan",1))
            txt = cell.get_text(strip=True)
            for dr in range(rs):
                for dc in range(cs):
                    r2,c2 = ri+dr, ci+dc
                    if r2<R and c2<mc: grid[r2][c2]=txt; occupied[r2][c2]=True
            ci += cs
    return grid


# ════════════════════════════════════════
# 貸借データ取得（taisyaku.jp）
# ════════════════════════════════════════
def _parse_lending_html(html: str) -> list:
    soup = BeautifulSoup(html, "lxml")
    tgt = next((t for t in soup.find_all("table")
                if "融資" in t.get_text() and "差引残高" in t.get_text()), None)
    if not tgt: return []
    grid = _expand_table(tgt)

    # 申込日行
    date_ri = None; dates = []; dc0 = None
    for ri, row in enumerate(grid):
        fd, fc = [], []
        for ci, cell in enumerate(row):
            if _is_ymd(cell): fd.append(cell); fc.append(ci)
        if len(fd) >= 1: date_ri=ri; dates=fd; dc0=fc[0]; break
    if not dates: return []

    n = len(dates)
    vc = list(range(dc0, dc0+n))
    def gv(row): return [_to_float(row[c]) if c<len(row) else None for c in vc]
    def fr(k0="", k1="", k2=""):
        for ri, row in enumerate(grid):
            if ri == date_ri: continue
            lbl = "".join(row[:4])
            c1 = row[1] if len(row)>1 else ""
            c2 = row[2] if len(row)>2 else ""
            if k0 and k0 not in lbl: continue
            if k1 and k1 not in (c1+c2): continue
            if k2 and k2 not in c2: continue
            v = gv(row)
            if any(x is not None for x in v): return v
        return [None]*n

    yn=fr("融資","新規","新規"); yr=fr("融資","返済","返済"); yb=fr("融資","残高","残高")
    kn=fr("貸株","新規","新規"); kr=fr("貸株","返済","返済"); kb=fr("貸株","残高","残高")
    sh=fr("差引残高")
    # 最高料率（品貸日数分/円）
    mr=fr("最高料率")

    recs = []
    for i, d in enumerate(dates):
        dt = datetime.strptime(d, "%Y/%m/%d")
        recs.append({
            "_dt": dt,
            "申込日":   dt.strftime("%Y/%m/%d"),
            "融資新規": yn[i], "融資返済": yr[i], "融資残高": yb[i],
            "貸株新規": kn[i], "貸株返済": kr[i], "貸株残高": kb[i],
            "差引残高": sh[i],
            "最高料率": mr[i],
        })
    return recs

def fetch_lending(code: str) -> pd.DataFrame:
    today   = date.today()
    cutoff  = datetime.combine(today - timedelta(days=35), datetime.min.time())
    h = {**HEADERS, "Referer": "https://www.taisyaku.jp/"}
    all_recs = []
    for url in [
        f"https://www.taisyaku.jp/app/stock/detail/{code}-01"
        f"?start_date={(today-timedelta(days=35)).strftime('%Y-%m-%d')}"
        f"&end_date={today.strftime('%Y-%m-%d')}",
        f"https://www.taisyaku.jp/app/stock/detail/{code}-01",
    ]:
        try:
            r = requests.get(url, headers=h, timeout=20)
            r.raise_for_status()
            all_recs.extend(_parse_lending_html(r.text))
            time.sleep(0.5)
        except Exception as e:
            print(f"[貸借エラー {code}] {e}")
    if not all_recs: return pd.DataFrame()

    df = pd.DataFrame(all_recs)
    df = df.drop_duplicates(subset=["_dt"])
    df = df[df["_dt"] >= cutoff].sort_values("_dt").reset_index(drop=True)
    for c in ["融資新規","融資返済","融資残高","貸株新規","貸株返済","貸株残高","差引残高","最高料率"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    def cr(row):
        y,k = row["融資残高"], row["貸株残高"]
        if pd.isna(y) or pd.isna(k): return float("nan")
        return float("inf") if k==0 else round(y/k, 2)
    df["貸借倍率"] = df.apply(cr, axis=1)
    print(f"[{code}] 貸借: {len(df)}行")
    return df


# ════════════════════════════════════════
# 週次信用残取得（Yahoo Finance Japan）
# ログイン不要・静的HTMLで取得可能
# ════════════════════════════════════════
def fetch_margin(code: str) -> pd.DataFrame:
    """
    Yahoo Finance Japan の信用残ページから週次データを取得。
    URL: https://finance.yahoo.co.jp/quote/{code}.T/historical?type=margin
    """
    ticker = f"{code}.T"
    url = f"https://finance.yahoo.co.jp/quote/{ticker}/historical?type=margin"
    h = {**HEADERS, "Referer": "https://finance.yahoo.co.jp/"}
    try:
        r = requests.get(url, headers=h, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[信用残取得エラー {code}] {e}")
        return _fetch_margin_fallback(code)

    soup = BeautifulSoup(r.text, "lxml")

    # テーブルを探す（売り残・買い残を含む）
    tgt = None
    for tbl in soup.find_all("table"):
        txt = tbl.get_text()
        if ("売り残" in txt or "空売り" in txt or "信用売り" in txt) and \
           ("買い残" in txt or "信用買い" in txt):
            tgt = tbl; break

    if tgt is None:
        print(f"[Yahoo信用残テーブル未検出 {code}] → フォールバック")
        return _fetch_margin_fallback(code)

    recs = []
    for tr in tgt.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["th","td"])]
        if len(cells) < 4: continue
        # 日付パターン検出
        date_str = cells[0]
        dt = None
        for fmt_str in ("%Y年%m月%d日", "%Y/%m/%d", "%y/%m/%d"):
            try: dt = datetime.strptime(date_str, fmt_str); break
            except: pass
        if dt is None: continue
        nums = [_to_float(c) for c in cells[1:]]
        recs.append({"_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
                     "売り残": nums[0] if len(nums)>0 else None,
                     "買い残": nums[1] if len(nums)>1 else None,
                     "信用倍率": nums[2] if len(nums)>2 else None})

    if not recs:
        print(f"[Yahoo信用残レコードなし {code}] → フォールバック")
        return _fetch_margin_fallback(code)

    df = pd.DataFrame(recs)
    for c in ["売り残","買い残","信用倍率"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("_dt").reset_index(drop=True)
    df["買い残増減率"] = df["買い残"].pct_change()*100
    df["売り残増減率"] = df["売り残"].pct_change()*100
    print(f"[{code}] Yahoo信用残: {len(df)}件")
    return df


def _fetch_margin_fallback(code: str) -> pd.DataFrame:
    """
    フォールバック：日証金の貸借データから週次集計して信用残の代替指標を生成
    （金曜日のデータを週次の代表値として使用）
    """
    print(f"[{code}] 日証金データから週次信用残を代替生成")
    lending = fetch_lending(code)
    if lending.empty: return pd.DataFrame()

    df = lending.copy()
    # 金曜日（weekday==4）のデータを週次代表値として抽出
    df["weekday"] = df["_dt"].dt.weekday
    weekly = df[df["weekday"]==4].copy()
    if weekly.empty:
        weekly = df.copy()

    weekly = weekly.rename(columns={"融資残高":"買い残","貸株残高":"売り残","貸借倍率":"信用倍率"})
    weekly["日付"] = weekly["申込日"]
    weekly["買い残増減率"] = weekly["買い残"].pct_change()*100
    weekly["売り残増減率"] = weekly["売り残"].pct_change()*100
    cols = ["_dt","日付","売り残","買い残","信用倍率","買い残増減率","売り残増減率"]
    return weekly[[c for c in cols if c in weekly.columns]].reset_index(drop=True)


# ════════════════════════════════════════
# 株価・出来高（yfinance・過去1ヶ月）
# ════════════════════════════════════════
def fetch_price(ticker: str, days: int=35) -> pd.DataFrame:
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
    df["_dt"]  = pd.to_datetime(df[dc])
    df["日付"]  = df["_dt"].dt.strftime("%Y/%m/%d")
    df = df.rename(columns={"Open":"始値","High":"高値","Low":"安値",
                             "Close":"終値","Volume":"出来高"})
    for c in ["始値","高値","安値","終値","出来高"]:
        if c not in df.columns: df[c] = float("nan")
    df = df[["_dt","日付","始値","高値","安値","終値","出来高"]].reset_index(drop=True)

    df["前日比%"]  = df["終値"].pct_change()*100
    vm = df["出来高"].mean()
    df["出来高平均"] = vm
    df["日中幅"]   = df["高値"] - df["安値"]
    rm = df["日中幅"].rolling(5,min_periods=1).mean().shift(1).fillna(df["日中幅"].mean())
    vol = df["出来高"]; ret = df["前日比%"].abs()
    df["機関異常"]  = ((vol>vm*2.0)&(ret>=1.5)) | ((ret>=4.0)&(vol>vm*1.5)) | (df["日中幅"]>rm*2.0)
    df["出来高異常"] = vol > vm*2.0
    # 降順（直近が上）
    return df.sort_values("_dt", ascending=False).reset_index(drop=True)


# ════════════════════════════════════════
# 買い/売り圧力判定
# ════════════════════════════════════════
def judge_pressure(lending, price):
    if lending.empty or price.empty:
        return {"label":"データ不足","detail":"-","color":"gray"}
    pc = price["終値"].iloc[0] - price["終値"].iloc[-1]
    r  = lending["貸借倍率"].iloc[-1]
    lr = 0.0 if r!=r else r
    if pc<0 and lr<1: return {"label":"🔴 売り圧力優勢","detail":"株価下落＋貸株残高>融資残高","color":"#f85149"}
    if pc>0 and lr>2: return {"label":"🟢 買い圧力優勢","detail":"株価上昇＋融資残高大","color":"#3fb950"}
    if pc<0 and lr>2: return {"label":"🟠 高値売り圧力","detail":"株価下落＋融資多（高値圏）","color":"#d29922"}
    if pc>0 and lr<1: return {"label":"🔵 安値買い戻し","detail":"株価上昇＋貸株多（買い戻し）","color":"#388bfd"}
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
        result[code] = {"name":info["name"],"lending":l,"price":p,
                        "margin":m,"pressure":judge_pressure(l,p)}
    return result
