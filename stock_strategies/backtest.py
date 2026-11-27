"""單檔歷史回測：盡量用與實際 BUY 相同、且當時就能知道的條件找訊號。

與舊版差異：
- 不重疊：一筆交易持有期間不再開新訊號（舊版連續 10 天符合會算 10 筆，樣本灌水）
- 進場門檻與實盤一致：技術分取 max(訊號門檻, BUY 技術門檻)，並套用 5 日線乖離、
  5 日漲幅上限、大盤月線濾網（皆只用訊號當天以前的資料）
- 隔日開盤跳空超過 max_gap_up_pct 不進場（與盤前清單的放棄價一致）
- 進場日當天的高低點也納入停損／停利判斷
- 報酬扣除來回交易成本（round_trip_cost，預設 0.47%），扣完 > 0 才算贏
- exit_mode="trailing" 模擬實盤持倉健檢的移動停利出場；"fixed" 為固定持有期＋停損停利
- stop_mode="atr" 每筆停損 = clip(2.5 × ATR14 ÷ 訊號日收盤, 4%, 15%)；"fixed" 用固定 stop_loss
無法逐日重建的條件（歷史基本面、V3.4 籌碼／營收因子）仍未納入，勝率僅反映技術面進場規則。
"""

import numpy as np
import pandas as pd

from .config import CONFIG
from .indicators import tech_score_at
from .risk import stop_pct_from_atr


def _market_ok_series(df: pd.DataFrame, market_df: pd.DataFrame | None, ma_period: int) -> pd.Series | None:
    """回傳與 df 對齊的布林序列：該日加權指數是否站上 N 日線。無資料回 None。"""
    if market_df is None or market_df.empty or "date" not in df.columns:
        return None
    m = market_df[["date", "close"]].copy()
    m["date"] = pd.to_datetime(m["date"])
    m = m.sort_values("date")
    m["ok"] = m["close"].astype(float) > m["close"].astype(float).rolling(ma_period).mean()
    ok_map = m.set_index("date")["ok"]
    dates = pd.to_datetime(df["date"])
    return ok_map.reindex(dates, method="ffill").reset_index(drop=True)


