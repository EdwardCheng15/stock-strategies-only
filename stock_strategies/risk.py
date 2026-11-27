"""停損幅度計算：依波動率（ATR）或固定比例。

回測對照（近 3 年 110 檔、移動停利出場、扣成本）：ATR×2.5 在 2024 盤整年與排除 2025 後
都優於固定 8%，因此預設 stop_mode="atr"、atr_stop_mult=2.5，限制在 4%～15%。
"""

from __future__ import annotations

import math

import pandas as pd


def _params(params: dict | None) -> dict:
    if params is not None:
        return params
    from .loader import merge_params
    return merge_params(None)


def clip_stop(raw: float, params: dict | None = None) -> float:
    p = _params(params)
    lo, hi = float(p.get("stop_min_pct", 0.04)), float(p.get("stop_max_pct", 0.15))
    return round(min(max(raw, lo), hi), 4)


def atr14(px: pd.DataFrame) -> float | None:
    """最近 14 日平均真實波幅（與 indicators.add_indicators 的 atr 欄位相同算法）。"""
    if px is None or len(px) < 15:
        return None
    h, l, c = (px[k].astype(float) for k in ("high", "low", "close"))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    v = float(tr.rolling(14).mean().iloc[-1])
    return v if math.isfinite(v) and v > 0 else None


def stop_pct_from_atr(atr: float | None, close: float | None, params: dict | None = None) -> float:
    """停損幅度（小數）。stop_mode=atr 且資料有效時 = clip(倍數 × ATR / 收盤)，否則用固定 stop_loss。"""
    p = _params(params)
    fixed = float(p.get("stop_loss", 0.08))
    if p.get("stop_mode", "atr") != "atr" or not atr or not close or close <= 0:
        return fixed
    return clip_stop(float(p.get("atr_stop_mult", 2.5)) * atr / close, p)


def stop_pct_for_prices(px: pd.DataFrame, params: dict | None = None) -> float:
    close = float(px["close"].astype(float).iloc[-1]) if px is not None and len(px) else None
    return stop_pct_from_atr(atr14(px), close, params)
