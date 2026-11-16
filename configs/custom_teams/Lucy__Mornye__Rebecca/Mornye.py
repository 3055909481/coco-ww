from src.char.BaseChar import SwitchPriority
from src.char.Mornye import Mornye as NativeMornye


class Mornye(NativeMornye):
    """露莫丽(露西/莫宁/丽贝卡)里的莫宁。非本队时整套走原版。

    宏体在同目录的 Lucy.py。循环轴每一圈从她开始: 宏在她变奏入场那一刻返回,
    下一次框架调的就是她, 这里直接转给露西的 drive()。
    自定义角色文件互相 import 不到, 用 task.chars 里的实例按能力找。
    """

    @property
    def is_healer(self):
        """【本队里不把莫宁算作奶妈】。

        CharFactory 把她登记成 HEALER, 而 AutoCombatTask 的战斗循环在任何 do_perform 之前先跑
        switch_healer(): 看到场上不是奶妈就切过去 —— 启动轴第一个动作是露西的 E, 会白切两次人。
        只在本队关掉, 非本队保持原样(清达莫、爱琳莫两队同样处理)。
        """
        try:
            if self.is_in_lmr:
                return False
        except Exception:
            pass  # __init__ 里会先读一次, 那时队伍还没加载
        return super().is_healer

    @property
    def is_in_lmr(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Lucy', 'Rebecca'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """持宏的露西。露西那份自定义没启用时返回 None, 走原版。"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'drive') and hasattr(char, 'quick_switch'):
                return char
        return None

    def reset_state(self):
        super().reset_state()
        self.check_f_on_switch = False

    def do_perform(self):
        if not self.is_in_lmr:
            return super().do_perform()
        # 原版 do_perform 会把它改回 True, 本队处决全由宏/玩家管
        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            self.logger.warning('lmr: Lucy custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时让露西先上; 其余时候宏散了就优先把场子交给她, 循环轴从她接上。"""
        if self.is_in_lmr:
            macro = self.macro()
            if macro is not None:
                return SwitchPriority.NO if macro.opener_pending() else SwitchPriority.HIGH
        return super().get_switch_priority(current_char, has_intro, target_low_con)
