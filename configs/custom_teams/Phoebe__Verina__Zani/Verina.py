from src.char.BaseChar import SwitchPriority
from src.char.Verina import Verina as NativeVerina


class Verina(NativeVerina):
    """赞菲维队(赞妮/菲比/维里奈)里的维里奈。非本队时整套走原版。

    【她不驱动任何一段轴】: 两条轴的入口都是赞妮, 宏体在同目录的 Zani.py 里。
    走到 do_perform 就说明宏散了 —— 打一小段把场子交出去。

    【她那一段的顺序跟原版不一样】(视频组合2「维里奈AAA / 技能+声骸+大招 / 跳A打满协奏」):
      宏里是 3A -> E -> Q -> R -> 跳x2 -> 空中A
      原版 perform_combat 是 3A -> R -> E -> Q -> (重击) -> 跳 -> 2A
    差别在 E/Q 排在 R 前面 —— 光合作用那一格先落地, 大招的治疗和增益才盖在已经铺开
    的场上。所以宏【不调 perform_combat】, 那一段是照视频秒数在 Zani.seg_verina 里
    重写的; 原版留着不动, 是宏散掉之后的兜底。
    """

    @property
    def is_in_zanfei_nai(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Zani', 'Phoebe'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """拿到持宏的赞妮实例。【按能力找, 不按名字找】—— 那份自定义没启用时返回 None。"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_opener') and hasattr(char, 'quick_switch'):
                return char
        return None

    def do_perform(self):
        if not self.is_in_zanfei_nai:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('zanfei: Zani custom code is off, '
                                'falling back to the native rotation')
            return super().do_perform()
        if macro.opener_pending():
            # 【启动轴待跑时一下都不打, 直接把场子还回去】: 界面上那个"战斗前后切换到
            # 治疗角色"打开时, AutoCombatTask 进战斗的第一件事就是 switch_healer(),
            # 把她这个奶换上场 —— 于是第一轮 perform 落在她身上。这里每多打一下,
            # 赞妮那记防反就晚一下, 而古龙的开场拍掌+地刺是不等人的
            self.logger.info('verina: opener pending -> handing the field to Zani right away')
            return self.switch_next_char()
        self.logger.info('verina do_perform in zanfei team (macro fell apart) -> yielding the field')
        self.continues_normal_attack(0.6)
        return self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO —— 轴的第一个动作是赞妮的防反 E。

        【这一条对奶妈尤其要紧】: 框架的 unbuffed_support 分支本来就偏向先上奶,
        开局第一次选人很容易选到她, 那样赞妮那下防反就吃不到古龙的开场拍掌+地刺了。
        """
        if self.is_in_zanfei_nai:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
