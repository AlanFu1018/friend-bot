import re
import time
import random
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from .logger import get_logger
# 專案根目錄由 config 統一提供，避免此檔案移動位置時 parents[N] 的深度假設失效
from .config import BASE_DIR, ENABLE_MOOD, ENABLE_RARE_KAOMOJI, RARE_KAOMOJI_CHANCE, RARE_KAOMOJI_COOLDOWN_HOURS
from .mood import MoodTracker

logger = get_logger("emotion")

DEFAULT_KAOMOJI_MAP: Dict[str, List[str]] = {
    "tsundere": [
        "(///￣ ￣///)", "(*ﾉω＼*)", "ヽ(///＞_＜///)ﾉ", "(˘•ω•˘)",
        "(⁄ ⁄>⁄ ▽ ⁄<⁄ ⁄)", "(*/ω＼*)", "( 〃．．)", "(//ω//)", "(つд⊂) ⁄⁄⁄"
    ],
    "shock": [
        "(；ﾟДﾟ)", "(((ﾟДﾟ)))", "( ﾟдﾟ)", "⊂⌒~⊃｡Д｡)⊃",
        "(つд⊂)", "Σ(ﾟДﾟ；)", "(・_・;)", "(ﾟᗝﾟ;)", "(°Д°；)"
    ],
    "sigh": [
        "(；一_一)", "( ´Д｀)=3", "┐(´д｀)┌", "(눈_눈)",
        "(-_-;)", "(´ヘ｀；)", "(ー_ー)", "(￣_￣|||)"
    ],
    "proud": [
        "(๑•̀ㅂ•́)و✧", "( ¯•ω•¯ )", "(´・ω・´)", "╭( ･ㅂ･)و ̑̑",
        "(*￣ー￣)", "(｀・ω・´)ゝ", "(￣▽+￣*)"
    ],
    "soft": [
        "(´・ω・`)ﾉ", "(｡･ω･｡)", "(*´ω｀*)", "(*´∀｀*)",
        "(´∀｀*)", "(´ω｀*)", "(*˘︶˘*)"
    ],
    "angry": [
        "(╬ Ò ‸ Ó)", "(ノ´Д´)ノ", "(´Д´#)", "ヽ(´Д´)ﾉ",
        "(｀ε´)", "(-´ェ´-)", "(´皿´)"
    ],
    "thinking": [
        "(・ω・)？", "(・-・)？", "(´･ω･`)？", "(・へ・)", "( ˘•ω•˘ )"
    ],
    "awkward": [
        "(^ ^;)", "(・_・;)", "(;´∀｀)", "(；・∀・)", "(・ω・;)"
    ],
    "sad": [
        "(T_T)", "(ノ_・。)", "(ノДT)", "(つд⊂)", "(｡ŏ﹏ŏ)", "(´；ω；｀)", "(｡•́︿•̀｡)", "( ；∀；)"
    ],
    "depressed": [
        "(◞‸◟)", "(´-ω-｀)", "( _ _ )...", "orz", "OTZ", "(o_ _)o", "(；´д｀)=3", "( •́ ̯•̀ )"
    ]
}


