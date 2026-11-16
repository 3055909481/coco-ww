import threading
import time

from src.char.BaseChar import BaseChar, SwitchPriority

# 队伍 HUD 右下角图标, 模板是框架自带的:
#   hiyuki_right = 闪避键上的披风人: 寒意 >= 100, 闪避会进居合架势
#   hiyuki_left  = 左键变成的居合图标: 已经在架势里
# 视频 1080p 实测(同一套模板, TM_CCOEFF_NORMED): 亮 0.87~0.93, 不亮 0.30~0.59。
# 内置代码用的 0.5 会把"不亮"当成亮。
ICON_THRESHOLD = 0.75
ICON_VARIANCE = 0.01
# 锁定图标(has_target/no_target)在两种形态下位置不同, 用它分常世身/预求身:
#   常世身 box_target_enemy_long 0.84~0.90, 预求身 target_box_long2 0.83~0.90, 另一个框 <0.45
LOCK_THRESHOLD = 0.70

ENABLE_POLL_INTERVAL = 0.2


def start_enable_watch(task, logger=None):
    """盯 task.enabled 的 False->True 跳变, 把时刻记到 task._ww_task_restarted_at。

    daemon 线程, 跟着进程退出。每个 task 只启一次。
    """
    if getattr(task, '_ww_enable_watch_started', False):
        return
    task._ww_enable_watch_started = True
    if not hasattr(task, '_ww_task_restarted_at'):
        task._ww_task_restarted_at = -1

    def watch():
        was_on = None
        while True:
            try:
                now_on = bool(getattr(task, 'enabled', False))
                if was_on is False and now_on:
                    task._ww_task_restarted_at = time.time()
                    if logger:
                        logger.info('auto combat re-enabled -> opener re-armed')
                was_on = now_on
                time.sleep(ENABLE_POLL_INTERVAL)
            except Exception:
                return

    threading.Thread(target=watch, name='ok_ww_enable_watch', daemon=True).start()


