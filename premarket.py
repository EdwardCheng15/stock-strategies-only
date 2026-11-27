"""盤前「今日最終清單」（兩段式推播第二段，每個交易日 08:00 前後執行）

流程：
  1. 讀 Signals 分頁最新一批盤後候選
  2. 抓昨晚台指期夜盤、美股隔夜收盤
  3. 依夜盤與美股風險確認 BUY（大跌/強警示 → 暫緩；警示 → 收緊追價上限），
     並用連動美股、費半/那指、EWT 推估每檔今日方向（強逆風 → 該檔暫緩）
  4. 依目前庫存與 TOTAL_CAPITAL 分配建議部位
  5. 推三則：今日最終清單 → 美股供應鏈連動 → 持倉健檢與資金水位

執行: uv run python premarket.py
"""

import os
import sys
import time
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from stock_strategies.night_session import get_night_session
from stock_strategies.sheet import read_latest_signals
from stock_strategies.notify import send_telegram
from stock_strategies.us_market import fetch_us_stats, get_us_risk, get_us_market_summary, market_open_outlook
from stock_strategies.premarket_confirm import (
    latest_batch,
    staleness_note,
    confirm_candidates,
    format_final_list,
    _num,
)


REQUIRED_ENV = [
    "FINMIND_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDS_JSON",
]


def _allocate(buys: list[dict]) -> None:
    """依目前庫存（投入成本）與 TOTAL_CAPITAL 計算剩餘可配置部位。"""
    try:
        from stock_strategies.portfolio_guard import load_positions, allocate_buy_signals
        for b in buys:
            b["position_size_pct"] = _num(b.get("position_pct"), 15.0)
        cap = float(os.environ.get("TOTAL_CAPITAL", "200000") or 200000)
        # Signals 分頁沒有存策略類型，題材集中度上限在盤後已檢查過，這裡只控總曝險
        allocate_buy_signals(buys, load_positions(), total_capital=cap,
                             max_total_exposure_pct=70.0, max_archetype_exposure_pct=100.0)
    except Exception as e:
        print(f"⚠️ 部位分配略過: {e}", file=sys.stderr)


def main():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"❌ 缺少環境變數: {missing}", file=sys.stderr)
        sys.exit(1)

    print(f"[{datetime.now()}] 取得台指期夜盤...")
    night = get_night_session()
    print(f"  → {night['date']} 夜盤 {night['pct']:+.2f}% ({night['label']})" if night else "  → 夜盤資料暫時取不到")

    print("讀取盤後候選...")
    try:
        signals = read_latest_signals(limit=300)
    except Exception as e:
        print(f"⚠️ 讀取訊號失敗: {e}", file=sys.stderr)
        signals = []
    batch_day, batch = latest_batch(signals)
    print(f"  → 最新候選日 {batch_day or '無'}，{len(batch)} 筆")

    print("取得美股隔夜表現...")
    us_stats, us_err = fetch_us_stats()
    if us_err:
        print(f"  → {us_err}")
    us_risk = get_us_risk(us_stats)
    print(f"  → 美股風險等級 {us_risk['level']}")

    confirmed = confirm_candidates(batch, night, us_risk, us_stats)
    open_outlook = market_open_outlook(us_stats) if us_stats else None
    _allocate(confirmed["buys"])
    print(f"  → 可執行 BUY {len(confirmed['buys'])}，暫緩 {len(confirmed['downgraded'])}")

    print("發送 Telegram：今日最終清單...")
    send_telegram(format_final_list(confirmed, batch_day, night, us_risk, staleness_note(batch_day),
                                    open_outlook=open_outlook))
    time.sleep(0.5)

    print("發送 Telegram：美股供應鏈連動...")
    send_telegram(get_us_market_summary(signals=batch, threshold=2.0, stats=us_stats).lstrip("\n"))
    time.sleep(0.5)

    # 持倉移動停利健檢與資金水位（save=True 以更新波段最高價）
    try:
        from stock_strategies.portfolio_guard import load_positions
        from stock_strategies.vision_audit import audit_positions
        cur_pos = load_positions()
        if cur_pos:
            print("發送 Telegram：持倉健檢...")
            send_telegram(audit_positions(cur_pos, save=True))
    except Exception as e:
        print(f"⚠️ 持倉健檢推播失敗: {e}", file=sys.stderr)

    print("✅ 完成")


if __name__ == "__main__":
    main()
