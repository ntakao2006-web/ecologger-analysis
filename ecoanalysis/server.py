"""画面（web/）と裏の処理をつなぐローカルのHTTPサーバー。

画面とPythonのやりとりはここを通すだけにして、pywebview の独自の呼び出し（js_api）には頼らない。
こうしておくと、あとで Electron や Tauri へ移すときも、画面のコードをそのまま使える。
外からつながらないよう、127.0.0.1 だけで待つ。
ほかのWebページが裏からこのサーバーを呼ばないよう、/api/ は決まった見出し（X-EcoAnalysis）が付いた
呼び出しだけを受ける（ほかのページからこの見出しを付けて呼ぶと、ブラウザが先に問い合わせて止める）。
宛先の名前（Host）も 127.0.0.1・localhost だけを受ける。
"""
import json
import mimetypes
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__, laps, session

WEB = Path(__file__).resolve().parent.parent / 'web'
HOME = Path.home() / '.ecologger-analysis'
API_HEADER = 'X-EcoAnalysis'
MAX_BODY = 1 << 20
RECENT_KEEP = 10
mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('video/mp4', '.mp4')


class Store:
    """開いている走行（id → 走行と、アプリで切った周）"""

    def __init__(self, home=HOME):
        self.home = Path(home)
        self.lock = threading.Lock()
        self.items = {}
        self.next_id = 1
        self.picker = None          # フォルダーを選ぶ窓（app.py が pywebview の窓を渡す）

    @property
    def cache(self):
        return self.home / 'cache'

    def open(self, path):
        path = Path(path).expanduser()
        if path.name == 'session.json':
            path = path.parent
        key = str(path.resolve())
        with self.lock:
            for sid, it in self.items.items():
                if it['key'] == key:
                    return summary(sid, it)
        t = time.perf_counter()
        s = session.load(path, self.cache)
        app_laps, line, why = laps.for_session(s)
        it = {'key': key, 'session': s, 'laps': app_laps, 'line': line, 'why': why,
              'load_s': round(time.perf_counter() - t, 2)}
        with self.lock:
            sid = f's{self.next_id}'
            self.next_id += 1
            self.items[sid] = it
        self.remember(key)
        return summary(sid, it)

    def close(self, sid):
        with self.lock:
            return self.items.pop(sid, None) is not None

    def list(self):
        with self.lock:
            return [summary(sid, it) for sid, it in self.items.items()]

    # 最近開いた走行
    def recent(self):
        try:
            paths = json.loads((self.home / 'recent.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        if not isinstance(paths, list):
            return []
        return [p for p in paths if isinstance(p, str) and (Path(p) / 'session.json').is_file()][:RECENT_KEEP]

    def remember(self, key):
        paths = [key] + [p for p in self.recent() if p != key]
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            (self.home / 'recent.json').write_text(json.dumps(paths[:RECENT_KEEP], ensure_ascii=False, indent=1), encoding='utf-8')
        except OSError:
            pass

    def pick(self):
        """フォルダーを選んでもらう。選ばなければ None"""
        if self.picker:
            return self.picker()
        return pick_with_tk()


def pick_with_tk():
    """ブラウザで開いているとき（pywebview の窓が無いとき）の、フォルダーを選ぶ窓。別のプロセスで出す"""
    code = ('import tkinter as t, tkinter.filedialog as f\n'
            'r = t.Tk(); r.withdraw(); r.attributes("-topmost", True)\n'
            'print(f.askdirectory(title="走行のフォルダー（.ecorun）を選ぶ") or "")\n')
    try:
        out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, encoding='utf-8', timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def summary(sid, it):
    s = it['session']
    m = s.meta
    course = m.get('course') or {}
    g = s.gnss
    return {
        'id': sid, 'path': it['key'], 'title': s.title, 't0_utc': m['t0_utc'], 'duration_s': m['duration_s'],
        'clock': m['clock'], 'course': course.get('name'), 'line': it['line'],
        'sector_count': len(course.get('sectors') or []),
        'channels': m['channels'], 'rows': int(len(s.t)), 'rate_hz': m.get('rate_hz'),
        'gnss': None if g is None else {**(m.get('gnss') or {}), 'points': int(len(g['t']))},
        'videos': len(s.videos), 'notes': m.get('notes') or [], 'load_s': it['load_s'],
        'laps': {'app': it['laps'], 'app_reason': it['why'],
                 'logger': [{k: x.get(k) for k in ('lap', 'start', 'end', 'time_s', 'sectors')} for x in s.laps]},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = 'EcoLoggerAnalysis/' + __version__
    store = None

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

    def _host_ok(self):
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0].strip('[]').lower()
        return host in ('127.0.0.1', 'localhost')

    def _api_ok(self):
        if not self._host_ok() or self.headers.get(API_HEADER) != '1':
            self._json({'ok': False, 'error': 'forbidden'}, 403)
            return False
        return True

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            raise ValueError('大きすぎる')
        raw = self.rfile.read(n) if n else b'{}'
        obj = json.loads(raw.decode('utf-8'))
        if not isinstance(obj, dict):
            raise ValueError('{…} の形でない')
        return obj

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith('/api/'):
            if not self._api_ok():
                return
            if path == '/api/health':
                return self._json({'ok': True, 'app': 'ecologger-analysis', 'version': __version__})
            if path == '/api/sessions':
                return self._json({'ok': True, 'sessions': self.store.list()})
            if path == '/api/recent':
                return self._json({'ok': True, 'recent': self.store.recent()})
            return self._json({'ok': False, 'error': 'not found'}, 404)
        if not self._host_ok():
            return self._send(403, b'forbidden', 'text/plain; charset=utf-8')
        rel = 'index.html' if path in ('', '/') else path.lstrip('/')
        f = (WEB / rel).resolve()
        if WEB not in f.parents or not f.is_file():      # web/ の外は出さない
            return self._send(404, b'not found', 'text/plain; charset=utf-8')
        ctype = mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        self._send(200, f.read_bytes(), ctype)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body, err = self._body(), None     # 断るときも先に読み切る（読まずに返すと、つながりが切れることがある）
        except (ValueError, UnicodeDecodeError) as e:
            body, err = None, e
        if not self._api_ok():
            return
        if err is not None:
            return self._json({'ok': False, 'error': f'送られた中身が読めない（{err}）'}, 400)
        if path == '/api/open':
            p = body.get('path')
            if not isinstance(p, str) or not p.strip():
                return self._json({'ok': False, 'error': 'path が無い'}, 400)
            try:
                return self._json({'ok': True, 'session': self.store.open(p.strip().strip('"'))})
            except session.SessionError as e:
                return self._json({'ok': False, 'error': '走行データを開けません', 'problems': e.problems}, 400)
            except OSError as e:
                return self._json({'ok': False, 'error': f'開けません（{e}）'}, 400)
        if path == '/api/close':
            return self._json({'ok': self.store.close(body.get('id'))})
        if path == '/api/pick':
            return self._json({'ok': True, 'path': self.store.pick()})
        return self._json({'ok': False, 'error': 'not found'}, 404)


def start(port=0, store=None):
    """裏でサーバーを立て、(server, url) を返す。port=0 なら空いている番号を使う"""
    handler = type('BoundHandler', (Handler,), {'store': store or Store()})
    srv = ThreadingHTTPServer(('127.0.0.1', port), handler)
    srv.daemon_threads = True
    srv.store = handler.store
    threading.Thread(target=srv.serve_forever, name='http', daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}/'
