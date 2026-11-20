import time

from src.char.Qiuyuan import Qiuyuan as NativeQiuyuan
from src.char.Xigelika import Xigelika as NativeXigelika


class Qiuyuan(NativeQiuyuan):
    """仇远 —— 西仇守「25s 轮椅轴」的第二棒。

    轮换环: 守岸人 -> 仇远 -> 西格莉卡 -> 守岸人。她这一棒是纯增益中转:
    **长按E、E 一进冷却就放大招、再打一轮回路重击攒满协奏交给西格莉卡**。

    轴的来源: E:\\轴视频\\西仇守.mp4。下面的时间都是从视频里量的墙钟秒。
    启动轴和循环轴对她来说是【同一条】, 两处的读数放一起当交叉验证:

                                  启动轴            循环轴
        长按E + R              13.60 - 19.17     61.80 - 66.77
        2A + 回路重击*3 + Q     19.20 - 24.70     66.80 - 72.80
        -> 变奏西格莉卡          24.73 - 25.67     72.83 - 73.87

    逐帧读到的关键节点 (启动轴那一遍, 技能栏冷却数字为证):
        13.0   变奏入场, E / R 图标都是亮的
        13.75  按下 E 并按住
        14.35  E 图标出现冷却 13.9  <- 字幕说的「E技能开始CD」就是这一帧
        15.30  放 R (R 图标此时还亮着, 15.4 起进大招演出)
        19.00  演出结束回到战斗
        21.9   字幕「第一段回路唰唰唰的时候释放声骸技能」

    字幕原文: t=13.4「仇远长按E技能」 t=15.5「E技能开始CD后马上释放大招」
    t=19.5「然后两段普攻+回路重击」 t=21.9「第一段回路唰唰唰的时候释放声骸技能」
    t=23.7「然后打满协奏」 t=61.9「仇远长按E技能+大招」 t=67.4「2A+回路重击」
    t=70.7「唰唰唰的时候放声骸技能」。

    【为什么长按E 用判断、R 用固定延时】: E 有没有按出去是每轮都会变的事(被打断/
    冷却没好), 而它恰好有一个可靠信号 —— 图标出现冷却数字, 所以用判断;
    从"E 进冷却"到"该按 R"这一段是 E 的固定动画长度, 游戏不给任何信号, 只能计时。
    """
    # ================================================================ 固定顺序切人 (公共段)
    # 这一段在 Xigelika.py / Qiuyuan.py / ShoreKeeper.py 三个文件里逐字一样, 改一处记得同步改三处。
    # (跟 Chisa__Suisui__YangYangSp 那队的三份也是逐字一样的, 那边改了这边最好跟。)
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
    # 切人【真正完成】那一刻的时间戳 —— in_team 认出换人成功的那一帧, 不是
    # settle_with_attack 站稳之后。循环轴的落地E 就按这个锚点计时, 差 0.55 秒
    # 就会掉进"E 按在下落段完全没反应"的失败区
    switch_confirmed_at = 0.0

    def note_intro_handoff(self):
        """被别人用直接切人的方式交接进场时调用 —— 记下"我是带着变奏上来的"。"""
        self.intro_pending = True

    def take_intro(self):
        """本次入场是否带着变奏。读完就清, 避免下一轮误判。"""
        intro = self.has_intro or self.intro_pending
        self.intro_pending = False
        return intro

    def reset_fixed_rotation(self):
        """战斗结束时调 —— 在各自的 on_combat_end() 里。"""
        self.intro_pending = False

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

    # ================================================================ 西仇守: 仇远这一棒

    # ---- 时间常量。全部来自视频墙钟, 单位秒 ----
    INTRO_ANIM = 0.75      # 变奏入场动画。视频: 13.0 入场 -> 13.75 按 E
    E_HOLD_MIN = 0.35      # E 至少按住这么久再去看冷却。见 hold_resonance() 的注释
    E_HOLD_MAX = 1.6       # 按住 E 等冷却出现的上限 (视频实际 0.6 秒)
    # E 出现冷却 -> 按 R。【必须贴着按, 不要留安全余量】(2026-08-25 实机改):
    # 原来照视频填的 0.95 秒 —— 视频里那 0.95 秒是人眼看到冷却数字再动手的反应时间,
    # 不是招式需要的间隔, 照抄进来就是白等。
    # 之所以敢贴着按: click_liberation() 里是
    #     while self.liberation_available(): send_liberation_key()  (每 0.1 秒一次)
    # 也就是【R 图标还亮着就一直重发】, 按早了被 E 的动画吃掉也会自动补发, 不会漏。
    E_TO_LIB = 0.05
    LIB_TO_A = 0.20        # 大招演出结束 -> 第一段普攻。视频: 19.00 -> 19.20
    A_INTERVAL = 0.42
    A_TO_HEAVY = 0.30      # 第二段普攻 -> 按住普攻出回路重击
    # 【Q 挪到回路重击【打完之后】放】(2026-08-25 用户实机):
    #   「由于在打回路攻击第二段第三段时候 Q 是无法释放的状态, 最好最后打完放 Q,
    #     也不影响。」
    # 原来是按住 1.5 秒之后往重击动画里塞 Q, 日志里一直是
    # `qiuyuan: echo still reads available after 3 tries` —— 就是那时候放不出来。
    # 视频里 UP 主是在第一段回路的时候放的, 但那是人手的时机, 宏抓不准, 挪到后面更稳。
    ECHO_TRIES = 4         # Q 最多连发几次 (演出会吞按键), 见 fire_echo()
    ECHO_RETRY = 0.22      # 两次之间隔多久
    # (Q 已经挪到 leave() 里"协奏一满就 Q + 切人"那一下, 这里不再有 Q 的时间常量)
    HEAVY_MAX = 4.5        # 回路重击*3 打完的上限。视频这一段约 4 秒
    # 【先无条件按满这么久, 之后才允许靠判据提前收手】(2026-09-21 用户"一定要打回路重击"):
    # hold_until() 进来先查一次条件 —— "回路提示消失"那一帧没读到就当场松手, 一段都不出。
    # 三段按派生帧累加 ≈ 2.77 秒, floor 取 2.5, 剩下的交给判据
    HEAVY_MIN = 2.5
    # 离场前补打那一发时, 等回路满的上限(见 ensure_heavy)
    HEAVY_RESCUE_CAP = 4.0
    # 【R 的能量是靠长按E 打出来的, 所以要等它攒满再放】(2026-08-25 用户实机):
    #   「循环轴入场R的能量还不满, 所以要靠长按E来打满R的能量才能释放。」
    # 于是这里不是"到点就按", 而是【等大招图标亮起来(= 能量够了)再按】,
    # 等的过程中一边补普攻 —— 普攻本身也在攒能量。
    # 原来那版之所以经常放不出来: click_liberation() 只在"图标当下就亮着"时才真发键,
    # 读到不亮就在 wait_if_cd_ready 里补两下返回 False, 整个过程只有 0.13~0.47 秒,
    # 而那正是她刚入场、E 还在演、能量还没到账的时候。
    # 日志统计: 23 次里 6 次 `liberation not ready`, 全在入场后 0.5~0.7 秒。
    # 第一次等 R 图标亮的窗口。【3.0 -> 2.2】: 实机 R 真要等的最长一次是 1.56 秒,
    # 再往上全是"图标本来就不会亮"(能量不够)的情况 —— 那几秒纯粹站着挨打,
    # 而框架的脱战判定正好在这段之后掐过两次。等不到就先去打回路重击,
    # 重击给的能量往往就够了, 之后用 LIB_WINDOW_RETRY 再试一次
    LIB_WINDOW = 2.2
    LIB_WINDOW_RETRY = 3.0
    LIB_RETRY = 0.12
    # 回路没满时最多补多久普攻。【2.5 -> 4.0】: R 的等待窗口会先吃掉几秒,
    # 轮到这道闸时回路往往还差一点, 2.5 秒经常等不到就跳过重击了
    FORTE_WAIT = 4.0
    # 【协奏没满不许走】(2026-08-25 用户: "仇远加个保底, 如果流程被打断太多,
    #   至少要打到协奏圈满了再切人")。
    # 她带不带满协奏走人, 决定的是西格莉卡开不开得了主C轴 —— 空手入场的话
    # 西格莉卡只会丢个声骸就把场子交回守岸人, 整圈报废。所以这道闸给得很松。
    # 而且等的这段时间不是干点普攻, 见 fill_con()
    CON_GATE_TIMEOUT = 12.0
    CON_LOG_EVERY = 3.0    # 等协奏时每隔这么久报一次读数, 卡住了好查
    # 这一轮打出回路重击没有。【写成类属性】是为了它在第一次调用之前也存在,
    # 静态检查扫得到, 也不用 getattr 兜
    _heavy_done = False
    SWITCH_ANCHOR_MAX = 1.5

    @property
    def NEXT_SLOT(self):
        """打完切西格莉卡。按类找人, 不写死队伍位置。"""
        for char in self.task.chars:
            if isinstance(char, NativeXigelika):
                return char.index + 1
        return None

    def reset_state(self):
        super().reset_state()
        # 【必须写在 reset_state 里, 光写 __init__ 保不住】: CharFactory 换类时是
        # 先建新实例再 replacement.__dict__.update(内置实例.__dict__), 凡是"内置也有、
        # 我改了值"的字段都会被静默打回内置值。仇远不是治疗, 内置值是 True,
        # 不重申的话框架会在每次切人时替她按一下 F 处决
        self.check_f_on_switch = False
        # 上一轮的"打过重击"不能带到这一轮来, 否则 ensure_heavy 会以为已经打过了
        self._heavy_done = False

    def on_combat_end(self, chars):
        self.reset_fixed_rotation()
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

        # ---- 长按 E ----
        self.hold_resonance()

        # ---- E 一进冷却就放大招 ----
        self.sleep(self.E_TO_LIB)
        fired = self.fire_liberation()

        # ---- 2A + 回路重击*3 ----
        # 【Q 不在这里】: 挪到 leave() 里"协奏一满就 Q + 切人"那一下, 见 leave()
        # 【这一段不受 R 放没放出来影响】(2026-09-21 实机: "R 能量没有满, 多 A 了几下,
        # 放了 R 之后就不会打回路重击了"): 回路重击是她这一棒的主要伤害和协奏来源,
        # 不能因为 R 那一步卡住就连它一起丢掉
        self.sleep(self.LIB_TO_A)
        self.attack_chain(2, self.A_INTERVAL, last_interval=self.A_TO_HEAVY)
        self.forte_heavy()

        if not fired:
            # 【R 第一次没放出来就在重击之后再试一次】: 第一次失败的原因是能量不够,
            # 而回路重击那三段正好给了一大笔能量 —— 这时候再读一次图标往往就亮了。
            # 放在重击【之后】而不是继续在前面干等: 前面那 3 秒窗口已经白站过一次了,
            # 再耗下去容易被框架的脱战判定掐掉(实机两次漏重击都是这么来的)
            self.logger.info('qiuyuan: R 第一次没放出来, 打完回路重击再试一次')
            self.fire_liberation(window=self.LIB_WINDOW_RETRY)

        self.leave()

    # ------------------------------------------------------------------ 动作

    def hold_resonance(self):
        """长按 E, 按到图标出现冷却数字为止。

        【判"冷却出现"之前先确保它不在冷却】—— 这条轴上她的 E 冷却约 15 秒、
        一圈约 39 秒, 上场时本来就该是好的; 但读数抖一下就会让 wait_until 立刻成立,
        E 等于只点了一下没按住。E_HOLD_MIN 是给这个兜底的最小按住时长。
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
            self.logger.info('qiuyuan: E on cd, firing liberation next')
        else:
            self.logger.warning(f'qiuyuan: E cd not seen within {self.E_HOLD_MAX}s, '
                                f'going on anyway')
        self.sleep(0.05)
        return landed

    def fire_liberation(self, window=None):
        """R 大招 —— 【在一个窗口里反复试, 不是问一次就走】。

        click_liberation() 的真身: 图标亮着就每 0.1 秒重发一次直到人离场;
        读到"不亮"就掉进 wait_if_cd_ready 那个 0.3 秒的小分支, 补两下就 return False。
        整个过程只有 0.13 ~ 0.47 秒 —— 而她这时候刚变奏入场半秒、E 的动画还在演,
        能量环的读数正是最不可信的时候。日志里 6 次 `liberation not ready` 全在这个位置。

        【顺手清掉 task.in_liberation】: 它要是被上一个角色的大招留成 True,
        click_liberation() 开头那个 `if not self.task.in_liberation:` 会把发键整段跳过,
        0.13 秒就返回 False, 一次键都没按 —— 这正是那几次"秒失败"的形态。
        走到这里我们人在场上, 不可能正处在大招演出里, 清掉是安全的。
        """
        window = self.LIB_WINDOW if window is None else window
        self.task.in_liberation = False
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < window:
            self.task.next_frame()
            if self.liberation_available():
                # 图标亮了 = 能量够了, 这时候 click_liberation 里那个
                # `while liberation_available(): send_liberation_key()` 才会真的发键
                waited = self.time_elapsed_accounting_for_freeze(start)
                if self.click_liberation(wait_if_cd_ready=0.3, click_f=False):
                    self.logger.info(f'qiuyuan: liberation fired after {waited:.2f}s')
                    return True
            self.check_combat()
            # 别干等 —— 一边补普攻一边等能量攒够, 普攻自己也在攒
            self.click()
            self.sleep(self.LIB_RETRY)
        # 窗口内图标一直没亮。硬按一次尽人事(读数抖的情况还能救回来)
        self.logger.warning(f'qiuyuan: liberation icon never lit within {window}s, '
                            f'trying once anyway')
        return self.click_liberation(wait_if_cd_ready=0.5, click_f=False)

    def forte_heavy(self):
        """回路重击*3: 按住普攻一路打完。【Q 不在这里】, Q 在 leave() 里跟切人一起。

        【先无条件按满 HEAVY_MIN 再看判据】(2026-09-21 用户: "一定要打回路重击 Q 切人"):
        hold_until() 是【进来先查一次条件】的 —— "回路提示消失"这一帧要是没读到,
        它当场就返回、finally 立刻松手, 三段重击一下都没打出去。

        【按住期间绝对不要再调 mouse_down() 补按】—— PostMessage 后端的 mouse_down()
        内部会 try_activate() 投一条 WM_ACTIVATE, 窗口收到就把已经按住的左键松开。
        按住期间一个键都不发。
        """
        if not self.require('仇远 回路满', self.is_mouse_forte_full,
                            action=self.click, timeout=self.FORTE_WAIT):
            # 【这里不再顺手放 Q】: 以前是"回路闸没过就 fire_echo 走人", 于是 R 的能量
            # 保底一触发、回路又没等到, 整轮就没有重击了(实机反馈的就是这个)。
            # 现在交给 leave() 里的 ensure_heavy() 再补一次, Q 也统一在那里放
            self.logger.warning('qiuyuan: 回路闸没过, 交给离场前的 ensure_heavy 再等一次')
            return False
        self.task.mouse_down()
        try:
            self.sleep(self.HEAVY_MIN)
            self.hold_until(lambda: not self.is_mouse_forte_full(),
                            max(0.0, self.HEAVY_MAX - self.HEAVY_MIN),
                            '仇远 回路重击打完')
        finally:
            self.task.mouse_up()
        self._heavy_done = True
        self.sleep(0.05)
        return True

    def ensure_heavy(self):
        """离场之前兜底: 这一轮还没打出回路重击的话, 在这里等回路、打掉再走。

        2026-09-21 用户: 「R 能量不满触发了保底, 但是后面没有打回路重击 ——
        一定要打回路重击 Q 切人」。两条路都会导致漏掉:
          * R 的等待窗口吃掉几秒 -> 回路闸(FORTE_WAIT)到点还没满 -> 整轮没有重击;
          * 协奏先满了 -> fill_con() 立刻返回 -> 直接 Q 走人。
        所以这一步【排在 fill_con 前面】, 而且不看协奏 —— 协奏够不够跟这一发重击没关系。
        """
        if self._heavy_done:
            return True
        self.logger.info('qiuyuan: 这一轮还没打出回路重击, 离场前补一次')
        if not self.require('仇远 回路满(补)', self.is_mouse_forte_full,
                            action=self.click, timeout=self.HEAVY_RESCUE_CAP):
            self.logger.warning('qiuyuan: 补等回路还是没满, 照样按住试一下')
        self.task.mouse_down()
        try:
            self.sleep(self.HEAVY_MIN)
            self.hold_until(lambda: not self.is_mouse_forte_full(),
                            max(0.0, self.HEAVY_MAX - self.HEAVY_MIN),
                            '仇远 补的回路重击打完')
        finally:
            self.task.mouse_up()
        self._heavy_done = True
        self.sleep(0.05)
        return True


    def fire_echo(self):
        """Q 声骸 —— 【连发, 不是只发一次】。

        演出期间的按键是被游戏丢弃的、不排队, 只发一次很容易整段被吃掉。
        Q 是脱手技能, 多按几下不会打断角色当前的动作, 所以连发是安全的。
        读到进冷却就提前收手; 读不出来就把 ECHO_TRIES 次发满 —— 按在冷却上是空操作。

        【现在是在回路重击打完之后调的】, 不再往重击动画里塞 —— 第二三段期间
        Q 本来就放不出来 (日志里一串 `echo still reads available after 3 tries`)。
        """
        # 【无论如何先发一次, 读屏只用来决定要不要重发】: 读到"在冷却"就不发的话,
        # 读错一次代价就是整轮没有声骸 —— 而按在冷却上只是个空操作, 不要钱。
        was_available = self.echo_available()
        if not was_available:
            self.logger.info('qiuyuan: echo reads on-cd, sending once anyway')
        for i in range(self.ECHO_TRIES):
            self.send_echo_key()
            self.record_echo_use()
            self.sleep(self.ECHO_RETRY, check_combat=False)
            if not was_available:
                # 进来就在冷却, "现在也在冷却"证明不了任何事, 重发没意义
                return False
            if not self.echo_available():
                if i:
                    self.logger.info(f'qiuyuan: echo landed on try {i + 1}')
                return True
        self.logger.warning(f'qiuyuan: echo still reads available after '
                            f'{self.ECHO_TRIES} tries')
        return False

    # ------------------------------------------------------------------ 离场

    def leave(self):
        """[一定先打出回路重击] -> 协奏没满不许走 -> Q -> 按数字键直接切西格莉卡。

        她带不带变奏走人, 决定的是西格莉卡开不开得了那套主 C 轴 —— 西格莉卡的
        do_perform 里"空手入场"走的是完全不同的分支(只丢个声骸就交回守岸人)。
        """
        # 【重击排在协奏闸前面】: 协奏先满的话 fill_con() 会立刻返回, 这一发重击就漏了
        self.ensure_heavy()
        con_ok = self.fill_con()
        # 【Q 就放在这一刻】: 协奏刚满的这一帧手上没有演出在演, 是这一整轮里 Q 最不
        # 容易被吞的时刻; 而且紧接着就切人, Q 的召唤物照样留在场上打
        self.fire_echo()
        if not self.direct_switch(assume_intro=con_ok):
            self.logger.warning('qiuyuan: direct switch failed, falling back to framework')
            self.switch_next_char()


    def fill_con(self):
        """等协奏满 —— 【期间盯着回路, 满了就再补一发回路重击】。

        用户 2026-08-25: 「仇远加个保底, 如果流程被打断太多, 至少要打到协奏圈满了
        再切人啊」。被打断的那几轮, 光靠点普攻攒协奏太慢, 而回路重击是她攒得最快的
        手段 —— 所以等的时候一直看回路, 满一次就再放一次, 顺便协奏也上去了。

        跟原来的 require(is_con_full, action=self.click) 的区别:
          * 原来是 6 秒上限、纯点普攻; 现在 12 秒, 而且会补重击;
          * 原来的轮询间隔是公共段的 GATE_POLL(0.12), 拿来当普攻节奏太快;
        这跟西格莉卡的 fill_con 是同一个形态 —— 【等待段也要继续输出/继续检测】。
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
                self.logger.warning(f'qiuyuan: 还在等协奏 ({elapsed:.1f}s, '
                                    f'con={self.get_current_con():.2f}), 继续打')
            if self.is_mouse_forte_full():
                # 回路又满了 —— 再放一发重击, 这是她攒协奏最快的手段
                self.logger.info('qiuyuan: 等协奏的时候回路又满了, 补一发回路重击')
                self.task.mouse_down()
                try:
                    self.hold_until(lambda: not self.is_mouse_forte_full(),
                                    min(self.HEAVY_MAX, left), '仇远 补的回路重击')
                finally:
                    self.task.mouse_up()
                self.sleep(0.05)
                continue
            self.check_combat()
            self.click()
            self.sleep(self.A_INTERVAL)

    # ------------------------------------------------------------------ 小工具

    def switch_in_anchor(self):
        """本轮的计时基准: 头像认出「切人完成」的那一刻, 不是 do_perform 被调那一刻。"""
        now = time.time()
        anchored = self.last_switch_in_time
        gap = now - anchored if anchored > 0 else -1
        if 0 <= gap <= self.SWITCH_ANCHOR_MAX:
            return anchored
        return now
