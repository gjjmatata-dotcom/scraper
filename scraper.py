"""
scraper.py  ─ 完全版
・貸借: taisyaku.jp(直近7日) + irbank.net/nisshokin(過去分)
・信用残: irbank.net/margin
・株価: irbank.net/chart → Yahoo Finance Japan → kabutan.jp(US/海外指数)
・IRバンク404の場合は①②をスキップして株価取得のみ実施
"""
import re, time, requests
from bs4 import BeautifulSoup
import pandas as pd
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

# Yahoo Finance Japan のティッカー候補サフィックス（試行順）
YF_SUFFIXES = ["", ".T", ".O", ".OS", ".N"]

# ── ユーティリティ ───────────────────────────────────
def _safe_int_fmt(v) -> str:
    try:
        f = float(v)
        return "-" if f!=f or abs(f)==float("inf") else f"{int(round(f)):,}"
    except: return "-"

def _to_float(txt):
    s = re.sub(r"[\s,\u3000%倍兆億万円]","",str(txt))
    s = s.replace("▲","-").replace("－","-").replace("＊＊＊＊＊","").strip()
    if s in ("","-","―","*****","−"): return None
    try: return float(s)
    except: return None

def _parse_yaku(txt):
    s = txt.strip()
    if s in ("","-","―","−","＊＊＊＊＊","*****"): return None
    m = re.search(r"[\d.]+", s.replace(",",""))
    return float(m.group()) if m else None

def _fetch(url, referer="https://irbank.net/") -> tuple[str, int]:
    """(html_text, status_code) を返す。失敗時は ("", status_code)"""
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
            return [[td.get_text(" ",strip=True) for td in tr.find_all(["th","td"])]
                    for tr in tbl.find_all("tr")]
    return []

def _parse_irbank_rows(rows, min_cols, mapper):
    """IRバンク形式（年ラベル行あり・MM/DD）"""
    records=[]; year=datetime.today().year
    for row in rows:
        if not row: continue
        c0=row[0].strip()
        if re.fullmatch(r"\d{4}",c0): year=int(c0); continue
        m=re.search(r"(\d{1,2})/(\d{2})",c0)
        if not m or len(row)<min_cols: continue
        try: dt=datetime(year,int(m.group(1)),int(m.group(2)))
        except: continue
        rec=mapper(row,dt)
        if rec: records.append(rec)
    return records

def _bal_chg(txt):
    p=txt.strip().split()
    return _to_float(p[0]) if p else None, _to_float(p[1]) if len(p)>1 else None

def _two(txt):
    p=txt.strip().split()
    return _to_float(p[0]) if p else None, _to_float(p[1]) if len(p)>1 else None

def _irbank_exists(code) -> bool:
    """IRバンクに銘柄が存在するか確認"""
    _, status = _fetch(f"https://irbank.net/{code}")
    return status == 200


