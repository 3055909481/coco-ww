import time

import cv2
import numpy as np

from src.char.BaseChar import BaseChar, SwitchPriority, forte_white_color


def glow_slot(frame, slot):
    """队伍栏第 slot 格(0 起)头像外面那圈细白环 -> 落在同一半径上的上半圈角度占比(0~1)。照抄心锁守 Hsin.glow_slot。"""
    H, W = frame.shape[:2]
    s = H / 2160
    cx = W - (3840 - 3586.5) * s
    cy = (500 + 264 * slot) * s
    R = int(round(125 * s)) + 3
    xa, ya = int(cx) - R, int(cy) - R
    if xa < 0 or ya < 0 or xa + 2 * R + 1 > W or ya + 2 * R + 1 > H:
        return 0.0
    crop = frame[ya:ya + 2 * R + 1, xa:xa + 2 * R + 1, :3]
    white = np.ascontiguousarray(crop.min(axis=2)).astype(np.float32)
    polar = cv2.warpPolar(white, (R, 360), (cx - xa, cy - ya), R, cv2.WARP_POLAR_LINEAR)
    rows = polar[225:331]
    d = max(1, int(round(4.5 * s)))
    r = np.arange(int(80 * s), int(118 * s))
    ridge = rows[:, r] - np.maximum(rows[:, r - d], rows[:, r + d])
    k = ridge.argmax(axis=1)
    hit = ridge[np.arange(len(rows)), k] > 40
    if not hit.any():
        return 0.0
    hist = np.bincount(r[k][hit], minlength=int(r[-1]) + 2)
    win = max(2, int(round(4 * s)))
    return float(np.convolve(hist, np.ones(win), 'valid').max()) / len(rows)


