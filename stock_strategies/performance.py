"""訊號效度追蹤（成績單）——用與回測、持倉健檢相同的出場規則追蹤每個 BUY

每個盤後 BUY 都假設「隔天開盤進場」，之後逐日依實盤規則判斷出場：
- 開盤高於放棄價（昨收 × (1 + max_gap_up_pct)）→ 記為「放棄（跳空）」，不計入績效
- 收盤 ≤ 防守價 → 隔天開盤出場（防守價：初始 = 進場價 × (1 - ATR 停損)；
  收盤波段高點達 +6% 後上移到進場價 = 保本）
- 已保本且收盤跌破出場均線（預設月線）→ 隔天開盤停利出場
- 持有滿 max_hold_days 仍未出場 → 當天收盤出場
報酬扣除來回成本（round_trip_cost）。T+5/T+10/T+20 收盤報酬保留作參考。
"""

from datetime import datetime

import numpy as np
import pandas as pd

from .data import get_price_history
from .loader import merge_params
from .risk import stop_pct_for_prices

CHECKPOINTS = (5, 10, 20)
DONE = "完成"
SKIPPED = "放棄（跳空）"
TRACKING = "追蹤中"


def _parse_float(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _simulate_exit(px: pd.DataFrame, entry_iloc: int, entry: float, stop: float, p: dict):
    """回傳 (exit_iloc, exit_price, reason)；資料還不夠判斷時回 None。與 backtest._trailing_exit 規則一致。"""
    close = px["close"].astype(float).to_numpy()
    opens = px["open"].astype(float).to_numpy()
    ma = pd.Series(close).rolling(int(p.get("exit_ma_days", 20))).mean().to_numpy()
    be = float(p.get("breakeven_trigger_pct", 0.06))
    max_hold = int(p.get("max_hold_days", 120))
    n = len(close)
    peak = entry
    for j in range(entry_iloc, min(entry_iloc + max_hold, n)):
        peak = max(peak, close[j])
        breakeven = peak >= entry * (1 + be)
        active_stop = entry if breakeven else entry * (1 - stop)
        if close[j] <= active_stop:
            reason = "保本出場" if breakeven else "停損出場"
        elif breakeven and np.isfinite(ma[j]) and close[j] < ma[j]:
            reason = "破均線停利"
        else:
            continue
        if j + 1 >= n:
            return None  # 明天開盤才出場，還沒有開盤價
        px_out = opens[j + 1] if np.isfinite(opens[j + 1]) and opens[j + 1] > 0 else close[j]
        return j + 1, float(px_out), reason
    end = entry_iloc + max_hold - 1
    if end >= n:
        return None
    return end, float(close[end]), "持有到期"


def update_performance(existing: list[dict], today_signals: list[dict]) -> list[dict]:
    """補齊舊訊號的進場、T+N 與出場結果，再附上今日新 BUY。回傳要寫回 Sheet 的完整紀錄。"""
    p = merge_params(None)
    max_gap = float(p.get("max_gap_up_pct", 0.0) or 0.0) or 0.035
    cost = float(p.get("round_trip_cost", 0.0047))
    records = [dict(r) for r in existing]

    pending: dict[str, list[dict]] = {}
    for r in records:
        st = str(r.get("status", "")).strip()
        if st == SKIPPED or (st == DONE and _parse_float(r.get("ret_net")) is not None):
            continue  # 舊版規則標「完成」但沒有 ret_net 的紀錄，會用新規則重算
        sid = str(r.get("stock_id", "")).strip()
        if sid:
            pending.setdefault(sid, []).append(r)

    for sid, recs in pending.items():
        try:
            px = get_price_history(sid, years=1)
        except Exception:
            continue
        if px.empty:
            continue
        px = px.sort_values("date").reset_index(drop=True)
        px["date_str"] = px["date"].dt.strftime("%Y-%m-%d")
        idx = {d: i for i, d in enumerate(px["date_str"].tolist())}

        for r in recs:
            sig = idx.get(str(r.get("signal_date", "")).strip())
            if sig is None or sig + 1 >= len(px):
                continue
            entry_iloc = sig + 1
            if not _parse_float(r.get("entry_open")):
                r["entry_open"] = round(float(px.at[entry_iloc, "open"]), 2)
            entry = _parse_float(r.get("entry_open"))
            ref = _parse_float(r.get("entry_close")) or float(px.at[sig, "close"])
            if not entry or entry <= 0:
                continue

            gap_cap = _parse_float(r.get("gap_cap")) or round(ref * (1 + max_gap), 2)
            r["gap_cap"] = gap_cap
            if entry > gap_cap:
                r.update({"status": SKIPPED, "exit_reason": "開盤超過放棄價"})
                continue

            stop = _parse_float(r.get("stop_pct"))
            if not stop or not 0 < stop < 1:
                stop = stop_pct_for_prices(px.iloc[: sig + 1], p)
                r["stop_pct"] = round(stop, 4)

            for n in CHECKPOINTS:
                if _parse_float(r.get(f"t{n}_ret")) is not None:
                    continue
                t = entry_iloc + n
                if t < len(px):
                    r[f"t{n}_date"] = px.at[t, "date_str"]
                    r[f"t{n}_close"] = round(float(px.at[t, "close"]), 2)
                    r[f"t{n}_ret"] = round((float(px.at[t, "close"]) / entry - 1) * 100, 2)

            if _parse_float(r.get("ret_net")) is None:
                out = _simulate_exit(px, entry_iloc, entry, stop, p)
                if out:
                    ex_i, ex_px, reason = out
                    r.update({
                        "exit_date": px.at[ex_i, "date_str"],
                        "exit_price": round(ex_px, 2),
                        "exit_reason": reason,
                        "ret_net": round((ex_px / entry - 1 - cost) * 100, 2),
                        "hold_days": int(ex_i - entry_iloc + 1),
                        "status": DONE,
                    })
            if _parse_float(r.get("ret_net")) is None:
                r["status"] = TRACKING

    today = datetime.now().strftime("%Y-%m-%d")
    keys = {(str(r.get("signal_date", "")), str(r.get("stock_id", ""))) for r in records}
    for s in today_signals:
        if s.get("action") != "BUY":
            continue
        sig_date, sid = str(s.get("date", today)), str(s.get("stock_id", ""))
        if (sig_date, sid) in keys:
            continue
        entry_close = _parse_float(s.get("entry_price"))
        stop_pct = _parse_float(s.get("stop_loss_pct"))
        records.append({
            "signal_date": sig_date,
            "stock_id": sid,
            "name": s.get("name", ""),
            "entry_close": s.get("entry_price", ""),
            "gap_cap": round(entry_close * (1 + max_gap), 2) if entry_close else "",
            "stop_pct": round(stop_pct / 100, 4) if stop_pct else "",
            "status": TRACKING,
        })
    return records


def summary(records: list[dict]) -> dict:
    """統計已出場的訊號（不含開盤跳空放棄的）。"""
    done = [r for r in records
            if str(r.get("status", "")).strip() == DONE and _parse_float(r.get("ret_net")) is not None]
    rets = [_parse_float(r.get("ret_net")) for r in done]
    skipped = sum(1 for r in records if str(r.get("status", "")).strip() == SKIPPED)
    tracking = sum(1 for r in records if str(r.get("status", "")).strip() == TRACKING)
    if not rets:
        return {"count": 0, "skipped": skipped, "tracking": tracking}
    wins = [x for x in rets if x > 0]
    losses = [-x for x in rets if x <= 0]
    reasons: dict[str, int] = {}
    for r in done:
        k = str(r.get("exit_reason", "") or "其他")
        reasons[k] = reasons.get(k, 0) + 1
    return {
        "count": len(rets),
        "winrate": round(len(wins) / len(rets) * 100, 1),
        "avg_ret": round(float(np.mean(rets)), 2),
        "payoff": round(float(np.mean(wins) / np.mean(losses)), 2) if wins and losses else None,
        "avg_hold": round(float(np.mean([_parse_float(r.get("hold_days")) or 0 for r in done])), 1),
        "reasons": reasons,
        "skipped": skipped,
        "tracking": tracking,
    }
