import os
from pathlib import Path
from typing import List, Any, Dict, Tuple
import yaml
from dotenv import load_dotenv

from .logger import get_logger

logger = get_logger("config")

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
    """
    載入 config.yaml。若檔案存在但解析失敗（YAML 格式錯誤），直接拋出例外中止啟動，
    避免程式悄悄回退成全預設值運行、讓維運者誤以為設定已生效。
    檔案不存在則視為正常情況（純用預設值），回傳空字典。
    """
    if not CONFIG_PATH.exists():
        return {}

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"設定檔 {CONFIG_PATH} 解析失敗，請檢查 YAML 格式是否正確：{e}")
            raise RuntimeError(f"無法解析設定檔 {CONFIG_PATH}：{e}") from e

_yaml_config = _load_yaml_config()

# Helper to parse channel IDs from YAML or ENV as strings
def _parse_channel_ids(yaml_ids: Any, env_name: str) -> List[str]:
    result_set = set()
    if isinstance(yaml_ids, list):
        for cid in yaml_ids:
            clean = str(cid).strip()
            if clean:
                result_set.add(clean)
    elif yaml_ids:
        clean = str(yaml_ids).strip()
        if clean:
            result_set.add(clean)

    env_val = os.getenv(env_name, "")
    if env_val.strip():
        for cid_str in env_val.split(","):
            cid_clean = cid_str.strip()
            if cid_clean:
                result_set.add(cid_clean)

    return list(result_set)

# 1. 核心認證金鑰
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# 2. Bot 頻道與行為設定
_bot_cfg = _yaml_config.get("bot", {})
REPLY_CHANNEL_IDS: List[str] = _parse_channel_ids(_bot_cfg.get("reply_channel_ids"), "REPLY_CHANNEL_IDS")
LISTEN_CHANNEL_IDS: List[str] = _parse_channel_ids(_bot_cfg.get("listen_channel_ids"), "LISTEN_CHANNEL_IDS")
SHOW_TYPING: bool = _bot_cfg.get("show_typing", True)
MAX_MESSAGE_LENGTH: int = _bot_cfg.get("max_message_length", 2000)

# 3. 聊天氣泡發送行為設定
_chat_behavior_cfg = _yaml_config.get("chat_behavior", {})
_ignore_prefixes = _chat_behavior_cfg.get("ignore_prefixes", ["#", "＃", "//"])
IGNORE_PREFIXES: List[str] = [str(p) for p in _ignore_prefixes] if isinstance(_ignore_prefixes, list) else ["#", "＃", "//"]

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
GEMINI_TEMPERATURE: float = float(os.getenv("GEMINI_TEMPERATURE", _gemini_cfg.get("temperature", 0.88)))
GEMINI_FREQUENCY_PENALTY: float = float(os.getenv("GEMINI_FREQUENCY_PENALTY", _gemini_cfg.get("frequency_penalty", 0.3)))
GEMINI_PRESENCE_PENALTY: float = float(os.getenv("GEMINI_PRESENCE_PENALTY", _gemini_cfg.get("presence_penalty", 0.0)))
GEMINI_MAX_OUTPUT_TOKENS: int = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", _gemini_cfg.get("max_output_tokens", 2048)))

# 7. 記憶系統設定
_mem_cfg = _yaml_config.get("memory", {})
SHORT_TERM_HISTORY_LIMIT: int = int(_mem_cfg.get("short_term_history_limit", 15))
ENABLE_AUTO_MEMORY_EXTRACTION: bool = bool(_mem_cfg.get("enable_auto_memory_extraction", True))
LISTEN_DEBOUNCE_SECONDS: float = float(_mem_cfg.get("listen_debounce_seconds", 4.0))
LISTEN_MAX_QUEUE_MESSAGES: int = int(_mem_cfg.get("listen_max_queue_messages", 15))
EXTRACTION_SWEEP_INTERVAL_SECONDS: float = float(_mem_cfg.get("extraction_sweep_interval_seconds", 600))
ENABLE_HISTORY_RECALL: bool = bool(_mem_cfg.get("enable_history_recall", True))
HISTORY_RECALL_LIMIT: int = int(_mem_cfg.get("history_recall_limit", 4))
HISTORY_RECALL_MIN_SCORE: int = int(_mem_cfg.get("history_recall_min_score", 2))
HISTORY_RECALL_MAX_QUERY_TOKENS: int = int(_mem_cfg.get("history_recall_max_query_tokens", 30))
DB_PATH: str = str(BASE_DIR / _mem_cfg.get("db_path", "data/friend_bot.db"))

