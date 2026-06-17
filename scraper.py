"""
scraper.py  ─  IRバンク専用版
・貸借データ + 逆日歩 : irbank.net/{code}/nisshokin
・週次信用残           : irbank.net/{code}/margin
・株価 + 指標          : irbank.net/{code}/chart
  （終値・出来高・25日乖離・PER・PBR を静的HTMLから取得）
"""
import re, time, requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9",
    "Referer": "https://irbank.net/",
}

# デフォルトウォッチリスト（起動時に表示）
STOCKS = {
    "9432": {"name": "NTT",         "yf": "9432.T"},
    "9434": {"name": "ソフトバンク", "yf": "9434.T"},
    "6758": {"name": "ソニーG",      "yf": "6758.T"},
    "9984": {"name": "SBG",          "yf": "9984.T"},
}

def resolve_name(code: str) -> str:
    """
    IRバンクの銘柄ページから社名を取得する。
    取得失敗時は空文字を返す。
    """
    html = _fetch(f"https://irbank.net/{code}")
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    # <title> タグは "7203 トヨタ自動車 | IRバンク" のような形式
    title = soup.find("title")
    if title:
        parts = title.get_text(strip=True).split()
        # 先頭が銘柄コード、次が社名
        if len(parts) >= 2 and parts[0] == code:
            return parts[1]
        # コードを除いた部分を社名とする
        text = title.get_text(strip=True).replace(code, "").replace("|", "").replace("IRバンク","").strip()
        if text:
            return text
    # h1 タグからも試みる
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True).replace(code, "").strip()
    return ""

# ── ユーティリティ ────────────────────────────────────
def _safe_int_fmt(v) -> str:
    if v is None: return "-"
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"): return "-"
        return f"{int(round(f)):,}"
    except: return "-"

def _to_float(txt: str):
    s = re.sub(r"[\s,\u3000]", "", str(txt)).replace("▲","-").replace("－","-").strip()
    s = re.sub(r"[%倍兆億万]", "", s)
    if s in ("", "-", "―", "*****", "−"): return None
    try: return float(s)
    except: return None

def _parse_yaku(txt: str):
    s = txt.strip()
    if s in ("", "-", "―", "−"): return None
    m = re.search(r"[\d.]+", s.replace(",",""))
    return float(m.group()) if m else None

def _fetch(url: str) -> str:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code == 200: return r.text
            print(f"[HTTP {r.status_code}] {url}")
        except Exception as e:
            print(f"[取得エラー attempt={attempt+1}] {url}: {e}")
        time.sleep(2)
    return ""

