import time

from src.char.BaseChar import CharType, SwitchPriority, get_default_buff_time
from src.char.Chisa import Chisa as NativeChisa


class Chisa(NativeChisa):
    """千咲。她现在同时出现在两条队伍轴里, do_perform 按队伍分流:

      1) 爱达千(爱弥斯/达尼娅/千咲) —— 【轴的入口是她】。整条轴(启动 + 循环)是一条
         跨三人的线性宏, 住在 Denia.py 里(自定义角色文件互相 import 不到, 只能靠
         task.chars 拿到彼此的实例)。这里只负责把启动轴踢起来, 见 denia_macro()。
         千咲在 CharFactory 里登记的是 HEALER, 所以 AutoCombatTask.run() 开头那次
         switch_healer() 正好会把她摆上场 —— 跟这条轴的入口是一致的, 不用关掉它。

      2) 穗千秧(穗穗/千咲/秧秧玄翎) —— 下面那条 do_team_axis, 原样保留。

    【继承的是原版 src.char.Chisa, 不是 BaseChar】(2026-08-16 改): 原来这个文件是
    8-03 的一份过期拷贝, 连 do_dps_perform / perform_forte 都是照抄的 —— 上游后来
    改了就吃不到, 而且兜底的 super().do_perform() 会掉进 BaseChar 那套通用轮换。
    下面那两个同名方法留着不删(值可能被调过), 想吃上游版本直接删掉即可。

    ================= 穗千秧的轴 (do_team_axis) =================
        Q → E → A(2.6s) → R → 强化E → 一直 a 到能量条消失(3.5s) → 切秧秧
        (R 之后的 F 处决试过, 会吞掉强化E, 已用 F_BREAK_ON_LIB 关掉)

    原来的 do_fast_support() 还留在下面: 那是"进场就走"的极速辅助版, 穗千秧不用它,
    因为这里要千咲实打实站几秒把 R 和强化E 打出来。想换回去改 do_perform() 一行。
    Chisa DPS 配置项还在, 勾上就还是走原来的主C轴。
    """

    # ================================================================ 固定顺序切人 (公共段)
    # 这一段在 Suisui.py / Chisa.py / YangYangSp.py 三个文件里逐字一样, 改一处记得同步改三处。
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
        target.last_switch_in_time = time.time()
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

    # ============================================================ 公共段结束


    NEXT_SLOT = 3  # 打完切秧秧玄翎

    # ---- 本队的固定轴 ----
    ECHO_TO_E: float = 0.35        # Q 之后到按 E 的间隔 (秒)
    E_TO_ATTACK: float = 0.25      # E 之后到起手普攻的间隔 (秒)
    # E 之后到 R 之前的普攻时间 (秒)。原来是写死 4 段 ≈ 1.1s, 实战太短, 改成按秒算
    AXIS_A_TIME: float = 2.6
    AXIS_A_INTERVAL: float = 0.28  # 每次点击的间隔 (秒)
    # R 之后的固定缓冲 (秒)。给 0: 现在改成盯着 E 图标亮没亮 (wait_e_lit), 固定等
    # 只会让强化E 白白晚按
    POST_LIB_SETTLE: float = 0.0
    # 间隙里点普攻的间隔 (秒)。R 之后这一串缓冲原来全是干等, 人站着发愣近 1.2 秒,
    # 换成边等边点, 时长不变但手不停
    GAP_ATTACK_INTERVAL: float = 0.22
    # ---- R 之后的 F 处决 ----
    # R 之后按 F 处决 —— 已停用。击破动画的全局时停会把后面的强化E 吞掉, 为了躲它加的
    # 1.5s 缓冲又把整条轴拖长, 收益不抵成本。代码留着, 想再试改回 True
    F_BREAK_ON_LIB: bool = False
    # 按完 F 之后的缓冲 (秒), 不分处决没处决都留。
    # 试过自己找图判断处决到没到, 结果是判错的 —— 日志打 "no break prompt" 那一轮,
    # 场上其实处决了 (f_break() 内部自己那次找图是准的, 我们外面那次不准)。
    # 判错的代价很大: 以为没处决就不等, 强化E 和后面的 aaa 全被击破动画的时停吞掉。
    # 现在不判了, 这段还是边等边点普攻: 真处决了点击被时停吃掉、无害;
    # 没处决就白赚 1.5 秒输出。两种情况都不亏
    F_BREAK_WAIT: float = 1.5
    # 强化E: 0 = 短按共鸣键 (默认)。有些形态要长按才出强化段, 那就调成 1.2 走长按
    ENHANCED_E_HOLD: float = 0.0

    # ---- 强化E 触发: 读 E 图标的白像素占比, 亮了立刻按 (跟秧秧玄翎同一套) ----
    # 框架判图标亮不亮走的是像素占比 (box_highlighted → calculate_color_percentage),
    # resonance_available() 主动传 check_color=False 把这段跳过了, 所以自己读
    E_LIT_BOX: str = 'box_resonance'
    E_LIT_COLOR = {'r': (200, 255), 'g': (200, 255), 'b': (200, 255)}
    # 0.13, 跟秧秧同一个值。千咲实测亮起时 0.1619, 落在秧秧标定的亮起区间 0.158~0.211 内
    E_LIT_THRESHOLD: float = 0.13
    E_LIT_CONFIRM: int = 2         # 连续这么多次过阈值才认, 挡单点噪声
    E_LIT_POLL: float = 0.03       # 轮询间隔 (秒)
    E_LIT_MAX_WAIT: float = 1.2    # 等图标亮的上限 (秒), 到点没亮也照按
    # 采样打日志。已标定完关掉 —— 00:23 实测 R 结束那一刻 white=0.1619 就已经过阈,
    # 0.07s 按下强化E。要重新标定 (换角色/换界面) 再打开
    E_LIT_PROBE: bool = False
    E_LIT_PROBE_INTERVAL: float = 0.15
    ENHANCED_E_WAIT: float = 0.5   # 强化E 的演出时间 (秒), 这段也是边等边点普攻
    # 强化状态下一直点普攻的时间 (秒)。框架只有 is_forte_full(), 读不到"还剩多少",
    # 所以能量条见底这件事只能用时间兜底 —— 录一遍自己的实战, 数到条空为止再改这个值
    FORTE_DRAIN_TIME: float = 3.5
    FORTE_DRAIN_INTERVAL: float = 0.22
    DRAIN_LAST_INTERVAL: float = 0.15  # 最后一下之后到按切人键的间隔 (秒)

    # ------------------------------------------------------------------ 路由

    def is_dps_config(self):
        return self.task and self.task.char_config.get("Chisa DPS")

    def get_char_type(self):
        if self.is_dps_config():
            return CharType.MAIN_DPS
        return super().get_char_type()

    def get_buff_time(self):
        if self.is_dps_config():
            return get_default_buff_time(CharType.MAIN_DPS)
        return super().get_buff_time()

    # ---------------------------------------------------------- 爱达千 (轴在 Denia.py)

    @property
    def is_in_denia_cycle(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Aemeath', 'Denia'}:
                team_members += 1
        return team_members == 2

    def denia_macro(self):
        """拿到持宏的达尼娅实例。

        【按能力找, 不按名字找】: 达尼娅那份自定义没启用的话这里就该返回 None,
        然后老老实实走原版轮换 —— 而不是拿着一个没有 perform_opener 的实例崩掉。
        """
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_opener') and hasattr(char, 'quick_switch'):
                return char
        return None

    def do_perform(self):
        if self.is_in_denia_cycle:
            macro = self.denia_macro()
            if macro is not None:
                if macro.opener_pending():
                    # 【标记要在跑之前落】: perform_opener 中途抛异常(脱战/任务被停)
                    # 也不能让启动轴重跑 —— reset_state 会把 armed 重新置回来
                    macro.opener_armed = False
                    macro.last_opener = time.time()
                    if macro.perform_opener(self):
                        return
                    self.logger.warning('denia opener aborted, falling back to native rotation')
                else:
                    # 启动轴跑过了: 循环轴由爱弥斯驱动。走到这里说明宏散了
                    # (切人失败/脱战重进), 打一小段把场子交给她重新起跑
                    self.logger.info('chisa: denia opener already ran, handing the field back')
                    self.continues_normal_attack(0.8)
                    return self.switch_next_char()
            else:
                self.logger.warning('denia cycle: Denia custom code is off, '
                                    'chisa falls back to the native rotation')
            return super().do_perform()

        if self.is_dps_config():
            return self.do_dps_perform()
        return self.do_team_axis()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """爱达千的启动轴待跑时千咲【必须】先上场 —— 轴的第一个动作是她的 E。

        正常路径靠 switch_healer() 就把她摆上来了(她登记的是 HEALER), 这里是
        第二道保险: 玩家把"Switch to Healer"关掉时框架就不会主动选她。
        """
        if self.is_in_denia_cycle:
            macro = self.denia_macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    # ------------------------------------------------------------------ 本队的轴

    def do_team_axis(self):
        """Q → E → A(AXIS_A_TIME) → R → 强化E → a 到能量条空 → 切秧秧。"""
        self.check_f_on_switch = True
        if self.flying():
            self.wait_down()
        intro = self.take_intro()
        self.logger.info(f'chisa axis start (intro={intro})')

        self.click_echo(time_out=0)  # Q 声骸
        self.sleep(self.ECHO_TO_E)

        if self.click_resonance(time_out=0.5)[0]:  # E
            self.record_support_buff()
        self.sleep(self.E_TO_ATTACK)

        # A: 打满 AXIS_A_TIME 秒, 不再按段数写死 —— 段数打完会自动从第1段接上, 不会卡住
        self.continues_normal_attack(self.AXIS_A_TIME, interval=self.AXIS_A_INTERVAL)

        if self.click_liberation(wait_if_cd_ready=0.3):  # R
            self.record_support_buff()
            self.gap_attack(self.POST_LIB_SETTLE)
        else:
            self.logger.info('chisa: liberation not ready, going straight to enhanced E')

        self.execute_break()       # F 处决 —— 默认关闭, 直接返回
        self.enhanced_resonance()  # 强化E
        self.drain_forte()         # 一直 a 到能量条消失

        # 直接按数字键切秧秧 —— 走 switch_next_char() 会多打几下 A, 把节奏拖乱
        if self.direct_switch():
            return
        self.switch_next_char()

    def execute_break(self):
        """R 之后按 F 处决, 然后不管处决没处决都留一段缓冲。

        f_break() 是找图触发的 —— 没有 F 提示它根本不会按, 所以无脑按没有副作用,
        也就不需要我们自己再判一次(而且判也判不准: 日志打 "no break prompt" 那一轮,
        场上其实处决了)。缓冲的意义是别让强化E 撞进击破动画的全局时停里。
        """
        if not self.F_BREAK_ON_LIB:
            return False
        self.f_break()
        self.logger.info(f'chisa: f break sent, +{self.F_BREAK_WAIT:.1f}s buffer before enhanced E')
        self.gap_attack(self.F_BREAK_WAIT)
        return True

    def gap_attack(self, duration):
        """间隙里保持点普攻, 别让她站着发愣。"""
        if duration > 0:
            self.continues_normal_attack(duration, interval=self.GAP_ATTACK_INTERVAL)

    def enhanced_resonance(self):
        """R 之后共鸣键会亮成强化E —— 等图标真亮了立刻按, 不再固定等一个死时间。"""
        waited = self.wait_e_lit()
        if self.ENHANCED_E_HOLD > 0:
            self.task.send_key(self.get_resonance_key(), down_time=self.ENHANCED_E_HOLD)
        else:
            self.send_resonance_key()
        self.logger.info(f'chisa enhanced E cast (waited {waited:.2f}s for icon)')
        self.gap_attack(self.ENHANCED_E_WAIT)

    def wait_e_lit(self):
        """等 E 图标变成强化高亮。期间保持点普攻, 不干等。

        返回等了多久 (秒)。超时也返回, 由调用方照样按 —— 识别失灵不该把轴卡死。
        """
        start = time.time()
        hits = 0
        last_click = -1.0
        last_probe = -1.0
        while True:
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if elapsed >= self.E_LIT_MAX_WAIT:
                self.logger.info(f'chisa: e icon not lit in {self.E_LIT_MAX_WAIT:.1f}s, '
                                 f'pressing anyway')
                return elapsed

            if elapsed - last_click >= self.GAP_ATTACK_INTERVAL:
                last_click = elapsed
                self.click()

            white = self.e_icon_white()
            if self.E_LIT_PROBE and elapsed - last_probe >= self.E_LIT_PROBE_INTERVAL:
                last_probe = elapsed
                self.logger.info(f'chisa probe t={elapsed:.2f}s white={white:.4f}')

            hits = hits + 1 if white >= self.E_LIT_THRESHOLD else 0
            if hits >= self.E_LIT_CONFIRM:
                return elapsed
            self.sleep(self.E_LIT_POLL)

    def e_icon_white(self):
        """E 技能图标框里的白像素占比。读不到时返回 -1, 不让它把轴卡住。"""
        try:
            box = self.task.get_box_by_name(self.E_LIT_BOX)
            return self.task.calculate_color_percentage(self.E_LIT_COLOR, box)
        except Exception as e:
            self.logger.info(f'chisa: e icon probe failed ({e})')
            return -1

    def drain_forte(self):
        """强化状态下一直点普攻, 把能量条打空。

        用点按不用长按: 长按走的是重击, 强化普攻的段数吃不满。
        中途协奏满了也不停 —— 能量条没打完就走等于把强化段丢在场上。
        """
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.FORTE_DRAIN_TIME:
            self.click()
            left = self.FORTE_DRAIN_TIME - self.time_elapsed_accounting_for_freeze(start)
            if left <= self.FORTE_DRAIN_INTERVAL:
                self.sleep(self.DRAIN_LAST_INTERVAL)
                break
            self.sleep(self.FORTE_DRAIN_INTERVAL)
        self.logger.info('chisa forte drained')

    def on_combat_end(self, chars):
        self.reset_fixed_rotation()
        super().on_combat_end(chars)

    def record_support_buff(self):
        """记下变奏技能 / 共鸣解放给出的增益时间, 供框架的 buff 判定用。"""
        self.last_buff_time = time.time()

    def switch_out(self, con_full=False):
        support_buff_time = self.last_buff_time
        super().switch_out(con_full=con_full)
        if not self.is_dps_config():
            # 辅助形态下的增益是离场后还在的, 别被 switch_out 清掉
            self.last_buff_time = support_buff_time

    # ------------------------------------------------------------------ 旧路径 (本队不用)

    def do_fast_support(self):
        """极速辅助版: 进场就 Q/R 然后立刻走人。本队不用, 留着备查。"""
        self.check_f_on_switch = True
        if self.has_intro:
            self.record_support_buff()
            self.click_echo(time_out=0)
            return self.switch_next_char()

        if self.flying() and not self.liberation_available() and not self.resonance_available():
            self.wait_down()
        self.click_echo(time_out=0)
        if self.click_liberation():
            self.record_support_buff()
            return self.switch_next_char()

        self.click_resonance(time_out=0.5)
        return self.switch_next_char()

    def do_dps_perform(self):
        timeout = 2.5
        self.check_f_on_switch = True
        if self.has_intro:
            self.continues_normal_attack(0.8)
            timeout = 2.3
        if self.flying() and not self.liberation_available() and not self.resonance_available():
            self.wait_down()
        self.click_echo()
        start = time.time()
        under_liber = False
        while time.time() - start < timeout:
            if time.time() - start < 0.5 and self.click_liberation():
                start = time.time()
                under_liber = True
                timeout = 10
                self.sleep(0.2)
            if time.time() - start < 0.5 and not self.is_forte_full() and self.click_resonance()[0]:
                start = time.time()
                if timeout != 10:
                    timeout = 1.7
            if (under_liber or self.is_dps_config()) and self.is_forte_full() and self.perform_forte():
                self.check_f_on_switch = False
                return self.switch_next_char()
            self.click()
            self.check_combat()
            self.task.next_frame()
        self.switch_next_char()

    def perform_forte(self):
        if self.flying():
            self.wait_down()
        self.task.send_key(self.get_resonance_key(), down_time=1.2)
        if self.is_forte_full():
            return False
        self.heavy_attack(3.5)
        return True