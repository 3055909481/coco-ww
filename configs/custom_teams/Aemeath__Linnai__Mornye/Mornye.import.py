import time
from src.char.BaseChar import BaseChar, SwitchPriority

# 框架把 char_linnai / char_linnai2 认成同一个琳奈
LINNAI_NAMES = {'char_linnai', 'char_linnai2'}

class Mornye(BaseChar):
    # 升空(位势转换)第 22F 离地、48F 才进观测模式, 中间点普攻是空中下落攻击, 会把她带回地面
    RISE_WAIT = 1.0
    # 一圈的游戏时间刚好卡在 R 的 25s 冷却上, 快一点的圈到场时冷却还剩 0.1~0.2s。框架读到 <=0.2 就当好了,
    # 按一两下没出、转好那一帧图标又读成灭的, 就放弃不再按(10-29 01:45:44 那局 204.37 按、204.60 图标才亮)。
    # 接着连按到出, 盯的是这零点几秒, 上限给 1s, 再没出就是能量不够, 重击后再看一眼
    R_RETRY_CAP = 1.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_heavy = 0

    def do_perform(self):
        if self.has_intro:
            self.continues_normal_attack(1.33)
        # 本队处决交给爱弥斯。开着的话莫宁交棒时框架看到 F 提示会连按 F(最多 5s), 她在空中/声骸变身里按不出来,
        # 10-28 21:54:14 协奏满按完 Q 后白按 2.5s F 才切琳奈
        self.check_f_on_switch = False
        if not self.on_air():
            if self.combo_limit():
                self.logger.debug("perform quick actions")
                if self.click_echo():
                    return self.switch_next_char()
                elif self.click_resonance()[0]:
                    """地面奶一下，防止主C死亡"""
                    return self.switch_next_char()
                else:
                    self.continues_normal_attack(0.1)
                    return self.switch_next_char()
            self.not_on_air_actions()

        if self.on_air():
            self.on_air_actions()
        self.switch_next_char()

    def on_air(self):
        return self.has_long_action2()

    def on_air_actions(self):
        detect_ready = self.echo_available()
        self.logger.debug("on_air start attacking")
        start = time.time()

        if self.detect_elbow_strike(detect_ready):
            self.logger.debug("Detected an elbow strike, attempting to reset.")
            self.task.wait_until(lambda: not self.detect_elbow_strike(detect_ready), 
                                    post_action=lambda: self.continues_right_click(0.05), time_out=1.5)
        r_cast = self.cast_r()
        self.continues_normal_attack(1.8)
        self.click_resonance()
        self.logger.debug("mouse forte full, heavy attack")
        self.task.mouse_down()
        self.sleep(2)
        if not r_cast and self.liberation_available():
            self.task.mouse_up()
            r_cast = self.click_liberation()
            self.logger.info(f'mornye R late {"ok" if r_cast else "fail"}')
        # if self.heavy_click_forte(check_fun=lambda: self.is_mouse_forte_full() and not self.detect_elbow_strike(detect_ready)):
        #     if not self.task.wait_until(lambda: self.is_con_full(), time_out=1.5):
        #         self.logger.debug("not condition full, try clicking echo")
        #         self.click_echo(duration=0.2)
        #     self.last_heavy = time.time()
        #     self.check_f_on_switch = False
            # if self.on_air() and self.is_mouse_forte_full():
            #     self.logger.debug("mouse forte full, heavy attack")
            #     if self.heavy_click_forte(check_fun=lambda: self.is_mouse_forte_full() and not self.detect_elbow_strike(detect_ready)):
            #         if self.detect_elbow_strike(detect_ready):
            #             continue
            #         elif not self.task.wait_until(lambda: self.is_con_full(), time_out=1.5):
            #             self.logger.debug("not condition full, try clicking echo")
            #             self.click_echo(duration=0.2)
            #         self.last_heavy = time.time()
            #         self.check_f_on_switch = False
            #         break
        self.sleep(0.01)
        # 协奏满 = 协奏环 + 头像光圈互相确认, 判法在 Linnai.con_both
        judge = next((c for c in self.task.chars if c is not self and hasattr(c, 'con_both')), None)
        if judge:
            judge.con_both_reset()
            full = self.task.wait_until(lambda: judge.con_both(self), post_action=self.click)
        else:
            full = self.task.wait_until(lambda: self.is_con_full(), post_action=self.click)
        if full:
            # 读到过真满就记住。按完声骸切人时框架会再读一次, 环常被读成 0.99,
            # 当成没满就不切(10-28 21:30:39 实机: 13.73 读到 1, 13.84 切人读 0.99, 白站 4.3s)
            self.current_con = 1
            self.logger.info(f'mornye con full({getattr(judge, "con_why", "ring")})')
        self,self.click_echo()
        self.logger.debug("finished attacking on_air")

    def cast_r(self):
        # 任务设置里关了大招就不补
        if self.click_liberation() or not self.task.use_liberation:
            return True
        start = time.time()
        while time.time() - start < self.R_RETRY_CAP:
            if self.liberation_available() and self.click_liberation():
                self.logger.info(f'mornye R retry ok {time.time() - start:.2f}s')
                return True
            self.task.next_frame()
        self.logger.info('mornye R retry CAP')
        return False

    def not_on_air_actions(self):
        self.logger.debug("not on_air start attacking")
        start = time.time()
        try_douge = True
        while time.time() - start < 10 and not self.on_air():
            # 地面不放 E: 起手只要 A 攒满回路升空, E 留到空中(10-28 开局地面 E 连按 3s, 刷怪后又放了两次)
            self.click()
            self.check_combat()
            self.sleep(0.1)
            if self.is_mouse_forte_full():
                # if try_douge:
                #     self.task.click(key="right")
                #     try_douge = False
                self.heavy_attack()
                # 升空途中别点 A, 等进了空中再往下走; 没飞起来(重击图标误读)才接着 A
                self.task.wait_until(self.on_air, time_out=self.RISE_WAIT)

    def on_combat_end(self, chars):
        self.switch_other_char()

    def combo_limit(self):
        return self.time_elapsed_accounting_for_freeze(self.last_heavy) < 23

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if has_intro and current_char and current_char.char_name in {'char_aemeath'}:
            return SwitchPriority.MUST
        from src.char.Linnai import Linnai
        if has_intro and current_char and self.task.has_char(Linnai) and current_char.char_name not in LINNAI_NAMES:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)
        
    def detect_elbow_strike(self, ready):
        return ready and not self.available('echo', check_color=True)
