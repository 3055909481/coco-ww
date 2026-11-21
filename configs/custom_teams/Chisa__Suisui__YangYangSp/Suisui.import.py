from src.char.BaseChar import SwitchPriority
from src.char.Suisui import Suisui as NativeSuisui


class Suisui(NativeSuisui):
    """秧千穗(千咲/穗穗/秧秧·玄翎)里的穗穗。非本队时整套走原版。

    【她不驱动任何一段轴, 但启动轴是从她身上起跑的】: 启动宏第一格就是【穗穗 3A】
    (字幕: "流程从穗穗3a到秧秧最后的变奏入场结束"), 开局场上本来就该是她(1 号位)。
    所以这里跟千咲那份不一样 ——
      · get_switch_priority: 启动轴待跑时给 MUST, 让框架先把她放上场;
      · do_perform: 启动轴待跑时【不让场】, 直接把自己当入场角色交给秧秧的 perform_opener。
        让场再切回来要多花一次换人 CD, 而宏的第一格就是她的普攻, 白等那一下没有意义。
    宏体在同目录的 YangYangSp.py 里; 宏在跑的时候她全程由宏按数字键切进切出(一轮 4 次)。

    走到 do_perform 而启动轴不待跑, 说明宏散了(切人失败/脱战重进): 打一轮原版,
    打完框架会把场子交给秧秧, 从循环宏开头接上。
    """

    @property
    def is_in_yangqiansui(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Chisa', 'YangYangSp'}:
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
            # 【她就是启动宏的入场角色】: 把自己作为 entry_char 传进去, 宏的第一格
            # (穗穗 A1)直接发在场上的她身上, 不用切人
            self.logger.info('suisui: opener pending -> running it from here (she is the entry)')
            if macro.run_opener_from(self):
                return
            self.logger.warning('yangqiansui opener aborted, falling back to the native rotation')
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 MUST —— 宏的第一格是她的普攻, 场上必须先是她。"""
        if self.is_in_yangqiansui:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)
