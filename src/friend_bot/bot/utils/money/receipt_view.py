import discord
from typing import Optional, Awaitable, Callable

from src.friend_bot.core.config import W2W_COMMAND_PREFIX
from src.friend_bot.core.logger import get_logger

logger = get_logger("money.receipt_view")

# ReceiptItemView.confirm() 呼叫此型別的 callback 代發真正的 $w2w 指令，
# 由呼叫端（MoneyCommandsMixin）決定要送到哪個頻道，View 本身不管頻道解析邏輯。
DispatchCallback = Callable[[discord.abc.User, discord.abc.User, float], Awaitable[None]]


def format_amount(amount: float) -> str:
    """整數金額不顯示小數點；小數金額至多顯示到小數第二位並去除多餘的尾端 0。"""
    rounded = round(amount, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


class ReceiptItemView(discord.ui.View):
    """
    單一收據品項的拆帳卡片：兩個 UserSelect（欠錢的人／被欠款的人）＋修改金額／取消／確認送出三顆按鈕。

    self._finalized 是唯一的守門旗標，confirm、cancel 任一觸發後就整組鎖死：
    - 避免「先取消又送出」互相矛盾
    - 避免使用者連續雙擊「確認送出」造成同一筆帳被重複代發（$w2w 一旦送出無法收回）
    """

    def __init__(
        self,
        *,
        item_name: str,
        item_price: float,
        invoker_id: int,
        dispatch_command: DispatchCallback,
        timeout: Optional[float] = None,
    ):
        super().__init__(timeout=timeout)
        self.item_name = item_name
        self.item_price = item_price
        self.invoker_id = invoker_id
        self._dispatch_command = dispatch_command
        self.debtor: Optional[discord.abc.User] = None
        self.creditor: Optional[discord.abc.User] = None
        self._finalized = False
        # 由呼叫端在 followup.send() 之後回填，供 on_timeout() 編輯訊息用
        self.message: Optional[discord.Message] = None

        self.add_item(_DebtorSelect())
        self.add_item(_CreditorSelect())

    def build_embed(self, status: Optional[str] = None) -> discord.Embed:
        if status is None:
            color = 0x3498DB
        elif status.startswith("✅"):
            color = 0x2ECC71
        else:
            color = 0x95A5A6

        embed = discord.Embed(title=f"🧾 {self.item_name}", color=color)
        embed.add_field(name="金額", value=f"`{format_amount(self.item_price)}`", inline=True)
        embed.add_field(name="欠錢的人", value=self.debtor.mention if self.debtor else "（尚未選擇）", inline=True)
        embed.add_field(name="被欠款的人", value=self.creditor.mention if self.creditor else "（尚未選擇）", inline=True)
        embed.set_footer(text=status or "選好欠錢的人與被欠款的人後，按「確認送出」即可代發 $w2w 指令")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("（這不是你的收據卡片喔！）", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(embed=self.build_embed(status="⏰ 已逾時，請重新使用 /kurisu-money"), view=self)
            except discord.HTTPException as e:
                logger.warning(f"[拆帳卡片] 逾時鎖定訊息更新失敗: {e}")

    @discord.ui.button(label="修改金額", style=discord.ButtonStyle.secondary, row=2)
    async def edit_amount(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._finalized:
            await interaction.response.send_message("（這張卡片已經結束了）", ephemeral=True)
            return
        await interaction.response.send_modal(_AmountEditModal(self))

    @discord.ui.button(label="取消", style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._finalized:
            await interaction.response.send_message("（這張卡片已經結束了）", ephemeral=True)
            return
        self._finalized = True
        for item in self.children:
            item.disabled = True
        self.stop()
        await interaction.response.edit_message(embed=self.build_embed(status="❌ 已取消"), view=self)

    @discord.ui.button(label="確認送出", style=discord.ButtonStyle.success, row=2)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._finalized:
            await interaction.response.send_message("（這張卡片已經結束了）", ephemeral=True)
            return
        if not self.debtor or not self.creditor:
            await interaction.response.send_message("（請先選好『欠錢的人』和『被欠款的人』喔）", ephemeral=True)
            return
        if self.debtor.id == self.creditor.id:
            await interaction.response.send_message("（不能欠自己錢啦！）", ephemeral=True)
            return

        # 先鎖旗標＋disable 元件並送出 edit_message，才去代發指令——
        # 確保就算代發那步後續失敗，卡片也不會停在「可以再按一次」的狀態造成重複送出。
        self._finalized = True
        for item in self.children:
            item.disabled = True
        self.stop()
        await interaction.response.edit_message(embed=self.build_embed(status="✅ 已送出"), view=self)

        try:
            await self._dispatch_command(self.debtor, self.creditor, self.item_price)
        except Exception as e:
            logger.error(f"[拆帳代發] 送出 {W2W_COMMAND_PREFIX} 指令失敗: {e}", exc_info=True)
            await interaction.followup.send("（代發指令失敗了，請通知管理員檢查頻道設定）", ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ 已送出：{self.debtor.mention} 欠 {self.creditor.mention} "
            f"**{format_amount(self.item_price)}** 元（{self.item_name}）",
            ephemeral=True
        )


class _DebtorSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="選擇欠錢的人", min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: ReceiptItemView = self.view
        view.debtor = self.values[0]
        await interaction.response.edit_message(embed=view.build_embed())


class _CreditorSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="選擇被欠款的人", min_values=1, max_values=1, row=1)

    async def callback(self, interaction: discord.Interaction):
        view: ReceiptItemView = self.view
        view.creditor = self.values[0]
        await interaction.response.edit_message(embed=view.build_embed())


class _AmountEditModal(discord.ui.Modal, title="修改金額"):
    amount_input = discord.ui.TextInput(label="正確金額", placeholder="例如 120 或 99.5", max_length=12)

    def __init__(self, parent_view: ReceiptItemView):
        super().__init__()
        self.parent_view = parent_view
        self.amount_input.default = format_amount(parent_view.item_price)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_amount = float(self.amount_input.value.strip())
            if new_amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("（請輸入一個大於 0 的數字）", ephemeral=True)
            return
        self.parent_view.item_price = new_amount
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)