class Hiyuki(BaseChar):
    """绯雪单人刷声骸。队友釉瑚/炽霞只凑人数, 从不切人, 也不放 R2。

    每一场战斗(每一只 boss):
      没变身 -> 变身: 3A -> E -> 声骸 -> 按住左键(连段攒满心念, 派生重击解锁大招) -> R
      起手:   3A -> E(升空) -> E 长按(下落落地) -> 居合链
      循环:   点 A; 闪避键一变成披风(寒意 >= 100)就走居合链; 3A 打完还没有就闪避取消后摇再来一轮
    居合链: 闪避进架势 -> 架势里点左键(缓存到派生帧出刀) -> 出刀后马上闪避进下一个架势,
            寒意用完为止, 最后一刀之后再闪避一次收刀。
    不放 R2, 预求身一直保留: 大世界 boss 原地刷新后直接从起手开始; 副本重进后是常世身, 先变身。

    怪一死就停手: 每一步之间都过一次 check_combat()。框架判出战斗结束会抛异常,
    整段当场打断(没有长段不查的定时宏); 按住的键都在 finally 里松开。
    """

    # ---- 普攻 ----
    # 3A 从第一下点击到闪避。旧队伍实机标定: 1.10 太早(A3 特效没出), 1.20~1.35 都出
    A_CHAIN = 1.25
    A_INTERVAL = 0.15
    # task.click() 按下抬起之间一帧都没有, 必须打出来的普攻自己按住
    A_TAP = 0.03
    DODGE_TAP = 0.04
    # 普通闪避(取消 A3 后摇)之后到下一下 A
    DODGE_RECOVER = 0.35
    # 循环里多久过一次 check_combat(): 怪死后最多多点这么久
    CHECK_EVERY = 0.40

    # ---- 起手 E E ----
    # 预求地面E 第20F转空中、派生35F; 这之后按第二个 E 才是空中E
    E1_TO_E2 = 0.45
    # 第二个 E 长按到落地(空中E 下落 16F 起判定、持续 30F, 落地派生 6F)
    E2_HOLD = 0.75
    E2_AFTER = 0.10

    # ---- 居合链 ----
    # 视频两段 6 刀实测: 闪避 -> 出刀 0.50~0.57s(架势派生帧 30F), 出刀 -> 下一次闪避 0.13~0.17s
    # 架势里左键会缓存到派生帧才出刀, 这几下都点在出刀之前
    IAI_TAPS = (0.10, 0.25, 0.40)
    # 视频里闪避后 0.09s 左键就变成居合图标; 超过这个还没变 = 没进架势(按键被吞/寒意不够)
    IAI_STANCE_TIMEOUT = 0.35
    # 早于这个时刻的图标变化不算出刀(闪避后 0.2s 左右居合图标会闪一下)
    IAI_CAST_EARLIEST = 0.42
    # 没读到出刀信号时补点左键的起点和间隔(万一架势不吃缓存)
    IAI_RETAP_FROM = 0.62
    IAI_RETAP_INTERVAL = 0.12
    # 读不到出刀信号就按这个时刻当出刀
    IAI_CAST_TIMEOUT = 0.95
    # 出刀信号 -> 下一次闪避。早于出刀的闪避会把架势闪掉(白费一轮, 寒意不丢)
    IAI_NEXT_DODGE = 0.10
    IAI_CYCLE_MIN = 0.62
    IAI_MAX = 6
    IAI_STANCE_RETRY = 2
    # 最后一刀之后闪避收刀再接 A(视频"居合闪随后再接平a")
    IAI_END_DODGE = True

    # ---- 变身(常世身) ----
    T_A_CHAIN = 1.30  # 常世身 3A: A3 起手就给 100 心念
    T_E_TO_Q = 0.35
    T_Q_TO_HOLD = 0.35
    # 按住左键: E 给的 A3 BUFF 让下一个 A3 给 200, 凑满 300 -> 派生重击 -> 箭矢3 解锁大招
    T_HOLD_TIMEOUT = 8.0
    T_TARGET_LOST_GIVE_UP = 1.0
    TRANSFORM_TRIES = 3
    # R: 按到 HUD 掉(演出开始)为止; 帧表 大招拔刀 隐藏HUD 240F
    LIB_HUD_UP_WAIT = 1.5
    LIB_FIRE_TIMEOUT = 2.5
    LIB_TAP_INTERVAL = 0.10
    HUD_OFF_CONFIRM = 2
    LIB_ANIM_MAX = 6.0
    LIB_SETTLE = 0.25

    # ---- 处决 ----
    USE_EXECUTE = True
    EXECUTE_HUD_OFF_WAIT = 0.8
    EXECUTE_ANIM_MAX = 4.0
    EXECUTE_SETTLE = 0.20

    # ---- 入口 ----
    # 结算画面上框架还会调 do_perform: 队伍 HUD 不在就什么都不做
    ENTRY_HUD_WAIT = 1.5
    FORM_READ_WAIT = 1.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.check_f_on_switch = False
        self._fight_id = None
        self._opener_at = -1.0
        self._last_form = None
        self.macro_t0 = 0.0
        self.macro_marks = []
        start_enable_watch(self.task, self.logger)

    def reset_state(self):
        super().reset_state()
        # 内置实例 __dict__.update 会把 __init__ 里改过的值打回去, 这里重申
        self.check_f_on_switch = False
        start_enable_watch(self.task, self.logger)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        # 队友万一被游戏换上场(绯雪倒了), 凑数的那两个会切回来找她
        return SwitchPriority.MUST

    # ================================================================ 主流程

    def do_perform(self):
        if not self.wait_hud(self.ENTRY_HUD_WAIT):
            self.logger.info('hiyuki solo: no team HUD on entry, idle')
            self.mwait(0.2)
            return
        self.macro_start()
        try:
            fight = self.task.combat_start
            restarted = getattr(self.task, '_ww_task_restarted_at', -1)
            new_fight = fight != self._fight_id or restarted > self._opener_at
            self._fight_id = fight

            form = self.read_form_wait()
            self.mark(f'form={form}')
            if form != 'yq':
                if not self.transform_until_done():
                    return
                new_fight = True
            if new_fight:
                self._opener_at = time.time()
                self.opener()
            self.combat_loop()
        finally:
            self.logger.info(f'hiyuki solo timeline: {self.timeline()}')

    def opener(self):
        """3A 攒寒意 -> E 升空 -> E 长按落地 -> 居合链。"""
        self.mark('opener')
        self.attack_chain(self.A_CHAIN)
        self.alive()
        e = self.get_resonance_key()
        self.task.send_key(e, down_time=self.A_TAP)
        self.record_resonance_use()
        self.mark('E1')
        self.mwait(self.E1_TO_E2)
        self.task.next_frame()
        self.quick_alive()
        self.hold_key(e, self.E2_HOLD)
        self.mark('E2')
        self.mwait(self.E2_AFTER)
        self.alive()
        self.iai_chain()
        self.alive()

    def combat_loop(self):
        """该 A 就 A, 有居合就居合。只会被战斗结束的异常打断。"""
        while True:
            iai = self.attack_until_iai(self.A_CHAIN)
            if not iai:
                # 寒意恰好在最后一下攒满的话, 这一下闪避会进架势, 得接居合
                self.task.next_frame()
                iai = self.stance_ready()
            if iai:
                self.iai_chain()
            else:
                self.tap_right()
                self.mark('dodge')
                self.mwait(self.DODGE_RECOVER)
            self.alive()
            self.maybe_execute()

    # ================================================================ 普攻 / 闪避

    def attack_chain(self, duration):
        """固定时长连点普攻(起手/变身用, 中途不看居合)。"""
        self.task.next_frame()
        self.quick_alive()
        start = time.time()
        last_check = start
        while time.time() - start < duration:
            self.tap_left()
            self.mwait(max(0.0, self.A_INTERVAL - self.A_TAP))
            self.task.next_frame()
            self.quick_alive()
            if time.time() - last_check >= self.CHECK_EVERY:
                self.alive()
                last_check = time.time()
        self.mark(f'A{duration:.2f}')

    def attack_until_iai(self, duration):
        """连点普攻最多 duration 秒; 闪避键一变成披风就返回 True(A1~A3 都能跳架势)。"""
        self.task.next_frame()
        self.quick_alive()
        start = time.time()
        last_check = start
        while time.time() - start < duration:
            self.tap_left()
            self.mwait(max(0.0, self.A_INTERVAL - self.A_TAP))
            self.task.next_frame()
            self.quick_alive()
            if self.stance_ready():
                self.mark(f'A->iai@{time.time() - start:.2f}')
                return True
            if time.time() - last_check >= self.CHECK_EVERY:
                self.alive()
                last_check = time.time()
        self.mark('3A')
        return False

    def tap_left(self):
        self.task.mouse_down()
        try:
            time.sleep(self.A_TAP)
        finally:
            self.task.mouse_up()

    def tap_right(self):
        self.task.mouse_down(key='right')
        try:
            time.sleep(self.DODGE_TAP)
        finally:
            self.task.mouse_up(key='right')

    def hold_key(self, key, sec):
        self.task.send_key_down(key)
        try:
            self.mwait(sec)
        finally:
            self.task.send_key_up(key)

    # ================================================================ 居合

    def stance_ready(self):
        return bool(self.task.find_one('hiyuki_right', threshold=ICON_THRESHOLD,
                                       horizontal_variance=ICON_VARIANCE, vertical_variance=ICON_VARIANCE))

    def in_stance(self):
        return bool(self.task.find_one('hiyuki_left', threshold=ICON_THRESHOLD,
                                       horizontal_variance=ICON_VARIANCE, vertical_variance=ICON_VARIANCE))

    def iai_chain(self):
        """寒意还够就一直 闪避->居合; 返回出了几刀。"""
        n = 0
        misses = 0
        spent = False
        while n < self.IAI_MAX:
            self.task.next_frame()
            self.quick_alive()
            if not self.stance_ready():
                spent = True
                break
            if self.one_iai(n + 1):
                n += 1
                continue
            # 闪避被吞/没进架势: 寒意还在, 当场再闪一次; 收刀闪避这时按下去会进一个没人接的架势
            misses += 1
            if misses >= self.IAI_STANCE_RETRY:
                break
        if n and spent and self.IAI_END_DODGE:
            self.task.next_frame()
            self.quick_alive()
            self.tap_right()
            self.mark('iai_end_dodge')
            self.mwait(self.DODGE_RECOVER)
        return n

    def one_iai(self, index):
        """闪避进架势 -> 左键 -> 等出刀 -> 留出"出刀后再闪避"的间隔。返回是否进了架势。

        出刀信号(判据都要求先见过前置状态):
          R: 闪避键披风在闪避后灭过、又亮了 = 出刀了且还有寒意
          L: 居合图标在架势里亮过、又灭了 = 最后一刀出去了
          T: 都没读到, 按时间算
        """
        t0 = time.time()
        self.tap_right()
        taps = list(self.IAI_TAPS)
        next_retap = self.IAI_RETAP_FROM
        left_seen = False
        cloak_gone = False
        cast_at = None
        how = 'T'
        while True:
            now = time.time() - t0
            if taps and now >= taps[0]:
                taps.pop(0)
                self.tap_left()
                continue
            self.task.next_frame()
            # 这一刀里怪死了: 架势里缓存的左键和出刀后的闪避都不再发
            self.quick_alive()
            left = self.in_stance()
            right = self.stance_ready()
            if left:
                left_seen = True
            if not right:
                cloak_gone = True
            # 第2、3刀时居合图标本来就亮着, 光看它分不出闪避被吞了没有; 披风也得灭过
            stance_seen = left_seen and cloak_gone
            if stance_seen and now >= self.IAI_CAST_EARLIEST:
                if right and cloak_gone:
                    cast_at, how = time.time(), 'R'
                    break
                if not left:
                    cast_at, how = time.time(), 'L'
                    break
            if not stance_seen and now > self.IAI_STANCE_TIMEOUT:
                self.mark(f'iai{index}:no_stance')
                return False
            if now >= self.IAI_CAST_TIMEOUT:
                cast_at = time.time()
                break
            if now >= next_retap:
                self.tap_left()
                next_retap += self.IAI_RETAP_INTERVAL
                continue
            time.sleep(0.005)
        self.mark(f'iai{index}:{how}{cast_at - t0:.2f}')
        ready_at = max(cast_at + self.IAI_NEXT_DODGE, t0 + self.IAI_CYCLE_MIN)
        self.mwait(max(0.0, ready_at - time.time()))
        return True

    # ================================================================ 变身

    def read_form(self):
        """'yq' = 预求身(已变身), 'normal' = 常世身, None = 读不出(HUD 不在等)。"""
        self.task.next_frame()
        if self.stance_ready() or self.in_stance():
            return 'yq'
        names = list(self.task.get_target_names())
        long2 = self.task.find_best_match_in_box(self.task.get_box_by_name('target_box_long2').scale(1.2),
                                                 names, threshold=LOCK_THRESHOLD)
        long1 = self.task.find_best_match_in_box(self.task.get_box_by_name('box_target_enemy_long').scale(1.2),
                                                 names, threshold=LOCK_THRESHOLD)
        if long2 and not long1:
            return 'yq'
        if long1 and not long2:
            return 'normal'
        return None

    def read_form_wait(self):
        start = time.time()
        while True:
            form = self.read_form()
            if form is not None:
                self._last_form = form
                return form
            if time.time() - start >= self.FORM_READ_WAIT:
                fallback = self._last_form or 'normal'
                self.logger.warning(f'hiyuki solo: form unreadable, assume {fallback}')
                return fallback
            self.mwait(0.05)

    def transform_until_done(self):
        for i in range(self.TRANSFORM_TRIES):
            if self.transform():
                self._last_form = 'yq'
                return True
            self.alive()
            if self.read_form() == 'yq':
                self._last_form = 'yq'
                return True
        self.logger.warning('hiyuki solo: transform failed, retry next perform')
        return False

    def transform(self):
        """3A -> E -> 声骸 -> 按住左键到大招亮 -> R。返回大招放出去没有。"""
        self.mark('transform')
        self.attack_chain(self.T_A_CHAIN)
        self.alive()
        self.task.send_key(self.get_resonance_key(), down_time=self.A_TAP)
        self.record_resonance_use()
        self.mwait(self.T_E_TO_Q)
        self.task.next_frame()
        self.quick_alive()
        self.task.send_key(self.get_echo_key(), down_time=self.A_TAP)
        self.record_echo_use()
        self.mwait(self.T_Q_TO_HOLD)
        self.task.next_frame()
        self.quick_alive()
        lib_lit = self.hold_attack_until_lib()
        self.mark(f'hold->lib={lib_lit}')
        self.alive()
        if not lib_lit:
            return False
        return self.fire_transform_lib()

    def hold_attack_until_lib(self):
        """按住左键直到大招亮起。期间不调 check_combat(): 它锁不到目标时会中键重锁,
        那一下会激活窗口把按住的左键松掉; 只看目标, 丢太久就松手交给外面判。"""
        start = time.time()
        lost_since = None
        self.task.mouse_down()
        try:
            while time.time() - start < self.T_HOLD_TIMEOUT:
                self.mwait(0.1)
                self.task.next_frame()
                if self.liberation_available():
                    return True
                if self.task.has_target():
                    lost_since = None
                elif lost_since is None:
                    lost_since = time.time()
                elif time.time() - lost_since > self.T_TARGET_LOST_GIVE_UP:
                    return False
        finally:
            self.task.mouse_up()
        return False

    def fire_transform_lib(self):
        """R 连按到 HUD 掉下去(先见过 HUD 在), 等演出结束。"""
        if not self.wait_hud(self.LIB_HUD_UP_WAIT):
            return False
        key = self.get_liberation_key()
        start = time.time()
        misses = 0
        cast_at = None
        while time.time() - start < self.LIB_FIRE_TIMEOUT:
            if misses == 0:
                # HUD 还在(演出没开始)才按; 怪已经死了就别再按 R
                self.quick_alive()
                self.task.send_key(key, down_time=self.A_TAP)
            self.mwait(self.LIB_TAP_INTERVAL)
            self.task.next_frame()
            if self.task.in_team()[0]:
                misses = 0
            else:
                misses += 1
                if misses >= self.HUD_OFF_CONFIRM:
                    cast_at = time.time()
                    break
        if cast_at is None:
            self.mark('R_not_fired')
            return False
        self.mark('R')
        self.task.in_liberation = True
        try:
            while time.time() - cast_at < self.LIB_ANIM_MAX:
                self.task.next_frame()
                if self.task.in_team()[0]:
                    break
                self.mwait(0.05)
        finally:
            self.task.in_liberation = False
        self.add_freeze_duration(cast_at, time.time() - cast_at)
        self.record_liberation_use()
        self._liberation_available = False
        self.mwait(self.LIB_SETTLE)
        self.mark('R_done')
        # 大招本身可能就把 boss 打死了
        self.alive()
        return True

    # ================================================================ 处决

    def maybe_execute(self):
        """只认屏幕中央的处决提示; F 也是拾取键, 没提示不按。"""
        if not self.USE_EXECUTE:
            return False
        self.task.next_frame()
        box = self.task.box_of_screen(0.2, 0.2, 0.75, 0.8, hcenter=True, vcenter=True)
        if not self.task.find_one('f_break', box=box, target_height=720) or self.task.is_pick_f():
            return False
        start = time.time()
        self.task.send_key('f')
        self.task.can_break = False
        self.mark('F')
        # 处决带全局时停, 队伍 HUD 整个消失; 这段不判战斗
        self.task.in_liberation = True
        try:
            gone = False
            while time.time() - start < self.EXECUTE_HUD_OFF_WAIT:
                self.task.next_frame()
                if not self.task.in_team()[0]:
                    gone = True
                    break
                self.mwait(0.03)
            if gone:
                while time.time() - start < self.EXECUTE_ANIM_MAX:
                    self.task.next_frame()
                    if self.task.in_team()[0]:
                        break
                    self.mwait(0.05)
        finally:
            self.task.in_liberation = False
        self.add_freeze_duration(start, time.time() - start)
        self.mwait(self.EXECUTE_SETTLE)
        return True

    # ================================================================ 基础

    def alive(self):
        """战斗结束了就抛 NotInCombatException, 把整段打断。"""
        self.check_combat()

    def quick_alive(self):
        """每次读屏顺手看锁定目标(一次模板匹配)。boss 一死目标就丢, 这时才走完整判定,
        怪死了就在这里抛出去; 平时省掉 check_combat 那一整套读屏。"""
        if not self.task.has_target():
            self.alive()

    def wait_hud(self, time_out):
        start = time.time()
        while True:
            self.task.next_frame()
            if self.task.in_team()[0]:
                return True
            if time.time() - start >= time_out:
                return False
            self.mwait(0.05)

    def mwait(self, sec):
        """精确等待, 不走 executor.sleep(它每 0.4s 插一次截图)。任务被停用时抛异常。"""
        end = time.time() + sec
        while True:
            left = end - time.time()
            if left <= 0:
                return
            time.sleep(min(0.05, left))
            self.task.executor.check_enabled(check_pause=False)

    def macro_start(self):
        self.macro_t0 = time.time()
        self.macro_marks = []

    def mark(self, name):
        self.macro_marks.append(f'{name}@{time.time() - self.macro_t0:.2f}')
        if len(self.macro_marks) > 200:
            del self.macro_marks[:100]

    def timeline(self):
        return ' '.join(self.macro_marks)
