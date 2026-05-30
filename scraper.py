"""
scraper.py
・貸借データ + 最高料率 : taisyaku.jp HTML（過去1ヶ月）
・週次信用残             : taisyaku.jp CSV（確実に取得可能・ログイン不要）
・株価/出来高            : yfinance（過去1ヶ月）
"""
import time, re, io, requests
from bs4 import BeautifulSoup
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, date

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9",
    "Referer": "https://www.taisyaku.jp/",
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

def _to_float(txt):
    s = re.sub(r"[,\s\u3000]", "", str(txt))
    s = s.replace("▲","-").replace("－","-").replace("＊＊＊＊＊","").strip()
    if s in ("", "-", "―", "*****"): return None
    try: return float(s)
    except: return None

def _is_ymd(txt):
    if len(txt) == 10 and txt[4] == "/" and txt[7] == "/":
        try: datetime.strptime(txt, "%Y/%m/%d"); return True
        except: pass
    return False

def _expand_table(tbl):
    rows = tbl.find_all("tr")
    if not rows: return []
    mc = max(sum(int(c.get("colspan",1)) for c in r.find_all(["th","td"])) for r in rows) + 2
    R = len(rows)
    grid     = [[""] * mc for _ in range(R)]
    occupied = [[False] * mc for _ in range(R)]
    for ri, row in enumerate(rows):
        ci = 0
        for cell in row.find_all(["th","td"]):
            while ci < mc and occupied[ri][ci]: ci += 1
            if ci >= mc: break
            rs = int(cell.get("rowspan",1)); cs = int(cell.get("colspan",1))
            txt = cell.get_text(strip=True)
            for dr in range(rs):
                for dc in range(cs):
                    r2,c2 = ri+dr, ci+dc
                    if r2<R and c2<mc: grid[r2][c2]=txt; occupied[r2][c2]=True
            ci += cs
    return grid


# ════════════════════════════════════════
# 貸借データ取得（taisyaku.jp HTML）
# ════════════════════════════════════════
def _parse_lending_html(html: str) -> list:
    soup = BeautifulSoup(html, "lxml")
    tgt = next((t for t in soup.find_all("table")
                if "融資" in t.get_text() and "差引残高" in t.get_text()), None)
    if not tgt: return []
    grid = _expand_table(tgt)

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
    sh=fr("差引残高"); mr=fr("最高料率")

    recs = []
    for i, d in enumerate(dates):
        dt = datetime.strptime(d, "%Y/%m/%d")
        recs.append({
            "_dt":dt, "申込日":dt.strftime("%Y/%m/%d"),
            "融資新規":yn[i],"融資返済":yr[i],"融資残高":yb[i],
            "貸株新規":kn[i],"貸株返済":kr[i],"貸株残高":kb[i],
            "差引残高":sh[i],
            "最高料率":mr[i] if mr else None,
        })
    return recs

def fetch_lending(code: str) -> pd.DataFrame:
    today  = date.today()
    cutoff = datetime.combine(today - timedelta(days=35), datetime.min.time())
    all_recs = []
    for url in [
        (f"https://www.taisyaku.jp/app/stock/detail/{code}-01"
         f"?start_date={(today-timedelta(days=35)).strftime('%Y-%m-%d')}"
         f"&end_date={today.strftime('%Y-%m-%d')}"),
        f"https://www.taisyaku.jp/app/stock/detail/{code}-01",
    ]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            all_recs.extend(_parse_lending_html(r.text))
            time.sleep(0.5)
        except Exception as e:
            print(f"[貸借エラー {code}] {e}")

    if not all_recs: return pd.DataFrame()
    df = pd.DataFrame(all_recs)
    df = df.drop_duplicates(subset=["_dt"])
    df = df[df["_dt"] >= cutoff].sort_values("_dt").reset_index(drop=True)

    num_cols = ["融資新規","融資返済","融資残高","貸株新規","貸株返済","貸株残高","差引残高","最高料率"]
    for c in num_cols:
        if c not in df.columns:
            df[c] = float("nan")          # ← 列が無い場合は NaN で補完
        df[c] = pd.to_numeric(df[c], errors="coerce")

    def cr(row):
        y,k = row["融資残高"], row["貸株残高"]
        if pd.isna(y) or pd.isna(k): return float("nan")
        return float("inf") if k==0 else round(y/k, 2)
    df["貸借倍率"] = df.apply(cr, axis=1)
    print(f"[{code}] 貸借: {len(df)}行, 最高料率取得: {df['最高料率'].notna().sum()}件")
    return df


