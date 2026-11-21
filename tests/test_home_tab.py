import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from src.gui import HomeTab as home


class CoverRectTest(unittest.TestCase):
    def test_wide_image_is_cropped_left_and_right(self):
        self.assertEqual(home.cover_source_rect(2000, 1000, 1000, 1000), (500, 0, 1000, 1000))

    def test_tall_image_is_cropped_top_and_bottom(self):
        self.assertEqual(home.cover_source_rect(1000, 2000, 1000, 500), (0, 750, 1000, 500))

    def test_same_ratio_uses_whole_image(self):
        self.assertEqual(home.cover_source_rect(1920, 1080, 960, 540), (0, 0, 1920, 1080))

    def test_empty_sizes(self):
        self.assertEqual(home.cover_source_rect(0, 100, 100, 100), (0, 0, 0, 0))


class FadeTopTest(unittest.TestCase):
    def test_starts_above_the_cards(self):
        self.assertEqual(home.fade_top(800, 500, lead=64), 436)

    def test_keeps_at_least_the_bottom_quarter(self):
        self.assertEqual(home.fade_top(800, 790, lead=10), 600)

    def test_never_negative(self):
        self.assertEqual(home.fade_top(800, 20, lead=64), 0)


class Hsin: pass
class Suoming: pass
class Lily: pass


class TeamTest(unittest.TestCase):
    CHAR_INFO = {'char_hsin': {'cls': Hsin}, 'char_suoming': {'cls': Suoming}, 'char_lily': {'cls': Lily}}

    @staticmethod
    def task(*names):
        return SimpleNamespace(chars=[SimpleNamespace(char_name=n) if n else None for n in names])

    def test_team_from_tasks_keeps_slot_order(self):
        tasks = [SimpleNamespace(), self.task('char_lily', 'char_hsin', 'char_suoming')]
        self.assertEqual(home.team_from_tasks(tasks, self.CHAR_INFO), ['Lily', 'Hsin', 'Suoming'])

    def test_team_from_tasks_needs_three_known_members(self):
        tasks = [self.task('char_lily', None, 'char_hsin'), self.task('char_lily', 'char_x', 'char_hsin')]
        self.assertIsNone(home.team_from_tasks(tasks, self.CHAR_INFO))

    def test_team_from_last_run_reads_newest_start_event(self):
        with tempfile.TemporaryDirectory() as folder:
            def write(name, team):
                start = {'e': 'start', 'team': team}
                Path(folder, name).write_text(json.dumps(start) + '\n{"e":"in"}\n', encoding='utf-8')
            write('20261119_100000_A.jsonl', [{'i': 0, 'cls': 'Aemeath'}, {'i': 1, 'cls': 'Chisa'},
                                              {'i': 2, 'cls': 'Denia'}])
            write('20261120_030955_B.jsonl', [{'i': 2, 'cls': 'Lily'}, {'i': 0, 'cls': 'Hsin'},
                                              {'i': 1, 'cls': 'Suoming'}])
            Path(folder, '20261120_040000_C.jsonl').write_text('not json\n', encoding='utf-8')
            self.assertEqual(home.team_from_last_run(folder), ['Hsin', 'Suoming', 'Lily'])

    def test_team_from_last_run_without_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(home.team_from_last_run(folder))