class Linnai(BaseChar):
    RES_CHECK_THRESHOLD = 0.6
    # 加速状态的目标框会被特效遮一两帧, 读一次没读到就判失败会直接切走
    ACCEL_WAIT = 1.0
    # 切爱弥斯前补协奏的上限; 补不满照样切, 别卡死在琳奈身上
    CON_GATE_CAP = 3.0
    # 变奏入场 E: 每 0.05s 按一次, E 位冷却读到 3~6.5(琳奈式创想 6s)算放出, 上限 1.2s 照切
    INTRO_E_INTERVAL = 0.05
    INTRO_E_CD = (3.0, 6.5)
    INTRO_E_CAP = 1.2
    # 【协奏满 = 协奏环 + 头像光圈 互相确认】协奏满(切人能变奏)那一帧, 队伍栏另外两个人的头像外面套上一圈细白环。
    # 只看环两头都会错: 快满时环先判闭合报 1(心锁守比真满早 1.8s), 特效压着环又报 0.99 不认(10-28 莫宁白站 4.3s)。
    # 判满 = 另外两格光圈连 CON_BOTH_FRAMES 帧 且 环在最近 RING_WINDOW 内读到过 >= 0.99(含 0.99 哨兵)。莫宁那一棒也借这里判。
    # 不要求同一帧: 莫宁空中段环读数常掉到 0, 10-12 录屏 06.09.52 84.50 光圈亮的两帧环都读 0。
    # 光圈阈值同心锁守。爱琳莫 10-12 录屏 2K+1080: 真满那帧两格 0.5~0.9, 队伍栏在时平常 <= 0.25; 没认到队伍栏的帧会误读到 0.7, 不算。
    GLOW_BOTH = 0.15             # 两格都 >= 这个, 而且有一格 >= GLOW_ONE
    GLOW_ONE = 0.35
    GLOW_SOLO = 0.60             # 一格就够
    CON_BOTH_FRAMES = 2
    RING_WINDOW = 1.0
    # 这次开着 ok-ww 还一次都没对上过光圈(分辨率/UI 缩放认不出): 环读到 1 再等这么久还没光圈就只信环, 别卡死
    GLOW_GRACE = 0.5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_heavy = 0

        self.is_sending_e = False


    @property
    def is_in_25s_cycle(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Aemeath', 'Mornye'}:
                team_members += 1
        return team_members == 2
    

    def do_perform(self):
        if self.is_in_25s_cycle:
            self.check_f_on_switch = False
            self.perform_25s_cycle()
        else:
            if self.has_intro:
                if self.check_res():
                    self.continues_normal_attack(1.33)
                else:
                    self.continues_normal_attack(1)
                    self.click_echo(time_out=0)
                    if not self.is_con_full():
                        self.click_liberation()
                    if not self.is_mouse_forte_full():
                        self.click_resonance()   
                    self.task.wait_until(self.is_mouse_forte_full, post_action=self.click, time_out=2)
                    self.task.mouse_down() 
                    if self.task.wait_until(lambda: not self.is_mouse_forte_full(), time_out=5):
                        self.task.mouse_up()
                        self.sleep(0.4)                 
                        self.perform_under_intro()
                    else:
                        self.task.mouse_up()
                    
            else: 
                self.click_echo(time_out=0)
                if self.perform_under_intro():
                    pass
                elif self.flying():
                    self.continues_normal_attack(0.1)
                elif not self.is_con_full() and self.click_liberation():
                    self.continues_normal_attack(0.5)
                self.click_resonance()
            return self.switch_next_char()
    
    def perform_25s_cycle(self):
        if self.check_outro() in {'char_moning', 'char_moning_new'}:
            # 变奏入场
            self.click_liberation()
            # 原来 R 演完固定等 0.3s 再按 E; 现在连按到进冷却, 不用等
            self.send_intro_e()
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
            # 大招演出刚结束重击图标还没刷出来, 不先等它亮, 下面的"图标灭了"一按下就成立, 重击根本没蓄
            self.task.wait_until(self.is_mouse_forte_full, post_action=self.click, time_out=2)
            self.task.mouse_down()
            if self.task.wait_until(lambda: not self.is_mouse_forte_full(), time_out=5):
                self.task.mouse_up()
                self.sleep(0.4)
                if not send_lib:
                    send_lib = self.click_liberation()
                self.perform_under_intro()
                if not send_lib:
                    send_lib = self.click_liberation()
            else:
                self.task.mouse_up()
            self.fill_con_before_switch()
            self.switch_next_char()

    def send_intro_e(self):
        """变奏入场动作会吞按键, 原来 0.01s 连按 3 下等于只按一下: 多数圈被吞, 回来 e回切 再补;
        偶尔放出去了, e回切 就跳过 E 直接 R, 两种圈节奏不一样。改成连按到 E 进冷却。
        冷却读数限在 INTRO_E_CD 区间: 刚切进来那几帧技能栏可能还是莫宁的(她的 E 冷却 16s)。"""
        start = time.time()
        n = 0
        while time.time() - start < self.INTRO_E_CAP:
            self.send_resonance_key()
            n += 1
            self.sleep(self.INTRO_E_INTERVAL)
            self.task.next_frame()
            if self.task.in_team()[0]:
                cd = self.task.get_cd('resonance')
                if self.INTRO_E_CD[0] <= cd <= self.INTRO_E_CD[1]:
                    self.logger.info(f'linnai intro E ok x{n} {time.time() - start:.2f}s cd {cd:.1f}')
                    self.sleep(0.03)
                    return True
        self.logger.info(f'linnai intro E CAP x{n} {time.time() - start:.2f}s')
        return False

    def fill_con_before_switch(self):
        """这一棒交给爱弥斯放 R1, 协奏不满她就不是变奏入场, 整圈 R1/R2 都丢。
        10-24 19:49:54 实机: 加速状态读一帧失败 -> 协奏 0.38 就切走, 爱弥斯只平 A 了 2s。"""
        start = time.time()
        clicks = 0
        if self.current_con == 1:
            return True
        self.con_both_reset()
        self.task.next_frame()
        con = self.task.get_current_con()
        if self.con_both(self, need=1):
            # 记住满了: 切人时框架会再读一次, 环常被特效读成 0.99, 当成没满就不是变奏
            self.current_con = 1
            return True
        while time.time() - start < self.CON_GATE_CAP:
            if self.resonance_available():
                self.click_resonance()
            else:
                self.task.mouse_down()
                self.sleep(0.05)
                self.task.mouse_up()
                clicks += 1
            self.sleep(0.08)
            self.task.next_frame()
            if self.con_both(self):
                self.current_con = 1
                self.logger.info(f'linnai con gate full({self.con_why}) {con:.2f}->1 A x{clicks} '
                                 f'{time.time() - start:.2f}s')
                return True
        self.logger.info(f'linnai con gate CAP {con:.2f}->{self.task.get_current_con():.2f} '
                         f'glow {self.glow_lo:.2f}/{self.glow_hi:.2f} A x{clicks}')
        return False

    def glow_now(self, cur):
        """当前帧 cur 以外两格的光圈够不够"协奏满"。同一帧只算一次。"""
        frame = self.task.frame
        if frame is None:
            return False
        if getattr(self, '_glow_frame', None) is frame and getattr(self, '_glow_cur', None) == cur.index:
            return self.glow_on
        if self.task.in_team()[0]:
            others = [glow_slot(frame, i) for i in range(3) if i != cur.index]
        else:
            others = [0.0, 0.0]
        self.glow_lo, self.glow_hi = min(others), max(others)
        self.glow_on = ((self.glow_lo >= self.GLOW_BOTH and self.glow_hi >= self.GLOW_ONE)
                        or self.glow_hi >= self.GLOW_SOLO)
        self._glow_frame, self._glow_cur = frame, cur.index
        return self.glow_on

    def con_both_reset(self):
        """每次开始等协奏前调: 清掉连续帧计数和"环读到 1 的起点"。"""
        self._glow_hits = 0
        self._ring_hi_at = 0
        self._ring_full_at = 0
        self.con_why = None
        self.glow_lo = self.glow_hi = 0.0

    def con_both(self, cur, need=None):
        """cur 的协奏满没满: 另外两格光圈连 need 帧(默认 CON_BOTH_FRAMES) 且环最近 RING_WINDOW 内读到过 >= 0.99。
        读当前帧, 调用方负责 next_frame。这次开着 ok-ww 没对上过光圈时, 环一直读 1 满 GLOW_GRACE 也算(con_why='ring-only')。"""
        need = need or self.CON_BOTH_FRAMES
        now = time.time()
        ring = self.task.get_current_con()
        if ring >= 0.99:
            self._ring_hi_at = now
        glow = self.glow_now(cur)
        self._glow_hits = self._glow_hits + 1 if glow else 0
        if self._glow_hits >= need and self._ring_hi_at and now - self._ring_hi_at <= self.RING_WINDOW:
            setattr(self.task, 'lmr_glow_seen', True)
            self.con_why = (f'both ring{ring:.2f}@-{now - self._ring_hi_at:.2f}s '
                            f'glow{self.glow_lo:.2f}/{self.glow_hi:.2f}')
            return True
        # 环读数会掉帧到 0, 所以从第一次读到 1 算起, 不要求一直是 1
        if ring == 1 and not self._ring_full_at:
            self._ring_full_at = now
        if (self._ring_full_at and not getattr(self.task, 'lmr_glow_seen', False)
                and now - self._ring_full_at >= self.GLOW_GRACE and now - self._ring_hi_at <= self.RING_WINDOW):
            self.con_why = f'ring-only glow{self.glow_lo:.2f}/{self.glow_hi:.2f}'
            return True
        return False
            

            
    def perform_under_intro(self):
        if not self.check_res() and not self.task.wait_until(self.check_res, post_action=self.click_with_interval,
                                                             time_out=self.ACCEL_WAIT):
            self.logger.info(f'Linnai fails entering accelerate mode!')
            return False
        self.task.wait_until(lambda: self.is_color_full() or self.is_con_full(), post_action=self.click,
                                     time_out=1)
        if self.task.wait_until(lambda: not self.is_forte_full(),
             post_action=self.task.jump, time_out=3):
            self.continues_normal_attack(1.6)
            self.click_liberation()
            self.con_both_reset()
            self.task.next_frame()
            if not self.con_both(self, need=1):
                self.click_resonance()
                self.con_both_reset()
                if self.task.wait_until(lambda: self.con_both(self), post_action=self.click):
                    self.current_con = 1
                    self.logger.info(f'linnai con full({self.con_why})')
            else:
                self.current_con = 1
            # if self.task.wait_until(lambda: self.click_resonance()[0],
            #  post_action=self.click, time_out=2):
                # self.wait_after_resonance_kick()
                # second_kick = False

                # def click_second_resonance():
                #     nonlocal second_kick
                #     if self.is_con_full():
                #         return True
                #     second_kick = self.click_resonance()[0]
                #     return second_kick

                # self.task.wait_until(click_second_resonance, post_action=self.click, time_out=3)
                # if second_kick:
                #     self.wait_after_resonance_kick()
        # if not self.is_con_full() and self.click_liberation():
        #     self.task.wait_until(self.is_con_full, post_action=self.click_with_interval, time_out=1.2) 
        return True

    def wait_after_resonance_kick(self):
        self.sleep(0.3)
        self.wait_down()

    def find_target_status_in_box(self, box_name):
        try:
            return self.task.find_best_match_in_box(self.task.get_box_by_name(box_name),
                                                    ['has_target', 'no_target'],
                                                    threshold=self.RES_CHECK_THRESHOLD)
        except Exception as e:
            self.logger.debug(f'Linnai check res skipped {box_name}: {e}')
            return None

    def check_res(self):
        # 同原版琳奈: 长框读不到再看另外两个框
        if not self.task.in_team_and_world():
            return False
        best = self.find_target_status_in_box('target_box_long2')
        if not best:
            best = self.find_target_status_in_box('box_target_enemy_long')
        if not best:
            try:
                best = self.task.find_one('target_box_short', threshold=self.RES_CHECK_THRESHOLD)
            except Exception as e:
                self.logger.debug(f'Linnai check res skipped target_box_short: {e}')
                best = None
        self.logger.debug(f'check res {best}')
        return best

    def is_color_full(self):
        box = self.task.box_of_screen_scaled(5120, 2880, 2846, 2602, 2889, 2690, name='color_full', hcenter=True)
        white_percent = self.task.calculate_color_percentage(forte_white_color, box)
        self.logger.debug(f'forte_color_percent {white_percent}')
        return white_percent > 0.06

    def on_combat_end(self, chars):
        self.switch_other_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if self.is_in_25s_cycle:
            if self.is_sending_e or (has_intro and current_char and current_char.char_name in {'char_moning', 'char_moning_new'}):
                return SwitchPriority.MUST
            else:
                return SwitchPriority.NO
        elif has_intro and current_char and current_char.char_name in {'char_moning', 'char_moning_new'}:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)
