# -*- coding: utf-8 -*-
"""
卡夏轴夏空 (Ciaccona) 适配器。
主控卡提希娅单核心架构：当夏空意外留场或被换入时，立即切回卡提希娅。
"""
import time

from src.char.BaseChar import SwitchPriority
from src.char.Ciaccona import Ciaccona as NativeCiaccona


class Ciaccona(NativeCiaccona):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.macro_yield_logged = 0.0

    @property
    def is_healer(self):
        """声明非治疗位，防止 AutoCombatTask 开局强切夏空"""
        try:
            if self.is_in_kxq_cycle:
                return False
        except Exception:
            pass
        return super().is_healer

    @property
    def is_in_kxq_cycle(self) -> bool:
        """只要队内有卡提希娅(Cartethyia)，即判定属于卡夏体系"""
        for char in self.task.chars:
            if char is not None and char.name == 'Cartethyia':
                return True
        return False

    def macro(self):
        """获取单核卡提主控实例"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'quick_switch') or hasattr(char, 'hand_to_cart'):
                return char
        return None

    def do_perform(self):
        if not self.is_in_kxq_cycle:
            return super().do_perform()

        self.check_f_on_switch = False
        macro = self.macro()
        if macro is None:
            return super().do_perform()

        now = time.time()
        if now - self.macro_yield_logged > 3:
            self.macro_yield_logged = now
            self.logger.info('[夏空] 当前在前台，立即确认切回卡提希娅')

        if hasattr(macro, 'quick_switch'):
            macro.quick_switch(macro, time_out=2.5)
            return
        if hasattr(macro, 'hand_to_cart') and macro.hand_to_cart(self):
            return
        return self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """让选人一律优先给卡提希娅"""
        if self.is_in_kxq_cycle and self.macro() is not None:
            return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
