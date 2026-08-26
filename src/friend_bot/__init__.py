import sys
from pathlib import Path

# 自動將專案根目錄納入 sys.path，確保 config 及各模組無論在哪個目錄呼叫皆可順暢載入
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
