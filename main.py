"""
V3.2 每日選股訊號系統

新增：
- 回測以「訊號日隔天開盤價」為進場（符合真實可執行）
- 大盤濾鏡：加權指數跌破月線時，BUY 自動降為 WATCH
- 夜盤情緒濾鏡：昨晚台指期夜盤大跌時，BUY 進一步降為 WATCH（風控）
- 成績單：自動追蹤每個 BUY 在 T+1/T+5/T+10/T+20 的實際表現

執行: uv run python main.py
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

from stock_strategies.sheet import (
    read_watchlist,
    append_signals,
    read_performance,
    write_performance,
)
from stock_strategies.evaluate import evaluate
from stock_strategies.notify import send_telegram, format_messages
from stock_strategies.market import get_market_state, apply_market_filter
from stock_strategies.night_session import (
    get_night_session,
    apply_night_filter,
    night_filter_note,
)
from stock_strategies.performance import update_performance, summary as perf_summary
from stock_strategies.market_scanner import format_market_breakout_top5, scan_dynamic_candidates


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

    # 1. 讀取 watchlist
    print(f"[{datetime.now()}] 讀取 watchlist...")
    watchlist = read_watchlist()
    print(f"  → {len(watchlist)} 檔啟用中")

    # 2. 取得大盤狀態（濾鏡）
    print("取得大盤狀態...")
    market = get_market_state()
    print(f"  → {market['note']}")

    # 2b. 取得昨晚夜盤（情緒風控濾鏡）
    print("取得昨晚夜盤...")
    night = get_night_session()
    night_note = night_filter_note(night)
    print(f"  → {night_note}")

    # 2c. 併入全市場動能突圍前 15 名（確保包含完整 TOP 5 榜單）進入正式評分池
    watch_codes = {str(w["stock_id"]).strip() for w in watchlist}
    dynamic_pool = []
    try:
        from stock_strategies.market_scanner import scan_market_breakouts
        cands = scan_market_breakouts(top_n=20)
        if not cands:
            cands = scan_dynamic_candidates(min_volume=1500, min_pct=2.0, max_pct=8.5, top_n=20)
        for c in cands:
            code_str = str(c.get("code", "")).strip()
            if code_str and code_str not in watch_codes and len(dynamic_pool) < 15:
                dynamic_pool.append({
                    "stock_id": code_str,
                    "name": f"{c.get('name', '')}[全市場突圍]",
                    "strategy": "default",
                })
        print(f"🔥 全市場動能候選池：額外納入 {len(dynamic_pool)} 檔強勢突圍股進入精算")
    except Exception as e:
        print(f"⚠️ 全市場候選池取得失敗: {e}")

    combined_list = list(watchlist) + dynamic_pool

    # 3. 個股評分
    results = []
    for i, row in enumerate(combined_list, 1):
        sid = str(row["stock_id"])
        name = row.get("name", "")
        print(f"[{i}/{len(combined_list)}] {sid} {name}")
        r = evaluate(sid, name)
        if r:
            results.append(r)
        time.sleep(0.6)

    # 4. 套用大盤濾鏡：跌破月線時 BUY 一律降為 WATCH
    downgraded = apply_market_filter(results, market)
    if downgraded:
        print(f"⚠️ 大盤跌破月線，{downgraded} 檔 BUY 已自動降為 WATCH")

    # 4b. 套用夜盤情緒濾鏡：昨晚夜盤大跌時 BUY 進一步降為 WATCH
    night_downgraded = apply_night_filter(results, night)
    if night_downgraded:
        print(f"🌙 昨晚夜盤大跌，{night_downgraded} 檔 BUY 已自動降為 WATCH")

    order = {"BUY": 0, "WATCH": 1, "SKIP": 2, "ERROR": 3}
    results.sort(key=lambda x: (order.get(x.get("action"), 4), -x.get("signal_score", 0)))

    # 4c. 組合集中度與總曝險控管（大盤站穩月線上限 70%，跌破月線自動降至 40%）
    try:
        from stock_strategies.portfolio_guard import load_positions, allocate_buy_signals
        cur_pos = load_positions()
        max_exp = 70.0 if market.get("above_ma20", True) else 40.0
        buys = [r for r in results if r.get("action") == "BUY"]
        allocate_buy_signals(buys, cur_pos, max_total_exposure_pct=max_exp)
    except Exception as e:
        print(f"⚠️ 組合資金分配略過: {e}", file=sys.stderr)

    buy_count = sum(1 for r in results if r["action"] == "BUY")
    watch_count = sum(1 for r in results if r["action"] == "WATCH")
    print(f"\n{buy_count} BUY, {watch_count} WATCH")

    # 5. 寫回 Signals 分頁
    print("寫回 Google Sheet (Signals)...")
    append_signals(results)

    # 6. 更新 Performance 成績單（追蹤舊 BUY + 附上今日新 BUY）
    print("更新 Performance 成績單...")
    try:
        existing_perf = read_performance()
        updated_perf = update_performance(existing_perf, results)
        write_performance(updated_perf)
        stats = perf_summary(updated_perf)
        if stats["count"] > 0:
            print(
                f"  → 已完成追蹤 {stats['count']} 筆 | "
                f"T+20 勝率 {stats['winrate_t20']}% | "
                f"平均報酬 {stats['avg_t20']}% | "
                f"觸及停利 {stats['hit_target']} 次 / 停損 {stats['hit_stop']} 次"
            )
        else:
            print("  → 尚未有完成追蹤的訊號（需累積 20 交易日）")
    except Exception as e:
        print(f"⚠️ Performance 追蹤失敗: {e}", file=sys.stderr)
        stats = None

    # 7. 發送 Telegram
    print("發送 Telegram...")
    for msg in format_messages(results, watchlist, market=market, night_note=night_note):
        send_telegram(msg)
        time.sleep(0.5)

    # 7b. 雙軌並行：發送今日全市場突圍 TOP 5（自動排除既有觀察名單標的）
    print("掃描全市場突圍 TOP 5...")
    try:
        watch_codes = {str(w["stock_id"]).strip() for w in watchlist}
        breakout_msg = format_market_breakout_top5(exclude_codes=watch_codes, top_n=5)
        send_telegram(breakout_msg)
    except Exception as e:
        print(f"⚠️ 全市場突圍掃描失敗: {e}", file=sys.stderr)

    # 7c. 既有持倉移動停利與均線健檢推播（若有 positions.json）
    try:
        from stock_strategies.portfolio_guard import load_positions
        from stock_strategies.vision_audit import audit_positions
        cur_pos = load_positions()
        if cur_pos:
            print("執行既有持倉移動停利健檢...")
            send_telegram(audit_positions(cur_pos, save=True))
    except Exception as e:
        print(f"⚠️ 持倉健檢推播失敗: {e}", file=sys.stderr)

    # 8. 若有累積的成績單，額外推一則摘要
    if stats and stats["count"] >= 5:
        send_telegram(_format_perf_message(stats))

    print("✅ 完成")


def _format_perf_message(stats: dict) -> str:
    lines = [
        "📈 *系統成績單（累積追蹤）*",
        "",
        f"已完成追蹤訊號: {stats['count']} 筆",
        f"T+20 勝率: {stats['winrate_t20']}%",
        f"T+20 平均報酬: {stats['avg_t20']}%",
        f"觸及停利 +{int(0.10 * 100)}%: {stats['hit_target']} 次",
        f"觸及停損 -{int(0.08 * 100)}%: {stats['hit_stop']} 次",
        "",
        "_完整紀錄見 Google Sheet『Performance』分頁_",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
