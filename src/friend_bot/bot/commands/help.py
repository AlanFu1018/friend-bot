import discord
from discord import app_commands
import logging

from src.friend_bot.core.config import BOT_NAME

logger = logging.getLogger("friend_bot.commands.help")


class HelpCommandsMixin:
    """說明手冊指令 Mixin（包含 /kurisu-help 與說明卡片生成）"""

    def register_help_commands(self):
        """註冊 /kurisu-help 指令至 self.tree"""

        @self.tree.command(name="kurisu-help", description=f"查看 {BOT_NAME} 的所有指令功能說明指南")
        async def kurisu_help_command(interaction: discord.Interaction):
            embed = self._create_help_embed()
            await interaction.response.send_message(embed=embed)
            logger.info(f"已向 [{interaction.user.display_name}] 透過 /kurisu-help 展示說明手冊卡片")

    def _create_help_embed(self) -> discord.Embed:
        """產生 Help 指令的 Embed 說明卡片"""
        embed = discord.Embed(
            title=f"📖 {BOT_NAME} 機器人指令說明手冊",
            description=f"你好！我是 **{BOT_NAME}**，以下是目前支援的所有指令與功能（輸入 `/kurisu-` 即可自動跳出補全選單）：",
            color=0x2ECC71
        )
        embed.add_field(
            name="⏰ `/kurisu-alarm-set <時間> <提醒內容>`",
            value=(
                "**【定時鬧鐘提醒】**\n"
                "設定專屬提醒鬧鈴，在指定時刻以紅莉栖專屬傲嬌對白提醒你。\n"
                "*管理：`/kurisu-alarm-list` ｜ `/kurisu-alarm-cancel <編號>`*"
            ),
            inline=False
        )
        embed.add_field(
            name="📅 `/kurisu-calendar-set <時間> <排程內容> [webhook_url]`",
            value=(
                "**【Webhook 行事曆排程】**\n"
                "登記行事曆日程，支援 Webhook 推送，且**日常聊天問我『今天有什麼行程』也會自動回答你**！\n"
                "*管理：`/kurisu-calendar-list` ｜ `/kurisu-calendar-cancel <編號>`*"
            ),
            inline=False
        )
        embed.add_field(
            name="🔍 `/kurisu-search <查詢內容>`",
            value="**【強制線上搜尋】**\n當你想獲取即時時事、最新新聞或查證最新資訊時使用。\n*範例：`/kurisu-search 台北現在天氣`*",
            inline=False
        )
        embed.add_field(
            name="🧠 `/kurisu-profile [用戶或@機器人]`",
            value=(
                "**【查詢用戶個人畫像 / 機器人自身簡介】**\n"
                "查看機器人為你或指定群友建立的長期特徵與好感進展；**若指定 @機器人 則會展示紅莉栖自身的人物檔案與自我介紹**！\n"
                "*範例：`/kurisu-profile` 或 `/kurisu-profile user:@克莉絲`*"
            ),
            inline=False
        )
        embed.add_field(
            name="💬 `日常直接對話（支援多人短時熱絡引用回覆）`",
            value="**【自然群友聊天】**\n直接在頻道內聊天，短時間內**多人熱烈發言時會智慧選擇引用對象**，精準吐槽並兼顧在場群友！",
            inline=False
        )
        embed.add_field(
            name="📝 `訊息註解功能（以 # 開頭的訊息）`",
            value="**【訊息不回覆與記憶過濾】**\n任何訊息若以 `#` 或 `＃` 開頭，機器人將視為註解備忘：**不回覆、不記憶、不提煉該則訊息**。",
            inline=False
        )
        if self.user and self.user.display_avatar:
            embed.set_thumbnail(url=self.user.display_avatar.url)
        embed.set_footer(text=f"{BOT_NAME} • Multi-User Memory & Webhook Calendar Enabled")
        return embed
