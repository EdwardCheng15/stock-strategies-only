"""Telegram Bot 圖片監聽器：傳送庫存截圖自動匯入 positions.json 並回覆健檢報告"""

from __future__ import annotations
import json
import os
import pathlib
import tempfile
import time
import urllib.request
from dotenv import load_dotenv

from stock_strategies.vision_audit import audit_from_screenshot, audit_positions
from stock_strategies.portfolio_guard import load_positions

load_dotenv()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def tg_call(method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=35) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_reply(chat_id: int | str, text: str) -> None:
    tg_call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})


def download_photo(file_id: str) -> pathlib.Path:
    info = tg_call("getFile", {"file_id": file_id})
    file_path = info["result"]["file_path"]
    dl_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    tmp = pathlib.Path(tempfile.mktemp(suffix=".jpg"))
    urllib.request.urlretrieve(dl_url, tmp)
    return tmp


def run_polling() -> None:
    if not TOKEN:
        raise RuntimeError("請先在 .env 設定 TELEGRAM_BOT_TOKEN")
    print("🤖 Telegram 庫存健檢監聽器已啟動（支援傳送庫存截圖或輸入 /audit）...")
    offset = 0
    while True:
        try:
            res = tg_call("getUpdates", {"offset": offset, "timeout": 25})
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
                    continue

                if "photo" in msg:
                    send_reply(chat_id, "🔍 收到庫存截圖，正在解析持倉並進行移動停利健檢...")
                    file_id = msg["photo"][-1]["file_id"]
                    tmp_img = download_photo(file_id)
                    try:
                        report = audit_from_screenshot(tmp_img)
                        send_reply(chat_id, f"✅ *庫存已同步至 positions.json*\n\n{report}")
                    except Exception as e:
                        send_reply(chat_id, f"⚠️ 截圖解析失敗：{e}")
                    finally:
                        tmp_img.unlink(missing_ok=True)

                elif (msg.get("text") or "").strip().startswith("/audit"):
                    report = audit_positions(load_positions(), save=True)
                    send_reply(chat_id, report)
        except Exception as loop_err:
            print(f"⚠️ 輪詢異常: {loop_err}")
            time.sleep(3)


if __name__ == "__main__":
    run_polling()
