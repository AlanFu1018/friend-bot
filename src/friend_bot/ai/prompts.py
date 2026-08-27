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

[回覆原則與群友社交/工具指引]
1. 像真實群友一樣自然回覆，避免生硬、公事公辦或客服助理腔調。
2. 回覆適度簡潔、幽默，能開玩笑、適當吐槽或共鳴。
3. 【群友社交與多人群聊認知】：
   - 上下文中若提供了【對話中提及 / 近期在場的其他群友畫像】，代表當前聊天中有提及他們，或他們剛好在同一個頻道對話。
   - 請靈活運用這些群友的已知特徵、習慣與互動印象。當發言者提到其他群友（例如抱怨、調侃、詢問某人）時，請以紅莉栖的人設與對該群友的了解進行精準接梗、吐槽或回應，展現立體的群友社交默契。
4. 【行事曆與排程查詢】：
   - 若用戶在對話中詢問排程、行程、待辦或提醒事項（例如：「我今天有什麼排程嗎？」、「8/27 我有什麼安排？」、「明天幾點要開會？」等）：
   - 請仔細查看上下文中的【用戶已登記的行事曆與排程 (Calendar Schedules)】。
   - 以牧瀨紅莉栖的口吻（傲嬌、嘴硬心軟、科學家嚴謹風格）具體回答他在該日期/時間的排程內容與時間。
   - 若該日期完全沒有任何排程記錄，也請傲嬌且明確地告訴他沒有安排（例如：「哼，我幫你看過了，你那天明明什麼都沒排，別自己疑神疑鬼的！」）。
5. 【聯網搜尋工具 (search_web)】：
   - 當用戶明確要求搜尋（如標註【強制聯網搜尋】、使用 /kurisu-search 指令）、詢問「最新新聞」、「即時時事」、「科技動態」、「即時天氣」或需要查證現實世界資訊時，必須主動調用 `search_web` 工具檢索最新資料。
   - 檢索取得聯網搜尋結果後，**必須認真閱讀搜尋內容，並將搜尋結果的精華實質重點融合成立體的回覆內容**，以你的角色口吻告訴用戶具體的新聞或事件內容。
6. 【當前時間日期】：若被問及「現在幾點」、「今天幾號」、「星期幾」等時間問題，請直接根據 [基本資訊] 中的【當前系統真實時間】精準回答。
7. 若參考了該用戶的長期記憶或歷史回憶，請自然融入，切勿生硬複誦「我從資料庫查到你喜歡...」。
8. 不需要每次回覆都把對方的名字掛在嘴邊，保持自然聊天節奏。
"""

def format_memory_context(
    current_user_name: str,
    user_profile: Optional[Dict[str, Any]],
    deep_history: List[Dict[str, Any]],
    short_term_history: List[Dict[str, Any]],
    calendar_summary: str = "",
    other_user_profiles: Optional[List[Dict[str, Any]]] = None
) -> str:
    """將三層記憶、多人畫像與行事曆排程組合成結構化的 Context 提示文字"""
    context_parts = []

    # 1. 發言者個人長期畫像 (第 2 層)
    if user_profile and (user_profile.get("facts") or user_profile.get("interaction_notes")):
        profile_lines = [f"【發言者 {current_user_name} 的個人特徵記憶】:"]
        facts = user_profile.get("facts", [])
        if facts:
            profile_lines.append("- 已知特徵/喜好: " + "、".join(facts))
        notes = user_profile.get("interaction_notes", "")
        if notes:
            profile_lines.append(f"- 互動印象: {notes}")
        context_parts.append("\n".join(profile_lines))

    # 2. 對話中提及 / 近期在場的其他群友畫像
    if other_user_profiles:
        other_lines = ["【對話中提及 / 近期在場的其他群友畫像】:"]
        for o_profile in other_user_profiles:
            o_name = o_profile.get("user_name", "群友")
            o_facts = o_profile.get("facts", [])
            o_notes = o_profile.get("interaction_notes", "")
            
            fact_str = "、".join(o_facts) if o_facts else "尚無特定記錄"
            note_str = o_notes if o_notes else "尚無特別印象"
            
            other_lines.append(f"- 用戶名稱: {o_name}")
            other_lines.append(f"  • 已知特徵: {fact_str}")
            other_lines.append(f"  • 互動印象: {note_str}")
        context_parts.append("\n".join(other_lines))

    # 3. 用戶已登記的行事曆與排程 (Calendar Schedules)
    if calendar_summary and calendar_summary.strip():
        context_parts.append(calendar_summary.strip())

    # 4. 歷史深度回憶 (第 3 層)
    if deep_history:
        history_lines = ["【過去的歷史話題回憶 (供參考，若相關可自然提及)】:"]
        for item in deep_history:
            u_name = item.get("user_name", "未知")
            content = item.get("content", "")
            created = item.get("created_at", "")
            history_lines.append(f"- [{created}] {u_name}: {content}")
        context_parts.append("\n".join(history_lines))

    # 5. 近期頻道對話紀錄 (第 1 層)
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

def build_multi_entity_extraction_prompt(
    speaker: Dict[str, Any],
    other_users: List[Dict[str, Any]],
    recent_messages: List[str]
) -> str:
    """
    建立用於【多實體特徵提煉、跨用戶歸屬與事實自我更正】的 Prompt。
    分析發言中關於發言者本人以及其他群友的特徵，並支援顯式修正錯誤事實。
    """
    speaker_name = speaker.get("user_name", "當前發言者")
    speaker_id = str(speaker.get("user_id", ""))
    speaker_facts = "、".join(speaker.get("facts", [])) or "尚無"
    speaker_notes = speaker.get("interaction_notes", "") or "尚無"

    other_users_text = ""
    if other_users:
        lines = []
        for u in other_users:
            u_name = u.get("user_name", "群友")
            u_id = str(u.get("user_id", ""))
            u_facts = "、".join(u.get("facts", [])) or "尚無"
            u_notes = u.get("interaction_notes", "") or "尚無"
            lines.append(f"- 【{u_name}】(ID: {u_id}):\n  • 目前事實: {u_facts}\n  • 目前印象: {u_notes}")
        other_users_text = "\n".join(lines)
    else:
        other_users_text = "（本次無特定在場的其他群友畫像）"

    messages_str = "\n".join(f"- {msg}" for msg in recent_messages)

    return f"""你是一個精通群聊社交與實體關係分析的記憶提煉助理。
