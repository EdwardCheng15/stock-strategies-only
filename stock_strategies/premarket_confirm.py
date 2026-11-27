"""盤前確認：把昨日盤後候選，依昨晚夜盤與美股隔夜風險，篩成「今日最終清單」。

兩段式流程：
  盤後 main.py      → 評分、寫入 Signals 分頁、推「明日候選清單」
  盤前 premarket.py → 讀最新一批候選 → confirm_candidates() → 推「今日最終清單」
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np

from .config import CONFIG

DEFAULT_GAP_PCT = 3.5   # 開盤跳空超過此幅度放棄（與回測 max_gap_up_pct 一致）
WARN_GAP_PCT = 2.0      # 美股警示時收緊追價上限


def _num(v, default: float | None = None) -> float | None:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _act(s: dict) -> str:
    return str(s.get("action", "")).upper()


def latest_batch(signals: list[dict]) -> tuple[str, list[dict]]:
    """取 Signals 分頁中最新一天的所有紀錄。回傳 (日期, 紀錄)。"""
    dates = [str(s.get("date", "")) for s in signals if s.get("date")]
    if not dates:
        return "", []
    day = max(dates)
    seen, batch = set(), []
    for s in signals:  # read_latest_signals 最新在前；同日重跑時只保留最後一次結果
        sid = str(s.get("stock_id", "")).strip()
        if str(s.get("date", "")) == day and sid not in seen:
            seen.add(sid)
            batch.append(s)
    return day, batch


def staleness_note(batch_day: str, today: date | None = None) -> str:
    """候選日期不是上一個交易日時提醒（簡化：只看週一到週五，不含國定假日）。"""
    if not batch_day:
        return "⚠️ Signals 分頁沒有任何選股紀錄，昨日盤後可能未執行"
    today = today or datetime.now().date()
    try:
        d = datetime.strptime(batch_day[:10], "%Y-%m-%d").date()
    except ValueError:
        return ""
    gap = int(np.busday_count(d, today))  # d 到 today 之間（含 d、不含 today）的工作日數
    if gap > 1:
        return f"⚠️ 最新候選是 {batch_day}（已隔 {gap} 個交易日），昨日盤後可能沒有執行，請確認"
    return ""


def confirm_candidates(batch: list[dict], night: dict | None, us_risk: dict, us_stats: dict | None = None) -> dict:
    """依夜盤與美股風險確認候選。

    規則：
      - 夜盤大跌（≤ -night_gap_big）→ BUY 降為 WATCH
      - 美股強警示 → BUY 降為 WATCH
      - 美股警示 → BUY 保留，追價上限收緊到 +2%
      - 夜盤小跌 → BUY 保留，標註逆風
      - 個股美股推估（連動美股＋產業指數＋EWT，見 us_market.infer_stock_outlook）：
          強逆風 → 該檔 BUY 暫緩；逆風 → 標註等開盤止穩；強順風 → 提醒可能開高觸及放棄價
    回傳 {"buys", "downgraded", "watches", "header_notes"}，每檔附 gap_cap_price。
    """
    big = float(CONFIG.get("night_gap_big", 1.5))
    night_pct = float(night["pct"]) if night else None
    night_crash = night_pct is not None and night_pct <= -big
    night_weak = night is not None and night.get("bias") == "bear" and not night_crash
    us_level = us_risk.get("level", "unknown")
    gap_pct = WARN_GAP_PCT if us_level == "warn" else DEFAULT_GAP_PCT

    header_notes = []
    if night_crash:
        header_notes.append(f"🔴 昨晚夜盤大跌 {night_pct:+.2f}%，BUY 全數暫緩")
    if us_level == "strong_warn":
        header_notes.append("🚨 美股強警示，BUY 全數暫緩")
    elif us_level == "warn":
        header_notes.append(f"⚠️ 美股警示，追價上限收緊為 +{WARN_GAP_PCT:.0f}%")

    outlook_fn = None
    if us_stats:
        from .us_market import infer_stock_outlook
        outlook_fn = lambda sid: infer_stock_outlook(sid, us_stats)

    buys, downgraded, watches = [], [], []
    for s in batch:
        act = _act(s)
        if act not in ("BUY", "WATCH"):
            continue
        item = dict(s)
        entry = _num(s.get("entry_price"))
        item["gap_cap_price"] = round(entry * (1 + gap_pct / 100), 2) if entry else None
        item["gap_cap_pct"] = gap_pct
        item["confirm_notes"] = []
        item["us_outlook"] = outlook_fn(s.get("stock_id", "")) if outlook_fn else {"level": "unknown", "tag": "", "detail": ""}
        us_lv = item["us_outlook"]["level"]
        if act == "WATCH":
            watches.append(item)
            continue
        if night_crash:
            item["confirm_notes"].append(f"夜盤大跌 {night_pct:+.1f}%")
        if us_level == "strong_warn":
            item["confirm_notes"].append("美股強警示")
        if us_lv == "strong_head":
            item["confirm_notes"].append(f"連動美股強逆風（{item['us_outlook']['detail']}）")
        if item["confirm_notes"]:
            downgraded.append(item)
            continue
        if night_weak:
            item["confirm_notes"].append(f"夜盤逆風 {night_pct:+.1f}%，等開盤止穩")
        if us_level == "warn":
            item["confirm_notes"].append("美股警示，勿追跳空")
        if us_lv == "head":
            item["confirm_notes"].append("連動美股逆風，等開盤止穩再進")
        elif us_lv == "strong_tail":
            item["confirm_notes"].append("連動美股強漲，可能開高超過放棄價，不追")
        buys.append(item)

    key = lambda x: -(_num(x.get("signal_score"), 0) or 0)
    return {
        "buys": sorted(buys, key=key),
        "downgraded": sorted(downgraded, key=key),
        "watches": sorted(watches, key=key),
        "header_notes": header_notes,
    }


def format_final_list(
    confirmed: dict,
    batch_day: str,
    night: dict | None,
    us_risk: dict,
    stale_note: str = "",
    watch_limit: int = 6,
    open_outlook: dict | None = None,
) -> str:
    today = datetime.now()
    wd = "一二三四五六日"[today.weekday()]
    lines = [f"🌅 *今日最終清單* {today.strftime('%Y/%m/%d')} (週{wd})"]
    lines.append(f"依 {batch_day or '—'} 盤後候選 × 昨晚夜盤 × 美股隔夜確認")
    if stale_note:
        lines.append(stale_note)

    if night:
        lines.append(f"{night['emoji']} 夜盤 {night['pct']:+.2f}%（{night['label']}）→ {night['direction']}")
    else:
        lines.append("⚪ 夜盤資料取不到，未套用夜盤確認")
    us_label = {
        "normal": "🟢 美股無警示",
        "warn": "🟠 美股警示",
        "strong_warn": "🔴 美股強警示",
        "unknown": "⚪ 美股資料取不到，未套用美股確認",
    }
    us_line = us_label.get(us_risk.get("level", "unknown"), "")
    if us_risk.get("reasons"):
        us_line += f"（{'；'.join(us_risk['reasons'][:2])}）"
    lines.append(us_line)
    if open_outlook and open_outlook.get("est") is not None:
        lines.append(f"🇺🇸 美股推估台股開盤：{open_outlook['label']}（{open_outlook['basis']}）")
    lines.extend(confirmed["header_notes"])
    lines.append("")

    from .loader import merge_params, exit_rule_text
    trailing = merge_params(None).get("exit_mode", "trailing") == "trailing"
    buys = confirmed["buys"]
    if buys:
        lines.append("🟢 *可執行 BUY*")
        for s in buys:
            wr = _num(s.get("winrate"))
            wr_txt = f"{wr*100:.0f}%" if wr is not None else "N/A"
            pos_v = _num(s.get("allocated_size_pct", s.get("position_pct")))
            pos = "總曝險已達 70% 上限，不建議加倉" if pos_v == 0 else (f"{pos_v:g}%" if pos_v is not None else "—")
            gap_cap = s.get("gap_cap_price")
            exit_short = "保本後破 10 日線出場" if trailing else f"目標 {s.get('target_price')}"
            lines.append(f"*{s.get('stock_id')} {s.get('name')}* {s.get('signal_score')}分｜勝率 {wr_txt}")
            lines.append(
                f"  昨收 {s.get('entry_price')}｜開盤高於 *{gap_cap:.2f}*（+{s['gap_cap_pct']:.1f}%）放棄" if gap_cap else f"  昨收 {s.get('entry_price')}"
            )
            lines.append(f"  停損 {s.get('stop_loss_price')}｜{exit_short}｜部位 {pos}")
            uo = s.get("us_outlook") or {}
            if uo.get("tag"):
                lines.append(f"  {uo['tag']}（{uo['detail']}）")
            if s.get("confirm_notes"):
                lines.append(f"  ⚠️ {' / '.join(s['confirm_notes'])}")
    else:
        lines.append("🟢 今日沒有可執行的 BUY")
    lines.append("")

    if confirmed["downgraded"]:
        lines.append("🔻 *暫緩（BUY→WATCH）*")
        for s in confirmed["downgraded"]:
            lines.append(f"• {s.get('stock_id')} {s.get('name')}：{' / '.join(s['confirm_notes'])}")
        lines.append("")

    watches = confirmed["watches"]
    if watches:
        lines.append(f"🟡 *觀察 前 {min(watch_limit, len(watches))} 名*")
        for s in watches[:watch_limit]:
            uo = s.get("us_outlook") or {}
            star = f"｜{uo['tag']}" if uo.get("level") in ("tail", "strong_tail") else ""
            lines.append(f"• {s.get('stock_id')} {s.get('name')} {s.get('signal_score')}分{star}")
        lines.append("")

    if buys:
        lines.append(f"📐 {exit_rule_text()}；停損依各檔停損價")
    lines.append("💡 _9:00 開盤價超過放棄價不追；進場後依停損價嚴格執行_")
    return "\n".join(lines)
