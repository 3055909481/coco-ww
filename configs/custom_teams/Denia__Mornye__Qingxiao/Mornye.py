import time

from src.char.BaseChar import SwitchPriority
from src.char.Mornye import Mornye as NativeMornye


class Mornye(NativeMornye):
    """清达莫队(清宵/达妮娅/莫宁)里的莫宁。非本队时整套走原版。

    【她是循环轴的入口】: 启动轴的末尾是"变奏莫", 所以每一轮循环都从她开始。
    宏体在同目录的 Denia.py 里 —— 自定义角色文件互相 import 不到, 但同一个 task 下
    用 task.chars 拿得到彼此的实例。

    启动轴她也能驱动: 开局在场的不一定是清宵(战斗开始时框架不选人, 场上是探索时
    用的那个人), 这时由她起手, 宏自己会先把清宵换上来。
    """

    @property
    def is_healer(self):
        """【本队里不把莫宁算作奶妈】。

        CharFactory.py:122 把她登记成 CharType.HEALER —— 原版她确实兼职奶
        (src/char/Mornye.py 里那句"地面奶一下, 防止主C死亡")。
        但 AutoCombatTask.run() 在战斗循环的【第一件事】就是 switch_healer(),
        排在任何角色的 do_perform 之前; 它看到"当前角色不是奶妈、队里有奶妈"就
        直接切过去。而【这条轴的第一个动作是清宵的"跳A+E"】—— 于是每一局都要白花
        "切莫宁 -> 再切回清宵"两次切人(约 1.8 秒), 而且清宵被切下去还带着换人 CD。

        这条轴不需要那个保护: 莫宁本来就在轴上大量出场(每一轮循环都从她开始)。
        【只在本队关掉】, 非本队保持原样, 不影响别的配队。
        等价的用户侧开关是任务配置里的 "Switch to Healer before and after Combat",
        但那个是全局的, 会连别的队一起关掉。

        (爱琳莫那队 2026-08-13 定位过同一个问题, 处理方式相同。)
        """
        try:
            if self.is_in_qing_cycle:
                return False
        except Exception:
            pass  # __init__ 里 check_f_on_switch 会先读一次, 那时队伍还没加载
        return super().is_healer

    @property
    def is_in_qing_cycle(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Denia', 'Qingxiao'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """拿到持宏的达妮娅实例。【按能力找, 不按名字找】—— 她那份自定义没启用时
        这里返回 None, 老老实实走原版轮换, 而不是拿着一个没有 perform_loop 的实例崩掉。
        """
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_loop') and hasattr(char, 'quick_switch'):
                return char
        return None

    def do_perform(self):
        if not self.is_in_qing_cycle:
            return super().do_perform()
        # 处决全程由宏自己按, 理由见 Denia.f_break_persist
        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            self.logger.warning('qing cycle: Denia custom code is off, '
                                'falling back to the native rotation')
            return super().do_perform()

        if macro.opener_pending():
            # 【正常路径是清宵驱动启动轴的】(轴的第一个动作是她的"跳A+E")。走到这里说明
            # 开局在场的是莫宁 —— 那就由她起手, 宏自己会先把清宵换上来
            macro.opener_armed = False
            macro.last_opener = time.time()
            if macro.perform_opener(self):
                return
            self.logger.warning('qing opener aborted, falling back to the native rotation')
            return super().do_perform()

        # 【循环轴的入口】: 上一段末尾是"变奏莫", 她刚变奏入场就在场上。
        # 每次调用跑一整轮然后还给框架
        if macro.perform_loop(self):
            return
        self.logger.warning('qing loop aborted, falling back to the native rotation')
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """宏散掉之后, 场子要回到莫宁手上 —— 循环轴只能从她起跑。

        宏正常跑的时候这个函数根本不会被调到(宏直接按数字键切人, 绕开框架的选人),
        所以这里的 MUST 只在"切人失败/脱战重进"之后起作用。
        启动轴待跑时不抢: 那一段的入口是清宵, 她那边也返回 MUST。
        """
        if self.is_in_qing_cycle:
            macro = self.macro()
            if macro is not None and not macro.opener_pending():
                return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)
