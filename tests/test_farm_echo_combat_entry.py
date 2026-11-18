"""刷4C 打完那一阵的进/出战斗判定(不起游戏、不起界面)。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_farm_echo_combat_entry.py
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.task.AutoCombatTask import AutoCombatTask
from src.task.FarmEchoTask import FarmEchoTask, POST_COMBAT_HEALTH_ONLY_SECONDS


class FakeScene:
    def __init__(self):
        self.combat = None

    def in_combat(self):
        return None

    def set_in_combat(self):
        self.combat = True
        return True

    def set_not_in_combat(self):
        self.combat = False
        return False

    def reset_abyss_start(self):
        pass


def make_task(cls=FarmEchoTask, in_combat=False, target=False, health=False, boss_bar=False, echo=False,
              retarget=False):
    """不走 __init__ (那要执行器和截图), 只摆出判定用得到的属性。"""
    task = object.__new__(cls)
    task.__dict__.update(
        config={'Auto Target': True}, scene=FakeScene(), _in_combat=in_combat, _in_liberation=False,
        _health_only_until=0.0, _abyss_force_combat_until=0, _abyss_combat_active=False,
        _f_grace_since=-1.0, _f_consumed=float('inf'), combat_end_condition=lambda: echo,
        target=target, health=health, boss_bar=boss_bar, retarget=retarget, logs=[],
    )
    task.find_target = lambda double_check=False: task.target
    task.has_health_bar = lambda: task.health or task.boss_bar
    task.check_health_bar = lambda: task.health or task.boss_bar
    task.has_boss_health_bar = lambda: task.boss_bar
    task.target_enemy = lambda *a, **k: task.retarget
    task.load_chars = lambda: True
    task.middle_click = lambda *a, **k: None
    task.find_one = lambda *a, **k: None
    task.check_f_break = lambda: None
    task.get_current_char = lambda *a, **k: None
    task.on_combat_check = lambda: True
    task.should_check_monthly_card = lambda: False
    task.last_f_press = lambda: -1.0
    task.find_f_prompt = lambda: None
    task.tr = lambda s: s
    task.log_info = lambda message, *a, **k: task.logs.append(message)
    task.log_debug = lambda *a, **k: None
    return task


class EnterAfterKillTest(unittest.TestCase):
    def test_lock_without_health_bar_does_not_reenter(self):
        task = make_task(target=True)
        task._health_only_until = time.time() + POST_COMBAT_HEALTH_ONLY_SECONDS
        self.assertFalse(task.in_combat(target=True), '中键锁到倒地的 Boss 不算新一场')
        self.assertFalse(task._in_combat)

    def test_health_bar_reenters(self):
        task = make_task(target=True, boss_bar=True)
        task._health_only_until = time.time() + POST_COMBAT_HEALTH_ONLY_SECONDS
        self.assertTrue(task.in_combat(target=True), 'Boss 其实没死/新怪上来, 有血条照样进')

    def test_window_over_lock_counts_again(self):
        task = make_task(target=True)
        task._health_only_until = time.time() - 1
        self.assertTrue(task.in_combat(target=True))

    def test_auto_combat_unchanged(self):
        task = make_task(cls=AutoCombatTask, target=True)
        self.assertTrue(task.in_combat(target=True), '自动战斗照旧: 锁到目标就进')


class EndConditionTest(unittest.TestCase):
    def test_echo_without_boss_bar_ends(self):
        task = make_task(in_combat=True, echo=True)
        self.assertFalse(task.in_combat())
        self.assertEqual(task.out_of_combat_reason, 'end condition reached')

    def test_echo_with_boss_bar_keeps_fighting(self):
        task = make_task(in_combat=True, echo=True, boss_bar=True, retarget=True)
        self.assertTrue(task.in_combat(), 'Boss 还有血, 地上有声骸也不收手')
        self.assertTrue(task._in_combat)


if __name__ == '__main__':
    unittest.main(verbosity=1)
