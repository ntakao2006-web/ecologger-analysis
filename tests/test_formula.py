import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import formula as F, server  # noqa: E402
from ecoanalysis.sample import write_sample  # noqa: E402

T = np.arange(0, 10.001, 0.1)
CH = {'Speed': np.full_like(T, 36.0), 'Accel X': T * 2, 'Thr': np.where(T > 5, 100.0, 0.0)}
CH['Speed'][3] = np.nan


def run(expr, formulas=None):
    fs = dict(formulas or {})
    fs['x'] = (F.parse(expr), '')
    vals, why = F.compute(['x'], CH, T, slice(None), fs)
    if why:
        raise F.FormulaError(why['x'])
    return vals['x']


class EvaluateTest(unittest.TestCase):
    def test_arithmetic_and_precedence(self):
        self.assertEqual(run('1 + 2 * 3')[0], 7)
        self.assertEqual(run('(1 + 2) * 3')[0], 9)
        self.assertEqual(run('-2^2')[0], -4)           # べきが先
        self.assertEqual(run('2^3^2')[0], 512)         # べきは右から
        self.assertEqual(run('2^-1')[0], 0.5)
        self.assertEqual(run('7 % 3')[0], 1)
        self.assertEqual(run('1e3 + .5')[0], 1000.5)
        v = run('Speed / 3.6')
        self.assertEqual(v[0], 10)
        self.assertTrue(np.isnan(v[3]))                # 値の無い所は無いまま
        self.assertTrue(np.all(np.isnan(run('1 / 0'))))  # 0で割った所は無い

    def test_compare_logic_if(self):
        self.assertEqual(list(run('if(Thr > 95, 1, 0)')[[0, 60]]), [0, 1])
        self.assertEqual(list(run('Thr >= 100 and [Accel X] > 15')[[60, 90]]), [0, 1])
        self.assertEqual(list(run('not (Thr > 5) || 0')[[0, 60]]), [1, 0])
        self.assertTrue(np.isnan(run('Speed > 1')[3]))  # 比べる値が無ければ無い
        self.assertTrue(np.isnan(run('if(Speed > 1, 1, 0)')[3]))

    def test_functions(self):
        self.assertAlmostEqual(run('round(pi, 2)')[0], 3.14)
        self.assertEqual(list(run('max(Thr, 50, 20)')[[0, 60]]), [50, 100])
        self.assertEqual(list(run('clamp([Accel X], 2, 4)')[[0, 20, 99]]), [2, 4, 4])
        self.assertAlmostEqual(run('deg(atan2(1, 1))')[0], 45)
        self.assertAlmostEqual(run('g0')[0], 9.80665)
        self.assertEqual(list(run('time')[[0, -1]]), [0, 10])

    def test_time_functions(self):
        self.assertAlmostEqual(run('deriv([Accel X])')[50], 2.0)
        self.assertAlmostEqual(run('integ(Speed / 3.6)')[-1], 98.0)   # 10m/s×10秒、値の無い2区間（0.2秒）は足さない
        self.assertAlmostEqual(run('smooth(Thr, 1)')[50], 100 * 5 / 11)  # 4.5〜5.5秒の11点のうち5点が100

    def test_calc_channels_can_use_each_other(self):
        fs = {'ms': (F.parse('Speed / 3.6'), 'm/s')}
        self.assertEqual(run('ms * 2', fs)[0], 20)
        with self.assertRaises(F.FormulaError):
            run('Nope + 1')

    def test_rejects_anything_outside_the_grammar(self):
        for bad, pos in [('1 +', 3), ('Speed < 1 < 2', 10), ('foo(1)', 0), ('abs(1, 2)', 0), ('__import__("os")', 11),
                         ('smooth(Speed, Speed)', 14), ('a;b', 1), ('x = 1', 2), ('lambda: 1', 6), ('Speed[0]', 5), ('', 0)]:
            with self.assertRaises(F.FormulaError, msg=bad) as c:
                F.parse(bad)
            self.assertEqual(c.exception.pos, pos, bad)
        with self.assertRaises(F.FormulaError):
            F.parse('(' * 60 + '1' + ')' * 60)            # 深すぎる
        with self.assertRaises(F.FormulaError):
            F.parse('-' * 60 + '1')
        with self.assertRaises(F.FormulaError):
            F.parse('+'.join(['1'] * 400))                # 大きすぎる
        with self.assertRaises(F.FormulaError):
            F.parse('1' * 501)                            # 長すぎる
        self.assertEqual(F.refs(F.parse('Speed.__class__')), {'Speed.__class__'})   # 属性ではなく、ただの名前

    def test_save_rules(self):
        with tempfile.TemporaryDirectory() as d:
            fs = F.Formulas(Path(d) / 'channels.json')
            fs.save('ms', 'm/s', 'Speed / 3.6')
            fs.save('ms2', '', 'ms * 2')
            self.assertEqual([x['name'] for x in fs.list()], ['ms', 'ms2'])
            with self.assertRaises(F.FormulaError):
                fs.save('ms', '', '1')                     # 同じ名前
            with self.assertRaises(F.FormulaError):
                fs.save('Speed', '', '1', taken={'Speed'})  # 元のチャンネルと同じ名前
            with self.assertRaises(F.FormulaError):
                fs.save('abs', '', '1')
            with self.assertRaises(F.FormulaError):
                fs.save('[x]', '', '1')
            with self.assertRaises(F.FormulaError):
                fs.save('ms', 'm/s', 'ms2 / 2', old='ms')  # ぐるりと互いを使う
            with self.assertRaises(F.FormulaError):
                fs.save('kmh', 'km/h', 'Speed', old='ms')  # ms2 が使っているので名前を変えられない
            with self.assertRaises(F.FormulaError):
                fs.delete('ms')                            # 消せない
            fs.save('ms', 'm/s', 'Speed / 3.6 * 1', old='ms')   # 名前を変えずに直すのはよい
            fs.delete('ms2')
            fs.save('kmh', 'km/h', 'Speed', old='ms')     # 使う側が無くなれば名前を変えられる
            self.assertEqual([x['name'] for x in fs.list()], ['kmh'])
            self.assertEqual(list(fs.compiled()), ['kmh'])
            with self.assertRaises(F.FormulaError):
                fs.delete('ms2')                           # もう無い


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sample = write_sample(Path(self.tmp.name) / 'sample.ecorun')
        self.store = server.Store(Path(self.tmp.name) / 'home')
        self.sid = self.store.open(self.sample)['id']

    def tearDown(self):
        self.tmp.cleanup()

    def test_overlay_computes_per_lap(self):
        self.store.set_formula('save', 'dist', 'm', 'integ(Speed / 3.6)')
        self.store.set_formula('save', 'bad', '', 'Nope * 2')
        j = self.store.overlay([{'id': self.sid, 'lap': 1}, {'id': self.sid, 'lap': 2}], 'logger', ['Speed', 'dist', 'bad'])
        for lap in j['laps']:
            d = lap['channels']['dist']
            self.assertEqual(d[0], 0)                     # 周の頭で0から
            self.assertGreater(d[-1], 500)                # 1周ぶんの距離（作りものの楕円）
            self.assertNotIn('bad', lap['channels'])
            self.assertIn('Nope', lap['calc_errors']['bad'])

    def test_check(self):
        j = self.store.check_formula('Speed / 3.6')
        self.assertTrue(j['ok'])
        self.assertEqual(j['refs'], ['Speed'])
        self.assertEqual(j['missing'], [])
        self.assertAlmostEqual(j['preview']['max'], max(self.store.items[self.sid]['session'].channels['Speed']) / 3.6, places=3)
        j = self.store.check_formula('Speed +')
        self.assertFalse(j['ok'])
        self.assertEqual(j['pos'], 7)
        j = self.store.check_formula('Nope + 1')
        self.assertFalse(j['ok'])
        self.assertEqual(j['missing'], ['Nope'])
        with self.assertRaises(F.FormulaError):
            self.store.set_formula('save', 'Speed', '', '1')   # 開いている走行のチャンネルと同じ名前

    def test_saved_file(self):
        self.store.set_formula('save', 'ms', 'm/s', ' Speed / 3.6 ')
        saved = json.loads((Path(self.tmp.name) / 'home' / 'channels.json').read_text(encoding='utf-8'))
        self.assertEqual(saved, [{'name': 'ms', 'unit': 'm/s', 'expr': 'Speed / 3.6'}])


if __name__ == '__main__':
    unittest.main()
