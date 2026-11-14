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
