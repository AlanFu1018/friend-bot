# 行事曆與鬧鐘（Calendar & Alarm）

> 兩套獨立的定時提醒系統：它們的差別、時間解析規則、以及觸發流程。
>
> 最後更新：2026-08-30，已對照程式碼核實。

---

## 1. 兩套系統的差別

系統裡有**兩套結構近乎相同但用途不同**的定時機制：

| | 鬧鐘（Alarm） | 行事曆（Calendar） |
| :--- | :--- | :--- |
| 資料表 | `alarms` | `calendar_events` |
| 定位 | 一次性提醒 | 可查詢的**日程** |
| 送出方式 | 頻道 Embed | **Webhook**（可設定），失敗才退回頻道 |
| 進入對話 prompt | ❌ | ✅ 未來 14 天、最多 10 筆 |
| 可依日期查詢 | ❌ | ✅ `get_user_events_by_date()` |
| 指令 | `/kurisu-alarm-set` / `-list` / `-cancel` | `/kurisu-calendar-set` / `-list` / `-cancel` |

**關鍵差異是「行事曆會進入記憶上下文」**：使用者可以直接在聊天中問「我明天有什麼安排？」，紅莉栖看得到；鬧鐘則純粹是到點會響的計時器。

系統指令第 5 條明確要求模型查看上下文中的行事曆區塊回答排程問題，包括「該日期完全沒有安排」時也要明確告知。

---

## 2. 時間解析

`parse_alarm_time()` 與 `parse_calendar_time()`（各自的 `time_parser.py`）是兩份幾乎相同的實作，支援五種格式：

| 格式 | 範例 | 說明 |
| :--- | :--- | :--- |
| 相對時間 | `10m`、`2h`、`1h30m`、`1d` | 從現在起算 |
| `y/m/d/h/m` | `2026/8/27/15/30` | 完整指定 |
| `y/m/d h:m` | `2026-08-27 15:30` | 同上，不同分隔 |
| `m/d/h/m` | `8/27/15/30` | 自動補當年；若已過則補明年 |
| `h:m` | `15:30` | 自動補今天；**若已過則為明天** |

分隔符號寬鬆——`/`、`\`、`-`、`:`、`.`、空白、以及中文的「年月日點分」都會被正規化掉，因此 `2026年8月27日15點30分` 也能解析。

### 刻意的拒絕

- **三個數字**（如 `8/27/15`）會拋錯，因為無法判斷是「月/日/時」還是「年/月/日」——要求補完整。
- **過去的時間**一律拒絕，錯誤訊息帶人設：「時間只能往前走，時間機器可不能隨便借你用哦！」

解析失敗時拋 `ValueError`，指令層捕捉後以 ephemeral 訊息回覆使用者。

---

## 3. 背景排程器

兩個排程器（`AlarmScheduler` / `CalendarScheduler`）結構相同，在 `setup_hook()` 啟動：

```python
async def _run_loop(self):
    await self.client.wait_until_ready()
    while self._running:
        due = await Manager.get_due_*(current_ts)
        for item in due:
            await self._process_*(item)
        await asyncio.sleep(self.check_interval)   # 5 秒
```

`wait_until_ready()` 是必要的——排程器要用 `client.get_channel()` 發訊息，Discord 連線未就緒時拿不到頻道。

> 對照：提煉撿漏（`sweep_unextracted`）**不需要** `wait_until_ready()`，因為它只碰資料庫、不碰 Discord API。

### 觸發流程

```
掃到到期項目
   ↓
先標記已觸發（mark_*_triggered）   ← 先標記再發送，避免重試風暴
   ↓
以 Gemini 生成紅莉栖風格的提醒台詞
   （temperature=0.85, max_tokens=150, enable_tools=False）
   失敗則退回預寫的 fallback 台詞
   ↓
組裝 Embed / Webhook payload 送出
   ↓
把提醒內容存進 messages（is_bot=1）
```

**先標記後發送**是刻意的：若發送失敗就重試，可能因為 Discord 暫時性錯誤導致同一個提醒連續轟炸使用者。寧可漏一次也不重複。

### 台詞生成

提醒不是固定模板，而是每次請 Gemini 以紅莉栖的口吻現寫一段 1~3 句的催促。生成失敗時退回 `FALLBACK_KURISU_ALARM_QUOTES` 中隨機一則預寫台詞，因此**提醒本身永遠會送出**，只是語氣可能較制式。

---

## 4. Webhook 推送（僅行事曆）

行事曆觸發時優先走 Webhook：

```
target_webhook = 事件自帶的 webhook_url  或  全域 CALENDAR_WEBHOOK_URL
    ↓
POST payload（含頭像 avatar_url）
    ↓
HTTP 200/204 → 成功
其他狀態或例外 → webhook_sent = False → 退回頻道發送
```

Webhook 的用途是讓提醒能推到 Discord 以外的地方（或以自訂頭像/名稱出現）。沒設定時整個機制透明退回一般頻道訊息。

---

## 5. 相關設定

```yaml
calendar:
  webhook_url: ""    # 全域預設 Webhook（選填）
  avatar_url: ""     # Webhook 顯示的頭像（選填）
```

也可用環境變數 `CALENDAR_WEBHOOK_URL` / `CALENDAR_AVATAR_URL` 覆蓋。

排程器的 `check_interval`（5 秒）目前**寫死在建構子**，不可經 `config.yaml` 調整。

---

## 6. 已知問題

### 兩套系統高度重複

`alarm/` 與 `calendar/` 的 `time_parser.py` 幾乎逐字相同；兩個 `scheduler.py` 的迴圈結構、台詞生成、Embed 組裝也大同小異；兩個 Manager 的 CRUD 同構。

這是可觀的重複，但**尚未造成實際問題**——兩者用途確實不同（一次性提醒 vs 可查詢日程），且行事曆多了 Webhook 與日期查詢。合併的收益主要是維護成本，風險則是把兩個目前穩定的子系統攪在一起。目前判斷不值得動。

若日後要調整時間解析規則，記得**兩份都要改**。

### 沒有重複／週期性提醒

兩套系統都只支援一次性觸發，沒有「每週一提醒」這類週期設定。`status` 欄位只有 `pending` / 已觸發兩種狀態。

### 排程精度受輪詢間隔限制

5 秒輪詢意味著提醒最多可能延遲 5 秒。對日常使用無影響，但不適合需要秒級精度的場景。
