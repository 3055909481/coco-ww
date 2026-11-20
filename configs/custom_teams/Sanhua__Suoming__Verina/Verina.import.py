from src.char.BaseChar import SwitchPriority
from src.char.Verina import Verina as NativeVerina


class Verina(NativeVerina):
    """锁散维(锁暝/散华/维里奈)里的维里奈。非本队时整套走原版。

    宏体在同目录的 Suoming.py, 这里只转发。宏散了被框架调到时也转给锁暝的 drive()(循环轴先把场子交给散华)。
    自定义角色文件互相 import 不到, 用 task.chars 里的实例按能力找。
    """

    @property
    def is_in_ssw(self) -> bool:
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Sanhua', 'Suoming'}:
                members += 1
        return members == 2

    def macro(self):
        """持宏的锁暝。锁暝那份自定义没启用时返回 None, 走原版。"""
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
        if not self.is_in_ssw:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('ssw: Suoming custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO。【要写在 super() 之前】: 原版对绯雪有 MUST。"""
        if self.is_in_ssw:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
