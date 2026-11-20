import time

from src.char.BaseChar import BaseChar, SwitchPriority


class Lucilla(BaseChar):
    LIBERATION_ANIMATION_TIME = 3.0
    LIBERATION_HEAVY_TIME = 5.0
    HEAVY_PULSE_TIME = 0.6
    # 长按E(对焦)的时长: 要在聚焦环【变金色】的那段松手(完美焦距 = 追光), 早了晚了都是补光。
    # 10-08 18:13 两次录屏逐帧量(从按下算, E 冷却 16.0 当帧出现 = 按下当帧放出):
    #   环出现 +0.6s, 白色标记 +0.7s; 变金色 +0.79~0.86 起, 退成蓝白 +1.10~1.11; 只有这一段金色。
    # 所以窗口约 0.86~1.10s, 宽 0.24s。取 0.95: 离起点 0.09、离终点 0.15。
    # 对照: 1.2s 两次全补光(已过金色); 0.8s 12 次成 7 次(卡在金色起点上, 执行器 sleep 多睡几十毫秒就跨过去)。
    # 【别信帧表那几个窗口】: 帧表写 30~45F / 60F 后成功, 按 60fps 算 1.2s 该成功, 实测是补光。
    # 窗口这么窄, 按住用的是精确计时(hold_resonance), 不走执行器 sleep
    RES_HOLD_TIME = 0.95
    # 补光会让 E 冷却当场减 8 秒(技能原文"补光…使共鸣技能的冷却时间减少8秒", 录屏里松手那帧 14.9 -> 6.8)。
    # E 总冷却 16s, 松手后读到 >= 这个值 = 追光, 读到个位数 = 补光
    E_CD_FOCUS_OK = 11.0
    # 松手后读冷却最多等这么久: 补光那一下冷却数字当场跳变, 跳的那一帧读出来是 0, 以前就当成"E 被吞"
    # 又白按了第二下(日志 55 次 54 次"swallowed"其实都是这个误报, E 都是按下当帧就放出来的)
    E_CD_READ_WAIT = 1.0
    # 带变奏入场后等这么久才按第一下 E。以前日志 50 多次(切人后 ~0.95s 按)拿 E 冷却倒推放出时刻,
    # 全是按下当帧放出; 录屏里入场动作 ~1.0~1.1s 才收, E 能打断这段收尾。对焦窗口从放出那刻算, 跟起点无关。
    # 10-08 18:13~19:00 用过 1.40, 用户嫌慢
    INTRO_SETTLE = 0.90
    # 最后一发 E(印象会到 150 的那发)松手后连点 R, 直到确认放出去。
    # R = 集满 3 张照片(150 印象)就解锁, 不吃能量, 冷却 25s(nanoka)。变奏入场给 100, 追光再给 50 + 20 协奏,
    # 都在追光第 54F(≈0.9s)到账: 10-12 之前实机每次都是松手后 1.01~1.03s 判到放出。
    # 【0.9s 之前别按 R】: 手上有剩的照片时 R 会在追光中途放出来, 把追光打断, 那 20 协奏就没了 ——
    # 10-12 19:35 松手后 0.39s 判到放出, 变身打完协奏只有 0.66, 绯雪没变奏。正常轮 R 本来就是 0.9s 才亮, 不受影响
    LIB_TAP_INTERVAL = 0.05
    R_TAP_AFTER_RELEASE = 0.90
    # 按住 E 期间也点 R(10-08 用户要的): 正常轮那几下从来没起过作用(R 要等追光), 手上有剩照片时反而会打断 E
    TAP_R_DURING_HOLD = False
    R_AFTER_E_CAP = 2.0       # 松手后最多点这么久(0.9s 就该亮, 留一倍)
    # 队伍 HUD 见过之后连续这么久读不到 = 大招演出开始(大招隐藏 HUD 268F≈4.5s)。
    # 以前是"连续 2 帧", 特效挡一下就成立, 10-12 19:35 那次的 0.39s 可能就是这么误判的
    HUD_OFF_HOLD = 0.50
    # 变身段打完有一段隐藏 HUD 的收尾演出(录屏 ~1.4s); HUD 回来时协奏环 ~0.83, 再过 0.21/0.27/0.30s
    # 自己补满(10-08 19:00 录屏三次), 不用打。所以从"最后一次看不到 HUD"起等这么久还不满才打
    FILL_CON_SETTLE = 0.8
    FILL_CON_CAP = 4.0        # 等 HUD 回来 + 等补满的总上限
    # 追忆状态(放 R 之后, nanoka): E 和 R 都放不了; 长按攻击 = 溯念留形 1->2->3 段, 第 3 段一直按着就消耗照片,
    # 照片打完或第 3 段时松手 -> 断舍离(隐藏 HUD, +20 协奏, 期间不能切人) -> 退出追忆。
    # 10-12 19:36 回到洛瑟菈时她还在追忆里(上一轮没收掉), E 按了两次都不进冷却; 用户手动长按攻击把照片打完才出来。
    # 现在: 一发 E 按下去冷却读不到(= E 放不出来)就当还在追忆, 长按攻击等断舍离, 然后照常交棒
    ZY_FINISH_CAP = 4.0       # 最多按住这么久; 到点松手(第 3 段时松手也出断舍离)
    ZY_DUANSHE_WAIT = 1.0     # 松手后再等断舍离这么久(第 3 段松手当场出, 判它要 HUD_OFF_HOLD 0.5s)
    CHARGE_TIME_OUT = 7.2
    LIBERATION_CD_SKIP = 1.5
    ECHO_WINDOW = (0.6, 4.0)
    EXTRA_BUDGET = 4.0
    HOLD_FIELD_OUTRO = {'char_suisui'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.e_count = 0
        self.check_f_on_switch = False
        self.lib_cast_at = None
        self.r_watch = None
        self.hud_before_e = False
        self.e_release = 0.0
        self.e_swallowed = False

    def reset_state(self):
        super().reset_state()
        # 【v3.5.32 起这一句必须在 reset_state 里重申, 只写 __init__ 保不住】:
        # CharFactory.apply_team_char_classes() 换成自定义类的写法是
        # `replacement.__dict__.update(内置实例.__dict__)` —— __init__ 里所有跟内置版
        # 取值不同的字段会被整个打回内置值。BaseChar.__init__ 是 `not self.is_healer`,
        # 所以非治疗角色一律被打回 True, 框架就会在 switch_next_char() 里替她按 F。
        # reset_state() 跑在那次 update 之后(load_chars: apply_team_char_classes -> 逐个
        # reset_state), 而且每次重读队伍都会再跑一遍, 放这里才真的保得住。
        # 洛瑟菈全程不打处决, 处决只归绯雪按轴上那一处自己按。
        self.check_f_on_switch = False

    def do_perform(self):
        if not self.perform_combat():
            self.switch_next_char()

    def perform_combat(self):
        start = time.time()
        self.task.wait_until(lambda: self.task.in_team()[0], time_out=0.2)
        if self.has_intro:
            # 入场动画期间的输入会被吞, 不是延迟执行
            self.sleep(self.INTRO_SETTLE, check_combat=False)
        self.task.next_frame()

        self.e_count = 0
        self.lib_cast_at = None
        # 接穗穗延奏时手上已经有两格, 补一格即可; 空手入场要攒满三格。
        # 一格 = 50 印象(追光), 补光只给 25 = 半格
        target = 1 if self.has_intro else 3
        hold_field = self.has_intro and self.check_outro() in self.HOLD_FIELD_OUTRO
        self.e_swallowed = False
        zy_tried = False

        while self.time_elapsed_accounting_for_freeze(start) < self.CHARGE_TIME_OUT:
            if self.e_swallowed and not zy_tried:
                # E 图标亮着、按下去却不进冷却: 还在上一轮的追忆里, 先把它收掉
                zy_tried = True
                if self.finish_zhuiyi('E pressed but never went on cooldown'):
                    self.switch_next_char(free_intro=self.fill_con())
                    return True
            if self.lib_cast_at is not None:
                self.transform_after_cast(self.lib_cast_at, 'R taps with E')
                self.switch_next_char(free_intro=self.fill_con())
                return True
            if self.e_count >= target:
                if self.perform_liberation():
                    self.switch_next_char(free_intro=self.fill_con())
                    return True
                # 大招没放出说明有一发 E 被吞了, 退一格重攒
                self.e_count = max(0, self.e_count - 1)
            elif self.time_elapsed_accounting_for_freeze(start) >= 2 and not hold_field \
                    and not self.liberation_available() \
                    and self.task.get_cd('liberation') > self.LIBERATION_CD_SKIP:
                self.logger.info('Lucilla liberation on long cd, switch to save time')
                break
            self.charge_once(fire_r=self.task.use_liberation and self.e_count + 1 >= target)
        if self.lib_cast_at is not None:
            # 最后一圈正好在超时前把 R 带出来了: 变身段照样打完, 不然她带着追忆下场, 下一轮 E/R 都放不了
            self.transform_after_cast(self.lib_cast_at, 'R taps with E, at timeout')
            self.switch_next_char(free_intro=self.fill_con())
            return True
        return False

    def charge_once(self, fire_r=False):
        """长按一发 E。fire_r: 这发追光会让印象到 150, 松手后点 R, 放出去就记 lib_cast_at。"""
        if not self.resonance_available():
            self.sleep(0.15)
            return
        self.hud_before_e = self.task.in_team()[0]
        held = self.hold_resonance(self.RES_HOLD_TIME, tap_r=fire_r and self.TAP_R_DURING_HOLD)
        self.r_watch = None
        if fire_r:
            cd, fired_at = self.fire_r_after_e()
        else:
            cd, fired_at = self.read_e_cd(), None
        if fired_at is not None:
            self.e_count += 1
            self.lib_cast_at = fired_at
            self.logger.info(f'Lucilla E #{self.e_count:g} held {held:.2f}s, cd {cd:.1f}s -> '
                             f'R fired {fired_at - self.e_release:.2f}s after release')
            return
        if cd <= 0:
            self.e_swallowed = True
            w = self.r_watch or {}
            self.logger.warning(f'Lucilla E swallowed (held {held:.2f}s, cd still {cd:.1f}s after '
                                f'{self.E_CD_READ_WAIT:.1f}s, R icon lit {w.get("lit_frames", "?")} frames, '
                                f'E icon ready {self.resonance_available()}), not counted')
            return
        if cd >= self.E_CD_FOCUS_OK:
            self.e_count += 1
            self.logger.info(f'Lucilla E #{self.e_count:g} held {held:.2f}s -> 追光(对焦成功), cd {cd:.1f}s')
        else:
            self.e_count += 0.5
            self.logger.warning(f'Lucilla E #{self.e_count:g} held {held:.2f}s -> 补光(对焦失败, 冷却减了 8s, '
                                f'只给半格), cd {cd:.1f}s')
        self.task.next_frame()

    def fire_r_after_e(self):
        """最后一发 E 松手后: 每 LIB_TAP_INTERVAL 点一下 R, 同时读 E 冷却分追光/补光, 看 R 放没放出去。

        R 从松手后 R_TAP_AFTER_RELEASE 才开始点(之前点会打断追光), HUD 看不到的时候不点。
        放没放出去看 check_r_fired。读到补光就停: 只有半格, R 亮不了。E 冷却一直读不到 = E 没放出来, 也停。
        Returns: (E 冷却读数, 大招放出时刻 / None)
        """
        start = time.time()
        key = self.get_liberation_key()
        cd = 0.0
        w = self.r_watch = self.new_r_watch(self.hud_before_e)
        last_tap = 0.0
        # HUD 正在消失、还没满 HUD_OFF_HOLD 时到了上限, 多等它判完
        while time.time() - start < self.R_AFTER_E_CAP or \
                (w['off_at'] is not None and time.time() - w['off_at'] < self.HUD_OFF_HOLD + 0.1):
            now = time.time()
            if w['off_at'] is None and now - self.e_release >= self.R_TAP_AFTER_RELEASE \
                    and now - last_tap >= self.LIB_TAP_INTERVAL:
                self.tap_key_quiet(key)
                last_tap = now
            self.task.next_frame()
            hud, fired_at = self.check_r_fired(w)
            if fired_at is not None:
                return cd, fired_at
            if hud and cd <= 0:
                cd = self.task.get_cd('resonance')
                if 0 < cd < self.E_CD_FOCUS_OK:
                    return cd, None
                if cd <= 0 and time.time() - start >= self.E_CD_READ_WAIT:
                    return cd, None
        self.logger.warning(f'Lucilla R not out {time.time() - start:.1f}s after release '
                            f'(cd {cd:.1f}s, R icon lit {w["lit_frames"]} frames, '
                            f'HUD blips {w["blips"]})')
        return cd, None

    @staticmethod
    def new_r_watch(hud_seen=False):
        return {'hud_seen': hud_seen, 'off_at': None, 'lit_frames': 0, 'blips': 0}

    def check_r_fired(self, w):
        """读当前帧判断 R 放没放出去, 返回 (HUD 在不在, 放出时刻 / None)。

        只认一种: 队伍 HUD 见过在场之后, 连续 HUD_OFF_HOLD 秒读不到(大招演出隐藏 HUD ≈4.5s)。
        放出时刻记 HUD 刚消失那一帧。中途 HUD 回来了算一次 blip(被特效挡了一下), 清零重来。
        以前还认"图标亮着按过 R、之后暗两帧": 实机放 R 每次都隐藏 HUD, 这条从没用上, 又怕被图标闪一下骗到, 去掉了。
        """
        if not self.task.in_team()[0]:
            if w['hud_seen']:
                now = time.time()
                w['off_at'] = w['off_at'] or now
                if now - w['off_at'] >= self.HUD_OFF_HOLD:
                    return False, w['off_at']
            return False, None
        if w['off_at'] is not None:
            w['blips'] += 1
        w['hud_seen'], w['off_at'] = True, None
        if self.liberation_available():
            w['lit_frames'] += 1
        return True, None

    def tap_key_quiet(self, key, fallback=True):
        """点一下键, 不投 WM_ACTIVATE(按住 E 期间用, activate 会把 E 松开)。

        activate 参数只有 PostMessage 后端认; 别的后端: fallback=True 退回普通发键, False 就不发。
        """
        try:
            interaction = self.task.executor.interaction
            vk = self.task.validate_key(key)
            interaction.send_key_down(vk, activate=False)
        except (TypeError, AttributeError):
            if fallback:
                self.task.send_key(key)
                return True
            return False
        time.sleep(0.01)
        interaction.send_key_up(vk)
        return True

    def read_e_cd(self):
        """松手后读 E 冷却, 读不到数字就接着读到 E_CD_READ_WAIT。返回最后一次读数。"""
        start = time.time()
        cd = 0.0
        while True:
            self.task.next_frame()
            cd = self.task.get_cd('resonance')
            if cd > 0 or time.time() - start >= self.E_CD_READ_WAIT:
                return cd
            self.sleep(0.05, check_combat=False)

    def hold_resonance(self, duration, tap_r=False):
        """按住 E 精确 duration 秒再松, 返回实际按住了多久。tap_r: 按住期间每 LIB_TAP_INTERVAL 点一下 R。

        不用 self.sleep(): 执行器每 0.4s 插一次截图, 会多睡几十毫秒, 对焦窗口只有 0.24s 宽。
        切成 0.05s 一片的 time.sleep, 片间检查任务有没有被停用。
        R 走 tap_key_quiet 不激活窗口; 后端不支持就不点(普通发键会把 E 松开)。
        """
        key = self.get_resonance_key()
        lib_key = self.get_liberation_key()
        self.task.send_key_down(key)
        down = time.time()
        last_tap = down
        try:
            end = down + duration
            while True:
                now = time.time()
                left = end - now
                if left <= 0:
                    break
                if tap_r and left > 0.03 and now - last_tap >= self.LIB_TAP_INTERVAL:
                    tap_r = self.tap_key_quiet(lib_key, fallback=False)
                    last_tap = now
                    continue
                time.sleep(min(0.05, left))
                self.task.executor.check_enabled(check_pause=False)
        finally:
            self.task.send_key_up(key)
        self.e_release = time.time()
        held = self.e_release - down
        self.record_resonance_use()
        return held

    def perform_liberation(self):
        """放大招变身并输出。返回大招是否真的放出去了。"""
        if not self.task.use_liberation:
            return False

        # 不用 click_liberation: 它内部的 while not in_team() 在变身形态下会卡死。
        # 先按再看(图标在刚上场/动画期间会假阴性)。【"按过之后图标暗着"不能算放出去】:
        # 印象没到 150 时图标本来就暗, 10-08 18:42 就是按早了一下被当成放出去, 白打 8 秒重击。
        # 跟 fire_r_after_e 一样看 check_r_fired; HUD 正在消失时不按, 等它判完
        start = time.time()
        sent = 0
        w = self.new_r_watch(self.task.in_team()[0])
        fired_at = None
        while fired_at is None and (time.time() - start < 1.5 or
                                    (w['off_at'] is not None and time.time() - w['off_at'] < self.HUD_OFF_HOLD + 0.1)):
            if w['off_at'] is None:
                self.send_liberation_key()
                sent += 1
            until = time.time() + 0.15
            while time.time() < until and fired_at is None:
                self.task.next_frame()
                fired_at = self.check_r_fired(w)[1]
        if fired_at is None:
            self.logger.warning(f'Lucilla liberation not confirmed after {sent} presses '
                                f'(R icon lit {w["lit_frames"]} frames, HUD blips {w["blips"]}), not fired')
            return False

        self.transform_after_cast(fired_at, f'{sent} presses')
        return True

    def transform_after_cast(self, cast_at, how):
        """大招已经放出去之后(cast_at = HUD 刚消失那一帧): 等变身动画, 脉冲重击 + 窗口内放 Q, 打空回路。

        动画从 cast_at 算: 确认放出要看满 HUD_OFF_HOLD, 这段已经过去了, 别再整段重睡。
        """
        self.record_liberation_use()
        self.logger.info(f'Lucilla perform lib ({how}, confirmed {time.time() - cast_at:.2f}s after HUD off)')
        self.sleep(max(0.0, cast_at + self.LIBERATION_ANIMATION_TIME - time.time()), check_combat=False)
        start = cast_at
        self.pulse_heavy_attack(self.LIBERATION_HEAVY_TIME)
        # 被打断时能量会剩, 变身一结束就作废, 所以按图标打空为准
        self.pulse_until(lambda: not self.is_mouse_forte_full(), 'forte drained')
        self.logger.info(f'Lucilla perform lib end ({time.time() - start:.1f}s)')
        return True

    def pulse_heavy_attack(self, total_time):
        """脉冲重击: 每拍重新按下, 某拍被打断下一拍自动恢复。Q 在窗口内反复发。"""
        start = time.time()
        last_echo = 0
        while time.time() - start < total_time:
            elapsed = time.time() - start
            # 变身动画期间按 Q 会被吞, 形态结束后按又没了增益, 只有中间这段有效
            if self.ECHO_WINDOW[0] <= elapsed <= self.ECHO_WINDOW[1] and time.time() - last_echo >= 0.3:
                self.send_echo_key()
                last_echo = time.time()
            self.heavy_pulse()

    def pulse_until(self, cond, name):
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.EXTRA_BUDGET:
            if cond():
                self.logger.info(f'Lucilla {name} after {time.time() - start:.1f}s')
                return True
            self.heavy_pulse()
        self.logger.warning(f'Lucilla {name} timed out')
        return False

    def finish_zhuiyi(self, why):
        """还在追忆状态时收掉它: 长按攻击(溯念留形 1->2->3 段, 第 3 段按着消耗照片)等断舍离。

        断舍离 = HUD 连续消失 HUD_OFF_HOLD(跟判 R 放出同一个判据)。按满 ZY_FINISH_CAP 还没出就松手,
        第 3 段时松手也会出断舍离, 再等 ZY_DUANSHE_WAIT。按住期间不发别的键(发键会投 activate 把按住松掉)。
        Returns: 看没看到断舍离。没看到 = 多半不在追忆里, 交回原流程。
        """
        self.logger.warning(f'Lucilla still in 追忆? ({why}) -> holding attack until 断舍离')
        start = time.time()
        w = self.new_r_watch(self.task.in_team()[0])
        seen = None
        self.task.mouse_down()
        try:
            while seen is None and (time.time() - start < self.ZY_FINISH_CAP or w['off_at'] is not None):
                self.task.next_frame()
                seen = self.check_r_fired(w)[1]
        finally:
            self.task.mouse_up()
        released = time.time()
        while seen is None and (time.time() - released < self.ZY_DUANSHE_WAIT or w['off_at'] is not None):
            self.task.next_frame()
            seen = self.check_r_fired(w)[1]
        if seen is None:
            self.logger.warning(f'Lucilla no 断舍离 after holding {released - start:.1f}s + '
                                f'{self.ZY_DUANSHE_WAIT:.1f}s (HUD blips {w["blips"]}), back to normal flow')
            return False
        self.logger.info(f'Lucilla 断舍离 {seen - start:.2f}s after holding attack '
                         f'({"while held" if seen < released else "after release"}), 追忆 cleared')
        return True

    def attack_until(self, cond, name):
        """跟 pulse_until 一样一拍一拍按住攻击, 但按住期间逐帧看 cond, 成立就立刻松手返回。"""
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.EXTRA_BUDGET:
            self.task.mouse_down()
            try:
                end = time.time() + self.HEAVY_PULSE_TIME
                while time.time() < end:
                    self.task.next_frame()
                    if cond():
                        self.logger.info(f'Lucilla {name} after {time.time() - start:.2f}s')
                        return True
            finally:
                self.task.mouse_up()
            self.sleep(0.02, check_combat=False)
        self.logger.warning(f'Lucilla {name} timed out')
        return False

    def heavy_pulse(self):
        self.task.mouse_down()
        try:
            self.sleep(self.HEAVY_PULSE_TIME, check_combat=False)
        finally:
            self.task.mouse_up()
        self.sleep(0.02, check_combat=False)

    def fill_con(self):
        """确认协奏满再交棒。

        变身段打完先是一段隐藏 HUD 的收尾演出, HUD 回来后协奏环还要 0.2~0.3s 才自己补满。
        以前从变身结束起只等 1.5s, 10-08 19:00 三次分别差 0.01/0.01/0.12s 超时, 接着先打整整一拍
        0.6s 重击才再看 —— 就是"还要 A 一下才切"。现在逐帧看, HUD 回来满 FILL_CON_SETTLE 秒还不满才打,
        打的时候也逐帧看, 一满就松手交棒。
        """
        start = time.time()
        last_hidden = start
        # 最长一段连续看不到 HUD 有多久: 正常收尾(断舍离)是 ~1.3s, 短得多 = 断舍离可能没出, 下一轮会卡在追忆里
        hidden_from, longest = start, 0.0   # 第一帧就看不到 = 进来之前就开始藏了, 从 start 算
        while time.time() - start < self.FILL_CON_CAP:
            self.task.next_frame()
            if not self.task.in_team()[0]:
                last_hidden = time.time()
                if hidden_from is None:
                    hidden_from = last_hidden
                longest = max(longest, last_hidden - hidden_from)
                continue
            hidden_from = None
            if self.is_con_full():
                self.logger.info(f'Lucilla con full {time.time() - start:.2f}s after lib end '
                                 f'({time.time() - last_hidden:.2f}s after HUD back, HUD hidden {longest:.2f}s)')
                return True
            if time.time() - last_hidden >= self.FILL_CON_SETTLE:
                break
        # HUD 在场、读数已经该稳了还没满, 说明是真的短了(被打断), 站着等没用, 得打
        self.logger.info(f'Lucilla con reads {self.get_current_con():.2f} '
                         f'{time.time() - last_hidden:.2f}s after HUD back (HUD hidden {longest:.2f}s), attacking')
        if self.attack_until(self.is_con_full, 'con filled'):
            return True
        self.logger.warning(f'Lucilla con still reads {self.get_current_con():.2f}, '
                            f'next char gets no intro')
        return False

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if has_intro and current_char and current_char.char_name in {'char_verina', 'char_shorekeeper',
                                                                    'char_suisui'}:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)
