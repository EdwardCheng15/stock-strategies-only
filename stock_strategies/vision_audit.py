import base64, json, os, pathlib, urllib.request
from typing import Any
from .data import get_fundamental, get_price_history
from .sector_router import classify_stock_archetype
from .portfolio_guard import check_holding_health, save_positions

ARCH_SL = {"archetype_growth": 0.08, "archetype_turnaround": 0.07, "archetype_value": 0.05}

PROMPT = (
    "請解析這張台灣證券 App 庫存或持倉損益截圖，萃取出所有股票或 ETF 持倉。"
    "只回傳純 JSON 陣列（不要 Markdown 標記），每個元素格式為："
    "{\"stock_id\": \"代號(如0050或2330)\", \"name\": \"名稱\", \"shares\": 整數股數, \"avg_cost\": 浮點數平均成本價}。"
    "若顯示張數請換算為股數（1張=1000股）。"
)

def parse_broker_screenshot(image_path: str | pathlib.Path) -> list[dict[str, Any]]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("未設定 GEMINI_API_KEY")
    img_path = pathlib.Path(image_path)
    mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
    b64_img = base64.b64encode(img_path.read_bytes()).decode("utf-8")
    payload = {"contents": [{"parts": [{"text": PROMPT}, {"inline_data": {"mime_type": mime, "data": b64_img}}]}]}
    raw_text, last_err = "", ""
    for m in ("gemini-2.5-flash-lite", "gemini-3-flash-preview", "gemini-flash-latest"):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if raw_text:
                break
        except Exception as e:
            last_err = f"{m}: {e}"
    if not raw_text:
        raise RuntimeError(f"Gemini Vision 失敗 ({last_err})")
    raw_text = raw_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    items = json.loads(raw_text)
    return [
        {"stock_id": str(it["stock_id"]).strip(), "name": str(it.get("name", "")).strip(),
         "shares": int(float(it.get("shares", 0))), "avg_cost": float(it.get("avg_cost", 0.0))}
        for it in items if str(it.get("stock_id", "")).strip() and float(it.get("avg_cost", 0)) > 0
    ]

def audit_positions(positions: list[dict[str, Any]], save: bool = True) -> str:
    if not positions:
        return "📭 目前沒有任何持倉紀錄。"
    lines = ["🩺 *持倉移動停利健檢報告*"]
    enriched = []
    for pos in positions:
        sid, name = str(pos["stock_id"]).strip(), pos.get("name", "")
        cost, shares = float(pos["avg_cost"]), int(pos.get("shares", 0))
        try:
            px = get_price_history(sid, years=1)
            fund = get_fundamental(sid)
            close_s = px["close"].astype(float)
            close_px, ma10, ma20 = float(close_s.iloc[-1]), float(close_s.tail(10).mean()), float(close_s.tail(20).mean())
            strat_name, arch_label, _ = classify_stock_archetype(px, fund)
            sl_pct = ARCH_SL.get(strat_name, 0.08)
            peak_px = max(float(pos.get("peak_price", cost) or cost), close_px)
            pos_upd = {**pos, "archetype": strat_name, "peak_price": round(peak_px, 2)}
            enriched.append(pos_upd)
            h = check_holding_health(pos_upd, close_px, ma10, ma20, sl_pct=sl_pct)
            lines.append(f"• *{sid} {name}* ({shares:,}股 | 成本 {cost:.2f} → 現價 {close_px:.2f} | {h["pnl_pct"]:+.1f}%)")
            lines.append(f"  基因：{arch_label}\n  {h["advice"]}")
        except Exception as e:
            enriched.append(pos)
            lines.append(f"• *{sid} {name}*：⚠️ 健檢失敗 ({e})")
    if save:
        save_positions(enriched)
    return "\n".join(lines)

def audit_from_screenshot(image_path: str | pathlib.Path) -> str:
    return audit_positions(parse_broker_screenshot(image_path), save=True)
