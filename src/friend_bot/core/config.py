import os
from pathlib import Path
from typing import List, Any, Dict, Tuple
import yaml
from dotenv import load_dotenv

# 專案根目錄 (C:\ALL FILES\Code\friend-bot)
BASE_DIR = Path(__file__).resolve().parents[3]

# 載入專案根目錄的 .env 檔案
load_dotenv(dotenv_path=BASE_DIR / ".env")

def _find_config_file() -> Path:
    """尋找 config.yaml 的位置（優先檢查 config/ 目錄，其次根目錄）"""
    candidates = [
        BASE_DIR / "config" / "config.yaml",
        BASE_DIR / "config.yaml"
    ]
    for p in candidates:
        if p.exists():
            return p
    return BASE_DIR / "config" / "config.yaml"

CONFIG_PATH = _find_config_file()

def _load_yaml_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            try:
                return yaml.safe_load(f) or {}
            except Exception as e:
                print(f"[Warning] Failed to parse config.yaml from {CONFIG_PATH}: {e}")
    return {}

_yaml_config = _load_yaml_config()

# Helper to parse channel IDs from YAML or ENV
def _parse_channel_ids(yaml_ids: Any, env_name: str) -> List[int]:
    result_set = set()
    if isinstance(yaml_ids, list):
        for cid in yaml_ids:
            try:
                result_set.add(int(cid))
            except (ValueError, TypeError):
                pass

    env_val = os.getenv(env_name, "")
    if env_val.strip():
        for cid_str in env_val.split(","):
            cid_clean = cid_str.strip()
            if cid_clean.isdigit():
                result_set.add(int(cid_clean))

    return list(result_set)

# 1. 核心認證金鑰
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# 2. Bot 頻道與行為設定
_bot_cfg = _yaml_config.get("bot", {})
REPLY_CHANNEL_IDS: List[int] = _parse_channel_ids(_bot_cfg.get("reply_channel_ids"), "REPLY_CHANNEL_IDS")
LISTEN_CHANNEL_IDS: List[int] = _parse_channel_ids(_bot_cfg.get("listen_channel_ids"), "LISTEN_CHANNEL_IDS")
SHOW_TYPING: bool = _bot_cfg.get("show_typing", True)
MAX_MESSAGE_LENGTH: int = _bot_cfg.get("max_message_length", 2000)

# 3. 聊天氣泡發送行為設定
_chat_behavior_cfg = _yaml_config.get("chat_behavior", {})
ENABLE_MULTI_BUBBLE: bool = bool(_chat_behavior_cfg.get("enable_multi_bubble", True))
BUBBLE_TARGET_LENGTH: int = int(_chat_behavior_cfg.get("bubble_target_length", 35))
_typing_delay_cfg = _chat_behavior_cfg.get("typing_delay_range", [0.6, 1.3])
TYPING_DELAY_RANGE: Tuple[float, float] = (
    float(_typing_delay_cfg[0]) if len(_typing_delay_cfg) > 0 else 0.6,
    float(_typing_delay_cfg[1]) if len(_typing_delay_cfg) > 1 else 1.3,
)

# 3.1 多人群聊短時熱絡 (Burst) 聚合設定
_burst_cfg = _chat_behavior_cfg.get("burst_reply", {})
ENABLE_BURST_REPLY: bool = bool(_burst_cfg.get("enable_burst_reply", True))
BURST_WINDOW_SECONDS: float = float(_burst_cfg.get("window_seconds", 4.5))
BURST_MIN_USER_COUNT: int = int(_burst_cfg.get("min_user_count", 2))
BURST_MAX_MESSAGES: int = int(_burst_cfg.get("max_burst_messages", 5))

# 4. 聯網搜尋 (Web Search) 設定
_web_cfg = _yaml_config.get("web_search", {})
ENABLE_WEB_SEARCH: bool = bool(_web_cfg.get("enable_web_search", True))
SEARCH_TOP_K: int = int(_web_cfg.get("search_top_k", 3))
MAX_CONTENT_LENGTH_PER_PAGE: int = int(_web_cfg.get("max_content_length_per_page", 2500))

# 5. 行事曆與 Webhook 設定
_cal_cfg = _yaml_config.get("calendar", {})
CALENDAR_WEBHOOK_URL: str = os.getenv("CALENDAR_WEBHOOK_URL", _cal_cfg.get("webhook_url", "")).strip()
CALENDAR_AVATAR_URL: str = os.getenv("CALENDAR_AVATAR_URL", _cal_cfg.get("avatar_url", "")).strip()

# 6. Gemini 模型設定
_gemini_cfg = _yaml_config.get("gemini", {})
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", _gemini_cfg.get("model", "gemini-3.1-flash-lite"))
GEMINI_TEMPERATURE: float = float(_gemini_cfg.get("temperature", 0.85))
GEMINI_MAX_OUTPUT_TOKENS: int = int(_gemini_cfg.get("max_output_tokens", 2048))

# 7. 記憶系統設定
_mem_cfg = _yaml_config.get("memory", {})
SHORT_TERM_HISTORY_LIMIT: int = int(_mem_cfg.get("short_term_history_limit", 15))
ENABLE_AUTO_MEMORY_EXTRACTION: bool = bool(_mem_cfg.get("enable_auto_memory_extraction", True))
ENABLE_HISTORY_RECALL: bool = bool(_mem_cfg.get("enable_history_recall", True))
HISTORY_RECALL_LIMIT: int = int(_mem_cfg.get("history_recall_limit", 4))
DB_PATH: str = str(BASE_DIR / _mem_cfg.get("db_path", "data/friend_bot.db"))

# 8. 好感度與人際進展 (Favorability) 設定
_fav_cfg = _yaml_config.get("favorability", {})
ENABLE_FAVORABILITY: bool = bool(_fav_cfg.get("enable_favorability", True))
DEFAULT_FAVORABILITY: int = int(_fav_cfg.get("default_favorability", 30))
DAILY_GAIN_LIMIT: int = int(_fav_cfg.get("daily_gain_limit", 5))
DAILY_LOSS_LIMIT: int = int(_fav_cfg.get("daily_loss_limit", 10))

# 9. Persona 與 System Prompt 設定（從 .md 檔案載入）
_persona_cfg = _yaml_config.get("persona", {})
BOT_NAME: str = _persona_cfg.get("bot_name", "克莉絲")

def _load_persona_prompt() -> str:
    """從指定的 markdown 檔案中載入角色性格設定（檢查 config/ 與根目錄）"""
    persona_file_name = _persona_cfg.get("persona_file", "persona.md")
    candidates = [
        BASE_DIR / "config" / persona_file_name,
        BASE_DIR / persona_file_name
    ]

    for p in candidates:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        return content
            except Exception as e:
                print(f"[Warning] Failed to read persona file '{p}': {e}")

    # 若找不到檔案或讀取失敗時的 Fallback
    return _persona_cfg.get(
        "system_prompt",
        "你是一個在 Discord 群組中和大家一起聊天的「幽默群友」。說話風趣活潑、富有幽默感。"
    )

SYSTEM_PROMPT: str = _load_persona_prompt()
