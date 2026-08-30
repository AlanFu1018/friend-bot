from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import re
from src.friend_bot.core.config import (
    SYSTEM_PROMPT, BOT_NAME, ENABLE_FAVORABILITY,
    ENABLE_MUSIC_SUGGESTION, MUSIC_PLAY_COMMAND
)
from src.friend_bot.memory.memory_manager import MemoryManager

# 4 階 Tier 傲嬌態度動態指令對照表
TIER_ATTITUDE_MAP = {
    "stranger": "【對此用戶態度 (Tier 1 陌生警戒)】：對方為初識或關係疏離者。請保持冷淡、嚴肅、講究邏輯的態度，不主動開玩笑，不接受曖昧調侃，嚴格公事公辦。",
    "familiar": "【對此用戶態度 (Tier 2 熟識群友)】：對方為熟悉群友。展現經典傲嬌風格，嘴硬心軟，適度吐槽與接梗，受到調侃或誇獎時害羞反駁（例如：「哈？別誤會了！」）。",
    "trusted": "【對此用戶態度 (Tier 3 實驗室夥伴)】：對方為深受信賴的實驗室夥伴。傲嬌防線大幅變薄，極易害羞破防臉紅，會主動在傲嬌口吻中流露對其健康、作息與日常的關心。",
    "cherished": "【對此用戶態度 (Tier 4 靈魂共鳴)】：對方為命運石之門級別的不可替代夥伴。展現真誠溫柔與高度信賴（嬌70%/傲30%），深層羈絆堅定不移，危急時毫不猶豫站在對方身邊。"
}

def get_current_time_str() -> str:
    """獲取當前本地時間字串（含星期）"""
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday_str = weekdays[now.weekday()]
    return now.strftime(f"%Y年%m月%d日 %H:%M:%S ({weekday_str})")

MUSIC_SUGGESTION_RULE = """10. 【音樂推薦】：
   - 上下文若出現【語音頻道現況】，代表那些人此刻正和發言者同在語音頻道裡。
   - 當對話自然聊到音樂、心情或氣氛時，你可以推薦一首歌，並**結合在場者已知的喜好**挑選。
   - 推薦時附上可直接複製執行的指令，格式為：`{play_command} 歌名 - 演出者`
   - 你自己無法播放音樂，指令要由群友複製去執行——用自然的語氣帶出來，別像客服念稿。
   - 不要每次都推薦；只在話題自然帶到時才做。"""

def build_system_instruction() -> str:
    """建立系統人格與核心規則指令"""
    current_time = get_current_time_str()
    music_rule = (
        MUSIC_SUGGESTION_RULE.format(play_command=MUSIC_PLAY_COMMAND)
        if ENABLE_MUSIC_SUGGESTION else ""
    )
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
4. 【動態人際關係與態度指引】：
   - 上下文中若標註了【對此用戶態度】，請嚴格遵循該態度階級（Tier 1~4）所對應的傲嬌防線與情緒反饋，對不同親密度的群友展現層次分明的性格。
5. 【行事曆與排程查詢】：
   - 若用戶在對話中詢問排程、行程、待辦或提醒事項（例如：「我今天有什麼排程嗎？」、「8/27 我有什麼安排？」、「明天幾點要開會？」等）：
   - 請仔細查看上下文中的【用戶已登記的行事曆與排程 (Calendar Schedules)】。
   - 以牧瀨紅莉栖的口吻（傲嬌、嘴硬心軟、科學家嚴謹風格）具體回答他在該日期/時間的排程內容與時間。
   - 若該日期完全沒有任何排程記錄，也請傲嬌且明確地告訴他沒有安排（例如：「哼，我幫你看過了，你那天明明什麼都沒排，別自己疑神疑鬼的！才..才...才不是因為擔心你會錯過喔，只是順便而以[emotion:tsundere]」）。
6. 【聯網搜尋工具 (search_web)】：
   - 當用戶明確要求搜尋（如標註【強制聯網搜尋】、使用 /kurisu-search 指令）、詢問「最新新聞」、「即時時事」、「科技動態」、「即時天氣」或需要查證現實世界資訊時，必須主動調用 `search_web` 工具檢索最新資料。
   - 檢索取得聯網搜尋結果後，**必須認真閱讀搜尋內容，並將搜尋結果的精華實質重點融合成立體的回覆內容**，以你的角色口吻告訴用戶具體的新聞或事件內容。
