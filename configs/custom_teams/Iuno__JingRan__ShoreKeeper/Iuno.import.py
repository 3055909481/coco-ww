from src.char.BaseChar import SwitchPriority
from src.char.Iuno import Iuno as NativeIuno


class Iuno(NativeIuno):
    """景月守(景燃/尤诺/守岸人)里的尤诺。非本队时整套走原版。

    她这一段的轴写在同目录的 JingRan.py(seg_iuno)。框架调到她的 do_perform 只有两种情况:
    宏散了之后她带着变奏上场(drive 会从她这一段接着打), 或者被框架随手选上(drive 先把场子交给守岸人)。
    """

    @property
    def is_in_jys(self) -> bool:
        members = 0
        for char in self.task.chars:
            if char and char.name in {'JingRan', 'ShoreKeeper'}:
                members += 1
        return members == 2

    def macro(self):
        """持宏的景燃。景燃那份自定义没启用时返回 None, 走原版。"""
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
        if not self.is_in_jys:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('jys: JingRan custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时让守岸人先上。"""
        if self.is_in_jys:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
