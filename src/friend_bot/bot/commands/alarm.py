import discord
from discord import app_commands
import logging
from typing import Optional

from src.friend_bot.bot.utils.alarm import AlarmManager, parse_alarm_time

logger = logging.getLogger("friend_bot.commands.alarm")


class AlarmCommandsMixin:
    """定時提醒鬧鐘指令 Mixin（包含 /kurisu-alarm-set, /kurisu-alarm-list, /kurisu-alarm-cancel）"""

    def register_alarm_commands(self):
        """註冊定時鬧鐘相關指令至 self.tree"""

        # 1. /kurisu-alarm-set 指令
        @self.tree.command(name="kurisu-alarm-set", description="【設定定時提醒鬧鐘】紅莉栖會在指定時間以傲嬌風格發送醒目標題提醒")
        @app_commands.describe(
            time="提醒時間 (格式: y/m/d/h/m，例如 2026/8/27/15/30、8/27/15/30 或 15:30)",
            content="要提醒的具體事項內容 (例如: 搶特展門票、吃藥、開會)"
        )
        async def kurisu_alarm_set_command(interaction: discord.Interaction, time: str, content: str):
            content = content.strip()
            if not content:
                await interaction.response.send_message("（提醒內容不能是空的啦！請告訴我要提醒你什麼事。）", ephemeral=True)
                return

            try:
                target_dt, target_ts, date_str, time_str, formatted_time_str = parse_alarm_time(time)
            except ValueError as e:
                embed_err = discord.Embed(
                    title="⚠️【鬧鐘時間設定失敗】",
                    description=f"哼，連時間格式都弄錯了！\n\n{str(e)}",
                    color=0xE74C3C
                )
                embed_err.set_footer(text="格式範例：2026/8/27/15/30、8/27/15/30 或 15:30")
                await interaction.response.send_message(embed=embed_err, ephemeral=True)
                return

            channel_id = str(interaction.channel_id) if interaction.channel_id else ""
            user_id = str(interaction.user.id)
            user_name = interaction.user.display_name

            alarm_id = await AlarmManager.create_alarm(
                channel_id=channel_id,
                user_id=user_id,
                user_name=user_name,
                target_timestamp=target_ts,
                target_time_str=formatted_time_str,
                content=content
            )

            embed = discord.Embed(
                title="⏰【紅莉栖的鬧鐘已設定】",
                description=(
                    f"哼，既然你特地拜託我了，那我就幫你記下來吧！\n"
                    f"可別誤會了，我才不是特別關心你，只是實驗室助手對時間很嚴謹而已！"
                ),
                color=0x2ECC71
            )
            embed.add_field(name="📌 提醒事項", value=f"```{content}```", inline=False)
            embed.add_field(name="⏳ 預定提醒時間", value=f"`{formatted_time_str}`", inline=True)
            embed.add_field(name="🔢 鬧鐘編號", value=f"`#{alarm_id}`", inline=True)
            embed.set_footer(text="時間到了會在頻道發送提醒 • 可使用 /kurisu-alarm-list 查看")

            if self.user and self.user.display_avatar:
                embed.set_thumbnail(url=self.user.display_avatar.url)

            await interaction.response.send_message(embed=embed)
            logger.info(f"已為 [{user_name}] 設定鬧鐘 ID:{alarm_id}，時間:{formatted_time_str}，內容:「{content}」")

        # 2. /kurisu-alarm-list 指令
        @self.tree.command(name="kurisu-alarm-list", description="查看自己名下所有待觸發的定時鬧鐘清單")
        async def kurisu_alarm_list_command(interaction: discord.Interaction):
            user_id = str(interaction.user.id)
            alarms = await AlarmManager.get_pending_alarms(user_id=user_id)

            if not alarms:
                embed = discord.Embed(
                    title="⏰【待觸發鬧鐘清單】",
                    description="（你目前沒有任何待觸發的定時鬧鐘哦！可以使用 `/kurisu-alarm-set` 設定一個。）",
                    color=0x95A5A6
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            embed = discord.Embed(
                title=f"⏰【{interaction.user.display_name} 的待觸發鬧鐘清單】",
                description=f"目前共有 **{len(alarms)}** 則等待觸發的定時鬧鐘：",
                color=0x3498DB
            )
            for a in alarms:
                aid = a["id"]
                t_str = a["target_time_str"]
                cnt = a["content"]
                embed.add_field(name=f"鬧鐘 #{aid} ｜ {t_str}", value=f"內容: {cnt}", inline=False)
            embed.set_footer(text="若要取消某個鬧鐘，請使用 /kurisu-alarm-cancel <編號>")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        # 3. /kurisu-alarm-cancel 指令
        @self.tree.command(name="kurisu-alarm-cancel", description="取消指定的定時提醒鬧鐘")
        @app_commands.describe(alarm_id="要取消的鬧鐘編號 (可從 /kurisu-alarm-list 查詢)")
        async def kurisu_alarm_cancel_command(interaction: discord.Interaction, alarm_id: int):
            user_id = str(interaction.user.id)
            success = await AlarmManager.cancel_alarm(alarm_id=alarm_id, user_id=user_id)
            if success:
                embed = discord.Embed(
                    title="🗑️【鬧鐘已取消】",
                    description=f"鬧鐘 **#{alarm_id}** 已經幫你取消囉！",
                    color=0xE67E22
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(
                    title="⚠️【取消失敗】",
                    description=f"找不到編號 **#{alarm_id}** 的待觸發鬧鐘，或者該鬧鐘不是由你設定的！",
                    color=0xE74C3C
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