7. 【當前時間日期】：若被問及「現在幾點」、「今天幾號」、「星期幾」等時間問題，請直接根據 [基本資訊] 中的【當前系統真實時間】精準回答。
8. 若參考了該用戶的長期記憶或歷史回憶，請自然融入，切勿生硬複誦「我從資料庫查到你喜歡...」。
9. 不需要每次回覆都把對方的名字掛在嘴邊，保持自然聊天節奏。
{music_rule}"""

def format_alias_hint(profile: Optional[Dict[str, Any]]) -> str:
    """
    將使用者的別名渲染成「（大家也叫他：桶子、阿桶）」這樣的提示片段。

    別名是用來「解析出該載入誰的畫像」的，但若不把對應關係一併寫進 prompt，
    模型只會看到 Discord 顯示名稱，得自己猜對話中的綽號指的是誰——人一多就不可靠，
    猜錯時事實會被歸給錯的人或整個漏掉。因此凡是把某人放進 prompt 的地方，
    都必須附上他的別名。
    """
    if not profile:
        return ""
    names = MemoryManager.alias_texts(profile.get("aliases"))
    return f"（大家也叫他：{'、'.join(names)}）" if names else ""

def format_voice_channel_context(voice_context: Optional[Dict[str, Any]]) -> str:
    """
    渲染【語音頻道現況】區塊，讓模型知道此刻誰和發言人同在語音頻道裡。

    voice_context 格式：{"channel_name": str, "members": [{"user_id","user_name","aliases"}]}
    無語音資訊（發言人不在語音頻道）時回傳空字串，該區塊整塊不會進入 prompt。

    只有「發言人自己所在的語音頻道」會被帶入——發言人不在語音時一律不注入，
    不去猜「人數最多的頻道」。這讓推薦對象的定義沒有歧義：發言人與同頻道的其他人。
    """
    if not voice_context:
        return ""

    members = voice_context.get("members") or []
    if not members:
        return ""

    names = []
    for m in members:
        name = str(m.get("user_name") or "群友")
        alias_hint = format_alias_hint(m)
        names.append(f"{name}{alias_hint}")

    channel_name = voice_context.get("channel_name") or "語音頻道"
    return (
        "【語音頻道現況】:\n"
        f"- 頻道「{channel_name}」目前有 {len(members)} 人：" + "、".join(names)
    )

def format_history_timestamp(raw_ts: Any) -> str:
    """將歷史回憶訊息的 Unix timestamp 格式化為可讀日期；無法解析時回傳空字串"""
    if raw_ts in (None, ""):
        return ""
    try:
        return datetime.fromtimestamp(int(raw_ts)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError, OverflowError):
        return ""

def format_memory_context(
    current_user_name: str,
    user_profile: Optional[Dict[str, Any]],
    deep_history: List[Dict[str, Any]],
    short_term_history: List[Dict[str, Any]],
    calendar_summary: str = "",
    other_user_profiles: Optional[List[Dict[str, Any]]] = None,
    voice_context: Optional[Dict[str, Any]] = None
) -> str:
    """將三層記憶、多人畫像、好感度態度與行事曆排程組合成結構化的 Context 提示文字"""
    context_parts = []

    # 1. 發言者個人長期畫像與好感度態度指引 (第 2 層)
    if user_profile:
        speaker_alias = format_alias_hint(user_profile)
        profile_lines = [f"【主要發言者 {current_user_name}{speaker_alias} 的個人特徵記憶】:"]
        
        # 好感度態度動態注入
        tier = user_profile.get("relationship_tier", "familiar")
        if ENABLE_FAVORABILITY and tier in TIER_ATTITUDE_MAP:
            profile_lines.append(f"- {TIER_ATTITUDE_MAP[tier]}")

        facts = user_profile.get("facts", [])
        if facts:
            fact_strings = MemoryManager.to_fact_texts(facts)
            profile_lines.append("- 已知特徵/喜好: " + "、".join(fact_strings))
        notes = user_profile.get("interaction_notes", "")
        if notes:
            profile_lines.append(f"- 互動印象與習慣:\n{notes}")
        context_parts.append("\n".join(profile_lines))

    # 2. 對話中提及 / 近期在場的其他群友畫像
    if other_user_profiles:
        other_lines = ["【對話中提及 / 近期在場的其他群友畫像】:"]
        for o_profile in other_user_profiles:
            o_name = o_profile.get("user_name", "群友")
            o_facts = o_profile.get("facts", [])
            o_notes = o_profile.get("interaction_notes", "")
            o_tier = o_profile.get("relationship_tier", "familiar")
            
            fact_strings = MemoryManager.to_fact_texts(o_facts)
            fact_str = "、".join(fact_strings) if fact_strings else "尚無特定記錄"
            note_str = o_notes if o_notes else "尚無特別印象"
            
            other_lines.append(
                f"- 用戶名稱: {o_name}{format_alias_hint(o_profile)} (關係階級: {o_tier})"
            )
            if ENABLE_FAVORABILITY and o_tier in TIER_ATTITUDE_MAP:
                other_lines.append(f"  • {TIER_ATTITUDE_MAP[o_tier]}")
            other_lines.append(f"  • 已知特徵: {fact_str}")
            other_lines.append(f"  • 互動印象: {note_str}")
        context_parts.append("\n".join(other_lines))

    # 3. 語音頻道現況（僅在發言人身處語音頻道時才有內容）
    voice_block = format_voice_channel_context(voice_context)
    if voice_block:
        context_parts.append(voice_block)

    # 4. 用戶已登記的行事曆與排程 (Calendar Schedules)
    if calendar_summary and calendar_summary.strip():
        context_parts.append(calendar_summary.strip())

    # 5. 歷史深度回憶 (第 3 層)
    if deep_history:
        history_lines = ["【過去的歷史話題回憶 (供參考，若相關可自然提及)】:"]
        for item in deep_history:
            u_name = item.get("user_name", "未知")
            content = item.get("content", "")
            # 使用 timestamp（Discord 上實際發言時間），而非 messages.created_at（寫入資料庫的時間）
            spoken_at = format_history_timestamp(item.get("timestamp"))
            prefix = f"[{spoken_at}] " if spoken_at else ""
            history_lines.append(f"- {prefix}{u_name}: {content}")
        context_parts.append("\n".join(history_lines))

    # 6. 近期頻道對話紀錄 (第 1 層)
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

def build_burst_dialogue_prompt(
    memory_context: str,
    burst_messages: List[Dict[str, Any]]
) -> str:
    """
    建立【多人群聊短時熱絡 (Burst) 聚合回覆】專用 Prompt。
    引導模型在開頭輸出 [TARGET_ID: <message_id>] 標記引用目標，並生成兼顧多人語境的傲嬌回覆。
    """
    msg_lines = []
    for idx, m in enumerate(burst_messages, start=1):
        m_id = str(m.get("message_id", ""))
        u_name = m.get("user_name", "群友")
        content = m.get("content", "")
        has_img = " [附圖]" if m.get("has_image") else ""
        msg_lines.append(f"{idx}. [ID: {m_id}] {u_name}: {content}{has_img}")

    burst_block = "\n".join(msg_lines)

    return f"""{memory_context}

