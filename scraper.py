"""
scraper.py v5
長期株価: yfinance で取得（JWT/DB/Playwright不要・環境非依存）
投資信託(9I31115A等): yfinance/Yahoo長期スクレイピングどちらも不可なため、
                      毎回の短期データ取得結果を price_history_cache.json に
                      日付キーで蓄積し、時間経過とともに自前で長期データを構築する。
出来高なし銘柄(投資信託・一部指数)でも _add_flags がクラッシュしないよう修正
"""
import re, time, requests, json, os
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from io import StringIO

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

# ════════════════════════════════════════
# 価格キャッシュ（Supabase優先 / ローカルJSONフォールバック）
# ════════════════════════════════════════
# ─ 環境変数 ─
#   SUPABASE_URL : https://xxxx.supabase.co
#   SUPABASE_KEY : anon公開キー
#   （未設定時は price_history_cache.json にローカル保存）
# ─ Supabase テーブル（一度だけSQL Editorで実行） ─
#   create table price_cache (
#     code  text not null,
#     date  text not null,
#     data  jsonb not null,
#     primary key (code, date)
#   );

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
_USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

CACHE_FILE   = Path(os.environ.get("PRICE_CACHE_PATH", "price_history_cache.json"))
_CACHE_COLS  = ["始値","高値","安値","終値","出来高","基準価額","純資産(百万)","PER","PBR"]


# ── Supabase REST API ヘルパー ───────────────────────
def _sb_headers() -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates",  # upsert
    }

def _sb_url(path="") -> str:
    return f"{SUPABASE_URL}/rest/v1/price_cache{path}"


def _supa_load(code: str) -> dict:
    """Supabase から指定銘柄の全行を {date: {col:val}} 形式で返す。"""
    try:
        r = requests.get(
            _sb_url(f"?code=eq.{code}&select=date,data"),
            headers=_sb_headers(), timeout=10)
        if r.status_code != 200:
            print(f"[Supabase読込エラー] {r.status_code}: {r.text[:100]}")
            return {}
        return {row["date"]: row["data"] for row in r.json()}
    except Exception as e:
        print(f"[Supabase読込例外] {e}")
        return {}

def _supa_upsert(code: str, rows: list):
    """
    rows = [{"code": code, "date": "2026-06-01", "data": {...}}, ...]
    Supabase に upsert（INSERT OR UPDATE）する。
    一度に最大500行送信。
    """
    CHUNK = 500
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i+CHUNK]
        try:
            r = requests.post(
                _sb_url(),
                headers=_sb_headers(),
                json=chunk, timeout=20)
            if r.status_code not in (200, 201):
                print(f"[Supabase書込エラー] {r.status_code}: {r.text[:150]}")
        except Exception as e:
            print(f"[Supabase書込例外] {e}")


# ── ローカル JSON ヘルパー ───────────────────────────
def _local_load() -> dict:
    if not CACHE_FILE.exists(): return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ローカルキャッシュ読込エラー] {e}"); return {}

def _local_save(cache: dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        print(f"[ローカルキャッシュ保存エラー] {e}")


# ── 公開インターフェース ─────────────────────────────
def cache_append(code: str, df: pd.DataFrame):
    """
    取得した df をキャッシュに追記する。
    Supabase が設定されていればクラウドに、なければローカルJSONに保存。
    新規日付のみ書き込み（既存日付は最新値で上書き）。
    """
    if df.empty or "_dt" not in df.columns:
        return

    # DataFrame → {date_key: {col:val}} に変換
    new_entries: dict[str, dict] = {}
    for _, row in df.iterrows():
        date_key = row["_dt"].strftime("%Y-%m-%d")
        entry = {}
        for c in _CACHE_COLS:
            if c in df.columns:
                v = row.get(c)
                entry[c] = None if pd.isna(v) else float(v)
        new_entries[date_key] = entry

    if _USE_SUPABASE:
        # 既存日付を取得して差分だけ送信（リクエスト削減）
        existing = set(_supa_load(code).keys())
        rows_to_write = [
            {"code": code, "date": d, "data": v}
            for d, v in new_entries.items()
        ]
        if rows_to_write:
            _supa_upsert(code, rows_to_write)
            new_count = sum(1 for d in new_entries if d not in existing)
            print(f"[Supabaseキャッシュ] {code}: 新規{new_count}件 upsert / 累計{len(existing)+new_count}件")
    else:
        cache = _local_load()
        bucket = cache.setdefault(code, {})
        added = sum(1 for d in new_entries if d not in bucket)
        bucket.update(new_entries)
        if added > 0:
            _local_save(cache)
            print(f"[ローカルキャッシュ] {code}: 新規{added}件 / 累計{len(bucket)}件")


def cache_load_df(code: str) -> pd.DataFrame:
    """キャッシュから指定銘柄の全期間データをDataFrameで返す。無ければ空。"""
    if _USE_SUPABASE:
        bucket = _supa_load(code)
    else:
        bucket = _local_load().get(code, {})

    if not bucket:
        return pd.DataFrame()

    recs = []
    for date_key, entry in bucket.items():
        try: dt = datetime.strptime(date_key, "%Y-%m-%d")
        except Exception: continue
        rec = {"_dt": dt}
        rec.update(entry)
        recs.append(rec)

    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs).sort_values("_dt").reset_index(drop=True)
    return _finalize_price_df(df)


# ── 銘柄種別判定・正規化 ─────────────────────────────
def _normalize_jp_code(code: str) -> str:
    """
    日本株/ETFの裸コード（.Tなし）を .T 付きに正規化する。
    投資信託(英数字8桁)・指数(998xxx)・米国系(^含む/英字)はそのまま返す。
    例: "2869" → "2869.T" / "1570" → "1570.T" / "9I31115A" → そのまま
    """
    if re.fullmatch(r"[A-Z0-9]{8}", code):       return code  # 投資信託
    if re.match(r"998\d+\.(T|O)$", code):        return code  # 指数（既に.T/.O付き）
    # 裸の日本株/ETFコード: 従来形式(4桁数字、例:9432/1570)と
    # 2024年以降の新形式(3桁数字+英字1文字、例:285A/593A/153A)の両方に対応
    if re.fullmatch(r"\d{4}|\d{3}[A-Z]", code):  return f"{code}.T"
    return code

