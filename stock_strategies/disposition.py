import json, ssl, urllib.request
from typing import Any

def get_disposition_stocks() -> dict[str, str]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    disposed: dict[str, str] = {}
    urls = [
        ("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", "data", 2, 4),
        ("https://www.tpex.org.tw/www/zh-tw/bulletin/disposal?response=json", "tables", 2, 4),
    ]
    for url, key, c_idx, p_idx in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            rows = raw.get(key, [])
            if key == "tables" and rows:
                rows = rows[0].get("data", [])
            for r in rows:
                if len(r) > max(c_idx, p_idx) and str(r[c_idx]).strip().isdigit():
                    disposed[str(r[c_idx]).strip()] = str(r[p_idx]).strip()
        except Exception:
            continue
    return disposed

def apply_disposition_filter(results: list[dict[str, Any]], disposed_map: dict[str, str]) -> int:
    if not disposed_map:
        return 0
    downgraded = 0
    for r in results:
        sid = str(r.get("stock_id", "")).strip()
        if sid in disposed_map:
            r.setdefault("risk_notes", []).append(f"🚨 處置股警示({disposed_map[sid]})")
            if r.get("action") == "BUY":
                r["action"] = "WATCH"
                downgraded += 1
    return downgraded
