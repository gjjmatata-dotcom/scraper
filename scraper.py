"""
scraper.py
・貸借: taisyaku.jp（過去1ヶ月）
・株価: yfinance（過去1ヶ月）
・信用残: 株探 kabutan.jp（静的HTML・約29週）
"""
import time, re, requests
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

def _safe_int_fmt(v) -> str:
    if v is None: return "-"
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"): return "-"
        return f"{int(round(f)):,}"
    except: return "-"

def _to_float(txt):
    s = re.sub(r"[,\s\u3000]", "", str(txt)).replace("▲","-").replace("－","-").strip()
    if s in ("","-","―","*****"): return None
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

def _parse_html(html) -> list:
    soup = BeautifulSoup(html,"lxml")
    tgt = next((t for t in soup.find_all("table")
                if "融資" in t.get_text() and "差引残高" in t.get_text()), None)
    if not tgt: return []
    grid = _expand_table(tgt)
    # 申込日行を探す
    date_ri = None; dates = []; dc_start = None
    for ri, row in enumerate(grid):
        fd,fc = [],[]
        for ci,cell in enumerate(row):
            if _is_ymd(cell): fd.append(cell); fc.append(ci)
        if len(fd)>=1: date_ri=ri; dates=fd; dc_start=fc[0]; break
    if not dates: return []
    n = len(dates)
    vc = list(range(dc_start, dc_start+n))
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
    sh=fr("差引残高")
    recs=[]
    for i,d in enumerate(dates):
        dt = datetime.strptime(d,"%Y/%m/%d")
        recs.append({"_dt":dt,"申込日":dt.strftime("%Y/%m/%d"),
                     "融資新規":yn[i],"融資返済":yr[i],"融資残高":yb[i],
                     "貸株新規":kn[i],"貸株返済":kr[i],"貸株残高":kb[i],"差引残高":sh[i]})
    return recs

