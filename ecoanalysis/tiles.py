"""コース図の下に敷く航空写真（国土地理院の「全国最新写真（シームレス）」のタイル）。

- 画面は /tiles/gsi/{z}/{x}/{y}.jpg をこのアプリの裏から読む。裏は手元（~/.ecologger-analysis/tiles/）に
  あればそれを返し、無ければ国土地理院から取って残してから返す。一度見たコースは、ネットが無くても出せる
- 出典の表示（「国土地理院」）は画面の図の右下に出す（地理院タイルの利用の決まり）
- 送るのはタイルの番号だけ（＝見ている場所）。走行の中身は送らない
- ネットにつながらないときは、しばらく取りに行くのをやめる（図を動かすたびに何十枚も待たないため）
"""
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__

URL = 'https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg'
MIN_Z, MAX_Z = 2, 18
TIMEOUT_S = 8
BACKOFF_S = 30          # つながらなかったら、これだけ取りに行かない
MAX_BYTES = 2 << 20


def _fetch(url):
    """(HTTPの番号, 中身)。つながらなければ OSError"""
    req = urllib.request.Request(url, headers={'User-Agent': f'EcoLogger-Analysis/{__version__}'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        return e.code, b''


class Tiles:
    def __init__(self, home, fetch=_fetch, clock=time.monotonic):
        self.dir = Path(home) / 'tiles' / 'gsi'
        self.fetch = fetch
        self.clock = clock
        self.lock = threading.Lock()
        self.down_until = 0.0

    @staticmethod
    def valid(z, x, y):
        return MIN_Z <= z <= MAX_Z and 0 <= x < (1 << z) and 0 <= y < (1 << z)

    def path(self, z, x, y):
        return self.dir / str(z) / str(x) / f'{y}.jpg'

    def get(self, z, x, y):
        """(状態, 中身)。状態：'ok'（中身はJPEG）・'none'（その場所に写真が無い）・'offline'（取れない）・'bad'（番号が違う）"""
        if not self.valid(z, x, y):
            return 'bad', None
        f = self.path(z, x, y)
        try:
            return 'ok', f.read_bytes()
        except OSError:
            pass
        with self.lock:
            if self.clock() < self.down_until:
                return 'offline', None
        try:
            code, body = self.fetch(URL.format(z=z, x=x, y=y))
        except (OSError, ValueError):
            with self.lock:
                self.down_until = self.clock() + BACKOFF_S
            return 'offline', None
        if code == 404:
            return 'none', None
        if code != 200 or not body.startswith(b'\xff\xd8') or len(body) > MAX_BYTES:
            return 'offline', None
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f'{f.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            tmp.write_bytes(body)
            os.replace(tmp, f)
        except OSError:
            pass                    # 残せなくても、今回は出せる
        return 'ok', body
