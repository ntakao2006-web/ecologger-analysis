"""コース図（道路の両側の縁）を自動で作る（SP-10-19）。

周の線を細いまま、道路の縁だけの静かな図に重ねると、線がコース幅のどこを通ったかが読める。
縁は、使ってよい元（国土地理院）から自前で作る。ほかの解析ソフトのコース図は写さない。

作り方：
1. 走った周（ふだんはいちばん速い周）の線を案内にし、STEP_M おきに左右へ横断面を切る
2. 走行の全部の測位（ほかの周も）が横へ散らばった幅を見る。車が走った所は路面なので、縁はそれより外にある
   （地図の道路縁には、合流する車線の境目など、コースの中を通る線もあるため。これで見分ける）
3. 横断面ごとに、走った幅より外で、国土地理院の「地理院地図Vector」（最適化ベクトルタイル）の道路縁に当たれば、その距離を使う。
   測量した線なので確か。走る向きとの角度が EDGE_ANGLE_DEG より大きい縁（交わる道・橋）は見ない
4. 縁の近くの航空写真に赤白の画素が並ぶ所を、ゼブラ（縁石）として面で返す（薄く塗るため）
5. 道路縁が無い所（観客席の側など、地図で道路として描かれていない所）は、航空写真（全国最新写真）の色で、
   灰色の舗装・白線・赤い縁石が途切れる所を探す（草は緑寄り、砂利は赤寄り、木の陰は暗い）
6. 飛んだ値をならし、緯度経度の線にする。どちらでも見つからない所は線を切る

- タイルは ~/.ecologger-analysis/tiles/ に残す（写真は tiles.py と同じ置き場）。送るのはタイルの番号だけ
- できた図はコースごとに ~/.ecologger-analysis/outlines/ に残し、2回目からはすぐ出す
- ピットロードは作らない（走った周の線を案内にするため）
"""
import hashlib
import io
import json
import math
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from . import __version__, mvt

VERSION = 4                     # 作り方を変えたら上げる（残した図を作り直す）。2：ゼブラ（縁石）を足した。3：真ん中の線から測り直す。4：こぶ取り・ゼブラを帯に
BV_URL = 'https://cyberjapandata.gsi.go.jp/xyz/experimental_bvmap/{z}/{x}/{y}.pbf'
BV_Z = 16                       # 道路縁（縮尺 1/2500 の元）が入っている細かさ
PHOTO_Z = 18
STEP_M = 2.0                    # 横断面の間隔
REACH_M = 22.0                  # 案内の線から横へ探す遠さ
RAY_STEP_M = 0.25               # 写真を読む間隔
OFF_RUN_M = 1.5                 # 舗装でない色がこれだけ続いたら、その頭を縁とする
EDGE_ANGLE_DEG = 35.0
SPIKE_M = 3.0                   # 前後のならしよりこれだけ外れた値は飛びとみなす
JUMP_M = 2.0                    # 隣の横断面と縁の距離がこれより違えば線を切る（脇道の合流など。斜めの線でつながない）
USED_Q = 90                     # 走行の全部の測位が横へ散らばった幅（通った回の左右それぞれ、この百分位。コースを外れた数回・ピットは数えない）
PASS_GAP_S = 5.0                # 同じ横断面の近くを、これより離れた時刻に通ったら別の回
USED_WIN = 2                    # 幅を見る範囲（横断面の前後この数）
USED_SLACK_M = 1.5              # 走った幅よりこれだけ内側までは縁として認める（測位と地図の誤差。車の中心は縁から1 m ほど内）
EDGE_CODES = range(2200, 2300)  # 道路縁（2201 真幅道路 など）。27xx は道路の中心線なので使わない
EDGE_LEVEL = '2500'
M_PER_DEG = 111320.0
CENTER_WIN = 41                         # 真ん中の線：半分の幅をこの横断面の数（×2 m）の中央値と比べる（FILL_GAP の倍より長く）
CENTER_TOL_M = 0.3                      # 半分の幅がそこからこれだけ外れた所は、片側が別の縁を拾ったか、こぶとみなす
CENTER_GROW = 3                         # その前後この横断面の数も使わない
CENTER_SMOOTH = 9                       # 真ん中の線のならし（横断面の数）
FILL_TOL_M = 3.0                        # 2回目：縁は半分の幅からこれだけの内に探す（外は別の道の縁）
FILL_GAP = 35                           # 2回目：抜けがこの横断面の数（×2 m）までなら補う（ピット出口の合流など）
FILL_SLOPE = 0.06                       # 2回目：補う抜けの両端の差は JUMP_M か、抜けの長さのこれだけ倍まで
MIN_PIECE = 5                           # 2回目：これより短い切れ端は捨てる
BUMP_WIN = 51                           # こぶ取り：前後この横断面の数（×2 m）の中央値と比べる
BUMP_M = 0.5                            # こぶ取り：中央値からこれだけ飛び出したらこぶ
BUMP_FOOT = 0.2                         # こぶ取り：中央値から BUMP_M のこれだけ倍より出ている所までをこぶの裾とみなす
BUMP_GROW = 1                           # こぶ取り：裾のさらに前後この横断面の数も補い直す
ZEBRA_GAP = 5                           # ゼブラの区間：抜けがこの横断面の数（×2 m）までならつなぐ
ZEBRA_MIN = 6                           # ゼブラの区間：この横断面の数（×2 m）より短いものは捨てる（ピット出口の矢羽根など）
ZEBRA_W_M = 1.0                         # ゼブラの帯の幅（m）。写真では幅を読み切れないので決まった幅で描く
KERB_IN_M, KERB_OUT_M = 3.0, 4.0        # ゼブラ（赤白の縁石）を、縁から内へ・外へこれだけの幅で探す
KERB_ALONG_M = 2.0                      # 走る向きに前後これだけの幅で見る（赤と白が1 mほどで交互に並ぶ）
KERB_ALONG_STEP_M = 0.5
KERB_MIN_FLIPS = 2                      # 白と赤みの入れ替わりがこれだけあればゼブラ（白線は入れ替わらない）
KERB_GAP = 2                            # 写真の拾いの抜けがこの横断面の数までならつなぐ
KERB_MIN_RUN = 1                        # 写真の1つの横断面ごとの拾い（まとめは build で）
TIMEOUT_S = 8
MAX_BYTES = 2 << 20
ATTRIBUTION = '国土地理院の地理院地図Vector（道路縁）と航空写真を加工して作成'


