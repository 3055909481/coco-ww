import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QWidget

from ok.ui.qt.shell.top_nav import TopNav

SPEC = {
    'tabs': ['HomeTab', 'TasksPage', 'CharacterCodeTab', 'AxisRecorderTab', 'LogPage'],
    'actions': ['DashboardTab'],
    'labels': {'HomeTab': '首页', 'TasksPage': '任务设置'},
}


def page(class_name):
    return type(class_name, (QWidget,), {})()


class TopNavTest(unittest.TestCase):
    """顶部标签栏: 页面按类名分到标签/按钮/齿轮菜单, 顺序按配置, 不按加页面的先后。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.nav = TopNav(SPEC)
        self.picked = []
        self.nav.current_changed.connect(self.picked.append)
        # MainWindow 加页面的实际顺序: 主页、角色代码、跑轴复盘 (自定义页), 任务, 详细面板, 诊断, 日志, 全局配置页, 设置/关于
        for key, name, text, section, bottom in (
                ('home', 'HomeTab', '主页', 'run', False),
                ('code', 'CharacterCodeTab', '角色代码', 'game', False),
                ('axis', 'AxisRecorderTab', '跑轴复盘', 'game', False),
                ('tasks', 'TasksPage', '任务', 'run', False),
                ('dash', 'DashboardTab', '详细面板', 'run', False),
                ('diag', 'DiagnosisPage', '诊断', 'develop', False),
                ('log', 'LogPage', '日志', 'run', False),
                ('chars', 'LazyPage', '角色设置', 'game', False),
                ('keys', 'LazyPage', '游戏快捷键', 'game', False),
                ('settings', 'LazyPage', '设置', None, True),
                ('about', 'AboutTab', '关于', None, True)):
            self.nav.add_page(key, page(name), text, None, section, bottom)

    def test_placement_and_order(self):
        self.assertEqual(self.nav.tab_labels(), ['首页', '任务设置', '角色代码', '跑轴复盘', '日志'])
        self.assertEqual(self.nav.action_labels(), ['详细面板'])
        self.assertEqual(self.nav.menu_labels(), [['角色设置', '游戏快捷键'], ['诊断'], ['设置', '关于']])

    def test_current_and_clicks(self):
        self.nav.set_current('tasks')
        checked = [tab.label() for tab in self.nav._tabs.values() if tab.isChecked()]
        self.assertEqual(checked, ['任务设置'])
        self.nav._tabs['axis'].click()
        self.nav._actions['dash'].click()
        self.assertEqual(self.picked, ['axis', 'dash'])
        self.nav.set_current('dash')
        self.assertEqual(self.nav._actions['dash'].property('variant'), 'tonal')
        self.assertFalse(any(tab.isChecked() for tab in self.nav._tabs.values()))

    def test_menu_actions(self):
        self.nav.set_current('settings')
        menu = self.nav.build_menu()
        actions = [a for a in menu.actions() if not a.isSeparator()]
        self.assertEqual([a.text() for a in actions], ['角色设置', '游戏快捷键', '诊断', '设置', '关于'])
        self.assertEqual(len([a for a in menu.actions() if a.isSeparator()]), 2)
        self.assertTrue(actions[3].isChecked())
        actions[1].trigger()
        self.assertEqual(self.picked, ['keys'])

    def test_badge_and_remove(self):
        self.nav.set_badge('about', True)
        self.assertTrue(self.nav.more_button.property('badge'))
        self.assertIn('关于  ●', [a.text() for a in self.nav.build_menu().actions()])
        self.nav.remove_page('about')
        self.assertFalse(self.nav.more_button.property('badge'))
        self.assertFalse(self.nav.has_page('about'))
        self.nav.remove_page('code')
        self.assertEqual(self.nav.tab_labels(), ['首页', '任务设置', '跑轴复盘', '日志'])


if __name__ == '__main__':
    unittest.main()
