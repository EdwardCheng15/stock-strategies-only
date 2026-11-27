"""美股領先指標、多層級風控矩陣與台股供應鏈連動分析模組"""

from __future__ import annotations
import yfinance as yf

SUPPLY_CHAIN_GROUPS = [
    {
        "name": "AI 伺服器核心",
        "tickers": {"NVDA": "輝達", "AMD": "超微"},
        "tw_stocks": {"2330": "台積電", "2317": "鴻海", "2382": "廣達", "3231": "緯創", "6669": "緯穎", "2376": "技嘉"},
    },
    {
        "name": "客製化晶片 ASIC",
        "tickers": {"AVGO": "博通"},
        "tw_stocks": {"2330": "台積電", "3661": "世芯-KY", "3443": "創意"},
    },
    {
        "name": "雲端 CSP 資本支出",
        "tickers": {"MSFT": "微軟", "AMZN": "亞馬遜", "GOOGL": "Google", "META": "Meta"},
        "tw_stocks": {"2382": "廣達", "3231": "緯創", "6669": "緯穎", "3017": "奇鋐", "3324": "雙鴻", "2308": "台達電"},
    },
    {
        "name": "蘋果供應鏈",
        "tickers": {"AAPL": "蘋果"},
        "tw_stocks": {"3008": "大立光", "2317": "鴻海", "2330": "台積電", "2474": "可成"},
    },
    {
        "name": "電動車供應鏈",
        "tickers": {"TSLA": "特斯拉"},
        "tw_stocks": {"1536": "和大", "3665": "貿聯-KY"},
    },
    {
        "name": "記憶體循環",
        "tickers": {"MU": "美光"},
        "tw_stocks": {"2408": "南亞科", "2344": "華邦電", "2337": "旺宏"},
    },
    {
        "name": "半導體設備",
        "tickers": {"AMAT": "應材", "ASML": "艾司摩爾", "LRCX": "科林研發"},
        "tw_stocks": {"2330": "台積電", "3413": "京鼎", "3583": "辛耘", "6196": "帆宣"},
    },
    {
        "name": "手機與晶圓代工",
        "tickers": {"INTC": "英特爾", "QCOM": "高通"},
        "tw_stocks": {"2330": "台積電", "2454": "聯發科"},
    },
]

MARKET_INDICES = {
    "^GSPC": "S&P 500",
    "^DJI": "道瓊",
    "^IXIC": "納斯達克",
    "^SOX": "費城半導體",
    "TSM": "台積電 ADR",
    "EWT": "台灣 ETF（EWT）",
    "^VIX": "恐慌指數 VIX",
}

# 半導體相關供應鏈：個股推估時以費半為產業指標，其餘以納斯達克為準
SEMI_GROUPS = {"AI 伺服器核心", "客製化晶片 ASIC", "記憶體循環", "半導體設備", "手機與晶圓代工"}


def _pct(stats: dict, sym: str) -> float | None:
    v = stats.get(sym, {}).get("pct")
    return float(v) if v is not None else None


def market_open_outlook(stats: dict[str, dict]) -> dict:
    """用美股推估台股今日開盤方向。EWT（在美國交易的台股 ETF）最直接，沒有時退回費半＋那指平均。"""
    ewt = _pct(stats, "EWT")
    if ewt is not None:
        est, basis = ewt, f"EWT {ewt:+.2f}%"
    else:
        parts = [p for p in (_pct(stats, "^SOX"), _pct(stats, "^IXIC")) if p is not None]
        if not parts:
            return {"est": None, "label": "⚪ 無美股資料", "basis": ""}
        est = sum(parts) / len(parts)
        basis = f"費半/那指平均 {est:+.2f}%"
    if est >= 1.0:
        label = "🔥 預估開高"
    elif est >= 0.3:
        label = "🟢 預估小漲"
    elif est > -0.3:
        label = "⚪ 預估平盤"
    elif est > -1.0:
        label = "🟠 預估小跌"
    else:
        label = "🔴 預估開低"
    return {"est": est, "label": label, "basis": basis}


