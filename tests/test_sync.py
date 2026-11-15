"""カーソルと各周の時刻の対応（web/sync.js、SP-10-16）を node で確かめる（node が無ければ飛ばす）"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / 'web' / 'sync.js'
NODE = shutil.which('node')

SCRIPT = r'''
const fs = require('fs'), vm = require('vm');
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[process.argv.length - 1], 'utf8') + '\nthis.Sync = Sync;', sandbox);
const S = sandbox.Sync;
// 1000 m の周。基準は 10 m/s（100 秒）。比べる周は 500 m まで 10 m/s、そこから 5 m/s（200 秒で 1000 m の所…合わせて 150 秒）
const mk = (key, ref, f) => {
  const s = [], t = [];
  for (let d = 0; d <= 1000; d += 10) { s.push(d); t.push(f(d)); }
  return { key, ref, time_s: t[t.length - 1], track: { s, t, x: s, y: s.map(() => 0) } };
};
const ref = mk('a', true, (d) => d / 10);
const slow = mk('b', false, (d) => (d <= 500 ? d / 10 : 50 + (d - 500) / 5));
let axis = 'dist', align = 'lap', heads = [];
S.init({ x: () => axis, align: () => align, heads: () => heads });
const data = { laps: [ref, slow] };
S.setData(data);
const out = {};
out.space = S.lapTime(slow, 700, 'space');          // 700 m を通った時刻：50 + 40 = 90 秒
out.timeLap = S.lapTime(slow, 700, 'time');         // 基準が 700 m にいる 70 秒
out.axisDist = S.lapTime(slow, 700, 'axis');        // 距離の横軸なら同じ場所
heads = [600]; align = 'section'; S.setData(data);  // 区間の頭 600 m：基準 60 秒、比べる周 70 秒
out.aligning = S.aligning();
out.timeSec = S.lapTime(slow, 700, 'time');         // 基準は区間の頭から 10 秒 → 比べる周は 70 + 10 = 80 秒
out.before = S.lapTime(slow, 300, 'time');          // 区間の頭より前は周の頭でそろえる：30 秒
axis = 'time';
out.axisTime = S.lapTime(slow, 65, 'axis');         // 時間の横軸（基準の 65 秒）：70 + 5 = 75 秒
out.xOf = S.xOf(slow, 75);                          // その周の 75 秒 → 横軸 65
out.xOfBefore = S.xOf(slow, 40);
out.xOfRef = S.xOfRef(65);
out.outside = S.lapTime(slow, 400, 'time');         // 区間の頭から 340 秒 → 周の外（150 秒より後）は null
align = 'lap'; S.setData(data);
out.plainTime = S.lapTime(slow, 65, 'axis');
out.notAligning = S.aligning();
console.log(JSON.stringify(out));
'''


@unittest.skipUnless(NODE, 'node が無い')
class SyncTest(unittest.TestCase):
    def test_space_time_and_section_alignment(self):
        r = subprocess.run([NODE, '-e', SCRIPT, str(JS)], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout)
        self.assertAlmostEqual(o['space'], 90.0)
        self.assertAlmostEqual(o['timeLap'], 70.0)
        self.assertAlmostEqual(o['axisDist'], 90.0)
        self.assertTrue(o['aligning'])
        self.assertAlmostEqual(o['timeSec'], 80.0)
        self.assertAlmostEqual(o['before'], 30.0)
        self.assertAlmostEqual(o['axisTime'], 75.0)
        self.assertAlmostEqual(o['xOf'], 65.0)
        self.assertAlmostEqual(o['xOfBefore'], 40.0)
        self.assertAlmostEqual(o['xOfRef'], 65.0)
        self.assertIsNone(o['outside'])
        self.assertAlmostEqual(o['plainTime'], 65.0)
        self.assertFalse(o['notAligning'])


if __name__ == '__main__':
    unittest.main()
