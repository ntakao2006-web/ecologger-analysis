import http.client
import json
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


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name) / 'home'
        cls.sample = write_sample(Path(cls.tmp.name) / 'sample.ecorun')
        cls.srv, cls.url = server.start(0, server.Store(cls.home))
        cls.srv.store.picker = lambda: str(cls.sample)

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
        self.assertEqual(j, {'ok': True, 'app': 'ecologger-analysis', 'version': __version__})

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
        self.assertIn('EcoLogger 解析'.encode('utf-8'), body)
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
