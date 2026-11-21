from src.char.BaseChar import SwitchPriority
from src.char.Jinhsi import Jinhsi as NativeJinhsi


class Jinhsi(NativeJinhsi):
    """龙凤雪队(今汐/长离/绯雪)里的今汐。非本队时整套走原版。

    【她不驱动任何一段轴】: 入口是长离, 宏体在同目录的 Changli.py 里 ——
    自定义角色文件互相 import 不到, 但同一个 task 下用 task.chars 拿得到彼此的实例。
    宏在跑的时候她全程由宏按数字键切进切出, 这里一个动作都不用写。

    走到 do_perform 说明轴没在跑: 轴待跑就立刻让场给长离; 否则打一轮原版。
    """

    @property
    def is_in_longfengxue(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Changli', 'Hiyuki'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """持宏的长离。【按能力找】: 长离那份自定义没启用时返回 None, 老老实实走原版。"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_axis') and hasattr(char, 'quick_switch'):
                return char
        return None

    def do_perform(self):
        if not self.is_in_longfengxue:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('longfengxue: Changli custom code is off, falling back to the native rotation')
            return super().do_perform()
        if macro.opener_pending():
            self.logger.info('jinhsi: axis pending -> handing the field to Changli right away')
            return self.switch_next_char()
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """轴待跑时返回 NO。【要写在 super() 之前】: 原版在有变奏/凌霄时返回 MUST。"""
        if self.is_in_longfengxue:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
