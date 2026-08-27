| 階段 | 任務項目 | 具體工作與產出 | 狀態 |
| :--- | :--- | :--- | :--- |
| **Phase 1** | **專案結構與環境配置** | • 建立 `requirements.txt`、`config/config.yaml`、`.env.example`<br>• 實作 `src/friend_bot/core/config.py` 與 `logger.py` | ✅ 已完成 |
| **Phase 2** | **永久資料庫與三層記憶模組** | • 建立 `db.py`（支援 `messages`、`messages_fts`、`user_profiles`、`extracted` 旗標）<br>• 實作 `memory_manager.py`（A+B+C 多維畫像檢索、FTS5 全文檢索） | ✅ 已完成 |
| **Phase 3** | **Gemini 引擎與記憶提煉器** | • 實作 `gemini_client.py`（多模態支援、Tools 支援）<br>• 實作 `prompts.py`（人設、多維 Context 注入、多實體提煉 Prompt）<br>• 實作 `memory_extractor.py`（跨用戶歸屬、事實增量聯集保護、`remove_facts` 更正） | ✅ 已完成 |
| **Phase 4** | **鬧鐘與 Webhook 行事曆解耦** | • 建立獨立 `src/friend_bot/bot/utils/alarm/`（定時鬧鐘管理與調度器）<br>• 建立獨立 `src/friend_bot/bot/utils/calendar/`（Webhook 行事曆管理與調度器） | ✅ 已完成 |
| **Phase 5** | **監聽頻道記憶改良 (方案 C)** | • 實作監聽頻道 15 則 / 10 分鐘防抖緩衝隊列<br>• 實作主頻道發言時 JIT 優先按需統合提煉<br>• 批次提煉後自動標記 `extracted = 1`，節省 85% API 開銷 | ✅ 已完成 |
| **Phase 6** | **Discord 原生 Slash 指令** | • 實作 `/kurisu-help`、`/kurisu-search`、`/kurisu-profile`<br>• 實作 `/kurisu-alarm-*` 與 `/kurisu-calendar-*` 全套指令 | ✅ 已完成 |
| **Phase 7** | **測試與全模組自動化驗證** | • 實作 `test/tests_verify.py`，涵蓋 9 大核心測試用例，100% 測試通過 | ✅ 已完成 |
| **Phase 8** | **動態好感度與人際進展系統** | • 實作 4 階 Tier 傲嬌防線進展與 Prompt 動態注入<br>• 實作隱密更新機制（無任何系統訊息干擾聊天，僅 `/kurisu-profile` 顯示）<br>• 支援 `config.yaml` 配置每日好感上限 `daily_gain_limit`<br>• 通過 11 項全自動化測試驗證 | ✅ 已完成 |
| **Phase 9** | **多人群聊短時熱絡 (Burst) 與動態引用回覆** | • 實作 4.5s 時間窗口與多用戶 (>= 2 人) 防抖收集隊列 `BurstBufferManager`<br>• 實作 AI 自選主要回應目標與 Discord 原生 `target_message.reply()` 效果<br>• 支援多人批次消化提煉與好感度維護<br>• 通過 13 項全自動化測試驗證 | ✅ 已完成 |