# ════════════════════════════════════════
# taisyaku.jp 直近7営業日
# ════════════════════════════════════════
def _fetch_taisyaku(code) -> pd.DataFrame:
    html, status = _fetch(
        f"https://www.taisyaku.jp/app/stock/detail/{code}-01",
        referer="https://www.taisyaku.jp/")
    if not html: return pd.DataFrame()

    soup = BeautifulSoup(html,"lxml")
    tgt = next((t for t in soup.find_all("table")
                if "融資" in t.get_text() and "差引残高" in t.get_text()), None)
    if not tgt: return pd.DataFrame()

    rows = tgt.find_all("tr")
    mc = max(sum(int(c.get("colspan",1)) for c in r.find_all(["th","td"])) for r in rows)+2
    R  = len(rows)
    grid=[[""]*mc for _ in range(R)]; occ=[[False]*mc for _ in range(R)]
    for ri,row in enumerate(rows):
        ci=0
        for cell in row.find_all(["th","td"]):
            while ci<mc and occ[ri][ci]: ci+=1
            if ci>=mc: break
            rs=int(cell.get("rowspan",1)); cs=int(cell.get("colspan",1))
            txt=cell.get_text(strip=True)
            for dr in range(rs):
                for dc in range(cs):
                    r2,c2=ri+dr,ci+dc
                    if r2<R and c2<mc: grid[r2][c2]=txt; occ[r2][c2]=True
            ci+=cs

    date_ri=None; dates=[]; dc0=None
    for ri,row in enumerate(grid):
        fd,fc=[],[]
        for ci,cell in enumerate(row):
            if len(cell)==10 and cell[4]=="/" and cell[7]=="/":
                try: datetime.strptime(cell,"%Y/%m/%d"); fd.append(cell); fc.append(ci)
                except: pass
        if fd: date_ri=ri; dates=fd; dc0=fc[0]; break
    if not dates: return pd.DataFrame()

    n=len(dates); vc=list(range(dc0,dc0+n))
    def gv(row): return [_to_float(row[c]) if c<len(row) else None for c in vc]
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
    sh=fr("差引残高"); yaku_row=fr("最高料率")

    recs=[]
    for i,d in enumerate(dates):
        dt=datetime.strptime(d,"%Y/%m/%d")
        k=kb[i]; y=yb[i]
        ratio = round(y/k,2) if (y and k and k>0) else (float("inf") if (y and not k) else float("nan"))
        recs.append({"_dt":dt,"申込日":dt.strftime("%Y/%m/%d"),
                     "買い残高":yb[i],"買い増減":None,"買い新規":yn[i],"買い返済":yr[i],
                     "売り残高":kb[i],"売り増減":None,"売り新規":kn[i],"売り返済":kr[i],
                     "貸借倍率":ratio,"逆日歩":yaku_row[i] if yaku_row else None})

    df=pd.DataFrame(recs)
    for c in ["買い残高","買い新規","買い返済","売り残高","売り新規","売り返済","貸借倍率","逆日歩"]:
        if c in df.columns: df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.sort_values("_dt").reset_index(drop=True)


# ════════════════════════════════════════
# IRバンク nisshokin（過去分）
# ════════════════════════════════════════
LEND_COLS=["_dt","申込日","買い残高","買い増減","買い新規","買い返済",
           "売り残高","売り増減","売り新規","売り返済","貸借倍率","逆日歩"]

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


# ════════════════════════════════════════
# 貸借データ統合
# ════════════════════════════════════════
def fetch_lending(code) -> pd.DataFrame:
    df_r=_fetch_taisyaku(code); time.sleep(1)
    df_i=_fetch_irbank_lending(code)
    if df_r.empty and df_i.empty: return pd.DataFrame(columns=LEND_COLS)
    if df_r.empty: df=df_i
    elif df_i.empty: df=df_r
    else:
        rd=set(df_r["申込日"])
        df=pd.concat([df_i[~df_i["申込日"].isin(rd)],df_r],ignore_index=True)
    df=df.sort_values("_dt").reset_index(drop=True)
    cutoff=datetime.today()-timedelta(days=35)
    df=df[df["_dt"]>=cutoff].reset_index(drop=True)
    print(f"[{code}] 貸借: {len(df)}行")
    return df


# ════════════════════════════════════════
# IRバンク margin（週次信用残）
# ════════════════════════════════════════
MARGIN_COLS=["_dt","日付","買い残高","買い増減","売り残高","売り増減",
             "信用倍率","逆日歩","買い残増減率","売り残増減率"]

def fetch_margin(code) -> pd.DataFrame:
    html,_=_fetch(f"https://irbank.net/{code}/margin")
    rows=_get_rows(html,["買い残高","売り残高","倍率"])
    if not rows: return pd.DataFrame(columns=MARGIN_COLS)
    def mapper(row,dt):
        bb,bc=_bal_chg(row[1]) if len(row)>1 else (None,None)
        _,_  =_two(row[2])     if len(row)>2 else (None,None)
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
# 株価取得（IRバンク → Yahoo Finance Japan → kabutan.jp）
# ════════════════════════════════════════
PRICE_COLS=["_dt","日付","始値","高値","安値","終値","前日比%",
            "出来高","25日乖離率","PER","PBR","基準価額"]

