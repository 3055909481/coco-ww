from src.char.BaseChar import SwitchPriority
from src.char.Douling import Douling as NativeDouling


class Douling(NativeDouling):
    """露丽卜(露西/丽贝卡/卜灵)里的卜灵。非本队时整套走原版。

    宏体在同目录的 Lucy.py。循环轴每一圈从她开始: 宏在她变奏入场那一刻返回,
    下一次框架调的就是她, 这里直接转给露西的 drive()。
    自定义角色文件互相 import 不到, 用 task.chars 里的实例按能力找。
    """

    @property
    def is_healer(self):
        """【本队里不把卜灵算作奶妈】。

        CharFactory 把她登记成 HEALER, 而 AutoCombatTask 的战斗循环在任何 do_perform 之前先跑
        switch_healer(): 看到场上不是奶妈就切过去 —— 启动轴第一个动作是露西的 E, 会白切两次人。
        只在本队关掉, 非本队保持原样。
        """
        try:
            if self.is_in_lrb:
                return False
        except Exception:
            pass  # __init__ 里会先读一次, 那时队伍还没加载
        return super().is_healer

    @property
    def is_in_lrb(self) -> bool:
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
        # 【必须写在 reset_state 里】: CharFactory 换类时会把内置实例的字段整体拷过来, 改过的值被打回内置值。
        # _segment 是内置卜灵"分两次上场"的状态机, 本队她一次上场跑完, 留着只会在退回内置逻辑时切一半
        self._segment = 1
        self.check_f_on_switch = False

    def do_perform(self):
        if not self.is_in_lrb:
            return super().do_perform()
        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            self.logger.warning('lrb: Lucy custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时让露西先上; 其余时候宏散了就优先把场子交给她, 循环轴从她接上。
        (内置版那条"离开不到 8 秒不许切回来"是给它两段式轮换用的, 本队不走)
        """
        if self.is_in_lrb:
            macro = self.macro()
            if macro is not None:
                return SwitchPriority.NO if macro.opener_pending() else SwitchPriority.HIGH
        return super().get_switch_priority(current_char, has_intro, target_low_con)
