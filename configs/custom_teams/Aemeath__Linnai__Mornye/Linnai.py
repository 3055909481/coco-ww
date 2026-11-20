import time

from src.char.BaseChar import BaseChar, SwitchPriority
from src.char.Linnai import Linnai as NativeLinnai

# char_name 可能是这两个值中的任何一个: CharFactory.py:120 把 char_linnai 和
# char_linnai2 映射到同一个类。只认前者的话换个立绘整段轴就静默跳过
LINNAI_NAMES = {'char_linnai', 'char_linnai2'}
# 同理 CharFactory.py:122
MORNYE_NAMES = {'char_moning', 'char_moning_new'}


class Linnai(NativeLinnai):
    """爱琳莫队里的琳奈。非本队时整套走原版。

    只覆盖三个方法, 其余(check_res 的三重兜底、wait_for_accelerate_ready、
    charge_heavy、is_color_full)全部继承上游 —— 之前这份是 8-03 从原版拷出来的,
    上游后来加的兜底一条都没吃到。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_sending_e = False

    @property
    def is_in_25s_cycle(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Aemeath', 'Mornye'}:
                team_members += 1
        return team_members == 2

    def opener_macro(self):
        """拿到持宏的那个角色实例(莫宁)。

        启动轴是一条跨三个角色的线性宏, 只能放在一个文件里 —— 自定义角色文件由
        CustomCharLoader 按路径单独加载, 互相 import 不到, 但同一个 task 下用
        task.chars 拿得到彼此的实例。这是跨文件复用宏原语的唯一路子。

        Returns: 启动轴该跑而且还没跑的时候返回莫宁, 否则 None。
        """
        for char in self.task.chars:
            if char is not None and char.name == 'Mornye' and hasattr(char, 'perform_opener'):
                # 【只有第四版轴由琳奈开场】: V3 的第一个动作是莫宁的 A, 那一版由
                # 莫宁自己的 do_perform 驱动, 琳奈在这里必须放手 —— 否则两个入口
                # 都想开场, 白多一次切人
                if getattr(char, 'OPENER_VERSION', 4) != 4:
                    return None
                blocked = char.opener_blocked_by()
                if blocked is None:
                    return char
                # 【这里不出声就查不下去】: 琳奈放弃驱动之后会掉进兜底套路然后切人,
                # 于是启动轴改由后面上场的那个角色触发, 白多两次切人 —— 而日志上
                # 一点痕迹都没有。2026-08-13 排查"愣一下->切莫宁->切琳奈"就卡在这
                self.logger.info(f'linnai: not driving the opener ({blocked})')
                return None
        return None

    def do_perform(self):
        if not self.is_in_25s_cycle:
            self.is_sending_e = False
            return super().do_perform()
        self.check_f_on_switch = False
        # 【第四版启动轴由琳奈开场】(用户 2026-08-13 的图: 第一个动作是她的 E),
        # 所以入口挪到这里 —— 上一版是莫宁 MUST 抢先上场再切给琳奈, 白花一次切人。
        # 宏体仍然在 Mornye.py, 按键是全局的, 谁在场就是谁在打, 持宏的是谁无所谓
        macro = self.opener_macro()
        if macro is not None:
            macro.opener_armed = False
            macro.last_opener = time.time()
            # 【把自己传进去】: 框架是因为琳奈在场才调的这个 do_perform, 所以这就是
            # "现在场上是谁"的答案。宏据此跳过开场那次切人 —— 直接按 E。
            # 【不能让它去读屏判断】: in_team() 在战斗开局的头 0.8 秒会说谎
            # (HUD 还在渐入), 实机三轮都把在场的琳奈读成莫宁, 于是白白"换琳奈上场"
            if macro.perform_opener(self):
                return
            self.logger.warning('opener aborted, falling back to the native rotation')
        elif self.yield_to_mornye_opener():
            return
        return self.perform_25s_cycle()

    def yield_to_mornye_opener(self):
        """V2/V3/V5 启动轴第一格是莫宁的 A。启动轴待跑而琳奈在场时直接把场子交给莫宁。

        开关过自动战斗之后谁在场都有可能。走 perform_25s_cycle 会先把琳奈的 E/R 打掉,
        启动轴里那两下就没得用; 走 switch_next_char 的话爱弥斯见琳奈在场也报 MUST,
        可能先切给她再绕到莫宁。切人失败返回 False, 照旧走 perform_25s_cycle。
        """
        for char in self.task.chars:
            if char is not None and char.name == 'Mornye' and hasattr(char, 'quick_switch'):
                if getattr(char, 'OPENER_VERSION', 4) == 4 or not char.opener_pending():
                    return False
                self.logger.info('linnai: opener pending, handing the field to mornye')
                char.cur = self
                return char.quick_switch(char)
        return False

    def perform_25s_cycle(self):
        if self.check_outro() in MORNYE_NAMES:
            # 变奏入场
            self.click_liberation()
            self.sleep(0.3)
            for i in range(3):
                self.send_resonance_key()
                self.sleep(0.01)
            self.is_sending_e = True

            def post_action(switch_to: BaseChar, has_intro: bool):
                switch_to.continues_normal_attack(1.4)
                switch_to.switch_next_char()

            self.switch_next_char(post_action=post_action)
        else:
            # e回切
            self.is_sending_e = False
            if self.resonance_available():
                self.click_resonance()
                self.sleep(1)
            self.click_echo(time_out=0)
            send_lib = self.click_liberation()
            # 长按必须放 finally: wait_until 里的战斗判定会抛异常, 抛出去时
            # 鼠标还按着, 下一个角色上场就是一直按住普攻
            self.task.mouse_down()
            try:
                drained = self.task.wait_until(lambda: not self.is_mouse_forte_full(), time_out=5)
            finally:
                self.task.mouse_up()
            if drained:
                self.sleep(0.4)
                if not send_lib:
                    send_lib = self.click_liberation()
                self.perform_under_intro()
                if not send_lib:
                    send_lib = self.click_liberation()
            self.switch_next_char()

    def perform_under_intro(self):
        """替换原版那段"共鸣踢 x2"。原版写法留在 git 里, 这里是实战调过的版本。"""
        if not self.wait_for_accelerate_ready():
            self.logger.debug('Linnai fails entering accelerate mode!')
            return False
        self.task.wait_until(lambda: self.is_color_full() or self.is_con_full(),
                             post_action=self.click, time_out=1)
        if self.task.wait_until(lambda: not self.is_forte_full(),
                                post_action=self.task.jump, time_out=3):
            self.continues_normal_attack(1.6)
            self.click_liberation()
            if not self.is_con_full():
                self.click_resonance()
                self.task.wait_until(self.is_con_full, post_action=self.click)
        return True

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if self.is_in_25s_cycle:
            # 启动轴还没跑 -> 场子必须先交给琳奈, 轴的第一个动作是她的 E。
            # 【至少要留一个 MUST 候选】: 全队都是 NO 的话框架只会打一下普攻原地打转
            if self.opener_macro() is not None:
                return SwitchPriority.MUST
            if self.is_sending_e or (has_intro and current_char
                                     and current_char.char_name in MORNYE_NAMES):
                return SwitchPriority.MUST
            return SwitchPriority.NO
        if has_intro and current_char and current_char.char_name in MORNYE_NAMES:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)
