import time

from src.char.BaseChar import SwitchPriority
from src.char.Rover import Rover as NativeRover


class Rover(NativeRover):
    """卡夏风队(卡提希娅/夏空/风主·气动)里的漂泊者。非本队时整套走原版。

    【他是循环轴的入口】: 上一圈的结尾把他变奏出来, 所以框架下一次调 do_perform
    就是他 —— 这一次调用直接进循环轴。宏体在同目录的 Cartethyia.py 里:
    自定义角色文件由 CustomCharLoader 按路径单独加载, 互相 import 不到, 但同一个
    task 下用 task.chars 拿得到彼此的实例, 这是跨文件复用宏原语的唯一路子。

    启动轴不由他驱动(那一段由卡提起跑), 所以启动轴还没跑时他要【让位】——
    返回 SwitchPriority.NO, 让卡提的 MUST 拿到第一次上场。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 【自定义 __init__ 里跟内置版取值不同的字段会被 CharFactory 打回内置值】
        # (apply_team_char_classes 里 replacement.__dict__.update(char.__dict__))。
        # 这里新加的字段内置版没有, 不受影响
        self.macro_yield_logged = 0.0

    @property
    def is_in_cart_cycle(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char is not None and char.name in {'Cartethyia', 'Ciaccona'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """拿到持宏的卡提实例。

        【按能力找, 不按名字找】: 卡提那份自定义没启用的话这里就该返回 None,
        然后老老实实走原版轮换 —— 而不是拿着一个没有 perform_loop 的实例崩掉。
        """
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_loop') and hasattr(char, 'quick_switch'):
                return char
        return None

    def do_perform(self):
        if not self.is_in_cart_cycle:
            return super().do_perform()
        # 处决全程由宏自己按 —— 留着框架的自动 F 会在切人时插一发, 而击破动画带
        # 全局时停 + 队伍 HUD 消失, 正好把切人循环判成脱战
        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            self.logger.warning('cart cycle: Cartethyia custom code is off, '
                                'falling back to the native rotation')
            return super().do_perform()

        if macro.opener_pending():
            # 启动轴还没跑 -> 立刻把场子让出去, 别在这里打满一整套原版轮换
            now = time.time()
            if now - self.macro_yield_logged > 5:
                self.macro_yield_logged = now
                self.logger.info('rover: cartethyia opener pending, yielding the field')
            return self.switch_next_char()

        # 【把自己传进去】: 框架是因为风主在场才调的这个 do_perform, 所以这就是
        # "现在场上是谁"最可靠的答案。宏据此跳过开场那次切人。
        # 【不能让它去读屏判断】: in_team() 在战斗开局的头 0.8 秒会说谎
        if macro.perform_loop(self):
            return
        self.logger.warning('rover: loop aborted, falling back to the native rotation')
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时让位给卡提 —— 轴的第一个动作是她的 R。

        【这里别打日志】: 框架会对每个候选反复调用, 会刷屏。
        """
        if self.is_in_cart_cycle:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
