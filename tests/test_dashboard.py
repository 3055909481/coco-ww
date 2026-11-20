import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from src import log_feed, timer_precision
from src.recorder import latency, live
from src.gui import DashboardTab as dash

TEAM = [{'i': 0, 'n': 'Hsin', 'cls': 'Hsin'}, {'i': 1, 'n': 'Suoming', 'cls': 'Suoming'},
        {'i': 2, 'n': 'Lily', 'cls': 'Lily'}]


def field(t, slot, prev_seen=None):
    event = {'e': 'field', 't': t, 'team': slot >= 0, 'i': slot, 'cnt': 3}
    if prev_seen is not None:
        event['prev_seen'] = prev_seen
    return event


def run_lines(events, end=None):
    lines = [json.dumps({'e': 'start', 'team': TEAM}, separators=(',', ':'))]
    lines += [json.dumps(e, separators=(',', ':')) for e in events]
    if end is not None:
        lines.append(json.dumps(end, separators=(',', ':')))
    return '\n'.join(lines) + '\n'


def cycle_events():
    """开场轴 1→2, 然后 0→1→2→1 每圈 10 秒重复三圈, 最后 0 再上场。"""
    events, t = [], 0.0
    for slot in (1, 2):
        events.append(field(t, slot))
        t += 3.0
    for _ in range(3):
        for slot, length in ((0, 4.0), (1, 2.0), (2, 3.0), (1, 1.0)):
            events.append(field(t, slot))
            t += length
    events.append(field(t, 0))
    events.append({'e': 'in', 't': t + 0.5, 'a': 'key', 'k': 'e'})  # 实际记录换人后马上就有按键
    return events, t


class LatencyTest(unittest.TestCase):
    def test_switch_and_liberation(self):
        rows = [field(0.0, 0),
                {'e': 'in', 't': 1.0, 'a': 'key', 'k': '2'},
                {'e': 'in', 't': 1.0, 'a': 'kd', 'k': '2', 'si': 1},
                {'e': 'in', 't': 1.04, 'a': 'key', 'k': '2'},
                field(1.07, 1, prev_seen=1.03),
                {'e': 'in', 't': 2.0, 'a': 'key', 'k': 'r'},
                field(2.5, -1, prev_seen=2.45)]
        result = latency.analyse(rows)
        self.assertEqual(len(result['switch']), 1)
        up, low, presses = result['switch'][0]
        self.assertAlmostEqual(up, 0.07)
        self.assertAlmostEqual(low, 0.03)
        self.assertEqual(presses, 2)
        self.assertAlmostEqual(latency.median_ms(result['lib']), 500.0)
        self.assertIsNone(latency.median_ms([]))


class RunTailTest(unittest.TestCase):
    def test_reads_only_new_complete_lines_and_skips_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, '20261120_030955_Hsin__Suoming__Lily.jsonl')
            text = run_lines([field(0.0, 0), {'e': 'read', 't': 0.1, 'k': 'color'}, field(1.0, 1)])
            path.write_text(text + '{"e":"field","t":2.0,"te', encoding='utf-8')
            tail = live.RunTail(str(path))
            self.assertTrue(tail.poll())
            self.assertEqual([e['e'] for e in tail.events], ['field', 'field'])
            self.assertEqual(tail.header['team'][0]['cls'], 'Hsin')
            self.assertFalse(tail.poll())
            with open(path, 'a', encoding='utf-8') as f:
                f.write('am":true,"i":2,"cnt":3}\n{"e":"in","t":2.3,"a":"key","k":"e"}\n')
            self.assertTrue(tail.poll())
            self.assertEqual(live.current_slot(tail.data()), 2)

    def test_restarts_when_file_is_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'run.jsonl')
            path.write_text(run_lines([field(0.0, 0), field(1.0, 1), field(2.0, 2)]), encoding='utf-8')
            tail = live.RunTail(str(path))
            tail.poll()
            path.write_text(run_lines([field(0.0, 1)]), encoding='utf-8')
            self.assertTrue(tail.poll())
            self.assertEqual(len(tail.events), 1)


class LiveSummaryTest(unittest.TestCase):
    def data(self, events, end=None):
        header = {'e': 'start', 'team': TEAM}
        return live.RunData('x', parsed=(header, end, events))

    def test_cycle_laps_skip_the_opener(self):
        events, _ = cycle_events()
        slot, period, laps = live.cycle_laps(self.data(events))
        self.assertEqual((slot, period), (0, 4))
        self.assertEqual([round(d, 3) for _, _, d in laps], [10.0, 10.0, 10.0])
        self.assertEqual(laps[0][1], 6.0)
        self.assertEqual([n for n, _, _ in laps], [1, 2, 3])

    def test_cycle_laps_need_two_full_laps(self):
        events = [field(0.0, 0), field(1.0, 1), field(2.0, 2), field(3.0, 0)]
        self.assertEqual(live.cycle_laps(self.data(events)), (None, 0, []))

    def test_liberation_hud_gap_is_not_a_switch(self):
        events = [field(0.0, 0), field(1.0, -1), field(2.0, 0), field(3.0, 1),
                  {'e': 'in', 't': 3.5, 'a': 'key', 'k': 'e'}]
        self.assertEqual([slot for _, slot in live.field_changes(self.data(events))], [0, 1])
        self.assertEqual(live.current_slot(self.data(events)), 1)

    def test_field_time_and_segments(self):
        events, end_t = cycle_events()
        data = self.data(events, {'e': 'end', 't': end_t + 2.0, 'reason': 'x'})
        totals = live.field_time(data)
        self.assertAlmostEqual(sum(totals.values()), end_t + 2.0)
        start, end, segments = live.field_segments(data, 10.0)
        self.assertAlmostEqual(end - start, 10.0)
        self.assertEqual(segments[0][0], start)
        self.assertEqual(live.team_members(data)[1], (1, 'Suoming', 'Suoming'))

    def test_current_run_path(self):
        with tempfile.TemporaryDirectory() as folder:
            older = Path(folder, '20261120_010000_A.jsonl')
            newer = Path(folder, '20261120_020000_B.jsonl')
            for path in (older, newer):
                path.write_text('', encoding='utf-8')
            self.assertEqual(live.current_run_path(None, folder), (str(newer), False))
            recorder = SimpleNamespace(run=SimpleNamespace(path=str(older)), last_path=None, out_dir=folder)
            self.assertEqual(live.current_run_path(recorder), (str(older), True))
            recorder = SimpleNamespace(run=None, last_path=str(older), out_dir=folder)
            self.assertEqual(live.current_run_path(recorder), (str(older), False))
            self.assertEqual(live.current_run_path(None, str(Path(folder, 'missing'))), (None, False))


