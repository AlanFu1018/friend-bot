import json
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from .config import MOOD_HALF_LIFE_MINUTES, MOOD_STEP, MOOD_THRESHOLD

# 可以累積成「心情」的情緒類別與其自然語言描述（會接在「非常／明顯／有一點」之後，須為形容詞）。
# thinking 這類「動作」而非「心情」的類別刻意不列入，不會被記錄。
MOOD_DESCRIPTIONS: Dict[str, str] = {
    "tsundere": "害羞、不好意思",
    "shock": "驚魂未定",
    "sigh": "無奈",
    "proud": "得意",
    "soft": "開心、溫和",
    "angry": "不爽",
    "awkward": "尷尬",
    "sad": "難過",
    "depressed": "低落",
}

# 強度分級：(下限, 程度副詞)。由高到低比對
_INTENSITY_LEVELS = [(3.0, "非常"), (1.8, "明顯"), (0.0, "有一點")]

# 強度達到此值才會觸發情緒慣性（改變顏文字池）
INERTIA_MIN_INTENSITY = 1.8

# 記錄新情緒時，其他情緒同時被沖淡的比例（新話題讓舊心情慢慢緩和）
_OTHER_DAMPEN = 0.75

# 單一情緒強度上限，避免無限累積導致永遠消不掉
_MAX_SCORE = 5.0

# 衰減後低於此值的情緒直接移除
_PRUNE_BELOW = 0.05


@dataclass
class MoodState:
    """單一頻道的心情狀態：各情緒的強度（記錄當下的值）、最後更新時間、以及誰造成的"""
    scores: Dict[str, float] = field(default_factory=dict)
    updated_at: float = 0.0
    cause_user: str = ""

    def decayed_scores(self, now: float) -> Dict[str, float]:
        """依半衰期計算 now 時刻的各情緒強度（讀取時才衰減，不需要排程器）"""
        if not self.scores:
            return {}
        elapsed_min = max(0.0, now - self.updated_at) / 60.0
        factor = 0.5 ** (elapsed_min / MOOD_HALF_LIFE_MINUTES) if MOOD_HALF_LIFE_MINUTES > 0 else 0.0
        return {k: v * factor for k, v in self.scores.items() if v * factor >= _PRUNE_BELOW}

    def to_json(self) -> str:
        return json.dumps(self.scores, ensure_ascii=False)

    @classmethod
    def from_row(cls, scores_json: str, updated_at: float, cause_user: str) -> "MoodState":
        try:
            raw = json.loads(scores_json or "{}")
            scores = {str(k): float(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        except (ValueError, TypeError):
            scores = {}
        return cls(scores=scores, updated_at=float(updated_at or 0), cause_user=cause_user or "")


class MoodTracker:
    """
    頻道心情追蹤器（純記憶體快取，持久化由 memory/mood_store.py 負責）

    模型每輸出一個情緒標籤就累積到該頻道的心情；心情隨時間指數衰減。
    目前心情會 (1) 透過 mood_transitions 影響顏文字挑選（情緒慣性），
    (2) 以自然語言注入 prompt，讓模型的語氣前後一致。
    """

    _states: Dict[str, MoodState] = {}

    @classmethod
    def is_loaded(cls, channel_id: str) -> bool:
        return channel_id in cls._states

    @classmethod
    def set_state(cls, channel_id: str, state: MoodState) -> None:
        cls._states[channel_id] = state

    @classmethod
    def get_state(cls, channel_id: str) -> Optional[MoodState]:
        return cls._states.get(channel_id)

    @classmethod
    def record(cls, channel_id: str, category: str, cause_user: str = "", now: Optional[float] = None) -> bool:
        """記錄一次情緒；非心情類別（如 thinking）不記錄。回傳是否有記錄"""
        if category not in MOOD_DESCRIPTIONS:
            return False
        now = time.time() if now is None else now
        state = cls._states.setdefault(channel_id, MoodState())
        scores = state.decayed_scores(now)
        for k in scores:
            if k != category:
                scores[k] *= _OTHER_DAMPEN
        scores[category] = min(_MAX_SCORE, scores.get(category, 0.0) + MOOD_STEP)
        state.scores = scores
        state.updated_at = now
        if cause_user:
            state.cause_user = cause_user
        return True

    @classmethod
    def current(cls, channel_id: str, now: Optional[float] = None) -> Optional[Tuple[str, float]]:
        """回傳 (主導情緒, 強度)；強度未達門檻時視為平靜，回傳 None"""
        state = cls._states.get(channel_id)
        if not state:
            return None
        scores = state.decayed_scores(time.time() if now is None else now)
        if not scores:
            return None
        category, intensity = max(scores.items(), key=lambda kv: kv[1])
        if intensity < MOOD_THRESHOLD:
            return None
        return category, intensity

    @classmethod
    def apply_inertia(
        cls,
        channel_id: str,
        category: str,
        transitions: Dict[str, Dict[str, str]],
        now: Optional[float] = None
    ) -> str:
        """
        情緒慣性：目前心情夠強時，依 mood_transitions 把模型想表達的情緒轉成另一個類別。
        例如還在氣頭上（angry）時模型輸出 soft，會改抽 tsundere——嘴硬但已經消氣。
        """
        mood = cls.current(channel_id, now)
        if not mood:
            return category
        mood_cat, intensity = mood
        if intensity < INERTIA_MIN_INTENSITY:
            return category
        return transitions.get(mood_cat, {}).get(category, category)

    @classmethod
    def describe(cls, channel_id: str, now: Optional[float] = None) -> str:
        """把目前心情轉成給模型看的自然語言（不含數字）；平靜時回傳空字串"""
        mood = cls.current(channel_id, now)
        if not mood:
            return ""
        category, intensity = mood
        adverb = next(label for floor, label in _INTENSITY_LEVELS if intensity >= floor)
        desc = f"你現在{adverb}{MOOD_DESCRIPTIONS[category]}"
        state = cls._states[channel_id]
        if state.cause_user:
            desc += f"（起因和 {state.cause_user} 剛才的互動有關）"
        return desc + "。"
