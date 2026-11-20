from src.char.BaseChar import SwitchPriority
from src.char.Suoming import Suoming as NativeSuoming


class Suoming(NativeSuoming):
    """锁暝。心锁莉里只做转发: do_perform 交给心(Hsin.drive), 整条轴在 Hsin.py。"""

    @property
    def is_in_xsl(self) -> bool:
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Hsin', 'Lily'}:
                members += 1
        return members == 2

    def macro(self):
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'drive') and hasattr(char, 'quick_switch'):
                return char
        return None

    def reset_state(self):
        super().reset_state()
        # 内置非治疗角色默认 True, 换类时会被打回, 所以在这里重申(这条轴不在切人时按处决)
        self.check_f_on_switch = False

    def do_perform(self):
        if not self.is_in_xsl:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('xsl: Hsin custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if self.is_in_xsl:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
