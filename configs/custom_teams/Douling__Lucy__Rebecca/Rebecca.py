from src.char.BaseChar import SwitchPriority
from src.char.Rebecca import Rebecca as NativeRebecca


class Rebecca(NativeRebecca):
    """露丽卜(露西/丽贝卡/卜灵)里的丽贝卡。非本队时整套走原版。

    她不驱动任何一段轴: 宏体在同目录的 Lucy.py, 宏在跑的时候她全程由宏按数字键切进切出。
    走到 do_perform 说明宏散了(切人失败/脱战重进), 直接转给露西的 drive() —— 那边会先把场子交给卜灵。
    """

    @property
    def is_in_lrb(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Lucy', 'Douling'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'drive') and hasattr(char, 'quick_switch'):
                return char
        return None

    def reset_state(self):
        super().reset_state()
        self.check_f_on_switch = False

    def do_perform(self):
        if not self.is_in_lrb:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('lrb: Lucy custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if self.is_in_lrb:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
