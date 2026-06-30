"""
scraper.py v4
長期株価: yfinance で取得（JWT/DB/Playwright不要・環境非依存）
投資信託(9I31115A等): yfinanceでは取れないため Yahoo Finance スクレイピングにフォールバック
出来高なし銘柄(投資信託・一部指数)でも _add_flags がクラッシュしないよう修正
"""
import re, time, requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9",
}

STOCKS = {
    "9432": {"name": "NTT"},
    "9434": {"name": "ソフトバンク"},
    "6758": {"name": "ソニーG"},
    "9984": {"name": "SBG"},
}

# ── 銘柄種別判定 ─────────────────────────────────────
def _code_type(code: str) -> str:
    """
    投資信託 : 英数字ちょうど8桁（ドットなし）例) 9I31115A, AY311227
    日本指数  : 998xxx.T / 998xxx.O
    日本株    : 数字+任意英字.T
    米国株/指数: それ以外 例) ^IXIC, NDAQ, 2869.T は stock_jp
    """
    if re.fullmatch(r"[A-Z0-9]{8}", code):  return "fund"
    if re.match(r"998\d+\.(T|O)$", code):   return "index_jp"
    if re.match(r"\d+[A-Z]*\.T$", code):    return "stock_jp"
    return "stock_us"

# yfinance ティッカー変換テーブル
# Yahoo Finance Japan コード → yfinance ティッカー
_YF_TICKER_MAP = {
    "998407.O": "^N225",    # 日経平均
    "998405.T": "1306.T",   # TOPIX ETF (TOPIXそのものは^TOPIXだがデータ少)
    "^IXIC":    "^IXIC",
    "^N225":    "^N225",
    "^DJI":     "^DJI",
    "^GSPC":    "^GSPC",
    "^NDX":     "^NDX",
    "^SOX":     "^SOX",
    "NDAQ":     "NDAQ",
}

def _to_yf_ticker(code: str) -> list:
    """
    Yahoo Finance Japan コードを yfinance ティッカー候補リストに変換。
    複数候補を返し、fetch_price_long が順番に試す。
    """
    # 明示マップ優先
    if code in _YF_TICKER_MAP:
        return [_YF_TICKER_MAP[code]]

    ct = _code_type(code)
    if ct == "fund":
        return []  # yfinanceでは取れない → スクレイピングにフォールバック
    if ct == "stock_jp":
        # 例: 9984 → 9984.T / 1570 → 1570.T / 2869 → 2869.T
        base = code if "." in code else f"{code}.T"
        return [base]
    if ct == "index_jp":
        return []  # マップにない日本指数はスクレイピング
    # stock_us: ^から始まる指数 or 米国株
    return [code]

# ── ユーティリティ ───────────────────────────────────
def _safe_int_fmt(v) -> str:
    try:
        f = float(v)
        return "-" if f != f or abs(f) == float("inf") else f"{int(round(f)):,}"
    except: return "-"

def _to_float(txt):
    s = re.sub(r"[\s,\u3000%倍兆億万円]", "", str(txt))
    s = s.replace("▲","-").replace("－","-").replace("＊＊＊＊＊","").strip()
    if s in ("","-","―","*****","−"): return None
    try: return float(s)
    except: return None

def _parse_yaku(txt):
    s = txt.strip()
    if s in ("","-","―","−","＊＊＊＊＊","*****"): return None
    m = re.search(r"[\d.]+", s.replace(",",""))
    return float(m.group()) if m else None

def _fetch(url, referer="https://irbank.net/") -> tuple:
    h = {**HEADERS, "Referer": referer}
    for _ in range(2):
        try:
            r = requests.get(url, headers=h, timeout=20)
            if r.status_code == 200: return r.text, 200
            return "", r.status_code
        except Exception as e:
            print(f"[取得エラー] {url}: {e}")
        time.sleep(1)
    return "", 0