def fetch_lending(code: str) -> pd.DataFrame:
    today = date.today()
    cutoff = datetime.combine(today - timedelta(days=35), datetime.min.time())
    all_recs = []
    for url in [
        f"https://www.taisyaku.jp/app/stock/detail/{code}-01?start_date={(today-timedelta(days=35)).strftime('%Y-%m-%d')}&end_date={today.strftime('%Y-%m-%d')}",
        f"https://www.taisyaku.jp/app/stock/detail/{code}-01",
    ]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            all_recs.extend(_parse_html(r.text))
            time.sleep(0.5)
        except Exception as e:
            print(f"[貸借エラー {code}] {e}")
    if not all_recs: return pd.DataFrame()
    df = pd.DataFrame(all_recs)
    df = df.drop_duplicates(subset=["_dt"])
    df = df[df["_dt"] >= cutoff].sort_values("_dt").reset_index(drop=True)
    for c in ["融資新規","融資返済","融資残高","貸株新規","貸株返済","貸株残高","差引残高"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    def cr(row):
        y,k = row["融資残高"],row["貸株残高"]
        if pd.isna(y) or pd.isna(k): return float("nan")
        return float("inf") if k==0 else round(y/k,2)
    df["貸借倍率"] = df.apply(cr, axis=1)
    print(f"[{code}] 貸借: {len(df)}行 {df['申込日'].iloc[0]}〜{df['申込日'].iloc[-1]}")
    return df

def fetch_margin(code: str) -> pd.DataFrame:
    url = f"https://kabutan.jp/stock/kabuka?code={code}&ashi=shin"
    h = {**HEADERS, "Referer":"https://kabutan.jp/"}
    try:
        r = requests.get(url, headers=h, timeout=20); r.raise_for_status()
    except Exception as e:
        print(f"[信用残エラー {code}] {e}"); return pd.DataFrame()
    soup = BeautifulSoup(r.text,"lxml")
    tgt = next((t for t in soup.find_all("table")
                if "売り残" in t.get_text() and "買い残" in t.get_text()), None)
    if not tgt: return pd.DataFrame()
    recs=[]
    for tr in tgt.find_all("tr"):
        cells=[td.get_text(strip=True) for td in tr.find_all(["th","td"])]
        if len(cells)<8 or not re.match(r"\d{2}/\d{2}/\d{2}",cells[0]): continue
        yr=2000+int(cells[0][:2])
        dt=datetime(yr,int(cells[0][3:5]),int(cells[0][6:8]))
        recs.append({"_dt":dt,"日付":dt.strftime("%Y/%m/%d"),
                     "終値":_to_float(cells[1]),"前週比率":_to_float(cells[2]),
                     "売買高":_to_float(cells[4]),"売り残":_to_float(cells[5]),
                     "買い残":_to_float(cells[6]),"信用倍率":_to_float(cells[7])})
    if not recs: return pd.DataFrame()
    df = pd.DataFrame(recs)
    for c in ["終値","前週比率","売買高","売り残","買い残","信用倍率"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("_dt").reset_index(drop=True)
    df["買い残増減率"] = df["買い残"].pct_change()*100
    df["売り残増減率"] = df["売り残"].pct_change()*100
    print(f"[{code}] 信用残: {len(df)}週")
    return df

def fetch_price(ticker: str, days: int=35) -> pd.DataFrame:
    end=datetime.today(); start=end-timedelta(days=days)
    try:
        df=yf.Ticker(ticker).history(start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),interval="1d",auto_adjust=True)
    except Exception as e:
        print(f"[株価エラー {ticker}] {e}"); return pd.DataFrame()
    if df.empty: return pd.DataFrame()
    df=df.reset_index()
    dc="Date" if "Date" in df.columns else "Datetime"
    df["_dt"]=pd.to_datetime(df[dc])
    df["日付"]=df["_dt"].dt.strftime("%Y/%m/%d")  # ← 西暦付き
    df=df.rename(columns={"Open":"始値","High":"高値","Low":"安値","Close":"終値","Volume":"出来高"})
    for c in ["始値","高値","安値","終値","出来高"]:
        if c not in df.columns: df[c]=float("nan")
    df=df[["_dt","日付","始値","高値","安値","終値","出来高"]].reset_index(drop=True)
    df["前日比%"]=df["終値"].pct_change()*100
    vm=df["出来高"].mean()
    df["出来高平均"]=vm
    df["日中幅"]=df["高値"]-df["安値"]
    rm=df["日中幅"].rolling(5,min_periods=1).mean().shift(1).fillna(df["日中幅"].mean())
    vol=df["出来高"]; ret=df["前日比%"].abs()
    df["機関異常"]=(((vol>vm*2.0)&(ret>=1.5))|(ret>=4.0)&(vol>vm*1.5)|(df["日中幅"]>rm*2.0))
    df["出来高異常"]=vol>vm*2.0
    # 降順（直近が上）
    df=df.sort_values("_dt",ascending=False).reset_index(drop=True)
    return df

def judge_pressure(lending, price):
    if lending.empty or price.empty:
        return {"label":"データ不足","detail":"-","color":"gray"}
    pc=price["終値"].iloc[0]-price["終値"].iloc[-1]
    r=lending["貸借倍率"].iloc[-1]; lr=0.0 if r!=r else r
    if pc<0 and lr<1:  return {"label":"🔴 売り圧力優勢","detail":"株価下落＋貸株残高>融資残高","color":"#f85149"}
    if pc>0 and lr>2:  return {"label":"🟢 買い圧力優勢","detail":"株価上昇＋融資残高大","color":"#3fb950"}
    if pc<0 and lr>2:  return {"label":"🟠 高値売り圧力","detail":"株価下落＋融資多（高値圏）","color":"#d29922"}
    if pc>0 and lr<1:  return {"label":"🔵 安値買い戻し","detail":"株価上昇＋貸株多（買い戻し）","color":"#388bfd"}
    return {"label":"⚪ 中立","detail":"方向性なし","color":"#8b949e"}

def fetch_all() -> dict:
    result={}
    for code,info in STOCKS.items():
        print(f"\n{'='*40}\n{code} {info['name']}")
        l=fetch_lending(code); time.sleep(1)
        p=fetch_price(info["yf"]); time.sleep(1)
        m=fetch_margin(code); time.sleep(1)
        result[code]={"name":info["name"],"lending":l,"price":p,
                      "margin":m,"pressure":judge_pressure(l,p)}
    return result