class OutlineError(Exception):
    pass


def _fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': f'EcoLogger-Analysis/{__version__}'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        return e.code, b''


def tile_xy(lat, lon, z):
    """緯度経度 → その細かさのタイルの番号（小数つき）"""
    n = 2 ** z
    lat = np.clip(np.asarray(lat, float), -85.0, 85.0)
    x = (np.asarray(lon, float) + 180.0) / 360.0 * n
    y = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tile_latlon(x, y, z):
    n = 2 ** z
    lon = np.asarray(x, float) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1.0 - 2.0 * np.asarray(y, float) / n))))
    return lat, lon


def _smooth_circular(v, w):
    if len(v) < w:
        return v.copy()
    k = w // 2
    p = np.concatenate((v[-k:], v, v[:k]))
    return np.convolve(p, np.ones(w) / w, 'valid')


def _nanmean_circular(v, w):
    """NaN を見ない移動平均（輪として前後をつなぐ）"""
    ok = np.isfinite(v)
    num = _smooth_circular(np.where(ok, v, 0.0), w)
    den = _smooth_circular(ok.astype(float), w)
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(den > 0, num / den, np.nan)


def _median_circular(v, w):
    """飛んだ値に強いならし（NaN は見ない）。輪（周）として前後をつなぐ"""
    k = w // 2
    p = np.concatenate((v[-k:], v, v[:k])) if len(v) > k else v
    win = np.lib.stride_tricks.sliding_window_view(p, w) if len(p) >= w else p[None, :]
    with np.errstate(all='ignore'):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            return np.nanmedian(win, axis=1)[:len(v)]


def _runs(flags):
    """True が続く所の番号のリスト（輪として、頭と終わりをつなぐ）"""
    runs, cur = [], []
    for i, f in enumerate(flags):
        if f:
            cur.append(i)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][-1] == len(flags) - 1:
        runs[0] = runs.pop() + runs[0]
    return runs


def guide(lat, lon, closed=True):
    """周の線を STEP_M おきの点にし、ならす。(原点, x, y, 左向きの単位ベクトル nx, ny)"""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    ok = np.isfinite(lat) & np.isfinite(lon)
    lat, lon = lat[ok], lon[ok]
    if len(lat) < 10:
        raise OutlineError('案内にする周の測位が足りない')
    lat0, lon0 = float(lat.mean()), float(lon.mean())
    kx = M_PER_DEG * math.cos(math.radians(lat0))
    x, y = (lon - lon0) * kx, (lat - lat0) * M_PER_DEG
    s = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))))
    if s[-1] < 50 * STEP_M:
        raise OutlineError('案内にする周が短すぎる')
    ss = np.arange(0.0, s[-1], STEP_M)
    gx, gy = np.interp(ss, s, x), np.interp(ss, s, y)
    gx, gy = _smooth_circular(gx, 5), _smooth_circular(gy, 5)
    tx, ty = np.gradient(gx), np.gradient(gy)
    if closed:              # 周の頭と終わりをつなぐ
        tx[0] = tx[-1] = gx[1] - gx[-1]
        ty[0] = ty[-1] = gy[1] - gy[-1]
    norm = np.hypot(tx, ty)
    norm[norm == 0] = 1.0
    tx, ty = tx / norm, ty / norm
    return (lat0, lon0), gx, gy, -ty, tx


