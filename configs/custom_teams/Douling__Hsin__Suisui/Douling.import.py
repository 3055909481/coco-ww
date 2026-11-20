from src.char.BaseChar import SwitchPriority
from src.char.Douling import Douling as NativeDouling


class Douling(NativeDouling):
    """心卜穗(心/卜灵/穗穗)里的卜灵。非本队时整套走原版。

    【她不驱动任何一段轴】: 宏体在同目录的 Hsin.py, 她全程由宏按数字键切进切出(一圈上场两次)。
    走到 do_perform 说明宏散了(切人失败/脱战重进), 转给心的 drive() —— 它会先把场子交给穗穗再接循环轴。
    自定义角色文件互相 import 不到, 用 task.chars 里的实例按能力找。
    """

    @property
    def is_healer(self):
        """【本队里不把卜灵算作奶妈】: 框架开战前的 switch_healer() 会先切给奶妈, 白切一次人。"""
        try:
            if self.is_in_xbs:
                return False
        except Exception:
            pass  # __init__ 里会先读一次, 那时队伍还没加载
        return super().is_healer

    @property
    def is_in_xbs(self) -> bool:
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Hsin', 'Suisui'}:
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
        # 【必须写在 reset_state 里】: 换类时内置实例的字段会整体拷过来, 改过的值被打回内置值。
        # _segment 是内置卜灵"分两次上场"的状态机, 本队由宏驱动, 留着只会在退回内置逻辑时切一半
        self._segment = 1
        self.check_f_on_switch = False

    def do_perform(self):
        if not self.is_in_xbs:
            return super().do_perform()
        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            self.logger.warning('xbs: Hsin custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO(宏自己切人)。"""
        if self.is_in_xbs:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
