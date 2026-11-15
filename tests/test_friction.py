"""測位から計算するチャンネルと摩擦円（SP-10-14）"""
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import dynamics, overlay, server, session  # noqa: E402
from ecoanalysis.sample import write_sample  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sections import circle  # noqa: E402


def fake(gnss, rate=50.0, imu=True):
    """測位 gnss の上を走る記録（IMU の横G・前後G は測位の円から作る）"""
    t = np.arange(gnss['t'][0], gnss['t'][-1], 1 / rate)
    ch = {'Speed': np.interp(t, gnss['t'], gnss['speed_kmh'])}
    if imu:
        d = dynamics.from_gnss(gnss)
        ch['LatAccel'] = np.interp(t, d['t'], np.nan_to_num(d['lat_g']))
        ch['LongAccel'] = np.interp(t, d['t'], np.nan_to_num(d['long_g']))
    return SimpleNamespace(t=t, channels=ch, gnss=gnss, meta={'channels': [{'name': k} for k in ch]})


class AttachTest(unittest.TestCase):
    def test_adds_marked_channels_on_record_rows(self):
        s = fake(circle(50.0, 20.0, clockwise=True))
        dyn = dynamics.attach(s)
        self.assertIsNotNone(dyn)
        for name in ('GpsLatG', 'GpsLongG', 'GpsRadius', 'GripUse'):
            self.assertEqual(len(s.channels[name]), len(s.t))
        mid = slice(len(s.t) // 4, -len(s.t) // 4)
        self.assertAlmostEqual(float(np.nanmedian(s.channels['GpsRadius'][mid])), 50.0, delta=1.5)
        self.assertGreater(float(np.nanmedian(s.channels['GpsLatG'][mid])), 0.7)       # 右へ回る
        # 一定の円を回るだけなら、どこでも包絡いっぱい（100% 近く）
        self.assertAlmostEqual(float(np.nanmedian(s.channels['GripUse'][mid])), 100.0, delta=3.0)
        metas = {m['name']: m for m in s.meta['channels']}
        self.assertEqual(metas['GpsRadius']['derived'], 'gnss')
        self.assertIn('測位から計算', metas['GpsRadius']['label'])
        self.assertEqual(metas['GripUse']['source'], 'imu')

    def test_coarse_gnss_gives_no_gnss_channels(self):
        s = fake(circle(50.0, 20.0, rate=1.0), imu=False)
        self.assertIsNone(dynamics.attach(s))
        self.assertNotIn('GpsLatG', s.channels)
        self.assertNotIn('GripUse', s.channels)          # IMU も無いので作れない

    def test_existing_names_are_kept(self):
        s = fake(circle(50.0, 20.0))
        s.channels['GpsRadius'] = np.zeros(len(s.t))
        s.meta['channels'].append({'name': 'GpsRadius'})
        dynamics.attach(s)
        self.assertTrue(np.all(s.channels['GpsRadius'] == 0))
        self.assertEqual(sum(m['name'] == 'GpsRadius' for m in s.meta['channels']), 1)

    def test_straight_rows_stay_blank(self):
        s = fake(circle(5000.0, 20.0))
        dynamics.attach(s)
        self.assertTrue(np.all(np.isnan(s.channels['GpsRadius'])))


class EnvelopeTest(unittest.TestCase):
    def test_envelope_follows_each_direction(self):
        rng = np.random.default_rng(1)
        a = rng.uniform(0, 2 * np.pi, 20000)
        r = np.where(np.sin(a) > 0, 0.4, 1.0) * rng.uniform(0, 1, len(a))   # 加速の側は 0.4 G まで、ほかは 1 G まで
        env = dynamics.envelope(r * np.cos(a), r * np.sin(a))
        self.assertEqual(len(env), 24)
        self.assertAlmostEqual(env[6], 0.4, delta=0.02)        # 90°（加速）あたり
        self.assertAlmostEqual(env[18], 1.0, delta=0.03)       # 270°（減速）あたり
        self.assertAlmostEqual(float(dynamics.env_at(env, np.array([0.0]), np.array([-1.0]))[0]), 1.0, delta=0.03)

    def test_sparse_directions_are_filled_from_neighbours(self):
        a = np.radians(np.concatenate([np.full(100, 7.0), np.full(100, 97.0), np.full(100, 187.0)]))
        env = dynamics.envelope(np.cos(a), np.sin(a))
        self.assertTrue(np.all(np.isfinite(env)))
        self.assertIsNone(dynamics.envelope(np.ones(5), np.ones(5)))


class OverlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = write_sample(Path(cls.tmp.name) / 'sample.ecorun')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def items(self):
        s = session.load(self.path)
        dynamics.attach(s)
        return [{'key': f'a:{x["lap"]}', 'label': f'{x["lap"]}周', 'session': s, 'lap': x} for x in s.laps[:2]]

    def test_friction_points_and_envelope(self):
        out = overlay.compare(self.items(), ['Speed'], gsrc='imu')
        fr = out['friction']
        self.assertEqual(fr['src'], 'imu')
        self.assertEqual(len(fr['env']), 24)
        g = out['laps'][0]['g']
        self.assertEqual(set(g), {'t', 'x', 'y', 'r', 'v'})
        self.assertAlmostEqual(g['t'][1] - g['t'][0], 0.1, places=2)
        self.assertTrue(all(len(g[k]) == len(g['t']) for k in g))
        plain = overlay.compare(self.items(), ['Speed'])
        self.assertIsNone(plain['friction'])                     # 頼まなければ作らない
        self.assertNotIn('g', plain['laps'][0])

    def test_gnss_source(self):
        out = overlay.compare(self.items(), ['Speed'], gsrc='gnss')
        self.assertEqual(out['friction']['src'], 'gnss')


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = write_sample(Path(cls.tmp.name) / 'sample.ecorun')
        cls.srv, cls.url = server.start(0, server.Store(Path(cls.tmp.name) / 'home'))

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def api(self, path, body):
        req = urllib.request.Request(self.url + 'api/' + path, data=json.dumps(body).encode(),
                                     headers={'X-EcoAnalysis': '1', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_open_lists_derived_channels_and_overlay_gives_friction(self):
        code, j = self.api('open', {'path': str(self.path)})
        self.assertEqual(code, 200)
        names = {c['name']: c for c in j['session']['channels']}
        self.assertEqual(names['GpsLatG'].get('derived'), 'gnss')
        self.assertIn('GripUse', names)
        sid, lap = j['session']['id'], j['session']['laps']['app'][0]['lap']
        code, o = self.api('overlay', {'laps': [{'id': sid, 'lap': lap}], 'channels': ['GripUse', 'GpsRadius'], 'gsrc': 'imu'})
        self.assertEqual(code, 200)
        self.assertEqual(len(o['friction']['env']), 24)
        self.assertIn('GripUse', o['laps'][0]['channels'])
        code, o = self.api('overlay', {'laps': [{'id': sid, 'lap': lap}], 'channels': [], 'gsrc': 'x'})
        self.assertEqual(code, 400)
        code, st = self.api('lapstats', {'id': sid, 'mode': 'app', 'channels': ['GpsRadius']})
        self.assertEqual(code, 200)
        self.assertTrue(any(v['GpsRadius'] for v in st['laps'].values()))


if __name__ == '__main__':
    unittest.main()
