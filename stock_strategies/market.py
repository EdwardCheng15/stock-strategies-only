"""大盤狀態濾鏡

抓加權指數 (TAIEX) 的日 K 線，判斷目前是否站上 20 日均線。
若跌破月線，main.py 會把所有 BUY 訊號降級為 WATCH，避免在空頭市場
被連續洗損。
"""

from .datasources import get_index_history


def get_market_state(ma_period: int = 20) -> dict:
    """回傳大盤狀態 dict（可指定均線天數，預設 20=月線）"""
    try:
        df = get_index_history("TAIEX")
        if len(df) < ma_period + 1:
            return {"bullish": True, "close": None, "ma20": None,
                    "note": "⚠️ 大盤資料不足，暫不套用濾鏡"}
        df = df.copy()
        df["ma20"] = df["close"].rolling(ma_period).mean()
        latest = df.iloc[-1]
        close = float(latest["close"]); ma20 = float(latest["ma20"])
        bullish = close > ma20
        pct = (close / ma20 - 1) * 100
        if bullish:
            note = f"🟢 加權 {close:.0f} 站上 {ma_period} 日線 ({pct:+.1f}%)，BUY 訊號照常發出"
        else:
            note = f"🔴 加權 {close:.0f} 跌破 {ma_period} 日線 ({pct:+.1f}%)，BUY 全數降為 WATCH"
        return {"bullish": bullish, "close": close, "ma20": ma20, "note": note}
    except Exception as e:
        return {"bullish": True, "close": None, "ma20": None,
                "note": f"⚠️ 大盤狀態取得失敗（{str(e)[:60]}），暫不套用濾鏡"}


def apply_market_filter(results: list[dict], market: dict) -> int:
    """大盤跌破月線時採「扣 6 分制」：扣除後仍達 62 分者保留 BUY（逆勢領頭羊），其餘降為 WATCH。"""
    if market.get("bullish", True):
        return 0
    downgraded = 0
    for r in results:
        if r.get("action") == "BUY":
            new_score = round(r.get("signal_score", 0) - 6.0, 1)
            r["signal_score"] = new_score
            if new_score < 62.0:
                r["action"] = "WATCH"
                r.setdefault("risk_notes", []).append("大盤破月線扣6分，轉為 WATCH")
                downgraded += 1
            else:
                r.setdefault("risk_notes", []).append("大盤破月線(扣6分仍強勢)，保留 BUY")
    return downgraded
