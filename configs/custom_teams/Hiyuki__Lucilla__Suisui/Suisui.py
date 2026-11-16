from src.char.BaseChar import SwitchPriority
from src.char.Suisui import Suisui as NativeSuisui


class Suisui(NativeSuisui):
    """绯洛穗(绯雪/洛瑟菈/穗穗)手打宏轴里的穗穗。非本队时整套走原版。

    【她不驱动任何一段轴】: 宏体在同目录的 Hiyuki.py。启动轴从她在场时按下左键开始,
    走到 do_perform 就直接转给绯雪的 drive()。
    """

    @property
    def is_healer(self):
        """【本队里不把穗穗算作奶妈】: 框架开战前的 switch_healer() 会先切给奶妈, 白切一次人。"""
        try:
            if self.is_in_hls:
                return False
        except Exception:
            pass  # __init__ 里会先读一次, 那时队伍还没加载
        return super().is_healer

    @property
    def is_in_hls(self) -> bool:
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Hiyuki', 'Lucilla'}:
                members += 1
        return members == 2

    def macro(self):
        """持宏的绯雪。绯雪那份自定义没启用时返回 None, 走原版。"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'drive') and hasattr(char, 'sw_press'):
                return char
        return None

    def reset_state(self):
        super().reset_state()
        self.check_f_on_switch = False

    def do_perform(self):
        if not self.is_in_hls:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('hls: Hiyuki custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 MUST: 启动轴从她在场开始。【要写在 super() 之前】: 原版有自己的切人锁。"""
        if self.is_in_hls:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)