class EmotionReplacer:
    """
    情緒標籤渲染器 (Tag & Replace Engine)
    將模型輸出的 [emotion:類別] 或 (emotion:類別) 自動替換為對應情緒庫的日系/2ch顏文字（以程式碼區塊 `...` 格式輸出），
    並具備智慧防連續重複機制 (Anti-Consecutive Repetition)、頻道心情慣性與稀有顏文字。
    """

    _kaomoji_map: Dict[str, List[str]] = {}
    _rare_map: Dict[str, List[str]] = {}
    _mood_transitions: Dict[str, Dict[str, str]] = {}
    _recent_history: Dict[str, List[str]] = {}
    _rare_last_used: Dict[str, float] = {}
    # 連同標籤前的空白一起吃掉，替換時再統一補一個空格，避免留下雙空格
    _tag_regex = re.compile(r"[ \t]*(?:`*\[emotion:([\w\-]+)\]`*|`*\(emotion:([\w\-]+)\)`*)", re.IGNORECASE)

    # 模型未必會用正式類別名，因此有一層別名映射
    _ALIAS_MAP: Dict[str, str] = {
        "shy": "tsundere",
        "blush": "tsundere",
        "scared": "shock",
        "surprised": "shock",
        "panic": "shock",
        "tired": "sigh",
        "disdain": "sigh",
        "smug": "proud",
        "confident": "proud",
        "gentle": "soft",
        "happy": "soft",
        "smile": "soft",
        "mad": "angry",
        "rage": "angry",
        "cry": "sad",
        "crying": "sad",
        "grief": "sad",
        "sorrow": "sad",
        "heartbroken": "sad",
        "gloom": "depressed",
        "gloomy": "depressed",
        "down": "depressed",
        "frustrated": "depressed",
        "disappointed": "depressed",
        "hopeless": "depressed"
    }

    @classmethod
    def load_kaomoji(cls, config_path: Optional[Path] = None) -> None:
        """
        載入 kaomoji.yaml 配置檔：
        - kaomoji：各情緒類別的一般顏文字池
        - rare_kaomoji（選配）：各類別的稀有款，以極低機率出現
        - mood_transitions（選配）：情緒慣性規則 {目前心情: {模型輸出的情緒: 改抽的類別}}
        """
        target_path = config_path or (BASE_DIR / "config" / "kaomoji.yaml")
        if not target_path.exists():
            target_path = BASE_DIR / "kaomoji.yaml"

        loaded = False
        data: Dict = {}
        if target_path.exists():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                km = data.get("kaomoji", {})
                if isinstance(km, dict) and km:
                    cls._kaomoji_map = cls._parse_pools(km)
                    loaded = True
                    logger.debug(f"已成功載入顏文字庫: {list(cls._kaomoji_map.keys())}")
            except Exception as e:
                logger.warning(f"讀取顏文字配置 {target_path} 失敗: {e}")
                data = {}

        if not loaded:
            cls._kaomoji_map = {k: list(v) for k, v in DEFAULT_KAOMOJI_MAP.items()}

        rare = data.get("rare_kaomoji", {})
        cls._rare_map = cls._parse_pools(rare) if isinstance(rare, dict) else {}

        mt = data.get("mood_transitions", {})
        cls._mood_transitions = {
            str(mood).lower(): {str(a).lower(): str(b).lower() for a, b in rules.items()}
            for mood, rules in mt.items() if isinstance(rules, dict)
        } if isinstance(mt, dict) else {}

    @staticmethod
    def _parse_pools(raw: Dict) -> Dict[str, List[str]]:
        """{類別: [顏文字...]} → 類別轉小寫、內容轉字串，略過非清單的項目"""
        return {str(k).lower(): [str(x) for x in v] for k, v in raw.items() if isinstance(v, list) and v}

    @classmethod
    def resolve_category(cls, category: str) -> str:
        """將模型輸出的類別名正規化為正式類別（處理大小寫與別名）；查無對應時原樣回傳小寫"""
        if not cls._kaomoji_map:
            cls.load_kaomoji()
        cat = category.lower().strip()
        if cat in cls._kaomoji_map:
            return cat
        return cls._ALIAS_MAP.get(cat, cat)

    @classmethod
    def _pick_rare(cls, cat: str, now: float) -> str:
        """以 RARE_KAOMOJI_CHANCE 機率抽稀有款；冷卻中的稀有款不會再被抽到"""
        if not ENABLE_RARE_KAOMOJI:
            return ""
        pool = cls._rare_map.get(cat)
        if not pool or random.random() >= RARE_KAOMOJI_CHANCE:
            return ""
        cooldown = RARE_KAOMOJI_COOLDOWN_HOURS * 3600
        available = [k for k in pool if now - cls._rare_last_used.get(k, 0.0) >= cooldown]
        if not available:
            return ""
        chosen = random.choice(available)
        cls._rare_last_used[chosen] = now
        logger.info(f"✨ 抽中稀有顏文字 [{cat}]: {chosen}")
        return chosen

    @classmethod
    def get_random_kaomoji(cls, category: str) -> str:
        """從指定類別中隨機取得一個顏文字，並避開近期已使用的項目"""
        cat = cls.resolve_category(category)
        pool = cls._kaomoji_map.get(cat)
        if not pool:
            return ""

        rare = cls._pick_rare(cat, time.time())
        if rare:
            return rare

        # 防連續重複隊列（以正式類別為 key，別名與正式類別共用同一份紀錄）
        recent = cls._recent_history.setdefault(cat, [])
        available = [k for k in pool if k not in recent]
        if not available:
            available = pool
            recent.clear()

        chosen = random.choice(available)
        recent.append(chosen)
        if len(recent) > max(1, len(pool) // 2):
            recent.pop(0)

        return chosen

    @classmethod
    def replace_emotion_tags(
        cls,
        text: str,
        mood_channel_id: Optional[str] = None,
        cause_user: str = ""
    ) -> str:
        """
        將字串中的所有 [emotion:xxx] / (emotion:xxx) 標籤替換為以行內程式碼區塊 `...` 包裹的隨機顏文字。

        傳入 mood_channel_id 時才會讀寫該頻道的心情（情緒慣性 + 累積心情）；
        記憶提煉、提醒台詞等非聊天呼叫不傳，避免背景任務改動 bot 的心情。
        """
        if not text or "emotion:" not in text.lower():
            return text

        if not cls._kaomoji_map:
            cls.load_kaomoji()

        track_mood = ENABLE_MOOD and bool(mood_channel_id)

        def _repl(match: re.Match) -> str:
            cat = cls.resolve_category(match.group(1) or match.group(2))
            display_cat = cat
            if track_mood:
                display_cat = MoodTracker.apply_inertia(mood_channel_id, cat, cls._mood_transitions)
                MoodTracker.record(mood_channel_id, cat, cause_user)
            kaomoji = cls.get_random_kaomoji(display_cat)
            if not kaomoji:
                return ""
            safe_kaomoji = kaomoji.replace("`", "´")
            # 位於行首時不補前置空格
            at_line_start = match.start() == 0 or match.string[match.start() - 1] == "\n"
            return f"{'' if at_line_start else ' '}`{safe_kaomoji}`"

        rendered = cls._tag_regex.sub(_repl, text)
        return rendered.strip()
