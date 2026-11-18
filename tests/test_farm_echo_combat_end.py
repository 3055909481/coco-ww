"""刷4C「画面里有声骸就算打完」的判定(不起游戏、不起界面)。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_farm_echo_combat_end.py
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ok import Box
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
    task.boss_still_alive = lambda: task.boss_bar
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


class FrameTask(FarmEchoTask):
    frame = None  # 盖掉基类从执行器取帧的属性


W, H = 2560, 1440


def frame_with_boss_fill(width_px, pieces=1):
    """2K 黑屏, Boss 血条位置画 pieces 段宽 width_px 的血色。"""
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    y0 = int(H * 100 / 2160)
    x0 = int(W * 1300 / 3840)
    for i in range(pieces):
        x = x0 + i * (width_px + 6)
        frame[y0:y0 + 10, x:x + width_px] = (40, 120, 250)  # BGR, 落在 boss_health_color 里
    return frame


def make_frame_task(frame, boss_icon=False):
    task = object.__new__(FrameTask)
    task.__dict__.update(_in_combat=True, boss_health_box=None, boss_health=None)
    task.frame = frame
    task.width_of_screen = lambda r: int(r * W)
    task.height_of_screen = lambda r: int(r * H)
    task.boss_health_area = lambda: Box(int(W * 1269 / 3840), int(H * 58 / 2160),
                                        to_x=int(W * 2533 / 3840), to_y=int(H * 200 / 2160))
    task.is_boss = lambda: boss_icon
    task.draw_boxes = lambda *a, **k: None
    return task


class BossStillAliveTest(unittest.TestCase):
    def test_sliver_counts_as_alive(self):
        task = make_frame_task(frame_with_boss_fill(4))
        self.assertFalse(task.has_boss_health_bar(), '残血 4 像素, 原来的血条判断认不到')
        self.assertTrue(task.boss_still_alive())

    def test_split_fill_counts_as_alive(self):
        task = make_frame_task(frame_with_boss_fill(30, pieces=2))
        self.assertFalse(task.has_boss_health_bar(), '被切成两段, 原来要求恰好一块')
        self.assertTrue(task.boss_still_alive())

    def test_empty_bar_is_dead(self):
        self.assertFalse(make_frame_task(frame_with_boss_fill(0, pieces=0)).boss_still_alive())

    def test_break_icon_counts_as_alive(self):
        self.assertTrue(make_frame_task(frame_with_boss_fill(0, pieces=0), boss_icon=True).boss_still_alive())


if __name__ == '__main__':
    unittest.main(verbosity=1)
