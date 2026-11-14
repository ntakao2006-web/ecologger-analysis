import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import __version__, server  # noqa: E402


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = server.start(0)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.url + path.lstrip('/'), timeout=5) as r:
            return r.status, r.headers.get('Content-Type'), r.read()

    def test_listens_on_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], '127.0.0.1')

    def test_health(self):
        code, ctype, body = self.get('/api/health')
        self.assertEqual(code, 200)
        self.assertTrue(ctype.startswith('application/json'))
        self.assertEqual(json.loads(body), {'ok': True, 'app': 'ecologger-analysis', 'version': __version__})

    def test_index_and_assets(self):
        code, ctype, body = self.get('/')
        self.assertEqual(code, 200)
        self.assertIn('text/html', ctype)
        self.assertIn('EcoLogger 解析'.encode('utf-8'), body)
        for name, kind in (('app.js', 'javascript'), ('style.css', 'text/css')):
            code, ctype, _ = self.get(name)
            self.assertEqual(code, 200)
            self.assertIn(kind, ctype)

    def test_unknown_api_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get('/api/nothing')
        self.assertEqual(cm.exception.code, 404)

    def test_no_files_outside_web(self):
        for path in ('/../LICENSE', '/..%2fLICENSE', '/%2e%2e/ecoanalysis/server.py'):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 404, path)


if __name__ == '__main__':
    unittest.main()
