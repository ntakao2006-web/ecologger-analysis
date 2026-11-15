import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import laps, overlay, session  # noqa: E402
from ecoanalysis.sample import RX, RY, write_sample  # noqa: E402


class ProjectTest(unittest.TestCase):
    def test_points_beside_the_line_keep_their_distance(self):
        rx = np.linspace(0, 100, 51)
        ry = np.zeros_like(rx)
        rs = overlay.cumulative(rx, ry)
        x = np.array([0.0, 10.0, 37.5, 99.0])
        s = overlay.project(rx, ry, rs, x, np.full(4, 3.0))     # 3m 横を走っても、同じ所は同じ距離
        self.assertTrue(np.allclose(s, x))

    def test_lateral_offset_is_right_positive(self):
        rx = np.linspace(0, 100, 51)                             # 東へ進む線
        ry = np.zeros_like(rx)
        rs = overlay.cumulative(rx, ry)
        x = np.array([10.0, 50.0, 90.0])
        s, off = overlay.project(rx, ry, rs, x, np.array([-2.0, 0.0, 3.5]), lateral=True)
        self.assertTrue(np.allclose(s, x))
        self.assertTrue(np.allclose(off, [2.0, 0.0, -3.5]))      # 南（進む向きの右）が＋

    def test_monotone_and_ends(self):
        s = overlay._monotone(np.array([0.4, 5.0, 4.0, 9.0, 12.0]), 10.0)
        self.assertEqual(list(s), [0.0, 5.0, 5.0, 9.0, 10.0])


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = session.load(write_sample(Path(self.tmp.name) / 'sample.ecorun'))
        self.laps, _, _ = laps.for_session(self.s)

    def tearDown(self):
        self.tmp.cleanup()

    def items(self):
        return [{'key': f'x:{x["lap"]}', 'label': f'{x["lap"]}周', 'session': self.s, 'lap': x} for x in self.laps]

    def test_reference_is_fastest_and_delta_ends_at_lap_time_difference(self):
        r = overlay.compare(self.items(), ['Speed', 'ClutchDisengaged', 'Nothing'])
        self.assertTrue(r['distance'])
        self.assertEqual(r['ref'], 1)                               # 2周目（60.5秒）が一番速い
        perim = math.pi * (3 * (RX + RY) - math.sqrt((3 * RX + RY) * (RX + 3 * RY)))   # 楕円の周の長さ（近似）
        self.assertAlmostEqual(r['ref_length_m'], perim, delta=perim * 0.01)
        ref = r['laps'][1]
        self.assertTrue(all(v == 0 for v in ref['delta']))
        for lap in r['laps']:
            self.assertAlmostEqual(lap['delta'][-1], lap['time_s'] - ref['time_s'], delta=0.01)
            s = [v for v in lap['s'] if v is not None]
            self.assertTrue(all(b >= a for a, b in zip(s, s[1:])))
            self.assertEqual(sorted(lap['channels']), ['ClutchDisengaged', 'Speed'])
            self.assertEqual(len(lap['t']), len(lap['channels']['Speed']))
        self.assertEqual(len(r['grid']), len(r['laps'][0]['delta']))

    def test_paint_values_delta_rate_and_marks(self):
        r = overlay.compare(self.items(), ['Speed'], paint='Speed')
        self.assertEqual(r['paint'], 'Speed')
        ref, slow = r['laps'][1], r['laps'][0]
        for lap in r['laps']:
            tr = lap['track']
            n = len(tr['x'])
            for k in ('v', 'dd', 'rate', 'hacc', 'bad', 'dec'):
                self.assertEqual(len(tr[k]), n, k)
            self.assertFalse(any(tr['bad']))                        # 見本の測位は精度 0.8 m・10 Hz で抜けが無い
            self.assertTrue(all(abs(v - 0.8) < 1e-6 for v in tr['hacc']))
            self.assertTrue(70 < min(tr['v']) < max(tr['v']) < 150)
            self.assertTrue(any(tr['dec']) and not all(tr['dec']))  # 前後G 0.3·sin の負の側だけ点線
        self.assertTrue(all(v == 0 for v in ref['track']['dd']))
        self.assertAlmostEqual(slow['track']['dd'][-1], 1.5, delta=0.01)
        rate = [v for v in slow['track']['rate'] if v is not None]
        self.assertGreater(sum(rate) / len(rate), 0)                 # 遅い周はおおむね損（＋）
        # 印：前後G 0.3·sin(a) は a = π〜2π で負。−0.25 G を下回るのは1周に1回。踏み始めは −0.05 G を下回った所
        on = [m for m in slow['marks'] if m['k'] == 'on']
        off = [m for m in slow['marks'] if m['k'] == 'off']
        self.assertEqual((len(on), len(off)), (1, 1))
        u_on, u_off = on[0]['t'] / 62.0 * 2 * math.pi, off[0]['t'] / 62.0 * 2 * math.pi
        self.assertAlmostEqual(u_on, math.pi + math.asin(1 / 6), delta=0.05)
        self.assertAlmostEqual(u_off, 2 * math.pi - math.asin(1 / 6), delta=0.05)
        self.assertGreater(on[0]['v'], 0)                            # 踏み始めの速さ
        # 車速は 0.8 + 0.2·|cos a|：いちばん低いのは a = π/2・3π/2、高いのは π（周の頭の a = 0 は線の上なので捨てる）
        kinds = [(m['k'], round(m['t'] / 62.0 * 4) / 4) for m in slow['marks'] if m['k'] in ('min', 'max')]
        self.assertEqual(kinds, [('min', 0.25), ('max', 0.5), ('min', 0.75)])
        self.assertEqual(sorted(m['t'] for m in slow['marks']), [m['t'] for m in slow['marks']])

    def test_paint_bad_gnss_and_missing_channel(self):
        g = self.s.gnss
        lap = self.laps[0]
        t = np.asarray(g['t'])
        g['hacc_m'] = np.where((t > lap['start'] + 10) & (t < lap['start'] + 12), 3.0, np.asarray(g['hacc_m']))
        g['lat'] = np.where((t > lap['start'] + 30) & (t < lap['start'] + 31), np.nan, np.asarray(g['lat']))
        r = overlay.compare(self.items(), ['Speed'], paint='Nothing', mark='RPM')
        tr = r['laps'][0]['track']
        tt = np.asarray(tr['t'])
        bad = np.asarray(tr['bad'], bool)
        self.assertTrue(bad[(tt > 10.2) & (tt < 11.8)].all())         # 精度が悪い所
        self.assertTrue(bad[(tt > 29.95) & (tt < 30.95)].all())       # 測位が1秒抜けた所
        self.assertFalse(bad[(tt > 40) & (tt < 50)].any())
        self.assertEqual(r['laps'][0]['paint_error'], 'この走行に無いチャンネル')
        self.assertTrue(all(v is None for v in tr['v']))
        self.assertTrue(any(m['k'] == 'max' for m in r['laps'][0]['marks']))   # 回転数の山

    def test_brakes_from_on_off_channel(self):
        t = np.arange(0, 10, 0.05)
        b = ((t > 2) & (t < 3)) | ((t > 6) & (t < 6.05))               # 2つめは1点だけ（チャタリング）
        self.assertEqual([(round(a, 2), round(c, 2)) for a, c in overlay.brakes(t, brake=b)], [(2.05, 3.0)])
        ax = np.where((t > 4) & (t < 4.2), -0.5, 0.0)                  # −0.25 G を下回るのが0.2秒だけ：ブレーキにしない
        self.assertEqual(overlay.brakes(t, ax=ax), [])

    def test_chosen_reference_and_no_gnss(self):
        r = overlay.compare(self.items(), ['Speed'], ref=2)
        self.assertEqual(r['ref'], 2)
        self.assertTrue(r['laps'][2]['ref'])
        self.s.gnss = None
        r = overlay.compare(self.items(), ['Speed'])
        self.assertFalse(r['distance'])
        self.assertIsNone(r['laps'][0]['s'])
        self.assertEqual(overlay.compare([], ['Speed'])['laps'], [])


if __name__ == '__main__':
    unittest.main()