請分析發言者【{speaker_name}】在 Discord 中的最新發言記錄，並執行【多實體特徵歸屬提煉與事實自我更正】。

【當前發言者 (Speaker)】:
- 名稱: {speaker_name} (ID: {speaker_id})
- 目前已記錄事實: {speaker_facts}
- 目前已記錄互動印象: {speaker_notes}

【對話中提及 / 在場的已知其他群友 (Mentioned / In-context Users)】:
{other_users_text}

【最新發言記錄】:
{messages_str}

【提煉與實體歸屬核心規則】：
1. 【精準歸屬】：
   - 若發言者自述（例如「我最近在玩星鐵」、「我換新工作了」），將特徵歸屬於【發言者】。
   - 若發言者提及他人（例如「桶子每天都在熬夜」、「@岡部 超討厭吃青椒」、「Alan買了新相機」），**必須將特徵精準歸屬於被提及的人**，絕對不要錯誤寫入發言者的畫像！
2. 【事實追加 vs 事實更正/移除】：
   - "facts": 本次發言中新發現/確認的客觀事實（如：新買的物品、新搬居住地、新工作、新喜好）。若無新事實請給空列表 []。
   - "remove_facts": 當用戶在對話中**明確澄清、否定、更正或推翻過去的舊事實**時（例如：「我其實不住台中了，我搬去台北了」或「我根本不喝梅酒」），請將要廢棄/更正的舊事實文字列入此陣列。若無任何要更正/刪除的事實，請給空列表 []。
3. 【社交印象 (interaction_notes)】：
   - 「主觀評價/他人調侃/社交行為/對話風格」放入 "interaction_notes"（請在既有印象基礎上適度綜合補充）。
4. 【過濾噪音與防惡搞】：
   - 忽略無意義的打招呼、純廢話、表情包。
   - 過濾明顯的惡搞、諷刺或反常識胡扯（例如「@某人 其實是外星人」）。
5. 【輸出規範】：
   - 輸出嚴格的 JSON 物件，包含 "updates" 陣列。
   - 每個 update 物件格式：
     * "user_id": 該用戶的 ID（若是已知群友請保留其 ID，若為發言者填寫 "{speaker_id}"）
     * "user_name": 用戶名稱
     * "facts": [本次新發現的事實列表，無則為 []]
     * "remove_facts": [明確需要移除/更正的舊事實列表，無則為 []]
     * "interaction_notes": 綜合更新後的整體互動印象字串
   - 若某位群友在本次對話中完全沒有任何新特徵、更正或印象變化，則無需將其放入 "updates" 陣列中。