class LogFeedTest(unittest.TestCase):
    def test_split_source(self):
        self.assertEqual(log_feed.split_source('Hsin:L3心2A1: 0@0.08'), ('Hsin', 'L3心2A1: 0@0.08'))
        self.assertEqual(log_feed.split_source('no source here: x'), ('', 'no source here: x'))

    def test_records_and_install_once(self):
        name = 'coco_test_logs'
        handler = log_feed.install(name)
        self.assertIs(log_feed.install(name), handler)
        test_logger = logging.getLogger(name)
        test_logger.setLevel(logging.DEBUG)
        test_logger.propagate = False
        test_logger.debug('Hsin:too quiet')
        test_logger.info('Hsin:loop #3 30.87s')
        test_logger.warning('AutoCombatTask:found multiple rings 2')
        records = handler.since(0)
        self.assertEqual([(r[2], r[3]) for r in records], [(20, 'Hsin'), (30, 'AutoCombatTask')])
        self.assertEqual(handler.since(records[0][0])[0][4], 'found multiple rings 2')
        test_logger.removeHandler(handler)


class TimerMeasurementTest(unittest.TestCase):
    def test_remember(self):
        timer_precision._remember(2.04, '进战斗')
        value, tag, _ = timer_precision.last_measurement()
        self.assertEqual((value, tag), (2.04, '进战斗'))


class DashboardHelpersTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(dash.format_seconds(96.33), '96.3s')
        self.assertEqual(dash.format_seconds(243.5), '4:04')
        self.assertEqual(dash.format_seconds(None), '—')
        self.assertEqual(dash.format_ms(71.2), '71')
        self.assertEqual(dash.format_ms(2.04), '2.0')
        self.assertEqual(dash.format_ms(None), '—')

    def test_headline(self):
        self.assertEqual(dash.run_headline(True, None, 4, 96.3), ('战斗中 · 第 4 圈', '本局 96.3s'))
        self.assertEqual(dash.run_headline(True, None, 0, 5)[0], '战斗中 · 开场轴')
        self.assertEqual(dash.run_headline(False, 'auto combat disabled', 3, 243)[1], '用时 4:03 · 自动战斗被停了')
        self.assertIn('HUD 8 秒没出现', dash.run_headline(False, 'HUD 8 秒没出现', 3, 243)[1])

    def test_log_filter_and_html(self):
        warn = (1, 0.0, 30, 'AutoCombatTask', 'rings')
        mine = (2, 0.0, 20, 'Hsin', 'loop <3>')
        self.assertTrue(dash.log_matches(warn, dash.FILTER_WARN, set()))
        self.assertFalse(dash.log_matches(mine, dash.FILTER_WARN, {'Hsin'}))
        self.assertTrue(dash.log_matches(mine, dash.FILTER_TEAM, {'Hsin'}))
        self.assertFalse(dash.log_matches(warn, dash.FILTER_TEAM, {'Hsin'}))
        line = dash.log_html(mine, {'Hsin': '心'}, {'Hsin': '#F59E6B'})
        self.assertIn('心', line)
        self.assertIn('loop &lt;3&gt;', line)
        long_line = dash.log_html((3, 0.0, 20, 'Hsin', 'x' * 1000), {}, {})
        self.assertIn('x' * dash.LOG_MAX_CHARS + ' …', long_line)
        self.assertNotIn('x' * (dash.LOG_MAX_CHARS + 1), long_line)


class DashboardWidgetTest(unittest.TestCase):
    """离屏搭一遍整页, 喂一局假的记录: 没有执行器、没有记录器也不能报错。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_shows_a_run(self):
        events, _ = cycle_events()
        events.append({'e': 'in', 't': 29.92, 'a': 'key', 'k': '2'})  # 30 秒那次换到 2 号位
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, '20261120_030955_Hsin__Suoming__Lily.jsonl')
            path.write_text(run_lines(events), encoding='utf-8')
            tab = dash.DashboardTab()
            tab._current_path = lambda: (str(path), True)
            tab.resize(1200, 760)
            tab.refresh()
            self.assertEqual(tab.headline.text(), '战斗中 · 第 4 圈')
            self.assertEqual(tab.tile_switch.value.text(), '80')
            self.assertIn('每圈换 4 次人', tab.laps_note.text())
            self.assertEqual(tab.icon, 'pulse')
            self.assertEqual(tab.nav_section, 'run')
            tab.deleteLater()

    def test_empty(self):
        tab = dash.DashboardTab()
        tab._current_path = lambda: (None, False)
        tab.refresh()
        self.assertEqual(tab.headline.text(), '还没有跑轴记录')
        tab.deleteLater()


if __name__ == '__main__':
    unittest.main()
