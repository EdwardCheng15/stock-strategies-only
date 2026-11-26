"""命令行券商庫存截圖健檢入口"""
import sys
from dotenv import load_dotenv
from stock_strategies.vision_audit import audit_from_screenshot, audit_positions
from stock_strategies.portfolio_guard import load_positions

load_dotenv()

if __name__ == "__main__":
    if len(sys.argv) >= 2:
        print(audit_from_screenshot(sys.argv[1]))
    else:
        print(audit_positions(load_positions(), save=True))
