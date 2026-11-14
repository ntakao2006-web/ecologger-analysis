import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import laps, lines, server  # noqa: E402
from ecoanalysis.sample import CENTER, RX, RY, write_sample  # noqa: E402


def shifted_line(meta, east_m):
    """書き出しの線を、楕円の上の辺に沿って east_m だけ東へずらした線（向きはそのまま）"""
    line = dict(meta['course']['line'])
    line['lon'] += east_m / (111320.0 * math.cos(math.radians(CENTER[0])))
    return line


class LinesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / 'home'
        self.sample = write_sample(Path(self.tmp.name) / 'sample.ecorun')
        self.meta = json.loads((self.sample / 'session.json').read_text(encoding='utf-8'))
        self.course = self.meta['course']['name']
        self.store = server.Store(self.home)
        self.sid = self.store.open(self.sample)['id']

    def tearDown(self):
        self.tmp.cleanup()

    def laps_now(self):
        return self.store.list()[0]

    def test_save_recuts_and_keeps_lap_numbers(self):
        before = self.laps_now()['laps']['app']
        line = shifted_line(self.meta, 40.0)       # 40m 先へ動かす
        sessions = self.store.set_lines('save', self.course, '40m先', line, [])
        s = sessions[0]
        self.assertEqual(s['line_name'], '40m先')
        self.assertEqual(s['line_names'], ['40m先'])
        self.assertEqual(s['sectors'], [])
        after = s['laps']['app']
        self.assertEqual([x['lap'] for x in after], [x['lap'] for x in before])   # 番号はロガーの周のまま
        for a, b in zip(before, after):
            self.assertGreater(b['start'], a['start'])                           # 線が先へ動いた分、遅く越える
            self.assertIsNone(b['logger'])                                       # 線が違うのでロガーとは比べない
            self.assertEqual(b['sectors'], [])
        self.assertTrue((self.home / 'lines.json').is_file())

    def test_use_and_delete_go_back_to_the_export_line(self):
        self.store.set_lines('save', self.course, 'A', shifted_line(self.meta, 40.0), [])
        s = self.store.set_lines('use', self.course, None)[0]
        self.assertIsNone(s['line_name'])
        self.assertEqual(s['line'], self.meta['course']['line'])
        self.assertEqual(len(s['sectors']), 2)
        s = self.store.set_lines('use', self.course, 'A')[0]
        self.assertEqual(s['line_name'], 'A')
        s = self.store.set_lines('delete', self.course, 'A')[0]
        self.assertIsNone(s['line_name'])
        self.assertEqual(s['line_names'], [])
        self.assertTrue(all(x['logger'] for x in s['laps']['app']))

    def test_active_version_applies_when_opening_again(self):
        self.store.set_lines('save', self.course, 'A', shifted_line(self.meta, 40.0), [])
        other = server.Store(self.home)
        self.assertEqual(other.open(self.sample)['line_name'], 'A')

    def test_errors(self):
        with self.assertRaises(lines.LineError):
            self.store.set_lines('save', self.course, '', shifted_line(self.meta, 1.0), [])
        with self.assertRaises(lines.LineError):
            self.store.set_lines('save', self.course, 'x' * 41, shifted_line(self.meta, 1.0), [])
        with self.assertRaises(lines.LineError):
            self.store.set_lines('save', self.course, 'B', {'lat': 1}, [])
        with self.assertRaises(lines.LineError):
            self.store.set_lines('use', self.course, '無い版')
        with self.assertRaises(lines.LineError):
            self.store.set_lines('save', None, 'B', shifted_line(self.meta, 1.0), [])
        with self.assertRaises(lines.LineError):
            self.store.set_lines('rename', self.course, 'B')


class NumberingTest(unittest.TestCase):
    def test_loose_numbering_but_tight_comparison(self):
        app = [{'start': 0.0, 'end': 100.0, 'time_s': 100.0, 'logger': None}, {'start': 100.0, 'end': 200.0, 'time_s': 100.0, 'logger': None},
               {'start': 200.0, 'end': 300.0, 'time_s': 100.0, 'logger': None}]
        logger = [{'lap': 7, 'end': 102.0, 'time_s': 100.0}, {'lap': 8, 'end': 200.2, 'time_s': 99.9}]
        laps._number(app, logger)
        self.assertEqual([x['lap'] for x in app], [7, 8, 9])
        self.assertIsNone(app[0]['logger'])                  # 2秒ずれ：番号は取るが、比べない
        self.assertEqual(app[1]['logger']['lap'], 8)
        self.assertAlmostEqual(app[1]['logger']['diff_ms'], 100.0)


if __name__ == '__main__':
    unittest.main()
