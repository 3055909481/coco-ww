import time

from src.char.Qiuyuan import Qiuyuan as NativeQiuyuan
from src.char.ShoreKeeper import ShoreKeeper as NativeShoreKeeper


class ShoreKeeper(NativeShoreKeeper):
    """守岸人 —— 嘉仇守的辅助, 循环轮的第一棒。

    轮换环: 嘉贝莉娜 -> 守岸人 -> 仇远 -> 嘉贝莉娜。她这一棒的活只有一件:
    **把协奏打满, 带着变奏交给仇远**。

        循环轮 (变奏入场)   3A + E -> 2A + 回路重击 -> Q + R -> 变奏仇远
        启动轮 (空手入场)   3A + E -> 2A + 回路重击 + 闪避 -> 3A + 回路重击
                            -> Q + R -> 变奏仇远

    两条轴只差中间那一组「3A + 回路重击」: 空手入场时协奏差一截, 得多打一组,
    夹在中间那下闪避是用来断重击后摇的。所以用【有没有带变奏进场】分流,
    而不是现场读协奏 —— "打几组"这件事在这条轴上是死的。

    轴出自 E:\\轴视频\\【轮椅】嘉贝莉娜，仇远，守岸人.mp4。
    """
    # ================================================================ 固定顺序切人 (公共段)
    # 这一段在 Galbrena.py / Qiuyuan.py / ShoreKeeper.py 三个文件里逐字一样, 改一处记得同步改三处。
    # (Qiuyuan__ShoreKeeper__Xigelika / Chisa__Suisui__YangYangSp 那两队的拷贝跟这份只差这行注释。)
    # 下面各自的常量写在这一段【之后】, 所以子类想覆盖哪个直接在下面重写就行。
    #
    # 为什么不用框架的 switch_next_char():
    #   1. 它按 get_switch_priority() 现场挑人, 顺序会漂 —— 这套轴要求死顺序;
    #   2. 它内部有三处补普攻 ("performing too fast add a normal attack"、维持锁定、
    #      切换未检测到时补点击), 打完最后一下想立刻走人时那几下 A 会把节奏拖乱。
    # 代价是框架的交接记账要自己补, 全在 handoff_to() 里。

    SWITCH_TIMEOUT: float = 2.0        # 单程切人的最长尝试时间 (秒)
    SWITCH_KEY_INTERVAL: float = 0.12  # 连按数字键的间隔 (秒)
    # 切到之后等角色站稳的时间 (秒)。很敏感 —— 实测 0.50 / 0.52 会让下一个角色的起手被吞,
    # 0.55 才稳, 别再往下压
    SWITCH_SETTLE: float = 0.55
    SETTLE_ATTACK_INTERVAL: float = 0.42
    HANDOFF_CON_WAIT: float = 0.8      # 交接时等协奏读数稳定的上限 (秒)
    HANDOFF_CON_POLL: float = 0.1
    HOLD_POLL: float = 0.1             # 长按期间轮询条件的间隔 (秒)
    # 切走前按 F 处决 —— 框架 switch_next_char() 里有 current_char.f_break(True), 直接
    # 按数字键切人会绕开它。这套轴里默认【关掉】: handoff_to() 是在 switch_to_index()
    # 之后调的, 那时候下一个角色已经站上场了, 这一下 F 会被他吃掉, 而且发生在他的 R
    # 之前 (千咲"还没按 R 就开处决"就是这么来的)。处决统一由千咲在 R 之后接
    F_BREAK_ON_HANDOFF: bool = False

    # 别人用 handoff_to() 直接切给我时打的标记, 写成类属性省掉 __init__。
    # 不能只靠 has_intro —— reset_state() 每次重读队伍都会把它清成 False, 而战斗判定
    # 一抖动就会重进战斗、重读队伍, 于是角色以为自己空手入场, 跑去走开场那套流程
    intro_pending = False
    # 打上这个标记的时刻, 用来给它定保质期。
    # 【为什么需要保质期】(2026-09-04 用户报"开关一下自动战斗流程不能重置"):
    #   手动停任务时战斗循环是被任务停止打断的, 既不是 NotInCombatException 也不是
    #   CharDeadException, 所以 AutoCombatTask 里那句 combat_end() 整个跳过 ——
    #   on_combat_end() -> reset_fixed_rotation() 没跑, 标记留在角色身上;
    #   而 reset_state() 又【故意】不清它(战斗判定一抖动就会重读队伍, 清了会误判成
    #   空手入场)。两条一叠, 关掉再打开时角色还以为自己带着变奏, 直接跳过开场轴。
    # 正常交接 1~2 秒内就被 take_intro() 消费掉, 而关掉再打开怎么也不止 TTL 这么久,
    # 所以用时间就能把两种情况分开
    intro_pending_at = 0.0
    INTRO_PENDING_TTL: float = 8.0
    # 切人【真正完成】那一刻的时间戳 —— in_team 认出换人成功的那一帧, 不是
    # settle_with_attack 站稳之后。循环轴的落地E 就按这个锚点计时, 差 0.55 秒
    # 就会掉进"E 按在下落段完全没反应"的失败区
    switch_confirmed_at = 0.0

    def note_intro_handoff(self):
        """被别人用直接切人的方式交接进场时调用 —— 记下"我是带着变奏上来的"。"""
        self.intro_pending = True
        self.intro_pending_at = time.time()

    def take_intro(self):
        """本次入场是否带着变奏。读完就清, 避免下一轮误判。

        过期的标记不认 —— 见 intro_pending_at 的注释(停任务不会走 combat_end)。
        """
        pending = self.intro_pending
        if pending:
            age = time.time() - self.intro_pending_at
            if age > self.INTRO_PENDING_TTL:
                self.logger.info(f'intro_pending 已过期 ({age:.1f}s > '
                                 f'{self.INTRO_PENDING_TTL}s), 按空手入场处理')
                pending = False
        intro = self.has_intro or pending
        self.intro_pending = False
        self.intro_pending_at = 0.0
        return intro

    def reset_fixed_rotation(self, chars=None):
        """战斗结束时调 —— 在各自的 on_combat_end() 里, 把 chars 传进来。

        【要清全队, 不能只清自己】: combat_end() 只对【当前角色】调 on_combat_end(),
        另外两个人身上的 intro_pending 不传 chars 的话就留着了。
        """
        for char in (chars if chars is not None else [self]):
            if char is None:
                continue
            char.intro_pending = False
            char.intro_pending_at = 0.0

    def direct_switch(self, slot=None, assume_intro=False):
        """按数字键直接切到指定队伍位置 (1-based), 并自己补上框架的交接记账。

        Args:
            slot: 不传就用 NEXT_SLOT。
            assume_intro: True 时不读协奏, 直接认定是满的。刚放完大招要用这个 ——
                能量环读数滞后会把 has_intro 误判成 False, 下一个角色就拿不到变奏增益。
        """
        slot = self.NEXT_SLOT if slot is None else slot
        if slot is None:
            return False
        target = self.char_at_slot(slot)
        if target is None or target is self:
            return False

        has_intro = True if assume_intro else self.wait_con_full()
        if not self.switch_to_index(target.index):
            self.logger.warning(f'direct switch to slot {slot} failed')
            return False
        self.logger.info(f'direct switched to slot {slot} ({target.char_name}, intro={has_intro})')
        self.handoff_to(target, has_intro)
        return True

    def switch_to_index(self, index):
        """按数字键切到指定队伍位置, 直到 in_team 确认切过去了。

        点击落在谁身上就由谁打出来, 所以等待期间也保持点普攻, 两边连段都不断档。
        """
        start = time.time()
        while time.time() - start < self.SWITCH_TIMEOUT:
            in_team, current_index, _ = self.task.in_team()
            if in_team and current_index == index:
                # 【先记时间戳再站稳】: settle_with_attack 要花 SWITCH_SETTLE 秒,
                # 记在它后面的话锚点整体晚 0.55 秒, 靠锚点计时的动作全跟着晚
                self.switch_confirmed_at = time.time()
                self.settle_with_attack(self.SWITCH_SETTLE)
                return True
            self.task.send_key(index + 1)
            self.click()
            self.sleep(self.SWITCH_KEY_INTERVAL, check_combat=False)
        return False

    def settle_with_attack(self, duration):
        """等 duration 秒, 期间保持点普攻, 不干等。"""
        end = time.time() + duration
        while time.time() < end:
            self.click()
            self.sleep(min(self.SETTLE_ATTACK_INTERVAL, max(0.0, end - time.time())),
                       check_combat=False)

    def handoff_to(self, target, has_intro):
        """把场上角色交接给 target —— 复刻 switch_next_char() 内部那套记账。

        直接按数字键切人绕开了框架的交接逻辑, 必须自己补上, 否则:
          - 没人的 is_current_char 是 True → get_current_char() 返回 None → 下一帧崩溃;
          - has_intro / last_outro_time 不更新, 下一个角色拿不到变奏。
        """
        if self.F_BREAK_ON_HANDOFF:
            self.f_break(check_f_on_switch=True)
        self.task.in_liberation = False
        self.switch_out(con_full=has_intro)
        target.is_current_char = True
        target.has_intro = has_intro
        if has_intro and hasattr(target, 'note_intro_handoff'):
            # has_intro 是易失的, 再给对方补一个它自己保管的持久标记
            target.note_intro_handoff()
        # 用 in_team 确认那一刻, 不是现在 —— 中间隔着 settle_with_attack 那 0.55 秒
        target.last_switch_in_time = self.switch_confirmed_at or time.time()
        self.switch_confirmed_at = 0.0
        if has_intro:
            now = time.time()
            self.task.add_freeze_duration(now, target.intro_motion_freeze_duration, -100)
            self.last_outro_time = now

    def wait_con_full(self):
        """短暂轮询协奏是否满 —— 用来决定交接时给不给对方变奏。

        不能只读一次: 打完最后一下那一刻协奏其实已经满了, 但能量环读数滞后。
        必须在【按切人键之前】读: 切走之后能量环显示的已经是新角色的了。
        """
        start = time.time()
        while time.time() - start < self.HANDOFF_CON_WAIT:
            if self.is_con_full():
                return True
            self.sleep(self.HANDOFF_CON_POLL, check_combat=False)
        self.logger.info(f'con not full before switch (con={self.get_current_con()}), '
                         f'next char gets no intro')
        return False

    def char_at_slot(self, slot):
        """按队伍位置 (1-based) 取角色, 越界返回 None。"""
        index = slot - 1
        for char in self.task.chars:
            if char is not None and char.index == index:
                return char
        return None

    def attack_chain(self, count, interval, last_interval=None):
        """点按普攻打一轮连段。

        Args:
            last_interval: 最后一段之后的间隔。后面紧接切人时传更小的值 —— 按下切人键之后
                还要等游戏识别切换完成, 那段时间最后一下的动作照样在演, 两者是重叠的。
        """
        for i in range(count):
            self.click()
            if i == count - 1 and last_interval is not None:
                self.sleep(last_interval)
            else:
                self.sleep(interval)

    def hold_until(self, cond, timeout, name):
        """轮询某个条件, 期间不动手 —— 攻击键的按下/松开由调用方管。"""
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < timeout:
            if cond():
                self.logger.info(f'{name} ready after '
                                 f'{self.time_elapsed_accounting_for_freeze(start):.1f}s')
                return True
            self.sleep(self.HOLD_POLL)
        self.logger.info(f'timed out waiting for {name} ({timeout:.1f}s)')
        return False


    # ---------------------------------------------- "没打到就不许切人" 的硬 gate
    # 这套轴是靠变奏/延奏一棒接一棒串起来的: 任何一棒少做一步, 代价都直接传给下一棒。
    # 实测 21:23 那一圈就是完整的传染链:
    #   穗穗 forte3 没读到 -> 硬走 Q/R -> "liberation not ready, leaving without field"
    #   -> 领域没放, 千咲上场 -> "chisa: liberation not ready" -> 整圈裸打。
    # 所以关键步骤不再"到点就走", 改成留场重试到真打出来为止。
    #
    # 【没有超时是有意的】: 给上限就等于"打不出来也走", 那这个 gate 等于没有。
    # 唯一的出口是 check_combat() —— 脱战/任务被停时它抛异常, 整条轴一起退出。
    # 怪都没了的话, "这一步没打到"本来也就不是问题了。
    GATE_POLL: float = 0.12       # gate 轮询间隔 (秒)
    GATE_LOG_EVERY: float = 3.0   # 卡住时每隔这么久打一条日志, 好在日志里一眼看出卡在哪

    def require(self, name, cond, action=None, timeout=0):
        """留场直到 cond() 成立 —— 这一步没做到就不许往下走。

        Args:
            name: 打日志用的步骤名, 卡住时就是靠它定位。
            cond: 判据, 返回真就放行。
            action: 每轮做的事, 一般传 self.click。不传就是干等 (长按期间要用这个,
                手上已经按着了, 再 click 会打断长按)。
            timeout: >0 时的上限 (秒), 到点放行并返回 False。默认 0 = 不设上限。
                【什么时候该给上限】: 这一步做不到时"照走"能自愈的, 就必须给 ——
                比如协奏没满只是让下一棒退回启动轴(那条轴本来就为空手入场准备),
                而卡死在这里不能自愈。实测 01:18 那轮 fill_con 无上限直接把任务挂住了。

        Returns:
            bool: 条件成立返回 True; 到 timeout 放弃返回 False。
        """
        start = time.time()
        last_log = 0.0
        while True:
            if cond():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if waited > 0.3:
                    self.logger.info(f'gate [{name}] ok after {waited:.1f}s')
                return True
            self.check_combat()
            if action is not None:
                action()
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if timeout > 0 and elapsed >= timeout:
                self.logger.warning(f'gate [{name}] gave up after {elapsed:.1f}s, moving on')
                return False
            if elapsed - last_log >= self.GATE_LOG_EVERY:
                last_log = elapsed
                self.logger.warning(f'gate [{name}] not satisfied after {elapsed:.1f}s, '
                                    f'staying on field')
            self.sleep(self.GATE_POLL)

    def require_cast(self, name, send, landed, pre=None, timeout=0):
        """反复按某个键直到它真的放出去了。

        跟 require() 的区别: 这里每轮都【重新发键】。被打断时按键会被游戏吃掉,
        补发是唯一的解法。
        Args:
            send: 发键的函数。
            landed: 判据 —— "已经放出去了"返回真。
            pre: 发键之前必须先成立的条件 (比如"图标亮了"), 不成立就先不发, 省得
                在冷却里空按。不传就是每轮都发。
            timeout: >0 时的上限 (秒), 到点放弃并返回 False。默认 0 = 不设上限。
        """
        start = time.time()
        last_log = 0.0
        while True:
            if landed():
                waited = self.time_elapsed_accounting_for_freeze(start)
                self.logger.info(f'gate [{name}] cast confirmed after {waited:.1f}s')
                return True
            self.check_combat()
            if pre is None or pre():
                send()
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if timeout > 0 and elapsed >= timeout:
                self.logger.warning(f'gate [{name}] gave up after {elapsed:.1f}s, moving on')
                return False
            if elapsed - last_log >= self.GATE_LOG_EVERY:
                last_log = elapsed
                self.logger.warning(f'gate [{name}] not cast after {elapsed:.1f}s, retrying')
            self.sleep(self.GATE_POLL)

    # ============================================================ 公共段结束

    # ================================================================ 嘉仇守: 守岸人这一棒

    # ---- 时间常量。除注明外都是官方帧表换算 (60fps, 帧/60 = 秒), 单位秒 ----
    # 变奏入场: QTE 第 86F 前不响应输入。这一段照样点普攻 —— 按键会被吃掉,
    # 但窗口一结束就立刻接上, 比干等少浪费一拍
    INTRO_ANIM = 1.45
    A_INTERVAL = 0.40      # 普攻段与段之间
    A_TO_E = 0.12          # 第三段普攻 -> E
    E_TO_A = 0.55          # E -> 下一组普攻
    A_TO_HEAVY = 0.30      # 最后一段普攻 -> 按住普攻出回路重击
    GROUP1_A = 3           # 第 1 组: 3A + E
    GROUP2_A = 2           # 第 2 组: 2A + 回路重击 (视频里这一组就是两下)
    GROUP3_A = 3           # 启动轮多出来的第 3 组
    ROUND_GAP = 0.35       # 上一组打完 -> 下一组普攻
    HEAVY_TO_ECHO = 0.25   # 最后一组重击 -> Q
    ECHO_TO_LIB = 0.10     # Q -> R (fire_echo 自己会花掉几百毫秒)
    # 【闪避只在启动轮那一组之后】: 它是拿来断回路重击后摇的, 时机由"回路满 -> 重击"
    # 这个判据钉住; 闪完立刻接普攻, 那一下普攻又把闪避的后摇取消掉。
    # 循环轮的重击后面直接接 Q+R, 没有后摇可断, 闪一下反而把人往后拉
    DODGE_AFTER_HEAVY: bool = True
    # 【协奏够了就提前收工】: 一个大招给的协奏远不止 25%, 所以 75% + R 一定满。
    # 还要 R 是亮的 —— 收工的动作就是 Q+R, 缺的那 25% 本来就指望 R 补上,
    # 只看协奏不看 R 的话收工了 R 也放不出来, 下一棒拿不到变奏
    CON_EXIT_PCT = 0.75
    CON_CONFIRM = 2        # 协奏读数会抖, 连读几次一致才认
    _con_hits = 0
    LIB_WINDOW = 4.0       # 放 R 之前等大招图标亮的上限, 期间照样打普攻
    LIB_RETRY = 0.15
    ECHO_TRIES = 4
    ECHO_RETRY = 0.22
    FORTE_WAIT = 5.0       # 等回路条满的上限, 期间继续补普攻
    CON_GATE_TIMEOUT = 10.0
    LEAVE_TRIES = 2
    TEAM_HUD_WAIT = 2.5
    SWITCH_ANCHOR_MAX = 1.5

    @property
    def NEXT_SLOT(self):
        """打完切仇远。按【类】找人而不是写死队伍位置。"""
        for char in self.task.chars:
            if isinstance(char, NativeQiuyuan):
                return char.index + 1
        return None

    def reset_state(self):
        super().reset_state()
        # 守岸人登记的是 HEALER, BaseChar.__init__ 里 check_f_on_switch = not is_healer
        # 已经是 False 了。这里再写一遍是防上游哪天改了默认值 —— 这条轴上没有
        # 要她按的处决, 按 F 只会把节奏拖乱
        self.check_f_on_switch = False

    def on_combat_end(self, chars):
        self.reset_fixed_rotation(chars)
        super().on_combat_end(chars)

    # ------------------------------------------------------------------ 轴

    def do_perform(self):
        intro = self.take_intro()
        self.check_f_on_switch = False
        anchor = self.switch_in_anchor()
        if intro:
            # 用【切人确认那一刻】当基准, 不是这个函数被调到那一刻 —— 中间隔着框架的
            # 交接记账和任务循环, 那段延迟是变长的
            self.settle_with_attack(max(0.0, anchor + self.INTRO_ANIM - time.time()))
        if self.flying():
            self.wait_down()
        self.logger.info(f'shorekeeper axis start (intro={intro})')

        # ---- 第 1 组: 3A + E ----
        # 【每一下普攻之前都看一眼协奏】: 设固定检查点的话, 检查点之后协奏涨过 75%
        # 就再也没人看了, 后面那一两组纯粹白站
        if self.attack_watching_con(self.GROUP1_A, self.A_TO_E):
            return self.finish()
        self.cast_resonance()
        self.sleep(self.E_TO_A)
        if self.con_enough('E 之后'):
            return self.finish()

        # ---- 第 2 组: 2A + 回路重击 (启动轮在这之后加一下闪避) ----
        if self.attack_watching_con(self.GROUP2_A, self.A_TO_HEAVY):
            return self.finish()
        if self.forte_heavy(dodge=not intro) == 'con':
            return self.finish()
        if self.con_enough('第2组之后'):
            return self.finish()

        if not intro:
            # ---- 启动轮专属的第 3 组: 空手入场协奏差一组 ----
            self.sleep(self.ROUND_GAP)
            if self.attack_watching_con(self.GROUP3_A, self.A_TO_HEAVY):
                return self.finish()
            if self.forte_heavy(dodge=False) == 'con':
                return self.finish()

        return self.finish()

    def attack_watching_con(self, count, last_interval=None):
        """打 count 段普攻, 【每一下之前都看一眼协奏】—— 够 75% 就立刻收工。"""
        for i in range(count):
            if self.con_enough():
                return True
            self.click()
            gap = last_interval if (i == count - 1 and last_interval is not None) \
                else self.A_INTERVAL
            self.sleep(gap)
        return self.con_enough()

    def con_enough(self, where=None):
        """协奏够不够直接收工(75%) —— 【还要 R 是亮的】。

        连读 CON_CONFIRM 次一致才认: 能量环读数会抖, 抖到低值只是晚一点收工,
        抖到高值就会提前走人。
        """
        con = self.get_current_con()
        if con < self.CON_EXIT_PCT:
            self._con_hits = 0
            if where:
                self.logger.info(f'shorekeeper: con {con:.2f} ({where}), one more block')
            return False
        if not self.liberation_available():
            self._con_hits = 0
            if where:
                self.logger.info(f'shorekeeper: con {con:.2f} 够了但 R 还没亮 '
                                 f'({where}), one more block')
            return False
        self._con_hits += 1
        if self._con_hits < self.CON_CONFIRM:
            return False
        self._con_hits = 0
        self.logger.info(f'shorekeeper: con {con:.2f} >= {self.CON_EXIT_PCT} 且 R 亮着'
                         f'{" (" + where + ")" if where else ""}, wrapping up with Q+R')
        return True

    def finish(self):
        """收工: Q -> R -> 变奏仇远。"""
        self.sleep(self.HEAVY_TO_ECHO)
        self.fire_echo()
        self.sleep(self.ECHO_TO_LIB)
        self.fire_liberation()
        self.leave()

    # ------------------------------------------------------------------ 动作

    def cast_resonance(self):
        """E。走 click_resonance 而不是裸发键 —— 它会等动画结束并记账。

        【返回的是三元组】(clicked, duration, animated), 不是 bool。
        """
        return self.click_resonance(send_click=False, time_out=1)[0]

    def forte_heavy(self, dodge=False):
        """回路重击: 按住普攻直到回路耗完。

        判据用找图(鼠标长按提示图标)而不是颜色百分比 —— 找图对特效糊屏更扛得住。
        回路还没满就先补普攻等一等; 等到 FORTE_WAIT 还没有就跳过: 这一步做不到
        不会传染给下一棒, 卡死在这里反而会。
        """
        start = time.time()
        ready = False
        while self.time_elapsed_accounting_for_freeze(start) < self.FORTE_WAIT:
            # 等回路的过程中【也一直看协奏】—— 够了就直接收工, 这一下重击不打了
            if self.con_enough('等回路时'):
                return 'con'
            if self.is_mouse_forte_full():
                ready = True
                break
            self.check_combat()
            self.click()
            self.sleep(self.A_INTERVAL)
        if not ready:
            self.logger.warning('shorekeeper: forte not ready, skipping heavy')
            return False
        ok = bool(self.heavy_click_forte(self.is_mouse_forte_full))
        if ok and dodge and self.DODGE_AFTER_HEAVY:
            # 【重击 -> 马上闪 -> 马上普攻】: 闪避取消重击的后摇, 紧接着那一下普攻
            # 又取消闪避的后摇。中间【不能留任何固定延时】—— 留了就两个都取消不掉
            self.dodge()
            self.click()
        return ok

    def fire_echo(self):
        """Q 声骸 —— 【连发, 不是只发一次】。

        演出期间的按键被游戏丢弃、不排队, 只发一次很容易整段被吃掉;
        Q 是脱手技能, 多按几下不会打断当前动作。
        【不用 click_echo】: 它第一件事是读屏 echo_available(), 读到 False 就掉进
        一秒起步的慢路径, 而刚打完重击那几帧读数最不可信。
        """
        was_available = self.echo_available()
        for i in range(self.ECHO_TRIES):
            self.send_echo_key()
            self.record_echo_use()
            self.sleep(self.ECHO_RETRY, check_combat=False)
            if not was_available:
                return False
            if not self.echo_available():
                if i:
                    self.logger.info(f'shorekeeper: echo landed on try {i + 1}')
                return True
        return False

    def fire_liberation(self):
        """R 大招 —— 【等图标亮了再按, 不是问一次就走】。

        click_liberation() 只在"图标当下就亮着"时才真发键; 读到不亮就在
        wait_if_cd_ready(0.3秒) 里补两下就返回 False, 整个过程不到半秒,
        而她刚打完重击、正在收招, 能量环那几帧最不可信。
        等的过程中照样打普攻 —— 普攻自己也在攒能量。
        顺手清 task.in_liberation: 被上一个角色留成 True 的话, click_liberation()
        开头 `if not self.task.in_liberation:` 会把发键整段跳过。
        """
        self.task.in_liberation = False
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.LIB_WINDOW:
            self.task.next_frame()
            if self.liberation_available():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if self.click_liberation(wait_if_cd_ready=0.3, click_f=False):
                    self.logger.info(f'shorekeeper: liberation fired after {waited:.2f}s')
                    return True
            self.check_combat()
            self.click()
            self.sleep(self.LIB_RETRY)
        self.logger.warning(f'shorekeeper: liberation icon never lit within '
                            f'{self.LIB_WINDOW}s, leaving without it')
        return False

    def dodge(self):
        """闪避 = 右键。这条轴上只用来断启动轮那一下回路重击的后摇。"""
        self.task.click(key='right')

    # ------------------------------------------------------------------ 离场

    def leave(self):
        """【硬 gate】协奏没满不许走, 然后按数字键直接切仇远。

        这套轴是靠变奏/延奏一棒接一棒串起来的: 她没带满协奏走人, 仇远就拿不到
        入场那 400 挑灯问剑, 强化重击整段都出不来。
        这道闸【要给上限】: 协奏满不满取决于打没打到怪, 怪被清了就永远等不到;
        给了上限至少还能把大招和延奏往下传, 下一圈自己会补回来。
        """
        con_ok = self.require('守岸人 协奏满', self.is_con_full,
                              action=self.click, timeout=self.CON_GATE_TIMEOUT)
        if con_ok:
            # 原版 ShoreKeeper.switch_next_char() 里的记账 —— 直接按数字键切人绕开了它。
            # 队友的 shorekeeper_auto_dodge() 靠这两个字段决定还能不能替人闪避
            self.outrotime = time.time()
            self.dodge_count = 5
        for attempt in range(1, self.LEAVE_TRIES + 1):
            # 大招/重击的演出期间队伍 HUD 整块不渲染, in_team() 认不出人, 直接按数字键
            # 会白按满 SWITCH_TIMEOUT 然后判失败
            self.wait_team_hud()
            if self.direct_switch(assume_intro=con_ok):
                return
            self.logger.warning(f'shorekeeper: direct switch failed '
                                f'(第 {attempt}/{self.LEAVE_TRIES} 次), retrying')
        self.logger.warning('shorekeeper: falling back to framework switch, rotation may drift')
        self.switch_next_char()

    def wait_team_hud(self, timeout=None):
        """等右侧队伍头像的编号渲染回来 —— 演出期间它整块是不画的。"""
        timeout = self.TEAM_HUD_WAIT if timeout is None else timeout
        start = time.time()
        while time.time() - start < timeout:
            if self.task.in_team()[0]:
                waited = time.time() - start
                if waited > 0.2:
                    self.logger.info(f'shorekeeper: team hud back after {waited:.2f}s')
                return True
            self.task.next_frame()
            self.sleep(0.08, check_combat=False)
        self.logger.warning(f'shorekeeper: team hud still unreadable after {timeout:.1f}s')
        return False

    # ------------------------------------------------------------------ 小工具

    def switch_in_anchor(self):
        """本轮的计时基准: 头像认出「切人完成」的那一刻, 不是 do_perform 被调那一刻。"""
        now = time.time()
        anchored = self.last_switch_in_time
        gap = now - anchored if anchored > 0 else -1
        if 0 <= gap <= self.SWITCH_ANCHOR_MAX:
            return anchored
        return now
