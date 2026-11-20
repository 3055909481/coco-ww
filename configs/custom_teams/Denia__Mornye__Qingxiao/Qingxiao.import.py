import time

from src.char.BaseChar import SwitchPriority
from src.char.Qingxiao import Qingxiao as NativeQingxiao

# char_name 可能是这两个值中的任何一个: CharFactory.py:122 把 char_moning 和
# char_moning_new 映射到同一个类。只认前者的话换个立绘整段轴就静默跳过
MORNYE_NAMES = {'char_moning', 'char_moning_new'}


class Qingxiao(NativeQingxiao):
    """清达莫队(清宵/达妮娅/莫宁)里的清宵。非本队时整套走原版。

    【她是启动轴的入口】: 轴的第一个动作是她的"跳A+E"(5R 轴, 2026-09-06 起; 上一版是她的 R)。宏体在同目录的 Denia.py 里 ——
    自定义角色文件由 CustomCharLoader 按路径单独加载, 互相 import 不到, 但同一个
    task 下用 task.chars 拿得到彼此的实例, 这是跨文件复用宏原语的唯一路子。

    循环轴不由她驱动(那一段由莫宁起跑), 所以宏跑完之后她走到 do_perform 就说明
    宏散了 —— 打一小段把场子交出去, 让莫宁重新拿到入口。

    【原版的 do_perform / handle_heavy / cast_enhanced_resonance 一行没动】:
    宏调的就是原版的 heavy_available() / handle_heavy() / enhanced_resonance_available(),
    上游以后改了这几个函数, 这条轴自动跟着走。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 【自定义 __init__ 里跟内置版取值不同的字段会被 CharFactory 打回内置值】
        # (apply_team_char_classes 里 replacement.__dict__.update(char.__dict__))。
        # 这里新加的字段内置版没有, 不受影响; 真要改内置字段就得在 reset_state 里重申
        self.macro_yield_logged = 0.0

    @property
    def is_in_qing_cycle(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Denia', 'Mornye'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """拿到持宏的达妮娅实例。

        【按能力找, 不按名字找】: 达妮娅那份自定义没启用的话这里就该返回 None,
        然后老老实实走原版轮换 —— 而不是拿着一个没有 perform_opener 的实例崩掉。
        """
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_opener') and hasattr(char, 'quick_switch'):
                return char
        return None

    def do_perform(self):
        if not self.is_in_qing_cycle:
            return super().do_perform()
        # 处决全程由宏自己按 —— 留着框架的自动 F 会在切人时插一发, 而击破动画带
        # 全局时停 + 队伍 HUD 消失, 正好把切人循环判成脱战
        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            self.logger.warning('qing cycle: Denia custom code is off, '
                                'falling back to the native rotation')
            return super().do_perform()

        blocked = macro.opener_blocked_by()
        if blocked is None:
            # 【标记要在跑之前落】: perform_opener 中途抛异常(脱战/任务被停)也不能
            # 让启动轴重跑 —— reset_state 会把 armed 重新置回来
            macro.opener_armed = False
            macro.last_opener = time.time()
            # 【把自己传进去】: 框架是因为清宵在场才调的这个 do_perform, 所以这就是
            # "现在场上是谁"最可靠的答案。宏据此跳过开场那次切人, 直接起跳。
            # 【不能让它去读屏判断】: in_team() 在战斗开局的头 0.8 秒会说谎
            # (队伍 HUD 还在渐入, 三个槽位的文本不是同时出现的)
            if macro.perform_opener(self):
                return
            self.logger.warning('qing opener aborted, falling back to the native rotation')
            return super().do_perform()

        # 启动轴跑过了: 循环轴由莫宁驱动。走到这里说明宏散了(切人失败/脱战重进),
        # 打一小段把场子交给她重新起跑。
        # 【不要在这里跑循环轴】: 循环轴的第一步假设莫宁刚变奏入场, 从清宵这里起跑一定错位
        now = time.time()
        if now - self.macro_yield_logged > 5:
            self.macro_yield_logged = now
            self.logger.info(f'qingxiao: opener not pending ({blocked}), handing the field back')
        self.continues_normal_attack(0.6)
        return self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时清宵【必须】先上场 —— 轴的第一个动作是她的"跳A+E"。

        【至少要留一个 MUST 候选】: 全队都是 NO 的话框架只会打一下普攻原地打转。
        本队里达妮娅在启动轴待跑时返回 NO, 所以这个 MUST 是必需的。
        """
        if self.is_in_qing_cycle:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)
