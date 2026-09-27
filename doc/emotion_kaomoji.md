# 情緒顏文字（Emotion & Kaomoji）

> 模型如何表達情緒、標籤如何渲染成顏文字、防重複機制、頻道心情（情緒慣性）與稀有顏文字。
>
> 最後更新：2026-09-27，已對照程式碼核實。

---

## 1. 為什麼用標籤而不是直接讓模型輸出顏文字

讓模型自由輸出顏文字有兩個問題：它會反覆使用同幾個（訓練資料裡最常見的那些），而且經常產生破碎或全形半形混亂的字元。

因此改成**間接指定**：模型只輸出情緒**類別標籤**，實際的顏文字由程式從對應的池子裡隨機挑選。

```
模型輸出：  哈？別誤會了！[emotion:tsundere]
渲染後：    哈？別誤會了！ `(///￣ ￣///)`
```

好處是顏文字庫可以隨時編輯（`config/kaomoji.yaml`）而不用改人格設定，且多樣性由程式保證而非仰賴模型。

---

## 2. 渲染流程

`EmotionReplacer.replace_emotion_tags()`（`core/emotion.py`）在 `GeminiClient.generate_response()` **回傳前**執行，因此所有走 Gemini 的輸出都會被渲染——包括對話回覆與鬧鐘提醒台詞。

```
正則比對 [emotion:類別] 或 (emotion:類別)   # 大小寫不敏感，連同標籤前的空白一起比對
    ↓
resolve_category()：轉小寫 → 查顏文字池 → 查不到再查別名表（shy→tsundere、cry→sad …）
    ↓
【有傳 mood_channel_id 時】情緒慣性轉換 → 記錄到頻道心情（見 §6）
    ↓
1% 機率抽稀有款（見 §5.1），否則防重複挑選一般池
    ↓ 仍查不到類別
替換為空字串（標籤消失，不會殘留在回覆裡）
    ↓ 查到
以行內程式碼區塊包裹 → ` (顏文字)`（位於行首時不補前置空格）
```

用 `` ` `` 包裹是為了在 Discord 中以等寬字型顯示，避免顏文字的對齊被比例字型破壞。顏文字本身若含反引號會被替換成 `´`，以免破壞 Markdown。

空白只在標籤位置處理（吃掉標籤前的空白、再補一個空格），**不會**全域壓縮連續空白，因此回覆中的程式碼縮排不受影響。

---

## 3. 十種情緒類別

| 類別 | 用途 |
| :--- | :--- |
| `tsundere` | 傲嬌、害羞、臉紅、嘴硬 |
| `shock` | 驚訝、驚嚇、慌張 |
| `sigh` | 無奈、嘆氣、疲憊 |
| `proud` | 得意、自信 |
| `soft` | 溫柔、開心 |
| `angry` | 生氣 |
| `thinking` | 疑問、思考 |
| `awkward` | 尷尬 |
| `sad` | 難過、哭泣 |
| `depressed` | 低落、沮喪 |

### 別名對照

模型未必會用上面的類別名，因此有一層別名映射：

```
shy / blush          → tsundere
scared / surprised / panic → shock
tired / disdain      → sigh
smug / confident     → proud
gentle / happy / smile → soft
mad / rage           → angry
cry / crying / grief / sorrow / heartbroken → sad
gloom / gloomy / down / frustrated / disappointed / hopeless → depressed
```

未命中任何類別或別名時，標籤被替換為**空字串**——寧可少一個顏文字，也不要讓 `[emotion:xxx]` 這種內部語法漏到使用者眼前。

---

## 4. 防連續重複

單純 `random.choice()` 會讓同一個顏文字在幾則訊息內反覆出現。因此每個類別維護一個「近期使用」佇列：

```python
recent = _recent_history.setdefault(cat, [])
available = [k for k in pool if k not in recent]
if not available:            # 整池都用過了 → 重置
    available = pool
    recent.clear()

chosen = random.choice(available)
recent.append(chosen)
if len(recent) > max(1, len(pool) // 2):
    recent.pop(0)            # 佇列長度為池子的一半
```

佇列長度設為池子大小的一半，意味著**一個顏文字要等到該類別其他一半的選項都用過之後才可能再出現**。池子越大，重複間隔越長。

> 這是 class-level 狀態，跨頻道、跨使用者共用，且 bot 重啟後歸零。佇列以**正式類別**為 key，所以 `happy`、`smile`、`soft` 共用同一份紀錄。

---

## 5. 顏文字庫設定

`config/kaomoji.yaml`：

```yaml
kaomoji:
  tsundere:
    - "(///￣ ￣///)"
    - "ヽ(///＞_＜///)ﾉ"
  shock:
    - "(；ﾟДﾟ)"
```

載入邏輯（`load_kaomoji()`）：依序找 `config/kaomoji.yaml` → 根目錄 `kaomoji.yaml`；檔案不存在、格式錯誤、或 `kaomoji` 區塊為空時，**退回程式內建的 `DEFAULT_KAOMOJI_MAP`**（與上表相同的十類）。

因此刪掉 `kaomoji.yaml` 不會讓功能壞掉，只是失去自訂。

編輯這個檔案**不需要改程式碼**，但目前需要重啟才會重新載入（`_kaomoji_map` 只在為空時才載入）。

### 5.1 稀有顏文字（`rare_kaomoji`）

```yaml
rare_kaomoji:
  angry:
    - '(╯°□°）╯︵ ┻━┻'
```

每次挑選時先擲 `emotion.rare.chance`（預設 1%）；命中且該類別有稀有款時，從中抽一個**不在冷卻中**的（`emotion.rare.cooldown_hours`，預設 24 小時）。抽中會寫一筆 `logger.info`（`✨ 抽中稀有顏文字`）。沒列出的類別就沒有稀有款；全部在冷卻中時退回一般池。

### 5.2 情緒慣性規則（`mood_transitions`）

```yaml
mood_transitions:
  angry:            # 頻道目前的心情
    soft: tsundere  # 模型想輸出 soft → 改抽 tsundere
```

只有心情強度達「明顯」以上（`INERTIA_MIN_INTENSITY = 1.8`）才生效，見 §6。

---

## 6. 頻道心情（情緒慣性）

模型輸出的情緒標籤會累積成**該頻道**的心情（`core/mood.py` 的 `MoodTracker`），並產生兩個效果：

1. **影響顏文字**：心情夠強時依 `mood_transitions` 轉換類別。例如還在氣頭上時模型輸出 `soft`，實際抽 `tsundere`——嘴硬但已經消氣。
2. **回饋進 prompt**：`MoodTracker.describe()` 產生「你現在明顯不爽（起因和 桶子 剛才的互動有關）。」這類描述，以【你目前的心情】區塊注入（見 [`prompt_pipeline.md`](prompt_pipeline.md) §3.2），讓語氣與顏文字一致。**不顯示任何數字**，與好感度的隱密設計一致。

### 累積與衰減

```
每個標籤：其他情緒 × 0.75（沖淡）→ 該情緒 + step（上限 5.0）
讀取時：強度 × 0.5 ^ (經過分鐘 / half_life_minutes)
主導情緒 = 強度最高者；低於 threshold 視為平靜
```

| 強度 | 描述副詞 | 效果 |
| :--- | :--- | :--- |
| < 0.8（threshold） | — | 平靜，不注入 prompt |
| 0.8 ~ 1.8 | 有一點 | 注入 prompt |
| 1.8 ~ 3.0 | 明顯 | 注入 prompt ＋ 情緒慣性生效 |
| ≥ 3.0 | 非常 | 同上 |

`thinking` 是動作而非心情，**不會被記錄**（只有 `MOOD_DESCRIPTIONS` 中的類別才會）。

### 只有聊天路徑會改心情

`generate_response()` 只有在傳入 `mood_channel_id` 時才讀寫心情。目前只有對話回覆（`bot/client.py`）與 `/kurisu-search` 會傳；記憶提煉、鬧鐘與行事曆提醒**不傳**，避免背景任務改動 bot 的心情。

### 持久化

心情存於資料表 `channel_moods`（`channel_id`、`scores` JSON、`cause_user`、`updated_at`），由 `memory/mood_store.py` 讀寫：

- `ensure_mood_loaded()`：每個頻道第一次用到時從資料庫載入記憶體快取
- `persist_mood()`：每次渲染完寫回

`scores` 存的是 `updated_at` 當下的強度，讀取時才衰減，因此重啟後心情會延續，但若停機很久會自然回到平靜。

---

## 7. 讓模型輸出標籤

標籤的使用規則寫在 `config/persona.md` 的人格設定裡（不在程式碼中）。要調整模型使用顏文字的頻率或時機，編輯 persona 即可。

---

## 8. 已知問題

### 沒有失敗訊號

未知的情緒類別會被**靜默替換為空字串**，不會寫 log。若模型持續輸出某個未涵蓋的類別（例如 `[emotion:excited]`），沒有任何跡象——只會看起來像模型比較少用顏文字。

要診斷可暫時在 `_repl()` 的 `if not kaomoji` 分支加一行 `logger.debug`。

### 防重複狀態不持久

`_recent_history` 與稀有款冷卻 `_rare_last_used` 都是記憶體狀態，重啟即歸零。重啟後前幾則訊息可能出現與重啟前相同的顏文字，稀有款也可能在 24 小時內再次出現。影響輕微。（頻道心情則有持久化，見 §6。）

### 熱重載未實作

`load_kaomoji()` 只在 `_kaomoji_map` 為空時被呼叫，因此編輯 `kaomoji.yaml` 後需重啟 bot。若要支援熱重載，需另外提供清空 `_kaomoji_map` 的入口。
