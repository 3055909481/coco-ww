import time

from src.char.Qiuyuan import Qiuyuan as NativeQiuyuan
from src.char.ShoreKeeper import ShoreKeeper as NativeShoreKeeper


class ShoreKeeper(NativeShoreKeeper):
    """守岸人 —— 西仇守(西格莉卡 / 仇远 / 守岸人)「25s 轮椅轴」的第一棒。

    轮换环: 守岸人 -> 仇远 -> 西格莉卡 -> 守岸人。她这一棒的活就一件:
    **把协奏打满, 带着变奏交给仇远**。

    轴的来源: E:\\轴视频\\西仇守.mp4 (猫眼石攻略组的教学视频, 画面左侧带步骤条,
    底部带字幕)。下面所有时间都是从那条视频里量出来的【墙钟秒】——
    实测视频没有做变速, 视频时间就是真实时间, 步骤之间对不上的差值全是
    大招/处决的全局时停 (拿画面顶部的游戏倒计时对过: "守 3A-E" 视频 2.00 秒 /
    游戏 2.00 秒, 比值 1.00)。

        启动轴 (空手入场, 视频 2.90 - 12.20)
            3A + E                    2.90 - 4.90   (E 落在 4.07, CD 16.0 秒)
            3A + 回路重击 + 闪避        4.93 - 6.57
            3A + 回路重击 + Q + R      6.60 - 12.20  (Q 落在 8.9, R 落在 12.2)
            -> 变奏仇远                12.23 - 13.57

        循环轴 (变奏入场, 视频 53.70 - 60.77)
            3A + E                    53.70 - 55.57
            3A + 回路重击 + Q + R      55.60 - 60.77
            -> 变奏仇远                60.80 - 61.77

    两条轴只差一组「3A + 回路重击」: 空手入场时协奏差一截, 得多打一组, 中间那下闪避
    是用来断重击后摇的。所以代码里用 **有没有带变奏进场** 来决定打两组还是三组,
    而不是现场读协奏 —— 读协奏是每轮都在变的量, 但"打几组"这件事在这条轴上是死的。

    字幕原文 (供对照): t=4.0「然后切守岸人打满协奏」 t=12.2「然后变奏仇远」
    t=54.0「守岸人3A+E」 t=56.5「3A+重击+声骸+大招打满协奏」。
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

    # ================================================================ 西仇守: 守岸人这一棒

    # ---- 时间常量。全部来自视频墙钟, 单位秒。实机跟视频不会完全一致, 这些是【第一版
    #      的起点】, 要调就调这里, 别去改流程 ----
    INTRO_ANIM = 0.9       # 变奏入场动画。这段里的按键会被游戏整个吃掉, 只能等
    A_INTERVAL = 0.40      # 普攻段与段之间的间隔。视频: 开场 3A 花了 1.17 秒 = 0.39/段
    A_TO_E = 0.12          # 第三段普攻 -> E
    E_TO_A = 0.55          # E -> 下一组普攻
    # 【闪避回来了】(2026-08-25 用户指定): 「要等守岸人回路条满了, 重击然后马上闪避
    # 马上接平A, 这样既能取消重击的后摇也能取消闪避的后摇」。
    # 所以时机是死的: 回路条满 -> 重击 -> 立刻闪 -> 立刻 A, 中间不留任何固定延时。
    # (之前一刀切关掉是因为"时机不对会把人拉远", 现在时机由"回路满"这个判据钉住了)
    DODGE_AFTER_HEAVY: bool = True
    HEAVY_TO_ECHO = 0.25   # 最后一组重击 -> Q
    ECHO_TO_LIB = 0.10     # Q -> R (fire_echo 自己会花掉几百毫秒, 这里不用再等)
    # 【协奏够了就提前收工】(2026-08-25 用户要求): 协奏到 75% 就直接 Q + R 走人 ——
    # 一个大招给的协奏绝对不止 25%, 所以 75% + R 一定满。
    # 实机日志里她每一轮都老老实实打满三组、花掉 12 秒, 而 [协奏满] 那道闸一次都没
    # 触发过(说明早就满了) —— 后面那一两组纯粹是白站。
    CON_EXIT_PCT = 0.75
    # 提前收工【还要 R 是亮的】(2026-08-25 用户: "一旦协奏值大于等于75, 且R是亮的,
    # 马上开R切人")。只看协奏不看 R 的话, 收工了 R 也放不出来 —— 而那 25% 的协奏
    # 本来就指望 R 补上, 结果就是协奏永远差一截、下一棒拿不到变奏。
    # 实机 18:01 那一轮就是这个形态: 协奏读到 0.62, 打完三组去放 R, R 不亮,
    # 然后 [协奏满] 闸干等 10 秒还是没满
    LIB_WINDOW = 4.0         # 放 R 之前等大招图标亮起来的上限, 期间照样打普攻
    LIB_RETRY = 0.15
    # 协奏读数连读几次一致才认。能量环的读数会抖 —— 实机见过 0.49 下一帧读成 0.00
    CON_CONFIRM = 2
    _con_hits = 0            # con_enough() 的连读计数, 写成类属性好让静态检查扫得到
    LEAVE_TRIES = 2          # 切人失败重试几次, 之后才掉回框架
    TEAM_HUD_WAIT = 2.5      # 按数字键之前先等队伍 HUD 渲染回来的上限
    ECHO_TRIES = 4         # Q 最多连发几次, 见 fire_echo()
    ECHO_RETRY = 0.22      # 两次之间隔多久
    # 「协奏满了才准走」这道闸的上限。实机 26 次 `con not full before switch`,
    # 协奏差一点就切人 -> 仇远拿不到变奏 -> 整圈降级, 所以这道闸也放宽
    CON_GATE_TIMEOUT = 10.0
    # ================= 以下四组照搬露丽守/日月守那版守岸人(实机调过) =================
    # ---- 开头 3A + E 之后的主段: 【边打边判, 不再是固定的"第2组/第3组"】 ----
    # 旧版是 3A+E -> 3A+重击 -> (启动轴再来一组), 组数写死。毛病是两头都不对:
    # 回路提前满了要等这一组走完才重击; 被怪打断了组数照样走完就收工。
    # 现在一个循环跑到底, 退出条件三选一(协奏够 / 重击+闪避+两下A 打完 / 总上限)
    SEG_CAP = 9.0            # 整段上限, 防判据失灵时永远出不来
    SEG_MIN = 2.2            # 正常收尾前至少打这么久: 变奏入场有一段输入锁, 太早收等于没打
    E_RESEND = 0.60          # 段内补按 E 的间隔。落在冷却上是空操作, 但能救"E 被吞"那一次
    FORTE_CONFIRM = 2        # 回路指示连读几帧才认, 单帧会被特效骗
    A2_WINDOW = 0.80         # 重击+闪避之后再打这么久 A 才收尾 (A1+A2 派生累计 0.83)
    DODGE_TO_A = 0.25        # 闪避 -> 接普攻, 要跨过闪避的派生帧
    # 主段里多久读一次协奏。【别拿掉这个节流】: con_enough() 一次要读协奏环 + R 图标
    # 两处屏, 而主段的循环是 0.02 秒一圈 —— 每圈都读的话读屏会把普攻的节奏整个挤掉
    CON_POLL_EVERY = 0.25
    # ---- 放 R 之前的协奏保底 ----
    # R 自己给 20 协奏, 所以放 R 前差一点是正常的; 但差太多就说明前面那发重击被打断了,
    # 补一发再放 R —— 不然 R 放完协奏还是不满, 下一棒拿不到变奏
    LIB_MIN_CON = 0.70
    LIB_RESCUE_CAP = 3.0     # 补那一发时等回路满的上限
    # 大招演出隐藏队伍 HUD 约 179F(3 秒)。leave() 第一件事是读协奏, 演出里读到的不作数,
    # 所以放完 R 先等 HUD 回来。HUD 本来就在的时候这一步是 0 成本
    LIB_HUD_WAIT = 3.5
    # ---- 协奏没满时的补救: 【按给协奏多少排优先级】, 不是干点普攻 ----
    # E 给 20 / 强化重击给 6 / 普攻 1~4。旧版这道闸是 require(action=self.click),
    # 也就是纯点普攻等 10 秒 —— 实机 26 次 `con not full before switch` 全卡在这里。
    # 这跟西格莉卡/仇远的 fill_con 是同一个形态: 等待段也要继续输出 + 继续检测
    CON_RESCUE_SLICE = 0.80  # 实在没技能可用时, 每次先点这么久普攻再回头看协奏
    E_DERIVE = 0.67          # 补协奏按完 E 等多久再看下一步 (E-地面 派生 40F)
    SWITCH_ANCHOR_MAX = 1.5

    @property
    def NEXT_SLOT(self):
        """打完切仇远。

        按【类】找人而不是写死队伍位置 —— 玩家把队伍顺序摆成别的样子也不用改代码。
        公共段的 direct_switch() 只认 1-based 的队伍位置, 所以这里换算一下。
        """
        for char in self.task.chars:
            if isinstance(char, NativeQiuyuan):
                return char.index + 1
        return None

    def reset_state(self):
        super().reset_state()
        # 守岸人登记的是 HEALER, BaseChar.__init__ 里 check_f_on_switch = not is_healer
        # 已经是 False 了。这里再写一遍是为了防上游哪天改了默认值 ——
        # 这条轴的处决只有西格莉卡收尾那一下, 别的地方按 F 都是白扔。
        self.check_f_on_switch = False

    def on_combat_end(self, chars):
        self.reset_fixed_rotation()
        super().on_combat_end(chars)

    # ------------------------------------------------------------------ 轴

    def do_perform(self):
        intro = self.take_intro()
        self.check_f_on_switch = False
        anchor = self.switch_in_anchor()
        if intro:
            # 变奏入场动画打不断, 硬等。用【切人确认那一刻】当基准, 不是这个函数被调到
            # 那一刻 —— 中间隔着框架的交接记账和任务循环, 那段延迟是变长的
            self.sleep(max(0.0, anchor + self.INTRO_ANIM - time.time()), check_combat=False)
        if self.flying():
            self.wait_down()
        self.logger.info(f'shorekeeper axis start (intro={intro})')

        # ---- 开头: 3A + E ----
        # 这一段保持不变(视频轴面板写的就是「守 | 3A-E」), 而且 E 要早放:
        # 它一个人就给 20 协奏, 放晚了后面那道协奏闸才难过。
        # 【每一下普攻之前都看一眼协奏】: 只在固定检查点读的话, 实机那两个点落在
        # 开打后 1.6 秒和 3.2 秒, 读数才 0.27 / 0.44, 之后涨过 75% 就再也没人看了
        if self.attack_watching_con(3, self.A_TO_E):
            return self.finish()
        self.cast_resonance()
        self.sleep(self.E_TO_A)
        if self.con_enough('E 之后'):
            return self.finish()

        # ---- 主段: 边打边判 ----
        self.combat_segment()
        return self.finish()

    def combat_segment(self):
        """3A+E 之后的主段 —— 【一个循环跑到底, 不分组】(照搬露丽守/日月守那版)。

        旧版是写死的"第 2 组 3A+重击"、启动轴再加"第 3 组"。两头都不对:
        回路提前满了要等这一组走完才轮到重击; 被怪打断了组数照样走完就收工。
        而且启动轴/循环轴要靠 intro 分支去加减组数, 实际该打多久取决于协奏涨多快。

        一直 A, 期间:
          - 【回路满就一定打掉】: 重击 -> 闪避 -> 接着打。这一条【排在所有退出条件前面】;
          - 每 E_RESEND 补按一次 E —— 落在冷却上是空操作, 但能救"开头那一发被吞";
          - 协奏够了(con_enough: 75% 且 R 亮着) -> 收手去 Q+R;
          - 打过重击、闪避之后又打满 A2_WINDOW、整段也够 SEG_MIN -> 正常收尾。

        【为什么回路要排在退出条件前面】(2026-09-21 用户: "只要回路满了就要重击
        消耗回路再闪避取消重击后摇"): 协奏够/AA打完的判断排在前面的话, 回路刚好在
        那一帧满就被整个跳过 —— 那一格回路白攒了, 而且重击本身还给 6 协奏。
        退出之前还会【再查一次回路】, 免得在最后一帧攒满却没人打。
        """
        start = time.time()
        next_a = next_e = next_con = 0.0
        lit = heavies = 0
        dodge_at = None
        why = 'CAP'
        while self.time_elapsed_accounting_for_freeze(start) < self.SEG_CAP:
            now = time.time()
            self.task.next_frame()
            try:
                forte = bool(self.is_mouse_forte_full())
            except Exception:
                forte = False
            lit = lit + 1 if forte else 0
            if lit >= self.FORTE_CONFIRM:
                lit = 0
                heavies += 1
                self.heavy_and_dodge()
                dodge_at = time.time()
                continue

            if now >= next_con:
                next_con = now + self.CON_POLL_EVERY
                if self.con_enough('主段'):
                    why = '协奏够'
                    break
            if (dodge_at is not None and now - dodge_at >= self.A2_WINDOW
                    and self.time_elapsed_accounting_for_freeze(start) >= self.SEG_MIN):
                why = '重击+闪避+AA'
                break

            if now >= next_e:
                # 【补按 E】: 开头那一发被演出吞掉的话, 这一轮就白少 20 协奏
                self.send_resonance_key()
                next_e = now + self.E_RESEND
            elif now >= next_a:
                self.try_break()
                self.click()
                next_a = now + self.A_INTERVAL
            self.sleep(0.02)
        # 退出之前再查一次: 最后那一帧刚攒满的话, 这一格回路不能带着走
        if self.wait_forte_once():
            heavies += 1
            self.heavy_and_dodge()
        self.logger.info(f'shorekeeper 主段结束({why}, 重击 x{heavies}, '
                         f'{self.time_elapsed_accounting_for_freeze(start):.2f}s)')
        return why

    def heavy_and_dodge(self):
        """回路重击 -> 闪避断后摇 -> 立刻接普攻。

        三者中间【不留任何固定延时】: 闪避断重击的后摇, 紧接着那一下普攻又断闪避的。
        留了延时就两个后摇都断不掉。
        """
        ok = bool(self.heavy_click_forte(self.is_mouse_forte_full))
        if ok and self.DODGE_AFTER_HEAVY:
            self.dodge()
            self.click()
        self.sleep(self.DODGE_TO_A)
        return ok

    def wait_forte_once(self):
        """看一眼回路满没满(连读 FORTE_CONFIRM 帧)。只读屏, 不动手。"""
        hits = 0
        for _ in range(self.FORTE_CONFIRM):
            self.task.next_frame()
            try:
                if not self.is_mouse_forte_full():
                    return False
            except Exception:
                return False
            hits += 1
            self.sleep(0.02, check_combat=False)
        return hits >= self.FORTE_CONFIRM


    def attack_watching_con(self, count, last_interval=None):
        """打 count 段普攻, 【每一下之前都看一眼协奏】—— 够 75% 就立刻返回 True 收工。

        用户 2026-08-25: 「为什么协奏值已经超过75了还不放R切人」。
        答案是原来只在两个固定点各读一次协奏, 而那两个点太早 (开打 1.6 秒和 3.2 秒,
        读数 0.27 / 0.44), 之后协奏涨过 75% 就再也没人看了。
        跟西格莉卡那边一个道理: 【要持续检测, 不能只在检查点上看】。
        """
        for i in range(count):
            if self.con_enough():
                return True
            self.try_break()
            self.click()
            gap = last_interval if (i == count - 1 and last_interval is not None)                 else self.A_INTERVAL
            self.sleep(gap)
        return self.con_enough()

    def try_break(self):
        """看一眼有没有处决可打, 有就打掉。

        用户 2026-08-25: 「没处决就切守岸人继续, 让守岸人在A的时候处决就行」——
        西格莉卡那边只"看一眼"就走人, 漏掉的处决由这里顺手补上。
        她这一段是纯点普攻, 手上没有长按, 所以 f_break() 那个"阻塞 0.5~5 秒 + 期间
        连打左键"的行为在这里是安全的 (换成长按流程里就绝对不能插)。

        【不能只调 task.f_break()】: 它第一行是 `if can_break or check_f_break()`,
        提示晚亮 0.1 秒这次调用就整个跳过; 而且宏跑起来后 skip_combat_check=True,
        战斗判定线程不再刷新 can_break, check_f_break() 里屏幕中央那一路找图外面
        还裹着 1 秒频率限制。所以自己补那一路。
        """
        if not (self.task.can_break or self.break_prompt()):
            return False
        self.task.can_break = True
        fired = self.f_break()
        self.task.can_break = False   # 不清掉的话框架切人时会再按一次
        self.logger.info(f'shorekeeper: break executed ({fired})')
        return True

    def break_prompt(self):
        """屏幕中央那一路处决提示 —— check_f_break() 里的同一段判据, 但不吃频率限制。"""
        box = self.task.box_of_screen(0.2, 0.2, 0.75, 0.8, hcenter=True, vcenter=True)
        if self.task.find_one('f_break', box=box, target_height=720):
            return not self.task.is_pick_f()
        return False

    def con_enough(self, where=None):
        """协奏够不够直接收工 (75%)。

        够了就不用再打下一组 —— 差的那点由大招补上 (一个 R 给的协奏绝对不止 25%)。

        【连读 CON_CONFIRM 次一致才认】: 能量环的读数会抖, 实机见过 0.49 下一帧
        读成 0.00。抖到低值只是晚一点收工, 抖到高值就会提前走人, 所以要去抖。
        """
        con = self.get_current_con()
        if con < self.CON_EXIT_PCT:
            self._con_hits = 0
            if where:
                self.logger.info(f'shorekeeper: con {con:.2f} ({where}), one more block')
            return False
        # 【协奏够了还要 R 亮着】—— 收工的动作是 Q+R, R 放不出来就别收
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
        """收工: Q -> [协奏保底] -> R -> 等演出 -> 切仇远。"""
        self.sleep(self.HEAVY_TO_ECHO)
        self.fire_echo()
        self.sleep(self.ECHO_TO_LIB)
        self.ready_for_lib()
        if self.fire_liberation():
            # 演出期间队伍 HUD 整块不渲染, 而 leave() 第一件事是读协奏 ——
            # 演出里读到的数不作数, 等 HUD 回来再往下走
            self.wait_team_hud(self.LIB_HUD_WAIT)
        self.leave()

    def ready_for_lib(self):
        """放 R 之前的协奏保底 —— 差太多就先补一发回路重击。

        R 自己给 20 协奏, 所以放 R 前差一点是正常的; 但差到 LIB_MIN_CON 以下就说明
        前面那一发重击被打断了 —— 直接放 R 的话放完还是不满, 下一棒拿不到变奏。
        只补一次: 补不上多半是打不到怪, 赖在这里更亏。
        """
        con = self.get_current_con()
        if con >= self.LIB_MIN_CON:
            return True
        self.logger.info(f'shorekeeper: 放 R 前协奏只有 {con:.2f} (< {self.LIB_MIN_CON}), 补一发回路重击')
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.LIB_RESCUE_CAP:
            if self.is_mouse_forte_full():
                self.heavy_and_dodge()
                break
            self.try_break()
            self.click()
            self.sleep(self.A_INTERVAL)
        con2 = self.get_current_con()
        if con2 < self.LIB_MIN_CON:
            self.logger.warning(f'shorekeeper: 补完协奏还是只有 {con2:.2f}, 照样放 R')
        return con2 >= self.LIB_MIN_CON

    def fire_liberation(self):
        """R 大招 —— 【等图标亮了再按, 不是问一次就走】。

        (2026-08-25 实机: 5 次 `liberation not ready`, 其中两次紧跟着
         `[守岸人 协奏满]` 干等 10 秒还没满 —— 因为那 25% 本来就指望 R 补上。)
        click_liberation() 只在"图标当下就亮着"时才真发键; 读到不亮就在
        wait_if_cd_ready(0.3秒) 里补两下然后 return False, 整个过程不到半秒。
        而她刚打完重击/正在收招, 能量环那几帧最不可信。
        所以改成在窗口里轮询 liberation_available(), 亮了才按, 等的过程中照样打普攻
        —— 普攻自己也在攒能量。

        【顺手清 task.in_liberation】: 被上一个角色的大招留成 True 的话,
        click_liberation() 开头 `if not self.task.in_liberation:` 会把发键整段跳过。
        """
        self.task.in_liberation = False
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.LIB_WINDOW:
            self.task.next_frame()
            if self.liberation_available():
                waited = self.time_elapsed_accounting_for_freeze(start)
                # click_f=False: 大招演出期间框架默认每 0.1 秒发一次 F, 会把全队攒着的
                # 处决提前吃光 —— 这条轴的处决只留给西格莉卡收尾那一下
                if self.click_liberation(wait_if_cd_ready=0.3, click_f=False):
                    self.logger.info(f'shorekeeper: liberation fired after {waited:.2f}s')
                    return True
            self.check_combat()
            self.click()
            self.sleep(self.LIB_RETRY)
        self.logger.warning(f'shorekeeper: liberation icon never lit within '
                            f'{self.LIB_WINDOW}s, leaving without it')
        return False

    # ------------------------------------------------------------------ 动作

    def cast_resonance(self):
        """E。走 click_resonance 而不是裸发键 —— 它会等动画结束并记账。

        【返回的是三元组】(clicked, duration, animated), 不是 bool。
        """
        return self.click_resonance(send_click=False, time_out=1)[0]

    def fire_echo(self):
        """Q 声骸 —— 【连发, 不是只发一次】。

        (2026-08-25 实机: "守岸人有时候不会放Q"。)
        演出期间的按键是被游戏丢弃的、不排队, 只发一次很容易整段被吃掉。
        Q 又是脱手技能, 多按几下不会打断角色当前的动作, 所以连发是安全的。
        读到进冷却就提前收手; 读不出来就把 ECHO_TRIES 次发满 —— 按在冷却上是空操作。

        【不用 click_echo】: 它第一件事是读屏 echo_available(), 读到 False 就掉进
        一秒起步的慢路径, 而刚打完重击那几帧读数最不可信。
        """
        was_available = self.echo_available()
        for i in range(self.ECHO_TRIES):
            self.send_echo_key()
            self.record_echo_use()
            self.sleep(self.ECHO_RETRY, check_combat=False)
            if not was_available:
                # 进来就读成冷却中, "现在也是冷却"证明不了任何事, 发一次尽人事就走
                return False
            if not self.echo_available():
                if i:
                    self.logger.info(f'shorekeeper: echo landed on try {i + 1}')
                return True
        return False

    def dodge(self):
        """闪避 = 右键。视频里是用来断回路重击后摇的。"""
        self.task.click(key='right')

    # ------------------------------------------------------------------ 离场

    def ensure_con(self, cap=None):
        """协奏没满就别交棒 —— 【按"能给多少协奏"排优先级补】: E(20) > 回路重击(6) > 普攻(1~4)。

        旧版这道闸是 require(is_con_full, action=self.click), 也就是【纯点普攻】等满 ——
        实机日志里 26 次 `con not full before switch`, 读数 0.74~0.97, 全是差一点。
        普攻一下才 1~4 协奏, 差那一点要点十几下; 而 E 一发就是 20, 回路重击 6。
        西格莉卡和仇远的 fill_con 早就是这个形态了(等待段也要继续输出 + 继续检测),
        守岸人这边一直没跟上。

        【要给上限】: 协奏满不满取决于打没打到怪, 怪被别人清了/位移了就永远等不到。
        到点就认赔交棒, 至少把大招和延奏往下传。
        """
        cap = self.CON_GATE_TIMEOUT if cap is None else cap
        start = time.time()
        rounds = 0
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            rounds += 1
            if self.is_con_full():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if waited > 0.3:
                    self.logger.info(f'shorekeeper: 协奏满 after {waited:.1f}s ({rounds} 轮)')
                return True
            # 1) E: 一发 20 协奏, 有就先放
            if not self.has_cd('resonance'):
                self.cast_resonance()
                self.sleep(self.E_DERIVE)
                continue
            # 2) 回路重击: 6 协奏, 而且是她攒得最快的手段
            try:
                forte = bool(self.is_mouse_forte_full())
            except Exception:
                forte = False
            if forte:
                self.heavy_and_dodge()
                continue
            # 3) 都没有就点普攻, 打一小段再回头看协奏
            end = time.time() + self.CON_RESCUE_SLICE
            while time.time() < end:
                if self.is_con_full():
                    break
                self.try_break()
                self.click()
                self.sleep(self.A_INTERVAL)
        self.logger.warning(f'shorekeeper: 补了 {cap:.1f}s 协奏还是没满 (con={self.get_current_con():.2f}), 认赔交棒')
        return False

    def leave(self):
        """【硬 gate】协奏没满不许走, 然后按数字键直接切仇远。

        为什么要 gate: 这套轴是靠变奏/延奏一棒接一棒串起来的, 她没带满协奏走人,
        仇远就拿不到变奏, 仇远的 E-R 又是喂给西格莉卡的 —— 一步没做到整圈报废。

        为什么这道闸【要给上限】: 协奏满不满取决于打没打到怪, 怪被别人清了/位移了
        就永远等不到。给了上限至少还能把大招和延奏往下传, 下一圈自己会补回来。
        """
        con_ok = self.ensure_con()
        if con_ok:
            # 原版 ShoreKeeper.switch_next_char() 里的记账 —— 直接按数字键切人绕开了它。
            # 队友的 shorekeeper_auto_dodge() 靠这两个字段决定还能不能替人闪避
            self.outrotime = time.time()
            self.dodge_count = 5
        for attempt in range(1, self.LEAVE_TRIES + 1):
            # 大招/重击的演出期间队伍 HUD 整块不渲染, in_team() 认不出人, 直接按数字键
            # 会白按满 SWITCH_TIMEOUT 然后判失败 (实机 `direct switch to slot 2 failed`)
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
        """本轮的计时基准: 头像认出「切人完成」的那一刻 (handoff_to 写进
        last_switch_in_time 的那个时间戳), 而不是 do_perform 被调用那一刻。
        """
        now = time.time()
        anchored = self.last_switch_in_time
        gap = now - anchored if anchored > 0 else -1
        if 0 <= gap <= self.SWITCH_ANCHOR_MAX:
            return anchored
        return now
