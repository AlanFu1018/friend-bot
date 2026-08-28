import discord
from discord import app_commands
import logging
from typing import Optional

from src.friend_bot.core.config import (
    BOT_NAME,
    ENABLE_FAVORABILITY,
    DEFAULT_FAVORABILITY,
    DAILY_GAIN_LIMIT
)
from src.friend_bot.memory import MemoryManager

logger = logging.getLogger("friend_bot.commands.profile")

TIER_NAME_MAP = {
    "stranger": "Tier 1: 陌生警戒 (Stranger)",
    "familiar": "Tier 2: 熟識群友 (Familiar)",
    "trusted": "Tier 3: 實驗室夥伴 (Labmem Partner)",
    "cherished": "Tier 4: 靈魂共鳴 (Steins;Gate Bond)"
}

def render_favorability_bar(score: int, total: int = 100, bar_length: int = 10) -> str:
    """產生好感度視覺化進度條 [██████░░░░]"""
    clamped_score = max(0, min(total, score))
    filled = int(round((clamped_score / total) * bar_length))
    filled = max(0, min(bar_length, filled))
    return "█" * filled + "░" * (bar_length - filled)


class ProfileCommandsMixin:
    """使用者記憶特徵畫像與機器人自身簡介指令 Mixin（包含 /kurisu-profile）"""

    def register_profile_commands(self):
        """註冊 /kurisu-profile 指令至 self.tree"""

        @self.tree.command(name="kurisu-profile", description="查看自己、指定群友的記憶特徵畫像與好感度，或機器人自身簡介")
        @app_commands.describe(user="選擇要查詢畫像的群友或克莉絲的簡介（留空代表查詢自己）")
        async def kurisu_profile_command(interaction: discord.Interaction, user: Optional[discord.User] = None):
            target_user = user or interaction.user
            target_user_id = str(target_user.id)
            target_user_name = target_user.display_name

            # 檢查是否查詢機器人自身（包含 @機器人、自身 user_id 或 bot 自身）
            is_bot_self = False
            if self.user and target_user.id == self.user.id:
                is_bot_self = True
            elif target_user == self.user:
                is_bot_self = True
            elif getattr(target_user, "bot", False) and (
                target_user.name == BOT_NAME or target_user.display_name == BOT_NAME or (self.user and target_user.name == self.user.name)
            ):
                is_bot_self = True

            if is_bot_self:
                embed = self._create_bot_profile_embed()
                await interaction.response.send_message(embed=embed)
                logger.info(f"已向 [{interaction.user.display_name}] 透過 /kurisu-profile 顯示 {BOT_NAME} 自身的個人檔案簡介")
                return

            profile = await MemoryManager.get_user_profile(target_user_id)
            embed = self._create_profile_embed(target_user_id, target_user_name, profile, target_user)
            await interaction.response.send_message(embed=embed)
            logger.info(f"已向 [{interaction.user.display_name}] 透過 /kurisu-profile 顯示 [{target_user_name}] 的畫像與好感度")

    def _create_bot_profile_embed(self) -> discord.Embed:
        """產生機器人自身的角色自我介紹與背景檔案 Embed 卡片"""
        embed = discord.Embed(
            title=f"🧠 角色檔案：牧瀨紅莉栖（Makise Kurisu）｜ {BOT_NAME}",
            description=(
                "「才、才不是特地向你做自我介紹呢！只是身為維克多·孔多利亞大學的研究員，"
                "適度的身分公開是學術禮儀而已……可別會錯意了！」"
            ),
            color=0x9B59B6
        )

        embed.add_field(
            name="🧬 身份與背景 (Identity)",
            value=(
                "• **本名**：牧瀨紅莉栖（Makise Kurisu）\n"
                f"• **暱稱**：{BOT_NAME}、助手、克里斯蒂娜 (Christina)、The Zombie\n"
                "• **學術所屬**：維克多·孔多利亞大學 腦科學研究所研究員\n"
                "• **實驗室編號**：未來道具研究所 Labmem No.004"
            ),
            inline=False
        )

        embed.add_field(
            name="🔬 專長領域與性格特質 (Expertise & Traits)",
            value=(
                "• **專長領域**：腦科學、神經脈衝傳導、時間跳躍理論\n"
                "• **性格標籤**：天才少女、極度理性、傲嬌 (Tsundere)、重感情\n"
                "• **日常喜好**：Dr Pepper、閱讀學術論文、黑咖啡、匿名論壇討論 (@channel)"
            ),
            inline=False
        )

        embed.add_field(
            name="💖 關係與好感機制 (Relationship System)",
            value=(
                "• **好感階級**：支援動態 4 階好感進展 (`Stranger` ➔ `Familiar` ➔ `Labmem Partner` ➔ `Steins;Gate Bond`)\n"
                "• **關係定位**：默默守護每位群友的傲嬌助手\n"
                "• **每日好感**：平時在頻道聊天會自動累積好感度與記憶特徵"
            ),
            inline=False
        )

        embed.add_field(
            name="💡 常用指令指南",
            value=(
                "• `/kurisu-help`：查看所有指令說明\n"
                "• `/kurisu-profile`：查看你或群友的記憶特徵與好感進度\n"
                "• `/kurisu-search`：強制聯網檢索即時資料\n"
                "• `/kurisu-alarm-*` / `/kurisu-calendar-*`：設定定時提醒與行事曆"
            ),
            inline=False
        )

        if self.user and hasattr(self.user, "display_avatar") and self.user.display_avatar:
            embed.set_thumbnail(url=self.user.display_avatar.url)

        embed.set_footer(text=f"{BOT_NAME} • Labmem No.004 • Steins;Gate Worldline")
        return embed

    def _create_profile_embed(
        self,
        target_user_id: str,
        target_user_name: str,
        profile: Optional[dict],
        target_user: Optional[discord.User] = None
    ) -> discord.Embed:
        if not profile:
            embed = discord.Embed(
                title=f"📋 個人畫像檔案：{target_user_name}",
                description="（目前資料庫中尚未建立該使用者的長期畫像，多跟我聊聊天就會自動累積囉～）",
                color=0x95A5A6
            )
            embed.set_footer(text=f"User ID: {target_user_id}")
            return embed

        user_name_in_db = profile.get("user_name", target_user_name)
        facts = profile.get("facts", [])
        notes = profile.get("interaction_notes", "")
        fav_score = profile.get("favorability", DEFAULT_FAVORABILITY)
        tier = profile.get("relationship_tier", "familiar")
        daily_gain = profile.get("daily_favorability_gain", 0)
        updated_at = profile.get("updated_at", "未知時間")

        tier_title = TIER_NAME_MAP.get(tier, f"Tier: {tier}")
        progress_bar = render_favorability_bar(fav_score, total=100, bar_length=10)

        embed = discord.Embed(
            title=f"🧠 個人特徵畫像檔案：{user_name_in_db}",
            description=f"以下是 {BOT_NAME} 目前為你整理的長期記憶特徵與互動印象：",
            color=0x3498DB
        )

        if ENABLE_FAVORABILITY:
            embed.add_field(
                name="💖 關係進展 (Relationship Progression)",
                value=f"**【{tier_title}】**\n📊 信任進度條：`[{progress_bar}]` **{fav_score} / 100** *(今日累積: +{daily_gain}/{DAILY_GAIN_LIMIT})*",
                inline=False
            )

        facts_formatted = "\n".join([f"• {f}" for f in facts]) if facts else "尚無記錄明確的事實特徵"
        embed.add_field(name="📌 已知特徵 / 喜好 / 事實", value=facts_formatted, inline=False)
        embed.add_field(name="💬 互動印象與習慣", value=notes or "尚無特別印象", inline=False)
        embed.set_footer(text=f"User ID: {target_user_id} | 最後更新：{updated_at}")

        if target_user and hasattr(target_user, "display_avatar") and target_user.display_avatar:
            embed.set_thumbnail(url=target_user.display_avatar.url)

        return embed
