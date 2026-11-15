import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import dynamics, laps, lines, sections, server, session  # noqa: E402
from ecoanalysis.sample import write_sample  # noqa: E402

M_PER_DEG = 111320.0


def circle(radius, speed, rate=10.0, seconds=30.0, clockwise=False, lat0=36.0):
    """半径 radius m の円を速さ speed m/s で回る測位"""
    t = np.arange(0, seconds, 1 / rate)
    a = speed / radius * t * (-1 if clockwise else 1)
    x, y = radius * np.cos(a), radius * np.sin(a)
    return {'t': t, 'lat': lat0 + y / M_PER_DEG, 'lon': 140.0 + x / (M_PER_DEG * math.cos(math.radians(lat0))),
            'speed_kmh': np.full(len(t), speed * 3.6)}


class DynamicsTest(unittest.TestCase):
    def test_circle_gives_radius_and_lateral_g(self):
        left = dynamics.from_gnss(circle(50.0, 20.0))
        mid = slice(20, -20)
        self.assertAlmostEqual(float(np.nanmedian(left['radius_m'][mid])), 50.0, delta=1.0)
        self.assertAlmostEqual(float(np.nanmedian(left['lat_g'][mid])), -20.0 ** 2 / 50.0 / 9.80665, delta=0.03)  # 左へ曲がると −
        self.assertAlmostEqual(float(np.nanmedian(left['long_g'][mid])), 0.0, delta=0.01)
        right = dynamics.from_gnss(circle(50.0, 20.0, clockwise=True))
        self.assertGreater(float(np.nanmedian(right['lat_g'][mid])), 0.7)                                          # 右へ曲がると ＋

    def test_straight_slow_and_coarse_records(self):
        self.assertTrue(np.all(np.isnan(dynamics.from_gnss(circle(5000.0, 20.0))['radius_m'])))   # 600 m より大きい半径は出さない
        self.assertTrue(np.all(np.isnan(dynamics.from_gnss(circle(50.0, 2.0))['radius_m'])))       # 10 km/h より遅い
        self.assertIsNone(dynamics.from_gnss(circle(50.0, 20.0, rate=1.0)))                        # 1Hz の記録では作らない
        self.assertIsNone(dynamics.from_gnss(None))


class SectionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = write_sample(Path(self.tmp.name) / 'sample.ecorun', laps=4)
        self.s = session.load(self.path)
        self.laps, self.line, _ = laps.for_session(self.s)

    def tearDown(self):
        self.tmp.cleanup()

    def test_auto_places_split_at_brake_start(self):
        # 作りものの前後Gは 0.3·sin(周の角度)。−0.25 G を下回るブレーキは周に1回で、踏み始め（−0.05 G）は角度 189.6° の所
        found = sections.auto(self.s, self.laps[1])
        self.assertEqual(found['how'], 'brake')
        self.assertEqual(len(found['splits']), 1)
        tr, _ = sections._track(self.s, self.laps[1]['start'], self.laps[1]['end'])
        length = float(tr[3][-1])
        # 楕円の上の距離の割合は角度の割合と少し違うので、角度 189.6° の点の距離と比べる
        a = np.linspace(0, 2 * math.pi, 20001)
        d = np.concatenate(([0], np.cumsum(np.hypot(np.diff(400 * np.sin(a)), np.diff(150 * np.cos(a))))))
        want = float(np.interp(math.radians(189.6), a, d)) / d[-1] * length
        self.assertAlmostEqual(found['at_m'][0], want, delta=8.0)
        sp = found['splits'][0]
        self.assertTrue(laps.valid_line(sp))
        self.assertEqual(sp['width_m'], sections.SPLIT_WIDTH_M)

    def test_without_long_accel_uses_top_speed(self):
        del self.s.channels['LongAccel']
        found = sections.auto(self.s, self.laps[1])
        self.assertEqual(found['how'], 'speed')                 # 最高速は周の頭（線の上なので捨てる）と半周の所
        tr, _ = sections._track(self.s, self.laps[1]['start'], self.laps[1]['end'])
        self.assertEqual(len(found['at_m']), 1)
        self.assertAlmostEqual(found['at_m'][0] / float(tr[3][-1]), 0.5, delta=0.03)

    def test_cut_metrics_rolling_and_summary(self):
        found = sections.auto(self.s, self.laps[1])
        cut, _, _ = laps.for_session(self.s, splits=found['splits'])
        for x in cut:
            self.assertEqual(len(x['splits']), 2)
            self.assertAlmostEqual(sum(x['splits']), x['time_s'], delta=0.002)
            self.assertTrue(x['start'] < x['split_t'][0] < x['end'])
        sections.metrics(self.s, cut, dynamics.from_gnss(self.s.gnss))
        ref = cut[1]['sec']
        self.assertEqual(len(ref), 2)
        self.assertIsNone(ref[0]['brake_m'])                    # 周の頭の区間はブレーキの切り目ではない
        self.assertAlmostEqual(ref[1]['brake_m'], 0.0, delta=2.0)   # 線を置いた周は、踏み始めが線の上
        sp = np.asarray(self.s.channels['Speed'], float)
        at = float(np.interp(cut[1]['split_t'][0], self.s.t, sp))
        self.assertAlmostEqual(ref[1]['entry'], at, delta=0.1)
        self.assertAlmostEqual(ref[0]['exit'], at, delta=0.1)
        self.assertLessEqual(ref[1]['min_kmh'], ref[1]['entry'])
        self.assertGreater(ref[1]['min_at_m'], 0)
        self.assertAlmostEqual(ref[1]['min_radius_m'], 150.0 ** 2 / 400.0, delta=3.0)   # ボトムは楕円の端（半径 150²/400 m）
        roll = sections.rolling(self.s.gnss, self.line, found['splits'])
        st = sections.summarize([cut], [roll])
        self.assertEqual(st['best_lap'], min(x['time_s'] for x in cut))
        self.assertLessEqual(st['splits']['theory'], st['best_lap'] + 1e-9)
        self.assertLessEqual(st['splits']['theory'], roll['time_s'] + 1e-9)
        self.assertLessEqual(roll['time_s'], st['best_lap'] + 1e-9)
        self.assertEqual(st['splits']['rolling'], roll['time_s'])
        self.assertEqual(len(st['sectors']['best']), 3)

    def test_summary_leaves_out_slow_sections_and_gaps(self):
        lap = lambda sp, gap=False: {'time_s': sum(sp), 'splits': sp, 'sectors': [], 'gap': gap}   # noqa: E731
        st = sections.summarize([[lap([10.0, 20.0]), lap([10.2, 20.4]), lap([10.1, 30.0])], [lap([9.0, 19.0], gap=True)]])
        sp = st['splits']
        self.assertEqual(sp['best'], [10.0, 20.0])               # 測位の抜けた周は使わない
        self.assertEqual(sp['theory'], 30.0)
        self.assertEqual(sp['used'], [3, 2])                     # 30.0 は中央値 20.4 の 5% より遅いので外す
        self.assertAlmostEqual(sp['mean'][1], 20.2, delta=1e-9)
        self.assertAlmostEqual(sp['average'], 10.1 + 20.2, delta=1e-9)
        self.assertIsNone(sp['rolling'])
        self.assertIsNone(sections.summarize([[]])['best_lap'])


class StoreSectionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / 'home'
        self.sample = write_sample(Path(self.tmp.name) / 'sample.ecorun', laps=4)
        self.course = json.loads((self.sample / 'session.json').read_text(encoding='utf-8'))['course']
        self.store = server.Store(self.home)
        self.sid = self.store.open(self.sample)['id']

    def tearDown(self):
        self.tmp.cleanup()

    def test_auto_keeps_splits_and_versions_carry_them(self):
        found = self.store.sections_auto(self.sid, 2)
        self.assertEqual(found['count'], 1)
        s = self.store.list()[0]
        self.assertEqual(len(s['splits']), 1)
        self.assertTrue(all(len(x['splits']) == 2 and len(x['sec']) == 2 for x in s['laps']['app']))
        self.assertIsNotNone(s['rolling'])
        self.assertIsNotNone(s['stats']['splits']['theory'])
        loose = json.loads((self.home / 'lines.json').read_text(encoding='utf-8'))[self.course['name']]['splits']
        self.assertEqual(len(loose), 1)                          # 版を使っていなければ、その場で置いた分として残る
        # 版を残すと区間の線も持っていき、書き出しの線へ戻すとその場で置いた分に戻る
        self.store.set_lines('save', self.course['name'], '版A', self.course['line'], self.course['sectors'])
        self.assertEqual(len(self.store.lines.splits(self.course['name'])), 1)
        self.store.set_splits(self.course['name'], [])
        self.assertEqual(self.store.list()[0]['splits'], [])
        self.store.set_lines('use', self.course['name'], None)
        self.assertEqual(len(self.store.list()[0]['splits']), 1)
        # 区間の線は開き直しても使う
        again = server.Store(self.home)
        again.open(self.sample)
        self.assertEqual(len(again.list()[0]['splits']), 1)

    def test_errors(self):
        with self.assertRaises(ValueError):
            self.store.sections_auto(self.sid, 99)
        with self.assertRaises(KeyError):
            self.store.sections_auto('s999', 1)
        with self.assertRaises(lines.LineError):
            self.store.set_splits(self.course['name'], [{'lat': 1}])
        with self.assertRaises(lines.LineError):
            self.store.set_splits('', [])

    def test_store_reads_home_when_made(self):
        with mock.patch.object(server, 'HOME', self.home / 'other'):
            self.assertEqual(server.Store().home, self.home / 'other')


if __name__ == '__main__':
    unittest.main()