def _get_rows(html: str, keywords: list) -> list:
    if not html: return []
    soup = BeautifulSoup(html, "lxml")
    for tbl in soup.find_all("table"):
        if all(kw in tbl.get_text() for kw in keywords):
            return [[td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
                    for tr in tbl.find_all("tr")]
    return []

def _parse_rows(rows: list, min_cols: int, mapper) -> list:
    records = []; year = datetime.today().year
    for row in rows:
        if not row: continue
        c0 = row[0].strip()
        if re.fullmatch(r"\d{4}", c0): year = int(c0); continue
        # IRバンクchartは "MM/DD" または "[MM/DD]..." 形式
        m = re.search(r"(\d{1,2})/(\d{2})", c0)
        if not m: continue
        if len(row) < min_cols: continue
        try: dt = datetime(year, int(m.group(1)), int(m.group(2)))
        except: continue
        rec = mapper(row, dt)
        if rec: records.append(rec)
    return records

def _bal_chg(txt):
    parts = txt.strip().split()
    return _to_float(parts[0]) if parts else None, _to_float(parts[1]) if len(parts)>1 else None

def _two(txt):
    parts = txt.strip().split()
    return _to_float(parts[0]) if parts else None, _to_float(parts[1]) if len(parts)>1 else None


# ════════════════════════════════════════
# 貸借データ（IRバンク nisshokin）
# ════════════════════════════════════════
LEND_COLS = ["_dt","申込日","買い残高","買い増減","買い新規","買い返済",
             "売り残高","売り増減","売り新規","売り返済","貸借倍率","逆日歩"]

def fetch_lending(code: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=LEND_COLS)
    html  = _fetch(f"https://irbank.net/{code}/nisshokin")
    rows  = _get_rows(html, ["買い残高","売り残高","倍率"])
    if not rows: print(f"[{code}] 貸借テーブル未検出"); return empty

    def mapper(row, dt):
        bb, bc = _bal_chg(row[1]) if len(row)>1 else (None,None)
        bn, br = _two(row[2])     if len(row)>2 else (None,None)
        sb, sc = _bal_chg(row[3]) if len(row)>3 else (None,None)
        sn, sr = _two(row[4])     if len(row)>4 else (None,None)
        ratio  = _to_float(row[5]) if len(row)>5 else None
        yaku   = _parse_yaku(row[6]) if len(row)>6 else None
        return {"_dt":dt,"申込日":dt.strftime("%Y/%m/%d"),
                "買い残高":bb,"買い増減":bc,"買い新規":bn,"買い返済":br,
                "売り残高":sb,"売り増減":sc,"売り新規":sn,"売り返済":sr,
                "貸借倍率":ratio,"逆日歩":yaku}

    recs = _parse_rows(rows, 5, mapper)
    if not recs: return empty
    df = pd.DataFrame(recs)
    for c in LEND_COLS[2:]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.sort_values("_dt").reset_index(drop=True)
    print(f"[{code}] 貸借: {len(df)}行 {df['申込日'].iloc[0]}〜{df['申込日'].iloc[-1]}")
    return df


# ════════════════════════════════════════
# 週次信用残（IRバンク margin）
# ════════════════════════════════════════
MARGIN_COLS = ["_dt","日付","買い残高","買い増減","売り残高","売り増減",
               "信用倍率","逆日歩","買い残増減率","売り残増減率"]

def fetch_margin(code: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=MARGIN_COLS)
    html  = _fetch(f"https://irbank.net/{code}/margin")
    rows  = _get_rows(html, ["買い残高","売り残高","倍率"])
    if not rows: print(f"[{code}] 信用残テーブル未検出"); return empty

    def mapper(row, dt):
        bb, bc = _bal_chg(row[1]) if len(row)>1 else (None,None)
        _,_    = _two(row[2])     if len(row)>2 else (None,None)
        sb, sc = _bal_chg(row[3]) if len(row)>3 else (None,None)
        ratio  = _to_float(row[5]) if len(row)>5 else None
        yaku   = _parse_yaku(row[6]) if len(row)>6 else None
        return {"_dt":dt,"日付":dt.strftime("%Y/%m/%d"),
                "買い残高":bb,"買い増減":bc,"売り残高":sb,"売り増減":sc,
                "信用倍率":ratio,"逆日歩":yaku}

    recs = _parse_rows(rows, 4, mapper)
    if not recs: return empty
    df = pd.DataFrame(recs)
    for c in ["買い残高","買い増減","売り残高","売り増減","信用倍率","逆日歩"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.sort_values("_dt").reset_index(drop=True)
    df["買い残増減率"] = df["買い残高"].pct_change() * 100
    df["売り残増減率"] = df["売り残高"].pct_change() * 100
    print(f"[{code}] 信用残: {len(df)}件 {df['日付'].iloc[0]}〜{df['日付'].iloc[-1]}")
    return df


# ════════════════════════════════════════
# 株価・指標（IRバンク chart）
# 列: 日付 始値 高値 安値 終値 前日比 出来高 時価総額 25日乖離 PER PBR
# ════════════════════════════════════════
PRICE_COLS = ["_dt","日付","始値","高値","安値","終値","前日比%",
              "出来高","25日乖離率","PER","PBR"]

def fetch_price(code: str, days: int = 35) -> pd.DataFrame:
    """
    irbank.net/{code}/chart の株価推移テーブルから取得。
    直近 days 日分に絞って返す。機関異常・出来高異常フラグも付与。
    """
    empty = pd.DataFrame(columns=PRICE_COLS)
    html  = _fetch(f"https://irbank.net/{code}/chart")
    # テーブルキーワード：IRバンクchartは「始値」「高値」「安値」「終値」「25日乖離」が入る
    rows  = _get_rows(html, ["始値","終値","25日乖離"])
    if not rows: print(f"[{code}] chartテーブル未検出"); return empty

    def mapper(row, dt):
        # 列順: 日付(0) 始値(1) 高値(2) 安値(3) 終値(4) 前日比(5) 出来高(6) 時価総額(7) 25日乖離(8) PER(9) PBR(10)
        def g(i): return _to_float(row[i]) if len(row) > i else None
        return {
            "_dt":      dt,
            "日付":     dt.strftime("%Y/%m/%d"),
            "始値":     g(1), "高値": g(2), "安値": g(3), "終値": g(4),
            "前日比%":  g(5),
            "出来高":   g(6),
            "25日乖離率": g(8),
            "PER":      g(9),
            "PBR":      g(10),
        }

    recs = _parse_rows(rows, 9, mapper)
    if not recs: print(f"[{code}] chartレコード0件"); return empty

    df = pd.DataFrame(recs)
    for c in PRICE_COLS[2:]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.sort_values("_dt").reset_index(drop=True)

    # 直近 days 日分に絞る
    cutoff = datetime.today() - timedelta(days=days)
    df = df[df["_dt"] >= cutoff].reset_index(drop=True)
    if df.empty: return empty

    # 機関異常・出来高異常フラグ
    vm = df["出来高"].mean()
    df["出来高平均"] = vm
    df["日中幅"]    = df["高値"] - df["安値"]
    rm = df["日中幅"].rolling(5, min_periods=1).mean().shift(1).fillna(df["日中幅"].mean())
    vol = df["出来高"]; ret = df["前日比%"].abs()
    df["機関異常"]   = ((vol>vm*2.0)&(ret>=1.5)) | ((ret>=4.0)&(vol>vm*1.5)) | (df["日中幅"]>rm*2.0)
    df["出来高異常"]  = vol > vm*2.0

    # 降順（直近が上）で返す
    df = df.sort_values("_dt", ascending=False).reset_index(drop=True)
    print(f"[{code}] chart: {len(df)}行 最新={df['日付'].iloc[0]}")
    return df


# ════════════════════════════════════════
# 買い/売り圧力判定
# ════════════════════════════════════════
def judge_pressure(lending: pd.DataFrame, price: pd.DataFrame) -> dict:
    if lending.empty or "貸借倍率" not in lending.columns or price.empty:
        return {"label":"データ不足","detail":"-","color":"gray"}
    pc = price["終値"].iloc[0] - price["終値"].iloc[-1]
    r  = lending["貸借倍率"].iloc[-1]
    lr = 0.0 if (r != r or abs(r) == float("inf")) else r
    if pc<0 and lr<1: return {"label":"🔴 売り圧力優勢","detail":"株価下落＋売り残高>買い残高","color":"#f85149"}
    if pc>0 and lr>2: return {"label":"🟢 買い圧力優勢","detail":"株価上昇＋買い残高大","color":"#3fb950"}
    if pc<0 and lr>2: return {"label":"🟠 高値売り圧力","detail":"株価下落＋買い残多（高値圏）","color":"#d29922"}
    if pc>0 and lr<1: return {"label":"🔵 安値買い戻し","detail":"株価上昇＋売り残多（買い戻し）","color":"#388bfd"}
    return {"label":"⚪ 中立","detail":"方向性なし","color":"#8b949e"}


# ════════════════════════════════════════
# 全銘柄まとめて取得
# ════════════════════════════════════════
def fetch_one(code: str) -> dict:
    """単一銘柄のデータを取得して返す。"""
    name = STOCKS.get(code, {}).get("name") or resolve_name(code) or code
    print(f"\n{'='*40}\n{code} {name}")
    l = fetch_lending(code); time.sleep(2)
    p = fetch_price(code);   time.sleep(2)
    m = fetch_margin(code);  time.sleep(2)
    return {"name": name, "lending": l, "price": p,
            "margin": m, "pressure": judge_pressure(l, p)}
