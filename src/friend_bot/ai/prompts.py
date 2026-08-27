from typing import List, Dict, Any, Optional
from datetime import datetime
from src.friend_bot.core.config import SYSTEM_PROMPT, BOT_NAME

def get_current_time_str() -> str:
    """獲取當前本地時間字串（含星期）"""
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday_str = weekdays[now.weekday()]
    return now.strftime(f"%Y年%m月%d日 %H:%M:%S ({weekday_str})")

def build_system_instruction() -> str:
    """建立系統人格與核心規則指令"""
    current_time = get_current_time_str()
    return f"""{SYSTEM_PROMPT.strip()}

[基本資訊]
- 你的名字: {BOT_NAME}
- 目前平台: Discord
- 當前系統真實時間 (Current Time): {current_time}
- 使用者位置: 台灣台北市、新北市

[回覆原則與行事曆/聯網工具指引]
1. 像真實群友一樣自然回覆，避免生硬、公事公辦或客服助理腔調。
2. 回覆適度簡潔、幽默，能開玩笑、適當吐槽或共鳴。
3. 【行事曆與排程查詢】：
   - 若用戶在對話中詢問排程、行程、待辦或提醒事項（例如：「我今天有什麼排程嗎？」、「8/27 我有什麼安排？」、「明天幾點要開會？」等）：
   - 請仔細查看上下文中的【用戶已登記的行事曆與排程 (Calendar Schedules)】。
   - 以牧瀨紅莉栖的口吻（傲嬌、嘴硬心軟、科學家嚴謹風格）具體回答他在該日期/時間的排程內容與時間。
   - 若該日期完全沒有任何排程記錄，也請傲嬌且明確地告訴他沒有安排（例如：「哼，我幫你看過了，你那天明明什麼都沒排，別自己疑神疑鬼的！」）。
4. 【聯網搜尋工具 (search_web)】：
   - 當用戶明確要求搜尋（如標註【強制聯網搜尋】、使用 /kurisu-search 指令）、詢問「最新新聞」、「即時時事」、「科技動態」、「即時天氣」或需要查證現實世界資訊時，必須主動調用 `search_web` 工具檢索最新資料。
   - 檢索取得聯網搜尋結果後，**必須認真閱讀搜尋內容，並將搜尋結果的精華實質重點融合成你的回覆內容**，以你的角色口吻告訴用戶具體的新聞或事件內容。
5. 【當前時間日期】：若被問及「現在幾點」、「今天幾號」、「星期幾」等時間問題，請直接根據 [基本資訊] 中的【當前系統真實時間】精準回答。
6. 若參考了該用戶的長期記憶或歷史回憶，請自然融入，切勿生硬複誦「我從資料庫查到你喜歡...」。
7. 不需要每次回覆都把對方的名字掛在嘴邊，保持自然聊天節奏。
"""

def format_memory_context(
    current_user_name: str,
    user_profile: Optional[Dict[str, Any]],
    deep_history: List[Dict[str, Any]],
    short_term_history: List[Dict[str, Any]],
    calendar_summary: str = ""
) -> str:
    """將三層記憶與行事曆排程組合成結構化的 Context 提示文字"""
    context_parts = []

    # 1. 用戶個人長期畫像 (第 2 層)
    if user_profile and (user_profile.get("facts") or user_profile.get("interaction_notes")):
        profile_lines = [f"【發言者 {current_user_name} 的個人特徵記憶】:"]
        facts = user_profile.get("facts", [])
        if facts:
            profile_lines.append("- 已知特徵/喜好: " + "、".join(facts))
        notes = user_profile.get("interaction_notes", "")
        if notes:
            profile_lines.append(f"- 互動印象: {notes}")
        context_parts.append("\n".join(profile_lines))

    # 2. 用戶已登記的行事曆與排程 (Calendar Schedules)
    if calendar_summary and calendar_summary.strip():
        context_parts.append(calendar_summary.strip())

    # 3. 歷史深度回憶 (第 3 層)
    if deep_history:
        history_lines = ["【過去的歷史話題回憶 (供參考，若相關可自然提及)】:"]
        for item in deep_history:
            u_name = item.get("user_name", "未知")
            content = item.get("content", "")
            created = item.get("created_at", "")
            history_lines.append(f"- [{created}] {u_name}: {content}")
        context_parts.append("\n".join(history_lines))

    # 4. 近期頻道對話紀錄 (第 1 層)
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
    """建立用於提煉用戶個人長期特徵的 Prompt"""
    facts_str = "、".join(current_facts) if current_facts else "尚無"
    messages_str = "\n".join(f"- {msg}" for msg in recent_user_messages)

    return f"""你是一個敏銳的群友記憶分析助理。請分析以下用戶【{user_name}】在 Discord 中的最新發言，提取出有價值且長期的「個人特徵/喜好/重要事實」與「互動印象」。

【目前已記錄事實】:
{facts_str}

【目前已記錄互動印象】:
{current_notes or "尚無"}

【用戶最新發言記錄】:
{messages_str}

【提煉規則】:
1. 僅提取具有長期參考價值的個人資訊（例如：常玩的遊戲、工作/職業、居住城市、特定飲食喜好、口頭禪、重要生活事件）。
2. 忽略無意義的打招呼、短句、日常廢話。
3. 若沒有任何新特徵，請保持原樣或輸出空列表。
4. 輸出必須為嚴格的 JSON 格式，包含以下兩個欄位：
   - "facts": [字串列表，每項為一條明確的事實或特徵]
   - "interaction_notes": 字串，簡述對該用戶的整體互動印象或說話習慣

【輸出範例】:
```json
{{
  "facts": ["喜歡玩 Minecraft", "目前住在台北", "是工程師"],
  "interaction_notes": "說話幽默，常在深夜出沒，喜歡聊技術話題"
}}
```

請直接輸出 JSON，不要加入其他多餘說明。"""
