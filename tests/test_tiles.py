import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import server, tiles  # noqa: E402

JPEG = b'\xff\xd8\xff\xe0' + b'0' * 100


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.answer = (200, JPEG)
        self.clock = Clock()
        self.t = tiles.Tiles(self.tmp.name, fetch=self.fake, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, url):
        self.calls.append(url)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def test_fetches_once_then_serves_from_disk(self):
        self.assertEqual(self.t.get(18, 232000, 101000), ('ok', JPEG))
        self.assertEqual(self.calls, ['https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/18/232000/101000.jpg'])
        self.assertTrue(self.t.path(18, 232000, 101000).is_file())
        self.answer = OSError('ネットが無い')
        self.assertEqual(self.t.get(18, 232000, 101000), ('ok', JPEG))     # 手元にあれば取りに行かない
        self.assertEqual(len(self.calls), 1)

    def test_rejects_out_of_range(self):
        for z, x, y in ((1, 0, 0), (19, 0, 0), (10, 1024, 0), (10, 0, -1)):
            self.assertEqual(self.t.get(z, x, y), ('bad', None))
        self.assertEqual(self.calls, [])

    def test_no_photo_there(self):
        self.answer = (404, b'')
        self.assertEqual(self.t.get(10, 1, 1), ('none', None))
        self.assertFalse(self.t.path(10, 1, 1).exists())

    def test_backs_off_when_offline(self):
        self.answer = OSError('つながらない')
        self.assertEqual(self.t.get(10, 1, 1), ('offline', None))
        self.assertEqual(self.t.get(10, 1, 2), ('offline', None))
        self.assertEqual(len(self.calls), 1)                 # しばらくは取りに行かない
        self.clock.t += tiles.BACKOFF_S + 1
        self.answer = (200, JPEG)
        self.assertEqual(self.t.get(10, 1, 2), ('ok', JPEG))

    def test_does_not_keep_non_jpeg(self):
        self.answer = (200, b'<html>maintenance</html>')
        self.assertEqual(self.t.get(10, 1, 1), ('offline', None))
        self.assertFalse(self.t.path(10, 1, 1).exists())


class TileRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store = server.Store(cls.tmp.name)
        store.tiles.fetch = lambda url: (200, JPEG) if '/12/' in url else (404, b'')
        cls.srv, cls.url = server.start(0, store)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.url + path, timeout=10) as r:
                return r.status, r.headers.get('Content-Type'), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, None, e.read()

    def test_tile(self):
        self.assertEqual(self.get('tiles/gsi/12/3640/1600.jpg'), (200, 'image/jpeg', JPEG))
        self.assertEqual(self.get('tiles/gsi/13/3640/1600.jpg')[0], 404)
        self.assertEqual(self.get('tiles/gsi/12/9999/1600.jpg')[0], 400)
        self.assertEqual(self.get('tiles/gsi/12/x/1600.jpg')[0], 404)


if __name__ == '__main__':
    unittest.main()