【💬 多人群聊即時熱烈討論 (短時間內有多位群友連續發言)】:
{burst_block}

【回覆指導原則】：
1. 這是一段多人同時搶話/熱烈聊天的場景。請你在回覆時：
   - 第一行必須明確標記你主要想要「引用回覆（Reply）」的訊息 ID，格式為：`[TARGET_ID: <message_id>]`。
   - 緊接著在下一行開始輸出你的回覆文字。
2. 你的回覆應當**主要聚焦於該被引用的訊息/群友**（解答、反駁、傲嬌吐槽），但**同時可極為自然地順手吐槽或兼顧在場其他群友的發言**，展現立體的群友默契。
3. 保持牧瀨紅莉栖的傲嬌/理性/幽默群友性格。

請生成回覆（開頭務必包含 [TARGET_ID: 訊息ID] 標籤）："""

def parse_burst_reply_response(raw_text: str, default_target_id: str = "") -> Tuple[str, str]:
    """
    解析 Burst 回覆文字，提取 target_message_id 與純回覆內容。
    回傳: (target_message_id, reply_content)
    """
    cleaned = raw_text.strip()
    match = re.search(r'\[TARGET_ID:\s*([a-zA-Z0-9_\-]+)\]', cleaned, re.IGNORECASE)
    if match:
        target_id = match.group(1).strip()
        # 移除標籤部分
        content = re.sub(r'\[TARGET_ID:\s*([a-zA-Z0-9_\-]+)\]', '', cleaned, flags=re.IGNORECASE).strip()
        return target_id, content
    
    return default_target_id, cleaned

# 【輸出規範】與收尾指令：兩種提煉 Prompt（單次即時 / 多輪批次）共用同一份輸出格式要求，
# 抽成共用常數避免未來調整輸出格式時漏改其中一處。至於「好感度評估」「深度結構化社交印象」等規則，
# 單次即時與多輪批次兩種情境刻意使用不同措辭調校（前者針對單則發言的即時反應，後者針對整段對話的
# 總結），因此保留在各自函式中分別維護，不強行合併。
_EXTRACTION_ALIAS_RULE = """5. 【別名提議 (aliases)】：
   - 若對話中有人以**顯示名稱以外的慣用綽號**稱呼某位在場群友（例如顯示名稱是
     「daru_1024」，但大家都叫他「桶子」），請將該綽號填入那位群友的 "aliases"。
   - 僅在你**明確判斷該綽號指的就是這個人**時才填，無法確定請給空列表 []。
   - 只能提議「本次對話中確實出現的人」的綽號；不要替沒有出現的第三方取名，
     也不要把話題內容、物品名稱或稱謂（例如「學長」「老師」）當成綽號。
   - 這是為了讓機器人日後聽到綽號時能認出是誰，不是用來記錄事實。"""

_EXTRACTION_OUTPUT_FORMAT_RULE = """6. 【輸出規範】：
   - 輸出嚴格的 JSON 物件，包含 "updates" 陣列。"""

_EXTRACTION_OUTPUT_CLOSING_INSTRUCTION = "請直接輸出 JSON，不要附帶任何多餘文字。"

def build_multi_entity_extraction_prompt(
    speaker: Dict[str, Any],
    other_users: List[Dict[str, Any]],
    recent_messages: List[str]
) -> str:
    """
    建立用於【多實體特徵提煉、跨用戶歸屬、好感度評估、事實更正與結構化深度印象】的 Prompt。
    """
    speaker_name = speaker.get("user_name", "當前發言者")
    speaker_id = str(speaker.get("user_id", ""))
    raw_sp_facts = speaker.get("facts", [])
    speaker_facts = "、".join(MemoryManager.to_fact_texts(raw_sp_facts)) or "尚無"
    speaker_notes = speaker.get("interaction_notes", "") or "尚無"
    speaker_fav = speaker.get("favorability", 30)
    speaker_alias = format_alias_hint(speaker)

    other_users_text = ""
    if other_users:
        lines = []
        for u in other_users:
            u_name = u.get("user_name", "群友")
            u_id = str(u.get("user_id", ""))
            raw_u_facts = u.get("facts", [])
            u_facts = "、".join(MemoryManager.to_fact_texts(raw_u_facts)) or "尚無"
            u_notes = u.get("interaction_notes", "") or "尚無"
            lines.append(f"- 【{u_name}】{format_alias_hint(u)}(ID: {u_id}):\n  • 目前事實: {u_facts}\n  • 目前印象: {u_notes}")
        other_users_text = "\n".join(lines)
    else:
        other_users_text = "（本次無特定在場的其他群友畫像）"

    messages_str = "\n".join(f"- {msg}" for msg in recent_messages)

    return f"""你是一個精通群聊社交、好感度評估與實體關係分析的記憶提煉助理。
