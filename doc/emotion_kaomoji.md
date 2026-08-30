# 情緒顏文字（Emotion & Kaomoji）

> 模型如何表達情緒、標籤如何渲染成顏文字、以及防重複機制。
>
> 最後更新：2026-08-30，已對照程式碼核實。

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

`EmotionReplacer.replace_emotion_tags()`（`bot/utils/emotion.py`）在 `GeminiClient.generate_response()` **回傳前**執行，因此所有走 Gemini 的輸出都會被渲染——包括對話回覆與鬧鐘提醒台詞。

```
正則比對 [emotion:類別]        # \[emotion:([a-zA-Z0-9_\-]+)\]，大小寫不敏感
    ↓
類別轉小寫 → 查顏文字池
    ↓ 查不到
別名對照表（shy→tsundere、cry→sad …）
    ↓ 仍查不到
替換為空字串（標籤消失，不會殘留在回覆裡）
    ↓ 查到
防重複挑選 → 以行內程式碼區塊包裹 → ` (顏文字)`
    ↓
清理連續空白
```

用 `` ` `` 包裹是為了在 Discord 中以等寬字型顯示，避免顏文字的對齊被比例字型破壞。顏文字本身若含反引號會被替換成 `´`，以免破壞 Markdown。

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

> 這是 class-level 狀態，跨頻道、跨使用者共用，且 bot 重啟後歸零。

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

---

## 6. 讓模型輸出標籤

標籤的使用規則寫在 `config/persona.md` 的人格設定裡（不在程式碼中）。要調整模型使用顏文字的頻率或時機，編輯 persona 即可。

---

## 7. 已知問題

### 沒有失敗訊號

未知的情緒類別會被**靜默替換為空字串**，不會寫 log。若模型持續輸出某個未涵蓋的類別（例如 `[emotion:excited]`），沒有任何跡象——只會看起來像模型比較少用顏文字。

要診斷可暫時在 `_repl()` 的 `if not kaomoji` 分支加一行 `logger.debug`。

### 防重複狀態不持久

`_recent_history` 是記憶體狀態，重啟即歸零。重啟後前幾則訊息可能出現與重啟前相同的顏文字。影響輕微。

### 熱重載未實作

`load_kaomoji()` 只在 `_kaomoji_map` 為空時被呼叫，因此編輯 `kaomoji.yaml` 後需重啟 bot。若要支援熱重載，需另外提供清空 `_kaomoji_map` 的入口。
