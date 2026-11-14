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
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__, laps, lines, overlay, session

WEB = Path(__file__).resolve().parent.parent / 'web'
HOME = Path.home() / '.ecologger-analysis'
API_HEADER = 'X-EcoAnalysis'
MAX_BODY = 1 << 20
RECENT_KEEP = 10
CHUNK = 1 << 20
MAX_RANGE = 8 << 20     # 途中から読む頼みに一度で返す上限。終わりを書かない頼み（bytes=N-）に残り全部を流し続けると、
                        # 画面がつながりを握ったまま読まなくなり、早送り・巻き戻しが止まる（Chromiumで確かめた）
MEDIA = re.compile(r'^/media/(s\d+)/(\d+)$')
RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')
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
        self.lines = lines.Lines(self.home / 'lines.json')

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
        it = {'key': key, 'session': s, 'load_s': None}
        self._cut(it)
        it['load_s'] = round(time.perf_counter() - t, 2)
        with self.lock:
            sid = f's{self.next_id}'
            self.next_id += 1
            self.items[sid] = it
        self.remember(key)
        return summary(sid, it)

    def _cut(self, it):
        """そのコースで使っている線の版（無ければ書き出しの線）で周を切る"""
        s = it['session']
        course = (s.meta.get('course') or {}).get('name')
        act = self.lines.active(course) if course else None
        it['line_names'] = self.lines.names(course) if course else []
        if act:
            name, line, sectors = act
            it['laps'], it['line'], it['why'] = laps.for_session(s, line, sectors)
            it['sectors'], it['line_name'] = sectors, name
        else:
            it['laps'], it['line'], it['why'] = laps.for_session(s)
            it['sectors'], it['line_name'] = (s.meta.get('course') or {}).get('sectors') or [], None

    def set_lines(self, action, course, name=None, line=None, sectors=None):
        """線の版を残す（save）・使う（use、name=None なら書き出しの線）・消す（delete）。同じコースの走行の周を切り直す"""
        if action == 'save':
            self.lines.save(course, name, line, sectors)
        elif action == 'use':
            self.lines.use(course, name)
        elif action == 'delete':
            self.lines.delete(course, name)
        else:
            raise lines.LineError('知らない頼み')
        with self.lock:
            for it in self.items.values():
                if (it['session'].meta.get('course') or {}).get('name') == course:
                    self._cut(it)
        return self.list()

    def close(self, sid):
        with self.lock:
            return self.items.pop(sid, None) is not None

    def overlay(self, picks, mode, channels, ref=None):
        """picks：[{id, lap}]。mode：'app'（コースの線で切った周）か 'logger'。周が見つからなければ KeyError"""
        items = []
        with self.lock:
            for p in picks:
                it = self.items.get(p.get('id')) if isinstance(p, dict) else None
                if it is None:
                    raise KeyError(f'開いていない走行：{p!r}'[:200])
                use_app = mode == 'app' and it['laps'] is not None
                pool = it['laps'] if use_app else it['session'].laps
                lap = next((x for x in pool if x['lap'] == p.get('lap')), None)
                if lap is None:
                    raise KeyError(f'{it["session"].title} に {p.get("lap")}周目が無い')
                items.append({'key': f'{p["id"]}:{lap["lap"]}', 'label': f'{it["session"].title} {lap["lap"]}周',
                              'session': it['session'], 'lap': lap, 'sid': p['id']})
        out = overlay.compare(items, channels, ref)
        for item, lap in zip(items, out['laps']):
            lap['video'] = lap_videos(item['sid'], item['session'], item['lap'])
        return out

    def media(self, sid, idx):
        """開いている走行の idx 本目の動画のファイル。無ければ None"""
        with self.lock:
            it = self.items.get(sid)
        if it is None:
            return None
        s = it['session']
        if not 0 <= idx < len(s.videos):
            return None
        f = session.safe_file(s.path, s.videos[idx].get('file'))
        return f if f and f.is_file() else None

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


