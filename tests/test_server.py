import http.client
import json
import time
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import __version__, server  # noqa: E402
from ecoanalysis.sample import write_sample  # noqa: E402

API = {'X-EcoAnalysis': '1'}


def t0_end(meta):
    """作りものの動画の終わり（ロガーの時刻）"""
    return meta['laps'][0]['start'] + 20.0 + 80 * 1.001


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name) / 'home'
        cls.sample = write_sample(Path(cls.tmp.name) / 'sample.ecorun')
        cls.srv, cls.url = server.start(0, server.Store(cls.home))
        cls.pick_starts = []
        cls.srv.store.picker = lambda start=None: cls.pick_starts.append(start) or str(cls.sample)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def request(self, path, body=None, headers=API):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path.lstrip('/'), data=data, headers=dict(headers or {}))
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers.get('Content-Type'), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers.get('Content-Type'), e.read()

    def api(self, path, body=None):
        code, _, raw = self.request('/api/' + path, body)
        return code, json.loads(raw)

    def test_listens_on_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], '127.0.0.1')

    def test_health(self):
        code, j = self.api('health')
        self.assertEqual(code, 200)
        self.assertEqual({k: j[k] for k in ('ok', 'app', 'version', 'mode')}, {'ok': True, 'app': 'ecologger-analysis', 'version': __version__, 'mode': 'window'})
        self.assertIsInstance(j['rev'], int)
        self.assertIsNotNone(self.srv.store.last_seen)          # 画面からの知らせを覚えた

    def test_api_needs_header_and_local_host(self):
        self.assertEqual(self.request('/api/health', headers={})[0], 403)
        self.assertEqual(self.request('/api/open', {'path': str(self.sample)}, headers={})[0], 403)
        host, port = self.srv.server_address
        c = http.client.HTTPConnection(host, port, timeout=5)
        c.request('GET', '/api/health', headers={**API, 'Host': f'evil.example:{port}'})
        r = c.getresponse()
        r.read()
        c.close()
        self.assertEqual(r.status, 403)

    def test_index_and_assets(self):
        code, ctype, body = self.request('/', headers={})
        self.assertEqual(code, 200)
        self.assertIn('text/html', ctype)
        self.assertIn(b'EcoLogger Analysis', body)
        for name, kind in (('app.js', 'javascript'), ('style.css', 'text/css')):
            code, ctype, _ = self.request(name, headers={})
            self.assertEqual(code, 200)
            self.assertIn(kind, ctype)

    def test_unknown_api_is_404(self):
        self.assertEqual(self.api('nothing')[0], 404)
        self.assertEqual(self.api('nothing', {})[0], 404)

    def test_no_files_outside_web(self):
        for path in ('/../LICENSE', '/..%2fLICENSE', '/%2e%2e/ecoanalysis/server.py'):
            self.assertEqual(self.request(path, headers={})[0], 404, path)

    def test_open_list_close(self):
        j = self.api('pick', {})[1]
        self.assertEqual(j['path'], str(self.sample))
        code, j = self.api('open', {'path': j['path']})
        self.assertEqual(code, 200, j)
        s = j['session']
        self.assertEqual(s['title'], '作りものの走行')
        self.assertEqual([x['lap'] for x in s['laps']['app']], [1, 2, 3])
        self.assertEqual([x['lap'] for x in s['laps']['logger']], [1, 2, 3])
        self.assertIsNone(s['laps']['app_reason'])
        self.assertEqual(s['sector_count'], 2)
        self.assertGreater(s['gnss']['points'], 1000)
        again = self.api('open', {'path': str(self.sample / 'session.json')})[1]['session']
        self.assertEqual(again['id'], s['id'])                       # 同じ走行は2度開かない
        self.assertEqual([x['id'] for x in self.api('sessions')[1]['sessions']], [s['id']])
        self.assertIn(str(self.sample.resolve()), self.api('recent')[1]['recent'])
        self.assertTrue(self.api('close', {'id': s['id']})[1]['ok'])
        self.assertEqual(self.api('sessions')[1]['sessions'], [])
        self.assertTrue(any((self.home / 'cache').glob('*.npz')))   # 読んだ表を覚えた

    def test_last_and_pick_start(self):
        key = self.api('open', {'path': str(self.sample)})[1]['session']['path']
        # 選ぶ窓は、前回開いた走行の入っているフォルダーから
        self.api('pick', {})
        self.assertEqual(self.pick_starts[-1], str(Path(key).parent))
        # 前回の続き：開いていた走行と周を覚え、もう無い走行は落とす
        gone = str(Path(self.tmp.name) / 'gone.ecorun')
        self.assertTrue(self.api('last', {'paths': [key, gone], 'laps': [[key, 2], [gone, 1]]})[1]['ok'])
        self.assertEqual(self.api('last')[1]['last'], {'paths': [key], 'laps': [[key, 2]]})
        for bad in ({'paths': 'x', 'laps': []}, {'paths': [key], 'laps': [['other', 1]]}, {'paths': [key], 'laps': [[key, True]]}):
            self.assertEqual(self.api('last', bad)[0], 400, bad)
        self.assertEqual(self.api('last')[1]['last']['paths'], [key])     # 形の違うものでは上書きしない

    def test_overlay(self):
        sid = self.api('open', {'path': str(self.sample)})[1]['session']['id']
        code, j = self.api('overlay', {'laps': [{'id': sid, 'lap': 1}, {'id': sid, 'lap': 2}], 'mode': 'app',
                                       'channels': ['Speed', 'RPM']})
        self.assertEqual(code, 200, j)
        self.assertEqual(j['ref'], 1)
        self.assertEqual([x['key'] for x in j['laps']], [f'{sid}:1', f'{sid}:2'])
        self.assertAlmostEqual(j['laps'][0]['delta'][-1], 1.5, delta=0.01)    # 62.0 − 60.5 秒
        code, j = self.api('overlay', {'laps': [{'id': sid, 'lap': 9}], 'channels': []})
        self.assertEqual(code, 400)
        self.assertIn('9周目が無い', j['error'])
        self.assertEqual(self.api('overlay', {'laps': 'x'})[0], 400)
        # コース図の塗り（SP-10-12）：塗るチャンネルの値と印が返る。形の違う paint・mark は断る
        code, j = self.api('overlay', {'laps': [{'id': sid, 'lap': 1}, {'id': sid, 'lap': 2}], 'channels': [],
                                       'paint': 'Speed', 'mark': 'RPM'})
        self.assertEqual(code, 200, j)
        self.assertEqual((j['paint'], j['mark']), ('Speed', 'RPM'))
        self.assertEqual(len(j['laps'][0]['track']['v']), len(j['laps'][0]['track']['x']))
        self.assertTrue(any(m['k'] == 'on' for m in j['laps'][0]['marks']))
        self.assertEqual(self.api('overlay', {'laps': [], 'paint': 3})[0], 400)
        self.assertEqual(self.api('overlay', {'laps': [], 'mark': ['x']})[0], 400)
        self.api('close', {'id': sid})

    def test_shift_api(self):
        sid = self.api('open', {'path': str(self.sample)})[1]['session']['id']
        code, j = self.api('shift/set', {'id': sid, 'e': 0.25, 'n': -1})
        self.assertEqual(code, 200, j)
        self.assertEqual(j['shift'], [0.25, -1.0])
        code, j = self.api('overlay', {'laps': [{'id': sid, 'lap': 1}, {'id': sid, 'lap': 2}], 'channels': []})
        self.assertEqual([x['shift'] for x in j['laps']], [[0.25, -1.0], [0.25, -1.0]])
        self.assertEqual(self.api('shift/set', {'id': sid, 'e': 'x', 'n': 0})[0], 400)
        self.assertEqual(self.api('shift/set', {'id': 's999', 'e': 0, 'n': 0})[0], 404)
        code, j = self.api('shift/auto', {'id': sid, 'laps': [{'id': sid, 'lap': 1}, {'id': sid, 'lap': 2}], 'ref': 0})
        self.assertEqual(code, 400)                     # 基準の周の走行は自動では動かさない
        self.assertIn('基準の周の走行', j['error'])
        self.assertEqual(self.api('shift/auto', {'id': sid, 'laps': []})[0], 400)
        self.assertEqual(self.api('shift/set', {'id': sid, 'e': 0, 'n': 0})[1]['shift'], [0.0, 0.0])
        self.api('close', {'id': sid})

    def test_outline(self):
        # コース図（SP-10-19）：ネットへは出さない（地図は 404、写真は「無い」を返す偽物）
        class NoPhotos:
            def get(self, z, x, y):
                return 'none', None
        store = self.srv.store
        real = store.outlines
        store.outlines = server.outline.Outlines(self.home, NoPhotos(), fetch=lambda url: (404, b''))
        try:
            sid = self.api('open', {'path': str(self.sample)})[1]['session']['id']
            code, j = self.api('outline', {'id': sid})
            self.assertEqual(code, 200, j)
            o = j['outline']
            self.assertEqual(o['laps'], len(store.items[sid]['laps']))
            self.assertTrue(o['complete'])
            self.assertEqual(o['lines'], [])
            self.assertIn('国土地理院', o['attribution'])
            self.assertEqual(self.api('outline', {'id': 's999'})[0], 400)
            self.assertEqual(self.api('outline', {})[0], 400)
            # 手で直したもの（SP-10-24）：残すとそちらが出る。形が違えば 400。自動に戻せる
            ln = [[38.14, 140.78], [38.1401, 140.7801]]
            code, j = self.api('outline/save', {'id': sid, 'outline': {'lines': [ln], 'kerbs': [], 'boards': [
                {'lat': 38.14, 'lon': 140.78, 'text': '100'}]}})
            self.assertEqual(code, 200, j)
            self.assertTrue(j['outline']['edited'])
            self.assertEqual(self.api('outline', {'id': sid})[1]['outline']['lines'], [ln])
            self.assertFalse(self.api('outline', {'id': sid, 'auto': True})[1]['outline'].get('edited'))
            self.assertEqual(self.api('outline/save', {'id': sid, 'outline': {'lines': [[[1, 2]]]}})[0], 400)
            code, j = self.api('outline/reset', {'id': sid})
            self.assertEqual(code, 200, j)
            self.assertFalse(j['outline'].get('edited'))
            # ピットロード（SP-10-36）：作りものの走行はピットを通っていないので、理由を返して断る。形が違えば 400
            code, j = self.api('outline/pit', {'id': sid, 'edges': [ln]})
            self.assertEqual(code, 400, j)
            self.assertIn('ピット', j['error'])
            self.assertEqual(self.api('outline/pit', {'id': sid, 'edges': [[[1, 2]]]})[0], 400)
            self.assertEqual(self.api('outline/pit', {'id': sid, 'edges': 'x'})[0], 400)
            self.assertEqual(self.api('outline/pit', {'id': 's999'})[0], 400)
            self.api('close', {'id': sid})
        finally:
            store.outlines = real

    def test_sections_api(self):
        # 区間の線（SP-10-13）：基準の周のブレーキの踏み始めに置き、一覧に区間の数・理論ベストが入る。[] で消す
        d = write_sample(Path(self.tmp.name) / 'sections.ecorun')
        code, j = self.api('open', {'path': str(d)})
        sid, course = j['session']['id'], j['session']['course']
        try:
            code, j = self.api('sections/auto', {'id': sid, 'lap': 2})
            self.assertEqual(code, 200, j)
            self.assertEqual((j['auto']['how'], j['auto']['count']), ('brake', 1))
            s = next(x for x in j['sessions'] if x['id'] == sid)
            self.assertEqual(len(s['splits']), 1)
            self.assertEqual(len(s['laps']['app'][0]['sec']), 2)
            self.assertIsNotNone(s['stats']['splits']['theory'])
            for bad in ({'id': sid, 'lap': '2'}, {'id': sid, 'lap': True}, {'id': 1, 'lap': 2}, {'id': sid, 'lap': 99}, {'id': 's999', 'lap': 1}):
                self.assertEqual(self.api('sections/auto', bad)[0], 400, bad)
            self.assertEqual(self.api('sections/set', {'course': course, 'splits': 'x'})[0], 400)
            code, j = self.api('sections/set', {'course': course, 'splits': s['splits'], 'names': ['頭', '奥']})   # 名前（SP-10-37）
            self.assertEqual(code, 200, j)
            self.assertEqual(next(x for x in j['sessions'] if x['id'] == sid)['split_names'], ['頭', '奥'])
            self.assertEqual(self.api('sections/set', {'course': course, 'splits': s['splits'], 'names': ['頭']})[0], 400)
            code, j = self.api('sections/set', {'course': course, 'splits': []})
            self.assertEqual(code, 200, j)
            self.assertEqual(next(x for x in j['sessions'] if x['id'] == sid)['splits'], [])
        finally:
            self.api('close', {'id': sid})

    def test_lapstats_api(self):
        # 周ごとの最大・最小・平均（SP-10-39）
        d = write_sample(Path(self.tmp.name) / 'lapstats.ecorun')
        code, j = self.api('open', {'path': str(d)})
        sid = j['session']['id']
        try:
            code, j = self.api('lapstats', {'id': sid, 'mode': 'app', 'channels': ['Speed', 'RPM']})
            self.assertEqual(code, 200, j)
            first = j['laps'][min(j['laps'], key=int)]
            self.assertEqual(len(first['Speed']), 3)
            self.assertGreaterEqual(first['RPM'][0], first['RPM'][2])
            self.assertEqual(j['errors'], {})
            for bad in ({'id': sid, 'mode': 'x', 'channels': []}, {'id': sid, 'channels': 'Speed'}, {'id': 1, 'channels': []},
                        {'id': sid, 'channels': [1]}, {'id': sid, 'channels': ['a'] * 25}, {'id': sid, 'channels': ['x' * 101]},
                        {'id': 's999', 'channels': ['Speed']}):
                self.assertEqual(self.api('lapstats', bad)[0], 400, bad)
        finally:
            self.api('close', {'id': sid})

    def with_video(self, name):
        """作りものの走行に、作りものの動画（中身は0〜255の繰り返し）を足す。動画は1周目の途中から2周目の途中まで"""
        d = write_sample(Path(self.tmp.name) / name)
        meta = json.loads((d / 'session.json').read_text(encoding='utf-8'))
        (d / 'video').mkdir()
        (d / 'video' / '01.mp4').write_bytes(bytes(range(256)) * 40)
        l1 = meta['laps'][0]
        t0 = l1['start'] + 20.0
        meta['videos'] = [{'file': 'video/01.mp4', 'sync': [[float(k), t0 + k * 1.001] for k in range(0, 81)]}]
        (d / 'session.json').write_text(json.dumps(meta), encoding='utf-8')
        return d, meta

    def media(self, path, rng=None):
        host, port = self.srv.server_address
        c = http.client.HTTPConnection(host, port, timeout=5)
        c.request('GET', path, headers={'Range': rng} if rng else {})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, r.getheader('Content-Range'), body

    def test_lap_videos_and_media(self):
        d, meta = self.with_video('video.ecorun')
        sid = self.api('open', {'path': str(d)})[1]['session']['id']
        j = self.api('overlay', {'laps': [{'id': sid, 'lap': n} for n in (1, 2, 3)], 'mode': 'logger', 'channels': []})[1]
        v1, v2, v3 = (x['video'] for x in j['laps'])
        l1, l2 = meta['laps'][0], meta['laps'][1]
        self.assertEqual(len(v1), 1)
        self.assertEqual(v1[0]['url'], f'/media/{sid}/0')
        self.assertAlmostEqual(v1[0]['from'], 20.0, places=3)                     # 動画が始まるのは1周目の20秒
        self.assertAlmostEqual(v1[0]['to'], l1['time_s'], places=3)              # 周の終わりまである
        self.assertLessEqual(v1[0]['sync'][0][1], v1[0]['from'])                 # 周の範囲の前後1組まで渡す
        self.assertGreaterEqual(v1[0]['sync'][-1][1], v1[0]['to'])
        self.assertAlmostEqual(v2[0]['from'], 0.0, places=3)
        self.assertAlmostEqual(v2[0]['to'], t0_end(meta) - l2['start'], places=3)
        self.assertEqual(v3, [])                                                 # 3周目には動画が無い
        url = v1[0]['url']
        whole = bytes(range(256)) * 40
        self.assertEqual(self.media(url), (200, None, whole))
        self.assertEqual(self.media(url, 'bytes=10-19'), (206, f'bytes 10-19/{len(whole)}', whole[10:20]))
        self.assertEqual(self.media(url, 'bytes=10000-'), (206, f'bytes 10000-10239/{len(whole)}', whole[10000:]))
        self.assertEqual(self.media(url, 'bytes=-5'), (206, f'bytes 10235-10239/{len(whole)}', whole[-5:]))
        old = server.MAX_RANGE
        server.MAX_RANGE = 100                                                   # 一度に返すのは上限まで
        try:
            self.assertEqual(self.media(url, 'bytes=50-'), (206, f'bytes 50-149/{len(whole)}', whole[50:150]))
        finally:
            server.MAX_RANGE = old
        self.assertEqual(self.media(url, 'bytes=99999-')[:2], (416, f'bytes */{len(whole)}'))
        self.assertEqual(self.media(url, 'bytes=abc')[0], 416)
        self.assertEqual(self.media(f'/media/{sid}/1')[0], 404)                  # 無い動画
        self.assertEqual(self.media('/media/s999/0')[0], 404)                    # 開いていない走行
        self.api('close', {'id': sid})
        self.assertEqual(self.media(url)[0], 404)                                # 閉じたら出さない

    def test_media_needs_local_host(self):
        host, port = self.srv.server_address
        c = http.client.HTTPConnection(host, port, timeout=5)
        c.request('GET', '/media/s1/0', headers={'Host': f'evil.example:{port}'})
        r = c.getresponse()
        r.read()
        c.close()
        self.assertEqual(r.status, 403)

    def test_formulas_api(self):
        code, j = self.api('formulas')
        self.assertEqual(code, 200)
        self.assertIn('integ', [f['name'] for f in j['help']['funcs']])
        sid = self.api('open', {'path': str(self.sample)})[1]['session']['id']
        j = self.api('formulas/check', {'expr': 'Speed / 3.6'})[1]
        self.assertTrue(j['ok'], j)
        self.assertIsNotNone(j['preview'])
        j = self.api('formulas/check', {'expr': '(Speed'})[1]
        self.assertEqual((j['ok'], j['pos']), (False, 6))
        code, j = self.api('formulas/save', {'name': 'ms', 'unit': 'm/s', 'expr': 'Speed / 3.6'})
        self.assertEqual(code, 200, j)
        self.assertEqual([x['name'] for x in j['formulas']], ['ms'])
        code, j = self.api('formulas/save', {'name': 'x', 'expr': '1 +'})
        self.assertEqual((code, j['pos']), (400, 3))
        code, j = self.api('overlay', {'laps': [{'id': sid, 'lap': 1}], 'mode': 'app', 'channels': ['ms']})
        self.assertEqual(code, 200, j)
        self.assertEqual(len(j['laps'][0]['channels']['ms']), len(j['laps'][0]['t']))
        self.assertEqual(self.api('formulas/delete', {'name': 'ms'})[1]['formulas'], [])
        self.assertEqual(self.api('formulas/delete', {'name': 'ms'})[0], 400)
        self.assertEqual(self.api('formulas/rename', {'name': 'ms'})[0], 400)
        self.assertEqual(self.request('/api/formulas/check', {'expr': '1'}, headers={})[0], 403)
        self.api('close', {'id': sid})

    def test_handover_opens_and_raises(self):
        raised = []
        self.srv.store.raiser = lambda: raised.append(1)
        try:
            rev = self.api('health')[1]['rev']
            bad = str(Path(self.tmp.name) / 'nothing.ecorun')
            code, j = self.api('handover', {'paths': [str(self.sample), bad]})
            self.assertEqual(code, 200, j)
            self.assertEqual(raised, [1])
            h = self.api('health')[1]
            self.assertGreater(h['rev'], rev)                    # 画面は rev が変わったのを見て読み直す
            self.assertIn('nothing.ecorun を開けません', h['notice']['text'])
            sessions = self.api('sessions')[1]['sessions']
            self.assertEqual([s['path'] for s in sessions], [str(self.sample.resolve())])
            self.assertEqual(self.api('handover', {'paths': 'x'})[0], 400)
            self.api('close', {'id': sessions[0]['id']})
        finally:
            self.srv.store.raiser = None

    def test_reexported_session_is_reloaded_in_place(self):
        path = write_sample(Path(self.tmp.name) / 'again.ecorun')
        first = self.api('open', {'path': str(path)})[1]['session']
        self.assertEqual(self.api('open', {'path': str(path)})[1]['session']['id'], first['id'])   # 同じなら開き直さない
        meta = json.loads((path / 'session.json').read_text(encoding='utf-8'))
        meta['title'] = '書き出し直した走行'
        (path / 'session.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        os.utime(path / 'session.json', ns=(time.time_ns(), time.time_ns() + 10**9))
        again = self.api('open', {'path': str(path)})[1]['session']
        self.assertEqual((again['id'], again['title']), (first['id'], '書き出し直した走行'))
        self.assertEqual(sum(1 for x in self.api('sessions')[1]['sessions'] if x['id'] == first['id']), 1)
        self.api('close', {'id': first['id']})

    def test_open_errors(self):
        code, j = self.api('open', {'path': str(Path(self.tmp.name) / 'none.ecorun')})
        self.assertEqual(code, 400)
        self.assertIn('session.json が無い', j['problems'][0])
        bad = write_sample(Path(self.tmp.name) / 'bad.ecorun')
        meta = json.loads((bad / 'session.json').read_text(encoding='utf-8'))
        meta['version'] = 99
        (bad / 'session.json').write_text(json.dumps(meta), encoding='utf-8')
        code, j = self.api('open', {'path': str(bad)})
        self.assertEqual(code, 400)
        self.assertIn('知らない版', j['problems'][0])
        self.assertEqual(self.api('open', {})[0], 400)


if __name__ == '__main__':
    unittest.main()
