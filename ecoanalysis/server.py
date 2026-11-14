"""画面（web/）と裏の処理をつなぐローカルのHTTPサーバー。

画面とPythonのやりとりはここを通すだけにして、pywebview の独自の呼び出し（js_api）には頼らない。
こうしておくと、あとで Electron や Tauri へ移すときも、画面のコードをそのまま使える。
外からつながらないよう、127.0.0.1 だけで待つ。
"""
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__

WEB = Path(__file__).resolve().parent.parent / 'web'
mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('video/mp4', '.mp4')


class Handler(BaseHTTPRequestHandler):
    server_version = 'EcoLoggerAnalysis/' + __version__

    def log_message(self, fmt, *args):      # 端末を静かに保つ
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/health':
            return self._json({'ok': True, 'app': 'ecologger-analysis', 'version': __version__})
        if path.startswith('/api/'):
            return self._json({'ok': False, 'error': 'not found'}, 404)
        rel = 'index.html' if path in ('', '/') else path.lstrip('/')
        f = (WEB / rel).resolve()
        if WEB not in f.parents or not f.is_file():      # web/ の外は出さない
            return self._send(404, b'not found', 'text/plain; charset=utf-8')
        ctype = mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        self._send(200, f.read_bytes(), ctype)


def start(port=0):
    """裏でサーバーを立て、(server, url) を返す。port=0 なら空いている番号を使う"""
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name='http', daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}/'
