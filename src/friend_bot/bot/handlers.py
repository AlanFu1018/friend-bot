import aiohttp
import re
from typing import List, Tuple, Any, Union
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

async def download_image_attachments(
    target: Any
) -> Tuple[List[bytes], List[str]]:
    """
    下載 Discord 訊息或附件清單中的圖片附件並回傳位元組與 MIME 類型。
    相容傳入 discord.Message、List[discord.Message]、List[discord.Attachment] 或單一 discord.Attachment。
    """
    images: List[bytes] = []
    mime_types: List[str] = []

    if target is None:
        return images, mime_types

    attachments: List[discord.Attachment] = []
    if isinstance(target, discord.Message):
        attachments = list(target.attachments)
    elif isinstance(target, discord.Attachment):
        attachments = [target]
    elif isinstance(target, (list, tuple)):
        for item in target:
            if isinstance(item, discord.Message):
                attachments.extend(item.attachments)
            elif isinstance(item, discord.Attachment):
                attachments.append(item)
            elif hasattr(item, "attachments"):
                attachments.extend(getattr(item, "attachments"))
    elif hasattr(target, "attachments"):
        attachments = list(getattr(target, "attachments"))

    if not attachments:
        return images, mime_types

    async with aiohttp.ClientSession() as session:
        for attachment in attachments:
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

def split_message(
    text: str,
    max_length: int = MAX_MESSAGE_LENGTH,
    target_length: int = BUBBLE_TARGET_LENGTH,
    enable_multi_bubble: bool = ENABLE_MULTI_BUBBLE
) -> List[str]:
    """
    單行優先多氣泡切分器：
    1. 保護 Markdown Code Block (```...```) 整塊發送。
    2. 若開啟 multi_bubble，直接依「換行 (\\n)」為單位切分為單行獨立訊息發送。
    3. 若單行文本長度超過 target_length，進一步依句末標點（。！？!?…）自然拆句。
    4. 確保每則訊息皆在 Discord max_length (2000字) 以內。
    """
    text = text.strip()
    if not text:
        return []

    # 1. 識別並保護 Markdown Code Block (```...```)
    code_block_pattern = r'(```[\s\S]*?```)'
    parts = re.split(code_block_pattern, text)

    chunks: List[str] = []

    for part in parts:
        if not part:
            continue

        # 如果此部分是 Code Block，整塊作為獨立訊息發送
        if part.startswith("```") and part.endswith("```"):
            if len(part) <= max_length:
                chunks.append(part.strip())
            else:
                # 極長 Code Block 超過 Discord 限制時的分片保護
                lines = part.split("\n")
                sub_code = ""
                lang = lines[0].strip("`")
                for line in lines[1:-1]:
                    if len(sub_code) + len(line) + 10 > max_length - 10:
                        chunks.append(f"```{lang}\n{sub_code}\n```")
                        sub_code = line + "\n"
                    else:
                        sub_code += line + "\n"
                if sub_code:
                    chunks.append(f"```{lang}\n{sub_code}\n```")
            continue

        # 2. 一般文本：若未啟用 multi_bubble，直接檢查長度切分
        if not enable_multi_bubble:
            while len(part) > max_length:
                chunks.append(part[:max_length].strip())
                part = part[max_length:]
            if part.strip():
                chunks.append(part.strip())
            continue

        # 3. 開啟 multi_bubble：依換行符號 (\n) 切成單行發送
        raw_lines = part.split("\n")
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue

            # 若單行長度適中（小於 target_length 且小於 max_length），直接作為一則單行訊息
            if len(line) <= target_length:
                chunks.append(line)
            else:
                # 若單行過長，依句末標點拆句為多行
                sentence_pattern = r'([^。！？!?…]+[。！？!?…]*|[\n]+)'
                sentences = re.findall(sentence_pattern, line)
                if not sentences:
                    sentences = [line]

                curr = ""
                for s in sentences:
                    if not s:
                        continue
                    if len(curr) + len(s) <= target_length:
                        curr += s
                    else:
                        if curr.strip():
                            chunks.append(curr.strip())
                            curr = ""
                        if len(s) <= max_length:
                            curr = s
                        else:
                            while len(s) > max_length:
                                chunks.append(s[:max_length].strip())
                                s = s[max_length:]
                            curr = s
                if curr.strip():
                    chunks.append(curr.strip())

    return [c for c in chunks if c.strip()]
