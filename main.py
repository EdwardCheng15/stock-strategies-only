"""
V3.2 每日選股訊號系統（兩段式推播第一段：盤後「明日候選清單」）

兩段式：
- 盤後（本檔）：評分 → 寫入 Signals 分頁 → 推精簡候選清單
- 盤前（premarket.py）：依昨晚夜盤與美股確認 → 推今日最終清單 + 持倉健檢

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
from stock_strategies.notify import send_telegram, format_candidates
from stock_strategies.market import get_market_state, apply_market_filter
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

    # 2c. 併入全市場動能突圍前 15 名（確保包含完整 TOP 5 榜單）進入正式評分池
    watch_codes = {str(w["stock_id"]).strip() for w in watchlist}
    dynamic_pool = []
    try:
        cands = scan_dynamic_candidates(min_volume=1500, min_pct=2.0, max_pct=8.5, top_n=20)
        for c in cands:
            code_str = str(c.get("code", "")).strip()
            if code_str and code_str not in watch_codes and len(dynamic_pool) < 15:
                dynamic_pool.append({
                    "stock_id": code_str,
                    "name": f"{c.get('name', '')}[全市場突圍]",
                    "strategy": "default",
                })
        print(f"🔥 全市場動能候選池：{len(dynamic_pool)} 檔")
    except Exception as e:
        print(f"⚠️ 全市場候選池取得失敗: {e}")

    # 2d. 省 API 額度：全市場候選先只用價格算技術分，過門檻才進完整評分（基本面＋V3.4 因子很耗額度）
    dynamic_pool, prescreen_skipped = _prescreen_by_tech(dynamic_pool)
    print(f"  → 技術面初篩後保留 {len(dynamic_pool)} 檔，略過 {prescreen_skipped} 檔")

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

    # 4d. 處置股濾網
    try:
        from stock_strategies.disposition import get_disposition_stocks, apply_disposition_filter
        d_down = apply_disposition_filter(results, get_disposition_stocks())
        if d_down: print(f"🚨 處置股降級: {d_down} 檔")
    except Exception as e:
        print(f"⚠️ 處置股略過: {e}")

    # （夜盤濾網移到隔天 08:00 盤前確認，改用「今晚」夜盤，見 premarket.py）

    order = {"BUY": 0, "WATCH": 1, "SKIP": 2, "ERROR": 3}
    results.sort(key=lambda x: (order.get(x.get("action"), 4), -x.get("signal_score", 0)))

    # 4c. 組合集中度與總曝險控管（大盤站穩月線上限 70%，跌破月線自動降至 40%）
    try:
        from stock_strategies.portfolio_guard import load_positions, allocate_buy_signals
        cur_pos = load_positions()
        max_exp = 70.0 if market.get("bullish", True) else 40.0
        buys = [r for r in results if r.get("action") == "BUY"]
        total_cap = float(os.environ.get("TOTAL_CAPITAL", "200000") or 200000)
        allocate_buy_signals(buys, cur_pos, total_capital=total_cap, max_total_exposure_pct=max_exp)
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

    # 7. 發送 Telegram：明日候選清單（含全市場突圍 TOP 5）
    today_str = datetime.now().strftime("%Y-%m-%d")
    bar_dates = [r.get("bar_date") for r in results if r.get("bar_date")]
    data_note = ""
    errors = [r for r in results if r.get("action") == "ERROR"]
    rate_limited = [r for r in errors if r.get("error_type") == "FinMindRateLimitError"]
    if bar_dates:
        latest_bar = max(bar_dates)
        stale = sum(1 for d in bar_dates if d < today_str)
        if latest_bar < today_str:
            data_note = f"⚠️ 資料尚未更新到今天（最新 K 線 {latest_bar}），以下候選以 {latest_bar} 收盤計算"
        elif stale:
            data_note = f"⚠️ {stale}/{len(bar_dates)} 檔資料尚未更新到今天"
    if rate_limited:
        ids = "、".join(str(r["stock_id"]) for r in rate_limited[:8])
        data_note += f"\n⚠️ FinMind API 額度不足，{len(rate_limited)} 檔未評分（{ids}）"
    elif errors:
        ids = "、".join(str(r["stock_id"]) for r in errors[:8])
        data_note += f"\n⚠️ {len(errors)} 檔評分失敗（{ids}）"
    data_note = data_note.strip()
    if data_note:
        print(data_note)

    print("發送 Telegram：盤後候選清單...")
    msg = format_candidates(results, market=market, data_note=data_note)
    try:
        watch_codes = {str(w["stock_id"]).strip() for w in watchlist}
        msg += format_market_breakout_top5(exclude_codes=watch_codes, top_n=5)
    except Exception as e:
        print(f"⚠️ 全市場突圍掃描失敗: {e}", file=sys.stderr)
    send_telegram(msg)

    # 8. 若有累積的成績單，額外推一則摘要
    if stats and stats["count"] >= 5:
        send_telegram(_format_perf_message(stats))

    # 9. 清理超過 90 天（一季）未更新的冷門快取檔
    try:
        from stock_strategies.cache import prune_stale_cache
        pruned = prune_stale_cache(max_age_days=90)
        if pruned:
            print(f"🧹 已清理 {pruned} 個超過 90 天未更新的快取檔")
    except Exception as e:
        print(f"⚠️ 快取清理略過: {e}", file=sys.stderr)

    print("✅ 完成")


def _prescreen_by_tech(pool: list[dict], min_tech: float | None = None) -> tuple[list[dict], int]:
    """只用（已快取的）日 K 計算技術分，低於 BUY 技術門檻的直接略過，不抓基本面與因子資料。"""
    from stock_strategies.data import get_price_history
    from stock_strategies.indicators import add_indicators, tech_score_at
    from stock_strategies.loader import merge_params

    params = merge_params(None)
    threshold = float(min_tech if min_tech is not None else params["min_tech_score_for_buy"])
    kept, skipped = [], 0
    for row in pool:
        try:
            px = get_price_history(str(row["stock_id"]), params["backtest_years"])
            if len(px) < 100:
                skipped += 1
                continue
            score = tech_score_at(add_indicators(px).iloc[-1], params)["score"]
            if score >= threshold:
                kept.append(row)
            else:
                skipped += 1
        except Exception:
            kept.append(row)  # 初篩失敗不擋，交給完整評分處理
    return kept, skipped


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
