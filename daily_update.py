"""
毎朝の自動更新（GitHub Actionsから実行）。PC・iPhoneが off でも Supabase を最新にする。
  1) 市場全体: VIX・信用(週次/日次JPX)・空売り比率 → market.update_all()
  2) 個別株: watchlist.txt の銘柄を scraper.fetch_one()（貸借・信用・制度信用をSupabaseへ蓄積）
失敗した項目があっても残りは続行し、最後に結果を表示する（終了コードは常に0=他の処理を止めない）。
"""
import json, os, re, sys, time
from pathlib import Path

import market as M
import scraper as S

print("Supabase使用:", getattr(S, "_USE_SUPABASE", False))
if not getattr(S, "_USE_SUPABASE", False):
    print("SUPABASE_URL / SUPABASE_KEY が未設定です。GitHubのSecretsを確認してください。")
    sys.exit(1)

# ── 1) 市場全体 ──
try:
    msgs = M.update_all()
    print("市場更新メッセージ:", msgs or "なし")
except Exception as e:
    print("市場更新で例外:", e)

# ── 2) 個別株（watchlist.txt ＋ persist_state.json の watch_codes） ──
codes = []
p = Path("watchlist.txt")
if p.exists():
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.split("#")[0].strip()
        codes += re.findall(r"[0-9]{3}[0-9A-Za-z]|[0-9]{4}", ln)
ps = Path("persist_state.json")
if ps.exists():
    try:
        codes += list(json.loads(ps.read_text(encoding="utf-8")).get("watch_codes", []))
    except Exception:
        pass
codes = list(dict.fromkeys(str(c).upper() for c in codes))
print("個別株:", codes or "なし")

ng = []
for c in codes:
    try:
        S.fetch_one(c)
    except Exception as e:
        ng.append(f"{c}: {e}")
        print(f"[{c}] 失敗: {e}")
    time.sleep(1)
print("完了。失敗:", ng or "なし")
