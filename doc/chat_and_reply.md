# 對話與回覆（Chat & Reply）

> 訊息如何進入系統、如何決定回不回、以及回覆如何送出。
> 記憶檢索與 prompt 組裝的部分見 [`prompt_pipeline.md`](prompt_pipeline.md)。
>
> 最後更新：2026-08-30，已對照程式碼核實。

---

## 1. 三種頻道模式

`on_message()`（`bot/client.py`）依序判斷：

| 順序 | 條件 | 行為 |
| :--- | :--- | :--- |
| 0 | 作者是 bot 或自己 | 直接忽略 |
| 1 | 訊息以 `ignore_prefixes` 開頭（`#`、`＃`、`//`） | **完全繞過**：不記錄、不提煉、不回覆 |
| 2 | 頻道在 `listen_channel_ids` | 存庫 + 加入防抖佇列，**不回覆** |
| 3 | 頻道在 `reply_channel_ids` | 進入 Burst 緩衝，準備回覆 |
| — | 其他頻道 | 不做任何事 |

忽略前綴的用途是讓群友能在頻道裡講「旁白」而不污染記憶——例如協調事情、貼連結。**它比監聽更徹底**：監聽頻道會記錄，忽略前綴連記錄都不做。

> 頻道 ID 可在 `config.yaml` 或 `.env` 設定，兩者取**聯集**（與其他設定「環境變數覆蓋 YAML」的語意不同，見 [`configuration.md`](configuration.md)）。

---

## 2. Burst 多人聚合

`BurstBufferManager`（`bot/utils/burst/burst_manager.py`）以滑動視窗收集短時間內的多人發言，一次回覆整段而非逐則回應。

### 觸發規則

```
每收到一則訊息：
  加入該頻道的緩衝
  distinct_users = 緩衝中不同發言者數
  is_burst = distinct_users >= min_user_count (2)

  若緩衝訊息數 >= max_burst_messages (5)：
      取消計時器 → 立即觸發
  否則：
      重設計時器：is_burst ? window_seconds (4.5s) : 1.2s
```

單人只等 1.2 秒，避免一個人講話時被拖慢；一旦第二個人加入就延長到 4.5 秒，讓對話收集得更完整。

### 為什麼要聚合

群聊中三個人連續丟話時，逐則回覆會讓 bot 洗版，且每則都缺少其他人的語境。聚合後模型一次看到完整的多人交鋒，回覆更像真的在參與對話。

### 動態引用回覆

Burst 模式下，prompt 要求模型在第一行輸出 `[TARGET_ID: <message_id>]` 指定要引用誰：

```
1. [ID: 1401...] 岡部: 桶子今天又在通宵打遊戲！
2. [ID: 1402...] 桶子: 哪有，我是在編譯程式碼好嗎！
```

`parse_burst_reply_response()` 抽出該 ID，比對出對應的 `discord.Message` 做原生 Reply。模型沒給或給錯 ID 時，退回引用本批最後一則訊息。

這讓 bot 能在多人搶話時**選擇性地回應某一句**，而不是總是回覆最後一個講話的人。

---

## 3. 回覆生成

```
組裝 prompt（見 prompt_pipeline.md）
      ↓
下載圖片附件（若有）
      ↓
顯示 typing 狀態（show_typing）
      ↓
gemini.generate_response()   ← 模型可在此調用 search_web
      ↓
顏文字渲染（見 emotion_kaomoji.md）
      ↓
多氣泡切分
      ↓
第一則用 Reply 引用，其餘依序送出
      ↓
存 bot 自己的回覆（extracted=1，不再提煉）
      ↓
背景提煉（見 memory_sys_design.md）
```

### 圖片理解

`download_image_attachments()`（`bot/handlers.py`）下載 jpeg／png／webp／gif 附件，以 `types.Part.from_bytes` 傳給 Gemini 做多模態理解。無附件時不影響流程。

短期記憶中的圖片訊息會標註 `[附帶圖片]`，讓模型知道那則訊息有圖但內容不在 context 裡。

---

## 4. 多氣泡分段發送

`split_message()`（`bot/handlers.py`）把回覆切成多則短訊息，模擬真人打字節奏而非一次貼一大段。

### 切分規則（依序）

1. **保護程式碼區塊**：` ```...``` ` 整塊獨立發送，不被切開。超過 Discord 2000 字上限時才分片，並為每片補回語言標記。
2. **依換行切分**：`enable_multi_bubble` 開啟時，每一行成為獨立訊息。
3. **長行再拆句**：單行超過 `bubble_target_length`（47 字）時，依句末標點（`。！？!?…`）自然拆句。
4. **硬上限保護**：任何一則都不超過 `max_message_length`（2000）。

### 送出節奏

第一則之後，每則之間插入延遲：

```python
calc_delay = min(max_delay, max(min_delay, len(chunk) * 0.015))
actual_delay = max(min_delay, calc_delay + random.uniform(-0.1, 0.2))
```

依字數估算「打字時間」再加隨機抖動，範圍受 `typing_delay_range`（0.6~1.3 秒）約束。`show_typing` 開啟時延遲期間持續顯示輸入中狀態。

---

## 5. 相關設定

```yaml
bot:
  reply_channel_ids: []      # 會回覆的頻道
  listen_channel_ids: []     # 只記錄不回覆的頻道
  show_typing: true
  max_message_length: 2000

chat_behavior:
  ignore_prefixes: ["#", "＃", "//"]
  enable_multi_bubble: true
  bubble_target_length: 47
  typing_delay_range: [0.6, 1.3]
  burst_reply:
    enable_burst_reply: true
    window_seconds: 4.5
    min_user_count: 2
    max_burst_messages: 5
```

---

## 6. 已知問題

### 多氣泡可能觸發 Discord 速率限制

`enable_multi_bubble` 開啟時，一段 10~15 行的回覆會連續發送 10~15 次 API 請求。雖然有隨機延遲，但**沒有針對 Discord 頻道速率限制（5 則／5 秒）做退避重試**，長回覆或短時間內多次回覆可能被 429 限流。

記錄於 [`improv.md`](improv.md) 2.3，尚未處理。可行的做法是捕捉 `discord.HTTPException` 並退避，或限制單次回覆的最大氣泡數。

### 圖片附件無大小限制

`download_image_attachments()` 直接 `await resp.read()` 讀入整個檔案，沒有依 `attachment.size` 設限。惡意使用者可上傳偽裝成圖片的超大檔案造成記憶體壓力。

記錄於 [`improv.md`](improv.md) 3.3，尚未處理。discord.py 已提供 `attachment.size`，加一道檢查即可。

### Burst 緩衝在記憶體中

`BurstBufferManager._buffers` 是 in-memory dict，bot 重啟時緩衝中尚未回覆的訊息會失去回覆機會。訊息本身已存庫、記憶不受影響（背景撿漏會處理提煉），只是那幾則不會得到回應。