def infer_stock_outlook(stock_id: str, stats: dict[str, dict]) -> dict:
    """依美股推估單檔台股今日方向。

    分數 = 0.5 × 連動美股平均漲跌 + 0.3 × 產業指數（半導體看費半、其餘看那指）+ 0.2 × EWT
    （沒有連動美股時，連動權重改給產業指數）。單位：%。
    回傳 {score, tag, level, detail}；level = strong_head | head | neutral | tail | strong_tail | unknown
    """
    code = str(stock_id).strip()
    linked, names, semi = [], [], False
    for g in SUPPLY_CHAIN_GROUPS:
        if code in g["tw_stocks"]:
            semi = semi or g["name"] in SEMI_GROUPS
            for sym, cname in g["tickers"].items():
                p = _pct(stats, sym)
                if p is not None and cname not in names:
                    linked.append(p)
                    names.append(f"{cname} {p:+.1f}%")
    sector_sym = "^SOX" if semi else "^IXIC"
    sector = _pct(stats, sector_sym)
    ewt = _pct(stats, "EWT")
    if sector is None and ewt is None and not linked:
        return {"score": None, "tag": "", "level": "unknown", "detail": ""}
    sector = sector if sector is not None else 0.0
    ewt = ewt if ewt is not None else sector
    if linked:
        link_avg = sum(linked) / len(linked)
        score = 0.5 * link_avg + 0.3 * sector + 0.2 * ewt
    else:
        link_avg = None
        score = 0.8 * sector + 0.2 * ewt
    worst_link = min(linked) if linked else 0.0
    if score <= -2.0 or worst_link <= -4.0:
        level, tag = "strong_head", "🔴 美股強逆風"
    elif score <= -0.7:
        level, tag = "head", "🟠 美股逆風"
    elif score >= 2.0:
        level, tag = "strong_tail", "🔥 美股強順風"
    elif score >= 0.7:
        level, tag = "tail", "🟢 美股順風"
    else:
        level, tag = "neutral", "⚪ 美股中性"
    detail_parts = names[:3] + [f"{'費半' if semi else '那指'} {sector:+.1f}%"]
    return {"score": round(score, 2), "tag": tag, "level": level, "detail": "、".join(detail_parts)}


def _evaluate_risk_levels(stats: dict[str, dict]) -> tuple[dict[str, str], list[str], bool]:
    """依據三級燈號矩陣與四大組合條件判定風險等級"""
    levels: dict[str, str] = {}  # normal | watch | warn | strong_warn

    # 1. TSM 判定（觀察 -2%, 警示 -3%, 強警示 -5%）
    tsm_pct = stats.get("TSM", {}).get("pct", 0.0)
    if tsm_pct <= -5.0:
        levels["TSM"] = "strong_warn"
    elif tsm_pct <= -3.0:
        levels["TSM"] = "warn"
    elif tsm_pct <= -2.0:
        levels["TSM"] = "watch"
    else:
        levels["TSM"] = "normal"

    # 2. SOX 判定（觀察 -2%, 警示 -3%, 強警示 -5%）
    sox_pct = stats.get("^SOX", {}).get("pct", 0.0)
    if sox_pct <= -5.0:
        levels["^SOX"] = "strong_warn"
    elif sox_pct <= -3.0:
        levels["^SOX"] = "warn"
    elif sox_pct <= -2.0:
        levels["^SOX"] = "watch"
    else:
        levels["^SOX"] = "normal"

    # 3. 納斯達克判定（觀察 -1%, 警示 -1.5%~-2%, 強警示 -3%）
    ixic_pct = stats.get("^IXIC", {}).get("pct", 0.0)
    if ixic_pct <= -3.0:
        levels["^IXIC"] = "strong_warn"
    elif ixic_pct <= -1.5:
        levels["^IXIC"] = "warn"
    elif ixic_pct <= -1.0:
        levels["^IXIC"] = "watch"
    else:
        levels["^IXIC"] = "normal"

    # 4. VIX 判定（觀察 +10% 或 >=20, 警示 +15~20% 或 >=25, 強警示 +30% 或 >=30）
    vix_close = stats.get("^VIX", {}).get("close", 15.0)
    vix_pct = stats.get("^VIX", {}).get("pct", 0.0)
    if vix_pct >= 30.0 or vix_close >= 30.0:
        levels["^VIX"] = "strong_warn"
    elif vix_pct >= 15.0 or vix_close >= 25.0:
        levels["^VIX"] = "warn"
    elif vix_pct >= 10.0 or vix_close >= 20.0:
        levels["^VIX"] = "watch"
    else:
        levels["^VIX"] = "normal"

    # 檢查四大組合條件（任一成立即觸發「今日電子權值避險，勿追開盤跳空」）
    triggered_reasons: list[str] = []

    # 條件 1：TSM 或 SOX 單日跌 3% 以上
    if tsm_pct <= -3.0:
        triggered_reasons.append(f"TSM 單日跌 {abs(tsm_pct):.2f}%（≥3%）")
    if sox_pct <= -3.0:
        triggered_reasons.append(f"SOX 單日跌 {abs(sox_pct):.2f}%（≥3%）")

    # 條件 2：納斯達克單日跌 2% 以上
    if ixic_pct <= -2.0:
        triggered_reasons.append(f"納斯達克單日跌 {abs(ixic_pct):.2f}%（≥2%）")

    # 條件 3：VIX 單日漲 20% 以上，或收盤站上 25
    if vix_pct >= 20.0 or vix_close >= 25.0:
        triggered_reasons.append(f"VIX 達 {vix_close:.1f}（單日 {vix_pct:+.1f}%）")

    # 條件 4：任兩項同時達到「觀察」級以上
    watch_or_above = [
        MARKET_INDICES[sym]
        for sym, lv in levels.items()
        if lv in ("watch", "warn", "strong_warn")
    ]
    if len(watch_or_above) >= 2:
        triggered_reasons.append(f"多項指標共振達觀察級以上（{' + '.join(watch_or_above)}）")

    is_strong_warn = any(lv == "strong_warn" for lv in levels.values())
    return levels, triggered_reasons, is_strong_warn


