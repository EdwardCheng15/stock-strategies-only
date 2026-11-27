import base64, json, os, pathlib, urllib.request
from typing import Any
from .data import get_fundamental, get_price_history
from .sector_router import classify_stock_archetype
from .portfolio_guard import check_holding_health, load_positions, save_positions
from .risk import stop_pct_for_prices

ARCH_SL = {"archetype_growth": 0.08, "archetype_turnaround": 0.07, "archetype_value": 0.05, "archetype_etf": 0.12}
def _owner_tokens() -> tuple[str, ...]:
    """本人帳戶標記（戶名/帳號），從 .env 的 OWNER_MARKERS 讀取，以逗號分隔。
    未設定時回傳空 tuple → sync 一律降級為純健檢（fail-closed）。"""
    raw = os.environ.get("OWNER_MARKERS", "")
    return tuple(t.strip() for t in raw.split(",") if t.strip())
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

def _position_stop_pct(pos: dict[str, Any], old: dict[str, Any], px, archetype: str) -> float:
    """持倉停損幅度：ETF 固定 12%；個股沿用已存的 stop_pct，沒有才依 ATR 計算一次並存起來（之後固定不變）。"""
    if archetype == "archetype_etf":
        return ARCH_SL["archetype_etf"]
    for src in (pos, old):
        try:
            v = float(src.get("stop_pct") or 0)
            if 0 < v < 1:
                return v
        except (TypeError, ValueError):
            pass
    return stop_pct_for_prices(px)


def audit_positions(positions: list[dict[str, Any]], save: bool = False, header_note: str = "") -> str:
    if not positions: return "📭 未解析到有效持倉。"
    old_map = {str(p.get("stock_id", "")).strip(): p for p in load_positions()}
    cap = float(os.environ.get("TOTAL_CAPITAL", "200000") or 200000)
    lines = [f"🩺 *持倉移動停利健檢報告*\n{'✅ 已同步雲端庫存 (Upsert)' if save else '👀 純健檢模式（未異動庫存）'}"]
    if header_note: lines.append(header_note)
    merged, items_val, total_val, total_mv = dict(old_map), [], 0.0, 0.0
    for pos in positions:
        sid, name, cost, shares = str(pos["stock_id"]).strip(), pos.get("name", ""), float(pos["avg_cost"]), int(pos.get("shares", 0))
        try:
            px = get_price_history(sid, years=1)
            cs = px["close"].astype(float)
            c_px, ma10, ma20, ma60 = float(cs.iloc[-1]), float(cs.tail(10).mean()), float(cs.tail(20).mean()), float(cs.tail(60).mean())
            is_etf = sid.startswith("00")
            if is_etf:
                st, lbl = "archetype_etf", "指數ETF配置 (季線/12%寬幅防護)"
            else:
                st, lbl, _ = classify_stock_archetype(px, get_fundamental(sid))
            peak = max(float(old_map.get(sid, {}).get("peak_price", 0) or 0), cost, c_px)
            old = old_map.get(sid, {})
            sl = _position_stop_pct(pos, old, px, st)
            upd = {**pos, "archetype": st, "peak_price": round(peak, 2), "stop_pct": round(sl, 4)}
            merged[sid] = upd
            h = check_holding_health(upd, c_px, ma20 if is_etf else ma10, ma60 if is_etf else ma20, sl_pct=sl)
            if h["pnl_pct"] <= -15.0:
                low20 = float(cs.tail(20).min())
                h["advice"] = f"🟡 深套反彈站上月線({ma20:.2f})，可分批減碼降成本" if c_px >= ma20 else f"🟣 歷史深套整理（月線反壓 {ma20:.2f}｜破20日低 {low20:.2f} 停損）"
            mv = c_px * shares if shares > 0 else 0.0
            cv = cost * shares if shares > 0 else 0.0  # 投入成本：水位與集中度以此計算，不隨每日股價波動
            total_val += cv
            total_mv += mv
            items_val.append((sid, name, is_etf, cv))
            lines.append(f"• *{sid} {name}* ({f'{shares:,}股 | ' if shares > 0 else ''}成本 {cost:.2f} → 現價 {c_px:.2f} | {h['pnl_pct']:+.1f}%)\n  基因：{lbl.replace('nan%', 'N/A')}（波段高 {peak:.2f}）\n  {h['advice']}")
        except Exception as e:
            lines.append(f"• *{sid} {name}*：⚠️ 健檢失敗 ({e})")
    if total_val > 0 and cap > 0:
        expo_pct = total_val / cap * 100
        conc = [f"{s}({v/total_val*100:.0f}%)" for s, _, etf, v in items_val if not etf and v / total_val > 0.25]
        lines.append(f"\n💰 *資金水位（以投入成本計）*：成本 ${total_val:,.0f} / 總資金 ${cap:,.0f}（水位 *{expo_pct:.1f}%*，上限 70% 剩餘 ${max(0, cap*0.7-total_val):,.0f}）")
        lines.append(f"📊 目前市值 ${total_mv:,.0f}（未實現損益 {total_mv-total_val:+,.0f}）")
        if conc: lines.append(f"⚠️ *單一個股集中度過高(>25%)*：{'、'.join(conc)}")
    if save: save_positions(list(merged.values()))
    return "\n".join(lines)