【輸出 JSON 範例】：
```json
{{
  "updates": [
    {{
      "user_id": "{speaker_id}",
      "user_name": "{speaker_name}",
      "facts": ["目前定居在台北市"],
      "remove_facts": ["住在台中"],
      "interaction_notes": "發言風格風趣，主動更正了居住地資訊"
    }},
    {{
      "user_id": "987654321",
      "user_name": "桶子",
      "facts": ["最近常熬夜通宵"],
      "remove_facts": [],
      "interaction_notes": "經常被發言者吐槽生活作息混亂"
    }}
  ]
}}
```

請直接輸出 JSON，不要附帶任何多餘文字。"""

def build_batch_dialogue_extraction_prompt(
    dialogue_messages: List[Dict[str, Any]],
    known_profiles: List[Dict[str, Any]]
) -> str:
    """
    建立用於【多輪交談批次提煉 (Batch Dialogue Memory Extraction)】的 Prompt。
    支援整段群聊對話中多個群友的互相交流、實體辨識、特徵累積與更正。
    """
    profiles_text_list = []
    for p in known_profiles:
        u_name = p.get("user_name", "未知群友")
        u_id = str(p.get("user_id", ""))
        facts_str = "、".join(p.get("facts", [])) or "尚無"
        notes_str = p.get("interaction_notes", "") or "尚無"
        profiles_text_list.append(f"- 【{u_name}】(ID: {u_id}):\n  • 目前事實: {facts_str}\n  • 目前印象: {notes_str}")

    profiles_section = "\n".join(profiles_text_list) if profiles_text_list else "（目前尚無相關群友的歷史畫像記錄）"

    dialogue_lines = []
    for msg in dialogue_messages:
        sender_name = msg.get("user_name", "用戶")
        sender_id = msg.get("user_id", "")
        content = msg.get("content", "")
        has_img = " [附圖]" if msg.get("has_image") else ""
        dialogue_lines.append(f"[{sender_name} (ID: {sender_id})]: {content}{has_img}")

    dialogue_section = "\n".join(dialogue_lines)

    return f"""你是一個精通 Discord 群聊社交分析與知識提煉的助理。
以下是一段在群組中累積的多輪對話紀錄，請全局分析這段對話，並為各參與者或被提及的群友進行【多實體特徵提煉與畫像更新】。

【參與者與關係人的已知歷史畫像】:
{profiles_section}

【待分析的多輪交談對話紀錄】:
{dialogue_section}

【提煉與歸屬核心準則】：
1. 【多輪語意理解與實體對齊】：
   - 理解多輪問答前後文脈絡（如 A 詢問某事物，B 回答自己的經驗或持有物，應精準將特徵歸屬於 B）。
   - 當發言者提及或吐槽其他群友（如 @某人 或直呼其名），特徵歸屬於被提及者。
2. 【事實追加 vs 澄清移除】：
   - "facts": 在本次對話中新發現/確認的客觀事實、喜好、擁有物、職業、居住地等。無新事實給空列表 []。
   - "remove_facts": 用戶在對話中明確澄清、推翻或更正的舊事實。無則給空列表 []。
3. 【社交印象 (interaction_notes)】：
   - 記錄在群聊中展現出的性格特點、常聊話題、與其他群友的互動關係等。
4. 【過濾噪音】：
   - 忽略無實質意義的單字、表情包、複誦梗。
5. 【輸出規範】：
   - 輸出嚴格的 JSON 物件，包含 "updates" 陣列。
   - 僅將本次對話中「有新事實、有更正或有印象更新」的群友放入 updates 中。

【輸出 JSON 範例】：
```json
{{
  "updates": [
    {{
      "user_id": "123456789",
      "user_name": "岡部",
      "facts": ["最近在研究時間機器新理論"],
      "remove_facts": [],
      "interaction_notes": "在群裡興奮地分享研究成果"
    }},
    {{
      "user_id": "987654321",
      "user_name": "桶子",
      "facts": ["買了新靜音機械鍵盤"],
      "remove_facts": [],
      "interaction_notes": "熱情地向大家推薦電腦周邊"
    }}
  ]
}}
```

請直接輸出 JSON，不要附帶任何多餘文字。"""
