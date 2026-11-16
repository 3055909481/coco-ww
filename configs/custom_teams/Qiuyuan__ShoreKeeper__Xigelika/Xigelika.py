import time

from src.char.ShoreKeeper import ShoreKeeper as NativeShoreKeeper
from src.char.Xigelika import Xigelika as NativeXigelika


class Xigelika(NativeXigelika):
    """西格莉卡 —— 西仇守「25s 轮椅轴」的主 C, 也是整条轴的收尾。

    轮换环: 守岸人 -> 仇远 -> 西格莉卡 -> 守岸人。她这一棒最长, 而且【开场那一下
    和站场那一段是两件完全不同的事】, do_perform 按有没有带变奏进场分流:

      * 空手入场 (开局第一帧, 或者轮换散了) -> opener_toss():
        只丢一个声骸吃一层增伤, 然后立刻把场子交给守岸人去攒协奏。
        视频 t=1.83 按 Q, t=2.87 切人; 字幕「开局西格莉卡先丢个声骸技能」「获得一层增伤」。

      * 带变奏入场 -> main_axis(): 下面这一整套。

    轴的来源: E:\\轴视频\\西仇守.mp4。时间是墙钟秒。启动轴和循环轴对她来说只差一处:
    启动轴的 Q 在开局就用掉了(冷却 70 秒), 所以那一轮开头只有 R;
    循环轴是 Q + R 一起。代码里两条走同一条路 —— 冷却上的 Q 按下去是空操作。

                                     启动轴            循环轴
        (Q +) R                   25.70 - 33.90     73.90 - 80.97
        E + a3a4 + 强化A           (跟上一格同属一个步骤条格子)
        回路重击 + 闪避             34.03 - 39.57     81.00 - 82.47
        一套普攻(4A) + 强化A         40.40 - 43.20     82.50 - 86.40
        回路重击 (+ 补 Q)           43.23 - 46.17     86.60 - 87.40
        回路E (长按)               46.20 - 48.57     88.20 - 89.97
        A + 处决                   48.60 - 50.30     90.00 - 91.40
        -> 变奏守岸人               50.33 - 52.90     91.43 - 92.50

    循环轴逐帧节点 (技能栏冷却数字为证, 她的 E 冷却 10 秒、R 冷却 25 秒):
        73.90  变奏入场
        74.35  Q          74.80  R (75.2 - 77.4 大招演出)
        77.45  E          <- 字幕「这里E技能可以打断大招后摇」
        78.6 / 79.2  a3 a4
        80.4   强化A       81.0 - 82.2  回路重击     82.2  闪避
        82.8 - 85.5  一套普攻(4A)        85.8  强化A
        86.6 - 87.4  回路重击            88.2 - 90.0  回路E(长按)
        90.3   普攻 + 处决               91.4  变奏守岸人

    字幕原文: t=26.0「西格莉卡直接释放大招」 t=28.8「然后E技能」
    t=30.3「这里E技能可以打断大招后摇」 t=33.0「接着打a3a4+强化A」
    t=35.5「强化A的时候按住普攻释放回路重击」 t=38.3「回路重击招式出来后马上闪避」
    t=39.5「打断后摇」 t=40.5「然后再打一套」 t=43.0「强化A+回路重击」
    t=45.0「释放回路重击的时候长按E技能」 t=46.9「这样可以直接衔接回路技能」
    t=48.1「出招更快」 t=49.4「回路技能第二段伤害出来后」 t=50.5「普攻+处决」。

    【视频里那个 UP 主的小抄跟画面对不上的一处】: 循环轴的步骤条第 9 格写的是
    「西 | E-2A-强化A」, 但同一段的字幕写的是「一套普攻+强化A」, 而技能栏的 E 冷却
    从 79.0 秒的 8.4 一路数到 85.0 秒的 2.4 中间没有跳回去过 —— 那一段【没有按 E】。
    攻略图上写的也是「4A+强化A」。所以这里按 4A 实现, 步骤条那个 E 当笔误。
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

    # ================================================================ 西仇守: 西格莉卡这一棒

    # ---- 时间常量。来自视频墙钟, 单位秒 ----
    INTRO_ANIM = 0.45       # 变奏入场 -> 该按 Q。视频: 73.90 -> 74.35
    ECHO_TO_LIB = 0.05      # Q -> R。fire_echo 自己就要花几百毫秒连发, 这里不用再等
    # Q 连发几次。【读屏只用来提前收手, 不用来提前放弃】(2026-08-25 实机: "Q又没放"):
    # 上一版一读到"在冷却"就只发一次走人 —— 读错一次这一轮就没 Q 了。
    # 用户原话「多按几次, 西格莉卡Q是脱手的, 不会打断角色」, 所以改成【无条件按满】,
    # 只有真的确认放出去了(进来是好的、现在读到冷却)才提前停
    ECHO_TRIES = 5          # 声骸是好的时候最多连发几次(读到进冷却就提前收手)
    # 读屏说"在冷却上"时只发这么几次。她的声骸 70 秒冷却、这一棒才 25 秒, 循环里
    # 多半是在冷却上的 —— 而第一发 Q 正卡在变奏入场和大招中间, 按满 5 次是 0.6 秒的
    # 空操作, 大招就被推后这么多(实机"入场技能都打完了愣了一下才R")。
    # 【不要改成 0】: 读屏也会读错, 留两下当保险, 代价只有 0.16 秒
    ECHO_TRIES_CD = 2
    ECHO_RETRY = 0.08       # 连发间隔。好的时候 5 x 0.08 封顶 0.4 秒, 冷却上 0.16 秒
    LIB_TO_E = 0.05         # 大招演出结束 -> E。字幕说这一下是拿来打断大招后摇的,
                            # click_liberation() 本来就会阻塞到回到队伍界面才返回,
                            # 所以这里几乎不用等
    E_TO_A = 0.60           # E -> 开始点普攻。E 的动画会吞掉前面几下, 但不吞完
    A_INTERVAL = 0.18       # 普攻节奏。框架自己的 continues_normal_attack 用的是 0.1
    FORTE_POLL = 0.05       # 轮询回路指示器的间隔。现在判据是【算颜色占比】不是找图,
                            # 一次只算一个 140x120 的小框, 比模板匹配便宜得多, 可以放密
    # ---- 强化重击的按住时长, 分两段算 ----
    # 【锚点是"黄灭了"那一刻, 不是"按下"那一刻】。
    # 按下去到重击真正起手之间隔着一段不定长的延迟 —— 黄是"强化A 刚打出"就亮的,
    # 那一下的动画还得演完才轮得到重击, 视频里这段是 0.6~1.0 秒, 每次都不一样。
    # 从"按下"起算固定时长就会把这段延迟混进来, 松手/闪避/接回路E 的时刻整体飘。
    # 按下之后等"黄灭"(= 重击起手)的上限。
    # 【0.60 太短, 重击根本来不及出】(2026-09-20 实机): 按住 0.67 秒松手时黄还是亮的
    # (y=0.06~0.08), 说明这一下压根没起手; 而按住 1.67 秒那几次, 松手时黄是 0.02~0.04
    # = 已经出去了。所以这个值要盖住"按下去到重击真出手"的全程, 不是盖住检测延迟。
    HEAVY_START_WAIT = 1.60
    # 读不到"黄灭"时当作起手的偏移量 —— 【只拿来算松手时刻, 不拿来闪避】,
    # 估错了闪避就会把还没出手的重击顶掉(见 after_heavy_dodge)
    HEAVY_ASSUME = 0.25
    # 起手之后还按住多久。【1.20 -> 0.45】: 视频里重击起手 0.40~0.45 秒伤害就落地
    # (循环轴第一个重击 125.97 黄灭 / 126.40 出 20052), 之后全是后摇。
    # 起手就是已经交出去的动作, 松手不会截断 —— 视频里作者起手后 0.20 秒就松手去长按 E,
    # 重击照样打满。按满 1.2 秒只是把闪避和回路E 一起往后拖了将近一秒。
    # 读到强化E 就绪时更早: 那一下直接松手接回路E, 根本不按满(见 hold_heavy)
    # 起手之后还按住多久。【必须 <= DODGE_AT】—— 闪避是松手之后才发的,
    # 这个值比 DODGE_AT 大的话, 它就变成了闪避的实际时刻
    HEAVY_AFTER = 0.25
    # 闪避的时刻, 【从"黄灭"算起, 不是从松手算起】。
    # 调参轨迹 0.65 -> 0.45 -> 0.35, 每次实机都说"还能更快"。
    # 【0.35 已经在视频量的伤害落地时刻(起手后 0.40~0.45 秒)之前了】, 还能成立只能说明
    # 实机这个锚点(鼠标提示消失)比视频里那个金圈熄灭要晚一点, 两边不是同一个零点。
    # 所以往下调是【实机说了算】, 但盯住一个症状: 一旦重击又开始打不出来/被顶掉,
    # 就是过头了, 退回上一档。日志里 `dodge at +X.XXs` 是实际值, 比设定值大 0.02 左右
    DODGE_AT = 0.35
    # 闪避之后马上连打多久, 之后才交给补Q / 等黄那一段。
    # 【别给 0】: 那一路第一件事是读屏(模板匹配), 连段会断在读屏上, 实机看着就是
    # "闪完愣一下才继续A"。给到能盖住那次读屏 + 补Q 的头一下就够
    DODGE_RESUME = 0.30
    # "黄要连续灭满这么久"才算起手。特效会把这一格闪一下, 单帧读数不作数;
    # 视频里这种假灭最长 0.13 秒, 但那是在 0.015 的阈值下量的 —— 现在阈值抬到 0.05,
    # 0.12 秒够滤掉闪烁, 再长就会把起手的锚点整体拖后
    HEAVY_GONE_HOLD = 0.12
    # 按住期间顺便确认"强化E 变黄"的上限(从"黄灭"算的绝对时刻)。
    # 视频里回路一满 E 提示立刻就上来: 重击起手 +0.05 秒出现, 作者 +0.20 秒就长按 E。
    # 所以这一步放在【松手之前】—— 读到了就立刻松手接回路E(这一下不闪避),
    # 读不到才走闪避那条路。0.40 是给 HEAVY_GONE_HOLD 那 0.2 秒留的余量
    HEAVY_E_PROBE = 0.40
    # 【第二个重击 -> 回路E 之间必须留间隔】(2026-08-25 实机: "总是强化E放了才打强化重击"):
    # 原来松手后 0.15 秒就长按 E, E 是瞬发的, 直接插到重击动画里去了, 看起来就是
    # E 先出、重击后出, 而且重击被打断还得重来, 更慢。
    # 最后一个强化重击 -> 回路E 的间隔。【实机调出来的, 别照视频改回去】:
    # 视频里量出来是 0.8 秒 (第二个重击 86.60-87.40, 回路E 88.20 才开始),
    # 但那是人手的节奏; 实机 1.00 -> "慢了", 0.70 -> 还是"慢了", 定在 0.30。
    # (下限在 0 附近: 太贴着松手按, E 会插进重击的动画里, 变成"E先出、重击后出")
    # 0.30 实机还是"衔接等的太久了" -> 0.15。
    # 这一段之外还有一笔开销要一起算: forte_resonance 里的重复读屏
    # (已经用 confirmed 干掉了)。确认强化E 那一步现在在按住重击的中途就做完了
    # 0.30 -> 0.15 -> 0.05 -> 0: 用户四次都说"还能更快"。
    # 敢压到 0 是因为按 E 之前是【确认过 e_forte 那一格显示成字母E】的,
    # 而那一格只在重击打完、回路满了之后才变 E —— 不会再插进重击的动画里。
    # 现在衔接时间几乎全部来自"确认"那一步, 见 FORTE_E_POLL
    HEAVY_TO_FORTE_E = 0.0
    # 【闪避重新打开】: 轴上这一格本来就写着「回路重击-闪」, 闪避是用来断重击后摇的。
    # 之前关掉是因为闪的时机不对(按满 1.2 秒才闪, 那时后摇已经过完了, 只剩往后拉),
    # 现在锚在"黄灭 + DODGE_AT", 落在伤害已经出、后摇还没走完的那一段。
    # 【闪避只发生在重击循环里, 而且只在不是最后一个重击的时候】——
    # 最后一个要立刻接回路E(见 hold_heavy 的 may_finish);
    # 等协奏 / 找处决那些地方补打的重击【一律不闪】(hold_heavy 的 dodge 默认 False),
    # 那些不是轴上的动作, 闪一下只会把人往后拉
    DODGE_AFTER_HEAVY: bool = True
    # 回路E 的长按时长。【0.60 实机疑似没触发长按】—— 游戏一般 0.3~0.5 秒算长按,
    # 但按键是 PostMessage 投进去的, 中间还隔着窗口的输入采样, 留足余量更保险
    FORTE_E_HOLD = 0.90
    FORTE_E_SETTLE = 0.20   # 松开 E 之后等多久再去读"E 进冷却了没有"
    FORTE_E_TRIES = 2       # 长按没放出去就再来一次
    # (FORTE_E_TO_BREAK 已经没了: 处决的下限改成从【按下E 那一刻】算, 见下面的
    #  FORTE_E_SLAM —— 从"松手"算会随长按时长和读屏耗时飘, 而下砸的时刻是固定的)
    # 【处决不许早于"按下E 之后这么久"】(2026-09-20 实机: 处决把强化E 的下砸吞了)。
    # F 处决会直接打断她当前的动作, 而回路E 最大的那一下砸在整条轴里伤害最高,
    # 抢在它前面按 F 等于白打这一整套。
    # 【1.55 -> 2.30】: 1.55 是按"视频里按下E 到出砸 1.10 秒"定的, 漏算了长按阈值本身
    # —— 游戏要按住一段时间才判成长按, 技能起手比按下键晚, 砸也跟着晚。
    # 实机 1.55 秒照样被吞, 说明这个偏移不小。视频里作者是等到 +2.5 秒才按 F 的,
    # 2.30 仍然比他早 0.2 秒。等的这段时间一直点普攻(轴上这一格写的就是「A-处决」),
    # 不是站着发呆
    FORTE_E_SLAM = 2.30
    # 找处决提示的【窗口】。0 = 只看一帧, 不开窗口。
    # 用户 2026-08-25: 「直接检测有没有F, 没有就过切人就行。一般怪不死都是有的,
    #   就算错过了让守岸人打就是了」。1.4 -> 0.5 -> 0(只看一帧)
    BREAK_WINDOW = 0.0
    OPEN_ECHO_TO_SWITCH = 0.05
    # 【协奏没满就走人是整条轴最贵的失败】: 下一棒守岸人就拿不到变奏, 多打一整组。
    # 实机日志里 26 次 `con not full before switch`, 读数 0.74~0.97 —— 全是差一点。
    # 所以这道闸放宽, 而且下面还加了"协奏没满就接着补强化重击"的额外轮次,
    # 等协奏的这段时间不再是站着平A
    CON_GATE_TIMEOUT = 8.0
    # ---- 强化重击的轮次: 【不写死打几轮, 靠"强化E好了"收尾】 ----
    # (2026-08-25 用户定的保底方案, 原话:
    #  「前面只管A, 看到重击亮了长按重击, 一直到最后强化E亮起, 说明走到最后一段了,
    #    那就是要第二段强化重击接E, 切人」)
    # 好处是整段不再依赖"这一轮的重击到底打没打出去"这个我判不出来的东西 ——
    # 被打断就多打一轮, 反正退出条件是强化E, 不是轮数。
    # 收尾条件是【强化E 变黄】, 这里是"至少打几个重击才允许收尾"的下限。
    # 【1 -> 2】(2026-09-21): 循环轴轴面板写的就是两个重击, 而且第一个后面那一格是
    # 「回路重击-闪」—— 闪避只在"不是最后一个重击"时发生, 所以 MIN=1 的时候
    # 第一个重击一旦探测到强化E 就直接收尾, 闪避跟着一起没了。
    # 启动轴只有一个重击也不会卡: 回路先满时 attack_until_heavy 会读到字母E,
    # heavy_loop 那一路(E_WATCH_GRACE 之后)照样收尾
    MIN_HEAVY_ROUNDS = 2
    # (原来的 FORTE_E_CHECK 已经没了: "确认强化E 变黄"这一步从"松手之后"挪进了
    #  按住的中途, 见 HEAVY_E_PROBE —— 回路是重击打出那一刻就到账的, 手不用先松)
    # 确认"强化E 变黄"时的轮询间隔。【衔接快不快就看这个数】:
    # 去抖要连读 FORTE_CONFIRM(2) 帧, 每帧等一个轮询间隔,
    # 所以最小检测延迟 = 2 * FORTE_E_POLL。用普通的 0.05 就是 0.10 秒起,
    # 压到 0.01 之后是 0.02 秒起 —— 加上 next_frame 的开销, 实测衔接 0.13 -> 0.05 秒。
    # 【别把 FORTE_CONFIRM 降到 1 来提速】: 去抖是防特效闪光误判的, 那个坑踩过
    FORTE_E_POLL = 0.01
    # 等重击的时候顺便盯强化E 的间隔。【比重击那一路读得稀】: 它要多做一次模板匹配,
    # 每轮都读会把普攻的节奏挤掉; 而这一路只是"别错过回路先满了"这件事, 不用很密
    FORTE_E_WATCH_EVERY = 0.15
    # 一个重击都还没打出来就读到强化E 时, 至少等这么久才认。
    # 【别给 0】: forte_e_ready() 误读一次就会把两发重击整个跳掉;
    # 【也别给太大】: 回路真的先满了的话, 多等的每一秒都是在等一个不会来的鼠标提示
    E_WATCH_GRACE = 3.0
    # 【整段只有这一个总预算, 没有"每轮超时"】(2026-08-25 用户: "要持续检测啊,
    # 不要过了个点就不重击了开始点按了")。上一版是分轮 + 每轮 6 秒超时 + 最多 5 轮,
    # 被怪打断几次就把轮次耗光, 然后剩下的时间纯点普攻、再也不重击了 ——
    # "没跑完就切人"就是这么来的。现在改成一个从头跑到尾的循环, 黄一亮就长按,
    # 退出条件只有"强化重击和强化E同时是黄的"; 这个预算只是防判据失灵时永远不出来
    HEAVY_LOOP_BUDGET = 20.0
    CON_FILL_SLICE = 1.0     # 等协奏满时, 每次先打这么久普攻再回头看一眼协奏
    # hold_heavy() 每次松手时写进来的"这一下按住了多久(从起手算)", 只用来打日志。
    # 【写成类属性是为了它在第一次按住之前也存在】—— 不然静态检查扫不出来,
    # 而且哪天有人把 getattr 的默认值去掉就会炸
    heavy_hold_after = -1.0
    # 最后一次按下回路E 的时刻。处决的下限是从这个时刻算的(见 settle_before_break),
    # 【写成类属性】是为了它在第一次长按之前也存在 —— 静态检查扫得出来, 也不用 getattr 兜
    forte_e_pressed_at = 0.0
    SWITCH_ANCHOR_MAX = 1.5
    # ---- 离场 (见 leave() 的注释: 处决之后切人 7/7 全失败) ----
    SWITCH_TIMEOUT = 3.0     # 覆盖公共段的 2.0 —— 演出之后队伍 HUD 要多给点时间
    TEAM_HUD_WAIT = 2.5      # 按数字键之前先等 HUD 渲染回来的上限
    LEAVE_TRIES = 2          # 直接切人失败重试几次, 之后才掉回框架

    # ======================= 强化普攻 / 强化重击 指示器 =======================
    # 【2026-08-25 用户给了截图, 位置终于定死了】
    # 就是技能栏最左边那一格 (骷髅目标图标的右边、声骸装置的左边, E 往左数第二格):
    #     绿色光环 + 拳头图标  = 强化普攻可用
    #     金黄光环 + 剑刃图标  = 强化重击可用
    #     没光环(暗)           = 都不能放
    # 位置是拿视频里的 E/Q/R 三个已知框 (box_resonance/box_echo/box_liberation) 做锚点
    # 反算出来的: 视频里这一格中心在 x=957, 而 E=1081 / Q=1145 / R=1210,
    # 拟合出 norm = 0.0276 + 0.000758 * video_x  ->  这一格 norm ≈ 0.7356 ~ 0.7720,
    # 换算到 3840x2160 就是下面的 (2824,1875)-(2962,1995)。
    # 交叉验证: 它正好落在 assets 里 box_extra_action [2586,1846,375,161] 的右半边 ——
    # 那个框太宽(盖住了左边两格), 所以自己开一个窄的。
    FORTE_ICON = (2824, 1875, 2962, 1995)

    # 光环颜色 —— 【按"通道之间差多少"算, 不用绝对的 r/g/b 区间】。
    # (2026-08-25 实机第六轮改。)
    # 绝对区间的毛病: HUD 泛光会把光环的芯整个吹成白色 —— probe 里那行
    # `rgb=252,250,246` 就是。蓝通道一旦被抬过区间上限, 那一片就整个不算数了。
    # 同一个"黄亮着"的状态, 两场实机差了一倍多:
    #     13:30  y=0.11~0.23        13:44  y=0.02~0.08 (场景更亮, 于是漏判, 一直平A)
    # 改成看"红/绿比蓝高多少": 金色不管多亮, 蓝通道总是明显低于红绿; 纯白三通道齐平,
    # 自然被排除。拿视频那一格对比过, 分离度好一大截:
    #     绝对区间   亮 0.03~0.19 / 不亮 0.00
    #     通道差     亮 0.19~0.66 / 不亮 0.00      <- 用这个
    YELLOW_RB = 35      # 红 - 蓝 至少差这么多
    YELLOW_GB = 20      # 绿 - 蓝 至少差这么多
    YELLOW_MIN_R = 140  # 太暗的像素不算
    GREEN_GR = 25       # 绿 - 红 (绿只用来打日志, 不参与判断)
    GREEN_GB = 10
    GREEN_MIN_G = 140
    # 判据连读多少帧一致才认。【别去掉】: 上一版用单帧找图, 被特效闪光骗到,
    # 点下 A 才 37 毫秒就判定"强化普攻已经打出去了"。颜色比找图稳, 2 帧够了
    FORTE_CONFIRM = 2

    # ---- 重击预输入 (2026-08-25 用户要求: "正常来说应该在强化普攻时就开始长按重击
    #      预输入, 这样打的更快") ----
    # 打开时, 等黄的那段普攻不是"点一下就松", 而是【按住 PREINPUT_HOLD_TIME 再松】,
    # 按住期间照样在轮询: 黄一亮【就不松手了】, 直接接进强化重击 ——
    # 等于强化普攻还在演的时候手就已经按下去了, 重击不用再等我先按一次。
    # 按住时长压在 0.15 秒是怕被判成蓄力重击(那个一般要 0.3 秒以上)。
    # 觉得连段变味了就把它关掉, 退回一下一下点。
    # 【默认关掉】(2026-08-25 实机: "中间有段A的断断续续的, 尤其是被打断了以后"):
    # 按住 0.15 秒再松这个节奏, 游戏很可能当成"想蓄力又放弃", 连段接不上去,
    # 表现就是普攻一顿一顿的。省下的那点检测延迟不值这个价 ——
    # 黄的轮询本来就是 0.05 秒一次, 慢不了多少。
    # 想再试就打开, 顺便把 PREINPUT_HOLD_TIME 往下压(0.10 试试)。
    PREINPUT_HOLD: bool = False
    PREINPUT_HOLD_TIME = 0.15
    PREINPUT_GAP = 0.05
    # 长按E 之前等 e_forte 提示的上限。【别给太长】: 这个模板时灵时不灵
    # (实机既出现过 `after 0.07s` 也出现过 `prompt not seen`), 等不到就该直接按,
    # 1.5 秒的等待纯粹是白站
    FORTE_E_WAIT = 0.5
    FORTE_LOG_EVERY = 3.0

    # 视频里 UP 主是在回路重击【还在演的时候】就长按 E 抢时间的
    # (字幕「释放回路重击的时候长按E技能 / 这样可以直接衔接回路技能 / 出招更快」)。
    # 这里默认【先按完重击再按 E】: 按住鼠标期间发键有把长按打断的风险 ——
    # PostMessage 后端的 send_key 会投一条 WM_ACTIVATE, 窗口收到就可能松开左键。
    CHAIN_E_INTO_HEAVY: bool = False


    @property
    def NEXT_SLOT(self):
        """打完切守岸人。按类找人, 不写死队伍位置。"""
        for char in self.task.chars:
            if isinstance(char, NativeShoreKeeper):
                return char.index + 1
        return None

    def reset_state(self):
        super().reset_state()
        # 【必须写在 reset_state 里】: 换类时 __dict__.update 会把"内置也有、我改了值"
        # 的字段打回内置值。她不是治疗, 内置的 check_f_on_switch 是 True ——
        # 不重申的话框架会在切人时替她再按一次 F, 把 execute_break() 刚打完的处决重放
        self.check_f_on_switch = False
        # 上一轮的回路E 时刻不能带到这一轮来, 否则处决的下限会被算成"早就过了"
        self.forte_e_pressed_at = 0.0

    def on_combat_end(self, chars):
        self.reset_fixed_rotation()
        super().on_combat_end(chars)

    # ------------------------------------------------------------------ 路由

    def do_perform(self):
        intro = self.take_intro()
        self.check_f_on_switch = False
        if self.flying():
            self.wait_down()
        if not intro:
            # 空手入场 = 要么是开局第一帧, 要么是轮换散了。两种情况的处理是一样的:
            # 她的整条轴都建立在"带变奏 + 大招能量满"之上, 硬打是白打, 不如把场子
            # 交回给守岸人重新起一圈
            return self.opener_toss()
        return self.main_axis()

    # ------------------------------------------------------------------ 启动轴的第 0 步

    def opener_toss(self):
        """开局: 只丢一个声骸吃一层增伤, 然后交给守岸人。

        视频 0.00 - 2.87 这一格。字幕「开局西格莉卡先丢个声骸技能」「获得一层增伤」。
        她的声骸冷却 70 秒, 这一下也顺便把冷却转起来 —— 等她第一次真正上场时
        (视频 25.7 秒) 还没转好, 所以启动轴那一轮开头只有 R 没有 Q。
        """
        self.logger.info('xigelika: opening toss (echo only), handing the field to shorekeeper')
        self.fire_echo()
        self.sleep(self.OPEN_ECHO_TO_SWITCH)

        # 【这里不走 direct_switch()】(2026-08-25 实机改)
        # 它第一件事是 wait_con_full() —— 轮询【0.8 秒】去读协奏, 好决定要不要把变奏
        # 交出去。但走到这条分支就意味着"空手入场"(开局第一帧, 或者轮换散了),
        # 协奏铁定是 0, 那 0.8 秒纯粹是白等。实机日志里
        #     09:13:02.615 opening toss
        #     09:13:03.771 con not full before switch (con=0.0)   <- 白等的 0.8 秒在这
        #     09:13:04.489 direct switched
        # Q 到守岸人接手花了 1.87 秒, 其中一半是这个。
        # 所以自己走 switch_to_index + handoff_to: 交接记账一样补, 只是不读协奏, 直接
        # 按 has_intro=False 交出去。
        target = self.char_at_slot(self.NEXT_SLOT)
        if target is not None and target is not self and self.switch_to_index(target.index):
            self.handoff_to(target, False)
            return
        self.logger.warning('xigelika: opening switch failed, falling back to framework')
        self.switch_next_char()

    # ------------------------------------------------------------------ 主轴

    def main_axis(self):
        anchor = self.switch_in_anchor()
        self.sleep(max(0.0, anchor + self.INTRO_ANIM - time.time()), check_combat=False)
        self.logger.info('xigelika main axis: (Q)R -> E -> [一直A, 黄一亮就长按重击, '
                         '重击后闪避断后摇, 循环到强化E变黄] -> 回路E -> A -> 处决 -> 切人')

        # ---- Q + R ----
        # 启动轴那一轮 Q 还在 70 秒冷却里, 这一下是空操作, 不用分支
        self.fire_echo()
        self.sleep(self.ECHO_TO_LIB)
        # click_f=False: 大招演出里框架默认每 0.1 秒发一次 F, 会把留给收尾的处决吃掉
        cast = self.click_liberation(wait_if_cd_ready=0.4, click_f=False)
        if not cast:
            self.logger.warning('xigelika: liberation not ready, running the rest without it')

        # ---- E 打断大招后摇 ----
        self.sleep(self.LIB_TO_E)
        self.cast_resonance()

        # ---- 强化重击循环: 【持续检测, 打到强化E 变黄为止】----
        # 整段不依赖"这一下重击到底打没打完"这个判不出来的东西: 指示器只告诉我
        # "起手了", 招式后来被怪打断是看不见的。被打断就是多打一个重击,
        # 反正退出条件是强化E 变黄, 不是轮数, 也不是时间
        self.sleep(self.E_TO_A)
        e_ready = self.heavy_loop(chain_e=self.CHAIN_E_INTO_HEAVY)


        # ---- 回路E: 【最后一个强化重击打完了】才长按 E ----
        # 中间必须留 HEAVY_TO_FORTE_E 的间隔 —— E 是瞬发的, 贴着松手按就会插进
        # 重击的动画里, 实机看到的"总是强化E放了才打强化重击"就是这么来的。
        # 视频里这个间隔是 0.8 秒 (第二个重击 86.60-87.40, 回路E 88.20 才开始)
        if not self.CHAIN_E_INTO_HEAVY:
            self.sleep(self.HEAVY_TO_FORTE_E)
            # confirmed: heavy_loop 是"确认强化E 就绪"才返回 True 的, 那就别再读一遍
            self.forte_resonance(confirmed=e_ready)

        # ---- 回路E 打完: 补A 到下砸伤害出来, 再看处决 ----
        # 处决会打断她当前的动作, 而回路E 最大的那一下砸落在按下E 之后 ~1.10 秒
        # (视频: 启动轴 117321 / 循环轴 92057) —— 早一帧按 F 就把整条轴伤害最高的
        # 一击丢掉。所以这里不是"睡一个固定尾巴", 而是【等到按下E 之后满 FORTE_E_SLAM】,
        # 期间一直点普攻(轴上这一格写的就是「A-处决」)。
        # 处决本身还是只看一眼(BREAK_WINDOW=0), 没有就直接走, 漏掉的交给守岸人
        self.settle_before_break()
        self.execute_break()

        self.leave()

    def after_heavy_dodge(self, started_at=None, confirmed=True):
        """强化重击之后的那一下闪避 —— 断后摇, 闪完立刻接普攻。

        【时刻锚在"黄灭"(= 重击起手), 不是锚在松手】: 松手的时刻会随按住时长变,
        而闪避要卡的那个窗口是固定的 —— 视频里重击起手 0.40~0.45 秒伤害落地,
        DODGE_AT(0.65) 就落在"伤害已经出、后摇还没走完"这一段。
        早了伤害还没出就被自己顶掉, 晚了她已经能动, 闪避只剩"把人往后拉"这一个效果,
        而她攻击范围小, 拉远之后更打不到怪 —— 之前关掉闪避就是因为踩在晚的那一侧。

        【锚点是估出来的就不闪】(2026-09-20 实机): 闪避会打断她当前的动作 ——
        重击还没出手就闪, 等于自己把重击顶掉, 实机连着四次一个重击都没打出来。
        所以"读不到起手"的时候, 该做的是继续按住重试, 而不是照着估的时刻闪一下。
        (闪避是唯一一个"估错了会造成伤害"的动作, 其它步骤照常按估的锚点走。)

        Args:
            started_at: 读到的"起手"时间戳。
            confirmed: 这个锚点是读出来的还是估的 —— 估的就不闪。
        """
        if not self.DODGE_AFTER_HEAVY:
            return
        if not confirmed:
            self.logger.info('xigelika: 没读到重击起手, 这一下不闪避(免得把重击顶掉)')
            return
        base = time.time() if started_at is None else started_at
        self.sleep(max(0.0, base + self.DODGE_AT - time.time()), check_combat=False)
        self.dodge()
        self.click()      # 【就是这一下要和闪避同一时刻】, 打日志都排在它后面
        # 【闪避和普攻是同一时刻开始的】(用户 2026-09-20 要求)。
        # 原来只在这里补一下 click(), 然后就层层返回去做 fire_echo ——
        # 那一路要先读一次声骸冷却(模板匹配)才会点第一下, 中间空出来的就是"闪完愣一下"。
        # 改成闪避那一帧就进连打, 一直打到 DODGE_RESUME, 交给后面的段无缝接上
        self.logger.info(f'xigelika: dodge at +{time.time() - base:.2f}s after the heavy started '
                         f'(锚点{"读到的" if confirmed else "是估的"})')
        self.keep_attacking(self.DODGE_RESUME)

    # ------------------------------------------------------------------ 动作

    def keep_attacking(self, duration):
        """连续点普攻这么久 —— 用来把两段之间的空隙填掉。

        闪避之后、补 Q 之前这些地方, 原来都是"补一下 click() 就去做别的",
        而别的那一路往往要先读屏(模板匹配几十毫秒), 连段就断在这儿。
        """
        if duration <= 0:
            return
        end = time.time() + duration
        while time.time() < end:
            self.click()
            self.sleep(self.A_INTERVAL, check_combat=False)

    def cast_resonance(self):
        """E。返回的是三元组 (clicked, duration, animated), 不是 bool。"""
        return self.click_resonance(send_click=False, time_out=1)[0]

    def fire_echo(self, click_between=False):
        """Q 声骸 —— 【连发, 不是只发一次】。

        (2026-08-25 实机: "西格莉卡循环里也不会放Q, 可以多点几次,
         反正 Q 也是脱手的不会打断角色"。)
        演出期间的按键是被游戏丢弃的、不排队, 只发一次很容易整段被吃掉 ——
        她这两下 Q 一次紧跟着变奏入场、一次夹在连段里, 都是容易被吞的位置。
        读到进冷却就提前收手; 读屏说在冷却上就只发 ECHO_TRIES_CD 次(按在冷却上是空操作,
        但多按几下要花时间, 而这一发正卡在变奏入场和大招中间)。

        【不用 click_echo(time_out=0)】: 它第一件事是读屏 echo_available(),
        读到 False 就掉进一秒起步的慢路径, 而刚打完大招那几帧读数最不可信。
        """
        # 读屏只用来【决定发几次】, 不用来"一次都不发" —— 读错的代价是这一轮没 Q。
        # 【在冷却上就只发 ECHO_TRIES_CD 次】(2026-09-20 实机: "入场技能都打完了愣了
        # 一下才R"): 她的声骸 70 秒冷却、这一棒才 25 秒, 循环里多半是在冷却上的,
        # 而这一发 Q 正卡在变奏入场和大招中间 —— 按满 5 次 x 0.12 秒 = 0.6 秒全是空操作,
        # 大招就被这 0.6 秒推后了。冷却上仍然发两下当保险, 只是不再泡满
        if click_between:
            # 【先打一下再读屏】: echo_available() 是模板匹配, 放在第一下之前就等于
            # 在连段里插了个空。Q 是脱手的, 顺序反过来没有副作用
            self.click()
        was_available = self.echo_available()
        tries = self.ECHO_TRIES if was_available else self.ECHO_TRIES_CD
        for i in range(tries):
            self.send_echo_key()
            self.record_echo_use()
            if click_between:
                # 连发期间别停手 —— Q 是脱手的, 普攻照打
                self.click()
            self.sleep(self.ECHO_RETRY, check_combat=False)
            if was_available and not self.echo_available():
                # 进来是好的、现在读到冷却 = 确认放出去了, 可以收手
                self.logger.info(f'xigelika: echo landed on try {i + 1}')
                return True
        return False

    # ------------------------------------------------------------------ 回路指示器

    def forte_icon_box(self):
        """技能栏最左边那一格的框 —— 强化普攻(绿)/强化重击(黄) 的光环就画在这里。

        坐标见 FORTE_ICON 的注释。用 box_of_screen_scaled 按当前分辨率换算,
        不要去动 FeatureSet 里的共享 Box。
        """
        x0, y0, x1, y1 = self.FORTE_ICON
        return self.task.box_of_screen_scaled(3840, 2160, x0, y0, x1, y1,
                                              name='xigelika_forte_icon')

    def forte_color(self):
        """返回 (绿占比, 黄占比)。

        按【通道之间的差】算, 不用绝对色值区间 —— 理由见 YELLOW_RB 那一堆常量的注释:
        泛光会把光环的芯吹成白色, 绝对区间一旦被蓝通道顶出去就整片不算数,
        同一个状态在两场实机里能差一倍多。通道差对亮度不敏感: 金色再亮, 蓝也总是
        明显低于红绿; 纯白三通道齐平, 自然排除。

        自己算而不是走 task.calculate_color_percentage —— 那个只支持 r/g/b 区间。
        代价很小: 一个 140x120 的小框, 没有找图开销, 所以 FORTE_POLL 能压到 0.05 秒。
        """
        import numpy as np
        box = self.forte_icon_box()
        try:
            img = box.crop_frame(self.task.frame)
        except Exception:
            return 0.0, 0.0
        if img is None or img.size == 0:
            return 0.0, 0.0
        b = img[:, :, 0].astype(int)
        g = img[:, :, 1].astype(int)
        r = img[:, :, 2].astype(int)
        total = float(r.size)
        yellow = float((((r - b) >= self.YELLOW_RB) & ((g - b) >= self.YELLOW_GB)
                        & (r >= self.YELLOW_MIN_R)).sum()) / total
        green = float((((g - r) >= self.GREEN_GR) & ((g - b) >= self.GREEN_GB)
                       & (g >= self.GREEN_MIN_G)).sum()) / total
        return green, yellow

    def forte_e_ready(self):
        """强化E 好了没有 —— 【只认 e_forte 模板】, 理由同 heavy_ready()。

        原来是"模板 or E图标黄 >= YELLOW_MIN"。两台机器的日志都证明颜色那条不能要:

            真 ready  (E模板=True)   E图标黄 0.13 ~ 0.55, 还有一次是 0.00
            误报      (E模板=False)  我们 0.10 / 0.11 / 0.24, 他 0.19 / 0.29 / 0.92

        误报最高 0.92、真 ready 最低 0.00 —— 【两边完全重叠, 没有阈值能分开】。
        而且误报全都发生在 E 处于冷却时(冷却蒙版本身就是黄的)。
        模板那一路把上面每一个真 ready 都抓到了, 还多抓到颜色漏掉的那次 0.00。

        【机制记一下, 免得再搞反】: 强化重击【增加】回路, 只有黄色的强化E 消耗全部回路。
        所以"强化E 变黄" ≈ "回路条满了", 它是这一段唯一真正的进度信号。
        鼠标提示和字母E 共用回路条右边那一格、互斥 —— 回路一满就从鼠标变成 E。
        """
        return bool(self.is_e_forte_full())

    def forte_e_signals(self):
        """三条判据分开返回 (E图标黄占比, e_forte模板命中, E在冷却)。

        只有第一个参与判断 (见 forte_e_ready), 另外两个纯粹是日志里的参考值 ——
        留着是因为出问题时能一眼看出是不是框选偏了。
        """
        y = 0.0
        try:
            box = self.task.get_box_by_name('box_resonance')
            if box is not None:
                y = self.box_yellow(box)
        except Exception:
            pass
        return y, bool(self.is_e_forte_full()), bool(self.has_cd('resonance'))

    def box_yellow(self, box):
        """某个框里"金黄"占多少 —— 跟 forte_color() 用同一套通道差判据。"""
        try:
            img = box.crop_frame(self.task.frame)
        except Exception:
            return 0.0
        if img is None or img.size == 0:
            return 0.0
        b = img[:, :, 0].astype(int)
        g = img[:, :, 1].astype(int)
        r = img[:, :, 2].astype(int)
        return float((((r - b) >= self.YELLOW_RB) & ((g - b) >= self.YELLOW_GB)
                      & (r >= self.YELLOW_MIN_R)).sum()) / float(r.size)

    def heavy_ready(self):
        """强化重击可用没有 —— 【只认回路条右边那个鼠标提示的模板】。

        【颜色那条兜底已经彻底拿掉】(2026-09-28, 拿另一台 1920x1080 的机器和我们
        3840x2160 的日志对出来的):

            forte [黄] 模板=True   我们 32 次 / 他 3 次   -> 每次都真的起手了
            forte [黄] 模板=False  我们 3 次  / 他 14 次  -> 几乎全部以
                                                            "按住 1.6~1.7s 没读到起手
                                                             (提示亮过=False)" 收场

        也就是说颜色兜底【一次都没救过场】, 只在制造误触发: 按下去、白按一秒七、
        回去重来。他那台机器一轮能这样空转四五次。

        为什么颜色在不同机器上差这么多: 这个框是按 3840x2160 的坐标换算的,
        1080p 上只有 69x60 像素, 大部分落在图标本身的暗金色底纹上而不是那圈光环。
        他那边【提示没亮时】颜色照样报 0.32~0.75(我们是 0.02~0.08)。
        日志里 n(框内亮像素数) 一眼能分: 真亮 677~862, 假亮恒定在 256~263。

        模板那一路带 binarize_for_matching, 而且是在一个小范围里搜, 不吃分辨率和
        整体亮度 —— 两台机器上都没错过。颜色只留在 forte_probe 的日志里当参考。
        """
        return bool(self.is_mouse_forte_full())

    def forte_probe(self, tag):
        """把这一格里最亮那些像素的实测 RGB + 绿/黄占比打进日志, 用来调阈值。"""
        import numpy as np
        box = self.forte_icon_box()
        green, yellow = self.forte_color()
        rgb = 'dark'
        try:
            img = box.crop_frame(self.task.frame)
            if img is not None and img.size:
                b = img[:, :, 0].astype(int)
                g = img[:, :, 1].astype(int)
                r = img[:, :, 2].astype(int)
                lum = r + g + b
                mask = lum >= max(300, int(lum.max()) - 90)
                if int(mask.sum()) >= 4:
                    rgb = (f'{int(r[mask].mean())},{int(g[mask].mean())},'
                           f'{int(b[mask].mean())} n={int(mask.sum())}')
        except Exception:
            rgb = 'n/a'
        # 【E 图标的黄一起打出来】: 那是"强化E 好了没有"的唯一判据(见 forte_e_ready),
        # 而它的阈值还没实机标定过 —— 靠这行日志看它在整段里怎么涨的
        ey, tpl, cd = self.forte_e_signals()
        mouse = bool(self.is_mouse_forte_full())
        self.logger.info(f'xigelika forte [{tag}] 鼠标模板={mouse} | 颜色 g={green:.2f} '
                         f'y={yellow:.2f} rgb={rgb} | E图标黄={ey:.2f} '
                         f'(E模板={tpl} 冷却={cd})')

    def wait_forte(self, cond, name, timeout, click, click_after=0.0, confirm=None,
                   poll=None):
        """等某个回路状态成立 —— 【连读 confirm 帧一致才认】。

        去抖不能省: 上一版用单帧找图, 被特效闪光骗到 —— 点下 A 才 37 毫秒就判定
        "强化普攻已经打出去了"(11:16:46 的日志), 于是按住普攻的时机提前到了起手里,
        重击自然接不上。

        Args:
            cond: 判据。
            name: 打日志用的名字。
            timeout: 0 = 不设上限 (唯一的出口是 check_combat() 抛异常)。
            click: True 时按 A_INTERVAL 的节奏打普攻。False 时只轮询不动手 ——
                这时要自己 next_frame(), 否则读的一直是同一张缓存帧。
            click_after: click=True 时, 头这么多秒【先不点】。等"转黄"要用它:
                强化普攻刚放出去的那零点几秒不该再补普攻, 但一直干等更亏
                (这一下要是被躲了, 站着等只会更慢), 所以过了这个点就一边等一边继续 A。
            confirm: 连续命中多少次才认, 默认 FORTE_CONFIRM。
            poll: 轮询间隔, 默认 FORTE_POLL。【衔接路径上要给小值】——
                去抖要连读 confirm 帧, 每帧都要等一个 poll,
                所以"检测出来"的最小延迟就是 confirm * poll。
        """
        confirm = self.FORTE_CONFIRM if confirm is None else confirm
        poll = self.FORTE_POLL if poll is None else poll
        start = time.time()
        last_click = 0.0
        last_log = 0.0
        hits = 0
        while True:
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if not click or elapsed < click_after:
                # click() 内部会 reset_scene 刷新帧; 不点的时候得自己刷
                self.task.next_frame()
            if cond():
                hits += 1
                if hits >= confirm:
                    self.logger.info(f'xigelika: {name} after {elapsed:.2f}s')
                    return True
            else:
                hits = 0
            if timeout > 0 and elapsed >= timeout:
                return False
            self.check_combat()
            now = time.time()
            if click and elapsed >= click_after and now - last_click >= self.A_INTERVAL:
                self.click()
                last_click = now
            if elapsed - last_log >= self.FORTE_LOG_EVERY:
                last_log = elapsed
                g, y = self.forte_color()
                self.logger.warning(f'xigelika: still waiting for {name} ({elapsed:.1f}s) '
                                    f'g={g:.2f} y={y:.2f}')
            self.sleep(poll)
    def attack_until_heavy(self, timeout, watch_e=True):
        """一直打普攻, 直到【重击提示亮】或者【强化E 先亮】。

        返回 (原因, 手是不是已经按着):  原因 = 'heavy' / 'e' / None(到点了)。

        【为什么也要盯强化E】(2026-09-21 实机: "被打断过, 后续强化E亮了也没马上接上放"):
        鼠标提示(可以重击)和字母E(可以放强化E)是【同一格的互斥状态】。被打断之后
        回路要是靠普攻先攒满了, 那一格直接显示成 E —— 鼠标提示就【再也不会亮】,
        而这里只盯鼠标提示, 于是一路 A 到整段预算耗尽。
        盯上之后: 谁先亮就接谁, 强化E 亮了就直接去放, 不再等那个永远不来的重击。

        【普攻和读屏解耦】: 上一版是"点一下 -> 干等 A_INTERVAL -> 才看一眼", 那 0.18 秒
        是瞎的。现在按 FORTE_POLL 连续轮询, 普攻只是"到点了就补一下"。
        强化E 那一路读得稀一点(FORTE_E_WATCH_EVERY) —— 它比重击那一路多一次模板匹配,
        每轮都读会把普攻的节奏挤掉。
        """
        start = time.time()
        last_log = 0.0
        last_click = 0.0
        next_e_watch = 0.0
        hits = 0
        e_hits = 0
        while True:
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if timeout > 0 and elapsed >= timeout:
                return None, False
            self.check_combat()

            if self.PREINPUT_HOLD:
                self.task.mouse_down()
                held_at = time.time()
                while time.time() - held_at < self.PREINPUT_HOLD_TIME:
                    self.task.next_frame()
                    if self.heavy_ready():
                        self.logger.info(f'xigelika: 强化重击可用(黄) after {elapsed:.2f}s '
                                         f'(预输入: 手没松, 直接接重击)')
                        return 'heavy', True
                    self.sleep(self.FORTE_POLL, check_combat=False)
                self.task.mouse_up()
                self.sleep(self.PREINPUT_GAP, check_combat=False)
            else:
                now = time.time()
                if now - last_click >= self.A_INTERVAL:
                    self.click()
                    last_click = now

            self.task.next_frame()
            if self.heavy_ready():
                hits += 1
                if hits >= self.FORTE_CONFIRM:
                    self.logger.info(f'xigelika: 强化重击可用(黄) after {elapsed:.2f}s')
                    return 'heavy', False
            else:
                hits = 0

            if watch_e and time.time() >= next_e_watch:
                next_e_watch = time.time() + self.FORTE_E_WATCH_EVERY
                if self.forte_e_ready():
                    e_hits += 1
                    if e_hits >= self.FORTE_CONFIRM:
                        self.logger.info(f'xigelika: 强化E 先亮了 after {elapsed:.2f}s, '
                                         f'不等重击了, 直接接回路E')
                        return 'e', False
                else:
                    e_hits = 0

            if elapsed - last_log >= self.FORTE_LOG_EVERY:
                last_log = elapsed
                g, y = self.forte_color()
                self.logger.warning(f'xigelika: still waiting for 强化重击可用(黄) '
                                    f'({elapsed:.1f}s) g={g:.2f} y={y:.2f} '
                                    f'鼠标模板={self.is_mouse_forte_full()} '
                                    f'强化E={self.forte_e_ready()}')
            if not self.PREINPUT_HOLD:
                self.sleep(self.FORTE_POLL, check_combat=False)

    def heavy_loop(self, chain_e=False):
        """一直打强化重击, 直到【强化E 变黄】为止 —— 那一下重击就是最后一个。

        轴上西格莉卡这一段是: 一套普攻 -> 强化A -> 回路重击 -> 闪 -> 再一套 -> 回路重击
        -> 回路E。循环轴两个重击、启动轴一个, 差别只在回路攒满得多快, 所以这里【不数轮次】,
        只看"强化E 黄了没"。

        整段【不分轮次、不设每轮超时】: 上一版是"每轮等黄 N 秒 x 最多 M 轮",
        被怪打断几次就把轮次耗光, 剩下的时间纯点普攻、再也不重击了 ——
        实机"没跑完就切人"正是这么来的。现在是一个从头跑到尾的循环, 黄一亮就长按,
        只有一个总预算 HEAVY_LOOP_BUDGET 兜底, 防止判据失灵时永远出不来。

        按住没起手也不用特判 —— 循环回去黄还亮着, 下一圈立刻又按住, 天然就是重试。

        【等重击的时候也盯着强化E】(2026-09-21 实机: 被打断之后 "强化E 亮了也没马上接上放"): 两个提示共用一格、互斥 —— 回路要是靠普攻先攒满, 那一格就成了字母 E,
        鼠标提示再也不会亮。谁先亮就接谁。

        Returns:
            bool: 强化E 是不是已经就绪(True 就可以直接长按 E, 不用再读一遍)。
        """
        start = time.time()
        heavies = 0
        while True:
            left = self.HEAVY_LOOP_BUDGET - self.time_elapsed_accounting_for_freeze(start)
            if left <= 0:
                self.logger.warning(f'xigelika: 重击循环跑满 {self.HEAVY_LOOP_BUDGET}s '
                                    f'(打出 {heavies} 个重击), 收尾')
                return False
            # 进来就已经黄了(上一圈刚攒满) —— 不用再打重击了, 直接收尾
            if heavies >= self.MIN_HEAVY_ROUNDS and self.forte_e_ready():
                self.logger.info(f'xigelika: 强化E 已经黄了 (已打 {heavies} 个重击), 接回路E')
                return True

            # 【等重击的时候也盯强化E】, 但一个重击都没打出来之前先不认 ——
            # forte_e_ready() 误读一次就会把两发重击整个跳掉。所以在 E_WATCH_GRACE
            # 之前不开这一路, 而且把这一次的等待也截到 grace 那一刻, 到点回来重估
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            watch_e = (heavies >= self.MIN_HEAVY_ROUNDS
                       or elapsed >= self.E_WATCH_GRACE)
            budget = left if watch_e else min(left, self.E_WATCH_GRACE - elapsed)
            why, held = self.attack_until_heavy(budget, watch_e=watch_e)
            if why == 'e':
                # 【回路先满了】: 这一格显示的是字母 E, 鼠标提示再也不会亮,
                # 继续等重击就是等一个不会来的东西
                self.logger.info(f'xigelika: 回路先满了(已打 {heavies} 个重击), 接回路E')
                self.forte_probe('强化E 先亮')
                return True
            if not why:
                continue
            # 【黄一亮就长按, 不管这是第几个】
            self.forte_probe('黄')
            # 收尾判断挪进 hold_heavy(): 回路是重击【打出的那一刻】就到账的,
            # 手还按着就能读到强化E 变黄, 不用等松手之后再花一轮去确认。
            # 读到了 -> 立刻松手接回路E; 读不到 -> 这一下不是最后一个, 闪避断后摇
            started, e_ready = self.hold_heavy(
                chain_e=chain_e, already_down=held,
                may_finish=(heavies + 1) >= self.MIN_HEAVY_ROUNDS,
                dodge=True)
            # 【按次数计, 不按"确认起手"计】: 收尾靠的是强化E 变黄那个独立判据,
            # 拿黄灭来计数只会在它失灵时把轮次卡死在 0, 顶上的收尾检查永远不生效
            heavies += 1
            if e_ready and heavies >= self.MIN_HEAVY_ROUNDS:
                self.logger.info(f'xigelika: 第 {heavies} 个重击把回路攒满了, 接回路E')
                return True
            # 攻略图上这一格写的是「回路重击-(Q)」: 好了就补, 没好就算。
            # 【补 Q 的时候不要停手】—— fire_echo 连发要花 0.6 秒, 干等会把连段断掉
            self.fire_echo(click_between=True)


    def wait_heavy_start(self, timeout):
        """按住之后等"黄灭"。返回 (读到没有, 当作起手的那一刻)。

        【读不到也必须往下走】(2026-09-20 实机翻车): 这一格的读数在不同机器/不同场景
        上差得很远(实机不亮 0.02~0.04, 视频里是硬 0.00), 阈值稍微定紧一点就永远
        读不到"灭"。上一版读不到就 return False 然后跳过闪避、跳过强化E 探测、
        连重击都不计数 —— 一个读屏判据失灵, 整段轴就空转到预算耗尽。
        现在读不到就按 HEAVY_ASSUME 估一个锚点, 后面该松手松手、该闪避闪避。

        另外两点:
          - "灭"要【连续满 HEAVY_GONE_HOLD 秒】才算, 挡掉特效闪一下造成的假灭。
          - 锚点取【这一串零的第一帧】而不是确认成立的那一帧, 不然多读的那点时间
            会把闪避和回路E 的时刻整体推后。
        """
        start = time.time()
        first_off = None
        seen_present = False
        while True:
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if timeout > 0 and elapsed >= timeout:
                _, y = self.forte_color()
                self.logger.warning(f'xigelika: 按住 {elapsed:.2f}s 没读到起手 (提示亮过={seen_present} y={y:.2f}), '
                                    f'按 +{self.HEAVY_ASSUME:.2f}s 估个锚点继续')
                return False, start + self.HEAVY_ASSUME
            self.check_combat()
            self.task.next_frame()
            if self.is_mouse_forte_full():
                seen_present = True
                first_off = None
            elif not seen_present:
                # 【按下去之后一次都没见过提示亮着】= 这一下根本不是踩在"可以重击"上,
                # 多半是颜色兜底误判进来的。提示"本来就没有"不能当成"它灭了",
                # 否则会白判一次起手, 后面照着这个假锚点闪一下 —— 实机那些
                # "不该闪的时候闪避"就是这么来的
                pass
            else:
                now = time.time()
                if first_off is None:
                    first_off = now
                elif now - first_off >= self.HEAVY_GONE_HOLD:
                    self.logger.info(f'xigelika: 强化重击起手(提示灭) after {first_off - start:.2f}s')
                    return True, first_off
            self.sleep(self.FORTE_POLL, check_combat=False)

    def hold_heavy(self, chain_e=False, already_down=False, may_finish=False,
                   dodge=False):
        """按住普攻放强化重击 —— 分两段, 【锚点是"黄灭了"那一刻】。

            按下 -> [黄灭 = 重击起手] -> (may_finish: 读强化E) -> 松手 -> 闪避断后摇

        为什么不从"按下"起算固定时长: 按下到起手中间有一段不确定的延迟(强化A 的动画
        还没演完), 全算进去的话松手、闪避、接回路E 的时刻会整体飘。

        【松手不会截断已经起手的重击】(2026-09-20 看视频确认): 作者在重击起手后 0.20 秒
        就去长按 E 了, 重击照样打满。所以起手之后只按 HEAVY_AFTER(0.45) 就松 ——
        伤害 0.40~0.45 秒落地, 剩下的全是后摇, 拿闪避断掉。

        may_finish=True 时【松手之前】先读一眼强化E: 回路是重击打出那一刻就到账的,
        视频里 E 提示在重击起手后 0.05 秒就亮了。读到了就直接松手接回路E, 这一下不闪避
        —— 闪避会把回路E 往后推半秒多, 而这时候已经没有下一个重击要接了。

        Returns:
            (起手了没有, 强化E 是不是已经就绪)
        """
        started = False
        e_ready = False
        anchor = time.time()
        if not already_down:
            # already_down: 预输入那一段手还按着, 【绝对不要再 mouse_down 补按】——
            # PostMessage 后端每次 mouse_down 都会投一条 WM_ACTIVATE, 窗口收到就松手
            self.task.mouse_down()
        try:
            started, anchor = self.wait_heavy_start(self.HEAVY_START_WAIT)
            if chain_e:
                # 【按住期间只发键, 绝对不要再 mouse_down 补按】
                self.task.send_key_down(self.get_resonance_key())
                self.sleep(self.FORTE_E_HOLD, check_combat=False)
                self.task.send_key_up(self.get_resonance_key())
                self.record_resonance_use()
                self.sleep(max(0.0, self.HEAVY_AFTER - self.FORTE_E_HOLD), check_combat=False)
            else:
                # 【起手之后才读强化E】: 回路是重击打出那一刻才到账的, 起手之前读到的
                # 只可能是上一轮的残留, 照着它松手就等于这一下重击白按
                # 【不看 started】: 读不到黄灭只说明这个判据失灵了, 不代表重击没出去。
                # 拿它当门槛就会一失灵整段都不做 —— 强化E 不探测、闪避不闪
                if may_finish:
                    left = anchor + self.HEAVY_E_PROBE - time.time()
                    if left > 0:
                        e_ready = self.wait_forte(self.forte_e_ready, '强化E 变黄(重击按住时)',
                                                  timeout=left, click=False,
                                                  poll=self.FORTE_E_POLL)
                    else:
                        e_ready = bool(self.forte_e_ready())
                # 读到强化E 就【立刻松手】接回路E, 不再按满 HEAVY_AFTER
                if not e_ready:
                    self.sleep(max(0.0, anchor + self.HEAVY_AFTER - time.time()),
                               check_combat=False)
        finally:
            self.task.mouse_up()
            self.heavy_hold_after = time.time() - anchor
        if e_ready:
            self.forte_probe('强化E 变黄')
        elif dodge:
            # 不是最后一个重击 -> 闪避断后摇, 闪完立刻接着 A
            self.after_heavy_dodge(anchor, confirmed=started)
        return started, e_ready

    def forte_resonance(self, confirmed=False):
        """回路E: 长按 E。视频 88.2 - 90.0 那一格。

        跟普通 E 走的是同一个键, 但吃的是回路而不是冷却 —— 所以不能用 has_cd 去判,
        也不用 click_resonance (它会因为"图标不亮"直接不发)。

        【判据换成"强化E 变黄"】(2026-08-25 实机: "第二段强化重击打出去, 没有马上接
        黄色的强化E"): 原来等的是 `is_e_forte_full()` 那个 e_forte 模板, 它时灵时不灵
        (日志里既有 `after 0.07s` 也有 `prompt not seen`), 不灵的时候要白等
        FORTE_E_WAIT 秒 —— 叠上前面的 HEAVY_TO_FORTE_E, 就成了"接不上"。

        而走到这一行的时候, heavy_loop() 的退出条件本来就是"重击和强化E 同时是黄的",
        所以【绝大多数情况进来就已经是好的, 一秒都不用等】。
        forte_e_ready() 是三条判据取或(E图标变黄 / e_forte 模板 / E 不在冷却),
        比单看模板稳得多。读不到也照按 —— 这一步之后只剩处决和切人, 赖在这里更亏。
        """
        # confirmed: heavy_loop 退出时刚确认过 E 就绪, 这里【一帧都不要再读】——
        # 每多读一次就多几十毫秒, 实机反馈"第二个强化重击和强化E衔接等的太久了"
        # 就是这些零碎叠出来的
        if confirmed:
            self.logger.info('xigelika: 强化E 刚确认过, 直接长按')
        elif self.forte_e_ready():
            self.logger.info('xigelika: 强化E 已经就绪, 立刻长按')
        elif not self.wait_forte(self.forte_e_ready, '强化E 就绪',
                                 timeout=self.FORTE_E_WAIT, click=False):
            self.logger.warning('xigelika: 强化E 没读到就绪, 照样长按')

        key = self.get_resonance_key()
        for attempt in range(1, self.FORTE_E_TRIES + 1):
            # 只记【第一次】按下去的时刻: 下砸的时间是从第一次按算的, 重按一次并不会
            # 把那一下往后推。记成最后一次的话, 处决的下限会被整体推晚。
            # 见 settle_before_break()
            if attempt == 1:
                self.forte_e_pressed_at = time.time()
            self.task.send_key_down(key)
            self.sleep(self.FORTE_E_HOLD, check_combat=False)
            self.task.send_key_up(key)
            self.record_resonance_use()
            self.sleep(self.FORTE_E_SETTLE)
            # 【判"放出去了"看提示没不没】, 不看冷却 ——
            # 回路E 吃的是回路, 不上 E 的冷却, 所以 has_cd 永远是 False:
            # 实机每一次都被判成"没放出去", 白按第二遍, 多花 1.1 秒。
            # 提示(e_forte 模板 / E图标变黄)只在回路满的时候显示, 按完就灭 ——
            # 灭了就是回路被吃掉了, 这才是"确实放出去了"的证据。
            y, tpl, cd = self.forte_e_signals()
            if not self.forte_e_ready() or cd:
                self.logger.info(f'xigelika: 强化E 放出去了 (第 {attempt} 次长按, '
                                 f'提示已灭: 模板={tpl} E图标黄={y:.2f} 冷却={cd})')
                return True
            self.logger.warning(f'xigelika: 长按 {self.FORTE_E_HOLD}s 之后提示还在 '
                                f'(模板={tpl} E图标黄={y:.2f}), 没放出去 '
                                f'(第 {attempt}/{self.FORTE_E_TRIES} 次)')
        return False

    def dodge(self):
        """闪避 = 右键。用来断强化重击的后摇, 时刻见 DODGE_AT / after_heavy_dodge()。"""
        self.task.click(key='right')

    def settle_before_break(self):
        """回路E 之后、处决之前的那一段普攻 —— 【等下砸伤害出来】。

        回路E 的伤害不是一次出完: 视频里按下E 之后 ~0.5 秒出第一段, 最大的那一下砸
        落在 +1.10 秒。F 处决会直接打断她当前的动作, 抢在砸之前按下去就等于把整条轴
        伤害最高的一击丢掉 (2026-09-20 实机反馈)。

        【视频量的 1.10 秒不能直接当成"按下键之后 1.10 秒"】: 视频里那个起点是提示消失
        的那一帧(= 游戏已经认下这次长按), 而我们是从 send_key_down 起算的, 中间还隔着
        游戏判长按的那段时间。实机按 1.55 秒放行照样被吞, 就是这段偏移。
        视频里作者是等到 +2.5 秒才按 F, 现在取 2.30。

        这段时间不是干等 —— 一直点普攻, 轴上这一格写的就是「A-处决」。
        """
        base = self.forte_e_pressed_at
        if not base:
            return
        left = base + self.FORTE_E_SLAM - time.time()
        if left <= 0:
            return
        self.logger.info(f'xigelika: 等回路E 下砸落地, 补 {left:.2f}s 普攻再看处决')
        end = time.time() + left
        while time.time() < end:
            self.check_combat()
            self.click()
            self.sleep(self.A_INTERVAL)
        self.logger.info(f'xigelika: 放行处决 (按下回路E 之后 {time.time() - base:.2f}s)')

    # ------------------------------------------------------------------ 处决

    def execute_break(self):
        """A 之后接 F 处决 —— 全队唯一一处主动处决。

        【为什么不能只调一次 task.f_break()】: 它的第一行是
        `if self.can_break or self.check_f_break():`, 提示晚出来哪怕 0.1 秒这次调用
        就整个跳过。而且宏跑起来之后 skip_combat_check=True, 战斗判定线程不再刷新
        can_break; check_f_break() 里屏幕中央那一路找图外面还裹着 1 秒频率限制,
        高频重试等于只查了顶部那一路。所以自己把中央那一路补上, 命中就置 can_break
        再进 f_break() 的短路分支。

        【f_break() 本身很贵】: 阻塞 0.5 ~ 5 秒, 期间还一直连打左键。放在这里是安全的
        —— 这一步之后就直接切人了, 手上没有任何长按, 也没有靠墙钟计时的窗口。
        """
        end = time.time() + self.BREAK_WINDOW
        while True:
            if self.task.can_break or self.break_prompt():
                self.task.can_break = True
                fired = self.f_break()
                # 按完记得清掉, 否则框架切人时会再按一次
                self.task.can_break = False
                self.logger.info(f'xigelika: break executed ({fired})')
                return True
            if time.time() >= end:
                break
            # 开了窗口才有这一段: 顺便盯黄, 亮了就补一个强化重击
            if self.heavy_ready():
                self.logger.info('xigelika: 找处决的时候黄亮了, 补一个强化重击')
                self.hold_heavy()
                continue
            self.click()
            self.sleep(0.1)
        self.logger.info('xigelika: 没有处决提示, 直接切人 (漏的交给守岸人)')
        return False

    def break_prompt(self):
        """屏幕中央那一路处决提示。check_f_break() 里的同一段判据, 但不吃频率限制。"""
        box = self.task.box_of_screen(0.2, 0.2, 0.75, 0.8, hcenter=True, vcenter=True)
        if self.task.find_one('f_break', box=box, target_height=720):
            return not self.task.is_pick_f()
        return False

    # ------------------------------------------------------------------ 离场

    def leave(self):
        """【硬 gate】协奏没满不许走, 然后按数字键直接切守岸人。

        她带不带变奏走人, 决定的是守岸人打两组还是三组 —— 守岸人那边是靠
        take_intro() 分流的, 判错了不是白打就是多打一组。

        【切人前必须先等队伍 HUD 回来】(2026-08-25 实机定位, 这是"守岸人刚变奏入场
        就被切走"的真凶):
        统计整份日志, 西格莉卡切守岸人 4 成功 / 7 失败, 而且分得干干净净 ——
        4 次成功【全部】来自开场丢 Q 那条分支(opener_toss, 前面没有任何演出),
        7 次失败【全部】来自主轴结尾的 leave(), 也就是紧跟在 F 处决 / 回路E 后面。
        失败的耗时又都正好是 SWITCH_TIMEOUT(2.0 秒), 说明那 2 秒整个泡在演出里:
        处决动画带全局时停、队伍 HUD 不渲染, switch_to_index 的 in_team() 一路读不到人,
        于是判失败 -> 掉进框架的 switch_next_char()。而那玩意是按优先级现场挑人的,
        挑到仇远的时候(12:10:43 / 12:33:43)轮换顺序就漂了 ——
        实际画面就是: 数字键其实已经把守岸人换上来了(带着变奏), 紧接着框架又切了一次。
        """
        con_ok = self.fill_con()
        for attempt in range(1, self.LEAVE_TRIES + 1):
            self.wait_team_hud()
            if self.direct_switch(assume_intro=con_ok):
                return
            self.logger.warning(f'xigelika: direct switch failed '
                                f'(第 {attempt}/{self.LEAVE_TRIES} 次), retrying')
        # 【掉回框架是最后手段】: 它会按优先级重挑人, 轮换顺序可能整个错位
        self.logger.warning('xigelika: falling back to framework switch, rotation may drift')
        self.switch_next_char()

    def fill_con(self):
        """等协奏满 —— 期间【照样盯着黄, 亮了就长按重击】, 不是干点普攻。

        用户 2026-08-25: 「不要过了个点就不重击了开始点按了」。
        原来这里是 require(is_con_full, action=self.click): 最多点 8 秒普攻, 全程
        不看黄 —— 强化重击好了也白瞎。现在拆成一秒一段, 每段都是"打普攻+盯黄",
        黄亮就按住, 打完回头看一眼协奏。
        """
        start = time.time()
        while True:
            if self.is_con_full():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if waited > 0.3:
                    self.logger.info(f'xigelika: 协奏满 after {waited:.1f}s')
                return True
            left = self.CON_GATE_TIMEOUT - self.time_elapsed_accounting_for_freeze(start)
            if left <= 0:
                self.logger.warning(f'xigelika: 协奏没满就到点了 '
                                    f'(con={self.get_current_con():.2f}), 走人')
                return False
            # watch_e=False: 这里是切人前等协奏, 强化E 早就放掉了, 只补重击
            found, held = self.attack_until_heavy(min(self.CON_FILL_SLICE, left),
                                                  watch_e=False)
            if found:
                self.logger.info('xigelika: 等协奏的时候黄又亮了, 补一个强化重击')
                self.hold_heavy(already_down=held)

    def wait_team_hud(self, timeout=None):
        """等右侧队伍头像的编号渲染回来 —— 演出期间它整块是不画的。

        不等就按数字键的话, switch_to_index 的 in_team() 全程读不到人, 白按满
        SWITCH_TIMEOUT 秒然后判失败(实机 7/7 就是这么失败的)。
        """
        timeout = self.TEAM_HUD_WAIT if timeout is None else timeout
        start = time.time()
        while time.time() - start < timeout:
            if self.task.in_team()[0]:
                waited = time.time() - start
                if waited > 0.2:
                    self.logger.info(f'xigelika: team hud back after {waited:.2f}s')
                return True
            self.task.next_frame()
            self.sleep(0.08, check_combat=False)
        self.logger.warning(f'xigelika: team hud still unreadable after {timeout:.1f}s')
        return False

    def switched_away(self):
        """我自己那个队伍编号又出现了 = 我已经不在场上了。

        这是给 switch_to_index 补的第二条确认路。框架的 in_team() 是靠
        "三个编号里【第一个】读不到的就是当前角色"反推的, 所以想认出"当前是3号",
        1号和2号两个模板都得命中; 认"当前是1号"只要1号读不到就行 ——
        判"切到3号"天生比判"切到1号"难。而这里只看我自己那一个模板。
        """
        return self.task.find_one(f'char_{self.index + 1}_text', threshold=0.8) is not None

    def switch_to_index(self, index):
        """覆盖公共段的版本 —— 多一条确认路(switched_away), 其余逐字一样。

        公共段那份留着不动, 因为别的角色切 1号/2号 从来没失败过;
        只有西格莉卡要切的 3号 判起来最难, 而且她还是唯一一个紧跟演出切人的。
        """
        start = time.time()
        while time.time() - start < self.SWITCH_TIMEOUT:
            in_team, current_index, _ = self.task.in_team()
            if (in_team and current_index == index) or self.switched_away():
                self.switch_confirmed_at = time.time()
                self.settle_with_attack(self.SWITCH_SETTLE)
                return True
            self.task.send_key(index + 1)
            self.click()
            self.sleep(self.SWITCH_KEY_INTERVAL, check_combat=False)
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
