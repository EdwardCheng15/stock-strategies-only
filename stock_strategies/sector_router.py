"""股票行為基因（Stock Archetype）自動路由與個股週期引擎"""

from __future__ import annotations
import numpy as np
import pandas as pd


def classify_stock_archetype(
    px: pd.DataFrame,
    fund: dict | None = None,
    explicit_strategy: str = "",
) -> tuple[str, str, dict]:
    """依據個股真實波動率、獲利體質與均線位階，自動判定行為基因與週期"""
    close_s = px["close"].astype(float)
    close = float(close_s.iloc[-1])

    # 計算年化波動率（近 120 日日報酬標準差 * sqrt(252)）
    rets = close_s.pct_change().dropna().tail(120)
    ann_vol = float(rets.std() * np.sqrt(252) * 100.0) if len(rets) >= 20 else 28.0

    # 計算均線位階（個股自身週期）
    ma20 = float(close_s.tail(20).mean()) if len(close_s) >= 20 else close
    ma60 = float(close_s.tail(60).mean()) if len(close_s) >= 60 else close

    if close > ma20 and ma20 > ma60:
        cycle_info = {"phase": "多頭主升段", "emoji": "🟢", "threshold_adj": -3.0}
    elif close < ma20 and close < ma60:
        cycle_info = {"phase": "空頭修正期", "emoji": "🔴", "threshold_adj": 6.0}
    else:
        cycle_info = {"phase": "震盪整理期", "emoji": "🟡", "threshold_adj": 0.0}

    # 解析基本面體質
    fund = fund or {}
    eps_vals = list((fund.get("eps") or {}).values())
    roe_vals = list((fund.get("roe") or {}).values())
    latest_roe = float(roe_vals[-1]) if roe_vals else 0.0
    min_eps = min(float(v) for v in eps_vals) if eps_vals else 0.0

    # 行為基因自動分類
    if explicit_strategy and explicit_strategy not in ("default", "auto", ""):
        chosen_strat = explicit_strategy
        label = "指定策略"
    elif ann_vol > 32.0 and (min_eps > 0.5 or close > ma60):
        chosen_strat = "archetype_growth"
        label = f"高速成長動能(波動{ann_vol:.0f}%)"
    elif ann_vol <= 32.0 and latest_roe >= 8.0 and min_eps > 1.0:
        chosen_strat = "archetype_value"
        label = f"低波高息價值(ROE {latest_roe:.1f}%)"
    else:
        chosen_strat = "archetype_turnaround"
        label = f"景氣轉折突破(波動{ann_vol:.0f}%)"

    return chosen_strat, label, cycle_info