def _all_tickers() -> list[str]:
    tickers = list(MARKET_INDICES.keys())
    for g in SUPPLY_CHAIN_GROUPS:
        tickers.extend(g["tickers"].keys())
    return list(dict.fromkeys(tickers))


def fetch_us_stats() -> tuple[dict[str, dict], str]:
    """下載美股隔夜收盤，回傳 (stats, 錯誤訊息)。stats 為空代表抓取失敗。"""
    all_tickers = _all_tickers()
    try:
        df = yf.download(all_tickers, period="25d", progress=False, auto_adjust=True)["Close"]
    except Exception as e:
        return {}, f"美股資料抓取失敗: {e}"
    if df.empty or len(df) < 2:
        return {}, "美股收盤資料不足"
    stats: dict[str, dict] = {}
    for t in all_tickers:
        if t not in df.columns:
            continue
        series = df[t].dropna()
        if len(series) < 2:
            continue
        last_close = float(series.iloc[-1])
        prev_close = float(series.iloc[-2])
        ma20 = float(series.tail(20).mean()) if len(series) >= 20 else last_close
        stats[t] = {
            "close": last_close,
            "pct": (last_close - prev_close) / prev_close * 100,
            "above_ma20": last_close >= ma20,
        }
    return stats, ""


def get_us_risk(stats: dict[str, dict]) -> dict:
    """盤前確認用的美股風險等級：level = strong_warn | warn | normal | unknown。"""
    if not stats:
        return {"level": "unknown", "reasons": []}
    _, reasons, is_strong = _evaluate_risk_levels(stats)
    level = "strong_warn" if is_strong else ("warn" if reasons else "normal")
    return {"level": level, "reasons": reasons}


