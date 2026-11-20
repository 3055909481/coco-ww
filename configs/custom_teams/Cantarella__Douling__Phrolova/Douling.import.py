import time

from src.char.BaseChar import SwitchPriority
from src.char.Douling import Douling as NativeDouling


class Douling(NativeDouling):
    """弗坎卜队(弗洛洛/坎特蕾拉/卜灵)里的卜灵。非本队时整套走原版。

    【她不驱动任何一段轴】: 宏体在同目录的 Phrolova.py, 启动轴、循环轴都由弗洛洛起跑,
    她每一圈上场一次, 全程由宏按数字键切进切出。所以框架调到她的 do_perform
    就说明宏散了(切人失败 / 脱战重进) —— 打一小段把场子交出去, 让弗洛洛重新拿到入口。
    自定义角色文件互相 import 不到, 用 task.chars 里的实例按能力找。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 内置版没有这个字段, 不会被 CharFactory 换类时打回内置值
        self.macro_yield_logged = 0.0

    @property
    def is_healer(self):
        """【本队里不把卜灵算作奶妈】。

        CharFactory 把她登记成 HEALER, 而 AutoCombatTask 的战斗循环在任何 do_perform 之前先跑
        switch_healer(): 看到场上不是奶妈就切过去 —— 这条轴的入口是弗洛洛, 会白切两次人。
        【本队两个奶都要关】: 坎特蕾拉那边同理, 见 Cantarella.is_healer。
        只在本队关掉, 非本队保持原样。
        """
        try:
            if self.is_in_phro_cycle:
                return False
        except Exception:
            pass  # __init__ 里会先读一次, 那时队伍还没加载
        return super().is_healer

    @property
    def is_in_phro_cycle(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char is not None and char.name in {'Phrolova', 'Cantarella'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """拿到持宏的弗洛洛实例。她那份自定义没启用时返回 None, 走原版。"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_loop') and hasattr(char, 'quick_switch'):
                return char
        return None

    def reset_state(self):
        super().reset_state()
        # 【必须写在 reset_state 里】: CharFactory 换类时会把内置实例的字段整体拷过来, 改过的值被打回内置值。
        # _segment 是内置卜灵"分两次上场"的状态机, 本队她一次上场跑完, 留着只会在退回内置逻辑时切一半
        self._segment = 1
        self.check_f_on_switch = False

    def do_perform(self):
        if not self.is_in_phro_cycle:
            return super().do_perform()
        # 处决全程由宏自己按
        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            self.logger.warning('phro cycle: Phrolova custom code is off, falling back to the native rotation')
            return super().do_perform()
        now = time.time()
        if now - self.macro_yield_logged > 5:
            self.macro_yield_logged = now
            self.logger.info('buling: macro drives this team, handing the field back')
        # 【不要在这里跑卜灵段】: 那一段要接着切坎特蕾拉、再由坎特蕾拉变奏弗洛洛,
        # 从这里单独起跑接不上循环轴的入口。打一小段把场子交出去就行
        self.continues_normal_attack(0.6)
        return self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时让位给弗洛洛(启动轴由她起手)。

        【这里别打日志】: 框架会对每个候选反复调用, 会刷屏。
        """
        if self.is_in_phro_cycle:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
