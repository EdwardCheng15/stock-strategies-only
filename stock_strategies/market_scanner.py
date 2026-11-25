"""全市場動態漏斗掃描器（免觀察名單，自動從上市櫃撈取具量價動能之候選股）"""

from __future__ import annotations
import requests
import pandas as pd


def _fetch_twse_daily() -> list[dict]:
    """抓取台灣證交所（上市）最新一日全市場收盤行情"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    rows = []
    for item in resp.json():
        code = item.get("Code", "").strip()
        if len(code) != 4 or not code.isdigit() or code.startswith("00"):
            continue
        try:
            close = float(item["ClosingPrice"])
            open_p = float(item["OpeningPrice"])
            change = float(item["Change"])
            volume = int(item["TradeVolume"]) // 1000
            prev_close = close - change
            pct = (change / prev_close * 100) if prev_close > 0 else 0.0
            rows.append({
                "code": code,
                "name": item.get("Name", "").strip(),
                "market": "TWSE",
                "close": close,
                "open": open_p,
                "pct": pct,
                "volume": volume,
            })
        except (ValueError, TypeError):
            continue
    return rows


def _fetch_tpex_daily() -> list[dict]:
    """抓取櫃買中心（上櫃）最新一日全市場收盤行情"""
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    rows = []
    for item in resp.json():
        code = item.get("SecuritiesCompanyCode", "").strip()
        if len(code) != 4 or not code.isdigit() or code.startswith("00"):
            continue
        try:
            close = float(item["Close"])
            open_p = float(item["Open"])
            change = float(item["Change"])
            volume = int(item["TradingShares"]) // 1000
            prev_close = close - change
            pct = (change / prev_close * 100) if prev_close > 0 else 0.0
            rows.append({
                "code": code,
                "name": item.get("CompanyName", "").strip(),
                "market": "TPEX",
                "close": close,
                "open": open_p,
                "pct": pct,
                "volume": volume,
            })
        except (ValueError, TypeError):
            continue
    return rows


def scan_dynamic_candidates(
    min_volume: int = 1500,
    min_price: float = 15.0,
    max_price: float = 1500.0,
    min_pct: float = 2.0,
    max_pct: float = 8.5,
    top_n: int = 40,
) -> list[dict]:
    """執行第一階段全市場粗篩，回傳最具動能的前 N 檔候選股"""
    all_stocks = []
    try:
        all_stocks.extend(_fetch_twse_daily())
    except Exception as e:
        print(f"⚠️ 上市行情抓取異常: {e}")

    try:
        all_stocks.extend(_fetch_tpex_daily())
    except Exception as e:
        print(f"⚠️ 上櫃行情抓取異常: {e}")

    if not all_stocks:
        return []

    df = pd.DataFrame(all_stocks)

    cond = (
        (df["volume"] >= min_volume)
        & (df["close"] >= min_price)
        & (df["close"] <= max_price)
        & (df["pct"] >= min_pct)
        & (df["pct"] <= max_pct)
        & (df["close"] > df["open"])
    )
    filtered = df[cond].copy()
    filtered["momentum_score"] = filtered["volume"] * filtered["pct"]
    filtered = filtered.sort_values("momentum_score", ascending=False).head(top_n)

    return filtered.to_dict("records")


def format_market_breakout_top5(exclude_codes: set[str] | None = None, top_n: int = 5) -> str:
    """產出「今日全市場突圍 TOP 5」純文字 Telegram 推播區塊（自動排除既有觀察名單標的）"""
    exclude = exclude_codes or set()
    candidates = scan_dynamic_candidates(
        min_volume=1500,
        min_price=15.0,
        max_price=1500.0,
        min_pct=2.0,
        max_pct=8.5,
        top_n=25,
    )

    fresh_picks = [c for c in candidates if str(c["code"]) not in exclude][:top_n]

    lines = [
        "\n\n━━━━━━━━━━━━━━━━━━",
        "🔥 今日全市場突圍 TOP 5（觀察池外黑馬）",
        "━━━━━━━━━━━━━━━━━━",
    ]

    if not fresh_picks:
        lines.append("  • 今日全市場無符合量價動能條件之新突圍標的")
        return "\n".join(lines)

    for idx, item in enumerate(fresh_picks, start=1):
        mkt_tag = "上市" if item["market"] == "TWSE" else "上櫃"
        lines.append(
            f"  {idx}. {item['code']} {item['name']}（{mkt_tag}）\n"
            f"     收盤 {item['close']:.2f}（{item['pct']:+.2f}%）｜量 {item['volume']:,} 張"
        )

    lines.append("※ 篩選條件：量 ≥ 1,500 張、漲幅 2%~8.5%、實體紅 K 動能排序")
    return "\n".join(lines)
