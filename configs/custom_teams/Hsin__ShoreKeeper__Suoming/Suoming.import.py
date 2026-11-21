from src.char.BaseChar import SwitchPriority
from src.char.Suoming import Suoming as NativeSuoming


class Suoming(NativeSuoming):
    """心锁守(心/锁暝/守岸人)里的锁暝。非本队时整套走原版。

    宏体在同目录的 Hsin.py, 这里只转发。宏散了被框架调到时也转给心的 drive()(循环轴先把场子交给守岸人)。
    自定义角色文件互相 import 不到, 用 task.chars 里的实例按能力找。
    """

    @property
    def is_in_xss(self) -> bool:
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Hsin', 'ShoreKeeper'}:
                members += 1
        return members == 2

    def macro(self):
        """持宏的心。心那份自定义没启用时返回 None, 走原版。"""
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
        if not self.is_in_xss:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('xss: Hsin custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO。【要写在 super() 之前】: 原版有自己的切人优先级。"""
        if self.is_in_xss:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
