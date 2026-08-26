from typing import List, Dict, Any, Optional
from src.friend_bot.core.config import SYSTEM_PROMPT, BOT_NAME

def build_system_instruction() -> str:
    """建構系統人格與核心規則指令"""
    return f"""{SYSTEM_PROMPT.strip()}

[基本資訊]
- 你的名字: {BOT_NAME}
- 目前平台: Discord

[回覆原則]
1. 像真實群友一樣自然回覆，避免生硬、公事公辦或客服助理腔調。
2. 回覆適度簡潔、幽默，能開玩笑、適當吐槽或共鳴。
3. 若參考了該用戶的長期記憶或歷史回憶，請自然融入，切勿生硬複誦「我從資料庫查到你喜歡...」。
4. 不需要每次回覆都把對方的名字掛在嘴邊，保持自然聊天節奏。
"""

def format_memory_context(
    current_user_name: str,
    user_profile: Optional[Dict[str, Any]],
    deep_history: List[Dict[str, Any]],
    short_term_history: List[Dict[str, Any]]
) -> str:
    """將三層記憶組合為結構化的 Context 提示文字"""
    context_parts = []

    # 1. 第 2 層：當前發言用戶長期畫像
    if user_profile and (user_profile.get("facts") or user_profile.get("interaction_notes")):
        profile_lines = [f"【發言者 {current_user_name} 的個人特徵記憶】:"]
        facts = user_profile.get("facts", [])
        if facts:
            profile_lines.append("- 已知特徵/喜好: " + "、".join(facts))
        notes = user_profile.get("interaction_notes", "")
        if notes:
            profile_lines.append(f"- 互動印象: {notes}")
        context_parts.append("\n".join(profile_lines))

    # 2. 第 3 層：歷史深度回憶 (若有檢索出跨頻道相關話題)
    if deep_history:
        history_lines = ["【過去的歷史話題回憶 (供參考，若相關可自然提及)】:"]
        for item in deep_history:
            u_name = item.get("user_name", "未知")
            content = item.get("content", "")
            created = item.get("created_at", "")
            history_lines.append(f"- [{created}] {u_name}: {content}")
        context_parts.append("\n".join(history_lines))

    # 3. 第 1 層：近期頻道對話脈絡 (短期滑動視窗)
    if short_term_history:
        chat_lines = ["【近期頻道對話紀錄】:"]
        for msg in short_term_history:
            sender = msg.get("user_name", "用戶")
            is_bot = msg.get("is_bot", 0)
            prefix = f"{BOT_NAME} (你)" if is_bot else sender
            content = msg.get("content", "")
            has_img = " [附帶圖片]" if msg.get("has_image") else ""
            chat_lines.append(f"{prefix}: {content}{has_img}")
        context_parts.append("\n".join(chat_lines))

    return "\n\n".join(context_parts)

def build_extraction_prompt(
    user_name: str,
    current_facts: List[str],
    current_notes: str,
    recent_user_messages: List[str]
) -> str:
    """建構用於提煉用戶個人長期特徵的 Prompt"""
    facts_str = "、".join(current_facts) if current_facts else "尚無"
    messages_str = "\n".join(f"- {msg}" for msg in recent_user_messages)

    return f"""你是一個敏銳的群友記憶分析助理。請分析以下用戶【{user_name}】在 Discord 中的最新發言，提取出有價值且長期的「個人特徵/喜好/重要事實」與「互動印象」。

【目前已記錄事實】:
{facts_str}

【目前已記錄互動印象】:
{current_notes or "尚無"}

【用戶最新發言列表】:
{messages_str}

【提取規則】:
1. 僅提取**長期穩定**的事實（如：職業、居住地、寵物、喜歡的遊戲/食物/偶像、顯著性格習慣）。
2. 忽略短暫無意義的閒聊（例如「哈哈」、「+1」、「吃飽了沒」等無需提取）。
3. 若沒有任何新的有效事實，請維持原有事實。
4. 輸出必須嚴格為合法的 JSON 格式，不得包含額外說明文字或 Markdown 程式碼區塊外多餘文字。

【輸出 JSON 格式範例】:
{{
  "facts": ["軟體工程師", "養了一隻貓叫咪咪", "喜歡喝無糖綠茶"],
  "interaction_notes": "說話幽默風趣，常熬夜寫程式"
}}
"""
