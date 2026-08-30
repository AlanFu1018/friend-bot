# Slash 指令（Commands）

> 10 個指令的用途與參數、Mixin 註冊架構、以及權限模型。
>
> 最後更新：2026-08-30，已對照程式碼核實。

---

## 1. 指令一覽

| 指令 | 用途 | 主要參數 |
| :--- | :--- | :--- |
| `/kurisu-help` | 指令說明手冊 | — |
| `/kurisu-profile` | 查看群友或 bot 自己的畫像 | `user`（選填） |
| `/kurisu-alias` | 別名管理 | `action`、`alias`、`user` |
| `/kurisu-search` | 強制聯網搜尋 | `query` |
| `/kurisu-alarm-set` | 設定一次性鬧鐘 | `time`、`content` |
| `/kurisu-alarm-list` | 查看自己的待觸發鬧鐘 | — |
| `/kurisu-alarm-cancel` | 取消鬧鐘 | `alarm_id` |
| `/kurisu-calendar-set` | 登記行事曆排程 | `time`、`content`、`webhook_url` |
| `/kurisu-calendar-list` | 查看未來一個月排程 | — |
| `/kurisu-calendar-cancel` | 取消排程 | `event_id` |

指令在 `setup_hook()` 中註冊後呼叫 `tree.sync()` 同步至 Discord。**新指令上線後可能需要數分鐘才會出現在客戶端**。

---

## 2. 各指令細節

### `/kurisu-profile`

顯示目標使用者的記憶畫像：好感度進度條、關係階級、已知特徵、互動印象。

- 不指定 `user` → 查自己
- 指定 bot 自己 → 顯示**人設卡**（牧瀨紅莉栖的角色設定），而非記憶畫像
- 事實超過 7 條時精選展示（Top-4 高熱度 + 隨機 3 條），並標註總數，避免卡片過長

回覆是**公開的**（`defer(thinking=False)` 後 `followup.send`）。

### `/kurisu-alias`

見 [`memory_sys_design.md`](memory_sys_design.md#4-別名系統) 的完整設計。

| `action` | 行為 |
| :--- | :--- |
| `add` | 新增別名，須通過四道校驗（格式、碰撞、重複、數量上限） |
| `remove` | 移除別名 |
| `list` | 列出目前別名，含**來源記錄**（自動學習／手動設定、時間、提出者、訊息 ID） |

`list` 一律 ephemeral；`add`/`remove` 成功時公開、失敗時 ephemeral（失敗理由不需讓全群看到）。

### `/kurisu-search`

見 [`web_search.md`](web_search.md)。會載入完整記憶上下文後強制啟用搜尋工具。

### 鬧鐘與行事曆指令

時間格式與兩者的差別見 [`calendar_and_alarm.md`](calendar_and_alarm.md)。`-list` 只列出**自己**的項目；`-cancel` 需提供 `-list` 中顯示的 ID。

---

## 3. 註冊架構

每組指令是一個 Mixin，`FriendBotClient` 多重繼承它們：

```python
class FriendBotClient(
    HelpCommandsMixin, SearchCommandsMixin, ProfileCommandsMixin,
    AliasCommandsMixin, AlarmCommandsMixin, CalendarCommandsMixin,
    discord.Client
):
    async def setup_hook(self):
        self.register_help_commands()
        self.register_search_commands()
        self.register_profile_commands()
        self.register_alias_commands()
        self.register_alarm_commands()
        self.register_calendar_commands()
        await self.tree.sync()
```

每個 `register_*_commands()` 內部以 `@self.tree.command(...)` 裝飾巢狀函式。這個寫法讓指令實作能直接透過閉包存取 `self`（`self.gemini`、`self.memory_extractor` 等），不需另外傳遞。

### 新增一組指令的步驟

1. 在 `bot/commands/` 新增檔案，定義 `XxxCommandsMixin` 與 `register_xxx_commands()`
2. 在 `bot/commands/__init__.py` 匯出
3. 在 `client.py` 的類別定義加入繼承、在 `setup_hook()` 加入註冊呼叫

**三處都要改**——漏掉任何一處指令都不會出現，且不會有錯誤訊息。

---

## 4. 權限模型

### 身分一律取自 Discord

所有指令用 `interaction.user` 判斷呼叫者，**從不信任訊息或參數內容**。這使得指令天然不可偽造——與背景提煉必須靠 `allowed_uids` 白名單防注入形成對比。

### 代他人操作

目前只有 `/kurisu-alias` 支援代操作，需要 `manage_guild` 權限：

```python
if user is not None and user.id != invoker.id:
    perms = getattr(invoker, "guild_permissions", None)
    if not (perms and perms.manage_guild):
        拒絕（ephemeral）
```

`getattr` 是必要的——DM 情境下 `interaction.user` 是 `User` 而非 `Member`，沒有 `guild_permissions` 屬性。

### 其他指令的權限現況

`/kurisu-profile` **任何人都能查任何人的完整畫像**（事實、互動印象、好感度），沒有隱私控制或本人同意機制。這些資料是背景自動蒐集的，當事人未必知道會被公開查詢。記錄於 [`improv.md`](improv.md) 3.2，尚未處理——最小的改法是加上 ephemeral 選項。

鬧鐘與行事曆的 `-list` / `-cancel` 只作用於自己的項目，無代操作管道。

---

## 5. 已知問題

### `general.py` 是死程式碼

`GeneralCommandsMixin`（`bot/commands/general.py`）定義了 `register_general_commands()`，內部呼叫 help／search／profile 三組註冊。但：

- `FriendBotClient` **沒有繼承它**
- `setup_hook()` **沒有呼叫** `register_general_commands()`

它是早期的組合式寫法遺留，目前完全沒有作用。留著的唯一風險是誤導——有人可能以為改它就會生效。可以直接刪除（`commands/__init__.py` 的匯出也要一併移除）。

### 指令註冊分散在三處

如上所述，新增指令要同時改三個地方且沒有任何檢查。`test/tests_verify.py` 有一項 `test_mixin_inheritance_and_command_registration` 驗證繼承鏈，但無法偵測「Mixin 已繼承卻忘了呼叫 register」這種情況——`general.py` 正是漏網之魚。
