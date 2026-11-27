"""策略檔載入 / 驗證 / 儲存

策略以 strategies/<id>.json 形式存放。本模組負責：
- 讀取單一策略或全部策略
- 寫入新策略 / 更新現有策略 / 刪除
- 把策略的 params 與預設 CONFIG 合併成扁平 dict，給 evaluate / backtest 使用
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import CONFIG

# 策略目錄：環境變數可覆寫（部署時方便）
STRATEGY_DIR = Path(
    os.environ.get(
        "STRATEGY_DIR",
        str(Path(__file__).resolve().parent.parent / "strategies"),
    )
)

# 允許出現在 params 內的鍵 → 預設值
_PARAM_DEFAULTS: dict = {
    # 基本面
    "eps_threshold": CONFIG["eps_threshold"],
    "roe_threshold": CONFIG["roe_threshold"],
    "fundamental_pass_required": True,
    # 回測
    "backtest_years": CONFIG["backtest_years"],
    "hold_days": CONFIG["hold_days"],
    "min_tech_score_for_signal": CONFIG["min_tech_score_for_signal"],
    # 風險
    "target_return": CONFIG["target_return"],
    "stop_loss": CONFIG["stop_loss"],
    # 評分加權
    "weight_fundamental": 0.3,
    "weight_technical": 0.3,
    "weight_backtest": 0.4,
    "min_total_score_for_buy": CONFIG["min_total_score_for_buy"],
    "min_tech_score_for_buy": 50,
    # 技術訊號開關
    "use_ma_alignment": True,
    "use_bollinger_bounce": True,
    "use_kd_golden_cross": True,
    "use_macd_bullish": True,
    "use_volume_patterns": True,
    # 大盤濾鏡
    "market_filter_enabled": True,
    "market_filter_ma_period": 20,
}

# 進階參數：策略 JSON 有寫就生效，沒寫就用預設（預設值等同關閉或 V3.4 濾網標準）。
# 獨立於 _PARAM_DEFAULTS，因為 v34_factors 是 dict，不適用 validate_strategy 的純數值檢查。
_EXTRA_PARAM_DEFAULTS: dict = {
    # V3.4 多因子引擎
    "v34_factors": None,          # {"因子名": 權重}
    "v34_weight": 0.0,            # 多因子分數占總分比重（0 = 關閉）
    # 防追高
    "max_ma5_bias_pct": 0.0,      # 收盤距 5 日均線乖離上限 %（0 = 關閉）
    "max_gap_up_pct": 0.0,        # 回測時隔日跳空開高上限（0 = 關閉）
    # V3.4 BUY 放行濾網
    "min_backtest_winrate": 0.52, # 歷史勝率下限
    "min_backtest_samples": 10,   # 勝率需至少這麼多筆「不重疊」回測交易才採信（3 年最多約 35 筆）
    "max_chg_5d_pct": 15.0,       # 5 日漲幅上限 %（防隔日追高）
    "target_annual_vol": 0.35,    # 波動率倉位：年化波動超過此值時按比例縮小建議部位
    # 回測
    "round_trip_cost": 0.0047,    # 來回交易成本（手續費 6 折 + 證交稅）
    "exit_mode": "trailing",      # trailing = 模擬實盤移動停利（預設，與持倉健檢一致）；fixed = 固定持有＋停損停利
    "breakeven_trigger_pct": 0.06,# trailing：波段高點達 +6% 後停損上移到成本
    "max_hold_days": 120,         # trailing：最長持有天數
    "exit_ma_days": 20,           # trailing：保本後收盤跌破此均線停利（月線版；帳戶模擬優於 10 日線）
    # 停損
    "stop_mode": "atr",           # atr = 依波動率（預設）；fixed = 固定 stop_loss
    "atr_stop_mult": 2.5,         # 停損 = 2.5 × ATR14 ÷ 收盤
    "stop_min_pct": 0.04,         # ATR 停損下限
    "stop_max_pct": 0.15,         # ATR 停損上限
}


class StrategyError(ValueError):
    """策略驗證失敗"""


_WRITE_LOCK = threading.Lock()
_BOUNDS = {
    "backtest_years": (1, 10), "hold_days": (1, 120),
    "target_return": (0.01, 0.5), "stop_loss": (0.01, 0.5),
    "min_total_score_for_buy": (0, 100), "min_tech_score_for_buy": (0, 100),
    "min_tech_score_for_signal": (0, 100), "market_filter_ma_period": (5, 120),
    "weight_fundamental": (0, 1), "weight_technical": (0, 1), "weight_backtest": (0, 1),
}


def _strategy_path(sid: str) -> Path:
    if not isinstance(sid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", sid):
        raise StrategyError("策略 ID 只能包含英數字、底線與連字號，長度 1–100")
    path = STRATEGY_DIR / f"{sid}.json"
    if path.is_symlink():
        raise StrategyError("策略檔不可為符號連結")
    return path


def _slugify(text: str) -> str:
    """產生策略 ID。保留 ASCII 英數字與底線；CJK 中文字會被丟掉，
    若清掉後變空字串就回 uuid。"""
    text = re.sub(r"[^a-zA-Z0-9-_]+", "-", text.strip().lower())
    text = text.strip("-")
    if len(text) < 3:
        return "s-" + uuid.uuid4().hex[:8]
    return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir() -> None:
    STRATEGY_DIR.mkdir(parents=True, exist_ok=True)


def merge_params(strategy: Optional[dict]) -> dict:
    """把策略的 params 蓋在預設值上，回傳扁平 dict。"""
    merged = {**_PARAM_DEFAULTS, **_EXTRA_PARAM_DEFAULTS}
    if not strategy:
        return merged
    params = strategy.get("params") or {}
    for k, v in params.items():
        if k in merged and v is not None:
            merged[k] = v
    # 健全性：權重總和不為 0
    total = (
        merged["weight_fundamental"]
        + merged["weight_technical"]
        + merged["weight_backtest"]
    )
    if total <= 0:
        merged["weight_fundamental"] = 0.3
        merged["weight_technical"] = 0.3
        merged["weight_backtest"] = 0.4
    return merged


def validate_strategy(data: dict) -> dict:
    """驗證並補齊一份策略 JSON，回傳乾淨版本（可寫入）"""
    if not isinstance(data, dict):
        raise StrategyError("策略必須是 JSON 物件")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise StrategyError("策略名稱必須為 1–120 字")
    name = name.strip()

    sid = data.get("id") if data.get("id") is not None else _slugify(name)[:80]
    _strategy_path(sid)
    source = data.get("source") or "manual"
    if source not in ("default", "manual", "ai"):
        source = "manual"

    raw_params = data.get("params", {})
    if not isinstance(raw_params, dict):
        raise StrategyError("params 必須是物件")

    clean_params: dict = {}
    for key, default_val in _PARAM_DEFAULTS.items():
        if key in raw_params and raw_params[key] is not None:
            v = raw_params[key]
            if isinstance(default_val, bool):
                if not isinstance(v, bool):
                    raise StrategyError(f"{key} 必須是布林值")
            else:
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise StrategyError(f"{key} 必須是有限數值")
                try:
                    finite = math.isfinite(v)
                except OverflowError:
                    finite = False
                if not finite:
                    raise StrategyError(f"{key} 必須是有限數值")
                if isinstance(default_val, int):
                    if v != int(v):
                        raise StrategyError(f"{key} 必須是整數")
                    v = int(v)
                else:
                    v = float(v)
                if key in _BOUNDS:
                    low, high = _BOUNDS[key]
                    if not low <= v <= high:
                        raise StrategyError(f"{key} 必須介於 {low} 與 {high}")
            clean_params[key] = v
        else:
            clean_params[key] = default_val

    for key in _EXTRA_PARAM_DEFAULTS:
        v = raw_params.get(key)
        if v is None:
            continue
        if key == "exit_mode":
            if v not in ("fixed", "trailing"):
                raise StrategyError("exit_mode 必須是 fixed 或 trailing")
            clean_params[key] = v
        elif key == "stop_mode":
            if v not in ("fixed", "atr"):
                raise StrategyError("stop_mode 必須是 fixed 或 atr")
            clean_params[key] = v
        elif key == "v34_factors":
            if not isinstance(v, dict) or not all(
                isinstance(fk, str) and isinstance(fw, (int, float)) and not isinstance(fw, bool) and math.isfinite(fw)
                for fk, fw in v.items()
            ):
                raise StrategyError("v34_factors 必須是 {因子名: 數值權重}")
            clean_params[key] = {fk: float(fw) for fk, fw in v.items()}
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise StrategyError(f"{key} 必須是有限數值")
            clean_params[key] = v

    if sum(clean_params[k] for k in ("weight_fundamental", "weight_technical", "weight_backtest")) <= 0:
        raise StrategyError("評分權重總和必須大於 0")
    description = data.get("description") or ""
    if not isinstance(description, str) or len(description) > 4000:
        raise StrategyError("策略說明必須是文字且不超過 4000 字")

    return {
        "id": sid,
        "name": name,
        "description": description.strip(),
        "source": source,
        "created_at": data.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
        "params": clean_params,
    }


def list_strategies() -> list[dict]:
    _ensure_dir()
    out = []
    for p in sorted(STRATEGY_DIR.glob("*.json")):
        try:
            strategy = get_strategy(p.stem)
            if strategy is not None:
                out.append(strategy)
        except Exception as e:
            out.append({"id": p.stem, "name": p.stem, "error": str(e)})
    return out


def get_strategy(sid: str) -> Optional[dict]:
    path = _strategy_path(sid)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        clean = validate_strategy(data)
        clean["id"] = sid
        clean["updated_at"] = data.get("updated_at") or clean["updated_at"]
        return clean
    except (ValueError, TypeError) as exc:
        raise StrategyError(f"策略 {sid} 無法讀取：{exc}") from exc


def save_strategy(data: dict) -> dict:
    """新增或覆寫一份策略。回傳乾淨版本。"""
    _ensure_dir()
    clean = validate_strategy(data)
    with _WRITE_LOCK:
        path = _strategy_path(clean["id"])
        if data.get("id") is None and path.exists():
            clean["id"] = f"{clean['id']}-{uuid.uuid4().hex[:8]}"
            path = _strategy_path(clean["id"])
        if path.exists():
            clean["created_at"] = get_strategy(clean["id"])["created_at"]
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=STRATEGY_DIR, suffix=".tmp", delete=False) as f:
                temporary = Path(f.name)
                json.dump(clean, f, ensure_ascii=False, indent=2, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return clean


def delete_strategy(sid: str) -> bool:
    path = _strategy_path(sid)
    if path.exists():
        path.unlink()
        return True
    return False


def param_defaults() -> dict:
    """讓 API / 前端拿預設值用"""
    return dict(_PARAM_DEFAULTS)


def ma_label(params: dict | None = None) -> str:
    """出場均線的中文名稱，例如 20 → 破月線。"""
    p = params or merge_params(None)
    n = int(p.get("exit_ma_days", 20))
    return {5: "破週線", 10: "破 10 日線", 20: "破月線", 60: "破季線"}.get(n, f"破 {n} 日線")


def exit_rule_text(params: dict | None = None) -> str:
    """推播用的出場規則簡述。"""
    p = params or merge_params(None)
    if p.get("exit_mode", "trailing") == "trailing":
        return f"出場：漲 +{float(p.get('breakeven_trigger_pct', 0.06))*100:.0f}% 後保本、{ma_label(p)}停利"
    return f"目標 +{float(p.get('target_return', 0.1))*100:.0f}%"
