import time

from src.char.BaseChar import BaseChar, SwitchPriority


class Verina(BaseChar):
    """
    Verina 自动战斗(辅助/治疗)角色类。

    战斗循环: 重复执行 3A -> 大招 -> E -> 声骸 -> (重击) -> 跳跃 -> 3A
    直到协奏值满才切换角色，无站场时间限制。
    """

    # 普通攻击单次耗时（秒）
    NORMAL_ATTACK_TIME: float = 0.6
    # 跳跃攻击单次耗时（秒）
    JUMP_ATTACK_TIME: float = 0.5
    # 重击单次耗时（秒）
    HEAVY_ATTACK_TIME: float = 0.7
    # 技能后摇恢复等待时间（秒）
    RECOVER_TIME: float = 0.8
    # 重击最小间隔（秒），防止频繁重击
    HEAVY_ATTACK_INTERVAL: float = 8.0

    def __init__(self, *args, **kwargs):
        """初始化 Verina 角色实例。"""
        super().__init__(*args, **kwargs)
        # 记录上次重击的时间戳，用于冷却判断
        self.last_heavy = -1

    def do_perform(self):
        """
        执行当前角色的主要战斗行动序列。
        由 BaseChar.perform() 调用，子类应重写此方法。

        重复执行战斗连招，直到协奏值满，然后切换至下一个角色。
        """
        # 循环执行连招，直到协奏值满（should_stop 返回 True）
        while not self.should_stop():
            self.perform_combat()
        # 协奏已满，切换角色
        self.switch_next_char()

    def perform_combat(self):
        """
        执行一次完整的 Verina 战斗循环。

        流程: 3A -> 大招 -> E -> 声骸 -> (重击) -> 跳跃 -> 3A
        每步前检查协奏值是否已满，若是则提前结束并返回（由外部循环处理切人）。
        """
        # 1. 连续进行3次普通攻击（3A）
        self.continues_normal_attack(self.NORMAL_ATTACK_TIME)

        # 2. 依次释放 大招 -> E技能 -> 声骸技能
        #    每步执行前检查是否应提前停止（协奏满）
        for cast_skill in (self.cast_liberation, self.cast_resonance, self.cast_echo):
            # 若协奏值已满，则提前结束连招（外部循环会检测并切人）
            if self.should_stop():
                return
            # 释放当前技能（大招/E/声骸）
            cast_skill()

        # 再次检查是否应提前停止
        if self.should_stop():
            return

        # 等待角色切换动画完成，确保角色已稳定在场上
        # 超时时间2秒，若超时则继续执行
        self.task.wait_until(lambda: self.task.in_team()[0], time_out=2.0)
        # 等待技能后摇恢复
        self.sleep(self.RECOVER_TIME)

        # 3. 检查协奏值是否已满且重击可用（冷却已过）
        #    若满足条件则执行重击
        if self.is_mouse_forte_full() and self.can_heavy_attack():
            self.heavy_attack(self.HEAVY_ATTACK_TIME)
            # 更新上次重击时间戳
            self.last_heavy = time.time()

        # 4. 执行跳跃攻击（跳跃后接3次普通攻击）
        self.task.jump(after_sleep=0.01)
        # 连续进行3次空中普通攻击（3A）
        for _ in range(3):
            # 每次攻击前检查协奏是否已满
            if self.should_stop():
                return
            # 使用 continues_normal_attack 执行单次攻击，传入单次耗时
            self.continues_normal_attack(self.JUMP_ATTACK_TIME)

    def cast_resonance(self):
        """释放共鸣技能（E技能）：若可用则点击释放。"""
        if self.resonance_available():
            # 点击共鸣技能按钮，send_click=True 表示发送点击事件
            self.click_resonance(send_click=True, time_out=0)

    def cast_liberation(self):
        """释放共鸣解放（大招）：若可用则点击释放。"""
        if self.liberation_available():
            self.click_liberation()

    def cast_echo(self):
        """释放声骸技能：若可用则点击释放。"""
        if self.echo_available():
            self.click_echo(time_out=0)

    def should_stop(self):
        """
        判断连招是否应提前结束并切换角色。

        结束条件：协奏值已满（攒够入场技）
        无站场时间限制。
        """
        return self.is_con_full()

    def can_heavy_attack(self):
        """距上次重击是否已超过最小间隔(扣除冻结时间)。"""
        return self.time_elapsed_accounting_for_freeze(self.last_heavy) >= self.HEAVY_ATTACK_INTERVAL

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """
        获取当前角色切换优先级。

        若当前角色是 'char_hiyuki' 且拥有入场技，则强制切换（MUST）。
        否则调用父类默认优先级逻辑。
        """
        if has_intro and current_char and current_char.char_name in {'char_hiyuki'}:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)