def _add_flags(df) -> pd.DataFrame:
    vm=df["出来高"].fillna(0).mean()
    df["出来高平均"]=vm
    df["日中幅"]=(df["高値"]-df["安値"]).fillna(0)
    rm=df["日中幅"].rolling(5,min_periods=1).mean().shift(1).fillna(df["日中幅"].mean())
    vol=df["出来高"].fillna(0); ret=df["前日比%"].abs().fillna(0)
    df["機関異常"]=((vol>vm*2)&(ret>=1.5))|((ret>=4)&(vol>vm*1.5))|(df["日中幅"]>rm*2)
    df["出来高異常"]=vol>vm*2
    return df

def _irbank_chart(code, days=35) -> pd.DataFrame:
    html,_=_fetch(f"https://irbank.net/{code}/chart")
    rows=_get_rows(html,["始値","終値","25日乖離"])
    if not rows: return pd.DataFrame()
    def mapper(row,dt):
        def g(i): return _to_float(row[i]) if len(row)>i else None
        return {"_dt":dt,"日付":dt.strftime("%Y/%m/%d"),
                "始値":g(1),"高値":g(2),"安値":g(3),"終値":g(4),
                "前日比%":g(5),"出来高":g(6),"25日乖離率":g(8),
                "PER":g(9),"PBR":g(10),"基準価額":None}
    recs=_parse_irbank_rows(rows,9,mapper)
    if not recs: return pd.DataFrame()
    df=pd.DataFrame(recs)
    for c in PRICE_COLS[2:]: df[c]=pd.to_numeric(df.get(c),errors="coerce")
    df=df.sort_values("_dt").reset_index(drop=True)
    cutoff=datetime.today()-timedelta(days=days)
    return df[df["_dt"]>=cutoff].reset_index(drop=True)

def _yahoo_history(ticker_full, days=60) -> pd.DataFrame:
    """
    Yahoo Finance Japan の /history ページから株価・基準価額を取得。
    株式・ETF・指数: 日付 | 始値 | 高値 | 安値 | 終値 | 出来高 | 調整後終値
    投資信託        : 日付 | 基準価額 | 前日差 | 純資産（百万）
    日付形式        : "2026年4月15日"（日本語形式）
    """
    url = f"https://finance.yahoo.co.jp/quote/{ticker_full}/history"
    html, status = _fetch(url, referer="https://finance.yahoo.co.jp/")
    if not html or status != 200:
        return pd.DataFrame()

    soup = BeautifulSoup(html, "lxml")
    tgt = next((t for t in soup.find_all("table") if "基準価額" in t.get_text()
                or ("終値" in t.get_text() and "始値" in t.get_text())), None)
    if not tgt:
        return pd.DataFrame()

    # 投資信託テーブルか否かをヘッダーで判定
    headers = [th.get_text(strip=True) for th in tgt.find_all("th")]
    is_fund = "基準価額" in headers and "始値" not in headers

    rows = [[td.get_text(strip=True) for td in tr.find_all(["th","td"])]
            for tr in tgt.find_all("tr")]

    recs = []
    for row in rows:
        if len(row) < 3: continue
        c0 = row[0].strip()
        # 日付パース: "2026年4月15日" または "2026/4/15"
        dt = None
        m1 = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", c0)
        m2 = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", c0)
        m = m1 or m2
        if m:
            try: dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except: continue
        if dt is None: continue

        def g(i): return _to_float(row[i]) if len(row) > i else None

        if is_fund:
            # 列: 日付(0) 基準価額(1) 前日差(2) 純資産百万(3)
            kijun = g(1)
            zenjitsu_sa = g(2)   # 前日差（円）
            recs.append({
                "_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
                "始値": None, "高値": None, "安値": None,
                "終値": kijun,          # 終値として基準価額を使用
                "前日差": zenjitsu_sa,  # 前日差（円）←計算用
                "前日比%": None,        # 後で計算
                "出来高": None,
                "25日乖離率": None, "PER": None, "PBR": None,
                "基準価額": kijun,
                "純資産(百万)": g(3),
            })
        else:
            # 列: 日付(0) 始値(1) 高値(2) 安値(3) 終値(4) 出来高(5) 調整後終値(6)
            recs.append({
                "_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
                "始値": g(1), "高値": g(2), "安値": g(3), "終値": g(4),
                "前日差": None, "前日比%": None,
                "出来高": g(5),
                "25日乖離率": None, "PER": None, "PBR": None,
                "基準価額": None, "純資産(百万)": None,
            })

    if not recs: return pd.DataFrame()
    df = pd.DataFrame(recs)

    # 数値型に変換
    num_cols = ["終値","始値","高値","安値","出来高","基準価額","前日差","純資産(百万)",
                "25日乖離率","PER","PBR"]
    for c in num_cols:
        if c in df.columns: df[c] = pd.to_numeric(df.get(c), errors="coerce")

    df = df.sort_values("_dt").reset_index(drop=True)

    # 前日比% を計算
    if is_fund:
        # 投資信託: 前日差 ÷ 前日基準価額 × 100
        prev_kijun = df["基準価額"].shift(1)
        df["前日比%"] = df["前日差"] / prev_kijun * 100
    else:
        df["前日比%"] = df["終値"].pct_change() * 100

    # 25日乖離率
    ma = df["終値"].rolling(25, min_periods=1).mean()
    df["25日乖離率"] = (df["終値"] - ma) / ma * 100

    # 直近 days 日分に絞る
    cutoff = datetime.today() - timedelta(days=days)
    df = df[df["_dt"] >= cutoff].reset_index(drop=True)
    print(f"[{ticker_full}] Yahoo Finance({'投信' if is_fund else '株式'}): {len(df)}行")
    return df

