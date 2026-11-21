from src.char.BaseChar import SwitchPriority
from src.char.Lupa import Lupa as NativeLupa


class Lupa(NativeLupa):
    """三火队(长离/露帕/布兰特)里的露帕。非本队时整套走原版。

    【她不驱动任何一段轴】: 两条轴的入口都是长离。宏体在同目录的 Changli.py 里 ——
    自定义角色文件由 CustomCharLoader 按路径单独加载, 互相 import 不到, 但同一个
    task 下用 task.chars 拿得到彼此的实例, 这是跨文件复用宏原语的唯一路子。

    【她是一圈里上场最多的人 —— 循环轴里六次】(格 2/5/7/9/11/13), 全部由宏按数字键
    切进来, 所以这里【一个动作都不写】。走到 do_perform 就说明宏散了(切人失败/脱战
    重进) —— 打一小段把场子交出去, 让长离重新拿到入口。

    【原版的 res_wolf / judge_forte / click_jump_with_click 一行没动】: 狼魂那套判据
    (lupa_wolf_icon2 找图 + 回路条频率分析)上游以后改了, 这条轴自动跟着走。

    【原版认长离的名字是 'chang_changli'】(内置 Lupa.py 里 check_outro 和
    get_switch_priority 都写的这一个)。长离在 CharFactory 里其实挂着两个 char_name
    (chang_changli / char_changli2, 两套立绘) —— 【玩家用第二套立绘时原版那两条判定
    会静默失效】。本条轴不受影响: 宏是按数字键切人的, 队伍判定走的是类名(char.name),
    跟立绘无关。留这段话是因为它解释了"为什么不能把队伍判定写成对 char_name"。
    """

    @property
    def is_in_three_fire(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Changli', 'Brant'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """拿到持宏的长离实例。

        【按能力找, 不按名字找】: 长离那份自定义没启用的话这里就该返回 None,
        然后老老实实走原版轮换 —— 而不是拿着一个没有 perform_opener 的实例崩掉。
        """
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_opener') and hasattr(char, 'quick_switch'):
                return char
        return None

    def do_perform(self):
        if not self.is_in_three_fire:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('three-fire: Changli custom code is off, '
                                'falling back to the native rotation')
            return super().do_perform()
        if macro.opener_pending():
            # 【启动轴待跑时一下都不打, 直接把场子还回去】
            self.logger.info('lupa: opener pending -> handing the field to Changli right away')
            return self.switch_next_char()
        self.logger.info('lupa do_perform in three-fire team (macro fell apart) -> yielding the field')
        self.continues_normal_attack(0.6)
        return self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO —— 轴的第一个动作是长离的 A + E, 露帕不该抢先上场。

        【NO 要压过原版那两条 MUST】: 原版在 `still_in_liberation()`(大招后 12 秒内)
        和 "上一个是长离且有变奏" 两种情况下都返回 MUST。所以这一条必须写在
        super() 之前。
        """
        if self.is_in_three_fire:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
