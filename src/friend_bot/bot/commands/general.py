from .help import HelpCommandsMixin
from .search import SearchCommandsMixin
from .profile import ProfileCommandsMixin, TIER_NAME_MAP, render_favorability_bar

class GeneralCommandsMixin(HelpCommandsMixin, SearchCommandsMixin, ProfileCommandsMixin):
    """通用指令組合 Mixin（相容 HelpCommandsMixin, SearchCommandsMixin, ProfileCommandsMixin）"""

    def register_general_commands(self):
        self.register_help_commands()
        self.register_search_commands()
        self.register_profile_commands()

__all__ = [
    "GeneralCommandsMixin",
    "HelpCommandsMixin",
    "SearchCommandsMixin",
    "ProfileCommandsMixin",
    "TIER_NAME_MAP",
    "render_favorability_bar"
]
