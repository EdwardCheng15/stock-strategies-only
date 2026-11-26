"""持倉移動停利健檢（Trailing Stop）與投資組合集中度風控模組"""

from __future__ import annotations
import json
import pathlib
from typing import Any

POSITIONS_FILE = pathlib.Path("positions.json")


def load_positions(path: pathlib.Path = POSITIONS_FILE) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("positions", [])
    except Exception:
        return []


def save_positions(positions: list[dict[str, Any]], path: pathlib.Path = POSITIONS_FILE) -> None:
    path.write_text(json.dumps(positions, indent=2, ensure_ascii=False), encoding="utf-8")


def check_holding_health(
    pos: dict[str, Any],
    close_px: float,
    ma10: float,
    ma20: float,
    sl_pct: float = 0.08,
    breakeven_trigger_pct: float = 0.06,
) -> dict[str, Any]:
    """單檔持倉移動停利與均線健檢"""
    cost = float(pos.get("avg_cost", 0) or 0)
    if cost <= 0 or close_px <= 0:
        return {"status": "UNKNOWN", "pnl_pct": 0.0, "advice": "成本或收盤價無效"}

    pnl_pct = (close_px / cost - 1.0) * 100.0
    peak_px = max(float(pos.get("peak_price", cost) or cost), close_px)
    init_stop = cost * (1.0 - sl_pct)

    # 獲利達 +8% (1R) 後，停損上移至成本價（保本點）
    breakeven_active = peak_px >= cost * (1.0 + breakeven_trigger_pct)
    active_stop = cost if breakeven_active else init_stop

    if close_px <= active_stop:
        tag = "保本停利出場" if breakeven_active else "觸及停損出場"
        return {
            "status": "EXIT",
            "pnl_pct": round(pnl_pct, 2),
            "active_stop": round(active_stop, 2),
            "advice": f"🔴 {tag}（現價 {close_px} <= 防守價 {active_stop:.2f}）",
        }

    if breakeven_active and close_px < ma10:
        return {
            "status": "TRIM",
            "pnl_pct": round(pnl_pct, 2),
            "active_stop": round(active_stop, 2),
            "advice": f"🟠 獲利回吐跌破 10 日線（{ma10:.2f}），建議分批停利",
        }

    if close_px < ma20:
        return {
            "status": "WARN",
            "pnl_pct": round(pnl_pct, 2),
            "active_stop": round(active_stop, 2),
            "advice": f"🟡 跌破月線（{ma20:.2f}），留意轉弱風險",
        }

    mode = "保本啟動" if breakeven_active else "初始風控"
    return {
        "status": "HOLD",
        "pnl_pct": round(pnl_pct, 2),
        "active_stop": round(active_stop, 2),
        "advice": f"🟢 趨勢健康續抱（{mode}防守價 {active_stop:.2f}）",
    }


def allocate_buy_signals(
    buy_results: list[dict[str, Any]],
    current_positions: list[dict[str, Any]],
    total_capital: float = 1_000_000.0,
    max_total_exposure_pct: float = 70.0,
    max_archetype_exposure_pct: float = 35.0,
) -> list[dict[str, Any]]:
    """依據既有倉位計算剩餘可配置曝險，防止同題材過度集中"""
    used_val = sum(float(p.get("shares", 0)) * float(p.get("avg_cost", 0)) for p in current_positions)
    used_pct = (used_val / total_capital * 100.0) if total_capital > 0 else 0.0
    rem_total_pct = max(0.0, max_total_exposure_pct - used_pct)

    arch_used: dict[str, float] = {}
    for p in current_positions:
        arch = str(p.get("archetype", "archetype_growth"))
        val = float(p.get("shares", 0)) * float(p.get("avg_cost", 0))
        arch_used[arch] = arch_used.get(arch, 0.0) + (val / total_capital * 100.0 if total_capital > 0 else 0.0)

    for r in buy_results:
        arch = str(r.get("strategy_id", "archetype_growth"))
        raw_pos = float(r.get("position_size_pct", 15.0))
        rem_arch = max(0.0, max_archetype_exposure_pct - arch_used.get(arch, 0.0))
        allowed = round(min(raw_pos, rem_total_pct, rem_arch), 1)
        r["allocated_size_pct"] = allowed
        if allowed < raw_pos:
            r.setdefault("risk_notes", []).append(
                f"組合風控調降部位 {raw_pos:.1f}%→{allowed:.1f}%（剩餘總額度 {rem_total_pct:.0f}%）"
            )
        rem_total_pct = max(0.0, rem_total_pct - allowed)
        arch_used[arch] = arch_used.get(arch, 0.0) + allowed

    return buy_results
