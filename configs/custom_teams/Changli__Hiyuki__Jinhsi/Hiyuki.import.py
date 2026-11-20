from src.char.BaseChar import SwitchPriority
from src.char.Hiyuki import Hiyuki as NativeHiyuki


class Hiyuki(NativeHiyuki):
    """龙凤雪队(今汐/长离/绯雪)里的绯雪。非本队时整套走原版。

    【她不驱动任何一段轴】: 入口是长离, 宏体在同目录的 Changli.py 里, 宏在跑的时候她全程由宏切进切出。
    走到 do_perform 说明轴没在跑: 轴待跑就立刻让场给长离; 否则打一轮原版。
    """

    @property
    def is_in_longfengxue(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Changli', 'Jinhsi'}:
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
            self.logger.info('hiyuki: axis pending -> handing the field to Changli right away')
            return self.switch_next_char()
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if self.is_in_longfengxue:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
