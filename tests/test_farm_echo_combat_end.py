"""刷4C「画面里有声骸就算打完」的判定(不起游戏、不起界面)。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_farm_echo_combat_end.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.task.FarmEchoTask import FarmEchoTask


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


def make_task(target=False, health=False, boss_bar=False, echo=False, retarget=False):
    """不走 __init__ (那要执行器和截图), 只摆出判定用得到的属性。已经在战斗里。"""
    task = object.__new__(FarmEchoTask)
    task.__dict__.update(
        config={'Auto Target': True}, scene=FakeScene(), _in_combat=True, _in_liberation=False,
        _abyss_force_combat_until=0, _abyss_combat_active=False, _f_grace_since=-1.0, target_enemy_time_out=3,
        target=target, health=health, boss_bar=boss_bar, echo=echo, retarget=retarget, logs=[],
    )
    task.combat_end_condition = lambda: task.echo
    task.find_target = lambda double_check=False: task.target
    task.check_health_bar = lambda: task.health or task.boss_bar
    task.has_boss_health_bar = lambda: task.boss_bar
    task.target_enemy = lambda *a, **k: task.retarget
    task.find_one = lambda *a, **k: None
    task.check_f_break = lambda: None
    task.get_current_char = lambda *a, **k: None
    task.on_combat_check = lambda: True
    task.should_check_monthly_card = lambda: False
    task.log_info = lambda message, *a, **k: task.logs.append(message)
    return task


class EndConditionTest(unittest.TestCase):
    def test_echo_needs_a_second_look(self):
        task = make_task(echo=True)
        self.assertTrue(task.in_combat(), '第一次看到声骸先不收手')
        task._end_seen_since -= 1
        self.assertFalse(task.in_combat(), '隔一次判定还在才算打完')
        self.assertEqual(task.out_of_combat_reason, 'end condition reached')
        self.assertEqual(task._end_seen_since, 0.0)

    def test_one_frame_echo_is_forgotten(self):
        task = make_task(echo=True, retarget=True)
        task.in_combat()
        task.echo = False
        self.assertTrue(task.in_combat(), '误报的那一帧过去了, 照常找目标')
        self.assertEqual(task._end_seen_since, 0.0)

    def test_target_back_cancels_confirm(self):
        task = make_task(echo=True)
        task.in_combat()
        task.target = True
        self.assertTrue(task.in_combat())
        self.assertEqual(task._end_seen_since, 0.0)

    def test_echo_with_boss_bar_keeps_fighting(self):
        task = make_task(echo=True, boss_bar=True, retarget=True)
        task._end_seen_since = 1.0  # 就算之前看到过, Boss 还有血也不收手
        self.assertTrue(task.in_combat())
        self.assertTrue(task._in_combat)


if __name__ == '__main__':
    unittest.main(verbosity=1)
