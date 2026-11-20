from src.char.BaseChar import SwitchPriority
from src.char.ShoreKeeper import ShoreKeeper as NativeShoreKeeper


class ShoreKeeper(NativeShoreKeeper):
    """心锁守(心/锁暝/守岸人)里的守岸人。非本队时整套走原版。

    宏体在同目录的 Hsin.py。启动轴和每一圈循环轴都从她开始: 宏在她变奏入场、HUD 回来那一刻返回,
    下一次框架调的就是她, 这里直接转给心的 drive()。
    自定义角色文件互相 import 不到, 用 task.chars 里的实例按能力找。
    """

    @property
    def is_in_xss(self) -> bool:
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Hsin', 'Suoming'}:
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
        """启动轴待跑时她必须先上(第一个动作是她的普攻); 其余时候宏散了也优先把场子交给她, 循环轴从她接上。"""
        if self.is_in_xss:
            macro = self.macro()
            if macro is not None:
                return SwitchPriority.MUST if macro.opener_pending() else SwitchPriority.HIGH
        return super().get_switch_priority(current_char, has_intro, target_low_con)