請分析發言者【{speaker_name}】在 Discord 中的最新發言記錄，並執行【多實體特徵歸屬提煉、好感度微調評估、事實自我更正與多維度深度印象生成】。

【當前發言者 (Speaker)】:
- 名稱: {speaker_name}{speaker_alias} (ID: {speaker_id})
- 目前好感度: {speaker_fav}
- 目前已記錄事實: {speaker_facts}
- 目前已記錄互動印象: {speaker_notes}

【對話中提及 / 在場的已知其他群友 (Mentioned / In-context Users)】:
{other_users_text}

【最新發言記錄】:
{messages_str}

【提煉與好感度評估核心規則】：
1. 【精準歸屬】：
   - 若發言者自述（例如「我最近在玩星鐵」、「我換新工作了」），將特徵歸屬於【發言者】。
   - 若發言者提及他人（例如「桶子每天都在熬夜」、「@岡部 超討厭吃青椒」），必須將特徵精準歸屬於被提及的人。
2. 【事實追加 vs 事實更正/移除】：
   - "facts": 本次發言中新發現/確認的客觀事實。若無新事實請給空列表 []。
   - "remove_facts": 當用戶在對話中**明確澄清、否定、更正或推翻過去的舊事實**時（例如：「我其實不住台中了，我搬去台北了」），請將要廢棄的舊事實列入此陣列。
   - ⚠️ **必須從上方「目前已記錄事實」中挑出該條並原文照抄整句**，不可只給關鍵詞、不可改寫或縮短。
     例如要廢棄「每天早上都要喝一杯咖啡」，就填完整的「每天早上都要喝一杯咖啡」，而不是「咖啡」——
     只給關鍵詞無法判斷該刪哪一條（可能誤刪「喜歡咖啡廳的氣氛」等無關事實），系統會直接忽略。
   - 若目前已記錄事實中沒有需要廢棄的項目，請給空列表 []。
