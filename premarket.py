"""夜盤與美股連動盤前快報"""

import os
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from stock_strategies.night_session import get_night_session
from stock_strategies.sheet import read_latest_signals
from stock_strategies.notify import send_telegram, format_premarket
from stock_strategies.us_market import get_us_market_summary


REQUIRED_ENV = [
    "FINMIND_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDS_JSON",
]


def main():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"❌ 缺少環境變數: {missing}", file=sys.stderr)
        sys.exit(1)

    print(f"[{datetime.now()}] 取得台指期夜盤...")
    night = get_night_session()
    if night:
        print(f"  → {night['date']} 夜盤 {night['pct']:+.2f}% ({night['label']})")
    else:
        print("  → 夜盤資料暫時取不到")

    print("讀取昨日訊號...")
    try:
        signals = read_latest_signals(limit=300)
    except Exception as e:
        print(f"⚠️ 讀取訊號失敗: {e}", file=sys.stderr)
        signals = []
    print(f"  → {len(signals)} 筆")

    print("取得美股隔夜表現與供應鏈連動...")
    us_summary = get_us_market_summary(signals=signals, threshold=2.0)

    print("發送 Telegram 盤前快報...")
    full_message = format_premarket(night, signals) + us_summary
    send_telegram(full_message)
    print("✅ 完成")


if __name__ == "__main__":
    main()