def lap_videos(sid, s, lap):
    """その周に重なる動画。[{url, sync: [[動画の秒, 周の頭からの秒], …], from, to}]（周の頭からの秒の順）。

    sync は周の範囲とその前後1組だけに絞る。from・to は動画がある周の中の範囲（周の頭からの秒）。
    """
    out = []
    for i, v in enumerate(s.videos):
        sync = v['sync']
        ts = [p[1] for p in sync]
        lo, hi = max(lap['start'], ts[0]), min(lap['end'], ts[-1])
        if hi <= lo:
            continue
        a = max(0, next(k for k, t in enumerate(ts) if t >= lo) - 1)
        b = min(len(ts) - 1, next(k for k, t in enumerate(ts) if t >= hi))
        out.append({'url': f'/media/{sid}/{i}', 'from': round(lo - lap['start'], 3), 'to': round(hi - lap['start'], 3),
                    'sync': [[round(sync[k][0], 3), round(sync[k][1] - lap['start'], 3)] for k in range(a, b + 1)]})
    out.sort(key=lambda x: x['from'])
    return out


def summary(sid, it):
    s = it['session']
    m = s.meta
    course = m.get('course') or {}
    g = s.gnss
    return {
        'id': sid, 'path': it['key'], 'title': s.title, 't0_utc': m['t0_utc'], 'duration_s': m['duration_s'],
        'clock': m['clock'], 'course': course.get('name'), 'line': it['line'], 'sectors': it['sectors'],
        'line_name': it['line_name'], 'export_line': {'line': course.get('line'), 'sectors': course.get('sectors') or []},
        'line_names': it['line_names'],
        'sector_count': len(it['sectors']),
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
        m = MEDIA.match(path)
        if m:
            return self._media(m.group(1), int(m.group(2)))
        rel = 'index.html' if path in ('', '/') else path.lstrip('/')
        f = (WEB / rel).resolve()
        if WEB not in f.parents or not f.is_file():      # web/ の外は出さない
            return self._send(404, b'not found', 'text/plain; charset=utf-8')
        ctype = mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        self._send(200, f.read_bytes(), ctype)

    def _media(self, sid, idx):
        """動画を渡す。<video> は見出しを付けられないので、Host だけ見る。開いている走行の動画だけを出す。
        途中から読む頼み（Range）に答える（早送り・巻き戻しに要る）"""
        f = self.store.media(sid, idx)
        if f is None:
            return self._send(404, b'not found', 'text/plain; charset=utf-8')
        size = f.stat().st_size
        a, b, code = 0, size - 1, 200
        rng = self.headers.get('Range')
        if rng:
            m = RANGE.match(rng.strip())
            if not m or (not m.group(1) and not m.group(2)):
                return self._unsatisfiable(size)
            if m.group(1):
                a = int(m.group(1))
                b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:                                   # bytes=-N：最後の N バイト
                a = max(0, size - int(m.group(2)))
            if a >= size or a > b:
                return self._unsatisfiable(size)
            b = min(b, a + MAX_RANGE - 1)           # 頼まれたより短く返してよい（画面は続きをまた頼む）
            code = 206
        self.send_response(code)
        self.send_header('Content-Type', mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(b - a + 1))
        if code == 206:
            self.send_header('Content-Range', f'bytes {a}-{b}/{size}')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            with open(f, 'rb') as fh:
                fh.seek(a)
                left = b - a + 1
                while left > 0:
                    chunk = fh.read(min(CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                    # 早送りで、画面が途中で読むのをやめた

    def _unsatisfiable(self, size):
        self.send_response(416)
        self.send_header('Content-Range', f'bytes */{size}')
        self.send_header('Content-Length', '0')
        self.end_headers()

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
        if path == '/api/overlay':
            picks, chans, ref = body.get('laps'), body.get('channels') or [], body.get('ref')
            if not isinstance(picks, list) or not all(isinstance(c, str) for c in chans) or not (ref is None or isinstance(ref, int)):
                return self._json({'ok': False, 'error': 'laps・channels・ref の形が違う'}, 400)
            try:
                return self._json({'ok': True, **self.store.overlay(picks[:12], body.get('mode', 'app'), chans[:12], ref)})
            except KeyError as e:
                return self._json({'ok': False, 'error': str(e.args[0])}, 400)
        if path.startswith('/api/lines/'):
            try:
                sessions = self.store.set_lines(path.rsplit('/', 1)[1], body.get('course'), body.get('name'),
                                                body.get('line'), body.get('sectors'))
            except lines.LineError as e:
                return self._json({'ok': False, 'error': str(e)}, 400)
            return self._json({'ok': True, 'sessions': sessions})
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
