"""單檔歷史回測：盡量用與實際 BUY 相同、且當時就能知道的條件找訊號。

與舊版差異：
- 不重疊：一筆交易持有期間不再開新訊號（舊版連續 10 天符合會算 10 筆，樣本灌水）
- 進場門檻與實盤一致：技術分取 max(訊號門檻, BUY 技術門檻)，並套用 5 日線乖離、
  5 日漲幅上限、大盤月線濾網（皆只用訊號當天以前的資料）
- 隔日開盤跳空超過 max_gap_up_pct 不進場（與盤前清單的放棄價一致）
- 進場日當天的高低點也納入停損／停利判斷
無法逐日重建的條件（歷史基本面、V3.4 籌碼／營收因子）仍未納入，勝率僅反映技術面進場規則。
"""

import numpy as np
import pandas as pd

from .config import CONFIG
from .indicators import tech_score_at


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

    close = df["close"].astype(float).to_numpy()
    wins = losses = 0
    returns: list[float] = []
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

        entry = df.iloc[i + 1].get("open")
        if entry is None or pd.isna(entry) or entry <= 0:
            i += 1
            continue
        entry = float(entry)
        if max_gap > 0 and c > 0 and (entry / c - 1.0) > max_gap:
            i += 1  # 跳空過大放棄，隔天可重新評估
            continue

        window = df.iloc[i + 1 : i + 1 + hold_days]  # 含進場日
        if len(window) < hold_days:
            break
        hit_stop = False
        hit_target = False
        for _, bar in window.iterrows():  # 依時間順序判斷先觸及停損或停利
            if float(bar["low"]) <= entry * (1 - stop):
                hit_stop = True
                break
            if float(bar["high"]) >= entry * (1 + target):
                hit_target = True
                break
        if hit_stop:
            losses += 1
            returns.append(-stop)
        elif hit_target:
            wins += 1
            returns.append(target)
        else:
            r = (float(window.iloc[-1]["close"]) - entry) / entry
            returns.append(r)
            wins += int(r > 0)
            losses += int(r <= 0)
        i = i + hold_days + 1  # 不重疊：持有期結束後才找下一筆

    total = wins + losses
    if total == 0:
        return {"winrate": None, "samples": 0, "avg_return": None}
    return {"winrate": round(wins / total, 3), "samples": total, "avg_return": round(float(np.mean(returns)), 4)}
