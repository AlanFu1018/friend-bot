# 設定與基礎建設（Configuration & Infrastructure）

> 設定如何載入與覆蓋、日誌系統、資料庫遷移機制、命令列參數。
>
> 最後更新：2026-08-30，已對照程式碼核實。

---

## 1. 設定來源

三個來源，載入順序如下：

| 來源 | 內容 | 是否進版控 |
| :--- | :--- | :--- |
| `.env` | 金鑰（Discord Token、Gemini API Key） | ❌ 應被 gitignore |
| `config/config.yaml` | 幾乎所有行為參數 | ✅ |
| `config/persona.md` | 機器人人格 | ✅ |
| `config/kaomoji.yaml` | 顏文字庫 | ✅ |

`core/config.py` 在**匯入時**一次讀完所有設定並展開為模組層級常數（`SHORT_TERM_HISTORY_LIMIT`、`ENABLE_BURST_REPLY` …）。因此**改設定一律需要重啟**，沒有熱重載。

### 覆蓋語意不一致（需注意）

多數設定是「**環境變數覆蓋 YAML**」：

```python
GEMINI_MODEL = os.getenv("GEMINI_MODEL", _gemini_cfg.get("model", "..."))
```

但**頻道 ID 是「取聯集」**：

```python
REPLY_CHANNEL_IDS = _parse_channel_ids(yaml 的值, "REPLY_CHANNEL_IDS")
# YAML 與環境變數兩邊的 ID 會合併，而非後者取代前者
```

也就是說，在 `.env` 設了 `REPLY_CHANNEL_IDS` **不會**移除 `config.yaml` 裡已有的頻道。這是唯一的例外，記錄於 [`improv.md`](improv.md) 6.2。

### 設定檔解析失敗的行為

- **檔案不存在** → 視為正常，全部使用預設值
- **檔案存在但 YAML 格式錯誤** → `logger.error()` 後拋 `RuntimeError` **中止啟動**

第二種是刻意的：格式錯誤時靜默回退成預設值，會讓維運者以為設定已生效，實際上整份設定都沒讀進去。

---

## 2. 設定分區

| 區塊 | 涵蓋 | 詳見 |
| :--- | :--- | :--- |
| `bot` | 頻道 ID、typing、訊息長度上限 | [`chat_and_reply.md`](chat_and_reply.md) |
| `chat_behavior` | 忽略前綴、多氣泡、打字延遲、Burst | [`chat_and_reply.md`](chat_and_reply.md) |
| `web_search` | 搜尋開關、top_k、單頁字元上限 | [`web_search.md`](web_search.md) |
| `calendar` | Webhook URL 與頭像 | [`calendar_and_alarm.md`](calendar_and_alarm.md) |
| `gemini` | 模型、溫度、penalty、輸出上限 | 下方 §3 |
| `memory` | 三層記憶、提煉、別名、三軌 RAG、深度回憶 | [`memory_sys_design.md`](memory_sys_design.md) |
| `favorability` | 好感度開關、初始值、每日上下限 | [`persona_and_favorability.md`](persona_and_favorability.md) |
| `persona` | bot 名稱、人格檔路徑 | [`persona_and_favorability.md`](persona_and_favorability.md) |

---

## 3. Gemini 設定

```yaml
gemini:
  model: "gemini-3.1-flash-lite"
  temperature: 0.87            # 對話用；提煉一律硬編為 0.2
  frequency_penalty: 0.0
  presence_penalty: 0.0
  max_output_tokens: 2048
```

### Penalty 的自動降級

多數 Gemini 模型後端尚未啟用 penalty 參數。`GeminiClient.generate_response()` 會捕捉相關的 400 錯誤，**移除 penalty 後自動重試一次**，確保對話不中斷：

```python
if "Penalty is not enabled for this model" in err_msg or "penalty" in err_msg.lower():
    safe_config = 不含 penalty 的設定
    重試
```

### 溫度的分工

`temperature` 只作用於對話生成。背景提煉硬編為 `0.2`（需要穩定的 JSON 輸出），鬧鐘台詞生成硬編為 `0.85`。這三者刻意不共用同一個設定。

---

## 4. 日誌

`setup_logger()`（`core/logger.py`）在 `main.py` 最先執行：

| Handler | 目標 | 格式 |
| :--- | :--- | :--- |
| Console | `stdout` | 彩色，`時間 \| 等級 \| 模組 : 訊息` |
| File | `logs/friend_bot.log` | 加上 `[檔名:行號]` |

檔案採 `RotatingFileHandler`，單檔 10MB、保留 5 份備份（最多約 60MB）。建立失敗時只警告，不影響啟動。

