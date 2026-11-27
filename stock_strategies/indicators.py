import pandas as pd
import numpy as np


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()

    df["bb_mid"] = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std

    low_min = df["low"].rolling(9).min()
    high_max = df["high"].rolling(9).max()
    rsv = (df["close"] - low_min) / (high_max - low_min) * 100
    df["k"] = rsv.ewm(com=2).mean()
    df["d"] = df["k"].ewm(com=2).mean()

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["dif"] = ema12 - ema26
    df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["dif"] - df["dea"]
    df["macd_hist_prev"] = df["macd_hist"].shift(1)

    # KD 真正的黃金交叉：K 由下往上穿越 D，近 3 根 K 線內發生過
    cross = (df["k"] > df["d"]) & (df["k"].shift(1) <= df["d"].shift(1))
    df["kd_cross_recent"] = cross.astype(float).rolling(3, min_periods=1).max().astype(bool)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    return df


def tech_score_at(row: pd.Series, params: dict | None = None) -> dict:
    """對一天計算技術分 (0-100)。
    params 可包含 use_ma_alignment / use_bollinger_bounce / use_kd_golden_cross /
    use_macd_bullish 四個布林開關來開關各訊號。
    """
    if params is None:
        params = {}
    use_ma = params.get("use_ma_alignment", True)
    use_bb = params.get("use_bollinger_bounce", True)
    use_kd = params.get("use_kd_golden_cross", True)
    use_macd = params.get("use_macd_bullish", True)

    # 開啟的訊號數量決定每個訊號最大分數，讓總分維持 0-100
    enabled = sum([use_ma, use_bb, use_kd, use_macd]) or 1
    max_per = 100 / enabled

    score = 0.0
    signals: list[str] = []

    def _v(key):
        val = row.get(key) if hasattr(row, "get") else None
        return val if val is not None and pd.notna(val) else None

    close, ma20, ma60 = _v("close"), _v("ma20"), _v("ma60")
    uptrend = ma20 is not None and ma60 is not None and ma20 > ma60

    # 1. 趨勢：均線多頭排列
    if use_ma and close is not None and ma20 is not None and ma60 is not None:
        if close > ma20 > ma60:
            score += max_per
            signals.append("均線多頭")
        elif close > ma20:
            score += max_per * 0.48

    # 2. 進場位置：多頭趨勢中回測月線支撐（取代舊的布林下軌反彈，避免順勢與逆勢條件互相矛盾）
    #    沿用 use_bollinger_bounce 開關，策略 JSON 不需修改
    if use_bb and uptrend and close is not None:
        dist = close / ma20 - 1.0
        if -0.02 <= dist <= 0.03:
            score += max_per
            signals.append("回測月線支撐")
        elif 0.03 < dist <= 0.06:
            score += max_per * 0.4

    # 3. KD：近 3 日真正黃金交叉且未過熱
    k, d = _v("k"), _v("d")
    if use_kd and k is not None and d is not None:
        recent_cross = bool(row.get("kd_cross_recent", False)) if hasattr(row, "get") else False
        if recent_cross and k > d and k < 80:
            score += max_per
            signals.append("KD黃金交叉")
        elif k > d and k < 80:
            score += max_per * 0.5
        elif k > d:
            score += max_per * 0.2

    # 4. MACD：柱狀體翻正且持續放大
    hist, prev_hist = _v("macd_hist"), _v("macd_hist_prev")
    if use_macd and hist is not None:
        if hist > 0 and prev_hist is not None and hist > prev_hist:
            score += max_per
            signals.append("MACD多頭")
        elif hist > 0:
            score += max_per * 0.5

    return {"score": int(round(score)), "signals": signals}
