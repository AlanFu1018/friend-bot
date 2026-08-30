import discord
from discord import app_commands
import logging
from datetime import datetime
from typing import Optional

from src.friend_bot.core.config import BOT_NAME, MAX_ALIASES_PER_USER
from src.friend_bot.memory import MemoryManager

logger = logging.getLogger("friend_bot.commands.alias")

SOURCE_LABEL = {
    "extraction": "🤖 自動學習",
    "command": "✍️ 手動設定",
    "unknown": "❔ 來源不明"
}


class AliasCommandsMixin:
    """
    別名管理指令 Mixin（/kurisu-alias）。

    別名讓群友慣用的綽號也能被辨識——Discord 顯示名稱往往不是大家實際互稱的稱呼。
    別名與 user_name 分開儲存，不會被背景提煉覆寫。

    權限模型：一般使用者只能管理自己的別名；具 manage_guild 權限者可透過 user 參數
    代為設定或撤銷。呼叫者身分一律取自 `interaction.user`，不信任任何訊息內容，
    因此無法透過聊天內容偽造身分。
    """

    def register_alias_commands(self):
        """註冊 /kurisu-alias 指令至 self.tree"""

        @self.tree.command(
            name="kurisu-alias",
            description=f"【別名管理】設定 {BOT_NAME} 認得的綽號，讓她聽到暱稱時知道是誰"
        )
        @app_commands.describe(
            action="要執行的操作",
            alias="要新增或移除的綽號（查看清單時免填）",
            user="代為操作的對象（需要管理伺服器權限；不選則為自己）"
        )
        @app_commands.choices(action=[
            app_commands.Choice(name="新增別名", value="add"),
            app_commands.Choice(name="移除別名", value="remove"),
            app_commands.Choice(name="查看目前的別名", value="list"),
        ])
        async def kurisu_alias_command(
            interaction: discord.Interaction,
            action: app_commands.Choice[str],
            alias: Optional[str] = None,
            user: Optional[discord.User] = None
        ):
            invoker = interaction.user
            target = user or invoker

            # 【權限校驗】代他人操作需要管理伺服器權限
            if user is not None and user.id != invoker.id:
                perms = getattr(invoker, "guild_permissions", None)
                if not (perms and perms.manage_guild):
                    await interaction.response.send_message(
                        "（哈？你沒有權限替別人設定別名喔，只有伺服器管理員可以這麼做。）",
                        ephemeral=True
                    )
                    return

            act = action.value

            if act == "list":
                await self._reply_alias_list(interaction, target)
                return

            if not alias or not alias.strip():
                await interaction.response.send_message(
                    "（要新增或移除的話，總得先告訴我是哪個綽號吧？）", ephemeral=True
                )
                return

            if act == "add":
                ok, reason = await MemoryManager.add_alias(
                    user_id=str(target.id),
                    alias=alias,
                    source="command",
                    by=[str(invoker.id)]
                )
            else:
                ok, reason = await MemoryManager.remove_alias(str(target.id), alias)

            prefix = "✅" if ok else "⚠️"
            who = "" if target.id == invoker.id else f"（對象：{target.display_name}）"
            await interaction.response.send_message(
                f"{prefix} {reason}{who}", ephemeral=not ok
            )
            if ok:
                logger.info(
                    f"[別名指令] {invoker.display_name}({invoker.id}) 對 "
                    f"{target.display_name}({target.id}) 執行 {act}：{alias}"
                )

    async def _reply_alias_list(self, interaction: discord.Interaction, target) -> None:
        """回覆某位使用者目前的別名清單（含來源，便於稽核與撤銷）"""
        profile = await MemoryManager.get_user_profile(str(target.id))
        if not profile:
            await interaction.response.send_message(
                f"（{target.display_name} 目前還沒有任何記憶畫像喔。）", ephemeral=True
            )
            return

        aliases = MemoryManager.normalize_aliases(profile.get("aliases"))

        embed = discord.Embed(
            title=f"🏷️ {target.display_name} 的別名",
            description=(
                f"目前 {len(aliases)} / {MAX_ALIASES_PER_USER} 個"
                if aliases else "目前沒有設定任何別名。"
            ),
            color=0xB22222
        )
        embed.add_field(
            name="Discord 顯示名稱",
            value=f"`{profile.get('user_name', target.display_name)}`",
            inline=False
        )

        for a in aliases:
            source = SOURCE_LABEL.get(a.get("source", "unknown"), SOURCE_LABEL["unknown"])
            when = ""
            try:
                when = datetime.fromtimestamp(int(a.get("at", 0))).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError, OSError, OverflowError):
                when = "時間不明"

            detail = [f"{source} · {when}"]
            by = a.get("by") or []
            if by:
                detail.append("提出者：" + "、".join(f"<@{b}>" for b in by[:3]))
            if a.get("message_id"):
                detail.append(f"訊息 ID：`{a['message_id']}`")

            embed.add_field(name=f"「{a['alias']}」", value="\n".join(detail), inline=False)

        if aliases:
            embed.set_footer(text="若有認錯人的別名，請用 /kurisu-alias remove 移除。")

        await interaction.response.send_message(embed=embed, ephemeral=True)