3. 【好感度增減評估 (favorability_delta)】：
   - 數值為整數 (-2 ~ +2)：
     * +1 ~ +2：送紅莉栖禮物（Dr Pepper、咖啡）、認真討論科學話題、由衷感謝、關心紅莉栖、氛圍融洽之日常互動。
     * 0：一般客套問答、一般日常打招呼、陳述客觀事實。
     * -1 ~ -2：惡意挑釁、無理取鬧、人身攻擊、傳播嚴重偽科學且態度頑劣。
4. 【深度結構化社交印象 (interaction_notes)】：
   - 請將用戶的互動印象結構化為三個維度（約 80~150 字），格式必須包含以下三個標籤：
     * 【核心性格】：沉澱長期穩定的人格基調（中二、理性、溫柔、幽默自嘲等），若歷史已有記錄請參考保留並適度微調深化。
     * 【社交關係】：記錄對特定群友（如桶子、真由理等）的互動默契與態度，以及與紅莉栖的互動張力（傲嬌、調侃、尊重等）。
     * 【近期動態】：根據最新對話滾動更新當前的生活狀態、話題焦點、抱怨、作息或情緒動向。
{_EXTRACTION_ALIAS_RULE}
{_EXTRACTION_OUTPUT_FORMAT_RULE}

【輸出 JSON 範例】：
```json
{{
  "updates": [
    {{
      "user_id": "{speaker_id}",
      "user_name": "{speaker_name}",
      "facts": ["目前定居在台北市"],
      "remove_facts": ["住在台中市，離公司很近"],
      "aliases": [],
      "interaction_notes": "【核心性格】極度理性中帶著傲嬌，對未知科學充滿狂熱，是團隊的核心推手。\\n【社交關係】對桶子愛吐槽但非常信任，面對紅莉栖時嘴硬卻常被科學論點破防。\\n【近期動態】最近因為連夜做實驗而顯得疲憊，多次向群友抱怨程式 bug，互動時情緒較平時更直接。",
      "favorability_delta": 1
    }},
    {{
      "user_id": "987654321",
      "user_name": "桶子",
      "facts": ["最近常熬夜通宵"],
      "remove_facts": [],
      "aliases": ["桶子"],
      "interaction_notes": "【核心性格】幽默隨和、專精技術的超級駭客，對二次元文化充滿熱忱。\\n【社交關係】常被岡部與真由理吐槽生活作息，但關鍵時刻極度可靠。\\n【近期動態】近期沉迷新出的 Galgame 與鍵盤硬體，連日熬夜打電動。",
      "favorability_delta": 0
    }}
  ]
}}
```

