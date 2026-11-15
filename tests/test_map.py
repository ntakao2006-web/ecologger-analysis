"""コース図（web/map.js）を node で、偽のキャンバスに描かせて確かめる（node が無ければ飛ばす）。

2026-11-15：枠の境目を寄せて図が余白より小さくなると、航空写真の倍率が NaN になり、
タイルの段を決めるループが終わらずに画面が固まった。どの大きさでも描き終わることを確かめる。
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / 'web' / 'map.js'
NODE = shutil.which('node')

SCRIPT = r'''
const fs = require('fs'), vm = require('vm');
const noop = () => {};
const ctx2d = new Proxy({}, { get: (t, k) => (k in t ? t[k] : k === 'measureText' ? () => ({ width: 10 }) : noop), set: (t, k, v) => { t[k] = v; return true; } });
class Img { set src(v) { this._src = v; } }
const cv = { clientWidth: 400, clientHeight: 300, width: 0, height: 0, style: {}, addEventListener: noop, getContext: () => ctx2d,
             getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight, right: this.clientWidth, bottom: this.clientHeight }; } };
const sandbox = { window: { devicePixelRatio: 1.5, addEventListener: noop }, document: { body: {} }, Image: Img, requestAnimationFrame: noop,
                  getComputedStyle: () => ({ getPropertyValue: () => '#111827' }), console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[process.argv.length - 1], 'utf8') + '\nthis.CourseMap = CourseMap;', sandbox);
const M = sandbox.CourseMap;
// 半径 300 m の円を1周（SUGO の辺り）
const n = 400, x = [], y = [], s = [], t = [];
for (let i = 0; i < n; i++) { const a = 2 * Math.PI * i / n; x.push(300 * Math.cos(a)); y.push(300 * Math.sin(a)); s.push(i * 2 * Math.PI * 300 / n); t.push(i * 0.25); }
const lap = { key: 'a:1', ref: true, label: '1周', track: { x, y, s, t } };
M.init(cv, { x: () => 'dist', color: () => '#3b82f6', lines: () => null, editing: () => false, onPick: noop, onPlace: noop,
             photo: () => true, paint: () => ({ mode: 'lap', marks: false }), onFollow: noop });
M.show({ origin: [38.14, 140.78], laps: [lap] });
M.setCursor(500);
const out = [];
for (const [w, h] of [[400, 300], [400, 40], [400, 32], [400, 20], [400, 0], [10, 300], [0, 0], [400, 300]]) {
  cv.clientWidth = w; cv.clientHeight = h;
  M.redraw();
  M.setFollow(true); M.setCursor(800); M.setFollow(false);
  out.push([w, h]);
}
console.log(JSON.stringify({ done: out.length }));
'''


@unittest.skipUnless(NODE, 'node が無い')
class CourseMapTest(unittest.TestCase):
    def test_tiny_canvas_does_not_hang(self):
        # 終わらないループがあれば、ここで時間切れになる
        r = subprocess.run([NODE, '-e', SCRIPT, str(JS)], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {'done': 8})


if __name__ == '__main__':
    unittest.main()