class Outlines:
    def __init__(self, home, photos, fetch=_fetch):
        """photos：tiles.Tiles（航空写真。get(z, x, y) → (状態, JPEG)）"""
        self.home = Path(home)
        self.dir = self.home / 'outlines'
        self.vdir = self.home / 'tiles' / 'gsi-bvmap'
        self.photos = photos
        self.fetch = fetch
        self.lock = threading.Lock()
        self.memo = {}
        self._pcache = {}           # 1回の作りのあいだ、読んだ写真を覚える

    # ---- 残す・読む
    @staticmethod
    def key(course, lat, lon):
        base = course or f'{float(np.nanmean(lat)):.3f},{float(np.nanmean(lon)):.3f}'
        return hashlib.sha1(f'v{VERSION}:{base}'.encode('utf-8')).hexdigest()[:16]

    def cached(self, key):
        with self.lock:
            if key in self.memo:
                return self.memo[key]
        try:
            out = json.loads((self.dir / f'{key}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(out, dict) or out.get('version') != VERSION:
            return None
        with self.lock:
            self.memo[key] = out
        return out

    def _save(self, key, out):
        with self.lock:
            self.memo[key] = out
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            f = self.dir / f'{key}.json'
            tmp = f.with_name(f'{f.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            tmp.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, f)
        except OSError:
            pass

    def get(self, course, lat, lon, rebuild=False, t_all=None, lat_all=None, lon_all=None, laps=0):
        """コース図 {version, course, laps, lines: [[[lat, lon], …]], share: {gsi, photo, none}, attribution, complete, width_m}。
        laps：走った幅を見た周の数。残した図より多い周の走行で頼まれたら作り直す（周が多いほど、コースの中を通る
        地図の線を見分けやすい）"""
        key = self.key(course, lat, lon)
        if not rebuild:
            hit = self.cached(key)
            if hit and hit.get('laps', 0) >= laps:
                return hit
        out = self.build(lat, lon, t_all, lat_all, lon_all)
        out['course'], out['laps'] = course, int(laps)
        if out['complete']:             # タイルを取れなかった所があるときは残さない（次につながったら作り直す）
            self._save(key, out)
        return out

    # ---- タイル
    def _vector(self, x, y):
        f = self.vdir / str(BV_Z) / str(x) / f'{y}.pbf'
        try:
            return mvt.read(f.read_bytes()), True
        except (OSError, mvt.TileError):
            pass
        try:
            code, body = self.fetch(BV_URL.format(z=BV_Z, x=x, y=y))
        except (OSError, ValueError):
            return {}, False
        if code == 404:
            return {}, True
        if code != 200 or len(body) > MAX_BYTES:
            return {}, False
        try:
            layers = mvt.read(body)
        except mvt.TileError:
            return {}, False
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f'{f.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            tmp.write_bytes(body)
            os.replace(tmp, f)
        except OSError:
            pass
        return layers, True

    def edges(self, origin, x, y):
        """案内の線のまわりの道路縁を、線分の配列 (A, B)（原点からの m）にする。と、全部のタイルを読めたか"""
        lat0, lon0 = origin
        kx = M_PER_DEG * math.cos(math.radians(lat0))
        lat = lat0 + np.array([y.min() - REACH_M - 30, y.max() + REACH_M + 30]) / M_PER_DEG
        lon = lon0 + np.array([x.min() - REACH_M - 30, x.max() + REACH_M + 30]) / kx
        tx, ty = tile_xy(lat, lon, BV_Z)
        segs, complete = [], True
        for X in range(int(tx.min()), int(tx.max()) + 1):
            for Y in range(int(ty.min()), int(ty.max()) + 1):
                layers, ok = self._vector(X, Y)
                complete &= ok
                ext, feats = layers.get('road', (4096, []))
                for _g, props, parts in feats:
                    if props.get('ftCode') not in EDGE_CODES or str(props.get('orgGILvl')) != EDGE_LEVEL:
                        continue
                    for part in parts:
                        if len(part) < 2:
                            continue
                        p = np.asarray(part, float)
                        la, lo = tile_latlon(X + p[:, 0] / ext, Y + p[:, 1] / ext, BV_Z)
                        px, py = (lo - lon0) * kx, (la - lat0) * M_PER_DEG
                        segs.append(np.stack((px[:-1], py[:-1], px[1:], py[1:]), axis=1))
        if not segs:
            return np.zeros((0, 4)), complete
        s = np.concatenate(segs)
        keep = ((np.maximum(s[:, 0], s[:, 2]) >= x.min() - REACH_M) & (np.minimum(s[:, 0], s[:, 2]) <= x.max() + REACH_M) &
                (np.maximum(s[:, 1], s[:, 3]) >= y.min() - REACH_M) & (np.minimum(s[:, 1], s[:, 3]) <= y.max() + REACH_M))
        return s[keep], complete

    def _photo(self, cache, X, Y):
        k = (X, Y)
        if k not in cache:
            from PIL import Image          # 重いので使うときだけ読む
            state, body = self.photos.get(PHOTO_Z, X, Y)
            arr = None
            if state == 'ok':
                try:
                    arr = np.asarray(Image.open(io.BytesIO(body)).convert('RGB'), dtype=np.int16)
                except (OSError, ValueError):
                    arr = None
            cache[k] = (arr, state in ('ok', 'none'))
        return cache[k]

    # ---- 横断面
    @staticmethod
    def ray_hits(gx, gy, nx, ny, tx, ty, segs, tmin=None, tmax=None):
        """各点から ±法線の向きへ伸ばした線が、tmin (n, 2) より外で最初に当たる道路縁までの距離 (n, 2)（左, 右）。
        当たらなければ NaN"""
        out = np.full((len(gx), 2), np.nan)
        if tmin is None:
            tmin = np.zeros((len(gx), 2))
        if tmax is None:
            tmax = np.full((len(gx), 2), REACH_M)
        if not len(segs):
            return out
        ax, ay, bx, by = segs.T
        ex, ey = bx - ax, by - ay
        el = np.hypot(ex, ey)
        el[el == 0] = 1e-9
        sin_max = math.sin(math.radians(EDGE_ANGLE_DEG))
        CH = 256
        for i0 in range(0, len(gx), CH):
            sl = slice(i0, i0 + CH)
            ox, oy = gx[sl, None], gy[sl, None]
            near = (np.minimum(ax, bx)[None, :] <= ox + REACH_M) & (np.maximum(ax, bx)[None, :] >= ox - REACH_M) & \
                   (np.minimum(ay, by)[None, :] <= oy + REACH_M) & (np.maximum(ay, by)[None, :] >= oy - REACH_M)
            # 走る向きに沿う縁だけ
            along = np.abs(tx[sl, None] * ey[None, :] - ty[sl, None] * ex[None, :]) / el[None, :] < sin_max
            use = near & along
            for side, sg in ((0, 1.0), (1, -1.0)):
                dx, dy = sg * nx[sl, None], sg * ny[sl, None]
                den = dx * ey[None, :] - dy * ex[None, :]
                with np.errstate(divide='ignore', invalid='ignore'):
                    t = ((ax[None, :] - ox) * ey[None, :] - (ay[None, :] - oy) * ex[None, :]) / den
                    u = ((ax[None, :] - ox) * dy - (ay[None, :] - oy) * dx) / den
                hit = use & (np.abs(den) > 1e-9) & (t >= tmin[sl, side, None]) & (t <= tmax[sl, side, None]) & (u >= 0) & (u <= 1)
                t = np.where(hit, t, np.inf)
                m = t.min(axis=1)
                out[sl, side] = np.where(np.isfinite(m), m, np.nan)
        return out

    @staticmethod
    def paved(rgb):
        """灰色の舗装・白線・赤い縁石なら True（rgb は (…, 3) の int）"""
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
        kerb = (r > 140) & (r - g > 50)
        white = (mn > 185) & (mx - mn < 20)
        grey = (mx - mn < 30) & (mn > 35) & (b - g >= -2) & (r - b <= 4)
        return kerb | white | grey

    def sample(self, origin, px, py):
        """原点からの m の点（同じ形の配列）の航空写真の色。(rgb (…, 3), 読めたか (…), 全部のタイルを読めたか)"""
        lat0, lon0 = origin
        kx = M_PER_DEG * math.cos(math.radians(lat0))
        X, Y = tile_xy(lat0 + py / M_PER_DEG, lon0 + px / kx, PHOTO_Z)
        TX, TY = X.astype(np.int64), Y.astype(np.int64)
        col = np.clip(((X - TX) * 256).astype(np.int64), 0, 255)
        row = np.clip(((Y - TY) * 256).astype(np.int64), 0, 255)
        rgb = np.zeros(px.shape + (3,), np.int16)
        known = np.zeros(px.shape, bool)
        cache, complete = self._pcache, True
        keys = TX * (1 << 20) + TY
        for k in np.unique(keys):
            m = keys == k
            arr, ok = self._photo(cache, int(k >> 20), int(k & ((1 << 20) - 1)))
            complete &= ok
            if arr is None or arr.shape[0] < 256 or arr.shape[1] < 256:
                continue
            rgb[m] = arr[row[m], col[m]]
            known[m] = True
        return rgb, known, complete

    @staticmethod
    def kerb_pixels(rgb):
        """(白, 赤み)。写真（1画素 0.5 m ほど）では縁石の赤がにじんで、桃色がかった灰色になる"""
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
        white = (mn > 150) & (mx - mn < 40)
        pink = ~white & (r > 90) & (r - g >= 5) & (b - g >= -3)
        return white, pink

    @staticmethod
    def zebra_spans(kb, d):
        """片側の写真の拾い kb (n, 2)（内・外の距離）と縁 d (n,) から、ゼブラのある区間と、帯の真ん中の縁からの距離。
        [(横断面の番号の配列, 距離)]。写真では縞がにじんで途切れ途切れに拾えるので、ZEBRA_GAP までの抜けはつなぎ、
        短いものは捨てる。帯の位置は区間の中の中央値1つにする（縁に沿った揃った帯にするため）"""
        n = len(d)
        have = np.isfinite(kb[:, 0]) & np.isfinite(d)
        for run in _runs(~have):
            if len(run) <= ZEBRA_GAP and len(run) < n:
                a, b = (run[0] - 1) % n, (run[-1] + 1) % n
                if have[a] and have[b]:
                    have[run] = True
        out = []
        for run in _runs(have & np.isfinite(d)):
            if len(run) < ZEBRA_MIN:
                continue
            run = np.asarray(run)
            mid = (kb[run, 0] + kb[run, 1]) / 2 - d[run]
            off = float(np.nanmedian(mid)) if np.isfinite(mid).any() else ZEBRA_W_M / 2
            out.append((run, off))
        return out

    def kerbs(self, origin, gx, gy, nx, ny, tx, ty, d):
        """縁 d (n, 2) の近くのゼブラ（赤白の縁石）。左右それぞれ (内の距離, 外の距離) の配列 (n, 2, 2)（無ければ NaN）。
        縁に沿って白と赤みが交互に並ぶ所を探す（白線は途切れないので、交互の数で分ける）"""
        out = np.full((len(gx), 2, 2), np.nan)
        rr = np.arange(-KERB_IN_M, KERB_OUT_M + 1e-9, RAY_STEP_M)
        al = np.arange(-KERB_ALONG_M, KERB_ALONG_M + 1e-9, KERB_ALONG_STEP_M)
        for side, sg in ((0, 1.0), (1, -1.0)):
            idx = np.flatnonzero(np.isfinite(d[:, side]))
            if not len(idx):
                continue
            base = np.maximum(d[idx, side][:, None] + rr[None, :], 0.0)              # 案内からの距離 (k, r)
            px = gx[idx, None, None] + tx[idx, None, None] * al[None, None, :] + sg * nx[idx, None, None] * base[:, :, None]
            py = gy[idx, None, None] + ty[idx, None, None] * al[None, None, :] + sg * ny[idx, None, None] * base[:, :, None]
            rgb, known, _ok = self.sample(origin, px, py)
            white, pink = self.kerb_pixels(rgb)
            white &= known
            pink &= known
            flips = (np.diff(white.astype(int), axis=2) != 0).sum(axis=2)          # 縁に沿った白の入れ替わりの数
            col = (white.sum(axis=2) >= 2) & (pink.sum(axis=2) >= 1) & (flips >= KERB_MIN_FLIPS)
            for j in np.flatnonzero(col.sum(axis=1) >= 2):
                c = np.flatnonzero(col[j])
                # いちばん長く続く列のまとまり（1列の抜けはつなぐ）
                parts = np.split(c, np.flatnonzero(np.diff(c) > 2) + 1)
                pc = max(parts, key=len)
                if len(pc) < 2:
                    continue
                out[idx[j], side] = (base[j, pc[0]], base[j, pc[-1]] + RAY_STEP_M)
        # 続かないもの（1つ2つの横断面だけ）は捨て、1つ抜けた所はつなぐ
        for side in (0, 1):
            f = np.isfinite(out[:, side, 0])
            for run in _runs(~f):                           # 抜けが KERB_GAP 個まではつなぐ
                if len(run) > KERB_GAP or len(run) == len(f):
                    continue
                a, b = out[(run[0] - 1) % len(f), side], out[(run[-1] + 1) % len(f), side]
                out[run, side] = (a + b) / 2
            f = np.isfinite(out[:, side, 0])
            for run in _runs(f):
                if len(run) < KERB_MIN_RUN:
                    out[run, side] = np.nan
            for k in (0, 1):
                v = out[:, side, k]
                m = _median_circular(v, 3)
                out[:, side, k] = np.where(np.isfinite(v), np.where(np.isfinite(m), m, v), np.nan)
        return out

    def photo_edges(self, origin, gx, gy, nx, ny, need, tmin=None, tmax=None):
        """need (n, 2) が True の横断面だけ、航空写真で tmin より外の舗装の切れ目までの距離を探す。
        (距離 (n, 2), 写真を全部読めたか)"""
        out = np.full(need.shape, np.nan)
        idx = np.argwhere(need)
        if not len(idx):
            return out, True
        lat0, lon0 = origin
        kx = M_PER_DEG * math.cos(math.radians(lat0))
        r = np.arange(0.0, REACH_M + 1e-9, RAY_STEP_M)
        sg = np.where(idx[:, 1] == 0, 1.0, -1.0)
        px = gx[idx[:, 0], None] + (sg * nx[idx[:, 0]])[:, None] * r[None, :]
        py = gy[idx[:, 0], None] + (sg * ny[idx[:, 0]])[:, None] * r[None, :]
        rgb, known, complete = self.sample(origin, px, py)
        off = ~self.paved(rgb) & known
        if tmin is not None:                # 車が走った所までは路面（白い字や影で止まらない）
            off &= r[None, :] >= tmin[idx[:, 0], idx[:, 1]][:, None]
        n_run = int(round(OFF_RUN_M / RAY_STEP_M))
        # 舗装でない色が n_run 続く最初の所（その頭が縁）
        c = np.cumsum(np.concatenate((np.zeros((len(off), 1), int), off.astype(int)), axis=1), axis=1)
        run = (c[:, n_run:] - c[:, :-n_run]) == n_run
        first = np.where(run.any(axis=1), run.argmax(axis=1), -1)
        all_known = known.all(axis=1)
        d = np.where((first >= 0), r[np.maximum(first, 0)], np.nan)
        d = np.where((first < 0) | ~known[np.arange(len(first)), np.maximum(first, 0)], np.nan, d)
        d[(first < 0) & ~all_known] = np.nan
        if tmax is not None:
            d[d > tmax[idx[:, 0], idx[:, 1]]] = np.nan
        out[idx[:, 0], idx[:, 1]] = d
        return out, complete

    # ---- まとめ
    @staticmethod
    def used_width(origin, gx, gy, nx, ny, t_all, lat_all, lon_all):
        """走行の全部の測位が、案内の線から左右へ散らばった幅 (n, 2)（左, 右。点が無ければ 0）。
        横断面ごとに「通った回」（PASS_GAP_S より離れた時刻で分ける）の横の位置をそれぞれ1つにまとめ、
        回の中で左右の USED_Q の百分位を取る。止まっていた所・ゆっくり動いた所が点の数で重くならないように"""
        out = np.zeros((len(gx), 2))
        if t_all is None or lat_all is None or lon_all is None:
            return out
        lat0, lon0 = origin
        t, la, lo = np.asarray(t_all, float), np.asarray(lat_all, float), np.asarray(lon_all, float)
        ok = np.isfinite(t) & np.isfinite(la) & np.isfinite(lo)
        t = t[ok]
        px, py = (lo[ok] - lon0) * M_PER_DEG * math.cos(math.radians(lat0)), (la[ok] - lat0) * M_PER_DEG
        keep = (px >= gx.min() - REACH_M) & (px <= gx.max() + REACH_M) & (py >= gy.min() - REACH_M) & (py <= gy.max() + REACH_M)
        t, px, py = t[keep], px[keep], py[keep]
        idx = np.empty(len(px), np.int64)
        lat_off = np.empty(len(px))
        CH = 2048
        for i0 in range(0, len(px), CH):
            dx = px[i0:i0 + CH, None] - gx[None, :]
            dy = py[i0:i0 + CH, None] - gy[None, :]
            k = np.argmin(dx * dx + dy * dy, axis=1)
            r = np.arange(len(k))
            idx[i0:i0 + CH] = k
            lat_off[i0:i0 + CH] = dx[r, k] * nx[k] + dy[r, k] * ny[k]
        near = np.abs(lat_off) < REACH_M
        t, idx, lat_off = t[near], idx[near], lat_off[near]
        order = np.lexsort((t, idx))
        t, idx, lat_off = t[order], idx[order], lat_off[order]
        n = len(gx)
        for i in range(n):
            ks = [(i + j) % n for j in range(-USED_WIN, USED_WIN + 1)]
            sl = [slice(np.searchsorted(idx, k), np.searchsorted(idx, k, 'right')) for k in ks]
            tt = np.concatenate([t[x] for x in sl])
            if not len(tt):
                continue
            vv = np.concatenate([lat_off[x] for x in sl])
            o = np.argsort(tt)
            tt, vv = tt[o], vv[o]
            cut = np.flatnonzero(np.diff(tt) > PASS_GAP_S) + 1
            passes = np.array([np.median(x) for x in np.split(vv, cut)])
            out[i, 0] = max(0.0, float(np.percentile(passes, USED_Q)))
            out[i, 1] = max(0.0, -float(np.percentile(passes, 100 - USED_Q)))
        return out

    def measure(self, origin, gx, gy, nx, ny, segs, tmin, tmax=None):
        """横断面ごとの左右の縁までの距離 (n, 2) と、どこから取ったか (n, 2)（1 地図・2 写真・0 無し）、写真を全部読めたか"""
        tx, ty = ny, -nx                    # 走る向き（左向きの法線を右へ90°）
        d_gsi = self.ray_hits(gx, gy, nx, ny, tx, ty, segs, tmin, tmax)
        d_photo, ok_p = self.photo_edges(origin, gx, gy, nx, ny, np.isnan(d_gsi), tmin, tmax)
        src = np.where(np.isfinite(d_gsi), 1, np.where(np.isfinite(d_photo), 2, 0))
        d = np.where(src == 1, d_gsi, d_photo)
        for side in (0, 1):
            v = d[:, side]
            med = _median_circular(v, 9)
            bad = np.abs(v - med) > SPIKE_M
            v = np.where(bad, med, v)
            ph = src[:, side] == 2      # 写真から取った所はさらにならす（色の揺れで細かくぶれる）
            sm = _nanmean_circular(_median_circular(v, 11), 5)
            v = np.where(ph & np.isfinite(v) & np.isfinite(sm), sm, v)
            d[:, side] = v
        return d, src, ok_p

    @staticmethod
    def center(gx, gy, nx, ny, d):
        """1回目の縁から、コースの真ん中の線 (x, y) と、そこから縁までの半分の幅のならした値 (n,)。無理なら None。
        走行ラインは縁に触れたり（クリップ）測位のずれで縁の外へ出たりするので、2回目は真ん中から測る"""
        n = len(gx)
        both = np.isfinite(d[:, 0]) & np.isfinite(d[:, 1])
        half = np.where(both, (d[:, 0] + d[:, 1]) / 2, np.nan)
        bad = both & (np.abs(half - _median_circular(half, CENTER_WIN)) >= CENTER_TOL_M)  # 片側が別の縁を拾った所・こぶは使わない
        for k in range(1, CENTER_GROW + 1):
            bad = bad | np.roll(bad, k) | np.roll(bad, -k)
        ok = both & ~bad
        good = np.flatnonzero(ok)
        if len(good) < n * 0.3:
            return None
        i = np.arange(n)
        xp = np.r_[good - n, good, good + n]

        def spread(v):                  # 抜けは輪として線形に補う
            return np.interp(i, xp, np.tile(v[good], 3))
        off = _smooth_circular(spread((d[:, 0] - d[:, 1]) / 2), CENTER_SMOOTH)
        ref = spread(_median_circular(np.where(ok, half, np.nan), CENTER_WIN))
        return gx + nx * off, gy + ny * off, ref

    @staticmethod
    def fill(d, ref):
        """2回目の縁の、外れの捨て・短い切れ端を捨てる・抜けの補い。ref：半分の幅のならした値 (n,)"""
        n = len(d)
        out = d.copy()
        for side in (0, 1):
            v = out[:, side]
            v[np.abs(v - ref) > FILL_TOL_M] = np.nan        # 半分の幅から大きく外れた所（別の道の縁）
            for run in _runs(np.isfinite(v)):               # 短い切れ端は捨てる
                if len(run) < MIN_PIECE:
                    v[run] = np.nan
            v = Outlines.debump(v)                          # 抜けの手前で縁が曲がり込んでいると、両端が合わず補えないので先にも削る
            for run in _runs(~np.isfinite(v)):              # FILL_GAP 個までの抜けは、両端が近ければ線形に補う
                if len(run) > FILL_GAP or len(run) == n:
                    continue
                a, b = (run[0] - 1) % n, (run[-1] + 1) % n
                if abs(v[a] - v[b]) > max(JUMP_M, FILL_SLOPE * STEP_M * (len(run) + 1)):
                    continue
                w = (np.arange(len(run)) + 1) / (len(run) + 1)
                v[run] = v[a] + (v[b] - v[a]) * w
            out[:, side] = Outlines.debump(v)
        return out

    @staticmethod
    def debump(v):
        """真ん中から縁までの距離 v (n,) の、短いこぶ・切れ込みを削る。縁は前後数十 m ではゆっくりしか変わらないので、
        前後 BUMP_WIN の中央値から BUMP_M より飛び出した所（脇道との合流で地図の縁が曲がり込む所など）を、前後から補い直す。
        中央値は坂（幅がだんだん変わる所）にはそのまま沿うので、本当の幅の変わり目は削らない"""
        n = len(v)
        v = v.copy()
        for _ in range(2):
            med = _median_circular(v, BUMP_WIN)
            dev = np.where(np.isfinite(v) & np.isfinite(med), np.abs(v - med), 0.0)
            bad = dev > BUMP_M
            if not bad.any():
                break
            # こぶの裾も入れる：BUMP_M の BUMP_FOOT 倍より飛び出している続きのうち、こぶを含むもの
            soft = dev > BUMP_M * BUMP_FOOT
            grow = np.zeros(n, bool)
            for run in _runs(soft):
                if bad[run].any():
                    grow[run] = True
            g2 = grow.copy()
            for k in range(1, BUMP_GROW + 1):
                g2 |= np.roll(grow, k) | np.roll(grow, -k)
            grow = g2 & np.isfinite(v)
            for run in _runs(grow):
                if len(run) >= n:
                    continue
                a, b = (run[0] - 1) % n, (run[-1] + 1) % n
                if not (np.isfinite(v[a]) and np.isfinite(v[b])):
                    v[run] = med[run]
                    continue
                w = (np.arange(len(run)) + 1) / (len(run) + 1)
                v[run] = v[a] + (v[b] - v[a]) * w
        return v

    def build(self, lat, lon, t_all=None, lat_all=None, lon_all=None):
        """lat・lon：案内にする周。t_all・lat_all・lon_all：走行の全部の測位（縁がコースの中を通っていないかを見る）。
        1回目は案内の周の線から縁を測り、そこからコースの真ん中の線を出して、2回目は真ん中から測り直す"""
        self._pcache = {}
        origin, gx, gy, nx, ny = guide(lat, lon)
        segs, ok_v = self.edges(origin, gx, gy)
        used = self.used_width(origin, gx, gy, nx, ny, t_all, lat_all, lon_all)
        d, src, ok_p = self.measure(origin, gx, gy, nx, ny, segs, np.maximum(used - USED_SLACK_M, 0.0))
        c = self.center(gx, gy, nx, ny, d)
        if c is not None:
            cx, cy, ref0 = c
            lat0, lon0 = origin
            kx = M_PER_DEG * math.cos(math.radians(lat0))
            origin2, gx2, gy2, nx2, ny2 = guide(lat0 + cy / M_PER_DEG, lon0 + cx / kx)
            # 1回目の半分の幅を、2回目の点へ近いものから写す
            dx = (origin2[1] - lon0) * kx
            dy = (origin2[0] - lat0) * M_PER_DEG
            near = np.array([int(np.argmin((cx - x - dx) ** 2 + (cy - y - dy) ** 2)) for x, y in zip(gx2, gy2)])
            ref = ref0[near]
            used2 = self.used_width(origin2, gx2, gy2, nx2, ny2, t_all, lat_all, lon_all)
            lo = np.maximum(np.maximum(used2 - USED_SLACK_M, 0.0), np.maximum(ref - FILL_TOL_M, 0.0)[:, None])
            hi = np.repeat((ref + FILL_TOL_M)[:, None], 2, axis=1)
            segs2, ok_v2 = self.edges(origin2, gx2, gy2)
            d2, src2, ok_p2 = self.measure(origin2, gx2, gy2, nx2, ny2, segs2, lo, hi)
            d2 = self.fill(d2, ref)
            src2 = np.where(np.isfinite(d2) & (src2 == 0), 3, np.where(np.isfinite(d2), src2, 0))   # 3：補った所
            origin, gx, gy, nx, ny, d, src = origin2, gx2, gy2, nx2, ny2, d2, src2
            ok_v, ok_p = ok_v and ok_v2, ok_p and ok_p2
        tx, ty = ny, -nx
        lat0, lon0 = origin
        kx = M_PER_DEG * math.cos(math.radians(lat0))
        lines = []
        for side, sg in ((0, 1.0), (1, -1.0)):
            ex, ey = gx + sg * nx * d[:, side], gy + sg * ny * d[:, side]
            good = np.isfinite(ex) & np.isfinite(ey)
            jump = np.abs(np.diff(d[:, side], append=d[0, side])) > JUMP_M     # i と i+1 のあいだで切る
            pts = np.stack((lat0 + ey / M_PER_DEG, lon0 + ex / kx), axis=1)
            runs, cur = [], []
            for i in range(len(good)):
                if good[i]:
                    cur.append(i)
                    if jump[i]:
                        runs.append(cur)
                        cur = []
                elif cur:
                    runs.append(cur)
                    cur = []
            if cur:
                runs.append(cur)
            if len(runs) > 1 and runs[0][0] == 0 and runs[-1][-1] == len(good) - 1 and not jump[-1]:
                runs[0] = runs.pop() + runs[0]          # 周の頭と終わりをつなぐ
            elif len(runs) == 1 and len(runs[0]) == len(good) and not jump[-1]:
                runs[0] = runs[0] + [runs[0][0]]        # 1周つながっている：閉じる
            for rr in runs:
                if len(rr) >= 3:
                    lines.append([[round(float(pts[i, 0]), 7), round(float(pts[i, 1]), 7)] for i in rr])
        n = src.size
        share = {'gsi': round(float((src == 1).sum()) / n, 3), 'photo': round(float((src == 2).sum()) / n, 3),
                 'filled': round(float((src == 3).sum()) / n, 3), 'none': round(float((src == 0).sum()) / n, 3)}
        width = d[:, 0] + d[:, 1]
        kb = self.kerbs(origin, gx, gy, nx, ny, tx, ty, d)
        self._pcache = {}
        kerbs = []
        for side, sg in ((0, 1.0), (1, -1.0)):
            for run, off in self.zebra_spans(kb[:, side], d[:, side]):
                c = d[run, side] + off                      # 縁からの決まった距離に帯の真ん中
                xs, ys = gx[run] + sg * nx[run] * c, gy[run] + sg * ny[run] * c
                kerbs.append({'w': ZEBRA_W_M,
                              'pts': [[round(float(lat0 + b / M_PER_DEG), 7), round(float(lon0 + a / kx), 7)] for a, b in zip(xs, ys)]})
        return {'version': VERSION, 'lines': lines, 'kerbs': kerbs, 'share': share, 'attribution': ATTRIBUTION,
                'complete': bool(ok_v and ok_p),
                'width_m': [round(float(np.nanpercentile(width, q)), 1) for q in (5, 50, 95)] if np.isfinite(width).any() else None}
