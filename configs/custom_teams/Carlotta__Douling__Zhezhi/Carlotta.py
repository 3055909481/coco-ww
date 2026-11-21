import time

from src.char.BaseChar import SwitchPriority
from src.char.Carlotta import Carlotta as NativeCarlotta
from src.char.Zhezhi import Zhezhi as NativeZhezhi


class Carlotta(NativeCarlotta):
    """珂莱塔 —— 柯折卜的主C, 也是开局那一棒。

    轮换环: 折枝(E+处决) -> 卜灵 -> 折枝(主力段) -> 珂莱塔 -> 折枝(E+处决) -> ...

        开场段 (空手入场)   双e                                  -> 平切折枝
        爆发段 (变奏入场)   双e, 2a, 强化重击, 大招, 再点4下大招,
                            双e, (2a), 声骸                      -> 变奏折枝

    【为什么是这个顺序 —— 官方帧表(角色-女 r3835~r3921)的资源规则】:
      * 晶质来自: 变奏入场第30F +3、E1「暴力美学」第1F +3、A2 第20F +3、重击 +3。
        灵萃来自: 每一下强化A「减少1点晶质、增加10点灵萃」, 以及 E2「示我璀璨」
        一次性「清空晶质, 增加等量*10灵萃」。
        所以 双e 是"进3点晶质 -> 一次性兑成灵萃", 2a 再补两下, 灵萃满了才有强化重击。
      * 强化重击「末路见行」第1F 就【减少E1冷却、刷新E2】—— 这就是爆发段末尾还能再来
        一次双e 的原因, 也是双e 必须排在强化重击【前后各一次】的原因。
      * 大招前置第130F 给 10 秒大招状态, 状态里每按一次 R 出一发「死兆」,
        帧表写着「施放4次死兆后触发」时缓, 之后接「致死以终」收尾 ——
        视频原话「再点四下大招」说的就是这个, 不是四个独立的大招。
      * 她是这一圈里吃卜灵开阵状态第 2 层监听(+10%+15% 共鸣技能类型伤害)的人,
        所以【必须带着变奏进场】, 上一棒折枝的协奏闸不能省。

    轴出自 E:\\轴视频\\柯折卜，全网最详细教学！矩阵杀手，解放维里奈和守岸人。.mp4
    左侧「启动轴」/「循环轴」两块字幕面板(视频 25 秒 / 125 秒)。动作时机对着视频
    181~196 秒那一段实时录像核过(该段倒计时与视频秒数 1:1, 前面的教学段是 0.55 倍速)。
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

    # ================================================================ 柯折卜: 珂莱塔这一棒

    # ---- 时间常量。除注明外都是官方帧表换算 (60fps, 帧/60 = 秒), 单位秒 ----
    # 变奏入场: QTE「第71F前不能切人、不响应输入」。这一段【不点普攻】——
    # 入场后第一个动作是 E, 提前点一下会先出一段普攻, E 就被挤到后面了
    INTRO_ANIM = 1.18

    # ---- 双e ----
    # E1「暴力美学」派生 46F, 也就是 0.77 秒后才轮得到 E2「示我璀璨」。
    # 实机视频里两下相隔约 0.6 秒 —— 点按有输入缓存, 不必等满派生帧
    E1_TO_E2 = 0.70
    # E2「示我璀璨-时停」自己有一小段时停, 演完再接普攻
    E2_TAIL = 0.45
    # 开场那一次【不等 E2 演完】: 视频原话是「第二下共鸣技能按下的同时切折纸打3a」,
    # 也就是第二下 E 一按下人就该走了。
    # 2026-09-06 用户实机: "开头柯莱塔切折枝慢了0.1s" —— 比 E2_TAIL 少 0.1 秒。
    # 【只砍开场这一次】: 爆发段的双e 后面紧接着 2a / 声骸, 那里的 E2_TAIL 是给
    # 时停演完留的, 砍了会把后面的连段挤到时停里
    OPENER_E2_TAIL = 0.35
    E_TO_A = 0.30

    # ---- 2a ----
    # 强化A1 派生 24F, 强化A2-2 派生 46F。点按有输入缓存, 不必等满派生帧
    A_COUNT = 2
    A_INTERVAL = 0.40
    A_TO_HEAVY = 0.15

    # ---- 强化重击「末路见行」 ----
    # 等鼠标长按提示(灵萃满)亮起来的上限; 等的时候继续点普攻, 普攻本身也在把
    # 晶质兑成灵萃, 属于"边等边攒"
    FORTE_WAIT = 4.0
    FORTE_FILL_INTERVAL = 0.22
    # ---- 强化重击 -> 大招 ----
    # 视频原话「打出强化重击 最后伤害出来的同时放大招」。
    # 末路见行【一共 6 段】, 发生帧 18 / 22 / 26 / 30 / 32 / 【78】F。
    # 前 5 段每段削韧 10.69、大招回收 1.36, 【第 6 段是 80.16 / 584.16 / 10.2】——
    # 主伤害全压在最后那一下, 吞掉它等于这次强化重击白打。
    # 关键帧(从末路见行第 1F 算): 78F 第6段发生 -> 90F 判定走完(持续帧12) ->
    # 98F 改中断优先级 -> 100F 派生帧 -> 131F 动作结束。
    #
    # 【别拿折枝那一格来套】: 折枝的 HEAVY_TO_LIB 是 0.0, 因为那边是【松手】,
    # 重击·构形可脱手(P=√), 松手后飞行道具自己会打完;
    # 这边是【用大招打断】—— 大招的中断优先级压得过末路见行(Z=10), 还没发生的伤害段
    # 直接就没了。脱手和打断是两回事。
    #
    # 【这一格是"挑区间"不是"挑点"】(2026-09-06 用户: "重击开始到结束的帧, 压到靠后的
    # 位置再按R"; 之后又"能不能再快一点点, 保住下限的同时追一点上限"):
    #   起点(heavy_click_forte 返回)本身就抖 —— 实机估下来负向能到 -20F 上下,
    #   所以要挑的不是一个点, 而是【抖完还整个落在安全带里】的位置。
    #   把 78~100F 那一段当目标就太窄: 中心放 96F(=1.60), 往下一抖就掉回 90F 以内,
    #   那正是之前"有的早了有的正好"的来源。压到 100~131F(派生帧 ~ 动作结束)才够宽。
    #
    #   2.05 = 中心 126F, 下界约 116F —— 稳, 但上限追得不够。
    #   1.90 = 中心 117F, 下界约 107F: 离危险线(90F 第6段判定走完)还有 17 帧余量,
    #          而中心仍在 100F 派生帧之后, 后摇照样取消得到。比 2.05 快 0.15 秒。
    #
    # 【理论最优是"第6段伤害一出来(78F)就 R", 但实机波动控制不了】——
    # 贴着 90F 排就是拿主伤害(80.16 削韧 / 584.16 偏谐 / 10.2 大招回收全在第6段)
    # 去赌那点后摇。两边代价不对称, 所以只在余量里追, 不贴线。
    # 还想再快: 每往前 0.05 秒少 3 帧余量, 【1.75 是绝对下限】(中心 108F, 下界
    # 刚好压在 100F 派生帧上), 再往前就退回那个太窄的区间了。
    HEAVY_TO_LIB = 1.90
    #
    # 【试过并且失败了的做法, 别再来一次】(2026-09-06):
    #   想拿「末路见行第1F减少E1冷却、刷新E2」当起手锚点, 在长按期间轮询
    #   has_cd('resonance'), 一恢复就当作第 1 帧。
    #   ——【错的】: E 从冷却里恢复【不是末路见行独有的信号】, 双e 放完之后 E 的冷却
    #   本来就在走, 随时会自然好。于是按下去第一帧就"命中", 立刻 break + 松手:
    #   左键几乎没按住(重击根本没打出来), 锚点还是假的, 从假锚点数 1.65 秒就变成
    #   "重击都打完了才 R"。用户实机三种症状(很晚 / 太早 / 重击没打)全由这一个 bug 来。
    #   要找锚点的话, 判据必须是【这个动作独有】的状态变化。

    # ---- 大招 + 再点四下大招 ----
    # 大招前置第130F给 10 秒大招状态, 期间每按一次 R 出一发死兆, 四发之后触发时缓
    # 并接「致死以终」(前置 160F 不响应输入, 整段 260F)。
    # 窗口按"10 秒状态 + 致死以终演出"给, 宁可宽一点 —— 这一段是她全部伤害
    ULT_WINDOW = 16.0
    ULT_RETRY = 0.12

    # ---- 收尾 ----
    # 强化重击刷新了 E2、砍了 E1 冷却, 所以还能再来一次双e。
    # 后面那 2a 只在【协奏还没满】时才打: 启动轴那一圈写的是"双e, 2a, 声骸",
    # 循环轴写的是"双e, 声骸" —— 两者的差别本来就是启动轮协奏不够
    TAIL_A_COUNT = 2
    TAIL_A_TO_ECHO = 0.12

    ECHO_TRIES = 3
    ECHO_RETRY = 0.15
    FILL_CON_INTERVAL = 0.20
    CON_GATE_TIMEOUT = 10.0
    CON_LOG_EVERY = 3.0
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
        # press_w 写成 0 是为了让内置的 decide_teammate() 永远不会被触发 ——
        # 它一旦跑起来就会把 char_zhezhi 挂上, 内置珂莱塔和内置折枝双双切进
        # 「联动模式」(do_perform_interlock), 那是它们自带的另一条轴, 跟这里的
        # 死顺序打架
        self.press_w = 0
        self.char_zhezhi = None
        # 她不是治疗, 内置值是 True, 不重申的话框架会在每次切人时替她按一下 F。
        # 这条轴上的处决是折枝那一棒的活, 让框架乱按会把强化重击/大招整段打断
        self.check_f_on_switch = False

    def decide_teammate(self):
        """压掉内置的联动模式。见 reset_state 里 press_w 的注释。"""
        self.press_w = 0
        self.char_zhezhi = None

    def on_combat_end(self, chars):
        self.reset_fixed_rotation(chars)
        super().on_combat_end(chars)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """把内置那条「折枝带变奏来就必须切我」的规则拿掉 —— 这一圈里折枝要上场
        两次, 只有主力段那次才交给她, 处决段那次要交给卜灵。
        (只有 direct_switch 失败、退回框架切人时才轮得到这个函数。)
        """
        if self.healer_full_con_switch_locked():
            return SwitchPriority.NO
        return SwitchPriority.NORMAL

    def wait_switch(self):
        """内置版会在入场后 2.5 秒内一直要求"先别切我" —— 这条轴的切人时机是自己
        算好的, 不需要它再插一脚。
        """
        return False

    # ------------------------------------------------------------------ 轴

    def do_perform(self):
        intro = self.take_intro()
        self.take_handoff_source()
        self.check_f_on_switch = False
        anchor = self.switch_in_anchor()
        if not intro:
            # 空手入场 = 开局那一下(或者轮换被打断掉回她这里)。没有变奏就没有入场那
            # 3 点晶质, 也没有卜灵的开阵加成, 硬打爆发是浪费大招 —— 只丢个双e
            # 把场子交给折枝, 让轮换自己转回来
            self.logger.info('carlotta: 空手入场, 走开场分支')
            return self.opener()
        # 用【切人确认那一刻】当基准, 不是这个函数被调到那一刻 —— 中间隔着框架的
        # 交接记账和任务循环, 那段延迟是变长的
        self.sleep(max(0.0, anchor + self.INTRO_ANIM - time.time()), check_combat=False)
        if self.flying():
            self.wait_down()
        self.logger.info('carlotta: 爆发轴 start')
        return self.burst_axis()

    # ------------------------------------------------------------------ 开场段

    def opener(self):
        """双e -> 平切折枝。

        视频原话「等1秒怪出现 然后科莱塔双共鸣技能」「第二下共鸣技能按下的同时
        切折纸打3a」—— 这一棒不打伤害, 只是把 E 的冷却转起来、顺手垫点灵萃。
        """
        self.cast_double_e('开场双e', tail=self.OPENER_E2_TAIL)
        self.leave(con='no')

    # ------------------------------------------------------------------ 爆发段

    def burst_axis(self):
        """双e -> 2a -> 强化重击 -> 大招 -> 再点4下大招 -> 双e -> (2a) -> 声骸。"""
        # ---- 双e: 进 3 点晶质, 再一次性兑成灵萃 ----
        self.cast_double_e('入场双e')
        self.sleep(self.E_TO_A)

        # ---- 2a: 每一下减 1 晶质、加 10 灵萃, 把灵萃顶满 ----
        self.attack_chain(self.A_COUNT, self.A_INTERVAL, last_interval=self.A_TO_HEAVY)

        # ---- 强化重击「末路见行」: 顺手刷新 E2、砍 E1 冷却 ----
        self.forte_heavy()

        # ---- 第 6 段伤害打完的那一刻放大招, 把重击的后摇取消掉 ----
        self.sleep(self.HEAVY_TO_LIB)
        self.fire_ult_chain()

        # ---- 收尾: 双e -> 协奏不够就再补 2a -> 声骸 -> 变奏折枝 ----
        self.cast_double_e('收尾双e')
        if not self.is_con_full():
            self.logger.info('carlotta: 协奏还没满, 补一组 2a')
            self.attack_chain(self.TAIL_A_COUNT, self.A_INTERVAL,
                              last_interval=self.TAIL_A_TO_ECHO)
        self.fire_echo()
        self.leave(con='require')

    # ------------------------------------------------------------------ 动作

    def cast_double_e(self, name, tail=None):
        """双e = E1「暴力美学」+ E2「示我璀璨」, 也就是把 E 连按两下。

        【这里【不能】走 click_resonance()】: 内置珂莱塔重写过它, 循环条件是
        "一直按到图标进冷却为止", 而 E1 之后 E2 立刻可派生、图标根本不进冷却。
        它内部要靠 current_resonance() 这个白色像素百分比决定发不发键, 一旦某一帧
        被特效糊成 0 而 send_click 又是 False, 就【既不发键也不点击】, 一路空转到
        超时 —— 而内置版超时时是【无条件】调 alert_skill_failed() 的(基类版本只在
        time_out==0 时才报), 结果是弹通知 + 截图, 满屏假警报。
        E 在冷却里按空只是空操作, 少按一下这一轮却要少一段灵萃, 所以固定发两下。

        Args:
            tail: 第二下 E 之后还要等多久。不传就用 E2_TAIL(等时停演完);
                开场那一次传 OPENER_E2_TAIL —— 那一下按完就该切人了。
        """
        if self.has_cd('resonance'):
            self.logger.warning(f'carlotta: {name} 时 E 在冷却, 跳过')
            return 0
        self.check_combat()
        self.send_resonance_key()
        self.record_resonance_use()
        self.sleep(self.E1_TO_E2)
        self.check_combat()
        self.send_resonance_key()
        self.sleep(self.E2_TAIL if tail is None else tail)
        self.logger.info(f'carlotta: {name} 连按两下 E')
        return 2

    def forte_heavy(self):
        """强化重击 —— 等鼠标长按提示亮了再按住。

        判据用找图(is_mouse_forte_full, 找的是鼠标长按提示图标)而不是颜色百分比 ——
        找图对特效糊屏更扛得住。
        灵萃还没满就先补普攻等一等: 普攻自己就在把晶质兑成灵萃, 属于边等边攒;
        等到 FORTE_WAIT 还没有就跳过 —— 这一步做不到不会传染给下一棒(大招照放),
        卡死在这里反而会。

        这一段【走框架的 heavy_click_forte()】, 别再自己写长按去抓"起手锚点" ——
        为什么不行见上面 HEAVY_TO_LIB 那段末尾。
        """
        start = time.time()
        ready = False
        while self.time_elapsed_accounting_for_freeze(start) < self.FORTE_WAIT:
            if self.is_mouse_forte_full():
                ready = True
                break
            self.check_combat()
            self.click()
            self.sleep(self.FORTE_FILL_INTERVAL)
        if not ready:
            self.logger.warning(f'carlotta: 灵萃 {self.FORTE_WAIT}s 内没满, '
                                f'跳过强化重击直接进大招')
            return False
        waited = self.time_elapsed_accounting_for_freeze(start)
        ok = bool(self.heavy_click_forte(self.is_mouse_forte_full))
        self.logger.info(f'carlotta: 强化重击 {"打出" if ok else "没打出"} '
                         f'(等灵萃 {waited:.2f}s)')
        return ok

    def fire_ult_chain(self):
        """R 大招 -> 再点四下大招(死兆) -> 致死以终收尾。

        【为什么是一个 while 循环而不是按五次键】: 内置的 click_liberation() 本身就是
        "图标亮着就每 0.1 秒按一次"的循环, 大招状态里那四发死兆共用同一个图标,
        所以一次调用往往就把好几发按出去了; 而最后那一发有演出(队伍 HUD 消失),
        click_liberation() 会等它演完并做冻结记账 —— 这些都不该自己重写。
        外面这层循环只负责"图标还亮着就继续"。

        click_f=False: 演出期间框架默认每 0.1 秒发一次 F, 会把攒着的处决提前吃掉
        —— 这条轴上处决是下一棒(折枝处决段)的活。
        """
        self.task.in_liberation = False
        start = time.time()
        rounds = 0
        while self.time_elapsed_accounting_for_freeze(start) < self.ULT_WINDOW:
            if not self.liberation_available():
                if rounds:
                    break
                # 图标还没亮: 强化重击刚脱手, 能量环那几帧最不可信, 边打普攻边等
                self.check_combat()
                self.click()
                self.sleep(self.ULT_RETRY)
                continue
            if self.flying():
                # 视频原话「期间怪物如果打你 可以闪避再按大招」
                self.dodge()
            if self.click_liberation(wait_if_cd_ready=0.2, click_f=False):
                rounds += 1
            self.check_combat()
        if rounds:
            self.logger.info(f'carlotta: 大招段打完 ({rounds} 轮, '
                             f'{self.time_elapsed_accounting_for_freeze(start):.1f}s)')
        else:
            self.logger.warning(f'carlotta: 大招图标 {self.ULT_WINDOW}s 内一直没亮')
        return rounds > 0

    def dodge(self):
        """闪避 = 右键(全框架统一)。

        注意 Game Hotkey.json 里那个 "Dodge Key": "lshift" 【不是】战斗闪避,
        那是疾跑键, 只被导航代码用。
        """
        self.task.click(key='right')

    def fire_echo(self):
        """Q 声骸 —— 【连发, 不是只发一次】。

        演出期间的按键被游戏丢弃、不排队, 只发一次很容易整段被吃掉;
        Q 是脱手技能, 多按几下不会打断当前动作, 连发是安全的。
        先发键, 读屏只用来决定要不要重发 —— 读到"在冷却"就不发的话, 读错一次
        这一轮就没有声骸, 而按在冷却上只是空操作。
        """
        was_available = self.echo_available()
        if not was_available:
            self.logger.info('carlotta: echo reads on-cd, sending once anyway')
        for i in range(self.ECHO_TRIES):
            self.send_echo_key()
            self.record_echo_use()
            self.sleep(self.ECHO_RETRY, check_combat=False)
            if not was_available:
                return False
            if not self.echo_available():
                if i:
                    self.logger.info(f'carlotta: echo landed on try {i + 1}')
                return True
        self.logger.warning(f'carlotta: echo still reads available after '
                            f'{self.ECHO_TRIES} tries')
        return False

    # ------------------------------------------------------------------ 离场

    def leave(self, con='require'):
        """交棒折枝, 按数字键直接切过去。

        Args:
            con: 'require' 协奏没满就留场接着打(带上限);
                 'no'      直接当没满 —— 开场那一下协奏必然是 0, 读它是纯白等。

        【为什么爆发段一定要等满协奏】: 折枝下一棒(处决段)本身不打伤害, 但她要靠
        变奏入场才接得上; 更要紧的是这一圈的方向 —— 处决段之后是卜灵, 卜灵的开阵
        又要喂回珂莱塔, 少一环整条链都掉一层。
        这道闸【要给上限】: 协奏满不满取决于打没打到怪, 怪被清了就永远等不到;
        给了上限至少还能把轮换往下传, 下一圈自己会补回来。
        """
        con_ok = self.fill_con() if con == 'require' else False
        target = self.char_at_slot(self.NEXT_SLOT) if self.NEXT_SLOT else None
        if target is not None and target is not self:
            for attempt in range(1, self.LEAVE_TRIES + 1):
                # 大招/重击的演出期间队伍 HUD 整块不渲染, in_team() 认不出人,
                # 直接按数字键会白按满 SWITCH_TIMEOUT 然后判失败
                self.wait_team_hud()
                if self.switch_to_index(target.index):
                    self.handoff_to(target, con_ok)
                    self.logger.info(f'carlotta: 交棒折枝 (intro={con_ok})')
                    return
                self.logger.warning(f'carlotta: direct switch failed '
                                    f'(第 {attempt}/{self.LEAVE_TRIES} 次), retrying')
        self.logger.warning('carlotta: falling back to framework switch, rotation may drift')
        self.switch_next_char()

    def fill_con(self):
        """等协奏满 —— 期间继续点普攻, 不干等。"""
        start = time.time()
        last_log = 0.0
        while True:
            if self.is_con_full():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if waited > 0.3:
                    self.logger.info(f'carlotta: 协奏满 after {waited:.1f}s')
                return True
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if elapsed >= self.CON_GATE_TIMEOUT:
                self.logger.warning(f'carlotta: 协奏没满就到点了 '
                                    f'(con={self.get_current_con():.2f}), 走人')
                return False
            if elapsed - last_log >= self.CON_LOG_EVERY:
                last_log = elapsed
                self.logger.warning(f'carlotta: 还在等协奏 ({elapsed:.1f}s, '
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
                    self.logger.info(f'carlotta: team hud back after {waited:.2f}s')
                return True
            self.task.next_frame()
            self.sleep(0.08, check_combat=False)
        self.logger.warning(f'carlotta: team hud still unreadable after {timeout:.1f}s')
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
