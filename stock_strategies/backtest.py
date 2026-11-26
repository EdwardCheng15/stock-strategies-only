import numpy as np
import pandas as pd
from .config import CONFIG
from .indicators import tech_score_at

def backtest(df: pd.DataFrame, params: dict | None = None) -> dict:
    if params is None:
        params = {}
    hold_days = int(params.get("hold_days", CONFIG["hold_days"]))
    min_score = int(params.get("min_tech_score_for_signal", CONFIG["min_tech_score_for_signal"]))
    target = float(params.get("target_return", CONFIG["target_return"]))
    stop = float(params.get("stop_loss", CONFIG["stop_loss"]))
    max_gap = float(params.get("max_gap_up_pct", 0.0))
    max_bias = float(params.get("max_ma5_bias_pct", 0.0))

    indices = []
    for i in range(60, len(df) - hold_days - 1):
        if tech_score_at(df.iloc[i], params)["score"] >= min_score:
            if max_bias > 0 and i >= 5:
                ma5 = float(df["close"].iloc[i - 4 : i + 1].mean())
                if ma5 > 0 and (float(df["close"].iloc[i]) / ma5 - 1.0) * 100.0 > max_bias:
                    continue
            indices.append(i)

    if not indices:
        return {"winrate": None, "samples": 0, "avg_return": None}

    wins = losses = 0
    returns = []
    for idx in indices:
        sig_c = float(df.iloc[idx].get("close", 0) or 0)
        entry = df.iloc[idx + 1].get("open")
        if entry is None or pd.isna(entry) or entry <= 0:
            continue
        if max_gap > 0 and sig_c > 0 and (float(entry) / sig_c - 1.0) > max_gap:
            continue
        future = df.iloc[idx + 2 : idx + 2 + hold_days]
        if len(future) < hold_days:
            continue
        hi, lo, fc = future["high"].max(), future["low"].min(), future.iloc[-1]["close"]
        if hi >= entry * (1 + target) and not (lo <= entry * (1 - stop)):
            wins += 1
            returns.append(target)
        elif lo <= entry * (1 - stop):
            losses += 1
            returns.append(-stop)
        else:
            r = (fc - entry) / entry
            returns.append(r)
            wins += int(r > 0)
            losses += int(r <= 0)

    total = wins + losses
    if total == 0:
        return {"winrate": None, "samples": 0}
    return {"winrate": round(wins / total, 3), "samples": total, "avg_return": round(float(np.mean(returns)), 4)}