# ════════════════════════════════════════
# 週次信用残取得（taisyaku.jp CSV）
# ログイン不要・Streamlit Cloud でも動作確認済み
# ════════════════════════════════════════
def fetch_margin(code: str) -> pd.DataFrame:
    """
    日証金のCSVを直接ダウンロードして週次信用残を生成。
    CSVには日次の貸借データが含まれるため、金曜日を週次代表値として集計。
    列: 申込日 / 融資残高（→買い残代替）/ 貸株残高（→売り残代替）/ 貸借倍率（→信用倍率代替）
    """
    url = f"https://www.taisyaku.jp/app/stock/detail/{code}/csv"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        # 文字コード検出
        for enc in ("shift_jis", "cp932", "utf-8", "utf-8-sig"):
            try:
                text = r.content.decode(enc); break
            except: text = None
        if not text:
            raise ValueError("文字コード検出失敗")
    except Exception as e:
        print(f"[CSV取得エラー {code}] {e}")
        return _margin_from_lending(code)

    try:
        df_raw = pd.read_csv(io.StringIO(text), dtype=str, skip_blank_lines=True)
    except Exception as e:
        print(f"[CSV解析エラー {code}] {e}")
        return _margin_from_lending(code)

    # 列名正規化
    df_raw.columns = [c.strip().replace("　","").replace(" ","") for c in df_raw.columns]
    print(f"[{code}] CSV列名: {df_raw.columns.tolist()[:8]}")

    # 日付列を探す
    date_col = next((c for c in df_raw.columns if "申込" in c or "日付" in c or "DATE" in c.upper()), None)
    if date_col is None:
        print(f"[{code}] CSV日付列未検出 → フォールバック")
        return _margin_from_lending(code)

    # 融資残高・貸株残高・貸借倍率列を探す
    def find_col(*keywords):
        for c in df_raw.columns:
            if all(kw in c for kw in keywords): return c
        return None

    yb_col = find_col("融資","残高") or find_col("融資残")
    kb_col = find_col("貸株","残高") or find_col("貸株残")
    rt_col = find_col("貸借","倍率") or find_col("倍率")

    if not yb_col or not kb_col:
        print(f"[{code}] CSV必要列未検出 → フォールバック")
        return _margin_from_lending(code)

    df_raw["_dt"] = pd.to_datetime(df_raw[date_col], errors="coerce")
    df_raw = df_raw.dropna(subset=["_dt"]).copy()
    df_raw["融資残高_n"] = pd.to_numeric(df_raw[yb_col].str.replace(",",""), errors="coerce")
    df_raw["貸株残高_n"] = pd.to_numeric(df_raw[kb_col].str.replace(",",""), errors="coerce")
    if rt_col:
        df_raw["貸借倍率_n"] = pd.to_numeric(df_raw[rt_col].str.replace(",",""), errors="coerce")
    else:
        df_raw["貸借倍率_n"] = df_raw["融資残高_n"] / df_raw["貸株残高_n"].replace(0, float("nan"))

    # 金曜日を週次代表値として抽出（なければ全日次を使用）
    df_raw["weekday"] = df_raw["_dt"].dt.weekday
    weekly = df_raw[df_raw["weekday"]==4].copy()
    if weekly.empty: weekly = df_raw.copy()

    weekly = weekly.sort_values("_dt").reset_index(drop=True)
    weekly["日付"]   = weekly["_dt"].dt.strftime("%Y/%m/%d")
    weekly["買い残"] = weekly["融資残高_n"]   # 融資残高を買い残の代替指標として使用
    weekly["売り残"] = weekly["貸株残高_n"]   # 貸株残高を売り残の代替指標として使用
    weekly["信用倍率"] = weekly["貸借倍率_n"]

    weekly["買い残増減率"] = weekly["買い残"].pct_change() * 100
    weekly["売り残増減率"] = weekly["売り残"].pct_change() * 100

    result = weekly[["_dt","日付","売り残","買い残","信用倍率","買い残増減率","売り残増減率"]].copy()
    print(f"[{code}] 信用残(CSV代替): {len(result)}週分")
    return result

def _margin_from_lending(code: str) -> pd.DataFrame:
    """CSVが使えない場合のフォールバック：貸借HTMLデータから週次集計"""
    print(f"[{code}] 貸借HTMLから週次集計（最終フォールバック）")
    lending = fetch_lending(code)
    if lending.empty: return pd.DataFrame()
    df = lending.copy()
    df["weekday"] = df["_dt"].dt.weekday
    weekly = df[df["weekday"]==4].copy()
    if weekly.empty: weekly = df.copy()
    weekly = weekly.sort_values("_dt").reset_index(drop=True)
    weekly["日付"]    = weekly["申込日"]
    weekly["買い残"]  = weekly["融資残高"]
    weekly["売り残"]  = weekly["貸株残高"]
    weekly["信用倍率"] = weekly["貸借倍率"].replace(float("inf"), float("nan"))
    weekly["買い残増減率"] = weekly["買い残"].pct_change() * 100
    weekly["売り残増減率"] = weekly["売り残"].pct_change() * 100
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
    df["_dt"] = pd.to_datetime(df[dc])
    df["日付"] = df["_dt"].dt.strftime("%Y/%m/%d")
    df = df.rename(columns={"Open":"始値","High":"高値","Low":"安値",
                             "Close":"終値","Volume":"出来高"})
    for c in ["始値","高値","安値","終値","出来高"]:
        if c not in df.columns: df[c] = float("nan")
    df = df[["_dt","日付","始値","高値","安値","終値","出来高"]].reset_index(drop=True)

    df["前日比%"]  = df["終値"].pct_change() * 100
    vm = df["出来高"].mean()
    df["出来高平均"] = vm
    df["日中幅"] = df["高値"] - df["安値"]
    rm = df["日中幅"].rolling(5,min_periods=1).mean().shift(1).fillna(df["日中幅"].mean())
    vol = df["出来高"]; ret = df["前日比%"].abs()
    df["機関異常"]  = ((vol>vm*2.0)&(ret>=1.5)) | ((ret>=4.0)&(vol>vm*1.5)) | (df["日中幅"]>rm*2.0)
    df["出来高異常"] = vol > vm*2.0
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
