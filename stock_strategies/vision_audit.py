"""券商庫存截圖識別與持倉健檢報告產生器"""

from __future__ import annotations
import base64
import json
import os
import pathlib
import urllib.request
from typing import Any

from .data import get_fundamental, get_price_history
from .sector_router import classify_stock_archetype
from .portfolio_guard import check_holding_health, save_positions

ARCH_SL = {
    "archetype_growth": 0.08,
    "archetype_turnaround": 0.07,
    "archetype_value": 0.05,
}


def parse_broker_screenshot(image_path: str | pathlib.Path) -> list[dict[str, Any]]:
    """透過 Gemini Vision API 解析券商庫存截圖，回傳標準化持倉清單"""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("請先設定環境變數 GEMINI_API_KEY 以啟用截圖識別")

    img_path = pathlib.Path(image_path)
    mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
    b64_img = base64.b64encode(img_path.read_bytes()).decode("utf-8")

    prompt = (
        "請解析這張台灣證券 App 庫存或持倉損益截圖，萃取出所有股票持倉。"
        "只回傳純 JSON 陣列（不要 Markdown 標記），每個元素格式為："
        '{"stock_id": "4位數代號", "name": "股票名稱", "shares": 整數股數, "avg_cost": 浮點數平均成本價}。'
        "若截圖顯示張數（例如 2 張），請換算為股數（2000）。"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": b64_img}},
            ]
        }]
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    raw_text = raw_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    items = json.loads(raw_text)
    return [
        {
            "stock_id": str(it["stock_id"]).strip(),
            "name": str(it.get("name", "")).strip(),
            "shares": int(float(it.get("shares", 0))),
            "avg_cost": float(it.get("avg_cost", 0.0)),
        }
        for it in items
        if str(it.get("stock_id", "")).strip() and float(it.get("avg_cost", 0)) > 0
    ]


def audit_positions(positions: list[dict[str, Any]], save: bool = True) -> str:
    """對持倉清單執行行為基因分類與移動停利健檢，並回傳 Telegram 報告字串"""
    if not positions:
        return "📭 目前沒有任何持倉紀錄。"

    lines = ["🩺 *持倉移動停利健檢報告*"]
    enriched: list[dict[str, Any]] = []

    for pos in positions:
        sid = str(pos["stock_id"]).strip()
        name = pos.get("name", sid)
        cost = float(pos["avg_cost"])
        shares = int(pos.get("shares", 0))

        try:
            px = get_price_history(sid, years=1)
            fund = get_fundamental(sid)
            close_s = px["close"].astype(float)
            close_px = float(close_s.iloc[-1])
            ma10 = float(close_s.tail(10).mean())
            ma20 = float(close_s.tail(20).mean())

            strat_name, arch_label, _ = classify_stock_archetype(px, fund)
            sl_pct = ARCH_SL.get(strat_name, 0.08)
            peak_px = max(float(pos.get("peak_price", cost) or cost), close_px)

            pos_updated = {
                **pos,
                "archetype": strat_name,
                "peak_price": round(peak_px, 2),
            }
            enriched.append(pos_updated)

            health = check_holding_health(pos_updated, close_px, ma10, ma20, sl_pct=sl_pct)
            lines.append(
                f"• *{sid} {name}* ({shares:,}股 | 成本 {cost:.2f} → 現價 {close_px:.2f} | {health['pnl_pct']:+.1f}%)"
            )
            lines.append(f"  基因：{arch_label}")
            lines.append(f"  {health['advice']}")
        except Exception as e:
            enriched.append(pos)
            lines.append(f"• *{sid} {name}*：⚠️ 健檢失敗 ({e})")

    if save:
        save_positions(enriched)

    return "\n".join(lines)


def audit_from_screenshot(image_path: str | pathlib.Path) -> str:
    """一鍵從截圖匯入 positions.json 並回傳完整健檢報告"""
    positions = parse_broker_screenshot(image_path)
    return audit_positions(positions, save=True)