def _get_rows(html, keywords):
    if not html: return []
    soup = BeautifulSoup(html, "lxml")
    for tbl in soup.find_all("table"):
        if all(kw in tbl.get_text() for kw in keywords):
            return [[td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
                    for tr in tbl.find_all("tr")]
    return []

def _parse_irbank_rows(rows, min_cols, mapper):
    records=[]; year=datetime.today().year
    for row in rows:
        if not row: continue
        c0 = row[0].strip()
        if re.fullmatch(r"\d{4}", c0): year=int(c0); continue
        m = re.search(r"(\d{1,2})/(\d{2})", c0)
        if not m or len(row) < min_cols: continue
        try: dt = datetime(year, int(m.group(1)), int(m.group(2)))
        except: continue
        rec = mapper(row, dt)
        if rec: records.append(rec)
    return records

def _bal_chg(txt):
    p = txt.strip().split()
    return _to_float(p[0]) if p else None, _to_float(p[1]) if len(p)>1 else None

def _two(txt):
    p = txt.strip().split()
    return _to_float(p[0]) if p else None, _to_float(p[1]) if len(p)>1 else None

def _irbank_exists(code) -> bool:
    _, status = _fetch(f"https://irbank.net/{code}")
    return status == 200

# ════════════════════════════════════════
# taisyaku.jp 直近7営業日
# ════════════════════════════════════════
def _fetch_taisyaku(code) -> pd.DataFrame:
    html, _ = _fetch(f"https://www.taisyaku.jp/app/stock/detail/{code}-01",
                     referer="https://www.taisyaku.jp/")
    if not html: return pd.DataFrame()
    soup = BeautifulSoup(html, "lxml")
    tgt  = next((t for t in soup.find_all("table")
                 if "融資" in t.get_text() and "差引残高" in t.get_text()), None)
    if not tgt: return pd.DataFrame()

    rows = tgt.find_all("tr")
    mc   = max(sum(int(c.get("colspan",1)) for c in r.find_all(["th","td"])) for r in rows)+2
    R    = len(rows)
    grid = [[""]*mc for _ in range(R)]; occ = [[False]*mc for _ in range(R)]
    for ri, row in enumerate(rows):
        ci = 0
        for cell in row.find_all(["th","td"]):
            while ci < mc and occ[ri][ci]: ci += 1
            if ci >= mc: break
            rs = int(cell.get("rowspan",1)); cs = int(cell.get("colspan",1))
            txt = cell.get_text(strip=True)
            for dr in range(rs):
                for dc in range(cs):
                    r2,c2=ri+dr,ci+dc
                    if r2<R and c2<mc: grid[r2][c2]=txt; occ[r2][c2]=True
            ci += cs

    date_ri=None; dates=[]; dc0=None
    for ri, row in enumerate(grid):
        fd,fc=[],[]
        for ci,cell in enumerate(row):
            if len(cell)==10 and cell[4]=="/" and cell[7]=="/":
                try: datetime.strptime(cell,"%Y/%m/%d"); fd.append(cell); fc.append(ci)
                except: pass
        if fd: date_ri=ri; dates=fd; dc0=fc[0]; break
    if not dates: return pd.DataFrame()

    n=len(dates); vc_=list(range(dc0,dc0+n))
    def gv(row): return [_to_float(row[c]) if c<len(row) else None for c in vc_]
    def fr(k0="",k1="",k2=""):
        for ri,row in enumerate(grid):
            if ri==date_ri: continue
            lbl="".join(row[:4]); c1=row[1] if len(row)>1 else ""; c2=row[2] if len(row)>2 else ""
            if k0 and k0 not in lbl: continue
            if k1 and k1 not in (c1+c2): continue
            if k2 and k2 not in c2: continue
            v=gv(row)
            if any(x is not None for x in v): return v
        return [None]*n

    yn=fr("融資","新規","新規"); yr=fr("融資","返済","返済"); yb=fr("融資","残高","残高")
    kn=fr("貸株","新規","新規"); kr=fr("貸株","返済","返済"); kb=fr("貸株","残高","残高")
    yaku_row=fr("最高料率")
    recs=[]
    for i,d in enumerate(dates):
        dt=datetime.strptime(d,"%Y/%m/%d")
        k=kb[i]; y=yb[i]
        ratio=round(y/k,2) if (y and k and k>0) else (float("inf") if (y and not k) else float("nan"))
        recs.append({"_dt":dt,"申込日":dt.strftime("%Y/%m/%d"),
                     "買い残高":yb[i],"買い増減":None,"買い新規":yn[i],"買い返済":yr[i],
                     "売り残高":kb[i],"売り増減":None,"売り新規":kn[i],"売り返済":kr[i],
                     "貸借倍率":ratio,"逆日歩":yaku_row[i] if yaku_row else None})
    df=pd.DataFrame(recs)
    for c in ["買い残高","買い新規","買い返済","売り残高","売り新規","売り返済","貸借倍率","逆日歩"]:
        if c in df.columns: df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.sort_values("_dt").reset_index(drop=True)

# ════════════════════════════════════════
# IRバンク nisshokin / margin
# ════════════════════════════════════════
LEND_COLS=["_dt","申込日","買い残高","買い増減","買い新規","買い返済",
           "売り残高","売り増減","売り新規","売り返済","貸借倍率","逆日歩"]
MARGIN_COLS=["_dt","日付","買い残高","買い増減","売り残高","売り増減",
             "信用倍率","逆日歩","買い残増減率","売り残増減率"]

def _fetch_irbank_lending(code) -> pd.DataFrame:
    html,_=_fetch(f"https://irbank.net/{code}/nisshokin")
    rows=_get_rows(html,["買い残高","売り残高","倍率"])
    if not rows: return pd.DataFrame(columns=LEND_COLS)
    def mapper(row,dt):
        bb,bc=_bal_chg(row[1]) if len(row)>1 else (None,None)
        bn,br=_two(row[2])     if len(row)>2 else (None,None)
        sb,sc=_bal_chg(row[3]) if len(row)>3 else (None,None)
        sn,sr=_two(row[4])     if len(row)>4 else (None,None)
        return {"_dt":dt,"申込日":dt.strftime("%Y/%m/%d"),
                "買い残高":bb,"買い増減":bc,"買い新規":bn,"買い返済":br,
                "売り残高":sb,"売り増減":sc,"売り新規":sn,"売り返済":sr,
                "貸借倍率":_to_float(row[5]) if len(row)>5 else None,
                "逆日歩":_parse_yaku(row[6]) if len(row)>6 else None}
    recs=_parse_irbank_rows(rows,5,mapper)
    if not recs: return pd.DataFrame(columns=LEND_COLS)
    df=pd.DataFrame(recs)
    for c in LEND_COLS[2:]: df[c]=pd.to_numeric(df.get(c),errors="coerce")
    return df.sort_values("_dt").reset_index(drop=True)

def fetch_lending(code) -> pd.DataFrame:
    df_r=_fetch_taisyaku(code); time.sleep(1)
    df_i=_fetch_irbank_lending(code)
    if df_r.empty and df_i.empty: return pd.DataFrame(columns=LEND_COLS)
    if df_r.empty:   df=df_i
    elif df_i.empty: df=df_r
    else:
        rd=set(df_r["申込日"])
        df=pd.concat([df_i[~df_i["申込日"].isin(rd)],df_r],ignore_index=True)
    df=df.sort_values("_dt").reset_index(drop=True)
    cutoff=datetime.today()-timedelta(days=35)
    df=df[df["_dt"]>=cutoff].reset_index(drop=True)
    print(f"[{code}] 貸借: {len(df)}行")
    return df

def fetch_margin(code) -> pd.DataFrame:
    html,_=_fetch(f"https://irbank.net/{code}/margin")
    rows=_get_rows(html,["買い残高","売り残高","倍率"])
    if not rows: return pd.DataFrame(columns=MARGIN_COLS)
    def mapper(row,dt):
        bb,bc=_bal_chg(row[1]) if len(row)>1 else (None,None)
        sb,sc=_bal_chg(row[3]) if len(row)>3 else (None,None)
        return {"_dt":dt,"日付":dt.strftime("%Y/%m/%d"),
                "買い残高":bb,"買い増減":bc,"売り残高":sb,"売り増減":sc,
                "信用倍率":_to_float(row[5]) if len(row)>5 else None,
                "逆日歩":_parse_yaku(row[6]) if len(row)>6 else None}
    recs=_parse_irbank_rows(rows,4,mapper)
    if not recs: return pd.DataFrame(columns=MARGIN_COLS)
    df=pd.DataFrame(recs)
    for c in ["買い残高","買い増減","売り残高","売り増減","信用倍率","逆日歩"]:
        df[c]=pd.to_numeric(df.get(c),errors="coerce")
    df=df.sort_values("_dt").reset_index(drop=True)
    df["買い残増減率"]=df["買い残高"].pct_change()*100
    df["売り残増減率"]=df["売り残高"].pct_change()*100
    print(f"[{code}] 信用残: {len(df)}件")
    return df

# ════════════════════════════════════════
# 価格 DataFrame 正規化（共通処理）
# ════════════════════════════════════════
PRICE_COLS=["_dt","日付","始値","高値","安値","終値","前日比%",
            "出来高","25日乖離率","PER","PBR","基準価額"]

def _finalize_price_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    _dt・終値が揃った DataFrame に対して共通後処理を行う。
    出来高がない銘柄（投資信託等）でも安全に動作する。
    """
    df = df.sort_values("_dt").reset_index(drop=True)
    # 出来高が無ければ 0 埋め（_add_flags でクラッシュしないよう）
    if "出来高" not in df.columns:
        df["出来高"] = np.nan
    df["日付"]      = df["_dt"].dt.strftime("%Y/%m/%d")
    df["前日比%"]   = df["終値"].pct_change() * 100
    ma25            = df["終値"].rolling(25, min_periods=1).mean()
    df["25日乖離率"]= (df["終値"] - ma25) / ma25 * 100
    for c in ["PER","PBR","基準価額"]:
        if c not in df.columns: df[c] = None
    return df

def _add_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    機関異常フラグ:
      ① 出来高×1.5超 かつ 前日比±1.5%以上
      ② 前日比±4%以上 かつ 出来高×1.2超
    出来高が全て NaN の銘柄（投資信託等）はフラグを False で付与してクラッシュ回避。
    """
    vol = df["出来高"].fillna(0) if "出来高" in df.columns else pd.Series([0]*len(df), index=df.index)
    ret = df["前日比%"].abs().fillna(0) if "前日比%" in df.columns else pd.Series([0]*len(df), index=df.index)
    vm  = vol.mean()
    df["出来高平均"] = vm
    df["日中幅"]     = (df.get("高値", df["終値"]) - df.get("安値", df["終値"])).fillna(0)
    df["機関異常"]   = ((vol > vm*1.5) & (ret >= 1.5)) | ((ret >= 4.0) & (vol > vm*1.2))
    df["出来高異常"] = vol > vm*2
    return df

# ════════════════════════════════════════
# yfinance による長期株価取得
# ════════════════════════════════════════
def _fetch_yfinance(ticker: str, period: str = "max") -> pd.DataFrame:
    """
    yfinance で株価を取得し、共通形式に変換して返す。
    requirements.txt に yfinance が必要。
    """
    try:
        import yfinance as yf
        raw = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if raw.empty:
            return pd.DataFrame()
        # MultiIndex の場合はフラット化
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0] for c in raw.columns]
        raw = raw.reset_index()
        # Date 列を _dt に
        date_col = "Date" if "Date" in raw.columns else "Datetime"
        raw["_dt"] = pd.to_datetime(raw[date_col]).dt.tz_localize(None)
        df = pd.DataFrame()
        df["_dt"]  = raw["_dt"]
        df["始値"]  = pd.to_numeric(raw.get("Open"),  errors="coerce")
        df["高値"]  = pd.to_numeric(raw.get("High"),  errors="coerce")
        df["安値"]  = pd.to_numeric(raw.get("Low"),   errors="coerce")
        df["終値"]  = pd.to_numeric(raw.get("Close"), errors="coerce")
        df["出来高"] = pd.to_numeric(raw.get("Volume"), errors="coerce")
        df = _finalize_price_df(df)
        print(f"[yfinance] {ticker}: {len(df)}行 {df['_dt'].min().date()}~{df['_dt'].max().date()}")
        return df
    except ImportError:
        print("[yfinance] モジュール未インストール → pip install yfinance")
        return pd.DataFrame()
    except Exception as e:
        print(f"[yfinance] {ticker} エラー: {e}")
        return pd.DataFrame()