def backtest(df: pd.DataFrame, params: dict | None = None, market_df: pd.DataFrame | None = None) -> dict:
    if params is None:
        params = {}
    hold_days = int(params.get("hold_days", CONFIG["hold_days"]))
    min_score = max(
        int(params.get("min_tech_score_for_signal", CONFIG["min_tech_score_for_signal"])),
        int(params.get("min_tech_score_for_buy", 0) or 0),
    )
    target = float(params.get("target_return", CONFIG["target_return"]))
    stop = float(params.get("stop_loss", CONFIG["stop_loss"]))
    max_gap = float(params.get("max_gap_up_pct", 0.0) or 0.0)
    max_bias = float(params.get("max_ma5_bias_pct", 0.0) or 0.0)
    max_chg5 = float(params.get("max_chg_5d_pct", 0.0) or 0.0)
    use_mkt = bool(params.get("market_filter_enabled", True))
    mkt_ok = _market_ok_series(df, market_df, int(params.get("market_filter_ma_period", 20))) if use_mkt else None

    cost = float(params.get("round_trip_cost", 0.0047) or 0.0)
    exit_mode = str(params.get("exit_mode", "fixed"))
    be_trigger = float(params.get("breakeven_trigger_pct", 0.06))
    max_hold = int(params.get("max_hold_days", 120))
    exit_ma = int(params.get("exit_ma_days", 20))

    close = df["close"].astype(float).to_numpy()
    opens = df["open"].astype(float).to_numpy()
    highs = df["high"].astype(float).to_numpy()
    lows = df["low"].astype(float).to_numpy()
    ma_exit = pd.Series(close).rolling(exit_ma).mean().to_numpy()
    use_atr = params.get("stop_mode", "atr") == "atr" and "atr" in df.columns
    atr = df["atr"].astype(float).to_numpy() if use_atr else None
    dates = df["date"].astype(str).str[:10].to_numpy() if "date" in df.columns else np.arange(len(df)).astype(str)

    trades: list[dict] = []
    i = 60
    last = len(df) - hold_days - 1
    while i < last:
        c = close[i]
        if tech_score_at(df.iloc[i], params)["score"] < min_score:
            i += 1
            continue
        if max_bias > 0 and i >= 5:
            ma5 = close[i - 4 : i + 1].mean()
            if ma5 > 0 and (c / ma5 - 1.0) * 100.0 > max_bias:
                i += 1
                continue
        if max_chg5 > 0 and i >= 5 and close[i - 5] > 0 and (c / close[i - 5] - 1.0) * 100.0 > max_chg5:
            i += 1
            continue
        mv = mkt_ok.iloc[i] if mkt_ok is not None else None
        if mv is not None and not pd.isna(mv) and not bool(mv):
            i += 1
            continue

        entry = opens[i + 1]
        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue
        if max_gap > 0 and c > 0 and (entry / c - 1.0) > max_gap:
            i += 1  # 跳空過大放棄，隔天可重新評估
            continue

        trade_stop = stop_pct_from_atr(atr[i] if atr is not None and np.isfinite(atr[i]) else None, c, params) if use_atr else stop
        if exit_mode == "trailing":
            gross, exit_idx = _trailing_exit(i + 1, entry, close, opens, ma_exit, trade_stop, be_trigger, max_hold)
        else:
            gross, exit_idx = _fixed_exit(i + 1, entry, close, highs, lows, hold_days, target, trade_stop)
        if gross is None:
            break
        trades.append({"entry_date": dates[i + 1], "exit_date": dates[exit_idx], "ret": float(gross - cost), "stop": trade_stop})
        i = exit_idx + 1  # 不重疊：出場後才找下一筆

    if not trades:
        return {"winrate": None, "samples": 0, "avg_return": None, "trades": []}
    rets = [t["ret"] for t in trades]
    wins = sum(1 for r in rets if r > 0)
    return {
        "winrate": round(wins / len(rets), 3),
        "samples": len(rets),
        "avg_return": round(float(np.mean(rets)), 4),
        "trades": trades,
    }


def _fixed_exit(start, entry, close, highs, lows, hold_days, target, stop):
    """固定持有：持有期內先碰停損或停利就出場（同一根都碰到時保守算停損），否則期末收盤出場。"""
    end = start + hold_days
    if end > len(close):
        return None, None
    for j in range(start, end):
        if lows[j] <= entry * (1 - stop):
            return -stop, j
        if highs[j] >= entry * (1 + target):
            return target, j
    return close[end - 1] / entry - 1.0, end - 1


def _trailing_exit(start, entry, close, opens, ma_exit, stop, be_trigger, max_hold):
    """模擬實盤持倉健檢（portfolio_guard.check_holding_health）：
    每天收盤後檢查 → 隔天開盤執行。
      - 收盤 ≤ 防守價 → 出場（防守價：初始 = 成本×(1-停損)；波段收盤高點達 +6% 後上移到成本）
      - 已保本且收盤跌破出場均線（exit_ma_days，預設月線）→ 停利出場
      - 最多持有 max_hold 天，到期收盤出場
    """
    n = len(close)
    peak = entry
    for j in range(start, min(start + max_hold, n)):
        peak = max(peak, close[j])
        breakeven = peak >= entry * (1 + be_trigger)
        active_stop = entry if breakeven else entry * (1 - stop)
        exit_now = close[j] <= active_stop or (breakeven and np.isfinite(ma_exit[j]) and close[j] < ma_exit[j])
        if exit_now:
            if j + 1 >= n:
                return None, None
            px = opens[j + 1] if np.isfinite(opens[j + 1]) and opens[j + 1] > 0 else close[j]
            return px / entry - 1.0, j + 1
    end = start + max_hold - 1
    if end >= n:
        return None, None
    return close[end] / entry - 1.0, end
