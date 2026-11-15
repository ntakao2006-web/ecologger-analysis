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


# 2026-11-16：車を追っているとき、押した所が車から離れている分だけ線に沿って先へ送る（デジスパイスの送り方）
STEP = r"""
const fs = require('fs'), vm = require('vm');
const noop = () => {};
const ctx2d = new Proxy({}, { get: (t, k) => (k in t ? t[k] : k === 'measureText' ? () => ({ width: 10 }) : noop), set: (t, k, v) => { t[k] = v; return true; } });
const on = { cv: {}, win: {} };
const add = (o) => (k, f) => { (o[k] = o[k] || []).push(f); };
const cv = { clientWidth: 400, clientHeight: 300, width: 0, height: 0, style: {}, addEventListener: add(on.cv), getContext: () => ctx2d,
             getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 300, right: 400, bottom: 300 }; } };
const sandbox = { window: { devicePixelRatio: 1, addEventListener: add(on.win) }, document: { body: {} }, Image: class {}, requestAnimationFrame: noop,
                  getComputedStyle: () => ({ getPropertyValue: () => '#111827' }), console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[process.argv.length - 1], 'utf8') + '\nthis.CourseMap = CourseMap;', sandbox);
const M = sandbox.CourseMap;
const n = 400, x = [], y = [], s = [], t = [];
for (let i = 0; i < n; i++) { const a = 2 * Math.PI * i / n; x.push(300 * Math.cos(a)); y.push(300 * Math.sin(a)); s.push(i * 2 * Math.PI * 300 / n); t.push(i * 0.25); }
const lap = { key: 'a:1', ref: true, label: '1周', track: { x, y, s, t } };
let axis = 'dist';
M.init(cv, { x: () => axis, color: () => '#3b82f6', lines: () => null, editing: () => false, onPick: (v) => M.setCursor(v), onPlace: noop,
             photo: () => false, paint: () => ({ mode: 'lap', marks: false }), onFollow: noop });
M.show({ origin: [38.14, 140.78], laps: [lap] });
let cur = null;
const pick0 = M.setCursor; M.setCursor = (v) => { cur = v; pick0(v); };
M.setFollow(true); M.setCursor(500);          // 追うと 250 m の幅へ寄る：1 m = min(400, 300 - 32)/250 画素
const k = 268 / 250;
const click = (dx, dy, shift) => {
  const e = { button: 0, clientX: 200 + dx, clientY: 150 + dy, shiftKey: !!shift, preventDefault: noop };
  on.cv.mousedown.forEach((f) => f(e)); on.win.mouseup.forEach((f) => f(e));
  return cur;
};
const out = {};
out.far = click(100 * k, 0);                  // 車から 100 m 離れた所 → 100 m 先
out.side = click(0, -30 * k);                 // 向きは関係なく、離れている分だけ
out.near = click(5 * k, 0);                   // 車の印の上（つかめる範囲）を押しただけでも少し進む
out.back = click(40 * k, 0, true);            // Shift で手前へ
M.setCursor(1850); out.end = click(100 * k, 0);   // 周の端で止まる
axis = 'time'; M.setCursor(10); out.time = click(100 * k, 0);   // 時間の横軸でも、線に沿って 100 m（1点 0.25 s・2π·300/400 m）
M.setFollow(false); M.setCursor(500); axis = 'dist'; out.notFollowing = click(100 * k, 0);   // 追っていなければ前のとおり（近くに線が無いので動かない）
out.along = M.alongBy(lap.track, 't', 10, 100);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, 'node が無い')
class CourseMapTest(unittest.TestCase):
    def test_tiny_canvas_does_not_hang(self):
        # 終わらないループがあれば、ここで時間切れになる
        r = subprocess.run([NODE, '-e', SCRIPT, str(JS)], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {'done': 8})

    def test_click_steps_along_while_following(self):
        r = subprocess.run([NODE, '-e', STEP, str(JS)], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout)
        self.assertAlmostEqual(o['far'], 600, delta=0.5)
        self.assertAlmostEqual(o['side'], 630, delta=0.5)
        self.assertAlmostEqual(o['near'], 635, delta=0.5)
        self.assertAlmostEqual(o['back'], 595, delta=0.5)
        end = 399 * 2 * 3.141592653589793 * 300 / 400
        self.assertAlmostEqual(o['end'], end, delta=0.01)
        per = 2 * 3.141592653589793 * 300 / 400 / 0.25        # m/s
        self.assertAlmostEqual(o['time'], 10 + 100 / per, delta=0.01)
        self.assertAlmostEqual(o['along'], 10 + 100 / per, delta=0.01)
        self.assertEqual(o['notFollowing'], 500)


if __name__ == '__main__':
    unittest.main()