{_EXTRACTION_OUTPUT_CLOSING_INSTRUCTION}"""

def build_batch_dialogue_extraction_prompt(
    dialogue_messages: List[Dict[str, Any]],
    known_profiles: List[Dict[str, Any]]
) -> str:
    """
    建立用於【多輪交談批次提煉 (Batch Dialogue Memory Extraction)】的 Prompt。
    """
    profiles_text_list = []
    for p in known_profiles:
        u_name = p.get("user_name", "未知群友")
        u_id = str(p.get("user_id", ""))
        raw_p_facts = p.get("facts", [])
        facts_str = "、".join(MemoryManager.to_fact_texts(raw_p_facts)) or "尚無"
        notes_str = p.get("interaction_notes", "") or "尚無"
        fav_val = p.get("favorability", 30)
        profiles_text_list.append(f"- 【{u_name}】{format_alias_hint(p)}(ID: {u_id}, 目前好感: {fav_val}):\n  • 目前事實: {facts_str}\n  • 目前印象: {notes_str}")

    profiles_section = "\n".join(profiles_text_list) if profiles_text_list else "（目前尚無相關群友的歷史畫像記錄）"

    dialogue_lines = []
    for msg in dialogue_messages:
        sender_name = msg.get("user_name", "用戶")
        sender_id = msg.get("user_id", "")
        content = msg.get("content", "")
        has_img = " [附圖]" if msg.get("has_image") else ""
        dialogue_lines.append(f"[{sender_name} (ID: {sender_id})]: {content}{has_img}")

    dialogue_section = "\n".join(dialogue_lines)

    return f"""你是一個精通 Discord 群聊社交分析、好感度評估與知識提煉的助理。
以下是一段在群組中累積的多輪對話記錄，請全局分析這段對話，並為各參與者或被提及的群友進行【多實體特徵提煉、好感度增減評估、事實更正與多維度深度印象更新】。

【參與者與關係人的已知歷史畫像】:
{profiles_section}

【待分析的多輪交談對話記錄】:
{dialogue_section}

【提煉與歸屬核心準則】：
1. 【多輪語意理解與實體對齊】：
   - 理解多輪問答前後文脈絡，將事實精準歸屬於對應人選。
2. 【事實追加 vs 澄清移除】：
   - "facts": 在本次對話中新發現/確認的客觀事實、喜好等。無新事實給空列表 []。
   - "remove_facts": 用戶在對話中明確澄清、推翻或更正的舊事實。無則給空列表 []。
   - ⚠️ **必須從上方該用戶的「目前事實」中原文照抄整句**，不可只給關鍵詞或改寫。
     只給關鍵詞無法判斷該刪哪一條，系統會直接忽略。
3. 【好感度增減評估 (favorability_delta)】：
   - **好感度衡量的是「該用戶對紅莉栖本人的態度」，而非群友之間的互動氛圍。**
     這段對話紅莉栖並未參與，因此多數情況應給 0。
   - 針對有發言互動的參與者評估 (-2 ~ +2 整數)：
     * +1 ~ +2：**對紅莉栖本人**表達善意、關心、感謝、正面評價或期待。
     * 0：一般日常閒聊、群友之間的互動（即使氣氛熱絡或互相調侃），只要未直接涉及紅莉栖一律給 0。
     * -1 ~ -2：**對紅莉栖本人**惡意挑釁、貶低或人身攻擊。群友之間互嗆與紅莉栖無關，不得扣分。
4. 【深度結構化社交印象 (interaction_notes)】：
   - 結構化為三維度（約 80~150 字），包含標籤：
     * 【核心性格】：沉澱長期穩定的人格特質基調，參考歷史記錄適度保留。
     * 【社交關係】：記錄對其他群友的互動態度，以及與紅莉栖的互動默契。
     * 【近期動態】：根據此批對話總結最近的生活焦點、情緒或話題動態。
{_EXTRACTION_ALIAS_RULE}
{_EXTRACTION_OUTPUT_FORMAT_RULE}

【輸出 JSON 範例】：
```json
{{
  "updates": [
    {{
      "user_id": "123456789",
      "user_name": "岡部",
      "facts": ["最近在研究時間機器新理論"],
      "remove_facts": [],
      "aliases": [],
      "interaction_notes": "【核心性格】中二狂氣科學家風格，熱衷於發表作戰計畫。\\n【社交關係】常與桶子交流實驗室進展，對紅莉栖愛反駁卻深受信賴。\\n【近期動態】在群裡興奮地分享新的時間理論研究成果。",
      "favorability_delta": 1
    }},
    {{
      "user_id": "987654321",
      "user_name": "桶子",
      "facts": ["買了新靜音機械鍵盤"],
      "remove_facts": [],
      "aliases": ["桶子"],
      "interaction_notes": "【核心性格】技術精湛且熱愛二次元文化的頂級駭客。\\n【社交關係】常與岡部互相吐槽，是實驗室的技術頂樑柱。\\n【近期動態】熱情地向大家推薦電腦週邊與靜音鍵盤。",
      "favorability_delta": 0
    }}
  ]
}}
```

{_EXTRACTION_OUTPUT_CLOSING_INSTRUCTION}"""
