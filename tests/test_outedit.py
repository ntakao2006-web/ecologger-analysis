"""コース図のエディタ（web/outedit.js）の道具を、node で動かして確かめる（node が無ければ飛ばす）"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / 'web' / 'outedit.js'
NODE = shutil.which('node')

# 座標は m のまま（toXY・toLatLon を素通しにする）。100 m 四方の輪を2 mおきの点で
SCRIPT = r'''
const fs = require('fs'), vm = require('vm');
const ctx = {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[process.argv.length - 1], 'utf8') + '\nthis.OutlineEditor = OutlineEditor;', ctx);
const E = ctx.OutlineEditor, out = {};
const sq = [];
for (let i = 0; i < 50; i++) sq.push([i * 2, 0]);
for (let i = 0; i < 50; i++) sq.push([100, i * 2]);
for (let i = 0; i < 50; i++) sq.push([100 - i * 2, 100]);
for (let i = 0; i < 50; i++) sq.push([0, 100 - i * 2]);
const ring = sq.concat([sq[0]]);
const id = (a, b) => [a, b];
const geo = { k: 4, nearestTrack: (x, y) => [50, 50] };      // 走った線は輪の真ん中
const ui = { radius: () => 10, zebraW: () => 1, zebraTaper: () => 4, boardText: () => '100', onChange: () => {} };
const fresh = () => E.load({ lines: [ring], kerbs: [], boards: [] }, id, ui);

fresh();
out.loaded = E.counts();
let d = E.dump(id);
out.closedKept = d.lines[0].length === 201 && d.lines[0][0].join() === d.lines[0][200].join();

// まっすぐ：下の辺の (20,0) と (60,0) のあいだにこぶを作ってから、まっすぐにする
E.setTool('move');
E.down(40, 0, geo); E.move(40, 5); E.up();
d = E.dump(id);
out.moved = d.lines[0].find((p) => p[0] === 40)[1];
out.movedNear = d.lines[0].find((p) => p[0] === 36)[1];
out.movedFar = d.lines[0].find((p) => p[0] === 60)[1];
E.setTool('straight');
E.click(20, 0, geo); E.click(60, 0, geo);
d = E.dump(id);
out.straightMaxY = Math.max(...d.lines[0].filter((p) => p[0] > 20 && p[0] < 60 && p[1] < 50).map((p) => p[1]));
out.undoBack = E.undo() && Math.abs(E.dump(id).lines[0].find((p) => p[0] === 40)[1] - 5) < 1e-9;

// 消す：輪の下の辺を消すと、開いた1本になる。もう一度押すと2本に分かれる
fresh();
E.setTool('erase');
E.click(20, 0, geo); E.click(60, 0, geo);
d = E.dump(id);
out.eraseRing = { n: d.lines.length, first: d.lines[0][0], last: d.lines[0][d.lines[0].length - 1] };
E.click(100, 40, geo); E.click(100, 60, geo);
out.eraseOpen = E.dump(id).lines.length;

// つなぐ：分かれた2本の端どうし、さらに両端で輪に戻す
E.setTool('join');
E.click(100, 40, geo); E.click(100, 60, geo);
out.joinTwo = E.dump(id).lines.length;
E.click(20, 0, geo); E.click(60, 0, geo);
d = E.dump(id);
out.joinRing = d.lines.length === 1 && d.lines[0][0].join() === d.lines[0][d.lines[0].length - 1].join();

// ゼブラ：下の辺（20〜60）に足すと、内側の端は縁の上（y = 0）、外は輪の外（side で y < 0 の向き）、端は 4 m で細る
const zx = (z, x) => z.pts.findIndex((p) => Math.abs(p[0] - x) < 1e-9);
fresh();
E.setTool('zebraAdd');
E.click(20, 0, geo); E.click(60, 0, geo);
d = E.dump(id);
let z = d.kerbs[0];
out.zebra = { n: d.kerbs.length, side: z.side, ys: [...new Set(z.pts.map((p) => Math.round(p[1] * 100) / 100))],
              ws: [z.ws[0], z.ws[1], z.ws[10], z.ws[z.ws.length - 2], z.ws[z.ws.length - 1]] };
// 縁を動かすと一緒に動く：(40,0) を 3 m 上へ（なめらかさ 10 m）
E.setTool('move');
E.down(40, 0, geo); E.move(40, 3); E.up();
z = E.view().kerbs[0];
const lineAt = (x) => E.view().lines[0].pts.find((p) => Math.abs(p[0] - x) < 1e-9);
out.glueMove = { z40: z.pts[zx(z, 40)][1], l40: lineAt(40)[1], z36: z.pts[zx(z, 36)][1], l36: lineAt(36)[1] };
// まっすぐにすると、ゼブラも縁へ載り直す
E.setTool('straight'); E.click(20, 0, geo); E.click(60, 0, geo);
z = E.view().kerbs[0];
out.glueStraight = Math.max(...z.pts.map((p) => Math.abs(p[1])));
// 外側の端をつかむと幅が変わる：x=40 の外側（y = -1）を y = -2.5 へ
E.setTool('move');
E.down(40, -1, geo); E.move(40, -2.5); E.up();
z = E.view().kerbs[0];
out.width = { w40: z.ws[zx(z, 40)], w44: z.ws[zx(z, 44)], w56: z.ws[zx(z, 56)], onEdge: Math.max(...z.pts.map((p) => Math.abs(p[1]))) };
// 端（60,0）をつかんで縁に沿って 80 まで伸ばす：細りは端と一緒に動き、伸ばした所は手前の幅で埋める
E.down(60, 0, geo); E.move(80, 0.4); E.up();
z = E.view().kerbs[0];
out.slide = { last: z.pts[z.pts.length - 1], wLast: z.ws[z.ws.length - 1], wNear: z.ws[z.ws.length - 2], w70: z.ws[zx(z, 70)], w40: z.ws[zx(z, 40)], first: z.pts[0] };
E.down(80, 0, geo); E.move(30, 0); E.up();          // 縮める
z = E.view().kerbs[0];
out.shrink = { last: z.pts[z.pts.length - 1], wLast: z.ws[z.ws.length - 1] };
// ゼブラの一部を消すと2本に分かれ、幅も分かれる
E.setTool('erase'); E.click(24, -0.5, geo); E.click(26, -0.5, geo);
out.zebraErase = E.view().kerbs.map((k) => [k.pts.length, k.ws.length]);
E.setTool('zebraDel');
E.click(22, -0.3, geo);
E.click(28, -0.5, geo);
out.zebraDel = E.dump(id).kerbs.length;
// 前の形 {w, pts（帯の真ん中）} を読むと、縁に近い側を内側にする
E.load({ lines: [ring], kerbs: [{ w: 1, pts: [[20, -0.5], [40, -0.5], [60, -0.5]] }], boards: [] }, id, ui);
z = E.view().kerbs[0];
out.legacy = { ys: z.pts.map((p) => Math.round(p[1] * 1e6) / 1e6), ws: z.ws, side: z.side };

// 看板：押すと置き、もう一度押すと消す
E.setTool('board');
E.click(30, 10, geo);
out.board = E.dump(id).boards;
E.click(30, 10, geo);
out.boardDel = E.dump(id).boards.length;
out.dirty = E.dirty();

// ならす：半径 30 m の円（2 mおき）に ±0.3 m のガタガタを足す。まるごとならすと、ガタガタは減り、半径は保つ
const R0 = 30, N = Math.round(2 * Math.PI * R0 / 2), circ = [];
for (let i = 0; i < N; i++) { const a = 2 * Math.PI * i / N, e = 0.3 * (i % 2 ? 1 : -1); circ.push([(R0 + e) * Math.cos(a), (R0 + e) * Math.sin(a)]); }
const rads = (pts) => pts.map((p) => Math.hypot(p[0], p[1]));
const sd = (v) => { const m = v.reduce((s, x) => s + x, 0) / v.length; return Math.sqrt(v.reduce((s, x) => s + (x - m) ** 2, 0) / v.length); };
const mean = (v) => v.reduce((s, x) => s + x, 0) / v.length;
E.load({ lines: [circ.concat([circ[0]])], kerbs: [], boards: [] }, id, ui);
E.setTool('smooth');
E.click(R0, 0, geo); E.click(R0, 0, geo);
let r = rads(E.view().lines[0].pts);
out.smoothRing = { sdBefore: sd(rads(circ)), sdAfter: sd(r), meanAfter: mean(r) };
// 区間だけ：円を開いた線にして、区間の外は動かず、区間の両端も動かない
const arc = circ.slice(0, 60);
E.load({ lines: [arc], kerbs: [], boards: [] }, id, ui);
E.setTool('smooth');
E.click(...arc[15], geo); E.click(...arc[45], geo);
const after = E.view().lines[0].pts, moved = after.map((p, i) => Math.hypot(p[0] - arc[i][0], p[1] - arc[i][1]));
out.smoothSpan = { outside: Math.max(...moved.slice(0, 16), ...moved.slice(45)), inside: sd(rads(after.slice(22, 39))), insideBefore: sd(rads(arc.slice(22, 39))) };
out.smoothUndo = E.undo() && E.view().lines[0].pts.every((p, i) => p[0] === arc[i][0] && p[1] === arc[i][1]);
// やり直す：戻した分をもう一度。新しく直すと、やり直しの分は消える
out.redo = { can: E.canRedo(), done: E.redo(), moved: E.view().lines[0].pts[30][0] !== arc[30][0], after: E.canRedo() };
E.undo();
E.setTool('straight'); E.click(...arc[10], geo); E.click(...arc[20], geo);
out.redoCleared = !E.canRedo();
// 押しかけの取り消しと、ジェスチャーの道具の並び
E.setTool('erase'); E.click(...arc[5], geo);
out.cancel = [E.cancelPending(), E.cancelPending()];
out.gesture = E.GESTURE.map((g) => g[0]);

// 原点が変わったとき：点も戻す分も移る
fresh();
E.setTool('board'); E.click(30, 10, geo);
E.remap((x, y) => [x + 5, y - 5]);
out.remap = E.dump(id).boards[0];
E.undo();
out.remapUndo = E.dump(id).lines[0][0];
console.log(JSON.stringify(out));
'''


@unittest.skipUnless(NODE, 'node が無い')
class OutlineEditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, '-e', SCRIPT, str(JS)], capture_output=True, text=True, encoding='utf-8', timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr)
        cls.o = json.loads(r.stdout)

    def test_load_keeps_ring_closed(self):
        self.assertEqual(self.o['loaded'], {'lines': 1, 'points': 200, 'kerbs': 0, 'boards': 0})
        self.assertTrue(self.o['closedKept'])

    def test_move_is_soft_and_straighten_and_undo(self):
        self.assertAlmostEqual(self.o['moved'], 5)
        self.assertTrue(0 < self.o['movedNear'] < 5)          # 近くの点は少しだけ
        self.assertEqual(self.o['movedFar'], 0)                # なめらかさの外は動かない
        self.assertLess(self.o['straightMaxY'], 1e-9)
        self.assertTrue(self.o['undoBack'])

    def test_erase_and_join(self):
        self.assertEqual(self.o['eraseRing'], {'n': 1, 'first': [60, 0], 'last': [20, 0]})
        self.assertEqual(self.o['eraseOpen'], 2)
        self.assertEqual(self.o['joinTwo'], 1)
        self.assertTrue(self.o['joinRing'])

    def test_zebra_goes_outside_and_board(self):
        z = self.o['zebra']
        self.assertEqual(z['n'], 1)
        self.assertEqual(z['side'], -1)                        # 下の辺を右へ進む線の右（y < 0）が外
        self.assertEqual(z['ys'], [0])                         # 内側の端は縁の上
        self.assertEqual(z['ws'], [0, 0.5, 1, 0.5, 0])          # 端は 4 m で 0 まで細る（点は 2 m おき）
        self.assertEqual(self.o['zebraDel'], 0)
        self.assertEqual(self.o['board'], [{'lat': 30, 'lon': 10, 'text': '100'}])
        self.assertEqual(self.o['boardDel'], 0)
        self.assertTrue(self.o['dirty'])

    def test_zebra_follows_edge(self):
        g = self.o['glueMove']
        self.assertAlmostEqual(g['z40'], 3)                    # 縁と同じだけ動く
        self.assertAlmostEqual(g['l40'], 3)
        self.assertAlmostEqual(g['z36'], g['l36'])
        self.assertTrue(0 < g['z36'] < 3)
        self.assertLess(self.o['glueStraight'], 1e-9)          # まっすぐにした縁へ載り直す

    def test_zebra_width_and_slide(self):
        w = self.o['width']
        self.assertAlmostEqual(w['w40'], 2.5)
        self.assertTrue(1 < w['w44'] < 2.5)                    # なめらかさの距離の内は少しずつ
        self.assertAlmostEqual(w['w56'], 1)                    # 外は変わらない
        self.assertLess(w['onEdge'], 1e-9)                     # 内側の端は縁から離れない
        s = self.o['slide']
        self.assertAlmostEqual(s['last'][0], 80, places=6)
        self.assertAlmostEqual(s['last'][1], 0, places=9)      # 縁の上を伸びる
        self.assertEqual(s['first'], [20, 0])                  # 反対の端は動かない
        self.assertAlmostEqual(s['wLast'], 0)                  # 細りは端と一緒
        self.assertAlmostEqual(s['wNear'], 0.5)
        self.assertAlmostEqual(s['w70'], 1)                    # 伸ばした所は手前の幅
        self.assertAlmostEqual(s['w40'], 2.5)                  # 途中の幅はそのまま
        self.assertAlmostEqual(self.o['shrink']['last'][0], 30, places=6)
        self.assertAlmostEqual(self.o['shrink']['wLast'], 0)
        self.assertEqual(len(self.o['zebraErase']), 2)         # 2本に分かれ、幅の数も点と同じ
        self.assertTrue(all(a == b for a, b in self.o['zebraErase']))

    def test_zebra_legacy_shape(self):
        lg = self.o['legacy']
        self.assertEqual(lg['ys'], [0, 0, 0])                  # 帯の真ん中から縁の側へ w/2
        self.assertEqual(lg['ws'], [1, 1, 1])
        self.assertEqual(lg['side'], -1)

    def test_smooth_keeps_curve(self):
        s = self.o['smoothRing']
        self.assertLess(s['sdAfter'], s['sdBefore'] / 5)       # ガタガタは 1/5 より小さく
        self.assertAlmostEqual(s['meanAfter'], 30, delta=0.1)  # カーブは内へ縮まない
        p = self.o['smoothSpan']
        self.assertEqual(p['outside'], 0)                      # 区間の外と両端は動かない
        self.assertLess(p['inside'], p['insideBefore'] / 3)
        self.assertTrue(self.o['smoothUndo'])

    def test_redo_cancel_and_gesture_order(self):
        self.assertEqual(self.o['redo'], {'can': True, 'done': True, 'moved': True, 'after': False})
        self.assertTrue(self.o['redoCleared'])
        self.assertEqual(self.o['cancel'], [True, False])
        # 8つの道具が1つずつ、ボタン・数字キーと同じ順
        self.assertEqual(self.o['gesture'], ['move', 'smooth', 'straight', 'join', 'erase', 'zebraAdd', 'zebraDel', 'board'])

    def test_remap(self):
        self.assertEqual(self.o['remap'], {'lat': 35, 'lon': 5, 'text': '100'})
        self.assertEqual(self.o['remapUndo'], [5, -5])


if __name__ == '__main__':
    unittest.main()
