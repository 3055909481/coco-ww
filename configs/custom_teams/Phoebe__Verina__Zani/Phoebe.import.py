from src.char.BaseChar import SwitchPriority
from src.char.Phoebe import Phoebe as NativePhoebe


class Phoebe(NativePhoebe):
    """赞菲维队(赞妮/菲比/维里奈)里的菲比。非本队时整套走原版。

    【她不驱动任何一段轴】: 启动轴和循环轴的入口都是赞妮 —— 一圈的第一个动作是她的
    防反 E, 最后一个动作是她的 R2, 首尾都在她身上。宏体在同目录的 Zani.py 里 ——
    自定义角色文件由 CustomCharLoader 按路径单独加载, 互相 import 不到, 但同一个
    task 下用 task.chars 拿得到彼此的实例, 这是跨文件复用宏原语的唯一路子。

    【她在一圈里上场两次】(视频 510.5 的过场 + 518.0 的输出两套), 两次都由宏按数字键
    切进来, 所以这里【一个动作都不写】。走到 do_perform 就说明宏散了(切人失败/脱战
    重进) —— 打一小段把场子交出去, 让赞妮重新拿到入口。

    【原版的 absolution_or_confession / starflash_combo / check_middle_star 一行没动】:
    宏调的就是这三个原版方法(长按E进告解、左键点到重击图标亮再长按放重击星辉、
    读中间那颗星), 上游以后改了它们, 这条轴自动跟着走。
    """

    @property
    def is_in_zanfei_nai(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Zani', 'Verina'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """拿到持宏的赞妮实例。

        【按能力找, 不按名字找】: 赞妮那份自定义没启用的话这里就该返回 None,
        然后老老实实走原版轮换 —— 而不是拿着一个没有 perform_opener 的实例崩掉。
        """
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
            # 【启动轴待跑时一下都不打, 直接把场子还回去】: 轴的第一个动作是赞妮那记
            # 防反, 每多打一下她就晚一下
            self.logger.info('phoebe: opener pending -> handing the field to Zani right away')
            return self.switch_next_char()
        # 【不要在这里跑循环轴】: 循环轴的第一步是"赞妮 R2 之后一直戳E", 从菲比这里
        # 起跑一定错位。打一小段把场子还回去就行
        self.logger.info('phoebe do_perform in zanfei team (macro fell apart) -> yielding the field')
        self.continues_normal_attack(0.6)
        return self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO —— 轴的第一个动作是赞妮的防反 E, 菲比不该抢先上场。

        (框架那边赞妮在启动轴待跑时返回 MUST, 不会因为这里返回 NO 就原地打转)
        """
        if self.is_in_zanfei_nai:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
