import time

from src.char.BaseChar import BaseChar, SwitchPriority

# char_name / check_outro() 可能是这两个值中的任何一个: CharFactory.py:120 把
# char_linnai 和 char_linnai2 映射到同一个类。只认前者的话, 换个立绘就永远进不了
# perform_25s_cycle 的爆发分支, 每轮只打 2 秒普攻走人, 而且日志上一点声都没有
LINNAI_NAMES = {'char_linnai', 'char_linnai2'}


class Aemeath(BaseChar):
    LIBERATION_COOLDOWN = 25
    LIBERATION_FORCE_DURATION = 30
    LIB2_PREPARE_WINDOW = 8
    INTRO_LIBERATION_DELAY = 14
    # 爆发段每个等待的预算。原来这三处都是 while not <条件>: 没有上限也不做战斗判定,
    # 能量/E 永远不到就永远出不来
    CYCLE_READY_TIME_OUT = 6.0
    CYCLE_LIB1_TIME_OUT = 6.0
    CYCLE_E2_TIME_OUT = 8.0
    CYCLE_LIB2_TIME_OUT = 8.0
    R2_KEY_INTERVAL = 0.05      # 重击之后连按 R 的间隔
    # 强化E 的演出长度, 按住攻击键要盖过它。实测 click_resonance 走完整段用了 3.08 秒
    # (含发键)。给 2.80: 宁可略短 —— 短了顶多在演出末尾少按一会儿, 长了就是
    # 重击打完还按着, 会多打出普攻
    # 2.80 不够 —— 按住 2.80 秒出的是平A, 说明演出远短于此, 重击另有触发条件。
    # 加到 4.00 并在按住期间采样高亮, 看它到底亮不亮
    # 4.00 -> 5.50(2026-08-14 用户: "处决完了放RE然后会普通重击")。
    # 【上限卡得太紧, 余量只有 0.3 秒】: 实测强化重击要按住约 3.0 秒才亮,
    # 亮完再打约 0.8 秒 = 成功的轮次要 3.7 秒, 而上限是 4.00 —— 起手慢一点点就
    # 撞上限放弃, 鼠标一松, 前面按住的那几秒就变成普通攻击/普通重击。
    # 【抬高上限几乎不花时间】: 打出来就立刻退出, 只有失败的轮次才会多等
    E2_ANIM_HOLD = 5.50
    # 按住期间采样重击高亮的间隔。【它直接就是"重击出手 -> 下一个动作"的延迟】——
    # 高亮灭掉之后最坏要等一个间隔才发现。用户 2026-08-13: "重击接处决稍微晚了一点"、
    # "最后的重击接R2也稍微晚了一点", 两处都是这个 0.25 秒。
    # 0.25 -> 0.12。代价是长按期间多几次读屏(has_long_action 是 HUD 上的小图),
    # 换来的是两处衔接各快约 0.13 秒
    SPIN_CLICK_INTERVAL = 0.12   # spin_until 里的点击频率(条件是每帧查的)
    E2_POLL = 0.12
    # 采样变密之后【必须配 min_hold】: 演出期间 HUD 是乱的, 高亮可能先读到 True
    # 下一帧又 False —— 采样越密越容易抓到这种抖动, 抓到就误判成"重击出手了",
    # 实际只按住零点几秒就松手, 打出来的是一下平A(2026-08-12 踩过)。
    # 强化E 的重击实测要按住约 3.8 秒才出手, 所以 2.00 之前的"亮过又灭"一律不认
    E2_HEAVY_MIN_HOLD = 2.00
    E2_SAMPLE_MAX = 20
    # 最后一发强化E 之后, 等重击高亮亮起的上限。等的期间一下都不点,
    # 所以 E 和重击之间不会有多余的 A

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.should_wait = False
        self.intro_time = -1
        self.last_liber = -1
        self.last_enhance_e = -1
        self.intro_liberation_time = -1
        self.pending_lib2 = False
        self.opener_burst_pending = False

    
    @property
    def is_in_25s_cycle(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Linnai', 'Mornye'}:
                team_members += 1
        return team_members == 2

    @property
    def is_in_denia_cycle(self) -> bool:
        """爱达千队(爱弥斯/达尼娅/千咲)。整条轴在 Denia.py 里, 这里只出收尾爆发。"""
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Denia', 'Chisa'}:
                team_members += 1
        return team_members == 2


    def lib2_cooldown_anchor(self):
        if self.last_liber >= 0:
            return self.last_liber
        combat_start = getattr(self.task, 'combat_start', -1)
        return combat_start if combat_start > 0 else -1

    def lib2_cooldown_left(self):
        anchor = self.lib2_cooldown_anchor()
        if anchor < 0:
            return 0
        elapsed = self.time_elapsed_accounting_for_freeze(anchor)
        return max(0, self.LIBERATION_COOLDOWN - elapsed)

    def liberation_cooldown_left(self):
        if self.last_liber < 0:
            return 0
        elapsed = self.time_elapsed_accounting_for_freeze(self.last_liber)
        return max(0, self.LIBERATION_COOLDOWN - elapsed)

    def lib1_unlock_anchor(self):
        if self.last_liber >= 0:
            return self.last_liber
        return getattr(self.task, 'combat_start', -1)

    def record_intro_liberation(self):
        self.intro_liberation_time = time.time()

    def intro_lib1_ready(self):
        return self.intro_liberation_time >= 0 and (
                self.time_elapsed_accounting_for_freeze(self.intro_liberation_time)
                <= self.INTRO_LIBERATION_DELAY)

    def lib1_unlocked(self):
        if self.intro_lib1_ready():
            return True
        anchor = self.lib1_unlock_anchor()
        return anchor >= 0 and (
                self.time_elapsed_accounting_for_freeze(anchor) >= self.LIBERATION_FORCE_DURATION)

    def can_cast_lib1(self):
        return self.liberation_cooldown_left() <= 0 and self.lib1_unlocked()

    def can_cast_liberation(self):
        return self.can_cast_lib1()

    def lib2_available(self):
        return bool(self.task.find_one('aemeath_lib2', threshold=0.7))

    def preparing_lib2(self):
        return self.lib2_cooldown_anchor() >= 0 and (
                self.lib2_cooldown_left() < self.LIB2_PREPARE_WINDOW)

    def should_wait_for_lib2(self):
        return self.pending_lib2 or self.preparing_lib2()

    def should_wait_for_enhance_e(self):
        return self.time_elapsed_accounting_for_freeze(self.last_enhance_e) > 12

    def record_enhance_e(self):
        self.last_enhance_e = time.time()

    def record_heavy_liberation(self):
        self.pending_lib2 = True

    def record_liberation(self, is_lib2):
        if is_lib2:
            self.pending_lib2 = False
            self.last_liber = time.time()
            self.last_enhance_e = self.last_liber
        else:
            self.intro_liberation_time = -1

    # ---- 收尾爆发 (启动轴和循环轴共用一条尾巴) ----
    # 2026-08-13 的图上, 两段收尾只有【前缀】不同, 后面是同一串:
    #   启动轴: A3A4 -> R1 -> 重击 -> F处决 ------> ┐
    #   循环轴: 平A到强化E亮 -> F处决 -> R1 --------> ┤ 强化E -> A到A4 -> 强化E -> 重击 -> R2
    # 所以尾巴抽成 burst_tail(), 前缀各写各的。上一版两段的顺序真的不一样,
    # 那时不能合并; 现在能合并的只是尾巴, 前缀仍然分开
    # 打到 A4 的连点窗口。【有没有吃到变奏入场, 要的长度完全不同】——
    # 2026-08-13 实机: 按"没变奏"的 0.90 定, 结果有变奏那轮整个窗口都落在入场演出里,
    # 点击全被丢光, 画面上就是"没有A直接开R1"。
    #
    # 【必须用连点窗口, 不能离散点击】: 演出和 A2 的后摇会把贴着发的点击吞掉,
    # 而动画期间的点击是被丢掉的、不排队 —— 固定 stride 猜错一次这几段就没了。
    #
    # 有变奏(正常路径): 入场演出 0.9 秒(框架 BaseChar.py:96 的 intro_motion_freeze_duration
    #   就是这个数)期间点击全丢; 演出完【她自己起手 A2】(用户 2026-08-13),
    #   我们只要补 A3、A4。一段约 0.38, 所以 A3 起手约 1.28, A4 约 1.66, A5 约 2.04。
    #   取 1.90 —— 落在 A4 起手之后、A5 之前
    AEM_A_WINDOW_INTRO = 1.90
    # 没吃到变奏(协奏差一点): 没有入场演出, 也没有自动的 A2, 得从 A1 打起打满四段。
    # 1.45 是 v3 标定过的四段窗口(1.55 偏慢 / 1.30~1.40 偏早, 可用区间很窄)
    AEM_A_WINDOW_PLAIN = 1.45
    # A 窗口结束 -> 发 R1。【R1 要落在 A4 的后撤动作【途中】】(用户 2026-08-13),
    # 不是等后撤演完。给一点余量让 A4 确实出手, 给大了就错过后撤窗口。
    # 症状: 给小了 R1 打断的是 A3(A4 没出来), 给大了后撤已经结束
    M_A4_TO_R1 = 0.10
    # 中段"A到A4"的连点窗口。实测(2026-08-12): 1.55 四段都出但偏慢,
    # 1.30/1.35/1.40 偏早, 取 1.45。可用区间只有 1.45~1.55, 很窄
    BURST_A4_WINDOW = 1.45
    BURST_A_INTERVAL = 0.12
    # R1 演出【开始】之后多久把攻击键按下去。落在演出【期间】, 不是演出之后 ——
    # 演出之后再按就是一次新的按下沿, 会先出一下平A
    M_R1_TO_HEAVY = 0.10
    # R1 之后那发重击: 从按下算起的上限。【要盖住整个 R1 演出】——
    # 实测 R1@20.57 -> R1heavy(hit)@26.52, 演出接近 5 秒, 所以给 7.0。
    # 【别学之前那版设 lit_time_out 提前放弃】: 演出期间 HUD 不在, 高亮当然是灭的,
    # 提前放弃等于每次都在演出中途松手(那一版日志就是 R1heavy(miss) 全程 0)
    R1_HEAVY_HOLD = 7.00
    # 这么久之内不接受"亮过又灭"。演出结束 HUD 回来那几帧高亮会闪, 不设这个门
    # 就会按住零点几秒就松手 —— 那只是一下平A。取值要盖过整个演出
    R1_HEAVY_MIN_HOLD = 2.00
    # 【处决之后不留缓冲, 强化E 直接开始预输入】(用户 2026-08-13)。
    # 这两个原来是 0.15 / 0.10 的干等, 用来"让上一个动作演完再发 E" ——
    # 但那是单发时代的补丁: 连发本身就免疫被吞, 干等只剩纯延迟这一个效果。
    # 注意 f_break() 自己会阻塞至少 0.5 秒(它在那期间 F 和左键交替发), 所以
    # "处决之后"的真实起点已经比按下 F 晚了半秒, 这里再加就更晚
    M_AFTER_F = 0.0
    LOOP_R1_TO_E = 0.0
    # 强化E 重发到进 CD 的上限。要盖住处决的动画/时停(旧代码里按 1.6 秒估的)
    E1_RESEND_TIME_OUT = 1.80
    LOOP_E_RELIGHT_TIME_OUT = 8.0

    def burst_macro(self):
        """拿到持宏的队友实例。宏原语(macro_wait/macro_spam/时间轴)都在他那儿 ——
        自定义角色文件互相 import 不到, 但同一个 task 下拿得到彼此的实例。

        【按能力找, 不按名字找】(2026-08-16): 爱弥斯现在同时在两条队伍轴里出场,
        持宏的角色不一样 —— 爱琳莫是莫宁, 爱达千是达尼娅。写死名字的话换一条队
        就静默返回 None, 表现是"收尾爆发整段消失", 日志上只有一句 warning。
        三个原语一起查, 免得撞上别的自定义文件里同名的方法。
        """
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if (hasattr(char, 'macro_wait') and hasattr(char, 'macro_spam')
                    and hasattr(char, 'quick_switch')):
                return char
        return None

    def perform_loop_burst(self):
        """循环轴的收尾: 平A到强化E亮 -> F处决 -> R1 -> [共用尾巴]。

        【图上第六行写的是 F + RE + A4 + EZR, 但 F 前面还有一段平A】——
        图省略了它, 我照字面把 F 放在最前面, 实机就是"变奏入场自动接个A2就处决了"
        (用户 2026-08-13)。F 要卡在【强化E 亮起的那一瞬间】。
        """
        macro = self.burst_macro()
        if macro is None:
            self.logger.warning('loop burst: no macro engine, falling back to perform_everything')
            self.perform_everything()
            return False
        mark0 = len(macro.macro_marks)  # 只打这一段的时间轴, 别把整个循环轴重播一遍
        start = time.time()
        # 【先平A 到强化E亮起, 在亮起的那一瞬间才接 F 处决】(用户 2026-08-13:
        # "变奏小爱入场时需要平A至强化E亮起, 在亮起的一瞬间接F处决")。
        # 【我按图上的字面顺序写错了】: 第六行写的是 F + RE + A4 + EZR, 我就把 F
        # 放在最前面, 实机变成"变奏入场自动接个A2就处决了" —— 图上省掉了这段平A。
        # spin_until 正好是"边平A 边等条件成立, 成立的瞬间返回", 不用自己写循环
        self.spin_until(self.enhance_e_available, self.LOOP_E_RELIGHT_TIME_OUT, 'E lit before F')
        # 【用框架的 f_break(), 不要自制的严格版】: 自制版只认屏幕中央那个提示,
        # 而实机那个提示一直读不到(每轮都是 skip F (prompt=False)), 换上去之后
        # F 就再没按出来过。f_break() 读的是粘滞的 can_break, 理论上会空按,
        # 但实机是能打出处决的
        if self.LOOP_R_BEFORE_F:
            # 【R/E 的第一发挪到 F 之前】(用户 2026-08-14: F 接 R 还慢 20 多 ms)。
            # f_break() 内部会阻塞约 0.5 秒, 原来要等它跑完才开始连发 R ——
            # 那 20ms 就是它的尾巴。
            # 【提前发是安全的】: 处决动画期间的按键会被丢弃、不排队, 早发的那一下
            # 不会提前触发大招, 只是把"第一个合法帧"往前挪
            self.send_liberation_key()
            self.send_resonance_key()
            macro.macro_mark('preRE')
        self.f_break()
        macro.macro_mark('F')
        macro.macro_wait(self.M_AFTER_F)
        # 【F 之后同时连发 R 和 E, 发到 R1 演出开始】(用户 2026-08-14:
        # "按了F就开始长按R和E")。原来是 fire_r1 只发 R、E 要等到 burst_tail
        # 里才发 —— 两段串行。处决的收招会吞掉单发的键, 连发才吃得到第一个合法帧
        lib1 = self.spam_r_e_until_lib(macro, tag='R1')
        # 【连发已经把强化 E 放出去了, burst_tail 不能再放一次】(2026-08-14):
        # 实机 R1_R(x15)_E(x20)@31.95 -> E1_timeout(x12)@33.87, 每轮白亏 1.9 秒
        lib2 = self.burst_tail(macro, skip_e1=self.LOOP_E_SPAM_WINDOW > 0)
        self.logger.info(f'loop burst: R1={lib1} R2={lib2} '
                         f'{self.time_elapsed_accounting_for_freeze(start):.1f}s')
        self.logger.info(f'loop burst timeline: {macro.macro_timeline(mark0)}')
        return lib2

    def perform_opener_burst(self):
        """启动轴的收尾(图上第三行): A3A4 -> R1 -> 重击 -> F处决 -> [共用尾巴]。

        跟循环轴那段的差别就是这个前缀: 这里是变奏入场刚落地, 要先把连段补到 A4,
        R1 之后还多一发重击接处决。
        """
        macro = self.burst_macro()
        if macro is None:
            self.logger.warning('opener burst: no macro engine, falling back to perform_everything')
            self.perform_everything()
            return False
        mark0 = len(macro.macro_marks)
        start = time.time()
        # 有变奏: 入场那一下算 A1、演出完她自己起手 A2, 我们只补 A3 A4;
        # 没变奏: 没有演出也没有自动的 A2, 得从 A1 打满四段。窗口按这个分
        intro = bool(self.has_intro)
        window = self.AEM_A_WINDOW_INTRO if intro else self.AEM_A_WINDOW_PLAIN
        macro.macro_spam(window, self.BURST_A_INTERVAL,
                         tag=f'aemA{"34" if intro else "14"}')
        # 【R1 要落在 A4 的后撤动作途中】, 所以这里只留够 A4 出手, 不等后撤演完
        macro.macro_wait(self.M_A4_TO_R1)
        # 【R1 发出去之后不等演出结束, 直接按住攻击键按穿它】。
        # 等演出结束再按 = 一次新的按下沿, 而【按下沿会被判成普攻】(按住 = 先普攻,
        # 续按才变重击) —— 用户 2026-08-13 看到的"R1接重击中间多了下平A"就是它。
        # 全程一次按住不松就没有第二次起手, 那下 A 自然消失。
        # 这跟 8-12 修强化E接重击是同一个坑同一个解法(见 cast_e2_into_heavy)
        lib1 = self.fire_r1(macro, wait_end=False)
        macro.macro_wait(self.M_R1_TO_HEAVY)
        self.hold_until_heavy_fired(macro, self.R1_HEAVY_HOLD, 'R1heavy',
                                    min_hold=self.R1_HEAVY_MIN_HOLD)
        self.f_break()
        macro.macro_mark('F')
        macro.macro_wait(self.M_AFTER_F)
        e1_done = False
        if self.OPENER_F_THEN_E_SPAM:
            # 【F 之后连发 E】(2026-08-14 从循环轴移植: 用户要求"F之后...只按E")。
            # burst_tail 第一步就是强化 E, 而处决的收招会吞掉单发的键 ——
            # 连发才吃得到放行的第一帧
            ne = 0
            t0 = time.time()
            while self.time_elapsed_accounting_for_freeze(t0) < self.OPENER_F_E_WINDOW:
                self.send_resonance_key()
                ne += 1
                self.task.next_frame()
                self.sleep(self.EF_SPAM_INTERVAL, check_combat=False)
            macro.macro_mark(f'F_E(x{ne})')
            self.record_resonance_use()
            self.logger.info(f'opener burst: F -> E spam x{ne}')
            e1_done = True
        lib2 = self.burst_tail(macro, skip_e1=e1_done)
        self.logger.info(f'opener burst: R1={lib1} R2={lib2} '
                         f'{self.time_elapsed_accounting_for_freeze(start):.1f}s')
        self.logger.info(f'opener burst timeline: {macro.macro_timeline(mark0)}')
        return lib2

    # ---- 爱达千的收尾爆发 ----
    # 两段收尾只有【前缀】不同, 后面是同一串(用户 2026-08-16 给的轴):
    #   启动轴: 变奏入场 -> Q ------------> ┐
    #   循环轴: 变奏入场 -> A到A3 --------> ┤ R1 -> 强化E -> F处决
    #                                       └-> A到强化E再亮 -> 强化E -> 蓄力重击 -> R2
    # 收尾的最后一步是【R2 演出期间连发 E】—— 演出一结束就变机甲, 那一下就是
    # 循环轴的第一个动作("爱E"), 也是两条轴的接口。跟爱琳莫那条轴同一个形状
    DENIA_BURST_A_INTERVAL = 0.12

    def perform_denia_burst(self, macro, echo=True, a_window=0.0):
        """爱达千的收尾爆发。

        Args:
            echo: 开头放不放声骸 Q。启动轴的轴上写了 Q(变奏爱q r1...), 循环轴没有。
            a_window: 开 R1 之前先连点多久。循环轴要"A到A3", 启动轴是 0(变奏入场
                之后直接 Q + R1)。
                【必须是连点窗口不是数点击数】: 变奏入场的演出 0.9 秒期间点击全丢,
                固定 stride 猜错一次这几段就整个没了。

        Returns: R2 有没有放出去(False 说明这一轮的循环轴接不上机甲)。
        """
        if macro is None:
            self.logger.warning('denia burst: no macro engine, falling back to perform_everything')
            self.perform_everything()
            return False
        mark0 = len(macro.macro_marks)
        start = time.time()
        if echo:
            self.click_echo(time_out=0)
            macro.macro_mark('aemQ')
        if a_window > 0:
            macro.macro_spam(a_window, self.DENIA_BURST_A_INTERVAL, tag='burstA3')
        # 【只发键, 一下普攻都不补】: spin_until(click_liberation) 每轮补一下普攻再睡
        # 0.1 秒, 那下普攻会顶掉正在演的动作(R1 要落在变奏/A4 的动作里), 而且
        # 0.1 秒一轮太粗卡不进窗口
        lib1 = self.fire_lib_fast(macro, 'R1', self.CYCLE_LIB1_TIME_OUT)
        # 【R 的演出里只发 E, E 的演出里只发 F】—— 两段不能混着发:
        # 混发的话 F 会落在 R 的演出里, 处决提前触发(实机见过"怎么提前处决了");
        # 处决必须落在【第一个强化E 的动画里】。演出期间的按键被丢掉不排队,
        # 所以靠连发盖住整段, 不靠算准某一帧
        self.spam_e_then_f(macro, tag='R1')
        # 【处决必须有兜底】: 上面那段 F 连发被吞掉就再也没有第二次机会。
        # E 不用补(连发已经放出去了, 补了反而白烧一个 CD)
        self.f_break_persist(tag='burstF')
        # 【边 A 边等第二个强化E 亮】—— 轴上写的就是"A到强化E亮"。
        # 这里【不】先等第一个灭掉: 上面 F 连发已经过去 3 秒多, 第一个 E 早就消费完了,
        # 读数是干净的。(紧挨着放完 E 就查的地方才需要"先确认它灭过", 见 burst_tail)
        self.spin_until(self.enhance_e_available, self.CYCLE_E2_TIME_OUT, 'E2 relight')
        macro.macro_mark('E2_lit')
        # 强化E -> 一路按住穿过整个演出 -> 蓄力重击; 按住期间一直补发 R,
        # 重击一出手 R2 就吃到第一个合法帧
        self.cast_e2_into_heavy(macro)
        lib2 = self.fire_r2_fast(macro)
        if lib2:
            # 【R2 演出期间就一直点 E】: 演出结束那一刻直接变机甲, 那一下就是
            # 循环轴的第一个动作。演出结束之后再发单发的 E 试过, 不生效
            self.spam_e_through_animation(macro, tag='mechaE')
        self.record_liberation(True)
        self.logger.info(f'denia burst: R1={lib1} R2={lib2} '
                         f'{self.time_elapsed_accounting_for_freeze(start):.1f}s')
        self.logger.info(f'denia burst timeline: {macro.macro_timeline(mark0)}')
        return lib2

    def fire_r1(self, macro, wait_end=True):
        """R1。

        【发键不能用 spin_until(click_liberation)】: 它每轮之间补一下普攻再睡 0.1 秒,
        那下普攻会顶掉 A4 的后撤动作, 而 R1 必须落在【后撤动作途中】(用户 2026-08-13);
        0.1 秒一轮的节奏也太粗, 卡不进那个窗口。

        Args:
            wait_end: 要不要等演出演完再返回。
                循环轴那段 R1 之后接的是强化E, 必须等(True)。
                启动轴那段 R1 之后接的是【重击】, 反而【不能等】—— 见
                perform_opener_burst 里的说明: 要在演出期间就把攻击键按下去。
        """
        fired = self.fire_lib_fast(macro, 'R1', self.CYCLE_LIB1_TIME_OUT)
        if fired and wait_end:
            self.wait_lib_animation_end(macro, 'R1')
        return fired

    def spam_r_e_until_lib(self, macro, tag='R1', time_out=None):
        """处决之后: 先只连发 R 一段, 再只连发 E 一段。

        用户 2026-08-14: "F之后马上只按R 1.5s, R之后马上只按E 2s"。
        【两段分开, 不混着发】: 混发的话 E 可能在大招还没出手时就被吃掉一次,
        白烧一个 CD; 分开之后第一段只争 R 的出手, 第二段才把 E 预输入进演出里
        (演出期间的按键会被带过边界, 演出一结束就兑现)。
        """
        start = time.time()
        fired = False
        nr = 0
        while self.time_elapsed_accounting_for_freeze(start) < self.LOOP_R_SPAM_WINDOW:
            self.send_liberation_key()
            nr += 1
            self.task.next_frame()
            if not fired and nr > 1 and not self.task.in_team()[0]:
                fired = True
            self.sleep(self.EF_SPAM_INTERVAL, check_combat=False)
        t_r = time.time()
        ne = 0
        # 【窗口设 0 = 整段不发 E】(2026-08-14 用户: "把E去了先")。
        # 怀疑就是这 20 下 E 把强化 E 的状态吃掉了 —— 后面 E2 拿不到强化态,
        # 按住只能出普通重击(用户: "放RE然后会普通重击 流程错了")
        back_at = None
        # 【E 要一直发到演出真的结束, 不能按固定时长】(2026-08-14 实机):
        # R 在前面那 1.5 秒窗口里【哪一刻出手是浮动的】—— 早出手(0.3s)时演出
        # 4.3 秒就结束, E 还递得进去; 晚出手(1.4s)时演出到 5.4 秒才完, 而固定
        # 3.5 秒的 E 早就停了, 35 下一次都没进去
        # (日志: enhance E never went dark / E2heavy fired=False)。
        # 现在盯 HUD 回来 = 演出结束, 再补 LOOP_E_AFTER_ANIM 秒确保递得进去
        while self.time_elapsed_accounting_for_freeze(t_r) < self.LOOP_E_SPAM_WINDOW:
            self.send_resonance_key()
            ne += 1
            self.task.next_frame()
            if back_at is None:
                if self.task.in_team()[0]:
                    back_at = time.time()
            elif time.time() - back_at >= self.LOOP_E_AFTER_ANIM:
                break
            self.sleep(self.EF_SPAM_INTERVAL, check_combat=False)
        if macro is not None:
            macro.macro_mark(f'{tag}_R(x{nr})_E(x{ne})')
        self.logger.info(f'burst: {tag} R spam x{nr} ({self.LOOP_R_SPAM_WINDOW:.1f}s, '
                         f'lib {"fired" if fired else "NOT fired"}) -> '
                         f'E spam x{ne} ({self.LOOP_E_SPAM_WINDOW:.1f}s)')
        if fired:
            self.record_liberation(False)
        self.record_resonance_use()
        return fired


    def spam_e_then_f(self, macro, tag='R1'):
        """R 之后【先只按 E】(盖住 R 的演出), 再【只按 F】(盖住 E 的演出)。

        用户 2026-08-14 逐帧给的时间(60fps):
            R 动画 2s01 -> 6s01 = 4.00 秒
            E 动画 6s12 -> 8s18 = 2.10 秒
        各加 1 秒防漏按 —— 【E 和 F 不能同时发】: 同时发的话 F 落在 R 的演出里,
        处决提前触发(用户: "怎么提前处决了")。处决必须落在【第一个 E 的动画里】。
        演出期间的按键会被游戏边界截掉, 所以靠连发盖住整段, 不靠算准某一帧。
        """
        n_e = 0
        t0 = time.time()
        while self.time_elapsed_accounting_for_freeze(t0) < self.OPENER_R_ANIM_SPAM:
            self.send_resonance_key()
            n_e += 1
            self.task.next_frame()
            self.sleep(self.EF_SPAM_INTERVAL, check_combat=False)
        self.record_resonance_use()
        if macro is not None:
            macro.macro_mark(f'{tag}_E(x{n_e})')

        n_f = 0
        t1 = time.time()
        while self.time_elapsed_accounting_for_freeze(t1) < self.OPENER_E_ANIM_SPAM:
            self.task.send_key('f')
            # 【按 F 的同时连点】(用户 2026-08-14: "处决完了没有马上A, 发了会儿呆")。
            # 处决往往在这 3.1 秒的前段就打完了, 剩下的时间只发 F 不点 = 站着发呆。
            # 框架自己的 f_break() 也是 send_key('f') + click() 一起发的, 不冲突
            self.click()
            n_f += 1
            self.task.next_frame()
            self.sleep(self.EF_SPAM_INTERVAL, check_combat=False)
        if macro is not None:
            macro.macro_mark(f'{tag}_F(x{n_f})')
        self.add_freeze_duration(t0, time.time() - t0)
        self.logger.info(f'burst: {tag} E spam x{n_e} ({self.OPENER_R_ANIM_SPAM:.1f}s) '
                         f'-> F spam x{n_f} ({self.OPENER_E_ANIM_SPAM:.1f}s)')
        return True

    def spam_e_f_through_lib(self, macro, tag='R1', time_out=8.0):
        """大招演出期间【同时】连发 E 和 F, 发到演出结束(HUD 回来)为止。

        用户 2026-08-14: "我需要 R 的同时长按 E 和 F 直到开始 A"。
        【为什么不是串行】: 演出期间的按键会被游戏带过边界, 演出一结束按先后
        自己解开; 而"等演出结束再发"要先付一整段演出时长, 单发还会被收招动作
        吞掉 —— 这个形状今天已经踩了六七次。
        """
        start = time.time()
        n = 0
        back = False
        back_at = None
        while self.time_elapsed_accounting_for_freeze(start) < time_out:
            self.send_resonance_key()
            self.task.send_key('f')
            n += 1
            self.task.next_frame()
            if back_at is None and self.task.in_team()[0]:
                # 【演出结束不立刻停, 再发一个尾巴】(用户 2026-08-14: "长按时间
                # 是不是短了")。HUD 回来那一刻她可能还在收招里, 那几下照样被吞 ——
                # 尾巴保证有几发落在真正能出手的帧上
                back = True
                back_at = time.time()
            if back_at is not None and time.time() - back_at >= self.EF_SPAM_TAIL:
                break
            # 【必须 check_combat=False】: 大招演出期间读不到目标, 默认的 sleep
            # 会跑战斗判定并抛异常 —— 实测整个连发在 R1 之后 0.17 秒就被打断,
            # 接着判脱战、整轮报废(2026-08-14)。旁边 hold_until_heavy_fired
            # 早就是这么写的, 我没照做
            self.sleep(self.EF_SPAM_INTERVAL, check_combat=False)
        self.add_freeze_duration(start, time.time() - start)
        if macro is not None:
            macro.macro_mark(f'{tag}_EF(x{n},{"ok" if back else "timeout"})')
        self.logger.info(f'burst: {tag} E+F spam x{n} in {time.time() - start:.2f}s '
                         f'(anim {"ended" if back else "TIMEOUT"})')
        self.record_resonance_use()
        return back

    def wait_lib_animation_end(self, macro, tag, time_out=8.0):
        """等大招演出结束(队伍 HUD 回来), 并把这段计入 freeze。"""
        start = time.time()
        back = self.task.wait_until(lambda: self.task.in_team()[0], time_out=time_out)
        self.add_freeze_duration(start, time.time() - start)
        if macro is not None:
            macro.macro_mark(f'{tag}_end({"ok" if back else "timeout"})')
        return back

    def burst_tail(self, macro, skip_e1=False):
        """两段收尾共用的尾巴: 强化E -> A到A4 -> 强化E -> 重击 -> R2。"""
        # "接强化E速度要快"(用户) —— R1 之后立刻按, 不留缓冲
        macro.macro_wait(self.LOOP_R1_TO_E)
        if not skip_e1:
            self.cast_enhance_e(macro, tag='E1')
        else:
            # 【前面的连发已经把强化 E 放出去了, 这里不能再放一次】(2026-08-14):
            # 实机日志 F@28.07 F_E(x10)@29.16 E1_timeout(x12)@31.11 ——
            # cast_enhance_e 发 12 次全落空、跑满超时才往下走, 白亏 2 秒,
            # 表现出来就是"E 放完没马上接 A"
            macro.macro_mark('E1_by_spam')
        # 【用连点窗口不用离散点击】: 强化E 的演出刚过, 立刻点的那一下会被后摇吃掉;
        # 连点总有一下落在能接受输入的第一帧, 打出几段由窗口长度决定
        macro.macro_spam(self.BURST_A4_WINDOW, self.BURST_A_INTERVAL, tag='burstA')
        # 窗口打完强化E 还没亮就边A边等 —— 轴上写的就是"A到强化E技能再亮了"
        # 【先等第一个强化E灭掉, 再等第二个亮】(用户 2026-08-14: "总是在第一个
        # 强化E后接重击了, 不对, 应该A到第二个强化E亮起接E")。
        # 第一个 E 刚放完时读数还是"亮"的, 下面那句会【立刻通过】—— 于是把第一个
        # 当成了第二个, A 串草草结束就接重击。
        # 【判"某状态出现"之前先确认它消失过】: 今天第五次栽在这个形状上。
        # spin_until 本身边点边等, 所以这两段就是用户要的"A 到第二个 E 亮起"
        if not self.spin_until(lambda: not self.enhance_e_available(),
                               self.E_CONSUMED_TIME_OUT, 'E1 consumed'):
            self.logger.warning('burst: enhance E never went dark, 第一个 E 可能没放出去')
        macro.macro_mark('E1_dark')
        self.spin_until(self.enhance_e_available, self.LOOP_E_RELIGHT_TIME_OUT, 'E2 relight')
        macro.macro_mark('E2_lit')
        self.cast_e2_into_heavy(macro)
        # 【重击一出手就不停按 R】(用户 2026-08-13)。原来走 spin_until(click_liberation):
        # 放不出就补一下普攻、再等 0.1 秒才重试 —— 节奏太慢, 而且那下普攻会顶掉重击的收尾
        lib2 = self.fire_r2_fast(macro)
        # 【R2 发完必须等演出结束再把控制权交回去】(2026-08-13 实机)。
        # fire_lib_fast 是"演出一开始就返回"的, 不等的话这里一返回, 框架的战斗判定
        # 就接手了 —— 而【大招演出期间 has_target() 读不到目标】, 它会 target_enemy
        # 中键连点满 3 秒都锁不上, 于是判定脱战。日志形状:
        #   R2_fired@37.95 -> (39ms 后) target lost -> 3 秒后 break out of combat
        # 脱战之后重新进战斗会 load_chars -> reset_state -> opener_armed 重新置 True,
        # 于是【又跑一遍启动轴, 循环轴永远轮不到】(实机 67 次启动轴, 0 次循环轴)。
        # 跟 R1 那处是同一个 bug, 当时只修了 R1
        if lib2:
            # 【R2 演出期间就一直点 E, 等她能放的那一刻直接变机甲】
            # (用户 2026-08-13: "直接在R2动画过程中一直持续点按E不就好了")。
            # 演出结束之后再发单发的 E 试过, 不生效 —— 所以改成预输入。
            # 顺带也解决了上面说的脱战误判: 这个循环本身就把控制权按在演出里,
            # 不会在演出中途交回框架
            self.spam_e_through_animation(macro, tag='mechaE')
        self.record_liberation(True)
        return lib2

    # HUD 回来之后再补几下 E。1 -> 10 (用户 2026-08-13: "按了E但是没按出来, 加到10次")。
    # 【这是有代价的】: 轴上机甲形态还有一发 E(打断 A3), 补太多会把它提前按掉,
    # 症状是后面 aemE2 打断不了 A3。所以先用它换"到底能不能变机甲"这个答案,
    # 变得出来之后要往回收
    # 【这是【上限】不是固定次数】(2026-08-19)。HUD 回来之后每补一发就花
    # MECHA_E_INTERVAL(0.12) 秒, 而且每发都跟一下点击 —— 用户两条反馈正好是它的两面:
    #   "爱E切达慢了0.78s" + "变了机甲有多A了几下才切达"
    # 我据此把它从 10 砍到 3, 结果【变不出机甲了】—— 说明这十发不是冗余,
    # 变身真的需要 HUD 回来之后再敲好几下。所以砍次数是错的方向。
    # 现在改成【变出来就停】: 每发之后看 E 还可不可用, 不可用 = 这一发吃进去了,
    # 立刻退出。顺利的轮次一两发就走(该省的时间和多余的 A 一起省掉),
    # 不顺的轮次照样能敲满 10 发(退化成改动之前的行为, 不会再变不出机甲)
    MECHA_E_EXTRA = 10
    # 补发的间隔。比 R2_KEY_INTERVAL(0.05) 大 —— 技能键要铺开落在不同的帧/状态上,
    # 挤在半秒内连发 10 下等于只试了一个时刻
    # 0.12 -> 0.10 (用户 2026-08-19: "循环轴一开头爱弥斯E切达再快0.2s试试")。
    # 【那 0.2 不在循环轴里, 在这条尾己上】: 循环轴开头实测只有
    #   con(full)@0.00 -> >Den+aemE^(0.11) —— 总共 0.11, 拿不出 0.2。
    # 而紧接在它前面的是这段: after x10 × 0.12 = 【整整 1.20 秒】,
    # 期间 A x0(一下普攻都没有) —— 画面上就是"爱弥斯在那里敲E半天才切达"。
    # 10 发 × 0.10 = 1.00, 正好减 0.20。
    #
    # 【代价: 覆盖时长从 1.20 缩到 1.00】—— 发数没变(还是 10 发),
    # 只是排得密了。如果她变成能放 E 的那一刻落在 1.00~1.20 之间,
    # 就会变不出机甲 —— 【症状是"小爱没有变机甲", 上次把 extra 砍到 3
    # 就是这个症状】。真发生了把这一行改回 0.12。
    #
    # 【真正该修的是早退判据】: 日志里每轮都是 after x10,
    # 也就是说 resonance_available() 从来没读到过"E 进 CD 了",
    # 早退一次都没生效、这条尾巴每轮都花满。修好它比砍时长好得多
    MECHA_E_INTERVAL = 0.10
    # 【变机甲那一段要不要顺带点普攻】(用户 2026-08-19: "爱弥斯E的时候不能普攻,
    # 然后切达的瞬间开始连续普攻")。False = 整段只发 E 不点, 点击推迟到切达那一刻。
    # 爱琳莫那条轴当初是 True(理由: R2 之后有强直, E 能打断它, A 跟着一起点,
    # 变机甲下一帧连段就开始推) —— 那份在另一个队伍目录里, 不受这里影响
    MECHA_CLICK = False
    # 大招演出期间连发 E/F 的间隔
    # 用户 2026-08-14 逐帧: R 动画 2s01-6s01 = 4.00 秒, E 动画 6s12-8s18 = 2.10 秒。
    # 各加 1 秒防漏按
    OPENER_R_ANIM_SPAM = 5.0     # R 之后只按 E 的时长
    OPENER_E_ANIM_SPAM = 3.1     # 再只按 F 的时长(处决要落在第一个 E 的动画里)
    # 收尾处决在这个窗口里反复尝试(见 f_break_persist)。没有处决可打时按 F 不会
    # 触发别的东西, 所以给长一点是净赚
    # 等第一个强化E被用掉(读数灭)的上限, 见 burst_tail
    E_CONSUMED_TIME_OUT = 2.0
    # 2.0 -> 0.6: 处决现在由 spam_e_then_f 的 F 连发负责了, 这里只兜"提示来得
    # 特别晚"的情况 —— 留 2.0 的话, 没处决可打的轮次要白等 2 秒
    OPENER_F_WINDOW = 0.6
    F_RETRY_INTERVAL = 0.10
    EF_SPAM_INTERVAL = 0.08
    # 大招演出结束之后还要继续连发 E/F 多久(收招期间的按键照样会被吞)
    # 0.60 不够(用户 2026-08-14) -> 2.0。演出结束之后的收招比想象的长得多
    EF_SPAM_TAIL = 2.0
    # 强化重击按住期间是否一直发 R(用户要求; 重击动画会吞掉单发的 R)
    HEAVY_SPAM_LIB = True
    # 重击期间补发 R 的间隔(比 E2_POLL 密, 免得被读屏节奏卡住)
    LIB_SPAM_INTERVAL = 0.04
    # 处决之前先发一发 R/E(f_break 会阻塞约 0.5 秒, 等它跑完再发就晚了)
    # 【必须 F 了才开始发 R/E】(用户 2026-08-14 明确): 提前发那一下是错的
    LOOP_R_BEFORE_F = False
    # 处决之后: 先只连发 R 这么久, 再只连发 E 这么久(用户 2026-08-14 指定)
    # V4 启动轴收尾: 处决之后连发 E(形状跟循环轴那段一样)。
    # 【窗口比循环轴短】: 启动轴 1.0 秒(用户 2026-08-14), 循环轴那边是 2.0
    OPENER_F_THEN_E_SPAM = True
    OPENER_F_E_WINDOW = 1.0
    LOOP_R_SPAM_WINDOW = 1.5
    # R 之后连发 E 的窗口。2.0 -> 3.5(用户 2026-08-14: "循环轴里有时候小爱R1完
    # 不接E, 在动画时间里就开始长按")。
    # 【2.0 根本盖不住 R 的演出】: R 动画实测 4.00 秒(用户逐帧), 而这里是
    #   先只发 R 1.5 秒 -> 再只发 E 2.0 秒 = E 只覆盖到 3.5 秒就停了,
    #   演出还没结束, 那些 E 全被吃掉 —— 所以"R1完不接E"。
    # 【这是上限, 不是固定时长】: 正常情况看 HUD 回来就提前收手(见
    # spam_r_e_until_lib)。上限按最坏情况给 —— R 在 1.5 秒窗口的最后才出手,
    # 演出 4.0 秒, 要发到 5.5 秒
    # (和 skip_e1 绑在一起, 见 skip_e1=LOOP_E_SPAM_WINDOW > 0)
    LOOP_E_SPAM_WINDOW = 5.5
    LOOP_E_AFTER_ANIM = 0.6      # 演出结束后再补发多久的 E

    def spam_e_through_animation(self, macro, tag='mechaE', interval=None, extra=None,
                                 time_out=8.0):
        """大招演出期间一直发 E, HUD 一回来再补 extra 下就停。

        【为什么不能"等演出结束再发一次"】: 演出多长不知道, 而演出期间的按键是被
        丢掉的、不排队 —— 单发要么埋在演出里, 要么等太久。连发免疫被吞。

        【为什么必须停】: 轴上机甲形态还有一发 E(用来打断 A3), 一直发下去会把它
        提前按掉。以【队伍 HUD 回来】为界最稳: 那一刻她才重新可操作, 补一下就够。

        Returns: 演出有没有正常结束(False 说明等到超时, 后面多半也不对了)。
        """
        interval = self.R2_KEY_INTERVAL if interval is None else interval
        extra = self.MECHA_E_EXTRA if extra is None else extra
        start = time.time()
        n = 0
        clicks = 0
        back = False
        while time.time() - start < time_out:
            self.send_resonance_key()
            n += 1
            # 【爱达千这条轴上 E 期间不点】(用户 2026-08-19: "感觉爱弥斯E的时候
            # 不能普攻, 然后切达的瞬间开始连续普攻")。点击改由【切达那一刻】开始,
            # 见 Denia.loop_seg1。下面这段是爱琳莫那条轴的原始理由, 保留备查:
            # 【E 和 A 同时点】(用户 2026-08-13: "E和a同时开始也可以")。
            # R2 之后爱弥斯有一段强直, E 变机甲能打断它 —— 也就是说她能动的那一刻
            # 是由 E 决定的, 不是由某个固定时长决定的。A 跟着一起点, 变机甲的
            # 下一帧连段就开始推, 不用等 E 这边全部发完。
            # 【原来是串行的】: E 发满(演出 + HUD 回来后又 1.2 秒)才轮到 A,
            # 那 1.2 秒她已经能动却站着 —— 而且把 A 的连点窗口整体往后推,
            # 我为此一路加大 M_MECHA_A_WINDOW, 其实是在补偿一个不该存在的延迟
            if self.MECHA_CLICK:
                self.click()
                clicks += 1
            self.sleep(interval, check_combat=False)
            self.task.next_frame()
            if self.task.in_team()[0]:
                back = True
                break
        hud_at = time.time()
        for _ in range(extra):
            self.send_resonance_key()
            n += 1
            if self.MECHA_CLICK:
                self.click()
                clicks += 1
            # 【E 进 CD 了就停】: 发出去和吃进去是两回事, 但 E 一旦被消耗掉,
            # 图标就不再可用 —— 那就是"变机甲成功"的信号, 再敲下去只是白花
            # 0.12 秒并多推一段连段(用户: "变了机甲有多A了几下才切达")。
            # 【检查放在 sleep 【之前】】(用户 2026-08-19: "切人还要再快0.13s"):
            # 原来是"发E -> 点 -> 睡0.12 -> 才检查", 于是哪怕第一发就成功,
            # 也一定先白睡一个间隔才退出 —— 那 0.12 正好就是用户量到的 0.13。
            # 【代价】: 图标可能还没刷新就被查了, 那就多发一发再走, 净亏 0 —— 因为
            # 那一发本来在旧写法里也要发。【读不到就退化成敲满 extra 发】, 不会更糟
            try:
                self.task.next_frame()
                if not self.resonance_available():
                    break
            except Exception:
                pass
            self.sleep(self.MECHA_E_INTERVAL, check_combat=False)
        self.record_resonance_use()
        self.record_enhance_e()
        if macro is not None:
            # 演出里发了几下 / HUD 回来之后发了几下, 分开记 —— 变不出机甲时要靠
            # 这两个数分辨是"演出太长没等到"还是"能操作了但 E 就是放不出来"
            macro.macro_mark(f'{tag}({"ok" if back else "timeout"},'
                             f'anim x{n - extra}/{hud_at - start:.2f}s,after x{extra},A x{clicks})')
        self.logger.info(f'burst: {tag} E x{n} + A x{clicks} ({n - extra} E during the R2 '
                         f'animation over {hud_at - start:.2f}s, {extra} after hud back={back})')
        return back

    def cast_enhance_e(self, macro, tag='E'):
        """发强化E。

        【必须连着发, 不能只发一次】(用户 2026-08-13: "点了处决直接预输入一直点
        强化E就行")。单发那一下常常落在上一个动作的动画里被丢掉 —— 动画期间的按键
        是被丢掉的、不排队, 于是要等动画整个演完才有第二次机会,
        画面上就是"最后接强化E那里慢了"。

        判据是【E 进了 CD】: 发出去和放出来是两回事。进了 CD 立刻停, 不会多按掉
        第二发(轴后面还要一发强化E)。读不到 CD 就发满窗口, 日志里 {tag}_timeout(xN)
        会说明是哪种情况。
        """
        if not self.enhance_e_available():
            self.logger.info(f'burst: enhance E not lit at {tag}, sending anyway')
        macro.resend_until_fired(self, 'resonance', self.get_resonance_key(),
                                 self.E1_RESEND_TIME_OUT, tag=tag)
        self.record_enhance_e()
        self.click_echo(time_out=0)
        self.task.next_frame()

    def hold_until_heavy_fired(self, macro, hold_max, tag, poll=None, lit_time_out=None,
                               min_hold=0.0, spam_lib=False):
        """按住攻击键直到重击真的打出去 —— 判据是【高亮先亮起、再灭掉】。

        只查"灭了没有"是假阳性: 它一开始本来就是灭的, 一查就成立, 会报出一个
        实机根本不存在的重击。这个坑在这条轴上出现过三次(升天重击、琳奈蓄力、
        这里), 凡是判"某个状态消失"都要先确认它曾经出现过。

        Args:
            lit_time_out: 高亮多久还不亮就放弃松手。高亮没亮时按住【不是无效输入,
                是有效的普攻输入】, 白按只会多出一串平A。传 None 表示不提前放弃
                (强化E 那处已知要 2.96 秒才亮, 中途放弃会前功尽弃)。
            min_hold: 这么久之内不接受"亮过又灭"。防的是【大招/技能演出刚结束那几帧
                HUD 是乱的】: 高亮先读到 True 下一帧又 False, 于是按住 0.04 秒就松手,
                那只是一次点击、一下平A, 重击根本没出(2026-08-12 踩过一次)。
        """
        poll = self.E2_POLL if poll is None else poll
        self.task.mouse_down()
        samples = []
        seen_lit = False
        fired = False
        start = time.time()
        try:
            while time.time() - start < hold_max:
                lit = bool(self.has_long_action())
                if len(samples) < self.E2_SAMPLE_MAX:
                    samples.append(f'{time.time() - start:.2f}:{int(lit)}')
                held = time.time() - start
                if lit:
                    seen_lit = True
                elif seen_lit and held >= min_hold:
                    fired = True  # 亮过又灭 = 重击真的打出去了
                    break
                elif not seen_lit and lit_time_out is not None and held > lit_time_out:
                    break  # 一直没亮, 再按下去只是白打平A
                if spam_lib:
                    # 【重击期间一直发 R】(用户 2026-08-14: "最后的强化重击接R
                    # 必须在强化重击期间一直长按R")。重击的动画会把单发的 R 吞掉,
                    # 连发才能吃到收招放行的第一帧 —— 出手时刻不再靠算。
                    # 【发键频率要跟读屏解耦】: 原来是"读一次高亮发一次 R", R 就
                    # 被 E2_POLL(0.12) 卡住了 —— 放行的帧落在两次发键中间就要多等
                    # 一个间隔, 那正是用户说的"有时候马上接上有时候慢一点"。
                    # 现在读屏照旧 0.12, R 在这中间每 LIB_SPAM_INTERVAL 补发一次
                    spent = 0.0
                    while spent < poll:
                        self.send_liberation_key()
                        step = min(self.LIB_SPAM_INTERVAL, poll - spent)
                        self.sleep(step, check_combat=False)
                        spent += step
                else:
                    self.sleep(poll, check_combat=False)
        finally:
            self.task.mouse_up()
        self.sleep(0.01)
        if macro is not None:
            macro.macro_mark(f'{tag}({"hit" if fired else "miss"})')
        self.logger.info(f'burst: {tag} held {time.time() - start:.2f}s fired={fired} '
                         f'highlight[{" ".join(samples)}]')
        return fired

    def loop_macro(self):
        """拿到持宏的那个角色实例。启动轴还没跑完就返回 None。"""
        macro = self.burst_macro()
        if macro is None or not hasattr(macro, 'perform_loop'):
            return None
        if getattr(macro, 'opener_armed', True):
            return None  # 启动轴还没跑, 循环轴不能抢在它前面
        return macro

    def opener_not_run_yet(self):
        """启动轴该跑但还没跑。这时爱弥斯【不该占着场子】——
        两条轴的开场角色都不是她(爱琳莫是琳奈, 爱达千是千咲), 她要到最后才进来。"""
        macro = self.burst_macro()
        if macro is not None and hasattr(macro, 'opener_pending'):
            return bool(macro.opener_pending())
        return False

    def do_denia_perform(self):
        """爱达千队里爱弥斯是【循环轴的入口】: 她放完 R2 就在场上(机甲形态),
        循环轴的第一步就是从她这里起跑。

        启动轴的入口不是她 —— 是千咲(轴第一个动作是千咲的 E)。所以启动轴还没跑的
        时候她要【立刻】把场子让出去, 不能在这里打两秒普攻(那两秒是白扔的)。
        """
        macro = self.burst_macro()
        if macro is None or not hasattr(macro, 'perform_loop'):
            # 队伍对但达尼娅那份自定义没启用 —— 别硬跑, 走她自己的通用轮换
            self.logger.warning('denia cycle: no macro engine (Denia custom off?), '
                                'falling back to perform_everything')
            self.perform_everything()
            return self.switch_next_char()
        if hasattr(macro, 'opener_pending') and macro.opener_pending():
            if getattr(macro, 'USE_LOOP_AS_OPENER', False):
                # 【开场就在场时直接从她这里起跑】(用户 2026-08-19:
                # "我现在开局就是小爱在场")。不这么做的话她会把场子让给
                # 千咲, 千咲再把她切回来 —— 白花两次切人(爱琪莫那条轴上
                # 因为同样的形状白丢过 1.8 秒)。
                # 标记要在跑之前落, 避免中途抛异常时重跑
                macro.opener_armed = False
                macro.last_opener = time.time()
                if macro.perform_opener(self):
                    return
                self.logger.warning('loop-as-opener aborted, falling back')
            else:
                return self.switch_next_char()
        loop = self.loop_macro()
        if loop is not None and loop.perform_loop():
            return
        # 循环轴没跑起来(队伍不全/宏被打断): 打一小段再把场子交出去, 别站着
        self.continues_normal_attack(1.0)
        return self.switch_next_char()

    def do_perform(self):
        if self.is_in_denia_cycle:
            return self.do_denia_perform()
        if self.is_in_25s_cycle:
            # 启动轴的收尾爆发优先, 跑完它才算进入循环。
            # 【跑完不切人】: 轴的下一步就是她自己的"E + 机甲A×3 + E", 人不用动,
            # 下一次 do_perform 直接落进 perform_loop
            if getattr(self, 'opener_burst_pending', False):
                self.opener_burst_pending = False
                # 【两版的启动轴收尾不一样】: V4 是 A3A4->R1->重击->F+共用尾巴
                # (perform_opener_burst); V3 没有这个方法, 当时就是交给
                # perform_25s_cycle 收的 —— 原样保留, 别拿 V4 的收尾去套 V3 的轴
                macro = self.burst_macro()
                if macro is not None and getattr(macro, 'OPENER_VERSION', 4) in (2, 3, 5):
                    # 让 perform_25s_cycle 知道自己这一次是【启动轴收尾】而不是兜底,
                    # 好跳过声骸 Q。用完立刻清掉, 免得影响后面的兜底轮次
                    self.opener_burst_now = True
                    try:
                        self.perform_25s_cycle()
                    finally:
                        self.opener_burst_now = False
                else:
                    self.perform_opener_burst()
                return
            # 启动轴还没开跑 -> 立刻把场子让出去。原来这里会掉进 perform_25s_cycle
            # 的 else 分支打 2 秒普攻才走, 白扔 2 秒
            if self.opener_not_run_yet():
                return self.switch_next_char()
            macro = self.loop_macro()
            if macro is not None and macro.perform_loop():
                return
            self.perform_25s_cycle()
        else:
            self.intro_time = -1
            self.should_wait = False
            if self.has_intro:
                self.record_intro_liberation()
                self.continues_normal_attack(1.2)
                if self.check_outro() in LINNAI_NAMES | {'char_lupa'}:
                    self.intro_time = 14
                if self.check_outro() in {'chang_changli', 'char_changli2'}:
                    self.intro_time = 10
            self.perform_everything()
            self.switch_next_char()

    def lib(self):
        is_lib2 = self.lib2_available()
        if not is_lib2 and not self.can_cast_lib1():
            return False
        if not self.click_liberation(wait_if_cd_ready=0):
            return False
        self.record_liberation(is_lib2)
        self.f_break()
        return True

    def continue_in_intro(self):
        return self.time_elapsed_accounting_for_freeze(self.last_liber) < 30 and \
            self.time_elapsed_accounting_for_freeze(self.last_perform) < self.intro_time

    def perform_everything(self):
        start = time.time()
        self.should_wait = self.should_wait_for_lib2() or self.should_wait_for_enhance_e()
        if not self.should_wait:
            self.should_wait = self.has_intro and self.liberation_cooldown_left() < 12
        while self.time_elapsed_accounting_for_freeze(start) < 1.2 or (
                self.should_wait and self.time_elapsed_accounting_for_freeze(start) < 3.6):
            self.cycle_start()
            if self.handle_heavy():
                self.f_break()
                start = time.time()
                self.should_wait = self.should_wait_for_lib2()
                self.task.next_frame()
                continue
            if self.intro_lib1_ready() and self.lib():
                start = time.time()
                self.should_wait = self.should_wait_for_lib2()
            elif self.enhance_e_available():
                if self.click_resonance(has_animation=True, send_click=True, animation_min_duration=0.5,
                                        time_out=1.5)[0]:
                    self.record_enhance_e()
                    self.click_echo(time_out=0)
                    self.f_break()
                    self.task.next_frame()
                if (
                        self.intro_lib1_ready() and self.can_cast_lib1() and self.liberation_available()) or self.has_long_action():
                    start = time.time()
                else:
                    self.click(after_sleep=0.01)
                    return
            elif self.lib():
                start = time.time()
                self.should_wait = self.should_wait_for_lib2()
                continue
            else:
                self.click()
            self.cycle_sleep()
    
    def note_opener_burst(self):
        """启动轴的收尾就是爱弥斯这段爆发, 由 Mornye.py 里那条宏在切人之前打标记。

        不能靠 check_outro() 判断: 那读的是变奏入场的延奏标记, 而启动轴切进来时
        琳奈的协奏未必是满的, 读不到延奏就会掉进"打 2 秒普攻走人"的 else 分支,
        整条轴的收尾静默消失。
        """
        self.opener_burst_pending = True

    def f_break_persist(self, window=None, tag='F'):
        """在一个窗口内反复尝试处决, 而不是只赌调用那一瞬间。

        用户 2026-08-14: "启动轴里最后爱弥斯经常不放处决, 处决的时间可以放长一点,
        反正没有也不会触发什么"。
        【框架的 f_break() 只在"提示当下就亮着"时才动作】: 提示晚出来哪怕 0.1 秒,
        这一次就整个跳过(日志 skip F (prompt=False)) —— 这就是收尾经常没处决的原因。
        提示一亮 f_break() 自己会连按到打完, 所以这里只负责【一直问到它亮】。
        """
        window = self.OPENER_F_WINDOW if window is None else window
        start = time.time()
        n = 0
        while self.time_elapsed_accounting_for_freeze(start) < window:
            if self.f_break():
                self.logger.info(f'burst: {tag} fired after {n} tries '
                                 f'({time.time() - start:.2f}s)')
                return True
            self.click()   # 同上: 等处决提示的这段也别站着
            n += 1
            self.sleep(self.F_RETRY_INTERVAL, check_combat=False)
        self.logger.info(f'burst: {tag} never available in {window:.1f}s '
                         f'({n} tries) —— 这一轮本来就没有处决可打')
        return False

    def perform_25s_cycle(self):
        """【兜底路径】: 莫宁那条宏跑不起来(队伍不全 / perform_loop 返回 False)时
        才走这里。正常轴走 perform_opener_burst / perform_loop_burst, 不经过这里。
        """
        # 【启动轴收尾直接跳过这次读屏】(2026-08-14 实测): check_outro() 单次
        # 【0.635 秒】—— 它就是"宏结束到 R1"那段时间的全部。而这次读屏本来就是
        # 多余的: 宏刚刚把琳奈切成爱弥斯, 延奏来自谁是已知的, 不用去屏幕上认。
        # 【别把它当成框架调度开销】: 我先去掉了框架的 do_perform 来回, 时间几乎
        # 原样还在, 才定位到这一行 —— 读屏的钱要按"哪一次读"算, 不能按位置猜
        if getattr(self, 'opener_burst_now', False) or self.check_outro() in LINNAI_NAMES:
            # 变奏入场
            cycle_start = time.time()
            self.logger.info('burst cycle start')
            if getattr(self, 'opener_burst_now', False):
                # 【启动轴收尾必须跳过这次轮询】(用户 2026-08-14 实机描述解开的):
                # spin_until 每轮之间【补一下普攻】再睡 0.1 秒, 而爱弥斯此刻在空中
                # (机甲跳跃是飞天) —— 那一下补点变成【下落攻击】, 画面上就是
                # "飞很高 -> A 一下 -> 下落攻击 -> 这时才开 R1"。R1 等的不是能量,
                # 是那下下落攻击演完。
                # 而且这次轮询对 R1 本来就多余: 后面用的是 fire_lib_fast(只发键),
                # 不需要先读屏确认可放
                t_ready = 0.0
            else:
                self.spin_until(lambda: self.enhance_e_available() and self.liberation_available(),
                                self.CYCLE_READY_TIME_OUT, 'E+R ready')
                t_ready = time.time() - cycle_start
            # 【启动轴收尾不放声骸 Q】(用户 2026-08-14: "启动轴小爱的Q不要放了,
            # 好像会延迟输入, 虽然不打断动作")。宏里那个 aemQ 上一轮已经去掉,
            # 这个在爱弥斯自己的收尾代码里, 同样在启动轴期间放 —— 一起去掉。
            # 【它压在 R1 前面】: 实测跳跃到 R1 按下有 1.2 秒, 而宏只占 0.10
            if not getattr(self, 'opener_burst_now', False):
                self.click_echo()
            t_echo = time.time() - cycle_start
            if getattr(self, 'opener_burst_now', False):
                # 【启动轴收尾: 跳跃打断变奏后摇 -> R1 打断跳跃】(用户 2026-08-14
                # 说明的轴意图)。所以 R1 必须【紧贴跳跃】—— 而 spin_until(
                # click_liberation) 是"先读屏确认大招可放再按", 爱弥斯刚变奏入场
                # 又在跳跃动画里, HUD 读不出来, 它就在那反复读屏重试。
                # 【实测那段白等 0.63 秒】, 正好吃掉要用来打断跳跃的窗口。
                # 改成只发键、看 HUD 掉下去算数(动画期间的按键被丢掉不排队,
                # 所以只能连发到成立), 再单独等演出结束, 后面几步照旧在演出之后
                macro = self.burst_macro()
                send_lib1 = self.fire_lib_fast(macro, 'R1', self.CYCLE_LIB1_TIME_OUT)
                # 【R1 演出期间【同时】连发 E 和 F, 发到演出结束】
                # (用户 2026-08-14: "我需要R的同时长按E和F直到开始A")。
                # 原来是"等演出结束 -> 发E -> 发F"三段串行, 每段都在等上一段。
                # 演出期间的按键会被游戏带过边界, 演出一结束按先后自己解开
                self.spam_e_then_f(macro, tag='R1')
            else:
                send_lib1 = self.spin_until(self.click_liberation, self.CYCLE_LIB1_TIME_OUT, 'lib1')
            self.logger.info(f'aemeath burst pre-R1: ready {t_ready:.2f}s '
                             f'echo {t_echo - t_ready:.2f}s (opener={getattr(self, "opener_burst_now", False)})')
            if getattr(self, 'opener_burst_now', False):
                # 【处决必须有兜底】(用户 2026-08-14: "爱弥斯在最后没有接处决")。
                # 原来启动轴这条路【完全依赖 R1 演出里那次 E+F 连发】—— F 被吞掉
                # 就再也没有第二次机会。E 不用补(连发已经放出去了, 补了反而
                # 白烧一个 CD, 那个坑今天踩过), 只补 F
                self.f_break_persist(tag='openerF')
            else:
                self.click_resonance(has_animation=True)
                # 用框架的 f_break()。换成严格认屏幕中央提示的自制版之后, 提示一直
                # 读不到(日志里每轮 skip F (prompt=False)), F 就再没按出来过
                self.f_break()
            # 边 A 边等强化E亮 —— 轴上就是"A到强化E技能再亮了"
            self.spin_until(self.enhance_e_available, self.CYCLE_E2_TIME_OUT, 'E2 recharge')
            self.cast_e2_into_heavy(None)
            macro = self.burst_macro()
            send_lib2 = self.fire_r2_fast(macro)
            if getattr(self, 'opener_burst_now', False):
                # 【这里是启动轴交给循环轴的接口】(用户 2026-08-14: "小爱R2结束
                # 没有按e接上")。V4 的收尾在 R2 之后是【连发 E 进机甲】
                # (burst_tail 的最后一步), 那一下就是循环轴的入口 —— 循环轴第一段
                # 就是爱弥斯在机甲形态下打 A。V3 自己的尾巴没有这一步, 走的是
                # "补个E + 打两秒普攻 + 切人", 于是循环永远接不上。
                # 【也不能 switch_next_char】: 切走了机甲那段就没人打了
                if send_lib2:
                    self.spam_e_through_animation(macro, tag='mechaE')
                self.record_liberation(True)
                self.logger.info(f'aemeath opener burst: lib1={send_lib1} '
                                 f'lib2={send_lib2} total {time.time() - cycle_start:.1f}s '
                                 f'-> handing over to the loop')
            else:
                self.click_resonance()
                self.continues_normal_attack(2)
                self.send_resonance_key()
                self.logger.info(f'aemeath fallback burst: lib1={send_lib1} '
                                 f'lib2={send_lib2} total {time.time() - cycle_start:.1f}s')
                self.switch_next_char()
        else:
            # 打一套普攻切走
            self.continues_normal_attack(2)
            self.switch_next_char()

    def cast_e2_into_heavy(self, macro):
        """最后一发强化E -> 重击。

        【必须用 click_resonance 等演出结束再判重击】。2026-08-12 试过两条捷径,
        都失败, 别再走:
          1. 发完 E 就按住攻击键预输入重击 -> 一直出普攻。高亮没亮时按住不是
             无效输入, 是有效的普攻输入(跟大招演出期间的预输入不是一回事)。
          2. 发完 E 立刻等高亮 -> 日志 wait 0.00s / highlight=True / heavy 0.04s:
             演出期间 HUD 是乱的, 高亮先读到 True 下一帧又 False, 于是按住 0.04 秒
             就松手 —— 那就是一次点击, 一下平A, 重击根本没出。
        send_click=False 只是去掉它自带的那下普攻, 等演出这件事必须留着。
        """
        t0 = time.time()
        sent = self.send_e2_verified()
        # 【发完 E 立刻按住, 一路按穿整个演出】(用户 2026-08-12: 正常操作就是在
        # 强化E 释放的同时长按攻击键, 演出一结束重击就接上, 再进 R2)。
        #
        # 上一次试预输入失败是因为只按住了 1.20 秒, 而 E 的演出实测 3.08 秒 ——
        # 整段按住都埋在演出里, 演出还没完就松手了, 重击自然接不上。
        # 【lit_time_out 传 None, 不许提前放弃】: 实测重击高亮要到按住后 2.96 秒
        # 才亮起, 按 1.20 连高亮都等不到, 按 2.80 差 0.16 秒正好在亮起前松手,
        # 全程只有平A。中途放弃等于前功尽弃
        fired = self.hold_until_heavy_fired(macro, self.E2_ANIM_HOLD, 'E2heavy',
                                            min_hold=self.E2_HEAVY_MIN_HOLD,
                                            spam_lib=self.HEAVY_SPAM_LIB)
        self.logger.info(f'aemeath burst: E2 sent={sent} fired={fired} '
                         f'(total {time.time() - t0:.2f}s)')

    def send_e2_verified(self, time_out=1.5, interval=0.12):
        """把强化E 发出去并确认它真的放出来了(图标灭掉)。

        不用 click_resonance: 它一路阻塞到演出结束才返回, 而我们要在演出【期间】
        就把攻击键按下去。发出去和放出来是两回事, 所以要重发到图标灭为止。
        """
        start = time.time()
        n = 0
        max_presses = int(time_out / max(interval, 0.01)) + 2
        while time.time() - start < time_out and n < max_presses:
            if not self.enhance_e_available():
                return True
            self.send_resonance_key()
            self.record_enhance_e()
            n += 1
            self.sleep(interval, check_combat=False)
        self.logger.warning(f'aemeath burst: E2 icon still lit after {n} presses')
        return False

    def fire_r2_fast(self, macro=None):
        return self.fire_lib_fast(macro, 'R2', self.CYCLE_LIB2_TIME_OUT)

    def fire_lib_fast(self, macro, tag, time_out):
        """不停发大招键, 直到演出开始(队伍 HUD 消失)。R1 R2 共用。

        【只发键, 一下普攻都不补】。spin_until(click_liberation) 那种写法每轮补一下
        普攻再睡 0.1 秒, 两个毛病:
          - 那下普攻会顶掉正在演的动作 —— R1 要落在 A4 的后撤动作途中(用户 2026-08-13),
            R2 要接在重击的收尾上(用户 2026-08-13), 两处都被它顶掉过;
          - 0.1 秒一轮太粗, 卡不进这种动画窗口。
        """
        if macro is not None:
            macro.macro_mark(tag)
        start = time.time()
        n = 0
        while self.time_elapsed_accounting_for_freeze(start) < time_out:
            self.task.next_frame()
            if not self.task.in_team()[0]:
                self.record_liberation_use()
                if macro is not None:
                    macro.macro_mark(f'{tag}_fired(x{n})')
                self.logger.info(f'aemeath burst: {tag} fired after {n} presses '
                                 f'({time.time() - start:.2f}s)')
                return True
            self.send_liberation_key()
            n += 1
            self.sleep(self.R2_KEY_INTERVAL, check_combat=False)
        if macro is not None:
            macro.macro_mark(f'{tag}_FAIL(x{n})')
        self.logger.warning(f'aemeath burst: {tag} never fired after {n} presses')
        return False

    def spin_until(self, cond, time_out, name):
        """边普攻边等条件成立。返回条件是否成立。

        每一步都过 self.sleep(), 战斗判定才有机会跑; 原来那三个裸 while 都没有。
        """
        # 【每帧查条件, 点击照旧 0.12 秒一次】(用户 2026-08-14: "第二个强化E亮了
        # 没有第一时间放")。原来是"点一下 -> 睡 0.1 -> 再查", 强化E 在那 0.1 秒
        # 中间亮起的话要等下一轮才发现, 加上点击本身的开销最坏晚 0.2 秒以上。
        # 拆开之后【出手频率没变, 只是发现得更早】
        start = time.time()
        next_click = 0.0
        while self.time_elapsed_accounting_for_freeze(start) < time_out:
            if cond():
                return True
            now = time.time()
            if now >= next_click:
                self.click()
                next_click = now + self.SPIN_CLICK_INTERVAL
            self.task.next_frame()
        self.logger.warning(f'aemeath burst: {name} not ready after {time_out:.1f}s, moving on')
        return False

    def lib_cd_eminent(self):
        cd = self.task.get_cd('liberation')
        return self.lib1_unlocked() and (0 < cd < 1.5 or self.liberation_available())

    def enhance_e_available(self):
        return self.task.find_one('aemeath_e1', threshold=0.7) or self.task.find_one('aemeath_e2',
                                                                                     threshold=0.7)

    def heavy_wait_highlight_down(self):
        self.task.mouse_down()
        ret = self.task.wait_until(lambda: not self.has_long_action(), time_out=1.2)
        self.task.mouse_up()
        self.sleep(0.01)
        return ret

    def handle_heavy(self):
        if not self.has_long_action():
            return False
        prepares_lib2 = self.preparing_lib2()
        if self.heavy_wait_highlight_down():
            if prepares_lib2:
                self.record_heavy_liberation()
            return True
        return False

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if self.is_in_denia_cycle:
            # 启动轴待跑时爱弥斯不该被选上场 —— 轴的第一个动作是千咲的 E。
            # (框架还有千咲这个候选, 不会因为这里返回 NO 就原地打转)
            macro = self.burst_macro()
            if macro is not None and hasattr(macro, 'opener_pending') and macro.opener_pending():
                return SwitchPriority.NO
            return super().get_switch_priority(current_char, has_intro, target_low_con)
        if self.is_in_25s_cycle:
            if current_char and current_char.char_name in LINNAI_NAMES:
                return SwitchPriority.MUST
            else:
                return SwitchPriority.NO
        elif self.should_wait_for_lib2():
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.switch_other_char()
