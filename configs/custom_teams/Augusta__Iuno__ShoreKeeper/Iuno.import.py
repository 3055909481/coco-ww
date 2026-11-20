from src.char.BaseChar import SwitchPriority
from src.char.Iuno import Iuno as NativeIuno


class Iuno(NativeIuno):
    """日月守(奥古斯塔/尤诺/守岸人)里的尤诺。非本队时整套走原版。

    【她不驱动任何一段轴】: 宏体在同目录的 Augusta.py, 她全程由宏按数字键切进切出。
    框架调到她的 do_perform 就转给奥古斯塔的 drive() —— 每一圈从守岸人变奏入场开始, 宏散了也从这儿接回去。
    自定义角色文件互相 import 不到, 用 task.chars 里的实例按能力找。
    """

    @property
    def is_in_rys(self) -> bool:
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Augusta', 'ShoreKeeper'}:
                members += 1
        return members == 2

    def macro(self):
        """持宏的奥古斯塔。她那份自定义没启用时返回 None, 走原版。"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'drive') and hasattr(char, 'quick_switch'):
                return char
        return None

    def reset_state(self):
        super().reset_state()
        # 【必须写在 reset_state 里】: 换类时内置实例的字段会整体拷过来, 改过的值被打回内置值
        self.check_f_on_switch = False

    def do_perform(self):
        if not self.is_in_rys:
            return super().do_perform()
        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            self.logger.warning('rys: Augusta custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO(宏自己切人)。"""
        if self.is_in_rys:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
