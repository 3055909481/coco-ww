import time

from src.char.BaseChar import SwitchPriority
from src.char.Carlotta import Carlotta as NativeCarlotta
from src.char.Douling import Douling as NativeDouling
from src.char.Zhezhi import Zhezhi as NativeZhezhi


class Zhezhi(NativeZhezhi):
    """折枝 —— 柯折卜的转轴人, 一圈上场【两次】。

    轮换环: 折枝(E+处决) -> 卜灵 -> 折枝(主力段) -> 珂莱塔 -> 折枝(E+处决) -> ...

        启动段 (空手入场)    3a                                  -> 平切卜灵
        处决段 (珂莱塔交棒)  E, 处决                             -> 平切卜灵
        主力段 (卜灵交棒)    E, 预输入重击, 重击, 大招, 3e, 声骸  -> 变奏珂莱塔

    【为什么必须分得清是哪一次上场】: 两次都带着变奏, has_intro 一模一样, 但交棒
    对象是反的(处决段交卜灵、主力段交珂莱塔)。分错一次整圈的方向就反了, 所以
    公共段里加了 note_handoff_source(), 靠"上一棒是谁"来分。

    【为什么主力段接在卜灵后面】(官方帧表 角色-女 r4181~r4239 的资源规则):
      * 卜灵大招在阴阳相生状态下会给 24 秒【开阵状态】, 开阵期间监听全队变奏施放:
        第 1 次监听给前台角色共鸣技能类型伤害 +10%, 第 2 次再 +15%。
      * 这一圈里的两次变奏正好是"卜灵->折枝"和"折枝->珂莱塔" —— 折枝吃第 1 层,
        珂莱塔吃满 2 层。顺序换了珂莱塔就只剩 10%。

    轴出自 E:\\轴视频\\柯折卜，全网最详细教学！矩阵杀手，解放维里奈和守岸人。.mp4
    左侧「启动轴」/「循环轴」两块字幕面板(视频 25 秒 / 125 秒)。动作时机对着视频
    171~181 秒那一段实时录像核过(该段倒计时与视频秒数 1:1, 前面的教学段是 0.55 倍速)。
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

    # ================================================================ 柯折卜: 折枝这一棒

    # ---- 时间常量。除注明外都是官方帧表换算 (60fps, 帧/60 = 秒), 单位秒 ----
    # 变奏入场: QTE「不响应输入」, 第60F触发上一角色延奏, QTE-攻击发生在第66F。
    # 这一段【不点普攻】—— 入场后第一个动作是 E, 提前点会先出 A1 把 E 挤到后面
    INTRO_ANIM = 1.00

    # ---- 启动段 ----
    # A1 派生 37F / A2 派生 45F / A3 派生 60F。点按有输入缓存, 不必等满派生帧;
    # 视频里这三下一共约 1.65 秒
    OPENER_A = 3
    A_INTERVAL = 0.50
    A_TO_LEAVE = 0.15

    # ---- 主力段: E -> 预输入重击 -> 重击 -> 大招 ----
    # E1-长按前置写着「第33F前不能派生重击·构形」, 也就是 E 之后 0.55 秒才轮得到重击。
    # 「预输入」= 按下 E 之后【立刻把左键按住】, 到第33F那一帧重击自己就出来了。
    # 等看见 E 演完再按就晚了半拍, 第二段重击会掉出大招前那个窗口
    E_TO_HOLD = 0.05
    # 重击·构形-鹤影 派生 33F/34F, 两段 ≈ 0.55(E 的派生) + 0.57 + 0.57。
    # 重击是可脱手的(帧表 P=√), 按到第二段【起手】就够, 剩下的伤害松手也会打完
    HEAVY_HOLD = 1.45
    # 视频原话「重击抬手就放大取消后摇」—— 松手和按 R 之间不留间隔。
    #
    # 【这一格已经来回过一次, 别再往后调了】(2026-09-06 两次实机):
    #   0.0  -> 用户说"R开的有点早了", 于是照帧表把 R 推到"第二段重击的飞行道具发生"
    #           之后(重击·构形-鹤影 E=34/40, 绝对 1.68~1.78s, 而松手在 1.50s) = 0.25;
    #   0.25 -> 用户要求回档。也就是那笔账算错了地方: 重击·构形是【可脱手】的
    #           (帧表 P=√), 松手之后飞行道具自己会打完, 根本不用等它发生;
    #           在这里多站 0.25 秒, 后面的大招/3e/声骸整段跟着晚。
    # 真正要靠 R 取消的是【松手那一刻的收招】, 所以就是 0.0。
    # 如果以后还觉得 R 早, 该动的是 HEAVY_HOLD(第二段重击起没起手), 不是这一格
    HEAVY_TO_LIB = 0.0

    # ---- 大招之后的 3e ----
    # E 是 E1 -> E2 -> E3 -> E4 的四段链, 段首那一下是 E1, 这里补完剩下三下
    # (帧表里 E2/E3/E4 各有独立的「前置」行, 协奏回收 U=0, 是免费的后续)。
    #
    # 【折枝没有输入缓存, 三段之间必须等派生帧】: 帧表里她那一段只有「技能类型」
    # 「伤害计算」两节, 【没有「输入缓存」】—— 卜灵和守岸人那些角色才有。
    # 所以按早了就是直接丢, 不会排队。派生帧: E2 57F(0.95s) / E3 53F(0.88s) /
    # E4 72F(1.20s), 三段串起来约 3.0 秒。
    #
    # 于是这里【不数着按三下, 而是在一个窗口里一直按】: 每段的派生帧一到, 最近的
    # 那一次按键就被吃掉, 三段自然全出来; 落在动作中间的按键被游戏丢弃, 而
    # E2/E3/E4 既不吃冷却也不吃丹青, 按空是纯粹的空操作。
    E_CHAIN_RETRY = 0.12
    # 【判据是"强化E 的高亮暗下去", 不是按够几下也不是按够多久】
    # (2026-09-06 用户: "折纸的三段E有几率被打断, 这三段是强化E 技能会高亮,
    #  确保暗下去了再Q切人"):
    #   resonance_blue() 读的就是那个高亮 —— 亮着 = 叠影还没用完 = 还有 E 没放出去。
    #   被打断时它照样亮着, 所以"亮着就接着按"天然把补发也包含进去了。
    E_CHAIN_MIN = 0.35            # 只跳过大招刚演完那几帧(读数不可信), 之后立刻开始看
    E_CHAIN_DARK_CONFIRM = 3      # 连读到这么多次"暗"才认 —— 读数会抖, 抖一下就收手会少一段
    # 兜底上限: 正常都是被"暗下去"结束的, 走到这儿说明高亮判据读错了。
    # 【放宽到 6 秒是有代价的】: 万一 resonance_blue() 一直误读成亮, 多按的那几下会变成
    # 新的 E1, 而 E1 要吃 60 点丹青(帧表 V=-60)。但漏一段强化E 的损失更大, 所以宁可放宽
    E_CHAIN_WINDOW = 6.00
    E_CHAIN_TAIL = 0.35        # 等最后一段的伤害(起手后 56F)打完再放声骸
    E_CAST_TIMEOUT = 1.2

    # ---- 处决段 ----
    # 处决提示是【等出来的】: 珂莱塔那一棒把谐度打破的时刻不固定。
    # F_WINDOW 是最多等多久, F_GIVEUP 是"一直没见到提示就别再等了"的上限 ——
    # 这一棒后面接的是平切卜灵, 多等一秒卜灵就晚一秒
    F_WINDOW = 2.2
    F_GIVEUP = 1.1
    F_POLL = 0.12

    LIB_WINDOW = 3.0
    LIB_RETRY = 0.12
    ECHO_TRIES = 3
    ECHO_RETRY = 0.15
    FILL_CON_INTERVAL = 0.20
    CON_GATE_TIMEOUT = 8.0
    CON_LOG_EVERY = 3.0
    TEAM_HUD_WAIT = 2.5
    LEAVE_TRIES = 2
    SWITCH_ANCHOR_MAX = 1.5

    # 本轮交给谁 —— 由 leave() 现场写上, NEXT_SLOT 再翻成队伍位置
    _target_cls = None

    @property
    def NEXT_SLOT(self):
        """交棒对象由本轮跑的是哪一段决定。按【类】找人, 不写死队伍位置。"""
        target_cls = self._target_cls or NativeDouling
        for char in self.task.chars:
            if isinstance(char, target_cls):
                return char.index + 1
        return None

    def reset_state(self):
        super().reset_state()
        # 【必须写在 reset_state 里, 光写 __init__ 保不住】: CharFactory 换类时是先建新
        # 实例再 replacement.__dict__.update(内置实例.__dict__), 凡是"内置也有、我改了值"
        # 的字段都会被静默打回内置值。
        # char_carlotta 是内置折枝跟内置珂莱塔的「联动模式」开关, 一旦被置上,
        # do_perform() 会整个跳去 do_perform_interlock() —— 那是它们自带的另一条轴,
        # 跟这里的死顺序打架, 所以一直按 None 压着
        self.char_carlotta = None
        # 她不是治疗, 内置值是 True, 不重申的话框架会在每次切人时替她按一下 F。
        # 这条轴上的处决是【有指定位置的】(处决段, E 之后), 让框架乱按会把主力段的
        # 重击链整段打断
        self.check_f_on_switch = False
        self._target_cls = None

    def on_combat_end(self, chars):
        self.reset_fixed_rotation(chars)
        super().on_combat_end(chars)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """把内置的联动优先级拿掉 —— 内置版会在珂莱塔"准备好"时返回 MUST,
        那是给它自己那条轴用的, 会把这里的死顺序顶掉。
        (只有 direct_switch 失败、退回框架切人时才轮得到这个函数。)
        """
        if self.healer_full_con_switch_locked():
            return SwitchPriority.NO
        return SwitchPriority.NORMAL

    # ------------------------------------------------------------------ 轴

    def do_perform(self):
        source = self.take_handoff_source()
        intro = self.take_intro()
        self.check_f_on_switch = False
        anchor = self.switch_in_anchor()
        phase = self.decide_phase(source, intro)
        if intro:
            # 用【切人确认那一刻】当基准, 不是这个函数被调到那一刻 —— 中间隔着框架的
            # 交接记账和任务循环, 那段延迟是变长的
            self.sleep(max(0.0, anchor + self.INTRO_ANIM - time.time()), check_combat=False)
        if self.flying():
            self.wait_down()
        self.logger.info(f'zhezhi: {phase} 段 start (source={source}, intro={intro})')
        if phase == 'burst':
            return self.burst_axis()
        if phase == 'break':
            return self.break_axis()
        return self.opener()

    def decide_phase(self, source, intro):
        """这一次上场跑哪一段 —— 只看【上一棒是谁】。"""
        if source == 'Douling':
            return 'burst'
        if source == 'Carlotta':
            return 'break' if intro else 'opener'
        # 没有来源可依据: 开局第一棒, 或者交接记账被战斗判定抖掉了。
        # 带着变奏 + 大招亮着, 多半就是卜灵交过来的那一棒; 否则按启动段处理 ——
        # 启动段本来就是给空手入场准备的, 跑错了下一圈自己会转回来
        if intro and self.liberation_available():
            self.logger.info('zhezhi: 来源不明, 但带变奏且 R 亮着, 按主力段处理')
            return 'burst'
        return 'opener'

    # ------------------------------------------------------------------ 启动段

    def opener(self):
        """3a -> 平切卜灵。

        这三下是拿来垫丹青的: 主力段一上来就要 E1 + 两段重击·构形, 那些都从丹青里扣,
        第一圈没有这三下垫底就是空的(帧表核心回收: A1 +5 / A2 每段 +3 / A3 +25)。
        """
        self.attack_chain(self.OPENER_A, self.A_INTERVAL, last_interval=self.A_TO_LEAVE)
        self.leave(NativeDouling, con='no')

    # ------------------------------------------------------------------ 处决段

    def break_axis(self):
        """E, 处决 -> 平切卜灵。

        这一棒不打伤害, 只做两件事: 把 E 的冷却转起来, 把珂莱塔爆发打出来的谐度破坏
        兑成处决。【不等协奏】—— 视频轴上这一步写的是"卜灵"而不是"变奏卜灵",
        而且 E + 处决 本来也攒不满。
        """
        self.cast_e('处决段的 E')
        self.execute()
        self.leave(NativeDouling, con='peek')

    def execute(self):
        """处决 —— 在一个窗口里【一直问到提示亮】, 而不是只赌调用那一瞬间。

        框架的 f_break() 只在"提示当下就亮着"时才动作(CombatCheck.f_break 里
        `if self.can_break or self.check_f_break():` 不成立就直接返回 None),
        提示晚出来哪怕 0.1 秒这一次调用就整个跳过。
        更麻烦的是 check_f_break() 中央找图那一路外面裹着「每秒最多查一次」,
        而宏跑起来 task.skip_combat_check 是开着的, 战斗判定线程也不刷 can_break ——
        两条腿一起瘸, 表现就是"处决从来不放"。这里把中央那一路自己补上。

        【一直没提示就别等满】: 这一棒后面接的是平切卜灵, 每多等一秒卜灵就晚一秒,
        所以 F_GIVEUP 之前没见到提示就直接走。
        """
        start = time.time()
        seen = False
        while self.time_elapsed_accounting_for_freeze(start) < self.F_WINDOW:
            if self.f_prompt_now() or getattr(self.task, 'can_break', False):
                seen = True
                self.task.can_break = True
                if self.f_break():
                    self.logger.info(
                        f'zhezhi: 处决打出 '
                        f'({self.time_elapsed_accounting_for_freeze(start):.2f}s)')
                    return True
            if not seen and self.time_elapsed_accounting_for_freeze(start) >= self.F_GIVEUP:
                self.logger.info('zhezhi: 这一轮没有处决可打, 直接走')
                return False
            self.check_combat()
            self.sleep(self.F_POLL)
        self.logger.info('zhezhi: 处决窗口到点了')
        return False

    def f_prompt_now(self):
        """自己查一次屏幕中央的处决提示 —— 绕开框架那「每秒一次」的频率限制。

        判据跟 CombatCheck.check_f_break() 逐字一致: 中央找到 f_break, 且不是拾取提示
        (F 同时是拾取/交互键, is_pick_f 就是为了区分这个)。
        """
        try:
            box = self.task.box_of_screen(0.2, 0.2, 0.75, 0.8, hcenter=True, vcenter=True)
            if self.task.find_one('f_break', box=box, target_height=720):
                return not self.task.is_pick_f()
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------ 主力段

    def burst_axis(self):
        """E -> 预输入重击 -> 重击 -> 大招 -> 3e -> 声骸 -> 变奏珂莱塔。"""
        self.cast_e('E1')
        self.sleep(self.E_TO_HOLD)
        self.hold_heavy(self.HEAVY_HOLD, '预输入重击 + 重击')
        self.sleep(self.HEAVY_TO_LIB)
        self.fire_liberation()
        self.cast_e_chain()
        self.fire_echo()
        self.leave(NativeCarlotta, con='require')

    def cast_e(self, name):
        """一下 E。走 click_resonance 而不是裸发键 —— 它会等技能真的进冷却再返回。

        【返回的是三元组】(clicked, duration, animated), 不是 bool。
        send_click=False: 这条轴上 E 前后都不想插普攻, 插了会把连段带歪。
        """
        clicked = self.click_resonance(send_click=False, time_out=self.E_CAST_TIMEOUT)[0]
        if not clicked:
            self.logger.warning(f'zhezhi: {name} 没放出去 (图标不亮?)')
        return clicked

    def cast_e_chain(self):
        """大招之后把强化E(E2/E3/E4)按完 —— 【按到高亮暗下去为止】。

        2026-09-06 两次实机反馈叠出来的写法:
          1. "第三下E没按出来" —— 折枝【没有输入缓存】(帧表里她那块没有那一节),
             三段之间必须等派生帧, 固定间隔连点会把最后一下丢掉且没人补;
          2. "三段E有几率被打断, 确保暗下去了再Q切人" —— 所以收手的判据不是时间,
             是那个高亮。被打断时高亮还在, 循环自然会把它补完。

        暗下去要【连续确认几次】才认(读数会抖), 而且【确认期间不发键】——
        万一真暗了, 那几下会按成新的 E1, 白吃 60 点丹青。
        """
        start = time.time()
        n = 0
        dark = 0
        stopped = '窗口到点'
        while self.time_elapsed_accounting_for_freeze(start) < self.E_CHAIN_WINDOW:
            self.check_combat()
            if self.time_elapsed_accounting_for_freeze(start) >= self.E_CHAIN_MIN \
                    and not self.resonance_blue():
                dark += 1
                if dark >= self.E_CHAIN_DARK_CONFIRM:
                    stopped = '强化E已暗'
                    break
                # 【确认期间不发键】—— 见上面
                self.task.next_frame()
                self.sleep(self.E_CHAIN_RETRY)
                continue
            dark = 0
            self.send_resonance_key()
            n += 1
            self.sleep(self.E_CHAIN_RETRY)
            self.task.next_frame()
        used = self.time_elapsed_accounting_for_freeze(start)
        if stopped == '窗口到点':
            self.logger.warning(f'zhezhi: 3e 按了 {n} 下 / {used:.2f}s, '
                                f'高亮到上限还没暗 —— 判据可能读错了')
        else:
            self.logger.info(f'zhezhi: 3e 按了 {n} 下 / {used:.2f}s ({stopped})')
        self.sleep(self.E_CHAIN_TAIL)

    # ------------------------------------------------------------------ 动作

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
        self.logger.info(f'zhezhi: {name} 按住 {duration:.2f}s')

    def fire_liberation(self):
        """R 大招 —— 【在窗口里等图标亮了再按, 不是问一次就走】。

        click_liberation() 只在"图标当下就亮着"时才真发键, 读到不亮就在
        wait_if_cd_ready 里补两下返回 False —— 而这时候重击刚脱手, 能量环那几帧
        最不可信。
        顺手清 task.in_liberation: 它被上一个角色留成 True 的话, click_liberation()
        开头那个 `if not self.task.in_liberation:` 会把发键整段跳过。
        click_f=False: 大招演出期间框架默认每 0.1 秒发一次 F, 会把攒着的处决提前吃掉
        —— 这条轴上处决是留给下一棒(处决段)的。
        """
        self.task.in_liberation = False
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.LIB_WINDOW:
            self.task.next_frame()
            if self.liberation_available():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if self.click_liberation(wait_if_cd_ready=0.3, click_f=False):
                    self.logger.info(f'zhezhi: liberation fired after {waited:.2f}s')
                    return True
            self.check_combat()
            self.click()
            self.sleep(self.LIB_RETRY)
        self.logger.warning(f'zhezhi: 大招图标 {self.LIB_WINDOW}s 内没亮, 硬按一次')
        return self.click_liberation(wait_if_cd_ready=0.5, click_f=False)

    def fire_echo(self):
        """Q 声骸 —— 【连发, 不是只发一次】。

        演出期间的按键被游戏丢弃、不排队, 只发一次很容易整段被吃掉;
        Q 是脱手技能, 多按几下不会打断当前动作, 连发是安全的。
        先发键, 读屏只用来决定要不要重发 —— 读到"在冷却"就不发的话, 读错一次
        这一轮就没有声骸, 而按在冷却上只是空操作。
        """
        was_available = self.echo_available()
        if not was_available:
            self.logger.info('zhezhi: echo reads on-cd, sending once anyway')
        for i in range(self.ECHO_TRIES):
            self.send_echo_key()
            self.record_echo_use()
            self.sleep(self.ECHO_RETRY, check_combat=False)
            if not was_available:
                return False
            if not self.echo_available():
                if i:
                    self.logger.info(f'zhezhi: echo landed on try {i + 1}')
                return True
        self.logger.warning(f'zhezhi: echo still reads available after '
                            f'{self.ECHO_TRIES} tries')
        return False

    # ------------------------------------------------------------------ 离场

    def leave(self, target_cls, con='peek'):
        """交棒给 target_cls, 然后按数字键直接切过去。

        Args:
            con: 'require' 协奏没满就留场接着打(带上限);
                 'peek'    只读一眼, 满了就白捡一次变奏, 不满也不等;
                 'no'      直接当没满 —— 开场那一下协奏必然是 0, 读它是纯白等。

        【不走 direct_switch()】: 它开头还要自己 wait_con_full() 轮询 0.8 秒协奏,
        而协奏是什么状态这里刚刚才读完, 那 0.8 秒是重复的。
        """
        self._target_cls = target_cls
        if con == 'require':
            con_ok = self.fill_con()
        elif con == 'peek':
            con_ok = self.is_con_full()
        else:
            con_ok = False
        target = self.char_at_slot(self.NEXT_SLOT) if self.NEXT_SLOT else None
        if target is not None and target is not self:
            for attempt in range(1, self.LEAVE_TRIES + 1):
                # 大招/处决的演出期间队伍 HUD 整块不渲染, in_team() 认不出人,
                # 直接按数字键会白按满 SWITCH_TIMEOUT 然后判失败
                self.wait_team_hud()
                if self.switch_to_index(target.index):
                    self.handoff_to(target, con_ok)
                    self.logger.info(f'zhezhi: 交棒 {target_cls.__name__} (intro={con_ok})')
                    return
                self.logger.warning(f'zhezhi: direct switch failed '
                                    f'(第 {attempt}/{self.LEAVE_TRIES} 次), retrying')
        self.logger.warning('zhezhi: falling back to framework switch, rotation may drift')
        self.switch_next_char()

    def fill_con(self):
        """等协奏满 —— 期间继续点普攻, 不干等。

        给上限是有意的: 协奏满不满取决于打没打到怪, 怪被清了就永远等不到,
        而下一棒(珂莱塔)本来就有空手入场的分支, 能自愈; 卡死在这里不能。
        """
        start = time.time()
        last_log = 0.0
        while True:
            if self.is_con_full():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if waited > 0.3:
                    self.logger.info(f'zhezhi: 协奏满 after {waited:.1f}s')
                return True
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if elapsed >= self.CON_GATE_TIMEOUT:
                self.logger.warning(f'zhezhi: 协奏没满就到点了 '
                                    f'(con={self.get_current_con():.2f}), 走人')
                return False
            if elapsed - last_log >= self.CON_LOG_EVERY:
                last_log = elapsed
                self.logger.warning(f'zhezhi: 还在等协奏 ({elapsed:.1f}s, '
                                    f'con={self.get_current_con():.2f})')
            self.check_combat()
            self.click()
            self.sleep(self.FILL_CON_INTERVAL)

    def wait_team_hud(self, timeout=None):
        """等右侧队伍头像的编号渲染回来 —— 演出期间它整块是不画的。"""
        timeout = self.TEAM_HUD_WAIT if timeout is None else timeout
        start = time.time()
        while time.time() - start < timeout:
            if self.task.in_team()[0]:
                waited = time.time() - start
                if waited > 0.2:
                    self.logger.info(f'zhezhi: team hud back after {waited:.2f}s')
                return True
            self.task.next_frame()
            self.sleep(0.08, check_combat=False)
        self.logger.warning(f'zhezhi: team hud still unreadable after {timeout:.1f}s')
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
