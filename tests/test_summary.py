"""走行のまとめ（web/summary.js、SP-10-40）を node で確かめる（node が無ければ飛ばす）"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / 'web' / 'summary.js'
NODE = shutil.which('node')

SCRIPT = r'''
const S = require(process.argv[process.argv.length - 1]);
const lap = (t, sp, gap) => ({ time_s: t, splits: sp, gap: !!gap });
const a = { laps: [lap(112.0, [40.0, 37.0, 35.0]), lap(110.5, [39.5, 36.2, 34.8]), lap(111.1, [39.0, 36.9, 35.2]),
                   lap(108.0, [38.0, 35.0, 35.0], true),         // 測位の抜けた周は使わない
                   lap(125.0, [45, 40, 40]), lap(111.8, [39.8, 36.6, 35.4])], rolling: { time_s: 110.2 } };
const b = { laps: [lap(109.9, [38.9, 36.1, 34.9])], rolling: { time_s: 109.8 } };
const out = { one: S.of([a]), two: S.of([a, b]), none: S.of([{ laps: [lap(100, null, true)] }]),
              pit: S.of([{ laps: [lap(132.5), lap(129.5), lap(139.2), lap(160.6), lap(681.0)] }]),
              sectors: S.of([{ laps: [{ time_s: 60, sectors: [30, 30] }, { time_s: 61, sectors: [29, 32] }] }]),
              v: [0.3, 0.5, 0.8, 1.5, 2.5].map((r) => S.of([{ laps: [lap(100), lap(100 + r / 2), lap(100 + r)] }]).verdict.text) };
const sec = (brake, kmh, at, en, ex) => ({ brake_m: brake, min_kmh: kmh, min_at_m: at, entry: en, exit: ex });
const L = (lap, t, sp, secs) => ({ lap, time_s: t, splits: sp, sec: secs });
const r = { id: 's1', laps: [
  L(1, 60.0, [20.0, 20.0, 20.0], [sec(null, 80, 10, 100, 90), sec(5, 70, 30, 120, 110), sec(0, 60, 40, 130, 120)]),   // ベストの周
  L(2, 60.6, [19.6, 20.4, 20.6], [sec(null, 82, 12, 101, 92), sec(0, 68, 31, 119, 108), sec(2, 59, 41, 128, 118)]),
  L(3, 60.4, [20.3, 19.9, 20.2], [sec(null, 79, 9, 99, 91), sec(9, 72, 26, 121, 112), sec(1, 61, 39, 131, 121)]),
  L(4, 90.0, [30, 30, 30], [null, null, null])] };                   // 詰まった周：平均の周からは区間ごとに外れる
out.oppBest = S.opportunities([r], 'best');
out.oppMean = S.opportunities([r], 'mean');
out.oppNone = S.opportunities([{ id: 'x', laps: [L(1, 60, [30, 30], [])] }], 'best');
// 区間の中の損得：0〜300 m。比べる周は 100〜150 m（ブレーキ）で 0.1 秒、200〜300 m（立ち上がり）で 0.2 秒、ベストの周より遅れる
const grid = [], pd = [], bd = [];
for (let x = 0; x <= 400; x += 2) {
  grid.push(x); bd.push(0.01 * x / 100);
  pd.push(0.01 * x / 100 + 0.1 * Math.min(Math.max((x - 100) / 50, 0), 1) + 0.2 * Math.min(Math.max((x - 200) / 100, 0), 1));
}
const mk = [{ k: 'on', s: 90 }, { k: 'off', s: 160 }, { k: 'min', s: 180 }];
out.ph = S.phases(grid, pd, bd, mk, 50, 300, 140);        // ボトム 190 m
out.phTrail = S.phases(grid, pd, bd, mk, 50, 300, 60);    // ボトム 110 m（ブレーキを離す前）
out.phNone = S.phases(grid, pd, bd, [], 50, 300, null);
console.log(JSON.stringify(out));
'''


@unittest.skipUnless(NODE, 'node が無い')
class SummaryTest(unittest.TestCase):
    def test_summary(self):
        r = subprocess.run([NODE, '-e', SCRIPT, str(JS)], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout)
        one = o['one']
        self.assertEqual(one['n'], 5)
        self.assertAlmostEqual(one['best'], 110.5)
        self.assertEqual(one['theoryKind'], 'splits')
        self.assertAlmostEqual(one['theory'], 39.0 + 36.2 + 34.8)
        self.assertAlmostEqual(one['gapToTheory'], 0.5)
        self.assertFalse(one['bigGap'])
        self.assertAlmostEqual(one['rolling'], 110.2)
        self.assertAlmostEqual(one['top3']['mean'], (110.5 + 111.1 + 111.8) / 3)
        self.assertAlmostEqual(one['top3']['range'], 1.3)
        self.assertEqual(one['verdict']['text'], '安定が最優先')
        self.assertAlmostEqual(one['top5']['range'], 125.0 - 110.5)   # 125 秒はベストの130%より速いので入る
        self.assertIsNone(o['pit']['top5'])          # 681 秒（ピット）の周は上位の平均に入れない
        self.assertEqual(o['pit']['top3']['n'], 3)
        two = o['two']                               # 同じコースのほかの走行も使う
        self.assertEqual(two['n'], 6)
        self.assertAlmostEqual(two['best'], 109.9)
        self.assertAlmostEqual(two['theory'], 38.9 + 36.1 + 34.8)
        self.assertAlmostEqual(two['rolling'], 109.8)
        self.assertEqual(o['none']['n'], 0)
        self.assertIsNone(o['none']['best'])
        self.assertIsNone(o['none']['top3'])
        self.assertEqual(o['sectors']['theoryKind'], 'sectors')
        self.assertAlmostEqual(o['sectors']['theory'], 59.0)
        self.assertTrue(o['sectors']['bigGap'] is False and o['sectors']['top3'] is None)
        self.assertEqual(o['v'], ['上出来', '上出来', 'まあまあ', '安定が最優先', 'わけを探す'])

    def test_opportunities(self):
        r = subprocess.run([NODE, '-e', SCRIPT, str(JS)], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout)
        b = o['oppBest']
        self.assertEqual(b['partnerLap'], {'id': 's1', 'lap': 1})
        self.assertEqual([x['i'] for x in b['items']], [0, 1])              # 区間3はベストの周がベスト（伸びしろ0）なので出さない
        first = b['items'][0]
        self.assertAlmostEqual(first['loss'], 0.4)
        self.assertEqual(first['bestLap'], {'id': 's1', 'lap': 2})
        self.assertIsNone(first['brake'])                                   # 区間1（コントロールラインから）はブレーキを比べない
        self.assertAlmostEqual(first['apexKmh'], 2)
        self.assertAlmostEqual(first['exit'], 2)
        second = b['items'][1]
        self.assertAlmostEqual(second['loss'], 0.1)
        self.assertAlmostEqual(second['brake'], 4)                          # 3周は 4 m 奥で踏んだ
        self.assertAlmostEqual(second['apexAt'], -4)
        self.assertAlmostEqual(b['total'], 0.5)
        m = o['oppMean']                                                    # 平均の周：90秒の周は区間ごとに外れる
        self.assertIsNone(m['partnerLap'])
        self.assertEqual(len(m['items']), 3)
        self.assertEqual([x['i'] for x in m['items']], [0, 2, 1])       # 0.367・0.267・0.200 秒
        top = m['items'][1]
        self.assertAlmostEqual(top['partnerTime'], (20.0 + 20.6 + 20.2) / 3)
        self.assertAlmostEqual(top['brake'], 0 - 1.0)                       # ベストの周 0 m、平均 (0+2+1)/3
        self.assertEqual(o['oppNone']['items'], [])
        ph = o['ph']
        self.assertEqual([(x['a'], x['z']) for x in ph], [(50, 160), (160, 190), (190, 300)])
        self.assertAlmostEqual(ph[0]['lost'], 0.1, places=6)
        self.assertAlmostEqual(ph[1]['lost'], 0.0, places=6)
        self.assertAlmostEqual(ph[2]['lost'], 0.2, places=6)
        self.assertIsNone(o['phTrail'][1]['lost'])                          # 旋回は長さ0
        self.assertAlmostEqual(o['phTrail'][0]['lost'] + o['phTrail'][2]['lost'], 0.3, places=6)
        self.assertIsNone(o['phNone'])


if __name__ == '__main__':
    unittest.main()
