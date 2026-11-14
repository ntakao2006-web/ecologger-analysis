import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import laps, session  # noqa: E402
from ecoanalysis.sample import write_sample  # noqa: E402

LINE = {'lat': 35.0, 'lon': 135.0, 'bearing': 90.0, 'width_m': 20.0}
M_PER_DEG_LON = laps.EARTH_M_PER_DEG * math.cos(math.radians(35.0))


def east(t, x, north_m=3.0):
    """北緯35度付近を東西に動く測位（x は線からの東向きの距離 m）"""
    return {'t': np.asarray(t, float), 'lat': np.full(len(t), 35.0 + north_m / laps.EARTH_M_PER_DEG),
            'lon': 135.0 + np.asarray(x, float) / M_PER_DEG_LON}


class CrossingTest(unittest.TestCase):
    def test_interpolated_time_direction_width_and_gap(self):
        t = np.arange(0, 25, 0.1)
        g = east(t, 40.0 * (t - 12.34))           # 40 m/s で東へ、12.34 秒に線を越える
        c = laps.crossings(g, LINE)
        self.assertEqual(len(c), 1)
        self.assertAlmostEqual(c[0], 12.34, delta=1e-6)
        self.assertEqual(len(laps.crossings(g, {**LINE, 'bearing': 270.0})), 0)     # 逆向きは数えない
        self.assertEqual(len(laps.crossings(g, {**LINE, 'width_m': 4.0})), 0)       # 中心から3m：幅4mの外
        keep = (t < 12.0) | (t > 13.2)                                               # 越えた所の前後1.2秒が抜けた
        self.assertEqual(len(laps.crossings({k: v[keep] for k, v in g.items()}, LINE)), 0)

    def test_jitter_on_the_line_counts_once(self):
        t = np.arange(0, 30, 0.1)
        x = np.where(t < 10, 20.0 * (t - 10), 0.0) + np.where((t >= 10) & (t < 20), 0.4 * np.sin(t * 7), 0.0)
        x = np.where(t >= 20, 20.0 * (t - 20), x)  # 線の上で止まってふらつき、また走り出す
        self.assertEqual(len(laps.crossings(east(t, x), LINE)), 1)

    def test_invalid_line(self):
        g = east([0, 1], [-1, 1])
        self.assertEqual(len(laps.crossings(g, {'lat': 35.0})), 0)
        self.assertEqual(len(laps.crossings(None, LINE)), 0)


class CutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = session.load(write_sample(Path(self.tmp.name) / 'sample.ecorun'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_sample_matches_logger_laps(self):
        out, line, why = laps.for_session(self.s)
        self.assertIsNone(why)
        self.assertEqual([x['lap'] for x in out], [1, 2, 3])
        for x, g in zip(out, self.s.laps):
            self.assertAlmostEqual(x['time_s'], g['time_s'], delta=0.005)
            self.assertAlmostEqual(x['start'], g['start'], delta=0.005)
            self.assertEqual(x['logger']['lap'], g['lap'])
            self.assertFalse(x['gap'])
            self.assertEqual(len(x['sectors']), 3)
            for a, b in zip(x['sectors'], g['sectors']):
                self.assertAlmostEqual(a, b, delta=0.01)
        self.assertEqual(out[0]['end'], out[1]['start'])      # 続いた周は境目が同じ

    def test_reasons_when_it_cannot_cut(self):
        s = self.s
        s.meta['course'] = {'name': 'x'}
        self.assertEqual(laps.for_session(s)[2], 'コースの線が入っていない')
        s.gnss = None
        self.assertEqual(laps.for_session(s)[2], '測位が入っていない')

    def test_gap_and_numbering(self):
        t = np.arange(0, 400, 0.1)
        x = (t % 100.0) * 20.0 - 500.0              # 100秒ごとに線を越える（25・125・225・325秒）
        x[(t > 150) & (t < 160)] = np.nan           # 2周目の途中で10秒の抜け
        g = east(t, x)
        logger = [{'lap': 31, 'end': 125.02, 'time_s': 100.0}, {'lap': 33, 'end': 325.0, 'time_s': 100.0}]
        out = laps.cut(g, LINE, [], logger)
        self.assertEqual([round(v['start']) for v in out], [25, 125, 225])
        self.assertEqual([v['lap'] for v in out], [31, 32, 33])   # ロガーに無い周は前後から続き番号
        self.assertEqual([v['gap'] for v in out], [False, True, False])
        self.assertIsNone(out[1]['logger'])
        self.assertAlmostEqual(out[0]['logger']['diff_ms'], 0.0, delta=0.5)

    def test_sector_times(self):
        self.assertEqual(laps._sector_times(0.0, 10.0, [np.array([3.0]), np.array([7.0])]), [3.0, 4.0, 3.0])
        self.assertEqual(laps._sector_times(0.0, 10.0, [np.array([3.0]), np.array([])]), [3.0, None, None])
        self.assertEqual(laps._sector_times(0.0, 10.0, [np.array([7.0]), np.array([3.0])]), [7.0, None, None])
        self.assertEqual(laps._sector_times(0.0, 10.0, []), [])


if __name__ == '__main__':
    unittest.main()
