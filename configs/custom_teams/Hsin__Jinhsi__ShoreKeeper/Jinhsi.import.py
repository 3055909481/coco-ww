from src.char.BaseChar import SwitchPriority
from src.char.Jinhsi import Jinhsi as NativeJinhsi


class Jinhsi(NativeJinhsi):
    """心汐守(心/今汐/守岸人)里的今汐。非本队时整套走原版。

    【她不驱动任何一段轴】: 宏体在同目录的 Hsin.py, 她全程由宏按数字键切进切出。
    走到 do_perform 说明宏散了(切人失败/脱战重进), 转给心的 drive() —— 它会先把场子交给守岸人再接循环轴。
    """

    @property
    def is_in_xxs(self) -> bool:
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
        if not self.is_in_xxs:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('xxs: Hsin custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO。【要写在 super() 之前】: 原版在有变奏/凌霄时返回 MUST。"""
        if self.is_in_xxs:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
