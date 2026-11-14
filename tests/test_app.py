import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import app, server  # noqa: E402
from ecoanalysis.sample import write_sample  # noqa: E402


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name) / 'home'
        self.patches = [mock.patch.object(server, 'HOME', home), mock.patch.object(app, 'RUNNING', home / 'running.json')]
        for p in self.patches:
            p.start()
        self.home = home

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_webview2_version_reads_registry(self):
        v = app.webview2_version()
        if sys.platform == 'win32':
            self.assertTrue(v is None or v >= app.WEBVIEW2_MIN)
        with mock.patch.dict(sys.modules, {'winreg': None}):
            self.assertIsNone(app.webview2_version())        # Windows でなければ無い扱い

    def test_hand_over_to_running_app(self):
        sample = write_sample(Path(self.tmp.name) / 'sample.ecorun')
        self.assertFalse(app.hand_over([str(sample)]))         # 開いているアプリが無い
        srv, _ = server.start(0, server.Store(self.home))
        try:
            app.write_running(srv.server_address[1])
            self.assertEqual(json.loads(app.RUNNING.read_text(encoding='utf-8'))['port'], srv.server_address[1])
            self.assertTrue(app.hand_over([str(sample)]))
            self.assertEqual([s['title'] for s in srv.store.list()], ['作りものの走行'])
            app.clear_running()
            self.assertFalse(app.RUNNING.exists())
        finally:
            srv.shutdown()
            srv.server_close()
        app.RUNNING.parent.mkdir(parents=True, exist_ok=True)
        app.RUNNING.write_text(json.dumps({'port': srv.server_address[1], 'pid': 1}), encoding='utf-8')
        self.assertFalse(app.hand_over([str(sample)], timeout=2))   # 残っていた番号の先にもう居ない

    def test_browser_mode_ends_when_the_page_stops_calling(self):
        srv, url = server.start(0, server.Store(self.home))
        try:
            opened = []
            with mock.patch.object(app.webbrowser, 'open', opened.append), mock.patch.object(app, 'IDLE_S', 1), \
                    mock.patch.object(app.time, 'sleep', lambda s, real=time.sleep: real(0.05)):
                t = threading.Thread(target=app.run_in_browser, args=(srv, url, '理由'))
                t.start()
                for _ in range(10):                            # 画面が知らせている間は終わらない
                    srv.store.health()
                    time.sleep(0.1)
                self.assertTrue(t.is_alive())
                t.join(5)                                      # 知らせが途絶えたら終わる
                self.assertFalse(t.is_alive())
            self.assertEqual(opened, [url])
            h = srv.store.health()
            self.assertEqual((h['mode'], h['mode_note']), ('browser', '理由'))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_main_falls_back_to_browser_without_webview2(self):
        sample = write_sample(Path(self.tmp.name) / 'sample.ecorun')
        seen = {}

        def fake_browser(srv, url, why):
            seen['why'], seen['sessions'] = why, [s['title'] for s in srv.store.list()]
            seen['running'] = app.RUNNING.exists()

        with mock.patch.object(app, 'webview2_version', lambda: None), mock.patch.object(app, 'run_in_browser', fake_browser):
            self.assertEqual(app.main([str(sample)]), 0)
        self.assertIn('WebView2', seen['why'])
        self.assertEqual(seen['sessions'], ['作りものの走行'])   # 引数の走行を開いてから見せる
        self.assertTrue(seen['running'])
        self.assertFalse(app.RUNNING.exists())                 # 終わったら片づける

    def test_pick_folder_writes_choice(self):
        out = Path(self.tmp.name) / 'pick.txt'
        fake_tk = mock.MagicMock()
        fake_fd = mock.MagicMock()
        fake_fd.askdirectory.return_value = 'D:/走行/a.ecorun'
        fake_tk.filedialog = fake_fd
        with mock.patch.dict(sys.modules, {'tkinter': fake_tk, 'tkinter.filedialog': fake_fd}):
            self.assertEqual(app.pick_folder(str(out)), 0)
        self.assertEqual(out.read_text(encoding='utf-8'), 'D:/走行/a.ecorun')


if __name__ == '__main__':
    unittest.main()