`propagate = False` 避免訊息向 root logger 傳遞造成重複輸出。各模組以 `get_logger("memory")` 取得子 logger，名稱會出現在每一行日誌中，便於定位。

預設等級 `INFO`。**幾個關鍵診斷訊息在 `DEBUG` 等級**，例如：

```
🗑️ [刪除詞未套用]「咖啡」未達最低引述門檻…       ← 追查更正為何沒生效
🏷️ [別名提議未採納] …                            ← 追查別名為何沒學到
✅ [提煉完成] 頻道 […] N 則訊息、M 位發言者
```

要診斷記憶行為時把等級調到 `DEBUG`（目前需修改 `setup_logger()` 的預設參數，未做成設定項）。

---

## 5. 資料庫與遷移

`init_db()`（`memory/db.py`）在連線 Discord **之前**執行：

```
建表（IF NOT EXISTS）
   ↓
逐欄位檢查並 ALTER（相容舊版資料庫）
   ↓
_run_migrations()  依 PRAGMA user_version 分階段執行
```

### 連線設定

```python
PRAGMA journal_mode=WAL;    # 允許讀寫並行
PRAGMA busy_timeout=5000;   # 鎖競爭自動重試而非直接拋錯
```

WAL 是必要的——大量 `asyncio.create_task` 的背景提煉會與對話流程同時寫入。

### Schema 版本

| 版本 | 內容 |
| :--- | :--- |
| 1 | `messages_fts` 改存 n-gram 檢索字串 |
| 2 | 回填被改錯的 `user_name`；重置被灌水的 `hits` |
| 3 | 清除以名字為主鍵的幽靈畫像 |

各階段**皆為 idempotent**，任一階段失敗都不提升版本號，下次啟動整批重跑。詳見 [`memory_sys_design.md`](memory_sys_design.md#8-資料庫結構)。

### 兩種欄位新增方式

- **`ALTER TABLE` 檢查**（如 `aliases`、好感度四欄）：純新增欄位且有預設值，不需版本控管
- **版本遷移**（如上表三項）：需要轉換既有資料

---

## 6. 命令列參數

```bash
python main.py [選項]
```

| 參數 | 行為 |
| :--- | :--- |
| `--clear-memory` | 清空**所有**記憶（訊息、FTS、畫像、行事曆、鬧鐘） |
| `--clear-history` | 只清對話歷史與 FTS，**保留畫像與行事曆** |
| `--clear-profiles` | 只清使用者畫像，**保留歷史與行事曆** |
| `--only-clear` | 清理後直接結束，不啟動 bot |

清理在 `init_db()` **之後**執行，因此遷移仍會先跑過。`--only-clear` 可用於維護而不上線。

> 這些操作**不可逆且無確認提示**。`--clear-profiles` 會抹掉所有累積的事實、別名、好感度——那是重建成本最高的資料。

---

## 7. 已知問題

### 循環匯入

`ai/gemini_client.py` 匯入 `bot/utils/emotion.py`（顏文字渲染），而 `bot/__init__.py` 匯入 `client.py`，後者又匯入 `ai/gemini_client.py`。

結果是**匯入順序敏感**：直接 `from src.friend_bot.ai.memory_extractor import MemoryExtractor` 會失敗，必須先載入 `bot` 套件下的任一子模組。`test/tests_verify.py` 恰好因為先匯入 `bot.utils.alarm` 而正常運作，但這是巧合而非設計。

```python
# 可行
import src.friend_bot.bot.utils.emotion
from src.friend_bot.ai.memory_extractor import MemoryExtractor

# 失敗：ImportError: cannot import name 'GeminiClient' from partially initialized module
from src.friend_bot.ai.memory_extractor import MemoryExtractor
```

根因是層級倒置——`ai` 不該依賴 `bot`。乾淨的做法是把 `emotion.py` 移到 `core/` 或 `ai/`。

### 每次操作都開新的資料庫連線

`get_db_connection()` 每次呼叫都 `aiosqlite.connect()` 再關閉。高頻對話下（每則訊息觸發多次 DB 存取）有不必要的連線建立開銷。記錄於 [`improv.md`](improv.md) 2.1，尚未處理。

### `messages` 無保留策略

訊息表與 FTS 索引沒有任何清理或歸檔機制，會隨時間無限增長。n-gram 索引使 FTS 體積約為原本的 4~5 倍，放大了這個問題。記錄於 [`improv.md`](improv.md) 1.3。

### 日誌等級不可設定

`setup_logger(log_level=logging.INFO)` 的預設值寫死在函式簽章，`main.py` 呼叫時未傳參。要開 DEBUG 必須改程式碼。
