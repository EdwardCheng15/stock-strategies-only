import base64, json, os, pathlib, urllib.request
from typing import Any
from .data import get_fundamental, get_price_history
from .sector_router import classify_stock_archetype
from .portfolio_guard import check_holding_health, load_positions, save_positions

ARCH_SL = {"archetype_growth": 0.08, "archetype_turnaround": 0.07, "archetype_value": 0.05}
OWNER_TOKENS = ("戴淯澤", "0323967")
PROMPT = "解析台灣證券截圖，只回傳純JSON物件：{\"account_header\":\"上方帳號或戶名(無則空字串)\",\"positions\":[{\"stock_id\":\"代號\",\"name\":\"名稱\",\"shares\":整數股數,\"avg_cost\":成本價}]}。1張=1000股，無股數填0。"

def parse_broker_screenshot(image_path: str | pathlib.Path) -> tuple[str, list[dict[str, Any]]]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
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
                raw_text = json.loads(resp.read().decode("utf-8"))["candidates"][0]["content"]["parts"][0]["text"].strip()
                break
        except Exception as e:
            last_err = f"{m}: {e}"
    if not raw_text: raise RuntimeError(f"Vision失敗: {last_err}")
    parsed = json.loads(raw_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip())
    items = parsed.get("positions", parsed) if isinstance(parsed, dict) else parsed
    cleaned = [{"stock_id": str(i["stock_id"]).strip(), "name": str(i.get("name", "")).strip(), "shares": int(float(i.get("shares", 0))), "avg_cost": float(i.get("avg_cost", 0))} for i in items if str(i.get("stock_id", "")).strip() and float(i.get("avg_cost", 0)) > 0]
    return (str(parsed.get("account_header", "")).strip() if isinstance(parsed, dict) else ""), cleaned

def audit_positions(positions: list[dict[str, Any]], save: bool = False, header_note: str = "") -> str:
    if not positions: return "📭 未解析到有效持倉。"
    old_map = {str(p.get("stock_id", "")).strip(): p for p in load_positions()}
    lines = [f"🩺 *持倉移動停利健檢報告*\n{'✅ 已同步雲端庫存 (Upsert)' if save else '👀 純健檢模式（未異動庫存）'}"]
    if header_note: lines.append(header_note)
    merged = dict(old_map)
    for pos in positions:
        sid, name, cost, shares = str(pos["stock_id"]).strip(), pos.get("name", ""), float(pos["avg_cost"]), int(pos.get("shares", 0))
        try:
            px, fund = get_price_history(sid, years=1), get_fundamental(sid)
            cs = px["close"].astype(float)
            c_px, ma10, ma20 = float(cs.iloc[-1]), float(cs.tail(10).mean()), float(cs.tail(20).mean())
            st, lbl, _ = classify_stock_archetype(px, fund)
            peak = max(float(old_map.get(sid, {}).get("peak_price", 0) or 0), cost, c_px)
            upd = {**pos, "archetype": st, "peak_price": round(peak, 2)}
            merged[sid] = upd
            h = check_holding_health(upd, c_px, ma10, ma20, sl_pct=ARCH_SL.get(st, 0.08))
            lines.append(f"• *{sid} {name}* ({'f{shares:,}股 | ' if shares > 0 else ''}成本 {cost:.2f} → 現價 {c_px:.2f} | {h['pnl_pct']:+.1f}%)\n  基因：{lbl}\n  {h['advice']}")
        except Exception as e:
            lines.append(f"• *{sid} {name}*：⚠️ 健檢失敗 ({e})")
    if save: save_positions(list(merged.values()))
    return "\n".join(lines)

def audit_from_screenshot(image_path: str | pathlib.Path, want_sync: bool = False) -> str:
    header, positions = parse_broker_screenshot(image_path)
    if want_sync and not any(t in header for t in OWNER_TOKENS):
        return audit_positions(positions, save=False, header_note=f"⚠️ 帳戶「{header or '未識別'}」非本人，已轉為純健檢模式。")
    return audit_positions(positions, save=want_sync, header_note=(f"🔐 帳戶驗證通過：{header}" if want_sync and header else ""))
