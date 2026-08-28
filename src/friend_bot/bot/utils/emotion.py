import re
import random
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from src.friend_bot.core.logger import get_logger

logger = get_logger("emotion")

# 專案根目錄
BASE_DIR = Path(__file__).resolve().parents[4]

DEFAULT_KAOMOJI_MAP: Dict[str, List[str]] = {
    "tsundere": [
        "(///￣ ￣///)", "(*ﾉω＼*)", "ヽ(///＞_＜///)ﾉ", "(˘•ω•˘)",
        "(⁄ ⁄>⁄ ▽ ⁄<⁄ ⁄)", "(*/ω＼*)", "( 〃．．)", "(//ω//)", "(つд⊂) ⁄⁄⁄"
    ],
    "shock": [
        "(；ﾟДﾟ)", "(((ﾟДﾟ)))", "( ﾟдﾟ)", "⊂⌒~⊃｡Д｡)⊃",
        "(つд⊂)", "Σ(ﾟДﾟ；)", "(・_・;)", "(ﾟﾛﾟ;)", "(°Д°；)"
    ],
    "sigh": [
        "(；一_一)", "( ´Д｀)=3", "┐(´д｀)┌", "(눈_눈)",
        "(-_-;)", "(´ヘ｀；)", "(ー_ー)", "(￣_￣|||)"
    ],
    "proud": [
        "(๑•̀ㅂ•́)و✧", "( ¯•ω•¯ )", "(`・ω・´)", "╭( ･ㅂ･)و ̑̑",
        "(*￣ー￣)", "(｀・ω・´)ゞ", "(￣▽+￣*)"
    ],
    "soft": [
        "(´・ω・)ﾉ", "(｡･ω･｡)", "(*´ω｀*)", "(*´∀｀*)",
        "(´∀｀*)", "(´ω｀*)", "(*˘︶˘*)"
    ],
    "angry": [
        "(╬ Ò ‸ Ó)", "(ノ｀Д´)ノ", "(`Д´#)", "ヽ(`Д´)ﾉ",
        "(｀ε´)", "(-`ェ´-)", "(`皿´)"
    ],
    "thinking": [
        "(・ω・)？", "(・-・)？", "(´･ω･`)？", "(・へ・)", "( ˘•ω•˘ )"
    ],
    "awkward": [
        "(^ ^;)", "(・_・;)", "(;´∀｀)", "(；・∀・)", "(・ω・;)"
    ]
}


class EmotionReplacer:
    """
    情緒標籤渲染器 (Tag & Replace Engine)
    將模型輸出的 [emotion:類別] 自動替換為對應情緒庫的日系/2ch顏文字，
    並具備智慧防連續重複機制 (Anti-Consecutive Repetition)。
    """

    _kaomoji_map: Dict[str, List[str]] = {}
    _recent_history: Dict[str, List[str]] = {}
    _tag_regex = re.compile(r"\[emotion:([a-zA-Z0-9_\-]+)\]", re.IGNORECASE)

    @classmethod
    def load_kaomoji(cls, config_path: Optional[Path] = None) -> None:
        """載入 kaomoji.yaml 配置檔"""
        target_path = config_path or (BASE_DIR / "config" / "kaomoji.yaml")
        if not target_path.exists():
            target_path = BASE_DIR / "kaomoji.yaml"

        loaded = False
        if target_path.exists():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    km = data.get("kaomoji", {})
                    if isinstance(km, dict) and km:
                        cls._kaomoji_map = {k.lower(): [str(x) for x in v] for k, v in km.items() if isinstance(v, list)}
                        loaded = True
                        logger.debug(f"已成功載入顏文字庫: {list(cls._kaomoji_map.keys())}")
            except Exception as e:
                logger.warning(f"讀取顏文字配置 {target_path} 失敗: {e}")

        if not loaded:
            cls._kaomoji_map = {k: list(v) for k, v in DEFAULT_KAOMOJI_MAP.items()}

    @classmethod
    def get_random_kaomoji(cls, category: str) -> str:
        """從指定類別中隨機取得一個顏文字，並避開近期已使用的項目"""
        if not cls._kaomoji_map:
            cls.load_kaomoji()

        cat = category.lower().strip()
        pool = cls._kaomoji_map.get(cat)

        # 若未精確匹配，嘗試常用別名對應
        if not pool:
            alias_map = {
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
                "rage": "angry"
            }
            if cat in alias_map:
                pool = cls._kaomoji_map.get(alias_map[cat])

        if not pool:
            return ""

        # 防連續重複隊列（最近使用過的 2~3 個顏文字不重複抽取）
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
    def replace_emotion_tags(cls, text: str) -> str:
        """將字串中的所有 [emotion:xxx] 標籤替換為隨機顏文字"""
        if not text or "[emotion:" not in text:
            return text

        if not cls._kaomoji_map:
            cls.load_kaomoji()

        def _repl(match: re.Match) -> str:
            cat = match.group(1)
            kaomoji = cls.get_random_kaomoji(cat)
            return f" {kaomoji}" if kaomoji else ""

        rendered = cls._tag_regex.sub(_repl, text)
        # 清理多餘的連續空格
        rendered = re.sub(r"[ \t]{2,}", " ", rendered)
        return rendered.strip()
