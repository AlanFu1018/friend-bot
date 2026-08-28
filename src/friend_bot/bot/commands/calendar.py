import discord
from discord import app_commands
import logging
from typing import Optional

from src.friend_bot.core.config import CALENDAR_WEBHOOK_URL
from src.friend_bot.bot.utils.calendar import CalendarManager, parse_calendar_time

logger = logging.getLogger("friend_bot.commands.calendar")


class CalendarCommandsMixin:
    """Webhook 行事曆排程指令 Mixin（包含 /kurisu-calendar-set, /kurisu-calendar-list, /kurisu-calendar-cancel）"""

    def register_calendar_commands(self):
        """註冊行事曆排程相關指令至 self.tree"""

        # 1. /kurisu-calendar-set 指令
        @self.tree.command(name="kurisu-calendar-set", description="【設定 Webhook 行事曆排程】登記日程並支援 Webhook 推送，平時聊天可直接問行程")
        @app_commands.describe(
            time="排程時間 (格式: y/m/d/h/m，例如 2026/8/27/15/30、8/27/15/30 或 15:30)",
            content="具體排程日程事項 (例如: 實驗室進度匯報、客戶會議)",
            webhook_url="自訂 Webhook URL (選填，若留空則使用預設頻道通知)"
        )
        async def kurisu_calendar_set_command(
            interaction: discord.Interaction,
            time: str,
            content: str,
            webhook_url: Optional[str] = None
        ):
            content = content.strip()
            if not content:
                await interaction.response.send_message("（排程內容不能是空的啦！請輸入日程事項。）", ephemeral=True)
                return

            try:
                target_dt, target_ts, date_str, time_str, formatted_time_str = parse_calendar_time(time)
            except ValueError as e:
                embed_err = discord.Embed(
                    title="⚠️【行事曆時間設定失敗】",
                    description=f"哼，連時間格式都弄錯了！\n\n{str(e)}",
                    color=0xE74C3C
                )
                embed_err.set_footer(text="格式範例：2026/8/27/15/30、8/27/15/30 或 15:30")
                await interaction.response.send_message(embed=embed_err, ephemeral=True)
                return

            channel_id = str(interaction.channel_id) if interaction.channel_id else ""
            user_id = str(interaction.user.id)
            user_name = interaction.user.display_name
            wh_url = (webhook_url or "").strip()

            event_id = await CalendarManager.create_event(
                channel_id=channel_id,
                user_id=user_id,
                user_name=user_name,
                target_timestamp=target_ts,
                target_date=date_str,
                target_time=time_str,
                target_time_str=formatted_time_str,
                content=content,
                webhook_url=wh_url
            )

            embed = discord.Embed(
                title="📅【紅莉栖的行事曆已登記】",
                description=(
                    f"哼，既然你特地交代了，那我就幫你在行事曆記下來吧！\n"
                    f"平常聊天時直接問我「今天/某天有什麼行程」，我也會幫你查出來哦！"
                ),
                color=0x2ECC71
            )
            embed.add_field(name="📌 排程事項", value=f"```{content}```", inline=False)
            embed.add_field(name="⏳ 預定時間", value=f"`{formatted_time_str}`", inline=True)
            embed.add_field(name="🔢 排程編號", value=f"`#{event_id}`", inline=True)
            if wh_url or CALENDAR_WEBHOOK_URL:
                embed.add_field(name="🌐 Webhook 通知", value="`已啟用自訂 Webhook 推送`", inline=False)

            embed.set_footer(text="時間到達時推送提醒 • 平常聊天可隨時詢問當日排程")

            if self.user and self.user.display_avatar:
                embed.set_thumbnail(url=self.user.display_avatar.url)

            await interaction.response.send_message(embed=embed)
            logger.info(f"已為 [{user_name}] 設定行事曆 ID:{event_id}，時間:{formatted_time_str}，內容:「{content}」")

        # 2. /kurisu-calendar-list 指令
        @self.tree.command(name="kurisu-calendar-list", description="查看自己未來一個月內的所有行事曆排程清單")
        async def kurisu_calendar_list_command(interaction: discord.Interaction):
            user_id = str(interaction.user.id)
            events = await CalendarManager.get_upcoming_events(user_id=user_id, days=30, limit=20)

            if not events:
                embed = discord.Embed(
                    title="📅【行事曆待辦清單】",
                    description="（你目前沒有任何排程哦！可以使用 `/kurisu-calendar-set` 設定一個。）",
                    color=0x95A5A6
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            embed = discord.Embed(
                title=f"📅【{interaction.user.display_name} 的行事曆排程清單】",
                description=f"目前共有 **{len(events)}** 則待辦排程：\n*（提示：平常對話直接問我「今天有什麼行程」我也能回答你哦！）*",
                color=0x3498DB
            )
            for e in events:
                eid = e["id"]
                t_str = e["target_time_str"]
                cnt = e["content"]
                wh_tag = " [🌐Webhook]" if e.get("webhook_url") else ""
                embed.add_field(name=f"排程 #{eid} ｜ {t_str}{wh_tag}", value=f"內容: {cnt}", inline=False)
            embed.set_footer(text="若要取消某個排程，請使用 /kurisu-calendar-cancel <編號>")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        # 3. /kurisu-calendar-cancel 指令
        @self.tree.command(name="kurisu-calendar-cancel", description="取消指定的行事曆排程")
        @app_commands.describe(event_id="要取消的排程編號 (可從 /kurisu-calendar-list 查詢)")
        async def kurisu_calendar_cancel_command(interaction: discord.Interaction, event_id: int):
            user_id = str(interaction.user.id)
            success = await CalendarManager.cancel_event(event_id=event_id, user_id=user_id)
            if success:
                embed = discord.Embed(
                    title="🗑️【排程已取消】",
                    description=f"行事曆排程 **#{event_id}** 已經幫你取消囉！",
                    color=0xE67E22
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(
                    title="⚠️【取消失敗】",
                    description=f"找不到編號 **#{event_id}** 的待觸發排程，或者該排程不是由你設定的！",
                    color=0xE74C3C
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