def get_us_market_summary(
    signals: list[dict] | None = None,
    threshold: float = 2.0,
    stats: dict[str, dict] | None = None,
) -> str:
    if stats is None:
        stats, err = fetch_us_stats()
        if not stats:
            return f"\n\n━━━━━━━━━━━━━━━━━━\n🇺🇸 美股連動快報\n⚠️ {err}"
    elif not stats:
        return "\n\n━━━━━━━━━━━━━━━━━━\n🇺🇸 美股連動快報\n⚠️ 美股資料暫時取不到"

    levels, triggered_reasons, is_strong_warn = _evaluate_risk_levels(stats)

    badge_map = {
        "normal": "",
        "watch": " [🟡觀察]",
        "warn": " [🟠警示]",
        "strong_warn": " [🔴強警示]",
    }

    lines = [
        "\n\n━━━━━━━━━━━━━━━━━━",
        "🇺🇸 美股隔夜風向 & 供應鏈連動",
        "━━━━━━━━━━━━━━━━━━",
        "【大盤與核心指標】",
    ]

    # 1. 大盤逐行條列 + 燈號標註
    for sym, label in MARKET_INDICES.items():
        if sym in stats:
            s = stats[sym]
            badge = badge_map[levels.get(sym, "normal")]
            if sym == "^VIX":
                lines.append(f"  • {label}：{s['close']:.2f} ({s['pct']:+.2f}%){badge}")
            else:
                icon = "🔺" if s["pct"] > 0 else "🔻"
                ma_tag = "站上20MA" if s["above_ma20"] else "跌破20MA"
                lines.append(f"  {icon} {label}：{s['pct']:+.2f}%（{ma_tag}）{badge}")

    # 2. 今日台股開盤定調（套用四大避險組合條件）
    ixic_pct = stats.get("^IXIC", {}).get("pct", 0.0)
    sox_pct = stats.get("^SOX", {}).get("pct", 0.0)

    lines.append("\n【今日台股開盤定調】")
    if is_strong_warn:
        lines.append("  🚨 強警示警報：今日電子權值嚴格避險，切勿追開盤跳空，嚴控部位與停損！")
        for r in triggered_reasons:
            lines.append(f"     └ 觸發條件：{r}")
    elif triggered_reasons:
        lines.append("  ⚠️ 警示觸發：今日電子權值避險，勿追開盤跳空（BUY 訊號建議暫緩或等拉回）")
        for r in triggered_reasons:
            lines.append(f"     └ 觸發條件：{r}")
    elif sox_pct >= 1.5 and ixic_pct >= 1.0:
        lines.append("  🔥 科技強攻：納斯達克與費半雙強且無風險警示，有利半導體與成長股突破")
    elif sox_pct >= 1.5 and ixic_pct < 0:
        lines.append("  🎯 資金集中晶片：費半獨強、納指偏弱，聚焦晶圓代工/AI伺服器，避開消費電子")
    else:
        lines.append("  ⚖️ 震盪輪動：未觸發系統性避險條件，依個股供應鏈強弱操作")

    # 3. 昨日觀察池 BUY/WATCH 標記
    active_tw_codes = set()
    if signals:
        def _act(x: dict) -> str:
            return str(x.get("action") or x.get("signal") or "").upper()
        latest_date = max((str(s.get("date", "")) for s in signals if _act(s) in ("BUY", "WATCH")), default="")
        for s in signals:
            if str(s.get("date")) == latest_date and _act(s) in ("BUY", "WATCH"):
                code = str(s.get("code") or s.get("stock_id") or "").strip()
                if code:
                    active_tw_codes.add(code)

    # 4. 供應鏈分類明細
    bullish_blocks = []
    bearish_blocks = []
    neutral_blocks = []

    for group in SUPPLY_CHAIN_GROUPS:
        ticker_strs = []
        max_up = 0.0
        min_down = 0.0
        for sym, cname in group["tickers"].items():
            if sym in stats:
                p = stats[sym]["pct"]
                max_up = max(max_up, p)
                min_down = min(min_down, p)
                mark = "🔥" if p >= threshold else ("❄️" if p <= -threshold else "")
                ticker_strs.append(f"{mark}{cname} {p:+.1f}%")

        tw_labels = []
        for code, tname in group["tw_stocks"].items():
            if code in active_tw_codes:
                tw_labels.append(f"★{tname}({code})")
            else:
                tw_labels.append(tname)

        block = (
            f"  ▸ {group['name']}（{' / '.join(ticker_strs)}）\n"
            f"     連動台股：{'、'.join(tw_labels)}"
        )

        if max_up >= threshold:
            bullish_blocks.append(block)
        elif min_down <= -threshold:
            bearish_blocks.append(block)
        else:
            neutral_blocks.append(f"  • {group['name']}：{' / '.join(ticker_strs)}")

    if bullish_blocks:
        lines.append(f"\n【📈 順風供應鏈（漲幅 ≥ {threshold:.0f}%）】")
        lines.extend(bullish_blocks)

    if bearish_blocks:
        lines.append(f"\n【📉 逆風供應鏈（跌幅 ≤ -{threshold:.0f}%）】")
        lines.extend(bearish_blocks)

    if neutral_blocks:
        lines.append("\n【➖ 其餘供應鏈動態】")
        lines.extend(neutral_blocks)

    if active_tw_codes:
        lines.append("\n※ 註：標示「★」者為昨日選股訊號 BUY/WATCH 標的")

    return "\n".join(lines)


def format_index_overview(stats: dict[str, dict]) -> list[str]:
    """美股主要指數精簡兩行（給盤前最終清單用）。"""
    if not stats:
        return ["⚪ 美股資料取不到"]
    parts = []
    for sym, label in MARKET_INDICES.items():
        st = stats.get(sym)
        if not st:
            continue
        short = {"^GSPC": "標普", "^DJI": "道瓊", "^IXIC": "那指", "^SOX": "費半", "TSM": "TSM", "EWT": "EWT", "^VIX": "VIX"}.get(sym, label)
        if sym == "^VIX":
            parts.append(f"{short} {st['close']:.1f}({st['pct']:+.0f}%)")
        else:
            parts.append(f"{short} {st['pct']:+.2f}%")
    half = (len(parts) + 1) // 2
    return ["🇺🇸 " + "｜".join(parts[:half]), "　 " + "｜".join(parts[half:])] if parts else []


def format_candidate_chains(stats: dict[str, dict], candidates: dict[str, str], threshold: float = 2.0) -> list[str]:
    """只列出和盤後候選股有關的美股供應鏈：每條鏈的美股漲跌 → 對應到哪幾檔候選。
    candidates: {代號: 名稱}"""
    lines = []
    for g in SUPPLY_CHAIN_GROUPS:
        hit = [f"{candidates[c]}({c})" for c in g["tw_stocks"] if c in candidates]
        if not hit:
            continue
        tick = []
        for sym, cname in g["tickers"].items():
            if sym in stats:
                pc = stats[sym]["pct"]
                mark = "🔥" if pc >= threshold else ("❄️" if pc <= -threshold else "")
                tick.append(f"{mark}{cname} {pc:+.1f}%")
        if tick:
            lines.append(f"▸ {g['name']}：{' / '.join(tick)} → {'、'.join(hit)}")
    return lines