def audit_from_screenshot(image_path: str | pathlib.Path, want_sync: bool = False) -> str:
    header, positions = parse_broker_screenshot(image_path)
    if want_sync and not any(t in header for t in _owner_tokens()):
        return audit_positions(positions, save=False, header_note=f"⚠️ 帳戶「{header or '未識別'}」非本人，已轉為純健檢模式。")
    return audit_positions(positions, save=want_sync, header_note=(f"🔐 帳戶驗證通過：{header}" if want_sync and header else ""))

def reduce_positions(tokens: list[str]) -> str:
    cur = load_positions()
    pos_map = {str(p.get("stock_id", "")).strip(): dict(p) for p in cur}
    logs, i = [], 0
    while i < len(tokens):
        sid = tokens[i].strip()
        qty = int(tokens[i + 1]) if (i + 1 < len(tokens) and tokens[i + 1].isdigit()) else None
        i += 2 if qty is not None else 1
        if sid not in pos_map:
            logs.append(f"⚠️ 找不到 {sid}")
            continue
        old_sh = int(pos_map[sid].get("shares", 0))
        if qty is None or old_sh - qty <= 0:
            del pos_map[sid]
            logs.append(f"🗑️ {sid} 已全數清倉移除")
        else:
            pos_map[sid]["shares"] = old_sh - qty
            logs.append(f"📉 {sid} 減碼 {qty:,} 股（剩餘 {old_sh - qty:,} 股）")
    save_positions(list(pos_map.values()))
    return "\n".join(logs or ["用法：/sold 代號 [股數]，例如 /sold 0050 500"]) + f"\n📦 目前庫存共 {len(pos_map)} 檔"

def buy_positions(tokens: list[str]) -> str:
    if len(tokens) < 3 or len(tokens) % 3 != 0:
        return "用法：/buy 代號 股數 成交價（可多筆），例如：/buy 2330 100 1050"
    cur = load_positions()
    pos_map = {str(p.get("stock_id", "")).strip(): dict(p) for p in cur}
    logs = []
    for i in range(0, len(tokens), 3):
        sid = tokens[i].strip()
        try:
            qty, px = int(float(tokens[i + 1])), float(tokens[i + 2])
            if qty <= 0 or px <= 0: raise ValueError
        except ValueError:
            logs.append(f"⚠️ {sid} 參數格式錯誤（股數與價格須大於0）")
            continue
        if sid in pos_map:
            old_sh = int(pos_map[sid].get("shares", 0))
            old_cost = float(pos_map[sid].get("avg_cost", 0.0))
            new_sh = old_sh + qty
            new_cost = round(((old_sh * old_cost) + (qty * px)) / new_sh, 2)
            peak = max(float(pos_map[sid].get("peak_price", 0) or 0), px)
            pos_map[sid].update({"shares": new_sh, "avg_cost": new_cost, "peak_price": round(peak, 2)})
            logs.append(f"📈 *{sid} {pos_map[sid].get('name', '')}* 加碼 {qty:,} 股 @ {px:.2f}\n  → 總股數 {new_sh:,} 股｜新均價 {new_cost:.2f}")
        else:
            name = ""
            try:
                fund = get_fundamental(sid)
                if isinstance(fund, dict): name = str(fund.get("stock_name", "") or fund.get("name", "")).strip()
            except Exception: pass
            stop_pct = None
            if not sid.startswith("00"):
                try:
                    stop_pct = stop_pct_for_prices(get_price_history(sid, years=1))
                except Exception:
                    stop_pct = None
            pos_map[sid] = {"stock_id": sid, "name": name, "shares": qty, "avg_cost": round(px, 2), "peak_price": round(px, 2)}
            if stop_pct:
                pos_map[sid]["stop_pct"] = round(stop_pct, 4)
            stop_txt = f"｜停損 {px*(1-stop_pct):.2f}（-{stop_pct*100:.1f}%，依波動率）" if stop_pct else ""
            logs.append(f"🆕 *{sid} {name}* 建倉 {qty:,} 股 @ {px:.2f}{stop_txt}")
    save_positions(list(pos_map.values()))
    cap = float(os.environ.get("TOTAL_CAPITAL", "200000") or 200000)
    est_val = sum(int(p.get("shares", 0)) * float(p.get("avg_cost") or 0) for p in pos_map.values())
    warn = f"\n⚠️ 投入成本水位 {est_val/cap*100:.1f}% 已超過 70% 上限！" if cap > 0 and est_val > cap * 0.7 else ""
    return "\n".join(logs) + f"\n📦 目前庫存共 {len(pos_map)} 檔{warn}"
