import aiohttp
import re
from typing import List, Tuple
import discord
from src.friend_bot.core.config import (
    MAX_MESSAGE_LENGTH,
    ENABLE_MULTI_BUBBLE,
    BUBBLE_TARGET_LENGTH,
)
from src.friend_bot.core.logger import get_logger

logger = get_logger("handlers")

SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif"
}

async def download_image_attachments(message: discord.Message) -> Tuple[List[bytes], List[str]]:
    """下載 Discord 訊息中的圖片附件並回傳位元組與 MIME 類型"""
    images = []
    mime_types = []

    if not message.attachments:
        return images, mime_types

    async with aiohttp.ClientSession() as session:
        for attachment in message.attachments:
            # 檢查 Content-Type 或副檔名
            content_type = attachment.content_type or ""
            is_image = any(content_type.startswith(t) for t in ["image/jpeg", "image/png", "image/webp", "image/gif"])
            if not is_image:
                ext = attachment.filename.lower().split(".")[-1]
                if ext in ["jpg", "jpeg", "png", "webp"]:
                    is_image = True
                    content_type = f"image/{ext if ext != 'jpg' else 'jpeg'}"

            if is_image:
                try:
                    async with session.get(attachment.url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            images.append(data)
                            mime_types.append(content_type or "image/jpeg")
                            logger.debug(f"成功下載圖片附件: {attachment.filename} ({len(data)} bytes)")
                except Exception as e:
                    logger.error(f"下載圖片附件失敗 ({attachment.filename}): {e}")

    return images, mime_types

def _split_text_by_sentences(text: str, target_len: int, max_len: int) -> List[str]:
    """
    依照句末標點（。！？!?…以及換行）切分文字，並組合成約 target_len 長度的氣泡。
    """
    sentence_pattern = r'([^。！？!?…\n]+[。！？!?…\n]*|[\n]+)'
    tokens = re.findall(sentence_pattern, text)
    if not tokens:
        tokens = [text]

    chunks: List[str] = []
    current_chunk = ""

    for token in tokens:
        if not token:
            continue

        if len(current_chunk) + len(token) <= target_len:
            current_chunk += token
        else:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
                current_chunk = ""

            if len(token) <= max_len:
                current_chunk = token
            else:
                # 若單句異常長度大於 max_len，強制切分
                while len(token) > max_len:
                    chunks.append(token[:max_len].strip())
                    token = token[max_len:]
                current_chunk = token

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks

def split_message(
    text: str,
    max_length: int = MAX_MESSAGE_LENGTH,
    target_length: int = BUBBLE_TARGET_LENGTH,
    enable_multi_bubble: bool = ENABLE_MULTI_BUBBLE
) -> List[str]:
    """
    智慧多氣泡語意切分器：
    1. 保護 Markdown Code Block (```...```) 不被破壞切割。
    2. 若關閉 multi_bubble，僅在超過 Discord 2000 字元上限時進行硬分段。
    3. 若開啟 multi_bubble，優先依據雙換行 (\\n\\n)、單換行 (\\n) 與語意標點 (。！？) 拆為約 target_length (如 120 字) 的擬真聊天氣泡。
    """
    text = text.strip()
    if not text:
        return []

    # 若未啟用多氣泡，且文字未超過上限，直接回傳
    if not enable_multi_bubble:
        if len(text) <= max_length:
            return [text]
        target_length = max_length

    # 1. 識別並保護 Markdown Code Block (```...```)
    code_block_pattern = r'(```[\s\S]*?```)'
    parts = re.split(code_block_pattern, text)

    raw_chunks: List[str] = []

    for part in parts:
        if not part:
            continue

        # 如果此部分是 Code Block，整塊保留
        if part.startswith("```") and part.endswith("```"):
            if len(part) <= max_length:
                raw_chunks.append(part)
            else:
                # 極長 Code Block 超過 Discord 限制時的分片保護
                lines = part.split("\n")
                sub_code = ""
                lang = lines[0].strip("`")
                for line in lines[1:-1]:
                    if len(sub_code) + len(line) + 10 > max_length - 10:
                        raw_chunks.append(f"```{lang}\n{sub_code}\n```")
                        sub_code = line + "\n"
                    else:
                        sub_code += line + "\n"
                if sub_code:
                    raw_chunks.append(f"```{lang}\n{sub_code}\n```")
            continue

        # 2. 一般文本：先依段落 (\n\n) 拆分
        paragraphs = part.split("\n\n")
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(para) <= target_length:
                raw_chunks.append(para)
            else:
                # 3. 段落超過目標長度，按語意標點拆分
                sub_sentences = _split_text_by_sentences(para, target_length, max_length)
                raw_chunks.extend(sub_sentences)

    # 4. 氣泡後處理：合併過短的氣泡（例如單獨一個表情符號或只有幾個字），避免過度零碎
    final_chunks: List[str] = []
    accumulated = ""

    for chunk in raw_chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        # 若當前是 Code Block，直接作為獨立氣泡
        if chunk.startswith("```") and chunk.endswith("```"):
            if accumulated:
                final_chunks.append(accumulated)
                accumulated = ""
            final_chunks.append(chunk)
            continue

        if not accumulated:
            accumulated = chunk
        elif len(accumulated) + len(chunk) + 1 <= target_length:
            accumulated = f"{accumulated}\n{chunk}"
        else:
            final_chunks.append(accumulated)
            accumulated = chunk

    if accumulated:
        final_chunks.append(accumulated)

    return [c for c in final_chunks if c.strip()]