def _code_type(code: str) -> str:
    """
    投資信託  : 英数字ちょうど8桁（ドットなし）例) 9I31115A, AY311227
    日本指数  : 998xxx.T / 998xxx.O
    日本株    : 数字+任意英字.T
    米国株/指数: それ以外 例) ^IXIC, NDAQ
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
    # S&P500のよくある別表記 → yfinance正式ティッカー(^GSPC)へのエイリアス
    # ("SPX"はCBOEオプション用シンボルでありyfinanceでは無効なティッカーのため注意)
    "SPX":      "^GSPC",
    "SP500":    "^GSPC",
    "S&P500":   "^GSPC",
    "GSPC":     "^GSPC",
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

def _fetch(url, referer="https://irbank.net/", retries=3) -> tuple:
    """
    HTTPリクエストを行う。500系エラーや一時的な失敗は待機してリトライする。
    （修正前は最初の試行で200以外なら即returnしていたためリトライが機能していなかった）
    """
    h = {**HEADERS, "Referer": referer}
    last_status = 0
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=h, timeout=20)
            if r.status_code == 200:
                return r.text, 200
            last_status = r.status_code
            # 500系（サーバー側一時エラー）やレート制限は待って再試行
            if r.status_code >= 500 or r.status_code == 429:
                wait = 1.5 * (attempt + 1)
                time.sleep(wait)
                continue
            # 404等は再試行しても無駄なので即終了
            return "", r.status_code
        except Exception as e:
            print(f"[取得エラー] {url}: {e}")
            last_status = 0
        time.sleep(1.5 * (attempt + 1))
    return "", last_status

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

# ════════════════════════════════════════
# ①貸借 / ②信用残 の蓄積ストア
# ════════════════════════════════════════
# 取得したデータを銘柄ごとに保存し、2回目以降は「日付が新しい分だけ」追加する。
# 保存先は価格キャッシュと同じ（Supabase優先 / 無ければローカルJSON）。
# Supabaseは既存の price_cache テーブルを流用する（新規テーブル作成は不要）。
#   code = "margin:1570" / "lending:1570" のように種類名を付けて衝突を避ける
#   date = "YYYY-MM-DD"
#   date = "1900-01-01" の行は取得済みフラグ等のメタ情報用（データではない）
STORE_FILE = Path(os.environ.get("MARGIN_STORE_PATH", "margin_lending_store.json"))
_STORE_META_DATE = "1900-01-01"
_STORE_VALUE_COLS = {
    "margin":  ["買い残高","買い増減","売り残高","売り増減","信用倍率","逆日歩"],
    "lending": ["買い残高","買い増減","買い新規","買い返済",
                "売り残高","売り増減","売り新規","売り返済","貸借倍率","逆日歩"],
    "seido2":  ["制度買い残高","制度買い増減","制度売り残高","制度売り増減","制度信用倍率",
                "一般買い残高","一般売り残高","合計買い残高","合計売り残高","合計信用倍率"],
}

def _store_key(kind: str, code) -> str:
    base = re.sub(r"\.[A-Z]+$", "", str(code))
    return f"{kind}:{base}"

def _local_store_load() -> dict:
    if not STORE_FILE.exists(): return {}
    try:
        with open(STORE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[蓄積ストア読込エラー] {e}"); return {}

def _local_store_save(store: dict):
    try:
        with open(STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        print(f"[蓄積ストア保存エラー] {e}")

def _kv_load(key: str) -> dict:
    """指定キーの全行を {date: data} で返す。Supabaseは1000行ずつページングして全件取得。"""
    if not _USE_SUPABASE:
        return _local_store_load().get(key, {})
    out, off = {}, 0
    enc = requests.utils.quote(key, safe="")
    while True:
        try:
            r = requests.get(
                _sb_url(f"?code=eq.{enc}&select=date,data&order=date.asc&limit=1000&offset={off}"),
                headers=_sb_headers(), timeout=15)
        except Exception as e:
            print(f"[蓄積ストア Supabase読込例外] {key}: {e}"); return out
        if r.status_code != 200:
            print(f"[蓄積ストア Supabase読込エラー] {key}: {r.status_code} {r.text[:100]}"); return out
        rows = r.json()
        for row in rows: out[row["date"]] = row["data"]
        if len(rows) < 1000: break
        off += 1000
    return out

def _kv_upsert(key: str, entries: dict):
    if not entries: return
    if _USE_SUPABASE:
        _supa_upsert(key, [{"code": key, "date": d, "data": v} for d, v in entries.items()])
    else:
        store = _local_store_load()
        store.setdefault(key, {}).update(entries)
        _local_store_save(store)

def _enc_val(v):
    """JSON保存用。NaN/None→None、±inf→"inf"/"-inf"(文字列)、その他→float。"""
    try:
        if v is None or pd.isna(v): return None
        f = float(v)
    except Exception:
        return None
    return str(f) if f in (float("inf"), float("-inf")) else f

def store_load(kind: str, code):
    """蓄積済みデータを (DataFrame, メタ情報dict, 生データdict) で返す。"""
    cols = _STORE_VALUE_COLS[kind]
    raw = _kv_load(_store_key(kind, code)) or {}
    meta = dict(raw.get(_STORE_META_DATE) or {})
    recs = []
    for d, data in raw.items():
        if d == _STORE_META_DATE: continue
        try: dt = datetime.strptime(d, "%Y-%m-%d")
        except Exception: continue
        rec = {"_dt": dt}
        for c in cols:
            v = (data or {}).get(c)
            rec[c] = float(v) if v is not None else np.nan   # "inf"文字列もfloat()で復元
        recs.append(rec)
    df = pd.DataFrame(recs) if recs else pd.DataFrame(columns=["_dt"] + cols)
    return df, meta, raw

def store_save(kind: str, code, df: pd.DataFrame, raw: dict, meta: dict):
    """変更があった行だけを書き込む（毎回全件を書き直さない）。"""
    cols = _STORE_VALUE_COLS[kind]
    entries = {}
    for _, row in df.iterrows():
        d = row["_dt"].strftime("%Y-%m-%d")
        entry = {c: _enc_val(row.get(c)) for c in cols}
        if raw.get(d) != entry:
            entries[d] = entry
    if meta and meta != (raw.get(_STORE_META_DATE) or {}):
        entries[_STORE_META_DATE] = meta
    if entries:
        _kv_upsert(_store_key(kind, code), entries)
        n_new = sum(1 for d in entries if d != _STORE_META_DATE and d not in raw)
        print(f"[蓄積:{kind}] {code}: 新規{n_new}件 / 更新{len(entries)-n_new}件を保存")

def _merge_frames(frames, cols) -> pd.DataFrame:
    """
    複数のDataFrameを日付(_dt)単位で統合する。同じ日付は「後ろのフレームの非欠損値」を優先し、
    後ろ側が欠損(NaN)の列は前のフレームの値を残す。→ 2回目以降も二重取りにならない。
    """
    parts = []
    for f in frames:
        if f is None or f.empty: continue
        g = f.reindex(columns=["_dt"] + cols).copy()
        g["_dt"] = pd.to_datetime(g["_dt"]).dt.normalize()
        g[cols] = g[cols].apply(pd.to_numeric, errors="coerce").astype(float)
        parts.append(g)
    if not parts:
        return pd.DataFrame(columns=["_dt"] + cols)
    df = pd.concat(parts, ignore_index=True)
    return df.groupby("_dt", as_index=False).last().sort_values("_dt").reset_index(drop=True)

def _finalize_margin(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return pd.DataFrame(columns=MARGIN_COLS)
    df = df.sort_values("_dt").reset_index(drop=True)
    df["日付"] = df["_dt"].dt.strftime("%Y/%m/%d")
    # 増減がソースから得られていない行は、直前の記録日との残高差で補う
    df["買い増減"] = df["買い増減"].fillna(df["買い残高"].diff())
    df["売り増減"] = df["売り増減"].fillna(df["売り残高"].diff())
    df["買い残増減率"] = df["買い残高"].pct_change(fill_method=None) * 100
    df["売り残増減率"] = df["売り残高"].pct_change(fill_method=None) * 100
    return df[MARGIN_COLS]

def _finalize_lending(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return pd.DataFrame(columns=LEND_COLS)
    df = df.sort_values("_dt").reset_index(drop=True)
    df["申込日"] = df["_dt"].dt.strftime("%Y/%m/%d")
    df["買い増減"] = df["買い増減"].fillna(df["買い残高"].diff())
    df["売り増減"] = df["売り増減"].fillna(df["売り残高"].diff())
    return df[LEND_COLS]

# ════════════════════════════════════════
# 機関投資家 空売り残高情報 (nikkeiyosoku.com)
# ════════════════════════════════════════
SHORT_COLS = ["_dt","日付","空売り機関","残高比率","増減率差","残高数量","増減量"]
SHORT_TOPN = 3   # ページネーション不可のため取得できる範囲内でトップ3件を強調表示対象とする

def _parse_short_selling_table(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")
    tgt = next((t for t in soup.find_all("table")
                if "空売り機関" in t.get_text() and "増減量" in t.get_text()), None)
    if not tgt:
        return pd.DataFrame(columns=SHORT_COLS)

    rows = tgt.find_all("tr")
    if len(rows) < 2:
        return pd.DataFrame(columns=SHORT_COLS)

    today = datetime.today()
    recs = []
    for tr in rows[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["th","td"])]
        if len(cells) < 6: continue
        m = re.match(r"(\d{1,2})/(\d{1,2})", cells[0].strip())
        if not m: continue
        mo, da = int(m.group(1)), int(m.group(2))
        # 年が明示されないため、未来日になる場合は前年とみなす
        year = today.year
        try:
            dt = datetime(year, mo, da)
            if dt > today:
                dt = datetime(year - 1, mo, da)
        except ValueError:
            continue
        inst = cells[1].strip()
        ratio = _to_float(cells[2].replace("%",""))
        diff  = _to_float(cells[3].replace("%",""))
        qty   = _to_float(cells[4].replace("株","").replace(",",""))
        chg   = _to_float(cells[5].replace(",",""))
        recs.append({"_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
                     "空売り機関": inst, "残高比率": ratio, "増減率差": diff,
                     "残高数量": qty, "増減量": chg})

    if not recs:
        return pd.DataFrame(columns=SHORT_COLS)
    df = pd.DataFrame(recs)
    for c in ["残高比率","増減率差","残高数量","増減量"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("_dt").reset_index(drop=True)


def _parse_shortboard_detailux_table(html: str) -> pd.DataFrame:
    """
    shortboard.detailux.com の銘柄別空売りページをパースする。
    JPX公式データ由来。列見出しは「計算日」「空売り者」「残高数量」「残高割合」で、
    残高割合セル内に "7.49% (+7.49%)" のように前回比が括弧内に埋め込まれる形式。
    ヘッダー行から列インデックスを動的に判定し、サイト側の列順変更にも耐性を持たせる。
    """
    soup = BeautifulSoup(html, "lxml")
    tgt = next((t for t in soup.find_all("table")
                if "空売り者" in t.get_text() and ("残高" in t.get_text())), None)
    if not tgt:
        return pd.DataFrame(columns=SHORT_COLS)

    rows = tgt.find_all("tr")
    if len(rows) < 2:
        return pd.DataFrame(columns=SHORT_COLS)

    header_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th","td"])]
    idx = {"計算日": None, "空売り者": None, "残高数量": None, "残高割合": None}
    for i, txt in enumerate(header_cells):
        if "計算日" in txt or ("日" in txt and idx["計算日"] is None and "率" not in txt and "量" not in txt):
            idx["計算日"] = i
        elif "空売り者" in txt or "機関" in txt:
            idx["空売り者"] = i
        elif "数量" in txt:
            idx["残高数量"] = i
        elif "割合" in txt or "比率" in txt:
            idx["残高割合"] = i
    # ヘッダーが期待通り検出できなければ既定の並び(計算日,空売り者,残高数量,残高割合)にフォールバック
    if any(v is None for v in idx.values()) and len(header_cells) >= 4:
        idx = {"計算日": 0, "空売り者": 1, "残高数量": 2, "残高割合": 3}

    today = datetime.today()
    recs = []
    for tr in rows[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["th","td"])]
        if len(cells) < 4: continue
        try:
            date_txt = cells[idx["計算日"]].strip()
        except (TypeError, IndexError):
            continue
        m = (re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", date_txt)
             or re.match(r"(\d{1,2})/(\d{1,2})$", date_txt))
        if not m: continue
        if len(m.groups()) == 3:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            mo, da = int(m.group(1)), int(m.group(2))
            year = today.year
            dt = datetime(year, mo, da)
            if dt > today: dt = datetime(year - 1, mo, da)

        inst = cells[idx["空売り者"]].strip() if idx["空売り者"] is not None else None
        qty  = _to_float(cells[idx["残高数量"]].replace(",", "")) if idx["残高数量"] is not None else None

        ratio_cell = cells[idx["残高割合"]] if idx["残高割合"] is not None else ""
        rm = re.match(r"([\d.]+)%\s*\(([+\-][\d.]+)%\)", ratio_cell)
        if rm:
            ratio = _to_float(rm.group(1)); diff = _to_float(rm.group(2))
        else:
            ratio = _to_float(ratio_cell.replace("%", "")); diff = None

        if inst is None or qty is None:
            continue
        recs.append({"_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
                     "空売り機関": inst, "残高比率": ratio, "増減率差": diff,
                     "残高数量": qty, "増減量": None})

    if not recs:
        return pd.DataFrame(columns=SHORT_COLS)
    df = pd.DataFrame(recs)
    for c in ["残高比率","増減率差","残高数量"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values(["空売り機関","_dt"]).reset_index(drop=True)
    # 増減量が明示されないサイトのため、機関ごとの残高数量の前回差分から算出する
    df["増減量"] = df.groupby("空売り機関")["残高数量"].diff()
    return df.sort_values("_dt").reset_index(drop=True)


def _fetch_shortboard_detailux(code: str) -> pd.DataFrame:
    """
    JPX公式データを再集計している shortboard.detailux.com のフォールバック取得。
    nikkeiyosoku.com にデータが無い銘柄（対応外・掲載遅延など）の補完用。
    URL規則: https://shortboard.detailux.com/stocks/{証券コード}
    """
    base = re.sub(r"\.[A-Z]+$", "", code)
    url = f"https://shortboard.detailux.com/stocks/{base}"
    html, status = _fetch(url, referer="https://shortboard.detailux.com/")
    if not html or status != 200:
        print(f"[機関空売り(detailux)] {code}: status={status}")
        return pd.DataFrame(columns=SHORT_COLS)
    df = _parse_shortboard_detailux_table(html)
    if df.empty:
        print(f"[機関空売り(detailux)] {code}: データ0件")
    return df


def fetch_institutional_short(code: str) -> dict:
    """
    機関投資家 空売り残高データを取得する。
    優先順: nikkeiyosoku.com → shortboard.detailux.com（JPX公式データ由来のフォールバック）
    「もっと見る」はJS描画のためページネーション不可 → 取得できた範囲内で
    増加トップ{SHORT_TOPN}件・減少トップ{SHORT_TOPN}件を強調表示対象として算出する。

    戻り値:
      raw          : 取得した全行（日付昇順）
      top_increase : 増減量が多い(プラス)上位N件
      top_decrease : 増減量が少ない(マイナス)上位N件
      by_institution: 機関ごとの最新の残高比率で降順ソートした一覧
    """
    base = re.sub(r"\.[A-Z]+$", "", code)
    empty = {"raw": pd.DataFrame(columns=SHORT_COLS),
             "top_increase": pd.DataFrame(columns=SHORT_COLS),
             "top_decrease": pd.DataFrame(columns=SHORT_COLS),
             "by_institution": pd.DataFrame(columns=["空売り機関","残高比率","計算日"])}

    df = pd.DataFrame(columns=SHORT_COLS)
    for path in ("stock_etf", "stock"):
        url = f"https://nikkeiyosoku.com/{path}/short/{base}/"
        html, status = _fetch(url, referer="https://nikkeiyosoku.com/")
        if html and status == 200:
            df = _parse_short_selling_table(html)
            if not df.empty:
                break

    if df.empty:
        print(f"[機関空売り] {code}: nikkeiyosoku 0件 → shortboard.detailux.comで補完")
        df = _fetch_shortboard_detailux(code)

    if df.empty:
        print(f"[機関空売り] {code}: データ0件（両ソースとも失敗）")
        return empty

    top_increase = df[df["増減量"] > 0].nlargest(SHORT_TOPN, "増減量")
    top_decrease = df[df["増減量"] < 0].nsmallest(SHORT_TOPN, "増減量")

    by_inst = (df.sort_values("_dt")
                 .groupby("空売り機関", as_index=False)
                 .last()[["空売り機関","残高比率","日付"]]
                 .rename(columns={"日付":"計算日"})
                 .sort_values("残高比率", ascending=False)
                 .reset_index(drop=True))

    print(f"[機関空売り] {code}: {len(df)}件取得 (増加TOP{len(top_increase)}/減少TOP{len(top_decrease)})")
    return {"raw": df, "top_increase": top_increase, "top_decrease": top_decrease,
            "by_institution": by_inst}


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
    """
    ①貸借取引残高（日証金）。取得したデータは銘柄ごとに蓄積し、2回目以降は
    「保存済み＋今回取得分」を日付単位で統合して返す（同じ日付が二重になることはない）。
    取得元が一時的に失敗しても、蓄積済みのデータは表示できる。
    """
    cols = _STORE_VALUE_COLS["lending"]
    df_r = _fetch_taisyaku(code); time.sleep(1)     # 直近約8営業日（新規/返済/残高）
    df_i = _fetch_irbank_lending(code)               # それより古い分（増減あり）
    stored, meta, raw = store_load("lending", code)

    # 優先順位: 後ろほど優先（taisyaku > IRバンク > 保存済み）。ただし後ろ側が欠損の列は前の値を残す
    merged = _merge_frames([stored, df_i, df_r], cols)
    store_save("lending", code, merged, raw, meta)

    df = _finalize_lending(merged)
    _ds = lambda d: set(d["_dt"]) if ("_dt" in d.columns and not d.empty) else set()
    n_fetch = len(_ds(df_r) | _ds(df_i))
    print(f"[{code}] 貸借: 今回取得{n_fetch}日 / 蓄積{len(df)}日"
          + (f"（{df['_dt'].min():%Y/%m/%d}～{df['_dt'].max():%Y/%m/%d}）" if not df.empty else ""))
    return df

def _detect_margin_col_map(header_cells) -> dict:
    """
    信用残テーブルのヘッダー行テキストから列インデックスを動的に判定する。
    サイト（株たん／Yahoo Finance）によって列の並び順が異なるため、
    固定インデックス決め打ちではなく見出し文字列から都度判定する。
    """
    idx = {"買残高": None, "買増減": None, "売残高": None, "売増減": None, "倍率": None}
    for i, txt in enumerate(header_cells):
        t = txt.replace(" ", "")
        is_buy  = "買" in t
        is_sell = "売" in t
        is_chg  = ("増減" in t) or ("前週比" in t) or ("前日比" in t)
        is_rate = "倍率" in t
        if is_rate and idx["倍率"] is None:
            idx["倍率"] = i
        elif is_buy and is_chg and idx["買増減"] is None:
            idx["買増減"] = i
        elif is_sell and is_chg and idx["売増減"] is None:
            idx["売増減"] = i
        elif is_buy and idx["買残高"] is None:
            idx["買残高"] = i
        elif is_sell and idx["売残高"] is None:
            idx["売残高"] = i
    return idx

def _fetch_shintan_margin(code: str) -> pd.DataFrame:
    """
    株たん (https://kabutan.jp/stock/kabuka?code=XXXX&ashi=shin) の
    「週次信用残時系列データ」ページから信用残データを取得。
    IRバンクで取得できない場合のフォールバック。
    ※ finance?code=XXXX は決算・財務ページであり信用残データは載っていないため誤り。
    """
    # 4桁コードに正規化（1570.T → 1570）
    base = re.sub(r"\.[A-Z]+$", "", code)
    url  = f"https://kabutan.jp/stock/kabuka?code={base}&ashi=shin"
    html, status = _fetch(url, referer="https://kabutan.jp/")
    if not html or status != 200:
        print(f"[株たん] {code}: status={status}")
        return pd.DataFrame(columns=MARGIN_COLS)

    soup = BeautifulSoup(html, "lxml")
    # 信用残テーブルを探す（「買」「売」「残」を含む表。「買い残」「買残」どちらの表記にも対応）
    tgt = next((t for t in soup.find_all("table")
                if "買" in t.get_text() and "売" in t.get_text() and "残" in t.get_text()), None)
    if not tgt:
        print(f"[株たん] {code}: 信用残テーブル未検出")
        return pd.DataFrame(columns=MARGIN_COLS)

    rows = tgt.find_all("tr")
    if not rows:
        print(f"[株たん] {code}: データ0件")
        return pd.DataFrame(columns=MARGIN_COLS)

    # ヘッダー行から列インデックスを動的判定
    header_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th","td"])]
    cmap = _detect_margin_col_map(header_cells)

    recs = []
    for tr in rows[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["th","td"])]
        if len(cells) < 4: continue
        # 日付列を探す（YYYY/MM/DD または MM/DD形式）
        c0 = cells[0].strip(); dt = None
        m = (re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", c0)
             or re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", c0))
        if not m: continue
        try: dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except: continue
        def g(i): return _to_float(cells[i]) if i is not None and len(cells) > i else None
        recs.append({
            "_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
            "買い残高": g(cmap["買残高"]), "買い増減": g(cmap["買増減"]),
            "売り残高": g(cmap["売残高"]), "売り増減": g(cmap["売増減"]),
            "信用倍率": g(cmap["倍率"]),
            "逆日歩":   None,
        })

    if not recs:
        print(f"[株たん] {code}: データ0件")
        return pd.DataFrame(columns=MARGIN_COLS)

    df = pd.DataFrame(recs)
    for c in ["買い残高","買い増減","売り残高","売り増減","信用倍率"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.sort_values("_dt").reset_index(drop=True)
    df["買い残増減率"] = df["買い残高"].pct_change() * 100
    df["売り残増減率"] = df["売り残高"].pct_change() * 100
    print(f"[株たん] {code}: {len(df)}件取得")
    return df

def _parse_nikkeiyosoku_margin_table(html: str) -> pd.DataFrame:
    """
    nikkeiyosoku.com（投資の森）の信用残高ページをパースする。
    列構成: 日付(YYYY/M/D) | 売り残 | 売り増減 | 買い残 | 買い増減 | 信用倍率
    （同サイトの空売りページ用パーサーと違い、日付セルに年が含まれる点に注意）
    """
    soup = BeautifulSoup(html, "lxml")
    tgt = next((t for t in soup.find_all("table")
                if "信用倍率" in t.get_text() and "買い残" in t.get_text() and "売り残" in t.get_text()), None)
    if not tgt:
        return pd.DataFrame(columns=MARGIN_COLS)

    rows = tgt.find_all("tr")
    if len(rows) < 2:
        return pd.DataFrame(columns=MARGIN_COLS)

    recs = []
    for tr in rows[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
        if len(cells) < 6:
            continue
        m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", cells[0].strip())
        if not m:
            continue
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        sb = _to_float(cells[1]); sc = _to_float(cells[2])
        bb = _to_float(cells[3]); bc = _to_float(cells[4])
        rat = _to_float(cells[5])
        recs.append({"_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
                     "買い残高": bb, "買い増減": bc,
                     "売り残高": sb, "売り増減": sc,
                     "信用倍率": rat, "逆日歩": None})

    if not recs:
        return pd.DataFrame(columns=MARGIN_COLS)
    df = pd.DataFrame(recs)
    for c in ["買い残高", "買い増減", "売り残高", "売り増減", "信用倍率"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("_dt").reset_index(drop=True)
    df["買い残増減率"] = df["買い残高"].pct_change() * 100
    df["売り残増減率"] = df["売り残高"].pct_change() * 100
    return df


def _fetch_nikkeiyosoku_margin(code) -> pd.DataFrame:
    """
    nikkeiyosoku.com（投資の森）の信用残高ページを取得する。
    URL: https://nikkeiyosoku.com/stock/margin/{code}/     （個別株）
         https://nikkeiyosoku.com/stock_etf/margin/{code}/ （ETF）
    事前にETF/個別株の判別がつかないため、fetch_institutional_shortと同様に両方試す。
    """
    base = re.sub(r"\.[A-Z]+$", "", code)
    for path in ("stock_etf", "stock"):
        url = f"https://nikkeiyosoku.com/{path}/margin/{base}/"
        html, status = _fetch(url, referer="https://nikkeiyosoku.com/")
        if not html or status != 200:
            continue
        df = _parse_nikkeiyosoku_margin_table(html)
        if not df.empty:
            print(f"[投資の森信用残] {code}({path}): {len(df)}件取得 最新{df['_dt'].max():%Y/%m/%d}")
            return df
    print(f"[投資の森信用残] {code}: 取得失敗")
    return pd.DataFrame(columns=MARGIN_COLS)


def _fetch_irbank_margin(code) -> pd.DataFrame:
    """
    irbank.net の週次信用取引情報ページ（東証発表・信用取引残高）を取得する。
    URL: https://irbank.net/{code}/margin
    ページ形式: 日付 / 買い残高（残高 前週比） / 一般・制度内訳 / 売り残高（残高 前週比） / 一般・制度内訳 / 倍率
    Yahoo Financeの信用残タブは現在クライアント側JS描画のため静的HTML取得では
    テーブルが存在せず取得不可（下記_fetch_yahoo_marginは常に空を返す状態）。
    IRバンクは信用残テーブルがサーバー側で静的HTMLに描画されるため、これを第一取得元とする。
    """
    base = re.sub(r"\.[A-Z]+$", "", code)
    html, status = _fetch(f"https://irbank.net/{base}/margin", referer="https://irbank.net/")
    if not html or status != 200:
        print(f"[IRバンク信用残] {code}: status={status}")
        return pd.DataFrame(columns=MARGIN_COLS)

    rows = _get_rows(html, ["買い残高", "売り残高", "倍率"])
    if not rows:
        print(f"[IRバンク信用残] {code}: テーブル未検出（信用銘柄でない可能性）")
        return pd.DataFrame(columns=MARGIN_COLS)

    def mapper(row, dt):
        bb, bc = _bal_chg(row[1]) if len(row) > 1 else (None, None)
        sb, sc = _bal_chg(row[3]) if len(row) > 3 else (None, None)
        return {"_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
                "買い残高": bb, "買い増減": bc,
                "売り残高": sb, "売り増減": sc,
                "信用倍率": _to_float(row[5]) if len(row) > 5 else None,
                "逆日歩": None}

    recs = _parse_irbank_rows(rows, 6, mapper)
    if not recs:
        print(f"[IRバンク信用残] {code}: データ0件")
        return pd.DataFrame(columns=MARGIN_COLS)

    df = pd.DataFrame(recs)
    for c in ["買い残高", "買い増減", "売り残高", "売り増減", "信用倍率"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.sort_values("_dt").reset_index(drop=True)
    df["買い残増減率"] = df["買い残高"].pct_change() * 100
    df["売り残増減率"] = df["売り残高"].pct_change() * 100
    print(f"[IRバンク信用残] {code}: {len(df)}件取得")
    return df


_YAHOO_MARGIN_DATE_RE = re.compile(r"(\d{2,4})\s*[/年]\s*(\d{1,2})\s*[/月]\s*(\d{1,2})")

def _yahoo_margin_parse_date(text):
    """'2026/9/25' '26/09/25' '2026年9月25日' などをdatetimeに変換。不可ならNone。"""
    m = _YAHOO_MARGIN_DATE_RE.search(str(text))
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    if y < 100:
        y += 2000
    try:
        return datetime(y, mo, d)
    except ValueError:
        return None

def _yahoo_margin_to_number(x):
    """'1,234' '+56' '▲12' '--' '3.45倍' などを数値に。変換不可はNone。"""
    s = str(x).replace(",", "").replace("株", "").replace("倍", "").strip()
    s = s.replace("▲", "-").replace("△", "-").replace("＋", "+").replace("−", "-")
    try:
        return float(s)
    except ValueError:
        return None

def _parse_yahoo_margin_table(html: str) -> pd.DataFrame:
    """
    Yahoo!ファイナンス信用残ページ(styl=margin)を解析する。
    従来はBeautifulSoupで「買」「売」「残」の3文字が揃う表を探していたが、
    現行ページでは見つからず取得不可になっていたため、pandas.read_html で
    ページ内の全tableを読み込み「先頭列の過半数が日付として解釈できる表」を
    対象テーブルとみなす方式に変更（実際に動作確認済みの方式）。
    列構成(先頭6列、実ページで確認済み): 日付 | 売残 | 買残 | 売残増減 | 買残増減 | 信用倍率
    （残高(売残・買残)が先にまとまり、増減(売残増減・買残増減)はその後にまとまる構成。
    　「売残→売残増減→買残→買残増減」という交互構成だと誤認して実装していたため、
    　買い残高に売残増減の値が、売り増減に買残の値が入ってしまうバグがあった）
    """
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return pd.DataFrame(columns=MARGIN_COLS)

    tgt = None
    for df in tables:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = ["".join(dict.fromkeys(map(str, c))) for c in df.columns]
        if len(df) == 0:
            continue
        hits = df.iloc[:, 0].map(lambda v: _yahoo_margin_parse_date(v) is not None).sum()
        if hits >= max(1, len(df) // 2):
            tgt = df
            break
    if tgt is None:
        return pd.DataFrame(columns=MARGIN_COLS)

    tgt = tgt.copy()
    tgt["_dt"] = tgt.iloc[:, 0].map(_yahoo_margin_parse_date)
    tgt = tgt.dropna(subset=["_dt"])
    raw_cols = [c for c in tgt.columns if c != "_dt"]
    if tgt.empty or len(raw_cols) < 6:
        return pd.DataFrame(columns=MARGIN_COLS)

    recs = []
    for _, row in tgt.iterrows():
        dt = row["_dt"]
        recs.append({
            "_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
            "売り残高": _yahoo_margin_to_number(row[raw_cols[1]]),
            "買い残高": _yahoo_margin_to_number(row[raw_cols[2]]),
            "売り増減": _yahoo_margin_to_number(row[raw_cols[3]]),
            "買い増減": _yahoo_margin_to_number(row[raw_cols[4]]),
            "信用倍率": _yahoo_margin_to_number(row[raw_cols[5]]),
            "逆日歩":   None,
        })
    return pd.DataFrame(recs)


_YAHOO_MARGIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# ブラウザのようにCookieを保持しながら同一セッションでリクエストし続けるための
# モジュールレベルのセッション（毎回使い捨てのrequests.get()より実ブラウザに近い挙動）
_yahoo_margin_session = requests.Session()
_yahoo_margin_session.headers.update(_YAHOO_MARGIN_HEADERS)


def _fetch_yahoo_margin_page(ticker: str, page: int) -> pd.DataFrame:
    """
    Yahoo Finance信用残ページの1ページ分を取得してDataFrameで返す（内部用）。

    共通の_fetch()は Referer: https://finance.yahoo.co.jp/ を付与してリクエストするが、
    これは実際のページパスと一致しない汎用的な値であり、この組み合わせだと
    Yahoo側がstatus=500を返すことを確認した（動作確認済みのユーザー提供コードでは
    Refererを一切付けておらず、それで正常に取得できている）。
    そのためYahoo宛だけは_fetch()を経由せず、実ブラウザに近いヘッダー構成・
    Cookie保持セッションで直接リクエストする。
    """
    url = f"https://finance.yahoo.co.jp/quote/{ticker}/history?styl=margin&page={page}"
    try:
        r = _yahoo_margin_session.get(url, timeout=20)
    except Exception as e:
        print(f"[Yahoo margin] {ticker} page={page}: リクエスト例外 {e}")
        return pd.DataFrame(columns=MARGIN_COLS), 0
    if r.status_code != 200:
        return pd.DataFrame(columns=MARGIN_COLS), r.status_code
    return _parse_yahoo_margin_table(r.text), r.status_code


_yahoo_margin_down_until = None  # この時刻までは即スキップする（Noneなら稼働中とみなす）
_YAHOO_MARGIN_COOLDOWN_SEC = 600  # 失敗後10分はスキップし、以降は再度試す

def _fetch_yahoo_margin(code: str, pages: int = 3) -> pd.DataFrame:
    """
    Yahoo Finance Japan の信用残ページからデータ取得。
    URL例: https://finance.yahoo.co.jp/quote/1570.T/history?styl=margin&page=1
           https://finance.yahoo.co.jp/quote/1570.T/history?styl=margin&page=2
           https://finance.yahoo.co.jp/quote/1570.T/history?styl=margin&page=3
    （page= が無いと株価時系列タブにフォールバックされ、信用残テーブルが
      取得できないことがあるため明示的に付与する）
    1ページ目だけでは最新〜数週間分しか載らないため、page=1〜pages(既定3)まで
    取得してマージすることで、より広い期間・より新しい行を確実に拾う。

    【重要】2026年9月末時点、ユーザー環境ではYahoo!ファイナンスへのアクセスが
    ネットワーク(IPアドレス)起因とみられる理由で間欠的にブロックされており、
    ブラウザでの手動閲覧ですら半分程度失敗する状態が確認されている。
    この状態ではリトライしても大半は無駄になるため、一度失敗したら10分間は
    Yahooへのアクセス自体をスキップし、無駄な待機・リトライで処理が重くなるのを
    防ぐ（投資の森・IRバンクなど他ソースの取得は継続する）。Streamlitはプロセスが
    長時間稼働し続けるため、恒久スキップではなく一定時間後に再試行する。
    """
    global _yahoo_margin_down_until
    now = time.time()
    if _yahoo_margin_down_until and now < _yahoo_margin_down_until:
        remain = int(_yahoo_margin_down_until - now)
        print(f"[Yahoo margin] {code}: 直前の失敗によりスキップ（あと約{remain}秒）")
        return pd.DataFrame(columns=MARGIN_COLS)

    base = re.sub(r"\.[A-Z]+$", "", code)
    ticker = code if "." in code else f"{base}.T"
    time.sleep(1.5)  # 銘柄間のアクセス間隔を確保

    frames = []
    for page in range(1, pages + 1):
        df_p, status = _fetch_yahoo_margin_page(ticker, page)
        if df_p.empty and status != 200:
            # .Tなしのコードでも試す（1ページ目失敗時のみ、1回だけ）
            if page == 1:
                time.sleep(1.5)
                df_p, status = _fetch_yahoo_margin_page(base, page)
            if df_p.empty:
                print(f"[Yahoo margin] {code} page={page}: status={status}")
                _yahoo_margin_down_until = time.time() + _YAHOO_MARGIN_COOLDOWN_SEC
                break
        if df_p.empty:
            print(f"[Yahoo margin] {code} page={page}: 0件（打ち切り）")
            break
        frames.append(df_p)
        time.sleep(2.0)  # 連続アクセス間隔を空ける（ユーザー検証コードのSLEEP_SECに合わせる）

    if not frames:
        return pd.DataFrame(columns=MARGIN_COLS)

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates("_dt", keep="first").sort_values("_dt").reset_index(drop=True)
    for c in ["買い残高","買い増減","売り残高","売り増減","信用倍率"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["買い残増減率"] = df["買い残高"].pct_change() * 100
    df["売り残増減率"] = df["売り残高"].pct_change() * 100
    print(f"[Yahoo margin] {code}: {len(df)}件取得（{len(frames)}ページ分）")
    return df


MARGIN_STALE_DAYS = 14  # この日数より最新データが古い場合は「更新停止」とみなす

def _merge_margin(df_base: pd.DataFrame, df_new: pd.DataFrame) -> pd.DataFrame:
    """日付でマージし、重複日は新しく取得した方(df_new)を優先する。"""
    if df_base.empty: return df_new
    if df_new.empty:  return df_base
    merged = pd.concat([df_base, df_new], ignore_index=True)
    merged = merged.drop_duplicates("_dt", keep="last").sort_values("_dt").reset_index(drop=True)
    merged["買い残増減率"] = merged["買い残高"].pct_change() * 100
    merged["売り残増減率"] = merged["売り残高"].pct_change() * 100
    return merged

print("[scraper] 信用残=株探版(日次) を読み込みました")

def _kabutan_margin_parse_date(text):
    """株探の日付表記 '26/10/01'(YY/MM/DD) や '2026/10/01' をdatetimeに変換。不可ならNone。"""
    m = re.search(r"(\d{2,4})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})", str(text))
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    if y < 100:
        y += 2000
    try:
        return datetime(y, mo, d)
    except ValueError:
        return None


def _parse_kabutan_margin_table(html: str) -> pd.DataFrame:
    """
    株探「日次信用残時系列データ」ページ
    (https://kabutan.jp/stock/kabuka?code=XXXX&margin_balances=1&ashi=day)
    の #stock_kabuka_table > table.stock_kabuka_dwm > tbody を解析する。

    列構成(実ページで確認済み):
      0:日付(YY/MM/DD) 1:終値 2:前日比率 3:売り残/株数 4:売り残/金額
      5:買い残/株数 6:買い残/金額 7:信用倍率
    増減列はページに無いため、取得できた行の残高差(前営業日比)を別途計算する。
    """
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("#stock_kabuka_table table.stock_kabuka_dwm tbody tr")
    if not rows:
        # セレクタが外れた場合の保険: 「売り残」「買い残」を含む表を探す
        tgt = next((t for t in soup.find_all("table")
                    if "売り残" in t.get_text() and "買い残" in t.get_text()), None)
        rows = tgt.find_all("tr") if tgt else []

    recs = []
    for tr in rows:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < 8:
            continue
        dt = _kabutan_margin_parse_date(cells[0])
        if dt is None:
            continue
        recs.append({
            "_dt": dt, "日付": dt.strftime("%Y/%m/%d"),
            "売り残高": _to_float(cells[3]),
            "買い残高": _to_float(cells[5]),
            "信用倍率": _to_float(cells[7]),
            "逆日歩":   None,
        })
    return pd.DataFrame(recs)


def _fetch_kabutan_margin(code, pages: int = 3, since=None) -> pd.DataFrame:
    """
    株探の日次信用残(登録・課金不要)を取得する。
    URL: https://kabutan.jp/stock/kabuka?code={code}&margin_balances=1&ashi=day[&page=N]
    1ページ目で足りない過去分は page=2,3... で追加取得する(既定3ページまで)。
    page指定が無視されて同じ内容が返ってきた場合は重複日付で検知して打ち切る。
    since(datetime)を渡すと、そのページ内に since 以前の日付が含まれた時点で
    それ以上古いページは取得しない（蓄積済みの範囲に達したため。通常は1ページで終わる）。
    """
    base = re.sub(r"\.[A-Z]+$", "", code)
    frames, seen = [], set()
    for page in range(1, pages + 1):
        url = f"https://kabutan.jp/stock/kabuka?code={base}&margin_balances=1&ashi=day"
        if page > 1:
            url += f"&page={page}"
        html, status = _fetch(url, referer="https://kabutan.jp/")
        if not html or status != 200:
            print(f"[株探信用残] {code} page={page}: status={status}")
            break
        df_p = _parse_kabutan_margin_table(html)
        if df_p.empty:
            if page == 1:
                _t = re.search(r"<title>(.*?)</title>", html, re.S)
                print(f"[株探信用残] {code}: テーブル未検出"
                      f"（信用銘柄でない／ボット判定でブロックされた可能性。"
                      f"html長={len(html)} title={(_t.group(1).strip()[:40] if _t else '不明')}）")
            break
        new_dates = set(df_p["_dt"]) - seen
        if not new_dates:   # 同じページが返ってきた＝それ以上ページがない
            break
        seen |= new_dates
        frames.append(df_p[df_p["_dt"].isin(new_dates)])
        if since is not None and df_p["_dt"].min() <= since:
            break           # 蓄積済みの日付まで届いた
        time.sleep(1.0)     # 連続アクセスの間隔を空ける

    if not frames:
        return pd.DataFrame(columns=MARGIN_COLS)

    df = pd.concat(frames, ignore_index=True).drop_duplicates("_dt")
    for c in ["買い残高", "売り残高", "信用倍率"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("_dt").reset_index(drop=True)
    # 増減=前営業日との残高差、増減率=前営業日比%（いずれも取得した行同士で計算）
    df["買い増減"] = df["買い残高"].diff()
    df["売り増減"] = df["売り残高"].diff()
    df["買い残増減率"] = df["買い残高"].pct_change(fill_method=None) * 100
    df["売り残増減率"] = df["売り残高"].pct_change(fill_method=None) * 100
    print(f"[株探信用残] {code}: {len(df)}件取得 最新{df['_dt'].max():%Y/%m/%d}")
    return df


def _backfill_margin_history(code) -> pd.DataFrame:
    """
    株探の日次データは2026/9/25以降しか無いため、それより前の週次データを
    投資の森・IRバンク（どちらも静的HTMLで取得可能）から【1回だけ】取り込む。
    Yahoo!ファイナンスはIPブロックの原因になったため使わない。
    戻り値の並びは「古いソース→新しいソース」ではなく、_merge_framesで後ろが優先される
    ことを前提に [IRバンク, 投資の森] の順（投資の森を優先）で返すためのリスト結合用。
    """
    frames = []
    cols = _STORE_VALUE_COLS["margin"]
    for name, fetcher in (("IRバンク", _fetch_irbank_margin),
                          ("投資の森", _fetch_nikkeiyosoku_margin)):
        try:
            d = fetcher(code)
        except Exception as e:
            print(f"[{code}] 過去データ取込({name}): 例外 {e}")
            continue
        if d is not None and not d.empty:
            print(f"[{code}] 過去データ取込({name}): {len(d)}件 {d['_dt'].min():%Y/%m/%d}～{d['_dt'].max():%Y/%m/%d}")
            frames.append(d)
    return _merge_frames(frames, cols)


def fetch_margin(code) -> pd.DataFrame:
    """
    ②信用残（株探の日次データ）。取得したデータは銘柄ごとに蓄積する。

    ・通常: 株探の1ページ目だけ取得し、保存済みデータと日付単位で統合（二重取りなし）。
      保存済みの最新日より新しい分だけが追加される。
    ・初回のみ: 保存データに2026/9/25より前の分が無ければ、投資の森・IRバンクから
      過去の週次データを1度だけ取り込んで復元する（取得済みフラグを保存して再実行しない）。
    ・株探が一時的に失敗しても、蓄積済みデータはそのまま表示できる。
    """
    cols = _STORE_VALUE_COLS["margin"]
    stored, meta, raw = store_load("margin", code)

    # ── 初回のみ: 過去分の復元 ──
    hist = pd.DataFrame(columns=["_dt"] + cols)
    need_backfill = not meta.get("backfilled")
    if need_backfill:
        hist = _backfill_margin_history(code)
        if not hist.empty:
            meta["backfilled"] = datetime.today().strftime("%Y-%m-%d")
        else:
            meta["tries"] = int(meta.get("tries", 0)) + 1
            if meta["tries"] >= 3:           # 3回試して何も無い銘柄(信用取引非対象など)は打ち切る
                meta["backfilled"] = "none"

    # ── 毎回: 株探の日次データ（蓄積済みの最新日に届いたら止める）──
    since = stored["_dt"].max() if (not stored.empty) else None
    try:
        kab = _fetch_kabutan_margin(code, since=since)
    except Exception as e:
        print(f"[{code}] 信用残(株探): 例外発生 {e}")
        kab = pd.DataFrame(columns=MARGIN_COLS)

    # 優先順位: 株探(今回) > 保存済み > 過去取込
    merged = _merge_frames([hist, stored, kab], cols)
    store_save("margin", code, merged, raw, meta)

    df = _finalize_margin(merged)
    if df.empty:
        print(f"[{code}] 信用残: データなし")
    else:
        print(f"[{code}] 信用残: 蓄積{len(df)}日（{df['_dt'].min():%Y/%m/%d}～{df['_dt'].max():%Y/%m/%d}）")
    return df

# ════════════════════════════════════════
# ②-3 制度信用倍率（JPX公式「銘柄別信用取引残高」PDF・日次）
# ════════════════════════════════════════
# 一覧ページ(01.html)に載っている「YYYYMMDD_mtall.pdf」を全部対象にする。
# ・銘柄(code)は呼び出し時に都度指定、日付は「その時点で一覧に表示されている全日付」
# ・取得するのは「株数 Shs.」の行だけ。直下の「金額 Val.」の行の数値は使わない（既存の株数ベースと統一）
# ・取得済みの日付はストア("seido2:コード")に蓄積し、2回目以降は未取得の日付だけPDFを開く
# ・一覧ページから消えた日付も、PDFをローカルに保存しておけば後から別銘柄で再解析できる
#
# 【実PDFで確認したレイアウト（pdfplumberの抽出結果）】
#   1銘柄 = 2行。1行目が「株数 Shs.」行(数値12個)、2行目が「金額 Val.」行。
#   ・株数行: 日本語の銘柄名が折り返してコード・ISINの文字と混ざる。例（1570）:
#       A ＮＥＸＴ ＦＵＮＤＳ 日経平均レバ投レ信ッ等ジ・…連動型上1場57投00信 受JP益30証47券460005株数 Shs. 127,952 …
#     → コード(15700)もISINも日本語の文字の間にバラバラに入る。文字列のままでは検索できない。
#     ただし半角英数字だけを順に拾うと「A」+「15700」+「JP3047460005」が復元できる。
#     銘柄名が特に長いと「株数 Shs.」の目印まで崩れる（例: 15460「…株場数投 信Sh s.受 益証券 256 64 * …」）。
#     → 目印には頼らず、「行末の数値列(12個以上)」で株数行を判定する。
#   ・金額行: 英語の銘柄名の後に「15700 JP3047460005金額 Val.」とコード・ISINがきれいに残ることが多い。
#   ・コードは5桁（4桁+末尾"0"。7974→79740 / 285A→285A0 / 1570→15700）。
#   ※「コード行の次の行を株数行とみなす」方式は、次の銘柄(15710)の株数行を拾う事故の原因だった。
#
# 【銘柄の特定（間違った銘柄を拾わない）】
#   ISIN(12桁・銘柄ごとに一意)を主キーにして、株数行と金額行を結びつける。
#   1. 金額行から「5桁コード＋ISIN＋金額」の連続を抽出（コードは前後が英数字でない独立トークンのみ）
#   2. 株数行は、コード・ISINより左の部分から半角英数字だけを順に拾い、「ISINの直前5文字=コード」を使う
#      （コードは必ず「数字3桁+英数字1桁+0」の形式。形式外は無効。ISINの手前に別の数字が付いていても無視）
#   3. 目的の5桁コード → ISIN → 同じISINの株数行、の順にたどる。
#      株数行側のコードが読めた場合は、目的のコードと一致することも確認する（不一致なら不採用）
#   この「+0」変換は _jpx_code5() の中だけで行い、このセクションの外へは出さない。
#   ストアのキー・DataFrame・fetch_one 等は従来どおり4桁コード(7974 / 285A)のまま。
#
# 【数値の読み取り】
#   株数行の行末の数値列から、半角数字のみ12個（%と*は除外）。並び:
#   売合計,前比, 買合計,前比, 一般売,前比, 制度売,前比, 一般買,前比, 制度買,前比
#   整合性は「一般+制度=合計（売・買）」と「残高が負でない」のみで判定する（前比の整合性は見ない）。
JPX_MARGIN_INDEX_URL = "https://www.jpx.co.jp/markets/statistics-equities/margin/01.html"
JPX_PDF_ARCHIVE = os.environ.get("JPX_PDF_ARCHIVE", "1") != "0"           # "0"でPDFを保存しない
JPX_PDF_DIR     = Path(os.environ.get("JPX_PDF_DIR", "jpx_pdf_archive"))   # PDFの保存先
JPX_PDF_KEEP    = int(os.environ.get("JPX_PDF_KEEP", "60"))                # 保存PDFは新しい順にこの日数分だけ残す
# 解析結果キャッシュ: PDF(約108ページ・解析に約70秒)を1日分につき1回だけ解析し、全銘柄分の結果を保存する。
# 以降は銘柄を追加しても、アプリを再起動しても、PDFを開かずにこのキャッシュから引く。
JPX_PARSED_CACHE = os.environ.get("JPX_PARSED_CACHE", "1") != "0"          # "0"でキャッシュしない
JPX_PARSED_DIR   = Path(os.environ.get("JPX_PARSED_DIR", "jpx_parsed_cache"))
_JPX_PARSER_VER  = 3        # 解析ロジックを変えたら上げる（古いキャッシュは自動で破棄して再解析する）
JPX_DEBUG_DIR   = Path(os.environ.get("JPX_DEBUG_DIR", "jpx_debug"))       # 解析失敗時の行ダンプ先
_JPX_MIN_ROWS   = 100      # 正しく読めた株数行がこれ未満のPDFは、レイアウト変更/抽出失敗とみなす

SEIDO_COLS = ["_dt","日付","制度買い残高","制度買い増減","制度売り残高","制度売り増減",
              "制度信用倍率","一般買い残高","一般売り残高","合計買い残高","合計売り残高","合計信用倍率"]
_JPX_KEYS = ["売合計","売合計前比","買合計","買合計前比",
             "一般売","一般売前比","制度売","制度売前比",
             "一般買","一般買前比","制度買","制度買前比"]
_JPX_ID_RE    = re.compile(r"([0-9]{3}[0-9A-Z]0)([A-Z]{2}[0-9A-Z]{9}[0-9])")   # 5桁コード+ISIN(12桁)
_JPX_ISIN_OK  = re.compile(r"[A-Z]{2}[0-9A-Z]{9}[0-9]")
_JPX_CODE5_OK = re.compile(r"[0-9]{3}[0-9A-Z]0")
_JPX_TAIL_TOK = re.compile(r"▲|△|\*|＊|[0-9][0-9,]*(?:\.[0-9]+)?%?")             # 行末の数値列を構成するトークン
_JPX_NUM_RE   = re.compile(r"[▲△]?\s*[0-9][0-9,]*", re.ASCII)
_jpx_index_cache: dict = {}     # {"YYYYMMDD": 索引}（プロセス内キャッシュ）
_jpx_fail_logged: set = set()   # 失敗ログ・ダンプの重複出力防止


def _jpx_code5(code4: str):
    """
    JPX PDF用の5桁表記に変換する（JPX専用。他のスクレイピング処理では使わないこと）。
    4桁数字(7974) または 3桁数字+英字1文字(285A) 以外はNone（＝JPX対象外として扱う）。
    """
    c = str(code4).upper().strip()
    return c + "0" if re.fullmatch(r"\d{4}|\d{3}[A-Z]", c) else None


_jpx_list_cache: dict = {"t": 0.0, "v": {}}


def _jpx_list_pdfs() -> dict:
    """一覧ページに表示されている {YYYYMMDD: PDFのURL} を新しい順で返す（10分間は結果を使い回す）。"""
    from urllib.parse import urljoin
    if _jpx_list_cache["v"] and time.time() - _jpx_list_cache["t"] < 600:
        return dict(_jpx_list_cache["v"])
    html, status = _fetch(JPX_MARGIN_INDEX_URL, referer="https://www.jpx.co.jp/")
    if not html or status != 200:
        print(f"[JPX信用] 一覧ページ取得失敗 status={status}")
        return dict(_jpx_list_cache["v"])          # 失敗時は前回の結果があればそれを返す
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"(\d{8})_mtall\.pdf$", a["href"])
        if m:
            out[m.group(1)] = urljoin(JPX_MARGIN_INDEX_URL, a["href"])
    out = dict(sorted(out.items(), reverse=True))
    if out:
        _jpx_list_cache.update(t=time.time(), v=out)
    return out


def _jpx_archived_dates() -> list:
    """ローカルに保存済みの日付一覧（解析キャッシュ・PDF）。一覧ページから消えた日付も使うため。"""
    out = set()
    if JPX_PARSED_CACHE and JPX_PARSED_DIR.exists():
        for p in JPX_PARSED_DIR.glob("*.json.gz"):
            m = re.fullmatch(r"(\d{8})\.json\.gz", p.name)
            if m:
                out.add(m.group(1))
    if JPX_PDF_ARCHIVE and JPX_PDF_DIR.exists():
        for p in JPX_PDF_DIR.glob("*_mtall.pdf"):
            m = re.fullmatch(r"(\d{8})_mtall\.pdf", p.name)
            if m:
                out.add(m.group(1))
    return sorted(out, reverse=True)


def _jpx_prune_pdfs():
    """保存PDFが増えすぎないよう、新しい順に JPX_PDF_KEEP 日分だけ残す。"""
    try:
        files = sorted(JPX_PDF_DIR.glob("*_mtall.pdf"), key=lambda p: p.name, reverse=True)
        for p in files[max(JPX_PDF_KEEP, 1):]:
            p.unlink()
    except Exception as e:
        print(f"[JPX信用] 古いPDFの整理に失敗（処理は続行） {e}")


def _jpx_pdf_bytes(date: str, url):
    """PDFのバイト列を返す。保存済みならそれを使い、無ければ取得する。戻り値: (bytes|None, 取得したか)"""
    path = JPX_PDF_DIR / f"{date}_mtall.pdf"
    if JPX_PDF_ARCHIVE and path.exists():
        try:
            return path.read_bytes(), False
        except Exception as e:
            print(f"[JPX信用] {date}: 保存PDF読込失敗 {e}")
    if not url:
        return None, False
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            if r.status_code == 200:
                return r.content, True
            print(f"[JPX信用] {date}: status={r.status_code}")
        except Exception as e:
            print(f"[JPX信用] {date}: 取得例外 {e}")
        time.sleep(1.5 * (attempt + 1))
    return None, False


def _jpx_to_int(tok: str) -> int:
    tok = tok.replace(" ", "")
    neg = tok[:1] in ("▲", "△")
    n = int(tok.lstrip("▲△").replace(",", ""))
    return -n if neg else n


def _jpx_numbers(seg: str) -> list:
    """区間から数値を順に取り出す（半角数字のみ。%と*は除外）。"""
    seg = re.sub(r"[0-9]+(?:\.[0-9]+)?\s*%", " ", seg, flags=re.ASCII)
    seg = seg.replace("*", " ").replace("＊", " ")
    return [_jpx_to_int(m.group(0)) for m in _JPX_NUM_RE.finditer(seg)]


def _jpx_valid(d: dict) -> bool:
    """整合性チェック: 一般+制度=合計（売・買）、残高が負でない。前比の整合性は見ない。"""
    if d["一般売"] + d["制度売"] != d["売合計"]: return False
    if d["一般買"] + d["制度買"] != d["買合計"]: return False
    return all(d[k] >= 0 for k in ("売合計", "買合計", "一般売", "制度売", "一般買", "制度買"))


def _jpx_pick12(vals: list):
    """
    数値列から12個を選んで辞書にする。整合性が取れた最初の組み合わせを採用する。
    1. 先頭から連続12個 → 2. 先頭付近の連続12個 → 3. 先頭16個から順序を保って12個
    """
    n = len(vals)
    if n < 12:
        return None
    for s in range(0, min(n - 12, 4) + 1):
        d = dict(zip(_JPX_KEYS, vals[s:s + 12]))
        if _jpx_valid(d):
            return d
    head = vals[:16]
    if len(head) > 12:
        from itertools import combinations
        for idx in combinations(range(len(head)), 12):
            d = dict(zip(_JPX_KEYS, [head[i] for i in idx]))
            if _jpx_valid(d):
                return d
    return None


def _jpx_ids(prefix: str):
    """
    銘柄名・コード・ISINが混ざった文字列から、半角英数字だけを順に拾って「5桁コード+ISIN」を復元する。
    例: 'A ＮＥＸＴ…上1場57投00信 受JP益30証47券460005' → ('15700', 'JP3047460005')
    ISINの直前5文字がコード（数字3桁+英数字1桁+0）。複数見つかれば末尾側（数値に近い方）を採用。
    """
    asc = re.sub(r"[^0-9A-Za-z]", "", prefix)
    ms = list(_JPX_ID_RE.finditer(asc))
    return (ms[-1].group(1), ms[-1].group(2)) if ms else None


def _jpx_split_tail(line: str):
    """
    行末の数値列(▲・*・%を含む)とそれより左の部分に分ける。数値が12個に満たなければNone。
    「株数 Shs.」の目印が崩れている行でも、行末の数値列で判定できる。
    行末に付く区分文字(K, N など)は読み飛ばす。
    """
    toks = line.split()
    end = len(toks)
    while end > 0 and re.fullmatch(r"[A-Z]", toks[end - 1]):
        end -= 1
    start = end
    while start > 0 and _JPX_TAIL_TOK.fullmatch(toks[start - 1]):
        start -= 1
    tail = " ".join(toks[start:end])
    if len(_jpx_numbers(tail)) < 12:
        return None
    return " ".join(toks[:start]), tail


def _jpx_build_index(lines: list) -> dict:
    """
    PDFの全行から索引を作る（1日分のPDFにつき1回。その後は銘柄を変えても使い回す）。
      shs      : {ISIN: {"code5": 株数行から復元したコード, "row": 数値辞書|None, "line": 行番号}}
      val      : {5桁コード: ISIN}      金額行から復元したもの
      val_line : {5桁コード: 行番号}
      shs_code : {5桁コード: ISIN}      株数行から復元したコード
    行の分類: 「金額」「Val.」を含む行 = 金額行（数値は使わない）。それ以外で行末に数値が12個以上ある行 = 株数行。
    """
    shs, val, val_line, shs_code = {}, {}, {}, {}
    for i, ln in enumerate(lines):
        cut = [p for p in (ln.find("金額"), ln.find("Val.")) if p >= 0]
        if cut:                                         # 金額行 → コードとISINだけ取り出す
            ids = _jpx_ids(ln[:min(cut)])
            if ids and ids[0] not in val:
                val[ids[0]] = ids[1]
                val_line[ids[0]] = i
            continue
        sp = _jpx_split_tail(ln)
        if sp is None:
            continue
        prefix, tail = sp
        ids = _jpx_ids(prefix)
        if not ids:
            continue
        c5, isin = ids
        if isin in shs:                                 # 同じISINが複数ある場合は最初を採用
            continue
        shs[isin] = {"code5": c5, "row": _jpx_pick12(_jpx_numbers(tail)), "line": i}
        shs_code.setdefault(c5, isin)
    # 解析に問題のある銘柄だけ、周辺の行を診断用に残す（全行は保持しない）
    diag = {}
    for c5, isin in val.items():
        e = shs.get(isin)
        if e is None or not e["row"]:
            v = val_line[c5]
            diag[c5] = lines[max(v - 3, 0):v + 2]
    return {"shs": shs, "val": val, "val_line": val_line, "shs_code": shs_code, "diag": diag}


def _jpx_cache_path(date: str) -> Path:
    return JPX_PARSED_DIR / f"{date}.json.gz"


def _jpx_cache_save(date: str, idx: dict):
    """全銘柄分の解析結果を保存する（1日あたり数百KB程度）。"""
    if not JPX_PARSED_CACHE:
        return
    import gzip
    try:
        JPX_PARSED_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "ver": _JPX_PARSER_VER, "date": date,
            "shs": {isin: {"c": e["code5"], "r": [e["row"][k] for k in _JPX_KEYS] if e["row"] else None}
                    for isin, e in idx["shs"].items()},
            "val": idx["val"], "diag": idx["diag"],
        }
        path = _jpx_cache_path(date)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)
    except Exception as e:
        print(f"[JPX信用] {date}: 解析キャッシュの保存に失敗（処理は続行） {e}")


def _jpx_cache_load(date: str):
    """保存済みの解析結果を読み込む。無い・バージョン違い・壊れている場合はNone（→PDFを解析し直す）。"""
    if not JPX_PARSED_CACHE:
        return None
    import gzip
    path = _jpx_cache_path(date)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            p = json.load(f)
        if p.get("ver") != _JPX_PARSER_VER or p.get("date") != date:
            return None
        shs, shs_code = {}, {}
        for isin, e in p["shs"].items():
            row = dict(zip(_JPX_KEYS, e["r"])) if e["r"] else None
            shs[isin] = {"code5": e["c"], "row": row, "line": -1}
            if e["c"]:
                shs_code.setdefault(e["c"], isin)
        return {"shs": shs, "val": p["val"], "val_line": {}, "shs_code": shs_code, "diag": p.get("diag", {})}
    except Exception as e:
        print(f"[JPX信用] {date}: 解析キャッシュの読込に失敗（PDFから解析し直します） {e}")
        return None


def _jpx_index_for(date: str, url):
    """
    指定日の索引（全銘柄分）を返す。メモリ → 解析キャッシュ(ディスク) → PDF解析 の順に探す。
    PDFを解析するのは、その日付で初めて必要になったときの1回だけ。失敗・異常時はNone。
    """
    if date in _jpx_index_cache:
        return _jpx_index_cache[date]
    idx = _jpx_cache_load(date)
    if idx is not None:
        _jpx_index_cache[date] = idx
        return idx
    try:
        import pdfplumber
    except ImportError:
        print("[JPX信用] pdfplumber 未インストール → pip install pdfplumber")
        return None
    import io
    content, downloaded = _jpx_pdf_bytes(date, url)
    if content is None:
        return None
    t0 = time.time()
    lines = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                lines.extend((page.extract_text() or "").splitlines())
                try:
                    page.flush_cache()          # ページごとにメモリを解放
                except Exception:
                    pass
    except Exception as e:
        print(f"[JPX信用] {date}: PDF解析例外 {e}")
        return None

    idx = _jpx_build_index(lines)
    del lines
    n_ok = sum(1 for e in idx["shs"].values() if e["row"])
    print(f"[JPX信用] {date}: PDF解析 {time.time() - t0:.0f}秒 / 株数行{len(idx['shs'])}件（整合OK {n_ok}件）"
          f" / 金額行のコード{len(idx['val'])}件")
    if n_ok < _JPX_MIN_ROWS:
        # レイアウト変更や抽出失敗の疑い。キャッシュも保存もしない
        # （「銘柄が無い」と誤判定されて、その日付が二度と取得されなくなる事故を防ぐ）
        print(f"[JPX信用] {date}: 読めた株数行が{n_ok}件しかありません（PDFのレイアウト変更の可能性）→ 使用しません")
        return None

    if downloaded and JPX_PDF_ARCHIVE:
        try:
            JPX_PDF_DIR.mkdir(parents=True, exist_ok=True)
            tmp = JPX_PDF_DIR / f"{date}_mtall.pdf.tmp"
            tmp.write_bytes(content)
            tmp.replace(JPX_PDF_DIR / f"{date}_mtall.pdf")
            _jpx_prune_pdfs()
        except Exception as e:
            print(f"[JPX信用] {date}: PDF保存失敗（処理は続行） {e}")
    if downloaded:
        time.sleep(1.0)            # 連続アクセスの間隔
    _jpx_cache_save(date, idx)
    _jpx_index_cache[date] = idx
    return idx


def _jpx_lookup(idx: dict, code4: str):
    """
    索引から銘柄を引く。戻り値: (row辞書 or None, 銘柄がPDFに存在したか, 失敗理由)
    目的の5桁コード → ISIN → 同じISINの株数行 の順にたどる。
    株数行側のコードが読めている場合は、目的のコードと一致しなければ不採用にする。
    """
    code5 = _jpx_code5(code4)
    if code5 is None:
        return None, False, ""
    isins = []
    if code5 in idx["val"]:
        isins.append(idx["val"][code5])
    if code5 in idx["shs_code"] and idx["shs_code"][code5] not in isins:
        isins.append(idx["shs_code"][code5])
    if not isins:
        return None, False, ""
    reason = "株数行が見つからない"
    for isin in isins:
        e = idx["shs"].get(isin)
        if e is None:
            continue
        if e["code5"] is not None and e["code5"] != code5:
            reason = f"金額行のコード{code5}と株数行のコード{e['code5']}が不一致(ISIN={isin})"
            continue
        if e["row"]:
            return e["row"], True, ""
        reason = "株数行の数値が整合性チェックに通らない"
    return None, True, reason


def _jpx_dump_failure(date: str, base: str, idx: dict, reason: str):
    """解析に失敗した銘柄の周辺の生テキストをファイルに残す（原因調査用。同じ日付・銘柄は1回だけ）。"""
    key = (date, base)
    if key in _jpx_fail_logged:
        return
    _jpx_fail_logged.add(key)
    code5 = _jpx_code5(base)
    try:
        JPX_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        path = JPX_DEBUG_DIR / f"{date}_{base}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {date} {base}({code5}) 失敗理由: {reason}\n")
            for k, ln in enumerate(idx.get("diag", {}).get(code5, [])):
                f.write(f"{k:3d}: {ln!r}\n")
        print(f"[JPX信用] {base} {date}: {reason} → 周辺の行を {path} に保存しました")
    except Exception as ex:
        print(f"[JPX信用] {base} {date}: {reason}（ダンプ保存にも失敗: {ex}）")


_seido_legacy_purged = False


def _purge_legacy_seido():
    """
    旧版の蓄積キー "seido:*"（株数行の取り違えで誤った値が入っていた可能性がある）を一度だけ削除する。
    現在のキーは "seido2:*" なので影響しない。失敗しても処理は続行する。
    """
    global _seido_legacy_purged
    if _seido_legacy_purged:
        return
    _seido_legacy_purged = True
    try:
        if _USE_SUPABASE:
            r = requests.delete(_sb_url("?code=like.seido:*"), headers=_sb_headers(), timeout=15)
            if r.status_code not in (200, 204):
                print(f"[JPX信用] 旧キー(seido:)の削除は行われませんでした status={r.status_code}（動作には影響しません）")
        else:
            store = _local_store_load()
            old = [k for k in store if k.startswith("seido:")]
            if old:
                for k in old:
                    store.pop(k, None)
                _local_store_save(store)
                print(f"[JPX信用] 旧キー(seido:)を{len(old)}件削除しました")
    except Exception as e:
        print(f"[JPX信用] 旧キー(seido:)の削除に失敗（動作には影響しません） {e}")


def _finalize_seido(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=SEIDO_COLS)
    df = df.sort_values("_dt").reset_index(drop=True)
    df["日付"] = df["_dt"].dt.strftime("%Y/%m/%d")
    return df[SEIDO_COLS]


def fetch_margin_seido(code) -> pd.DataFrame:
    """
    ②-3 制度信用倍率（JPX公式・日次・株数ベース）。
    codeは都度指定（"7974" "1570" "285A" "7974.T" いずれも可。末尾の"0"は付けない）。
    日付は一覧ページに表示されている全日付（＋ローカル保存済みPDFの日付）が対象。
    蓄積済みの日付はPDFを開かないので、2回目以降は新しい日付の分だけ処理する
    （ログの「今回取得0日 / 蓄積N日」は、すべて取得済みでスキップしたという意味でエラーではない）。
    1日分の失敗が他の日付・他の銘柄の取得に影響しないよう、日付ごとに例外を握りつぶして続行する。
    """
    base = re.sub(r"\.[A-Z]+$", "", str(code)).upper().strip()
    if _jpx_code5(base) is None:                     # JPX対象外（投資信託・指数・米国株など）
        return pd.DataFrame(columns=SEIDO_COLS)

    # 蓄積キーは "seido2"。旧版("seido")は株数行の取り違えで誤った値が入っていた可能性があるため引き継がない
    _purge_legacy_seido()
    cols = _STORE_VALUE_COLS["seido2"]
    stored, meta, raw = store_load("seido2", base)
    notfound = set(meta.get("notfound", []))
    have = set(stored["_dt"].dt.strftime("%Y%m%d")) if not stored.empty else set()

    try:
        pdfs = _jpx_list_pdfs()                      # 失敗しても蓄積済みデータは返せる
    except Exception as e:
        print(f"[JPX信用] 一覧ページ処理で例外 {e}")
        pdfs = {}
    for d in _jpx_archived_dates():
        pdfs.setdefault(d, None)                     # 一覧から消えた日付も保存PDFから再解析
    pdfs = dict(sorted(pdfs.items(), reverse=True))

    todo = [d for d in pdfs if d not in have and d not in notfound]   # 新しい順
    rows = []
    for d in todo:
        try:
            idx = _jpx_index_for(d, pdfs[d])
            if idx is None:
                continue
            row, seen, reason = _jpx_lookup(idx, base)
            if row:
                sm, sb = row["制度売"], row["制度買"]
                tm, tb = row["売合計"], row["買合計"]
                rows.append({
                    "_dt": datetime.strptime(d, "%Y%m%d"),
                    "制度買い残高": sb, "制度買い増減": row["制度買前比"],
                    "制度売り残高": sm, "制度売り増減": row["制度売前比"],
                    "制度信用倍率": (sb / sm) if sm else np.nan,
                    "一般買い残高": row["一般買"], "一般売り残高": row["一般売"],
                    "合計買い残高": tb, "合計売り残高": tm,
                    "合計信用倍率": (tb / tm) if tm else np.nan,
                })
            elif not seen:
                notfound.add(d)        # 正常なPDFに銘柄自体が無い（信用取引対象外・未上場日など）
            else:
                _jpx_dump_failure(d, base, idx, reason)
        except Exception as e:
            print(f"[JPX信用] {base} {d}: 想定外の例外 {type(e).__name__}: {e}（この日付はスキップ）")

    new_df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["_dt"] + cols)
    if notfound != set(meta.get("notfound", [])):
        meta["notfound"] = sorted(notfound)

    merged = _merge_frames([stored, new_df], cols)
    try:
        store_save("seido2", base, merged, raw, meta)
    except Exception as e:
        print(f"[JPX信用] {base}: 蓄積ストアへの保存に失敗（今回の結果は表示に使用） {e}")

    df = _finalize_seido(merged)
    print(f"[JPX信用] {base}: 今回取得{len(rows)}日 / 蓄積{len(df)}日"
          + (f"（{df['_dt'].min():%Y/%m/%d}～{df['_dt'].max():%Y/%m/%d}）" if not df.empty else ""))
    return df


def estimate_margin_from_lending(L, M, min_pairs: int = 8) -> dict:
    """
    貸借(日証金・当日夜に判明)から、まだ公表されていない当日の信用残(翌日夕方に判明)を試算する。

    考え方:
      信用の残高変化 ≒ β × 貸借の残高変化（いずれも「株数」の増減同士で比べる）
      βは「同じ日付が両方にある日」の増減ペアから 原点を通る最小二乗法 β=Σxy/Σx² で求める。
      融資/貸株は規模が小さく日々の%変化が暴れるため、%同士ではなく株数の増減で見る。
      売り側（貸株→信用売り残）も買い側（融資→信用買い残）と同じ計算を行う。

    予測値 = 信用の最新確定残高 + β × (貸借の最新残高 − 同じ基準日の貸借残高)
    比率法（参考）= 貸借の最新残高 ÷ 直近の（貸借÷信用）比率  ※規模が小さく暴れやすい

    効果測定:
      過去の各日について「その日より前のデータだけでβを推定→予測→実績と比較」を行い、
      何もしない予測(前回値のまま=変化0)より誤差が小さいかを返す（improve>0なら貸借に予測力あり）。
    戻り値 status: ok / no_target(貸借に新しい日が無い) / no_base(基準日の貸借が無い) / no_data
    """
    out = {"status": "no_data", "sides": {}}
    if L is None or M is None or len(L) == 0 or len(M) == 0:
        return out

    def prep(df):
        g = df[["_dt", "買い残高", "売り残高"]].copy()
        g["_dt"] = pd.to_datetime(g["_dt"]).dt.normalize()
        return (g.dropna(subset=["_dt"]).drop_duplicates("_dt", keep="last")
                 .set_index("_dt").sort_index())
    l, m = prep(L), prep(M)
    t_L, t_M = l.index.max(), m.index.max()
    out.update(target_date=t_L, base_date=t_M)
    if t_L <= t_M:
        out["status"] = "no_target"; return out
    if t_M not in l.index:
        out["status"] = "no_base"; return out
    out["status"] = "ok"

    common = l.index.intersection(m.index)
    for side, col in (("買い", "買い残高"), ("売り", "売り残高")):
        lc = l.loc[common, col]; mc = m.loc[common, col]
        ok = (lc.notna() & mc.notna()).values
        lc, mc = lc[ok], mc[ok]
        x = lc.diff().dropna().values.astype(float)        # 貸借の増減(株)
        y = mc.diff().dropna().values.astype(float)        # 信用の増減(株)
        n = len(x)
        info = {"n": n, "min_pairs": min_pairs}
        base_m = m.loc[t_M, col]
        dl = l.loc[t_L, col] - l.loc[t_M, col]
        if n < min_pairs or pd.isna(base_m) or pd.isna(dl) or float((x ** 2).sum()) == 0:
            out["sides"][side] = info; continue

        beta = float((x * y).sum() / (x ** 2).sum())
        r = float(np.corrcoef(x, y)[0, 1]) if (n >= 3 and x.std() > 0 and y.std() > 0) else float("nan")
        info.update(beta=beta, r=r, base=float(base_m), delta_l=float(dl),
                    pred=float(max(base_m + beta * dl, 0.0)))

        # 比率法（参考）: 直近20日の「貸借÷信用」を合計同士で比べる
        tail = lc.index[-20:]
        ratio = lc.loc[tail].sum() / mc.loc[tail].sum() if mc.loc[tail].sum() else float("nan")
        info["ratio"] = float(ratio) if pd.notna(ratio) else float("nan")
        info["pred_ratio"] = float(l.loc[t_L, col] / ratio) if (pd.notna(ratio) and ratio > 0) else float("nan")

        # 効果測定（1日先予測のバックテスト）
        mend = mc.values[1:].astype(float)                 # 各ペア終点の信用残高
        e_model, e_naive, pct_model, pct_naive = [], [], [], []
        for i in range(min_pairs, n):
            sx2 = float((x[:i] ** 2).sum())
            if sx2 == 0: continue
            b_i = float((x[:i] * y[:i]).sum() / sx2)
            em, en = abs(b_i * x[i] - y[i]), abs(y[i])
            e_model.append(em); e_naive.append(en)
            if mend[i] > 0:
                pct_model.append(em / mend[i] * 100); pct_naive.append(en / mend[i] * 100)
        if len(e_model) >= 3 and sum(e_naive) > 0:
            info["bt"] = {"n": len(e_model),
                          "mape_model": float(np.mean(pct_model)) if pct_model else float("nan"),
                          "mape_naive": float(np.mean(pct_naive)) if pct_naive else float("nan"),
                          "improve": float(1 - sum(e_model) / sum(e_naive))}
        else:
            info["bt"] = None
        out["sides"][side] = info
    return out

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
        # reset_index前のインデックス名を保持（Date/Datetime以外の場合に備える）
        idx_name = raw.index.name or "Date"
        raw = raw.reset_index()
        # 日付列を特定（インデックス由来の列名、またはDate/Datetime）
        date_col = idx_name if idx_name in raw.columns else (
            "Date" if "Date" in raw.columns else (
            "Datetime" if "Datetime" in raw.columns else raw.columns[0]))
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
def _parse_history_table(html: str):
    """履歴テーブル1ページ分をパースして (is_fund, records) を返す"""
    soup = BeautifulSoup(html, "lxml")
    tgt = next((t for t in soup.find_all("table")
                if "基準価額" in t.get_text()
                or ("終値" in t.get_text() and "始値" in t.get_text())), None)
    if not tgt:
        return None, []
    headers = [th.get_text(strip=True) for th in tgt.find_all("th")]
    is_fund = "基準価額" in headers and "始値" not in headers
    recs = []
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
            recs.append({"_dt": dt, "始値": None, "高値": None, "安値": None,
                        "終値": g(1), "前日差": g(2), "出来高": None,
                        "基準価額": g(1), "純資産(百万)": g(3)})
        else:
            recs.append({"_dt": dt, "始値": g(1), "高値": g(2), "安値": g(3),
                        "終値": g(4), "前日差": None, "出来高": g(5),
                        "基準価額": None})
    return is_fund, recs

def _yahoo_scrape_history(ticker_full: str, years: int = 10, styl_stock: bool = None) -> pd.DataFrame:
    """
    Yahoo Finance Japan の履歴ページを from/to 範囲指定でスクレイピングし、
    1年ごとに区切って遡りながら全期間を結合する。

    URL形式:
      個別株/ETF : .../history?styl=stock&from=YYYYMMDD&to=YYYYMMDD&timeFrame=d&page=N
      日本指数   : .../history?from=YYYYMMDD&to=YYYYMMDD&timeFrame=d&page=N (styl=stockなし)
      投資信託   : .../history?styl=stock&from=...&to=...&timeFrame=d&page=N
                   ただし from/to が効かない場合は ?page=N のみのレガシー形式にフォールバック

    styl_stock: True→styl=stock付与 / False→付与しない / None→自動判定(_code_typeから)
    """
    if styl_stock is None:
        ct = _code_type(ticker_full)
        styl_stock = ct == "stock_jp"  # 投資信託・指数はstyl=stock無しの方が安定（500エラー回避）

    base_url = f"https://finance.yahoo.co.jp/quote/{ticker_full}/history"
    today = datetime.today()
    all_recs = []

    for y in range(years):
        to_dt   = today - timedelta(days=365*y)
        from_dt = to_dt - timedelta(days=365)
        to_s    = to_dt.strftime("%Y%m%d")
        from_s  = from_dt.strftime("%Y%m%d")

        year_recs = []
        for page in range(1, 11):  # 1年最大10ページ(約250営業日)あれば十分
            params = f"from={from_s}&to={to_s}&timeFrame=d&page={page}"
            if styl_stock: params = "styl=stock&" + params
            url = f"{base_url}?{params}"
            html, status = _fetch(url, referer="https://finance.yahoo.co.jp/")
            if not html or status != 200:
                if page == 1:
                    print(f"[Yahoo scrape] {ticker_full} {y}年目 status={status} (from/to方式失敗)")
                break
            is_fund, recs = _parse_history_table(html)
            if is_fund is None or not recs:
                if page == 1:
                    print(f"[Yahoo scrape] {ticker_full} {y}年目 テーブル無し/0件 (from/to方式)")
                break
            year_recs.extend(recs)
            time.sleep(0.4)
            if len(recs) < 20:  # ページ末尾（1ページ20件未満なら終端）
                break

        if not year_recs:
            # from/to が効かない銘柄（投資信託等）はレガシー ?page=N 形式にフォールバック
            if y == 0 and not all_recs:
                print(f"[Yahoo scrape] {ticker_full}: from/to方式失敗 → レガシー?page=N方式へ")
                for page in range(1, 21):
                    url = base_url if page == 1 else f"{base_url}?page={page}"
                    html, status = _fetch(url, referer="https://finance.yahoo.co.jp/")
                    if not html or status != 200:
                        print(f"[Yahoo scrape] {ticker_full} legacy page={page} status={status}")
                        break
                    is_fund, recs = _parse_history_table(html)
                    if is_fund is None or not recs:
                        if page == 1:
                            print(f"[Yahoo scrape] {ticker_full} legacy: テーブル未検出")
                        break
                    all_recs.extend(recs)
                    time.sleep(0.4)
                    if len(recs) < 20: break
            break  # year_recsが空＝それ以前のデータなし→打ち切り

        all_recs.extend(year_recs)
        # 直近年で取得件数が極端に少ない(上場間もない等)場合はそこで終了
        if len(year_recs) < 5 and y > 0:
            break

    if not all_recs:
        print(f"[Yahoo scrape] {ticker_full}: 全方式で0件")
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
      4. JSONキャッシュとマージ（上記すべてで長期データが取れない投資信託等向け）
         取得できた直近データをキャッシュに追記し、次回以降は過去分として積み上がる。

    days=0: 全期間 / days>0: 直近N日
    """
    # 裸の日本株コード(例:"2869")を "2869.T" に正規化してから判定・取得する
    code = _normalize_jp_code(code)

    df = pd.DataFrame()

    # 1. yfinance
    yf_tickers = _to_yf_ticker(code)
    for ticker in yf_tickers:
        df = _fetch_yfinance(ticker, period="max")
        if not df.empty: break

    # 2. Yahoo Finance スクレイピング（投資信託・日本指数・yfinance失敗銘柄すべて対象）
    if df.empty:
        df = _yahoo_scrape_history(code, years=10)

    # 3. kabutan（米国指数フォールバック）
    if df.empty:
        slug = _KABUTAN_MAP.get(code.lstrip("^"))
        if slug:
            df = _kabutan_history(slug, days=0)

    # 4. 上記すべて失敗、または取得できた期間が短い場合はキャッシュを併用
    #    （投資信託は1ページ目=直近20日分しか取れないため、これが主経路になる）
    cached = cache_load_df(code)
    if not cached.empty:
        if df.empty:
            df = cached
            print(f"[キャッシュ利用] {code}: {len(df)}件（長期スクレイピング不可のため自前蓄積データを使用）")
        else:
            # 新規取得分とキャッシュを日付マージ（新規取得分を優先）
            merged = pd.concat([cached, df], ignore_index=True)
            df = merged.drop_duplicates("_dt", keep="last").sort_values("_dt").reset_index(drop=True)
            df = _finalize_price_df(df)
            print(f"[キャッシュ統合] {code}: キャッシュ{len(cached)}件 + 新規{len(df)-len(cached) if len(df)>=len(cached) else 0}件 → 計{len(df)}件")

    # 新規取得できたデータ（短期でも）は必ずキャッシュに追記しておく
    # → 次回以降、取得のたびに過去分が積み上がっていく
    if not df.empty:
        ct = _code_type(code)
        if ct in ("fund", "index_jp"):  # 長期スクレイピングが不安定な種別のみキャッシュ運用
            cache_append(code, df)

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
    ct = _code_type(base)
    if ct == "fund":
        suffixes = [""]               # 投資信託: コードそのまま、.T等は付けない
    elif ct == "index_jp":
        suffixes = [""]               # 既に998xxx.T/.O形式
    elif re.fullmatch(r"\d{4}|\d{3}[A-Z]", base):
        suffixes = [".T"]             # 裸の日本株/ETFコード（新形式3桁+英字も含む）
    elif re.fullmatch(r"\d{6}", base):
        suffixes = [".T",".O",".N",".L"]
    else:
        suffixes = [""]               # 米国株/指数等はそのまま

    for sfx in suffixes:
        df = _yahoo_scrape_history(f"{base}{sfx}", years=1)
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
        # fetch_price_long が yfinance→スクレイピング→kabutanを内部で順に試すため、
        # ここで _yahoo_scrape_history を再度呼ぶと完全な重複リクエストになり
        # サーバーへの負荷増加・レート制限の誘発につながっていた。重複削除。
        df = fetch_price_long(ticker, days=days)
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
def calc_technicals(df: pd.DataFrame, warmup: int = 250) -> pd.DataFrame:
    """
    昇順ソート済み DataFrame にテクニカル指標を追加。

    warmup=250（約1年）:
      RSI・MACD・ストキャス等はウォームアップ期間として直前250日のデータを
      使って計算を安定させてから表示期間に渡す。
      これにより Yahoo Finance 等の「1年ベースの指標」と近い値になる。
      移動平均（MA25/75/200）とBBは全期間データで計算（長期トレンド把握のため）。

    世間一般の標準:
      RSI(14) / MACD(12,26,9) / ストキャス(14,3) は直近1〜2年で計算するのが一般的。
      本ツールでは250日（約1年）のウォームアップを使用。
    """
    if df.empty or "終値" not in df.columns: return df
    df = df.sort_values("_dt").reset_index(drop=True)
    c = df["終値"].astype(float)
    h = df["高値"].astype(float) if "高値" in df.columns and df["高値"].notna().any() else c
    l = df["安値"].astype(float) if "安値" in df.columns and df["安値"].notna().any() else c

    # ── 移動平均・BB（全期間で計算・長期トレンド把握用）──
    df["MA5"]   = c.rolling(5,   min_periods=1).mean().round(2)
    df["MA25"]  = c.rolling(25,  min_periods=1).mean().round(2)
    df["MA75"]  = c.rolling(75,  min_periods=1).mean().round(2)
    df["MA200"] = c.rolling(200, min_periods=1).mean().round(2)

    std25 = c.rolling(25, min_periods=1).std()
    df["BB_upper"] = (df["MA25"] + 2*std25).round(2)
    df["BB_lower"] = (df["MA25"] - 2*std25).round(2)
    df["BB_%B"]    = ((c - df["BB_lower"]) / (df["BB_upper"] - df["BB_lower"])).round(3)

    # ── パラボリック SAR（全期間：前の状態が必要なため全体で計算）──
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

    # ══ 以下はウォームアップ250日込みで計算し、世間標準に合わせる ══
    # ウォームアップ期間のデータを先頭に持つことで「直近1年から計算した値」を実現。
    # min_periods を使わず ewm の adjust=False で指数平滑を正確に計算する。

    # ── RSI (14) ──
    d    = c.diff()
    gain = d.clip(lower=0).ewm(com=13, adjust=False).mean()   # Wilder平滑（EMA方式）
    loss = (-d.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rs   = gain / loss.replace(0, np.nan)
    df["RSI"] = (100 - 100/(1+rs)).round(2)

    # ── ストキャスティクス (14,3,3) ──
    low14  = l.rolling(14, min_periods=14).min()
    high14 = h.rolling(14, min_periods=14).max()
    kr     = 100*(c - low14) / (high14 - low14)
    df["FastK"] = kr.round(2)
    df["FastD"] = kr.rolling(3, min_periods=1).mean().round(2)
    df["SlowK"] = df["FastD"]
    df["SlowD"] = df["SlowK"].rolling(3, min_periods=1).mean().round(2)

    # ── MACD (12,26,9) ──
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["MACD"]        = (ema12 - ema26).round(2)
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean().round(2)
    df["MACD_hist"]   = (df["MACD"] - df["MACD_signal"]).round(2)

    # ── DMI / ADX (14) ──
    tr   = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    pdm  = (h-h.shift()).clip(lower=0)
    ndm  = (l.shift()-l).clip(lower=0)
    atr14= tr.ewm(com=13, adjust=False).mean()   # Wilder平滑
    pdi  = 100 * pdm.ewm(com=13, adjust=False).mean() / atr14
    mdi  = 100 * ndm.ewm(com=13, adjust=False).mean() / atr14
    df["DI_plus"]  = pdi.round(2)
    df["DI_minus"] = mdi.round(2)
    dx   = (pdi - mdi).abs() / (pdi + mdi) * 100
    df["ADX"] = dx.ewm(com=13, adjust=False).mean().round(2)

    # ── モメンタム / ROC ──
    df["Momentum"] = (c - c.shift(10)).round(2)
    df["ROC"]      = ((c - c.shift(10)) / c.shift(10) * 100).round(3)

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
    # 短期データも取りこぼし防止のためキャッシュに追記（投資信託・日本指数のみ）
    if not p.empty and _code_type(_normalize_jp_code(code)) in ("fund", "index_jp"):
        cache_append(_normalize_jp_code(code), p)
    p_long=fetch_price_long(code, days=0)
    # 機関投資家 空売り情報（nikkeiyosoku.comは日本株/ETFのみ対応。指数・投資信託・米国株は対象外）
    if _code_type(_normalize_jp_code(code)) == "stock_jp":
        short_inst = fetch_institutional_short(code)
    else:
        short_inst = None
    # 制度信用倍率（JPX公式PDF・日次）。日本株/ETFのみ。失敗しても他の取得に影響させない
    try:
        seido = fetch_margin_seido(code) if _code_type(_normalize_jp_code(code)) == "stock_jp" else pd.DataFrame(columns=SEIDO_COLS)
    except Exception as e:
        print(f"[{code}] 制度信用(JPX): 例外 {e}")
        seido = pd.DataFrame(columns=SEIDO_COLS)
    return {"name":name,"lending":l,"price":p,"price_long":p_long,
            "margin":m,"margin_seido":seido,"pressure":judge_pressure(l,p),"short_inst":short_inst}
