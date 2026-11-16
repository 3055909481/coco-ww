from src.char.BaseChar import SwitchPriority
from src.char.Mornye import Mornye as NativeMornye


class Mornye(NativeMornye):
    """嘉露莫(嘉贝莉娜/露帕/莫宁)里的莫宁。非本队时整套走原版。

    【她是循环轴的入口】: 宏每圈在她变奏入场、HUD 在场时返回, 下一次框架调的就是她 —— 转给嘉贝的 drive(),
    循环轴从她的 R 开始。宏体在同目录的 Galbrena.py; 自定义角色文件互相 import 不到, 用 task.chars 里的实例按能力找。
    """

    @property
    def is_healer(self):
        """【本队里不把莫宁算作奶妈】。

        CharFactory 把她登记成 HEALER, AutoCombatTask.run() 战斗循环第一件事就是 switch_healer():
        看到当前角色不是奶妈、队里有奶妈就直接切过去 —— 会在宏交回框架的空当里把人切乱。
        这条轴每圈都有她一整段(E 回血 + 谐振场), 不需要那个保护。只在本队关掉
        (弗坎莫 / 清达莫 / 爱琳莫同一个处理)。
        """
        try:
            if self.is_in_jlm:
                return False
        except Exception:
            pass  # __init__ 里 check_f_on_switch 会先读一次, 那时队伍还没加载
        return super().is_healer

    @property
    def is_in_jlm(self) -> bool:
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Galbrena', 'Lupa'}:
                members += 1
        return members == 2

    def macro(self):
        """持宏的嘉贝。嘉贝那份自定义没启用时返回 None, 走原版。"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'drive') and hasattr(char, 'quick_switch'):
                return char
        return None

    def reset_state(self):
        super().reset_state()
        # 【必须写在 reset_state 里】: 换类时内置实例的字段会整体拷过来, 改过的值被打回内置值
        self.check_f_on_switch = False

    def do_perform(self):
        if not self.is_in_jlm:
            return super().do_perform()
        # 原版 do_perform 会把它改回 True; 处决全程由宏自己按
        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            self.logger.warning('jlm: Galbrena custom code is off, falling back to the native rotation')
            return super().do_perform()
        macro.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO(宏自己切人)。"""
        if self.is_in_jlm:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
