"""印刷向けの1枚のレポート（web/report.js、SP-10-43）の計算の部分を node で確かめる（node が無ければ飛ばす）。
地図・グラフ・1枚に収まるかは、画面（Edge の印刷で A4 横1ページ）で確かめた"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / 'web' / 'report.js'
HTML = Path(__file__).resolve().parent.parent / 'web' / 'index.html'
NODE = shutil.which('node')

SCRIPT = r'''
const R = require(process.argv[process.argv.length - 1])._pure;
console.log(JSON.stringify({
  c0: R.speedColor(0), c1: R.speedColor(1), cmid: R.speedColor(0.5), clo: R.speedColor(-3), chi: R.speedColor(9),
  pct: [R.pct([5, 1, null, 3, 2, 4], 0), R.pct([5, 1, null, 3, 2, 4], 1), R.pct([5, 1, 3, 2, 4], 0.5), R.pct([null], 0.5)],
  step: [R.niceStep(0.13), R.niceStep(23), R.niceStep(380), R.niceStep(4.2)],
  gap: [R.shortGap(0.512), R.shortGap(-1.2), R.shortGap(0), R.shortGap(1473.2), R.shortGap(99.9)],
}));
'''


@unittest.skipUnless(NODE, 'node が無い')
class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, '-e', SCRIPT, str(JS)], capture_output=True, text=True, encoding='utf-8', check=True)
        cls.r = json.loads(out.stdout)

    def test_speed_colors(self):
        r = self.r
        self.assertEqual(r['c0'], 'rgb(37,99,235)')     # 遅い＝青
        self.assertEqual(r['c1'], 'rgb(220,38,38)')     # 速い＝赤
        self.assertEqual(r['cmid'], 'rgb(245,158,11)')  # 真ん中＝黄
        self.assertEqual(r['clo'], r['c0'])             # 段の外は端の色
        self.assertEqual(r['chi'], r['c1'])

    def test_percentile_skips_missing(self):
        self.assertEqual(self.r['pct'], [1, 5, 3, None])

    def test_nice_steps(self):
        self.assertEqual(self.r['step'], [0.1, 20, 500, 5])

    def test_short_gap(self):
        # 100秒を超える差（赤旗・ピットで止まった周）は秒の整数にして、周の表の欄に収める
        self.assertEqual(self.r['gap'], ['+0.512', '−1.200', '0.000', '+1473', '+99.900'])


class PageTest(unittest.TestCase):
    def test_loaded_before_app(self):
        html = HTML.read_text(encoding='utf-8')
        self.assertIn('id="reportbtn"', html)
        self.assertLess(html.index('src="report.js"'), html.index('src="app.js"'))


if __name__ == '__main__':
    unittest.main()