# ════════════════════════════════════════
# Yahoo Finance Japan スクレイピング（投資信託・指数 フォールバック）
# ════════════════════════════════════════
def _yahoo_scrape_history(ticker_full: str, pages: int = 10) -> pd.DataFrame:
    """
    Yahoo Finance Japan の履歴ページを複数ページスクレイピング。
    投資信託・日本指数など yfinance で取れない銘柄用。
    pages: 取得するページ数（1ページ≒20日、10ページ≒200日）
    """
    all_recs = []
    base_url = f"https://finance.yahoo.co.jp/quote/{ticker_full}/history"

    for page in range(1, pages + 1):
        url = base_url if page == 1 else f"{base_url}?page={page}"
        html, status = _fetch(url, referer="https://finance.yahoo.co.jp/")
        if not html or status != 200:
            break
        soup = BeautifulSoup(html, "lxml")
        tgt = next((t for t in soup.find_all("table")
                    if "基準価額" in t.get_text()
                    or ("終値" in t.get_text() and "始値" in t.get_text())), None)
        if not tgt:
            break
        headers = [th.get_text(strip=True) for th in tgt.find_all("th")]
        is_fund = "基準価額" in headers and "始値" not in headers
        page_recs = []
        for tr in tgt.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["th","td"])]
            if len(cells) < 3: continue
            c0 = cells[0].strip(); dt = None
            m = (re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", c0)
                 or re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", c0))
            if m:
                try: dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except: continue
            if dt is None: continue
            def g(i): return _to_float(cells[i]) if len(cells) > i else None
            if is_fund:
                page_recs.append({
                    "_dt": dt, "始値": None, "高値": None, "安値": None,
                    "終値": g(1), "前日差": g(2), "出来高": None,
                    "基準価額": g(1), "純資産(百万)": g(3),
                })
            else:
                page_recs.append({
                    "_dt": dt, "始値": g(1), "高値": g(2), "安値": g(3),
                    "終値": g(4), "前日差": None, "出来高": g(5),
                    "基準価額": None,
                })
        if not page_recs:
            break
        all_recs.extend(page_recs)
        time.sleep(0.5)

    if not all_recs:
        return pd.DataFrame()

    df = pd.DataFrame(all_recs)
    for c in ["終値","始値","高値","安値","出来高","基準価額","前日差","純資産(百万)"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.drop_duplicates("_dt").sort_values("_dt").reset_index(drop=True)

    # 投資信託の前日比%は前日差から計算
    if "前日差" in df.columns and df["前日差"].notna().any():
        prev = df["基準価額"].shift(1)
        df["前日比%_calc"] = df["前日差"] / prev * 100
    df = _finalize_price_df(df)
    # 投資信託の前日比%を上書き
    if "前日比%_calc" in df.columns:
        mask = df["前日比%_calc"].notna()
        df.loc[mask, "前日比%"] = df.loc[mask, "前日比%_calc"]
        df.drop(columns=["前日比%_calc"], inplace=True)

    print(f"[Yahoo scrape] {ticker_full}: {len(df)}行 {df['_dt'].min().date()}~{df['_dt'].max().date()}")
    return df

# ════════════════════════════════════════
# kabutan.jp（米国指数フォールバック）
# ════════════════════════════════════════
_KABUTAN_MAP = {
    "NDX":  "%5ENDX", "SOX":  "%5ESOX",
    "IXIC": "%5EIXIC","DJI":  "%5EDJI",
    "GSPC": "%5EGSPC","SPX":  "%5EGSPC",
}

def _kabutan_history(slug: str, days: int = 365) -> pd.DataFrame:
    html, status = _fetch(
        f"https://us.kabutan.jp/indexes/{slug}/historical_prices/daily",
        referer="https://us.kabutan.jp/")
    if not html or status != 200: return pd.DataFrame()
    soup = BeautifulSoup(html, "lxml"); recs = []
    for tbl in soup.find_all("table"):
        if "終値" not in tbl.get_text(): continue
        for tr in tbl.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["th","td"])]
            if len(cells) < 5: continue
            c0 = cells[0].strip(); dt = None
            m2 = re.match(r"^(\d{2})/(\d{2})/(\d{2})$", c0)
            m4 = re.match(r"^(\d{4})/(\d{2})/(\d{2})$", c0)
            if m2:
                try: dt = datetime(2000+int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
                except: pass
            elif m4:
                try: dt = datetime(int(m4.group(1)), int(m4.group(2)), int(m4.group(3)))
                except: pass
            if dt is None: continue
            def g(i): return _to_float(cells[i]) if len(cells) > i else None
            recs.append({"_dt": dt, "始値": g(1), "高値": g(2), "安値": g(3),
                         "終値": g(4), "前日比%": g(6), "出来高": g(7)})
    if not recs: return pd.DataFrame()
    df = pd.DataFrame(recs)
    for c in ["始値","高値","安値","終値","前日比%","出来高"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.drop_duplicates("_dt").sort_values("_dt").reset_index(drop=True)
    df = _finalize_price_df(df)
    if days > 0:
        cutoff = datetime.today() - timedelta(days=days)
        df = df[df["_dt"] >= cutoff].reset_index(drop=True)
    print(f"[kabutan] {slug}: {len(df)}行")
    return df

# ════════════════════════════════════════
# 長期株価取得（メイン）
# ════════════════════════════════════════
def fetch_price_long(code: str, days: int = 0) -> pd.DataFrame:
    """
    長期株価を取得して返す。
    優先順:
      1. yfinance（日本株/米国株/指数）
      2. Yahoo Finance スクレイピング（投資信託/日本指数 yfinanceで取れないもの）
      3. kabutan.jp（米国指数フォールバック）

    days=0: 全期間 / days>0: 直近N日
    """
    df = pd.DataFrame()

    # 1. yfinance
    yf_tickers = _to_yf_ticker(code)
    for ticker in yf_tickers:
        df = _fetch_yfinance(ticker, period="max")
        if not df.empty: break

    # 2. Yahoo Finance スクレイピング（投資信託・日本指数など）
    if df.empty:
        ct = _code_type(code)
        if ct in ("fund", "index_jp"):
            # 20ページ ≒ 約400日分
            df = _yahoo_scrape_history(code, pages=20)

    # 3. kabutan（米国指数フォールバック）
    if df.empty:
        slug = _KABUTAN_MAP.get(code.lstrip("^"))
        if slug:
            df = _kabutan_history(slug, days=0)

    if df.empty:
        return df

    df = _add_flags(df)

    if days > 0:
        cutoff = datetime.today() - timedelta(days=days)
        df = df[df["_dt"] >= cutoff].reset_index(drop=True)

    return df.sort_values("_dt", ascending=False).reset_index(drop=True)

# ════════════════════════════════════════
# 短期株価取得（IRバンク → Yahoo → kabutan）
# ════════════════════════════════════════
def _irbank_chart(code, days=35) -> pd.DataFrame:
    html, _ = _fetch(f"https://irbank.net/{code}/chart")
    rows = _get_rows(html, ["始値","終値","25日乖離"])
    if not rows: return pd.DataFrame()
    def mapper(row, dt):
        def g(i): return _to_float(row[i]) if len(row) > i else None
        return {"_dt":dt, "始値":g(1), "高値":g(2), "安値":g(3), "終値":g(4),
                "出来高":g(6), "PER":g(9), "PBR":g(10), "基準価額":None}
    recs = _parse_irbank_rows(rows, 9, mapper)
    if not recs: return pd.DataFrame()
    df = pd.DataFrame(recs)
    for c in ["始値","高値","安値","終値","出来高","PER","PBR"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("_dt").reset_index(drop=True)
    cutoff = datetime.today() - timedelta(days=days)
    df = df[df["_dt"] >= cutoff].reset_index(drop=True)
    if df.empty: return df
    # 日付・前日比%・25日乖離率を共通処理で必ず生成（dashboard側のKeyError防止）
    return _finalize_price_df(df)

def fetch_price(code, days=35) -> pd.DataFrame:
    """短期データ（貸借チャート用）。IRバンク → Yahoo → kabutan の順"""
    df = _irbank_chart(code, days)
    if not df.empty:
        return _add_flags(df).sort_values("_dt", ascending=False).reset_index(drop=True)

    print(f"[{code}] IRバンクchart失敗 → Yahoo Finance scrape")
    base = code.upper()
    suffixes = ([".T",""] if re.fullmatch(r"\d{4}", base)
                else [".T",".O",".N",".L",""] if re.fullmatch(r"\d{6}", base)
                else ["",".T"])
    for sfx in suffixes:
        df = _yahoo_scrape_history(f"{base}{sfx}", pages=3)
        if not df.empty:
            return _add_flags(df).sort_values("_dt", ascending=False).reset_index(drop=True)

    slug = _KABUTAN_MAP.get(base.lstrip("^"))
    if slug:
        df = _kabutan_history(slug, days=days)
        if not df.empty:
            return _add_flags(df).sort_values("_dt", ascending=False).reset_index(drop=True)

    return pd.DataFrame(columns=PRICE_COLS+["出来高平均","機関異常","出来高異常"])

def fetch_price_by_url(url: str, name: str = "", days: int = 0) -> pd.DataFrame:
    """URL指定で取得。比較銘柄追加用。"""
    eff = days if days > 0 else 9999
    if "us.kabutan.jp" in url:
        m = re.search(r"/indexes/([^/]+)/", url)
        df = _kabutan_history(m.group(1) if m else "", days=0)
    elif "finance.yahoo.co.jp" in url:
        m = re.search(r"/quote/([^/]+)/", url)
        ticker = m.group(1) if m else ""
        df = fetch_price_long(ticker, days=days)
        if df.empty:
            df = _yahoo_scrape_history(ticker, pages=20)
    else:
        df = pd.DataFrame()
    if not df.empty:
        if "出来高平均" not in df.columns:
            df = _add_flags(df)
        if days > 0:
            cutoff = datetime.today() - timedelta(days=days)
            df = df[df["_dt"] >= cutoff]
        return df.sort_values("_dt", ascending=False).reset_index(drop=True)
    return pd.DataFrame(columns=PRICE_COLS+["出来高平均","機関異常","出来高異常"])

# ════════════════════════════════════════
# テクニカル指標計算
# ════════════════════════════════════════
def calc_technicals(df: pd.DataFrame) -> pd.DataFrame:
    """昇順ソート済み DataFrame にテクニカル指標を追加。"""
    if df.empty or "終値" not in df.columns: return df
    df = df.sort_values("_dt").reset_index(drop=True)
    c = df["終値"].astype(float)
    # 高値・安値がなければ終値で代用
    h = df["高値"].astype(float) if "高値" in df.columns and df["高値"].notna().any() else c
    l = df["安値"].astype(float) if "安値" in df.columns and df["安値"].notna().any() else c

    df["MA5"]   = c.rolling(5,   min_periods=1).mean().round(2)
    df["MA25"]  = c.rolling(25,  min_periods=1).mean().round(2)
    df["MA75"]  = c.rolling(75,  min_periods=1).mean().round(2)
    df["MA200"] = c.rolling(200, min_periods=1).mean().round(2)

    std25 = c.rolling(25, min_periods=1).std()
    df["BB_upper"] = (df["MA25"] + 2*std25).round(2)
    df["BB_lower"] = (df["MA25"] - 2*std25).round(2)
    df["BB_%B"]    = ((c - df["BB_lower"]) / (df["BB_upper"] - df["BB_lower"])).round(3)

    # パラボリック SAR
    n=len(df); af0,step,mx=0.02,0.02,0.2
    sar=[0.]*n; ep=[0.]*n; af=[af0]*n; bl=[True]*n
    sar[0]=l.iloc[0]; ep[0]=h.iloc[0]
    for i in range(1,n):
        s=sar[i-1]+af[i-1]*(ep[i-1]-sar[i-1])
        if bl[i-1]:
            s=min(s,l.iloc[i-1],l.iloc[i-2] if i>=2 else l.iloc[i-1])
            if l.iloc[i]<s:
                bl[i]=False;sar[i]=ep[i-1];ep[i]=l.iloc[i];af[i]=af0
            else:
                bl[i]=True;sar[i]=s
                if h.iloc[i]>ep[i-1]: ep[i]=h.iloc[i];af[i]=min(af[i-1]+step,mx)
                else: ep[i]=ep[i-1];af[i]=af[i-1]
        else:
            s=max(s,h.iloc[i-1],h.iloc[i-2] if i>=2 else h.iloc[i-1])
            if h.iloc[i]>s:
                bl[i]=True;sar[i]=ep[i-1];ep[i]=h.iloc[i];af[i]=af0
            else:
                bl[i]=False;sar[i]=s
                if l.iloc[i]<ep[i-1]: ep[i]=l.iloc[i];af[i]=min(af[i-1]+step,mx)
                else: ep[i]=ep[i-1];af[i]=af[i-1]
    df["SAR"]      = pd.Series(sar).round(2)
    df["SAR_bull"] = bl

    d=c.diff()
    gain=d.clip(lower=0).rolling(14,min_periods=1).mean()
    loss=(-d.clip(upper=0)).rolling(14,min_periods=1).mean()
    df["RSI"]=(100-100/(1+gain/loss.replace(0,np.nan))).round(2)

    low14=l.rolling(14,min_periods=1).min(); high14=h.rolling(14,min_periods=1).max()
    kr=100*(c-low14)/(high14-low14)
    df["FastK"]=kr.round(2)
    df["FastD"]=kr.rolling(3,min_periods=1).mean().round(2)
    df["SlowK"]=df["FastD"]
    df["SlowD"]=df["SlowK"].rolling(3,min_periods=1).mean().round(2)

    ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean()
    df["MACD"]       =(ema12-ema26).round(2)
    df["MACD_signal"]=df["MACD"].ewm(span=9,adjust=False).mean().round(2)
    df["MACD_hist"]  =(df["MACD"]-df["MACD_signal"]).round(2)

    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    pdm=(h-h.shift()).clip(lower=0); ndm=(l.shift()-l).clip(lower=0)
    atr14=tr.rolling(14,min_periods=1).mean()
    df["DI_plus"] =(100*pdm.rolling(14,min_periods=1).mean()/atr14).round(2)
    df["DI_minus"]=(100*ndm.rolling(14,min_periods=1).mean()/atr14).round(2)
    dx=(df["DI_plus"]-df["DI_minus"]).abs()/(df["DI_plus"]+df["DI_minus"])*100
    df["ADX"]=dx.rolling(14,min_periods=1).mean().round(2)

    df["Momentum"]=(c-c.shift(10)).round(2)
    df["ROC"]=((c-c.shift(10))/c.shift(10)*100).round(3)

    return df

# ════════════════════════════════════════
# 銘柄名取得
# ════════════════════════════════════════
def resolve_name(code) -> str:
    html,status=_fetch(f"https://irbank.net/{code}")
    if status==200 and html:
        soup=BeautifulSoup(html,"lxml"); t=soup.find("title")
        if t:
            s=t.get_text(strip=True).replace("|","").replace("IRバンク","").replace(code,"")
            s=re.sub(r"\s+"," ",s).strip()
            if s: return s
    for sfx in ["",".T",".O"]:
        html,status=_fetch(f"https://finance.yahoo.co.jp/quote/{code}{sfx}",
                           referer="https://finance.yahoo.co.jp/")
        if status==200 and html:
            soup=BeautifulSoup(html,"lxml"); t=soup.find("title")
            if t:
                s=t.get_text(strip=True).split("|")[0].strip()
                if s and s!=code: return s
    return code

# ════════════════════════════════════════
# 買い/売り圧力判定
# ════════════════════════════════════════
def judge_pressure(lending, price) -> dict:
    if lending.empty or "貸借倍率" not in lending.columns or price.empty:
        return {"label":"データ不足","detail":"-","color":"gray"}
    pc=price["終値"].iloc[0]-price["終値"].iloc[-1]
    r=lending["貸借倍率"].iloc[-1]
    lr=0.0 if (r!=r or abs(r)==float("inf")) else r
    if pc<0 and lr<1: return {"label":"🔴 売り圧力優勢","detail":"株価下落＋売り残高>買い残高","color":"#f85149"}
    if pc>0 and lr>2: return {"label":"🟢 買い圧力優勢","detail":"株価上昇＋買い残高大","color":"#3fb950"}
    if pc<0 and lr>2: return {"label":"🟠 高値売り圧力","detail":"株価下落＋買い残多（高値圏）","color":"#d29922"}
    if pc>0 and lr<1: return {"label":"🔵 安値買い戻し","detail":"株価上昇＋売り残多（買い戻し）","color":"#388bfd"}
    return {"label":"⚪ 中立","detail":"方向性なし","color":"#8b949e"}

# ════════════════════════════════════════
# 単一銘柄取得
# ════════════════════════════════════════
def fetch_one(code) -> dict:
    name=STOCKS.get(code,{}).get("name") or resolve_name(code) or code
    print(f"\n{'='*40}\n{code} {name}")
    exists=_irbank_exists(code)
    if exists:
        l=fetch_lending(code); time.sleep(1)
        m=fetch_margin(code);  time.sleep(1)
    else:
        print(f"[{code}] IRバンク未対応 → 貸借・信用残スキップ")
        l=pd.DataFrame(columns=LEND_COLS)
        m=pd.DataFrame(columns=MARGIN_COLS)
    p=fetch_price(code); time.sleep(1)
    p_long=fetch_price_long(code, days=0)
    return {"name":name,"lending":l,"price":p,"price_long":p_long,
            "margin":m,"pressure":judge_pressure(l,p)}
