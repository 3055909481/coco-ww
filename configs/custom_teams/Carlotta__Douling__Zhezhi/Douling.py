import time

from src.char.BaseChar import SwitchPriority
from src.char.Douling import Douling as NativeDouling
from src.char.Zhezhi import Zhezhi as NativeZhezhi


class Douling(NativeDouling):
    """卜灵 —— 柯折卜的开阵人, 一圈只上场一次, 而且是【平切】进来的。

    轮换环: 折枝(E+处决) -> 卜灵 -> 折枝(主力段) -> 珂莱塔 -> 折枝(E+处决) -> ...

        整段 (平切入场)  4a -> E -> a -> 长按重击(两段) -> 声骸 -> 大招 -> 变奏折枝

    【为什么是这个顺序 —— 官方帧表(角色-女 r4181~r4239)的资源规则】:
      * 卦象只有两种: A2 命中给【艮】, A4 命中给【震】, E-前置第1F 也给【震】。
        所以 4a 打完手上正好是「艮 + 震」两个卦象(视频字幕原话:
        "卜灵4a 图标出现两个卦象后按共鸣技能接a")。
      * 重击【一次吃掉两个卦象】, 吃到什么决定产出什么:
          艮震 / 震艮 -> 少阳
          艮艮 / 震震 -> 少阴 + 全队回血(这队的奶就是这么来的, 所以能解放维里奈/守岸人)
      * 于是 E(+震) 再接一下 a(A4 由E派生, +震) 把队列凑成「艮 震 震 震」:
        第一段重击吃 艮+震 出少阳, 第二段吃 震+震 出少阴 —— 同时拥有少阳少阴
        就是【阴阳相生】。
      * 帧表 大招-前置写着「处于阴阳相生状态时, 第236F获得24秒开阵状态、移除阴阳相生」。
        开阵状态监听全队变奏施放: 第1次 +10%, 第2次再 +15% 前台角色共鸣技能类型伤害。
        这一圈后面正好两次变奏(卜灵->折枝、折枝->珂莱塔), 珂莱塔吃满两层。
      * 所以【大招必须放在这一棒的最后】, 而且后面那两棒都得带变奏 —— 少一次监听
        珂莱塔就少 15%。

    轴出自 E:\\轴视频\\柯折卜，全网最详细教学！矩阵杀手，解放维里奈和守岸人。.mp4
    左侧「启动轴」/「循环轴」两块字幕面板(视频 25 秒 / 125 秒)。动作时机对着视频
    163.6~172 秒那一段实时录像核过(该段倒计时与视频秒数 1:1, 前面的教学段是 0.55 倍速)。
    """
    # ================================================================ 固定顺序切人 (公共段)
    # 这一段在 Carlotta.py / Douling.py / Zhezhi.py 三个文件里逐字一样, 改一处记得同步改三处。
    # (拷自 Galbrena__Qiuyuan__ShoreKeeper, 只多了 note_handoff_source 那一组 ——
    #  折枝一圈上场两次, 必须知道是谁把棒交过来的才分得清跑哪一段。)
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

    # 上一棒是谁交过来的 (角色类名), 以及打这个标记的时刻。
    # 【为什么 has_intro 不够用】: 柯折卜这一圈里折枝要上场两次 —— 科莱塔交来的那一次
    # 只做"E + 处决"然后走卜灵, 卜灵交来的那一次才是带大招的主力段。两次都带变奏,
    # 光看 has_intro 分不出是哪一次, 分错了整圈的方向就反了。
    # 保质期跟 intro_pending 用同一套理由 (见下面 intro_pending_at 的注释)。
    handoff_source = None
    handoff_source_at = 0.0

    def note_handoff_source(self, source):
        """被 handoff_to() 交接进场时调用 —— 记下上一棒是谁。带不带变奏都记。"""
        self.handoff_source = source.__class__.__name__ if source is not None else None
        self.handoff_source_at = time.time()

    def take_handoff_source(self):
        """读出上一棒是谁, 读完就清。过期的不认, 返回 None。"""
        name = self.handoff_source
        if name is not None and time.time() - self.handoff_source_at > self.INTRO_PENDING_TTL:
            self.logger.info(f'handoff_source ({name}) 已过期, 按未知处理')
            name = None
        self.handoff_source = None
        self.handoff_source_at = 0.0
        return name

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
            char.handoff_source = None
            char.handoff_source_at = 0.0

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
        if hasattr(target, 'note_handoff_source'):
            target.note_handoff_source(self)
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

    # ================================================================ 柯折卜: 卜灵这一棒

    # ---- 时间常量。除注明外都是官方帧表换算 (60fps, 帧/60 = 秒), 单位秒 ----
    # 她【正常是平切进来的】(轴上写的是"卜灵"不是"变奏卜灵"), 这个常量只在
    # 上一棒协奏刚好满了、白捡到一次变奏时才用得上。
    # QTE: 第70F前不响应输入, 第60F前不能切人
    INTRO_ANIM = 1.17

    # ---- 4a: 把「艮 + 震」两个卦象打出来 ----
    # A1 派生 25F / A2 动作结束 64F / A3 三段共 100F / A4 派生 57F。
    # 点按有输入缓存, 不必等满派生帧; 视频里这四下一共约 2.0 秒
    A_COUNT = 4
    A_INTERVAL = 0.48
    A_TO_E = 0.15

    # ---- E -> a ----
    # E-前置「第38F改变中断优先级」= 0.63 秒后才轮得到下一个动作。
    # 这一下 a 走的是帧表里那条独立的「A4 由E派生」(派生 37F), 也是给第三个【震】的
    E_TO_A = 0.55
    E_CAST_TIMEOUT = 1.5
    # 点完 a 就把左键按住 —— 长按到 A4 的派生帧那一刻重击自己就接上了(预输入)
    A_TO_HEAVY = 0.45

    # ---- 长按重击(两段) ----
    # 重击-前置写着「第40F获得少阳/少阴、协奏、移除2个卦象」, 派生帧 60F。
    # 也就是: 第一段在 40F 结算, 第二段起于 60F、结算在绝对 100F ≈ 1.67 秒。
    # 【所以按住时间必须盖过 1.67 秒】—— 少阴那一半没结算, 阴阳相生就凑不齐,
    # 大招退化成常态伤害、也不给开阵状态, 后面珂莱塔那 25% 直接没了。
    # 松手不影响已经打出去的部分(帧表 P=√ 可脱手), 所以宁可多按一点
    HEAVY_HOLD = 2.05
    HEAVY_TO_ECHO = 0.10
    ECHO_TO_LIB = 0.10

    LIB_WINDOW = 3.5
    LIB_RETRY = 0.12
    ECHO_TRIES = 3
    ECHO_RETRY = 0.15
    FILL_CON_INTERVAL = 0.20
    # ---- 协奏保底: 【没打满不许走】 ----
    # 视频原话「保险起见看一下协奏, 满了就切折纸」。
    #
    # 【她这一棒天然攒不满, 必须补刀】(帧表 U 列 = 协奏回收):
    #   4a 17.61(A1 1.67 + A2 2.7 + A3三段 5.7 + A4 7.54) + E 7.5 + a 7.54
    #   + 重击x2 30 + 大招 0  =  **62.65 / 100**, 天然还差 37.35,
    #   也就是打完标准轴还得再补 2.1 轮 A1-A4 连段(每轮 17.61, 约 1.9 秒)。
    #   原来这道闸给的是 8 秒, 理论上够(2.1 轮 ≈ 4 秒), 但只要 boss 位移、打空几下,
    #   或者能量环读数滞后, 就会"到点没满也走" —— 2026-09-06 用户要求改成打满才切。
    #
    # 【为什么值得为它多站几秒】: 她这一棒的产出是 24 秒开阵状态, 而开阵是靠
    # 【变奏】触发监听的。没带满协奏走人, 折枝那次入场不算一次监听, 珂莱塔就从
    # 吃满 +10%+15% 掉成只吃 +10% —— 补刀那几秒远比这一层便宜。
    CON_GATE_TIMEOUT = 30.0   # 【兜底防挂死, 不是节奏控制】—— 正常永远走不到这儿
    CON_LOG_EVERY = 3.0
    # 打不到怪时的快速出口: 协奏读数这么久【一点没涨】, 说明不是"还没打够"而是
    # "根本打不到"(boss 位移/无敌/已经被清)。这种情况再站下去也不会满,
    # 而下一棒折枝本来就有拿不到变奏也能跑的分支, 走人能自愈、卡死不能。
    # 【它比原来那个 8 秒上限还更早放手】—— 换来的是"能打到就一定打满"
    CON_STALL_WINDOW = 6.0
    CON_STALL_EPS = 0.02      # 能量环读数会抖, 涨幅小于这个不算涨
    #
    # 【补刀时优先长按重击】(2026-09-06 用户: "卜灵A会获得卦象, 有两个卦象强化攻击
    # 就亮了, 最后如果协奏不满但是有强化攻击先长按释放, 不亮就平A"):
    #   重击的协奏回收是 U=15, 比整整一轮 A1-A4 连段(17.61)只少一点, 却只要 0.75 秒 ——
    #   补协奏效率最高的动作。
    #   而且它顺手把 2 个卦象吃掉: 补刀打的是 A1-A4, A2 给艮、A4 给震, 每轮攒下一对,
    #   不清掉就会带进下一圈 —— 那时 4a 之后队列变成「艮震艮震…」, 两次重击都吃到
    #   艮震、都出少阳, 【少阴出不来, 阴阳相生就凑不齐了】。
    #   判据用鼠标长按提示(is_mouse_forte_full)= 用户说的"强化攻击亮了", 亮才按 ——
    #   【这条很要紧】: 没卦象时长按出的是帧表里那个「重击-鬼门占卦」, 会自伤,
    #   而且第57F前不能切人。
    # 按到第一段结算(第40F 拿协奏/少阳少阴/移除2卦象)之后、派生帧(60F)【之前】松手,
    # 免得接出第二段 —— 那时卦象已经清空, 第二段就是鬼门占卦
    FILL_HEAVY_HOLD = 0.75
    TEAM_HUD_WAIT = 3.0
    LEAVE_TRIES = 2
    SWITCH_ANCHOR_MAX = 1.5

    @property
    def NEXT_SLOT(self):
        """打完交折枝。按【类】找人而不是写死队伍位置。"""
        for char in self.task.chars:
            if isinstance(char, NativeZhezhi):
                return char.index + 1
        return None

    def reset_state(self):
        super().reset_state()
        # 【必须写在 reset_state 里, 光写 __init__ 保不住】: CharFactory 换类时是先建新
        # 实例再 replacement.__dict__.update(内置实例.__dict__), 凡是"内置也有、我改了值"
        # 的字段都会被静默打回内置值。
        # _segment 是内置卜灵那套"分两次上场"的状态机, 这里整段在一次上场里跑完,
        # 留着它只会在退回内置逻辑时把轴切一半
        self._segment = 1
        # 她登记的是 HEALER, BaseChar.__init__ 里 check_f_on_switch 已经是 False。
        # 再写一遍是防上游改默认值 —— 这条轴上处决是折枝那一棒的活, 她按 F 只会
        # 把长按重击整段打断(f_break 会阻塞并连打左键)
        self.check_f_on_switch = False

    def on_combat_end(self, chars):
        self.reset_fixed_rotation(chars)
        super().on_combat_end(chars)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """把内置那条「离开不到 8 秒就不许切回来」的限制拿掉。

        内置版是给它自己那套两段式轮换用的; 这条轴是死顺序, 一圈 35 秒左右,
        正常也撞不到 8 秒, 但 direct_switch 失败退回框架切人时会被它挡住。
        """
        if self.healer_full_con_switch_locked():
            return SwitchPriority.NO
        return SwitchPriority.NORMAL

    # ------------------------------------------------------------------ 轴

    def do_perform(self):
        intro = self.take_intro()
        self.take_handoff_source()
        self.check_f_on_switch = False
        anchor = self.switch_in_anchor()
        if intro:
            # 用【切人确认那一刻】当基准, 不是这个函数被调到那一刻 —— 中间隔着框架的
            # 交接记账和任务循环, 那段延迟是变长的
            self.sleep(max(0.0, anchor + self.INTRO_ANIM - time.time()), check_combat=False)
        if self.flying():
            self.wait_down()
        self.logger.info(f'douling: 开阵轴 start (intro={intro})')
        return self.axis()

    def axis(self):
        """4a -> E -> a -> 长按重击(两段) -> 声骸 -> 大招 -> 变奏折枝。"""
        # ---- 4a: 凑出「艮 + 震」 ----
        self.attack_chain(self.A_COUNT, self.A_INTERVAL, last_interval=self.A_TO_E)

        # ---- E(+震) -> a(A4 由E派生, +震) ----
        self.cast_e()
        self.sleep(self.E_TO_A)
        self.click()
        self.sleep(self.A_TO_HEAVY)

        # ---- 长按重击: 第一段出少阳, 第二段出少阴 -> 阴阳相生 ----
        self.hold_heavy(self.HEAVY_HOLD, '重击 x2 (少阳 + 少阴)')

        # ---- 声骸 -> 大招(阴阳相生 => 强化伤害 + 24 秒开阵) ----
        self.sleep(self.HEAVY_TO_ECHO)
        self.fire_echo()
        self.sleep(self.ECHO_TO_LIB)
        self.fire_liberation()

        self.leave()

    # ------------------------------------------------------------------ 动作

    def cast_e(self):
        """E。走 click_resonance 而不是裸发键 —— 它会等技能真的进冷却再返回。

        【返回的是三元组】(clicked, duration, animated), 不是 bool。
        send_click=False: E 之后那一下 a 是有讲究的(要的是「A4 由E派生」那一条),
        让 click_resonance 自己插普攻会把派生关系打乱。
        """
        clicked = self.click_resonance(send_click=False, time_out=self.E_CAST_TIMEOUT)[0]
        if not clicked:
            # E 没放出去 = 少一个【震】, 第二段重击会变成 震+艮 又出少阳,
            # 阴阳相生凑不齐。这里只报警不重试: 重试会把整条轴的节奏拖乱,
            # 而这一圈少一次开阵下一圈自己会补回来
            self.logger.warning('douling: E 没放出去, 这一圈可能凑不齐阴阳相生')
        return clicked

    def hold_heavy(self, duration, name):
        """按住普攻打重击链。

        重击是【固定长度的动画】, 游戏不给"这一段打完了"的信号, 只能计时;
        中途松手会把当前那一段截断。
        【按住期间一个键都不发】—— PostMessage 后端的 mouse_down()/send_key() 都会
        投一条 WM_ACTIVATE, 窗口收到就把已经按住的左键松开。
        """
        self.task.mouse_down()
        try:
            end = time.time() + duration
            while time.time() < end:
                self.sleep(min(self.HOLD_POLL, max(0.0, end - time.time())))
        finally:
            self.task.mouse_up()
        self.logger.info(f'douling: {name} 按住 {duration:.2f}s')

    def fire_echo(self):
        """Q 声骸 —— 【连发, 不是只发一次】。

        演出期间的按键被游戏丢弃、不排队, 只发一次很容易整段被吃掉;
        Q 是脱手技能, 多按几下不会打断当前动作, 连发是安全的。
        先发键, 读屏只用来决定要不要重发 —— 读到"在冷却"就不发的话, 读错一次
        这一轮就没有声骸, 而按在冷却上只是空操作。
        """
        was_available = self.echo_available()
        if not was_available:
            self.logger.info('douling: echo reads on-cd, sending once anyway')
        for i in range(self.ECHO_TRIES):
            self.send_echo_key()
            self.record_echo_use()
            self.sleep(self.ECHO_RETRY, check_combat=False)
            if not was_available:
                return False
            if not self.echo_available():
                if i:
                    self.logger.info(f'douling: echo landed on try {i + 1}')
                return True
        self.logger.warning(f'douling: echo still reads available after '
                            f'{self.ECHO_TRIES} tries')
        return False

    def fire_liberation(self):
        """R 大招 —— 【在窗口里等图标亮了再按, 不是问一次就走】。

        这一发是整队伤害的总闸(开阵状态给珂莱塔的 +10%+15% 全靠它), 所以窗口给得
        比别人宽一点。
        click_liberation() 只在"图标当下就亮着"时才真发键, 读到不亮就在
        wait_if_cd_ready 里补两下返回 False —— 而这时候重击刚脱手, 能量环那几帧
        最不可信。
        顺手清 task.in_liberation: 它被上一个角色留成 True 的话, click_liberation()
        开头那个 `if not self.task.in_liberation:` 会把发键整段跳过。
        click_f=False: 大招演出期间框架默认每 0.1 秒发一次 F —— 这条轴上处决是
        折枝那一棒的活, 让它在这里被提前吃掉就白费了。
        """
        self.task.in_liberation = False
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.LIB_WINDOW:
            self.task.next_frame()
            if self.liberation_available():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if self.click_liberation(wait_if_cd_ready=0.3, click_f=False):
                    self.logger.info(f'douling: liberation fired after {waited:.2f}s')
                    return True
            self.check_combat()
            self.click()
            self.sleep(self.LIB_RETRY)
        self.logger.warning(f'douling: 大招图标 {self.LIB_WINDOW}s 内没亮, 硬按一次')
        return self.click_liberation(wait_if_cd_ready=0.5, click_f=False)

    # ------------------------------------------------------------------ 离场

    def leave(self):
        """【硬 gate】协奏没满不许走, 然后按数字键直接切折枝。

        她这一棒的产出是 24 秒开阵状态, 而开阵是靠【变奏】触发监听的 ——
        没带满协奏走人, 折枝那次入场就不算一次监听, 珂莱塔少吃 10%,
        再传下去珂莱塔那一棒也只剩一层。
        所以这道闸是【硬的】: 打得到怪就一定打满再走(她标准轴只攒 62.65/100,
        补刀是常态, 不是异常)。放手只在"协奏根本不涨"时发生 —— 那说明打不到怪,
        再站也没用, 而下一棒折枝有拿不到变奏也能跑的分支, 能自愈。
        """
        con_ok = self.fill_con()
        target = self.char_at_slot(self.NEXT_SLOT) if self.NEXT_SLOT else None
        if target is not None and target is not self:
            for attempt in range(1, self.LEAVE_TRIES + 1):
                # 大招演出期间队伍 HUD 整块不渲染, in_team() 认不出人, 直接按数字键
                # 会白按满 SWITCH_TIMEOUT 然后判失败
                self.wait_team_hud()
                if self.switch_to_index(target.index):
                    self.handoff_to(target, con_ok)
                    self.logger.info(f'douling: 交棒折枝 (intro={con_ok})')
                    return
                self.logger.warning(f'douling: direct switch failed '
                                    f'(第 {attempt}/{self.LEAVE_TRIES} 次), retrying')
        self.logger.warning('douling: falling back to framework switch, rotation may drift')
        self.switch_next_char()

    def fill_con(self):
        """【硬 gate】协奏没打满就一直补普攻, 打满才返回。见上面常量那段的账。

        放弃只有两个理由, 都不是"时间到了":
          1. 协奏 CON_STALL_WINDOW 秒一点没涨 = 根本打不到怪, 再站也不会满;
          2. CON_GATE_TIMEOUT 那个兜底 —— 纯粹防止把整个任务挂死, 正常走不到。
        另外 check_combat() 会在脱战/任务被停时抛异常, 那是这个循环真正的出口。
        """
        start = time.time()
        last_log = 0.0
        best = self.get_current_con()
        best_at = time.time()
        while True:
            if self.is_con_full():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if waited > 0.3:
                    self.logger.info(f'douling: 协奏打满 after {waited:.1f}s')
                return True

            con = self.get_current_con()
            if con > best + self.CON_STALL_EPS:
                best = con
                best_at = time.time()
            stalled = self.time_elapsed_accounting_for_freeze(best_at)
            if stalled >= self.CON_STALL_WINDOW:
                self.logger.warning(f'douling: 协奏 {stalled:.1f}s 一点没涨 '
                                    f'(con={con:.2f}), 打不到怪, 不等了')
                return False

            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if elapsed >= self.CON_GATE_TIMEOUT:
                self.logger.error(f'douling: 协奏等了 {elapsed:.0f}s 还没满 '
                                  f'(con={con:.2f}), 触发兜底上限走人 —— '
                                  f'这条不该出现, 出现了说明补刀打不出伤害')
                return False
            if elapsed - last_log >= self.CON_LOG_EVERY:
                last_log = elapsed
                self.logger.info(f'douling: 补刀攒协奏 ({elapsed:.1f}s, '
                                 f'con={con:.2f}, 峰值 {best:.2f})')
            self.check_combat()
            if self.is_mouse_forte_full():
                # 强化攻击亮着 = 手上有两个卦象, 长按放掉最划算(见 FILL_HEAVY_HOLD)
                self.hold_heavy(self.FILL_HEAVY_HOLD, '补刀重击')
            else:
                self.click()
                self.sleep(self.FILL_CON_INTERVAL)

    def wait_team_hud(self, timeout=None):
        """等右侧队伍头像的编号渲染回来 —— 演出期间它整块是不画的。

        她的大招前置有 237 帧无敌 + 全局时停, HUD 消失得比别人久, 所以
        TEAM_HUD_WAIT 比另外两个人给得宽。
        """
        timeout = self.TEAM_HUD_WAIT if timeout is None else timeout
        start = time.time()
        while time.time() - start < timeout:
            if self.task.in_team()[0]:
                waited = time.time() - start
                if waited > 0.2:
                    self.logger.info(f'douling: team hud back after {waited:.2f}s')
                return True
            self.task.next_frame()
            self.sleep(0.08, check_combat=False)
        self.logger.warning(f'douling: team hud still unreadable after {timeout:.1f}s')
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
