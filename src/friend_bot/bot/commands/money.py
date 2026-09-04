import discord
from discord import app_commands
import logging

from src.friend_bot.core.config import MAX_RECEIPT_ITEMS, MONEY_VIEW_TIMEOUT_SECONDS
from src.friend_bot.bot.handlers import download_image_attachments
from src.friend_bot.bot.utils.money import ReceiptItemView

logger = logging.getLogger("friend_bot.commands.money")


class MoneyCommandsMixin:
    """收據拆帳指令 Mixin（/kurisu-money）：辨識收據照片品項，逐品項產生獨立的 $w2w 拆帳卡片"""

    def register_money_commands(self):
        """註冊 /kurisu-money 指令至 self.tree"""

        @self.tree.command(
            name="kurisu-money",
            description="【收據拆帳】上傳收據照片，紅莉栖幫你讀出品項並產生 $w2w 拆帳按鈕"
        )
        @app_commands.describe(photo="收據照片（需清晰、可辨識品項與金額）")
        async def kurisu_money_command(interaction: discord.Interaction, photo: discord.Attachment):
            content_type = photo.content_type or ""
            is_image = content_type.startswith("image/")
            if not is_image:
                ext = photo.filename.lower().split(".")[-1] if "." in photo.filename else ""
                is_image = ext in ("jpg", "jpeg", "png", "webp")
            if not is_image:
                await interaction.response.send_message("（請上傳圖片格式的收據照片喔！）", ephemeral=True)
                return

            # 辨識需要時間，先 defer 避免超過 Discord 3 秒互動回應限制
            await interaction.response.defer(thinking=True)

            image_bytes_list, mime_types = await download_image_attachments(photo)
            if not image_bytes_list:
                await interaction.followup.send("（圖片下載失敗，請再試一次）", ephemeral=True)
                return

            items = await self.gemini.extract_receipt_items(image_bytes_list[0], mime_types[0])
            if not items:
                await interaction.followup.send(
                    "（沒有在照片中辨識到任何品項，請確認上傳的是收據且畫面清晰）", ephemeral=True
                )
                return

            truncated = len(items) > MAX_RECEIPT_ITEMS
            items = items[:MAX_RECEIPT_ITEMS]

            invoker_id = interaction.user.id
            fallback_channel = interaction.channel

            async def dispatch(debtor, creditor, amount):
                await self._dispatch_money_command(
                    debtor=debtor, creditor=creditor, amount=amount, fallback_channel=fallback_channel
                )

            for item in items:
                view = ReceiptItemView(
                    item_name=item["name"],
                    item_price=item["price"],
                    invoker_id=invoker_id,
                    dispatch_command=dispatch,
                    timeout=MONEY_VIEW_TIMEOUT_SECONDS,
                )
                msg = await interaction.followup.send(embed=view.build_embed(), view=view)
                view.message = msg

            if truncated:
                await interaction.followup.send(
                    f"（品項數量較多，僅顯示前 {MAX_RECEIPT_ITEMS} 項，其餘請自行手動使用 `$w2w`）",
                    ephemeral=True
                )

            logger.info(
                f"[收據拆帳] 已為 [{interaction.user.display_name}] 辨識收據並產生 {len(items)} 張拆帳卡片"
            )
