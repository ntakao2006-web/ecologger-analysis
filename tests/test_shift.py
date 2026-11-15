"""走行ごとの線の平行移動（SP-10-17）：手元に残す・重ね合わせにかける・基準の走行に合わせる"""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import laps, overlay, server, session, shifts  # noqa: E402
from ecoanalysis.sample import write_sample  # noqa: E402

M = 111320.0


def move(s, e, n):
    """走行の測位を東へ e・北へ n m ずらす（別の日の測位のずれの作りもの）"""
    g = s.gnss
    lat = np.asarray(g['lat'], float)
    g['lon'] = np.asarray(g['lon'], float) + e / (M * math.cos(math.radians(float(np.nanmean(lat)))))
    g['lat'] = lat + n / M


class ShiftsTest(unittest.TestCase):
    def test_keep_round_and_forget_zero(self):
        with tempfile.TemporaryDirectory() as d:
            sh = shifts.Shifts(Path(d) / 'shifts.json')
            self.assertEqual(sh.get('a'), (0.0, 0.0))
            self.assertEqual(sh.set('a', 0.123, -1.0049), (0.12, -1.0))
            self.assertEqual(shifts.Shifts(Path(d) / 'shifts.json').get('a'), (0.12, -1.0))   # 手元に残る
            self.assertEqual(sh.how('a'), 'hand')
            sh.set('b', 2, 3, how='auto')
            self.assertEqual(sh.how('b'), 'auto')
            sh.set('a', 0, 0)                                    # 0 に戻した走行は消す
            self.assertNotIn('a', json.loads((Path(d) / 'shifts.json').read_text(encoding='utf-8')))
            for bad in ((51, 0), (0, -60), ('x', 0), (float('nan'), 0), (None, 1)):
                with self.assertRaises(shifts.ShiftError):
                    sh.set('c', *bad)


class OverlayShiftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.a = session.load(write_sample(root / 'a.ecorun'))
        self.b = session.load(write_sample(root / 'b.ecorun'))
        move(self.b, 1.5, -0.8)                     # 別の日：線が東へ 1.5 m・南へ 0.8 m ずれている
        self.la, _, _ = laps.for_session(self.a)
        self.lb, _, _ = laps.for_session(self.b)

    def tearDown(self):
        self.tmp.cleanup()

    def items(self, shift_b=(0.0, 0.0)):
        return ([{'key': 'a:2', 'label': 'a 2周', 'session': self.a, 'lap': self.la[1], 'shift': (0.0, 0.0)}] +
                [{'key': f'b:{x["lap"]}', 'label': f'b {x["lap"]}周', 'session': self.b, 'lap': x, 'shift': shift_b}
                 for x in self.lb[:2]])

    def median_off(self, r, i):
        return float(np.median(np.abs([v for v in r['laps'][i]['track']['off'] if v is not None])))

    def test_align_finds_the_shift_and_overlay_uses_it(self):
        r0 = overlay.compare(self.items(), [], ref=0)
        self.assertGreater(self.median_off(r0, 1), 0.5)            # ずれたまま：横のずれが 1 m 前後
        a = overlay.align(self.items(), 0, [1, 2])
        self.assertAlmostEqual(a['e'], -1.5, delta=0.05)
        self.assertAlmostEqual(a['n'], 0.8, delta=0.05)
        self.assertGreater(a['before'], 0.5)
        self.assertLess(a['after'], 0.05)
        self.assertEqual(a['laps'], 2)
        r1 = overlay.compare(self.items((a['e'], a['n'])), [], ref=0)
        self.assertLess(self.median_off(r1, 1), 0.05)              # 合わせると線が重なる
        # 合わせ直しても動かない（今の量から始める）
        a2 = overlay.align(self.items((a['e'], a['n'])), 0, [1, 2])
        self.assertAlmostEqual(a2['e'], a['e'], delta=0.01)
        self.assertEqual(a2['from'], [a['e'], a['n']])

    def test_shifting_the_reference_moves_every_offset(self):
        base = overlay.compare(self.items((-1.5, 0.8)), [], ref=0)
        it = self.items((-1.5, 0.8))
        it[0]['shift'] = (0.0, 2.0)                                # 基準だけを北へ 2 m
        r = overlay.compare(it, [], ref=0)
        self.assertTrue(np.allclose(np.array(r['laps'][0]['track']['y']) - np.array(base['laps'][0]['track']['y']), 2.0, atol=0.011))
        self.assertGreater(self.median_off(r, 1), 0.5)             # 比べる周は基準から離れる
        it = self.items((-1.5, 2.8))
        it[0]['shift'] = (0.0, 2.0)                                # 両方を同じだけ動かせば重なったまま
        self.assertLess(self.median_off(overlay.compare(it, [], ref=0), 1), 0.05)

    def test_track_adds_the_shift(self):
        tr = overlay.track(self.a.gnss, self.la[1]['start'], self.la[1]['end'], (0, 0), (3.0, 4.0))
        tr0 = overlay.track(self.a.gnss, self.la[1]['start'], self.la[1]['end'], (0, 0))
        self.assertTrue(np.allclose(tr[1] - tr0[1], 3.0) and np.allclose(tr[2] - tr0[2], 4.0))


class StoreShiftTest(unittest.TestCase):
    def test_set_auto_and_refuse_reference(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            st = server.Store(root / 'home')
            a = st.open(write_sample(root / 'a.ecorun'))
            b = st.open(write_sample(root / 'b.ecorun'))
            move(st.items[b['id']]['session'], -0.6, 1.2)
            picks = [{'id': a['id'], 'lap': 2}, {'id': b['id'], 'lap': 1}, {'id': b['id'], 'lap': 2}]
            r = st.auto_shift(b['id'], picks, 'app', 0)
            self.assertAlmostEqual(r['shift'][0], 0.6, delta=0.05)
            self.assertAlmostEqual(r['shift'][1], -1.2, delta=0.05)
            o = st.overlay(picks, 'app', [], 0)
            self.assertEqual([x['shift'] for x in o['laps']], [[0.0, 0.0], r['shift'], r['shift']])
            with self.assertRaises(ValueError):                    # 基準の周の走行は自動では動かさない
                st.auto_shift(a['id'], picks, 'app', 0)
            with self.assertRaises(ValueError):                    # その走行の周を選んでいない
                st.auto_shift(b['id'], picks[:1], 'app', 0)
            self.assertEqual(st.set_shift(a['id'], 0.3, 0)['shift'], [0.3, 0.0])
            self.assertEqual(st.overlay(picks, 'app', [], 0)['laps'][0]['shift'], [0.3, 0.0])
            with self.assertRaises(KeyError):
                st.set_shift('s99', 1, 1)
            with self.assertRaises(shifts.ShiftError):
                st.set_shift(a['id'], 99, 0)


if __name__ == '__main__':
    unittest.main()
