import time

from src.char.BaseChar import SwitchPriority
from src.char.Ciaccona import Ciaccona as NativeCiaccona


class Ciaccona(NativeCiaccona):
    """卡夏风队(卡提希娅/夏空/风主·气动)里的夏空。非本队时整套走原版。

    【她不驱动任何一段轴】: 启动轴由卡提起跑, 循环轴由风主起跑, 中间她上场三次
    都是宏按数字键直切进来的。所以框架调到她的 do_perform 就说明宏散了
    (切人失败 / 脱战重进) —— 打一小段普攻把场子交出去, 让卡提或风主重新拿到入口。

    【原版的 judge_forte() 一行没动】: 宏读她的回路格数用的就是那个函数
    (满格走 is_mouse_forte_full, 没满走能量条的频谱判据), 上游以后改了它,
    这条轴自动跟着走。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.macro_yield_logged = 0.0

    @property
    def is_in_cart_cycle(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char is not None and char.name in {'Cartethyia', 'Rover'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """拿到持宏的卡提实例。【按能力找, 不按名字找】。"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_loop') and hasattr(char, 'quick_switch'):
                return char
        return None

    def do_perform(self):
        if not self.is_in_cart_cycle:
            return super().do_perform()
        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            return super().do_perform()
        now = time.time()
        if now - self.macro_yield_logged > 5:
            self.macro_yield_logged = now
            self.logger.info('ciaccona: macro drives this team, handing the field back')
        # 【不要在这里跑循环轴】: 循环轴的第一步假设风主刚变奏入场, 从夏空这里
        # 起跑一定错位。打一小段把场子交出去就行
        self.continues_normal_attack(0.6)
        return self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时让位给卡提。【这里别打日志】, 框架会对每个候选反复调用。"""
        if self.is_in_cart_cycle:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