def _kabutan_history(slug, days=60) -> pd.DataFrame:
    """
    us.kabutan.jp から海外指数の株価を取得。
    列順: 日付(0) 始値(1) 高値(2) 安値(3) 終値(4) 前日比円(5) 前日比%(6) 売買高(7)
    前日比%はすでに%値（+1.95 など）なのでそのまま使用する。
    """
    url = f"https://us.kabutan.jp/indexes/{slug}/historical_prices/daily"
    html, status = _fetch(url, referer="https://us.kabutan.jp/")
    if not html or status != 200: return pd.DataFrame()

    soup = BeautifulSoup(html, "lxml")
    recs = []
    for tbl in soup.find_all("table"):
        if "終値" not in tbl.get_text(): continue
        for tr in tbl.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["th","td"])]
            if len(cells) < 5: continue
            c0 = cells[0].strip()
            dt = None
            # YY/MM/DD 形式
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
            recs.append({
                "_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
                "始値": g(1), "高値": g(2), "安値": g(3), "終値": g(4),
                # 前日比%(列6)はすでに%値。前日比円(列5)は使わない
                "前日比%": g(6),
                "出来高": g(7),
                "25日乖離率": None, "PER": None, "PBR": None, "基準価額": None,
            })

    if not recs: return pd.DataFrame()
    df = pd.DataFrame(recs)
    for c in PRICE_COLS[2:]: df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.sort_values("_dt").drop_duplicates("_dt").reset_index(drop=True)
    # 25日乖離率を計算
    ma = df["終値"].rolling(25, min_periods=1).mean()
    df["25日乖離率"] = (df["終値"] - ma) / ma * 100
    cutoff = datetime.today() - timedelta(days=days)
    df = df[df["_dt"] >= cutoff].reset_index(drop=True)
    print(f"[kabutan {slug}] {len(df)}行")
    return df