# 7.02 事實容量控制與語意去重 (Facts Capacity & Dedup) 設定
_facts_maint_cfg = _mem_cfg.get("facts_maintenance", {})
FACTS_MAX_STORED_PER_USER: int = int(_facts_maint_cfg.get("max_stored_per_user", 60))
ENABLE_FACTS_DEDUP: bool = bool(_facts_maint_cfg.get("enable_dedup", True))
FACTS_DEDUP_SIMILARITY_THRESHOLD: float = float(_facts_maint_cfg.get("dedup_similarity_threshold", 0.86))
FACTS_DEDUP_CLUSTER_MAX_SIZE: int = int(_facts_maint_cfg.get("dedup_cluster_max_size", 6))
FACTS_DEDUP_MAX_FACTS_PER_USER_PER_DAY: int = int(_facts_maint_cfg.get("dedup_max_facts_per_user_per_day", 30))
FACTS_EMBEDDING_MODEL: str = str(_facts_maint_cfg.get("embedding_model", "gemini-embedding-001"))
FACTS_EMBEDDING_BATCH_SIZE: int = int(_facts_maint_cfg.get("embedding_batch_size", 100))
FACTS_EMBEDDING_BATCH_DELAY_SECONDS: float = float(_facts_maint_cfg.get("embedding_batch_delay_seconds", 1.0))
FACTS_DEDUP_SWEEP_INTERVAL_SECONDS: float = float(_facts_maint_cfg.get("dedup_sweep_interval_seconds", 1800))

# 7.03 互動印象保護 (Interaction Notes Protection) 設定
_notes_protect_cfg = _mem_cfg.get("interaction_notes_protection", {})
INTERACTION_NOTES_SHRINK_RATIO: float = float(_notes_protect_cfg.get("shrink_ratio", 0.4))

# 7.05 別名 (Alias) 設定
_alias_cfg = _mem_cfg.get("alias", {})
ENABLE_ALIAS_LEARNING: bool = bool(_alias_cfg.get("enable_alias_learning", True))
MAX_ALIASES_PER_USER: int = int(_alias_cfg.get("max_aliases_per_user", 5))
ENABLE_AI_INVENTED_ALIAS: bool = bool(_alias_cfg.get("enable_ai_invented_alias", False))
INVENTED_ALIAS_MIN_TIER: int = int(_alias_cfg.get("invented_alias_min_tier", 3))

# 7.1 三軌事實記憶檢索 (3-Track Fact RAG & Heat) 設定
_facts_rag_cfg = _mem_cfg.get("facts_rag", {})
FACTS_SPEAKER_MAX_TOTAL: int = int(_facts_rag_cfg.get("speaker_max_total", 8))
FACTS_SPEAKER_HEAT_LIMIT: int = int(_facts_rag_cfg.get("speaker_heat_limit", 2))
FACTS_SPEAKER_RECENT_LIMIT: int = int(_facts_rag_cfg.get("speaker_recent_limit", 2))

FACTS_OTHERS_MAX_TOTAL: int = int(_facts_rag_cfg.get("others_max_total", 3))
FACTS_OTHERS_HEAT_LIMIT: int = int(_facts_rag_cfg.get("others_heat_limit", 1))
FACTS_OTHERS_RECENT_LIMIT: int = int(_facts_rag_cfg.get("others_recent_limit", 1))

FACTS_RAG_HIT_COOLDOWN_SECONDS: int = int(_facts_rag_cfg.get("rag_hit_cooldown_seconds", 3600))
FACTS_EXTRACTION_REAFFIRM_BONUS: int = int(_facts_rag_cfg.get("extraction_reaffirm_bonus", 3))
FACTS_RAG_HIT_BONUS: int = int(_facts_rag_cfg.get("rag_hit_bonus", 1))

# 7.5 語音頻道感知與音樂推薦 (Music Suggestion) 設定
_music_cfg = _yaml_config.get("music", {})
ENABLE_MUSIC_SUGGESTION: bool = bool(_music_cfg.get("enable_music_suggestion", True))
MUSIC_PLAY_COMMAND: str = str(_music_cfg.get("play_command", "m.play")).strip() or "m.play"
VOICE_MEMBERS_MAX: int = int(_music_cfg.get("voice_members_max", 5))
# 代發指令的目標頻道；留空則回退成當下觸發回覆的頻道（見 client.py 的 _dispatch_music_command）
MUSIC_COMMAND_CHANNEL_ID: str = os.getenv(
    "MUSIC_COMMAND_CHANNEL_ID", str(_music_cfg.get("command_channel_id", "") or "")
).strip()

# 7.6 收據拆帳 (Money Split) 設定
_money_cfg = _yaml_config.get("money", {})
ENABLE_MONEY_SPLIT: bool = bool(_money_cfg.get("enable_money_split", True))
# 代發拆帳指令使用的前綴，換用其他拆帳 bot 時只要改這裡，不需改程式碼。
W2W_COMMAND_PREFIX: str = str(_money_cfg.get("w2w_command", "$w2w")).strip() or "$w2w"
# 單張收據最多產生幾張拆帳卡片，避免品項過多洗版頻道。
MAX_RECEIPT_ITEMS: int = int(_money_cfg.get("max_receipt_items", 15))
# 拆帳卡片閒置逾時秒數，逾時後鎖定卡片元件。
MONEY_VIEW_TIMEOUT_SECONDS: int = int(_money_cfg.get("view_timeout_seconds", 600))
# 代發 w2w_command 指令訊息的目標頻道；留空則回退成觸發 /kurisu-money 指令當下的頻道。
MONEY_COMMAND_CHANNEL_ID: str = os.getenv(
    "MONEY_COMMAND_CHANNEL_ID", str(_money_cfg.get("command_channel_id", "") or "")
).strip()

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
