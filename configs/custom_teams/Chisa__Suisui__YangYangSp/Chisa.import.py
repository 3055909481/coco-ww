from src.char.BaseChar import SwitchPriority
from src.char.Chisa import Chisa as NativeChisa


class Chisa(NativeChisa):
    """秧千穗(千咲/穗穗/秧秧·玄翎)里的千咲。非本队时整套走原版。

    【她不驱动任何一段轴】: 两条轴的宏体都在同目录的 YangYangSp.py 里 ——
    自定义角色文件互相 import 不到, 但同一个 task 下用 task.chars 拿得到彼此的实例。
    宏在跑的时候她全程由宏按数字键切进切出(一轮里上场 5 次), 宏末尾的 2羽1苍 是秧秧自己的。
    这里一个动作都不用写。

    走到 do_perform 说明宏散了(切人失败/脱战重进): 启动轴待跑就立刻让场给穗穗
    (启动宏第一格是【穗穗的 A】); 否则打一轮原版, 打完框架会把场子交给秧秧, 宏接上。
    """

    @property
    def is_in_yangqiansui(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Suisui', 'YangYangSp'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """持宏的秧秧。【按能力找】: 秧秧那份自定义没启用时返回 None, 老老实实走原版。"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_opener') and hasattr(char, 'quick_switch'):
                return char
        return None

    def do_perform(self):
        if not self.is_in_yangqiansui:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('yangqiansui: YangYangSp custom code is off, '
                                'falling back to the native rotation')
            return super().do_perform()
        if macro.opener_pending():
            self.logger.info('chisa: opener pending -> handing the field over right away')
            return self.switch_next_char()
        self.logger.info('chisa do_perform in yangqiansui team (macro fell apart) -> native rotation')
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO —— 开局场上得是穗穗, 千咲抢先上场会把整张表错开一格。

        【要写在 super() 之前】: 原版在有变奏时会返回 MUST。
        """
        if self.is_in_yangqiansui:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