def fetch_price_by_url(url: str, name: str = "", days: int = 60) -> pd.DataFrame:
    """
    URLから直接データ取得。固定ボタン用。
    kabutan URL か Yahoo Finance URL かを判定して適切なパーサーを呼ぶ。
    """
    if "us.kabutan.jp" in url:
        m = re.search(r"/indexes/([^/]+)/", url)
        slug = m.group(1) if m else ""
        df = _kabutan_history(slug, days)
    elif "finance.yahoo.co.jp" in url:
        m = re.search(r"/quote/([^/]+)/", url)
        ticker = m.group(1) if m else ""
        df = _yahoo_history(ticker, days)
    else:
        df = pd.DataFrame()
    if not df.empty:
        return _add_flags(df).sort_values("_dt", ascending=False).reset_index(drop=True)
    return pd.DataFrame(columns=PRICE_COLS + ["出来高平均","機関異常","出来高異常"])


def fetch_price(code, days=35) -> pd.DataFrame:
    """
    コードから株価を取得。IRバンク → Yahoo Finance（複数サフィックス試行）→ kabutan の順。
    """
    # 1. IRバンク chart
    df = _irbank_chart(code, days)
    if not df.empty:
        return _add_flags(df).sort_values("_dt", ascending=False).reset_index(drop=True)

    print(f"[{code}] IRバンクchart失敗 → Yahoo Finance")

    # 2. Yahoo Finance Japan（サフィックスを自動生成して試行）
    base = code.upper()
    if re.fullmatch(r"\d{6}", base):
        suffixes = [".T", ".O", ".N", ".L", ""]
    elif re.fullmatch(r"\d{4}", base):
        suffixes = [".T", ""]
    else:
        suffixes = ["", ".T"]  # 投信・ETFはサフィックスなしを先に試みる

    for sfx in suffixes:
        ticker = f"{base}{sfx}"
        df = _yahoo_history(ticker, days)
        if not df.empty:
            return _add_flags(df).sort_values("_dt", ascending=False).reset_index(drop=True)

    print(f"[{code}] Yahoo Finance失敗 → kabutan.jp")

    # 3. kabutan（海外指数コード）
    kabutan_map = {
        "NDX": "%5ENDX", "SOX": "%5ESOX", "IXIC": "%5EIXIC",
        "DJI": "%5EDJI", "GSPC": "%5EGSPC", "SPX": "%5EGSPC",
    }
    slug = kabutan_map.get(base.lstrip("^"))
    if slug:
        df = _kabutan_history(slug, days)
        if not df.empty:
            return _add_flags(df).sort_values("_dt", ascending=False).reset_index(drop=True)

    return pd.DataFrame(columns=PRICE_COLS + ["出来高平均","機関異常","出来高異常"])


# ════════════════════════════════════════
# 銘柄名取得
# ════════════════════════════════════════
def resolve_name(code) -> str:
    html,status=_fetch(f"https://irbank.net/{code}")
    if status==200 and html:
        soup=BeautifulSoup(html,"lxml")
        t=soup.find("title")
        if t:
            s=t.get_text(strip=True).replace("|","").replace("IRバンク","").replace(code,"")
            s=re.sub(r"\s+"," ",s).strip()
            if s: return s
    # Yahoo Finance Japan でも試みる
    for sfx in ["",".T",".O"]:
        html,status=_fetch(f"https://finance.yahoo.co.jp/quote/{code}{sfx}",
                           referer="https://finance.yahoo.co.jp/")
        if status==200 and html:
            soup=BeautifulSoup(html,"lxml")
            t=soup.find("title")
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
# 単一銘柄取得（IRバンク404なら①②スキップ）
# ════════════════════════════════════════
def fetch_one(code) -> dict:
    name = STOCKS.get(code,{}).get("name") or resolve_name(code) or code
    print(f"\n{'='*40}\n{code} {name}")

    exists = _irbank_exists(code)
    if exists:
        l=fetch_lending(code); time.sleep(1)
        m=fetch_margin(code);  time.sleep(1)
    else:
        print(f"[{code}] IRバンク未対応 → 貸借・信用残をスキップ")
        l=pd.DataFrame(columns=LEND_COLS)
        m=pd.DataFrame(columns=MARGIN_COLS)

    p=fetch_price(code); time.sleep(1)
    return {"name":name,"lending":l,"price":p,
            "margin":m,"pressure":judge_pressure(l,p)}
