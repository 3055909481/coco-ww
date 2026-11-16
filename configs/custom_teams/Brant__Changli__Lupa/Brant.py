from src.char.BaseChar import SwitchPriority
from src.char.Brant import Brant as NativeBrant


class Brant(NativeBrant):
    """三火队(长离/露帕/布兰特)里的船长。非本队时整套走原版。

    【他不驱动任何一段轴】: 两条轴的入口都是长离 —— 启动轴的第一个动作是她的
    A + E, 循环轴一圈的首尾也都在她身上。宏体在同目录的 Changli.py 里 ——
    自定义角色文件由 CustomCharLoader 按路径单独加载, 互相 import 不到, 但同一个
    task 下用 task.chars 拿得到彼此的实例, 这是跨文件复用宏原语的唯一路子。

    【他在循环轴一圈里上场五次】(格 3/6/10/14/16), 五次都由宏按数字键切进来,
    所以这里【一个动作都不写】。走到 do_perform 就说明宏散了(切人失败/脱战重进)
    —— 打一小段把场子交出去, 让长离重新拿到入口。

    【原版的 resonance_forte_full / click_jump_with_click / perform_in_outro 一行没动】:
    "船锚"就是原版那句 `is_forte_full() and resonance_available() -> resonance_forte_full()`,
    宏里的 press_anchor 用的是同一个判据(is_forte_full), 上游改了判据这条轴自动跟着走。

    【他在内置表里是 HEALER】(CharFactory.py:104 char_type=HEALER, ring_index=FIRE)。
    本条轴【没有动这个类型】: healer 那套(healer_full_con_switch_locked 之类)只作用在
    框架自己的选人上, 而宏是直接按数字键切的, 绕过了那条路。宏散掉之后框架接手时
    那套逻辑照常生效, 正好是我们要的兜底行为。
    """

    @property
    def is_in_three_fire(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Changli', 'Lupa'}:
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
            # 【启动轴待跑时一下都不打, 直接把场子还回去】: 轴的第一个动作是长离的
            # A + E, 每多打一下她就晚一下
            self.logger.info('brant: opener pending -> handing the field to Changli right away')
            return self.switch_next_char()
        # 【不要在这里跑循环轴】: 循环轴的第一步是"长离预输入普攻切露帕变奏船长",
        # 从船长这里起跑一定错位。打一小段把场子还回去就行
        self.logger.info('brant do_perform in three-fire team (macro fell apart) -> yielding the field')
        self.continues_normal_attack(0.6)
        return self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO —— 轴的第一个动作是长离的 A + E, 船长不该抢先上场。

        (框架那边长离在启动轴待跑时返回 MUST, 不会因为这里返回 NO 就原地打转)

        【NO 要压过原版那两条 MUST】: 原版在 `still_in_liberation()`(大招后 12 秒内)
        和 "上一个是露帕且有变奏" 两种情况下都返回 MUST。开局那一刻两条都不成立,
        但脱战重进时大招计时可能还没过 —— 所以这一条必须写在 super() 之前。
        """
        if self.is_in_three_fire:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
