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


if __name__ == '__main__':
    unittest.main()