class CombatCardTest(unittest.TestCase):
    def test_chips(self):
        config = {'按 F 开始战斗': True, 'F 之后等待(秒)': 0.3, 'Auto Target': True, 'Use Liberation': True}
        self.assertEqual(home.combat_chips(config), ['按 F 开打 · 0.3 秒', '自动选敌'])
        config = {'按 F 开始战斗': True, 'F 之后等待(秒)': 'bad', 'Auto Target': False, 'Use Liberation': False}
        self.assertEqual(home.combat_chips(config), ['按 F 开打', '大世界不放大招'])

    def test_view_follows_switch_and_executor(self):
        # 开关开着但总开关没启动: 提醒去启动识别
        self.assertEqual(home.combat_view('start', True, False, False), ('等启动识别', 'warning'))
        # 开关关着: 不管总开关
        self.assertEqual(home.combat_view('start', False, False, False), ('已关闭', 'idle'))
        self.assertEqual(home.combat_view('pause', False, False, False), ('已关闭', 'idle'))
        self.assertEqual(home.combat_view('pause', True, False, False)[1], 'success')
        self.assertEqual(home.combat_view('pause', True, True, False), ('战斗中', 'accent'))
        self.assertEqual(home.combat_view('pause', True, False, True), ('交给刷4C', 'accent'))
        self.assertEqual(home.combat_view('cancel', True, False, False), ('正在启动游戏', 'accent'))
        self.assertEqual(home.combat_view('disabled', True, False, False)[1], 'warning')

    def test_hint(self):
        self.assertIn('开关', home.combat_hint('pause', False, True, 'F9'))
        self.assertEqual(home.combat_hint('start', True, True, 'F9'), '识别还没启动，点右上角「启动」或按 F9')
        self.assertEqual(home.combat_hint('start', True, True, ''), '识别还没启动，点右上角「启动」')
        self.assertIn('按 F', home.combat_hint('pause', True, True, 'F9'))
        self.assertEqual(home.combat_hint('pause', True, False, 'F9'), '进战斗自动打')
        self.assertIn('刷4C', home.combat_hint('pause', False, True, 'F9', farm_active=True))


class RunButtonTest(unittest.TestCase):
    def test_follows_run_bar_state(self):
        self.assertEqual(home.run_view('start', 'F9'), ('启动  F9', 'play', 'accent', True))
        self.assertEqual(home.run_view('pause', 'F9'), ('暂停  F9', 'pause', 'dark', True))
        self.assertEqual(home.run_view('cancel', 'F9'), ('取消启动', 'x', 'dark', True))
        self.assertEqual(home.run_view('disabled', 'F9')[3], False)

    def test_without_hotkey(self):
        self.assertEqual(home.run_view('start', '')[0], '启动')


class FarmCardTest(unittest.TestCase):
    KEYS = {'Weekly Challenge': ('Weekly Boss Name', 'Which Weekly Boss to Teleport')}

    def test_teleport_target(self):
        config = {'Teleport to Boss': 'Weekly Challenge', 'Weekly Boss Name': '达妮娅（虚妄诞生之种）',
                  'Boss Level': '80'}
        tr = {'Weekly Challenge': '战歌重奏'}.get
        self.assertEqual(home.farm_target(config, self.KEYS, tr), ('达妮娅（虚妄诞生之种）', '战歌重奏 · 80 级'))

    def test_no_teleport(self):
        self.assertEqual(home.farm_target({'Teleport to Boss': 'No'}, self.KEYS)[0], '不传送')

    def test_missing_boss_name(self):
        config = {'Teleport to Boss': 'Weekly Challenge', 'Boss Level': ''}
        self.assertEqual(home.farm_target(config, self.KEYS), ('还没选 Boss', 'Weekly Challenge'))

    def test_format_number(self):
        self.assertEqual(home.format_number(None), '—')
        self.assertEqual(home.format_number(9.6), '9.6')
        self.assertEqual(home.format_number(7.0), '7')
        self.assertEqual(home.format_number(3), '3')


class HomeTabWidgetTest(unittest.TestCase):
    """离屏搭一遍整页: 没有游戏、没有执行器也不能报错。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_builds_and_refreshes_without_executor(self):
        from ok.util.config import Config
        with tempfile.TemporaryDirectory() as folder:
            old = Config.config_folder
            Config.config_folder = folder
            try:
                tab = home.HomeTab()
                tab.resize(1000, 720)
                tab.refresh()
                tab._rebuild_backdrop()
                self.assertIsNotNone(tab.blurred_backdrop())
                self.assertEqual(tab.icon, 'home')
                self.assertEqual(tab.nav_section, 'run')
                self.assertTrue(tab.startup_page)
                self.assertFalse(tab.add_after_default_tabs)
                self.assertEqual(tab.run_button.text(), '启动')
                tab.deleteLater()
            finally:
                Config.config_folder = old


if __name__ == '__main__':
    unittest.main()
