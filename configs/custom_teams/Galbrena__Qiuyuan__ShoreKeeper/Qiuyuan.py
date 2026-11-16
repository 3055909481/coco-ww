import time

from src.char.Galbrena import Galbrena as NativeGalbrena
from src.char.Qiuyuan import Qiuyuan as NativeQiuyuan


class Qiuyuan(NativeQiuyuan):
    """仇远 —— 嘉仇守的第二棒, 启动轮和循环轮对他是【同一条轴】。

    轮换环: 嘉贝莉娜 -> 守岸人 -> 仇远 -> 嘉贝莉娜。

        2 段强化A -> (回路满) R -> 长按打 3 段强化重击 -> 长按E -> 长按声骸 -> 变奏嘉贝

    【这条轴的资源账 —— 官方帧表(角色-男 r503~r622)】:
      * 挑灯问剑上限 600。变奏入场(QTE) 直接给 400, 强化A 每段 +100 ——
        所以入场两下强普正好凑到 600, 这就是"打满回路能量"。
      * 挑灯问剑满 600 才进 8 秒醉墨状态, 【醉墨状态期间才解锁强化重击】。
        所以顺序只能是: 先打满 -> 再开大 -> 再长按出强化重击。
      * 强化重击 1/2/3 每段前置 -200, 三段正好把 600 清空, 醉墨随之结束。
      * 强化重击3 协奏回收 7.01, 被动 1 还额外给 30 协奏 —— 这就是"协奏值猛涨一截"
        的来源, 也是第三段真的打出去了的判据。
      * 长按E 走的是 E2-不辞远(短按是 E1)。E2 的伤害脱手后角色会进入奔袭,
        所以"往前冲出去"= 伤害已经出完了。
      * 长按声骸走的是无常凶鹭的喷火分支: 声骸表写着"第 0.2 秒检测到长按将自行
        跳转至喷火", 喷火发生帧 50F(0.83 秒)、整段 205F(3.42 秒)。
        幻形后首次命中回复自身 10 点共鸣能量 —— 视频里说的"拿到 10 点能量"就是它;
        变身后 15 秒内放延奏还能给下一个变奏登场的角色 +12% 伤害, 所以喷火完立刻切嘉贝。

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

    # ================================================================ 嘉仇守: 仇远这一棒

    # ---- 时间常量。除注明外都是官方帧表换算 (60fps, 帧/60 = 秒), 单位秒 ----
    # 变奏入场: QTE 不响应输入、第 70F 前不能切人。内置 Qiuyuan.do_perform 里那个
    # continues_normal_attack(1.17) 就是这个数
    INTRO_ANIM = 1.17
    A_COUNT = 2            # 强化A 两段 = +200, 加上入场的 400 正好 600
    A_INTERVAL = 0.55      # 强化A1-2 派生 36F / 强化A2-3 派生 56F
    FORTE_WAIT = 4.0       # 等挑灯问剑攒满(= 长按提示亮)的上限, 期间继续补普攻
    LIB_WINDOW = 3.0       # 等大招图标亮的窗口
    LIB_RETRY = 0.12
    LIB_TO_HEAVY = 0.20    # 大招演出由 click_liberation 自己等完, 这里只留衔接
    # 【处决放在仇远 R 的演出里】(2026-09-04 用户要求): click_liberation(click_f=True)
    # 会在大招演出期间【每 0.1 秒发一次 F】—— 这正是"R 动画期间就开始按 F"。
    # 这条轴上只有他一个人开 click_f, 守岸人和嘉贝都还是 False:
    # 处决是全队共用的资源, 谁都发 F 的话会被最先放大招的那个吃掉。
    LIB_CLICK_F = True
    # 处决动画期间队伍 HUD 也不渲染, click_liberation 是"HUD 一回来就返回"的,
    # 所以后面要再等一次 —— 不等的话长按重击会被处决的动画整个吃掉。
    # 没触发处决时 HUD 本来就在, 这一步是 0 成本
    LIB_BREAK_HUD_WAIT = 3.0
    # 【演出结束之后还要继续追一段 F】(2026-09-04 用户实机: "有时候仇远没处决到")。
    # click_liberation 里那个发 F 的循环条件是 `while not in_team()`, 也就是【只覆盖
    # 演出本身】, HUD 一回来就停。而他大招的伤害落在第 210F、演出总共 230F ——
    # 破韧是最后 0.33 秒才发生的, 处决提示往往演出刚结束才亮, 正好错过。
    BREAK_WINDOW = 1.0     # 演出结束后继续连发 F 的时长 (用户: R 结束后 1 秒)
    BREAK_F_INTERVAL = 0.12
    # 按住普攻打完 3 段强化重击的上限: 强化重击1 结束 136F + 2 结束 20F + 3 结束 60F
    # = 216F ≈ 3.6 秒。判据是长按提示消失(挑灯问剑清零), 这个数只是防判据失灵
    HEAVY_MAX = 5.0
    # 【先无条件按满这么久, 之后才允许靠判据提前收手】(2026-09-04 用户: "就算没有F
    # 也要照常打重击, 消耗完三段回路")。
    # hold_until() 是【进来先查一次条件】的 —— "挑灯问剑提示消失"这一帧要是没读到,
    # 它当场就返回, finally 立刻松手, 三段重击一下都没打出去。
    # 三段按派生帧累加: 强化重击1 派生92F + 重击2 派生14F + 重击3 结束60F = 166F ≈ 2.77 秒,
    # floor 取 2.5 (比它略小, 剩下的交给判据; 判据一直读不出来就按到 HEAVY_MAX)
    HEAVY_MIN = 2.5
    HEAVY_TO_E = 0.15
    E_HOLD_MIN = 0.35      # 至少按住这么久再去看冷却, 见 hold_resonance()
    E_HOLD_MAX = 1.6       # 按住 E 等冷却出现的上限
    # E -> 声骸。【0 是有意的】(2026-09-04 用户实机): E 一进冷却就能按 Q 了。
    # hold_resonance() 的退出条件本来就是"读到 E 的冷却数字", 那一刻伤害已经脱手,
    # 后面再加固定延时纯粹是白站 —— 原来填的 0.60 是照 E2-3 派生 30F 猜的余量
    E_TO_ECHO = 0.0
    # 长按声骸: 0.2 秒是游戏判定长按的门槛, 0.83 秒(50F) 是喷火的发生帧 ——
    # 按满 1 秒确保喷火已经起手。视频里 UP 按了 0.5~2.0 秒不等, 按更久只是多站着
    ECHO_HOLD = 1.00
    # 幻形结束(队伍 HUD 回来)之后到按切人键的间隔。
    # 2026-09-04 用户实机: 这一段可以再快 0.2 秒 -> 0。HUD 回来本来就是
    # "变身演完了"的真信号, 后面再加固定延时是没信号时代的产物
    ECHO_TO_LEAVE = 0.0
    # 幻形期间队伍 HUD 整块不渲染, 也不能切人。整段 205F ≈ 3.42 秒, 给到 4.5 秒
    BIRD_HUD_WAIT = 4.5
    # 协奏没满不许走: 他带不带变奏走人, 决定嘉贝跑爆发轴还是只丢个 Q 就交回去
    CON_GATE_TIMEOUT = 12.0
    CON_LOG_EVERY = 3.0
    LEAVE_TRIES = 2
    TEAM_HUD_WAIT = 2.5
    SWITCH_ANCHOR_MAX = 1.5
    # 【PC 党的预输入写法, 默认关】: 把长按E 挪到大招演出里预输入, 大招一结束 E 就
    # 已经进冷却, 接着直接长按出强化重击, 能省掉轴尾那一段。
    # 默认不开是因为演出期间的按键会被游戏丢弃、不排队 —— 人手按得住 1 秒多,
    # 宏走 PostMessage 只投一次按下, 中间隔着输入采样, 落空了这一轮就没有 E。
    # 想试的话把它打开, 长按会挪到 fire_liberation() 之前
    PREINPUT_E_IN_LIB = False

    # 长按声骸幻形期间要把战斗判定顶住 —— 变身时队伍 HUD 和锁定目标都读不到,
    # 判定线程会当成脱战, 于是重进战斗、重跑启动轴
    _in_bird = False

    @property
    def NEXT_SLOT(self):
        """打完切嘉贝莉娜。按【类】找人而不是写死队伍位置。"""
        for char in self.task.chars:
            if isinstance(char, NativeGalbrena):
                return char.index + 1
        return None

    def skip_combat_check(self):
        """幻形成无常凶鹭期间顶住战斗判定, 见 _in_bird 的注释。"""
        return self._in_bird or super().skip_combat_check()

    def reset_state(self):
        super().reset_state()
        # 【必须写在 reset_state 里, 光写 __init__ 保不住】: CharFactory 换类时会用
        # 内置实例的 __dict__ 覆盖掉同名字段。他不是治疗, 内置值是 True,
        # 不重申的话框架会在每次切人时替他按一下 F
        self.check_f_on_switch = False
        self._in_bird = False

    def on_combat_end(self, chars):
        self._in_bird = False
        self.reset_fixed_rotation(chars)
        super().on_combat_end(chars)

    # ------------------------------------------------------------------ 轴

    def do_perform(self):
        intro = self.take_intro()
        self.check_f_on_switch = False
        anchor = self.switch_in_anchor()
        if intro:
            self.sleep(max(0.0, anchor + self.INTRO_ANIM - time.time()), check_combat=False)
        if self.flying():
            self.wait_down()
        self.logger.info(f'qiuyuan axis start (intro={intro})')

        # ---- 2 段强化A, 把挑灯问剑打到 600 ----
        # 空手入场时少了 QTE 那 400, 这两下攒不满 —— 下面那道回路闸会自己接着补普攻
        self.attack_chain(self.A_COUNT, self.A_INTERVAL)

        # ---- 回路满(醉墨) -> 大招 ----
        # 【顺序不能换】: 醉墨只有 8 秒, 而强化重击要在醉墨里才解锁; 大招是全局时停,
        # 演出不烧醉墨的时间, 所以"打满再开大"比"先开大再打满"稳
        self.require('仇远 挑灯问剑满', self.is_mouse_forte_full,
                     action=self.click, timeout=self.FORTE_WAIT)
        if self.PREINPUT_E_IN_LIB:
            self.hold_resonance()
        self.fire_liberation()
        self.chase_break()
        self.sleep(self.LIB_TO_HEAVY)

        # ---- 长按打 3 段强化重击 ----
        self.forte_heavy()

        # ---- 长按E ----
        if not self.PREINPUT_E_IN_LIB:
            self.sleep(self.HEAVY_TO_E)
            self.hold_resonance()

        # ---- 长按声骸(喷火) -> 变奏嘉贝 ----
        if self.E_TO_ECHO:
            self.sleep(self.E_TO_ECHO)
        self.bird_and_leave()

    # ------------------------------------------------------------------ 动作

    def fire_liberation(self):
        """R 大招 —— 【在窗口里等图标亮了再按, 不是问一次就走】。

        click_liberation() 只在"图标当下就亮着"时才真发键, 读到不亮就在
        wait_if_cd_ready 里补两下返回 False, 整个过程只有 0.13~0.47 秒 ——
        而他这时候刚变奏入场没多久, 能量环读数最不可信。
        等的过程中一边补普攻: 普攻自己也在攒能量, 而且强化A 还能继续垫挑灯问剑。
        顺手清 task.in_liberation, 否则 click_liberation() 开头会把发键整段跳过。
        click_f 走 LIB_CLICK_F(默认 True): 演出期间每 0.1 秒发一次 F ——
        这条轴的处决就安排在他这一发大招里, 见 LIB_CLICK_F 的注释。
        """
        self.task.in_liberation = False
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.LIB_WINDOW:
            self.task.next_frame()
            if self.liberation_available():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if self.click_liberation(wait_if_cd_ready=0.3, click_f=self.LIB_CLICK_F):
                    self.logger.info(f'qiuyuan: liberation fired after {waited:.2f}s')
                    return True
            self.check_combat()
            self.click()
            self.sleep(self.LIB_RETRY)
        self.logger.warning(f'qiuyuan: 大招图标 {self.LIB_WINDOW}s 内没亮, 硬按一次')
        return self.click_liberation(wait_if_cd_ready=0.5, click_f=self.LIB_CLICK_F)

    def chase_break(self):
        """大招演出结束之后继续连发 F 抢处决, 触发了就等它演完。

        【为什么演出里那几下不够】: click_liberation(click_f=True) 的发 F 循环是
        `while not in_team()`, 只覆盖演出本身; 而他大招的伤害落在第 210F、演出
        总共 230F —— 破韧是最后 0.33 秒才发生的, 处决提示往往演出刚结束才亮,
        那时 F 已经停了。所以这里再追 BREAK_WINDOW 秒。

        【不用 task.f_break()】两个原因:
          1. 它第一行是 `if can_break or check_f_break()`, 提示晚亮 0.1 秒这次调用
             就整个跳过; 而且宏跑起来时战斗判定线程不刷新 can_break, check_f_break()
             里屏幕中央那一路找图外面还裹着 1 秒频率限制 —— 十次有九次被跳过。
          2. 它会阻塞 0.5~5 秒并【连打左键】, 而后面紧接着是长按强化重击,
             左键被插进来会把长按打断。
        裸发 F 没有这些副作用 —— 没有处决可打的时候按 F 也不触发别的东西。

        【等的这段顺手补普攻】: 醉墨里普攻不再给挑灯问剑, 但也不亏, 总比站着强。
        """
        start = time.time()
        fired = False
        while time.time() - start < self.BREAK_WINDOW:
            self.task.next_frame()
            if not self.task.in_team()[0]:
                # 队伍 HUD 没了 = 处决(或别的演出)开始了, 别再发 F
                fired = True
                break
            self.task.send_key('f')
            self.click()
            self.sleep(self.BREAK_F_INTERVAL, check_combat=False)
        self.logger.info(f'qiuyuan: 追处决 {time.time() - start:.2f}s, '
                         f'{"触发了" if fired else "没等到提示"}')
        # 处决的动画同样会把队伍 HUD 顶掉, 等它回来再往下走 ——
        # 不等的话长按重击会被处决动画整个吃掉。没触发时 HUD 本来就在, 0 成本
        self.wait_team_hud(self.LIB_BREAK_HUD_WAIT)
        # 不清掉的话框架切人时会再按一次 F
        self.task.can_break = False
        # 【没触发处决也照常往下走】: 这里只负责抢 F, 抢没抢到都不影响后面的
        # 三段强化重击 —— 那是靠 forte_heavy() 自己保底的, 见 HEAVY_MIN
        return fired

    def forte_heavy(self):
        """按住普攻打 3 段强化重击, 打到挑灯问剑清空为止。

        判据是长按提示消失 —— 三段各消耗 200, 打完正好清零、醉墨结束、提示灭掉,
        所以"提示没了"就是"三段都打出去了", 比数段数可靠(被打断也不会错位)。
        【按住期间一个键都不发】: PostMessage 后端的 mouse_down()/send_key() 都会投
        一条 WM_ACTIVATE, 窗口收到就把已经按住的左键松开。
        """
        if not self.is_mouse_forte_full():
            self.logger.warning('qiuyuan: 挑灯问剑没读到满, 还是按住试一下')
        self.task.mouse_down()
        try:
            # 【先按满 HEAVY_MIN 再看判据】—— 见 HEAVY_MIN 的注释。
            # 按住期间只 sleep, 不发任何键: PostMessage 后端每发一次键都会投一条
            # WM_ACTIVATE, 窗口收到就把已经按住的左键松开
            self.sleep(self.HEAVY_MIN)
            self.hold_until(lambda: not self.is_mouse_forte_full(),
                            max(0.0, self.HEAVY_MAX - self.HEAVY_MIN),
                            '仇远 3 段强化重击打完')
        finally:
            self.task.mouse_up()
        self.sleep(0.05)
        return True

    def hold_resonance(self):
        """长按 E(E2-不辞远), 按到图标出现冷却数字为止。

        【判"冷却出现"之前先确保它不在冷却】—— 读数抖一下就会让 wait_until 立刻成立,
        E 等于只点了一下按成短按的 E1。E_HOLD_MIN 是给这个兜底的最小按住时长。
        按到出冷却就松手: 再按下去他会进奔袭, 那一段是跑路不是输出, 还吃体力。
        """
        key = self.get_resonance_key()
        self.task.send_key_down(key)
        try:
            self.sleep(self.E_HOLD_MIN, check_combat=False)
            landed = self.task.wait_until(lambda: self.has_cd('resonance'),
                                          time_out=self.E_HOLD_MAX - self.E_HOLD_MIN)
        finally:
            self.task.send_key_up(key)
        self.record_resonance_use()
        if landed:
            self.logger.info('qiuyuan: E 已进冷却, 伤害脱手')
        else:
            self.logger.warning(f'qiuyuan: E 冷却 {self.E_HOLD_MAX}s 内没读到, 继续往下走')
        self.sleep(0.05)
        return landed

    def bird_and_leave(self):
        """长按声骸幻形喷火, 等变身结束再变奏嘉贝。

        整段要顶住战斗判定: 幻形期间队伍 HUD 和锁定目标都读不到, 判定线程会当成
        脱战 -> 重进战斗 -> 重跑启动轴(那时所有大招都在冷却上, 整条轴打空)。
        变身结束的判据用【队伍 HUD 回来】, 不用死等 —— 打不到怪时喷火会提前结束。
        """
        self._in_bird = True
        try:
            self.hold_echo()
            self.wait_team_hud(self.BIRD_HUD_WAIT)
        finally:
            self._in_bird = False
        if self.ECHO_TO_LEAVE:
            self.sleep(self.ECHO_TO_LEAVE)
        self.leave()

    def hold_echo(self):
        """长按声骸键 —— 短按是砸地, 长按才是喷火(声骸表: 第 0.2 秒检测到长按跳转)。

        【先发键, 读屏只用来记日志】: 读到"在冷却"就不按的话, 读错一次这一轮就没有
        声骸; 按在冷却上只是空操作, 不要钱。
        """
        if not self.echo_available():
            self.logger.info('qiuyuan: echo reads on-cd, holding anyway')
        key = self.get_echo_key()
        self.task.send_key_down(key)
        try:
            self.sleep(self.ECHO_HOLD, check_combat=False)
        finally:
            self.task.send_key_up(key)
        self.record_echo_use()
        self.logger.info(f'qiuyuan: 长按声骸 {self.ECHO_HOLD:.2f}s (喷火)')

    def dodge(self):
        """闪避 = 右键。这条轴上他没有要断的后摇, 留着给以后调轴用。"""
        self.task.click(key='right')

    # ------------------------------------------------------------------ 离场

    def leave(self):
        """协奏满了再走, 然后按数字键直接切嘉贝莉娜。

        他带不带变奏走人决定嘉贝跑哪条分支: 没变奏的话嘉贝只会丢个 Q 就把场子
        交回守岸人, 整圈的爆发都没了。所以这道闸给得很松。
        """
        con_ok = self.fill_con()
        for attempt in range(1, self.LEAVE_TRIES + 1):
            self.wait_team_hud()
            if self.direct_switch(assume_intro=con_ok):
                return
            self.logger.warning(f'qiuyuan: direct switch failed '
                                f'(第 {attempt}/{self.LEAVE_TRIES} 次), retrying')
        self.logger.warning('qiuyuan: falling back to framework switch, rotation may drift')
        self.switch_next_char()

    def fill_con(self):
        """等协奏满 —— 期间盯着挑灯问剑, 又满了就再补一发强化重击。

        被打断的那几轮光点普攻攒协奏太慢, 而强化重击3 一段就给 7 协奏回收 + 被动
        额外 30 协奏能量, 是他攒得最快的手段。等的时间顺便也变成了输出。
        给上限是有意的: 怪被清了就永远等不到, 而下一棒(嘉贝)有空手入场的分支能自愈。
        """
        start = time.time()
        last_log = 0.0
        while True:
            if self.is_con_full():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if waited > 0.3:
                    self.logger.info(f'qiuyuan: 协奏满 after {waited:.1f}s')
                return True
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            left = self.CON_GATE_TIMEOUT - elapsed
            if left <= 0:
                self.logger.warning(f'qiuyuan: 协奏没满就到点了 '
                                    f'(con={self.get_current_con():.2f}), 走人')
                return False
            if elapsed - last_log >= self.CON_LOG_EVERY:
                last_log = elapsed
                # 【卡在这里几乎只有一个原因】: 协奏全是强化重击3 给的(它自己协奏回收
                # 7.01, 被动1 命中后再给 30 点协奏能量), Q 一点不给。所以协奏没满
                # 等于三段强化重击没真打出去 —— 去看上面 forte_heavy 那段日志
                self.logger.warning(f'qiuyuan: 还在等协奏 ({elapsed:.1f}s, '
                                    f'con={self.get_current_con():.2f}) —— '
                                    f'多半是三段强化重击没打出去, 继续打')
            if self.is_mouse_forte_full():
                self.logger.info('qiuyuan: 等协奏的时候挑灯问剑又满了, 补一发强化重击')
                self.task.mouse_down()
                try:
                    self.hold_until(lambda: not self.is_mouse_forte_full(),
                                    min(self.HEAVY_MAX, left), '仇远 补的强化重击')
                finally:
                    self.task.mouse_up()
                self.sleep(0.05)
                continue
            self.check_combat()
            self.click()
            self.sleep(self.A_INTERVAL)

    def wait_team_hud(self, timeout=None):
        """等右侧队伍头像的编号渲染回来 —— 演出/幻形期间它整块是不画的。"""
        timeout = self.TEAM_HUD_WAIT if timeout is None else timeout
        start = time.time()
        while time.time() - start < timeout:
            if self.task.in_team()[0]:
                waited = time.time() - start
                if waited > 0.2:
                    self.logger.info(f'qiuyuan: team hud back after {waited:.2f}s')
                return True
            self.task.next_frame()
            self.sleep(0.08, check_combat=False)
        self.logger.warning(f'qiuyuan: team hud still unreadable after {timeout:.1f}s')
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
