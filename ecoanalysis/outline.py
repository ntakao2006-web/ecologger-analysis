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
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from . import __version__, mvt

VERSION = 5                     # 作り方を変えたら上げる（残した図を作り直す）。2：ゼブラ（縁石）を足した。3：真ん中の線から測り直す。4：こぶ取り・ゼブラを帯に。5：縁をゼブラの内側の端へ
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
ZEBRA_RAMP = 4                          # 縁をゼブラへ寄せるとき、区間の前後この横断面の数でなだらかに戻す
ZEBRA_SHIFT_MAX_M = 3.5                 # 縁をゼブラへ寄せるのはこの距離まで（それより離れたゼブラは何かの見違い）
KERB_IN_M, KERB_OUT_M = 3.0, 4.0        # ゼブラ（赤白の縁石）を、縁から内へ・外へこれだけの幅で探す
KERB_ALONG_M = 2.0                      # 走る向きに前後これだけの幅で見る（赤と白が1 mほどで交互に並ぶ）
KERB_ALONG_STEP_M = 0.5
KERB_MIN_FLIPS = 2                      # 白と赤みの入れ替わりがこれだけあればゼブラ（白線は入れ替わらない）
KERB_GAP = 2                            # 写真の拾いの抜けがこの横断面の数までならつなぐ
KERB_MIN_RUN = 1                        # 写真の1つの横断面ごとの拾い（まとめは build で）
SNAP_STEP_M = 1.0                       # なぞった線を吸い付かせるときの点の間隔
SNAP_REACH_M = 4.0                      # なぞった線から横へ探す遠さ（画面の倍率から決めて送られてこなければ）
SNAP_REACH_MIN_M, SNAP_REACH_MAX_M = 1.0, 8.0
SNAP_RES_M = 0.25                       # 横の候補の間隔
SNAP_WIN_M = 1.5                        # 縁の内と外の路面の割合を見る幅
SNAP_ALONG_M = 1.0                      # 前後これだけの幅でならす（ゼブラの縞・白い字で揺れないように）
SNAP_HINT = 0.5                         # なぞった線から離れる損（REACH の所で）
SNAP_HINT_POW = 2.0                      # 離れの損の形（2 は二乗、1 は距離に比例）
SNAP_MOVE = 0.25                        # 前後の点で横へ動く損（1 m あたり）
SNAP_MAX_SHIFT_M = 0.5                  # 前後の点（SNAP_STEP_M）で横へ動けるのはここまで
SNAP_GSI = 0.5                          # 地図の道路縁が通る所に足す縁らしさ
SNAP_GSI_SIGMA_M = 0.4
SNAP_LINE = 0.8                         # 白線の縁らしさの上限（路面と草の境目より少し弱く）
SNAP_LINE_CONTRAST = 40.0               # 白線の真ん中が両脇よりこれだけ明るければ上限
SNAP_C_LUM, SNAP_C_HUE = 25.0, 5.0      # 色の段：明るさはこれだけ、青み（b−g）・赤み（r−b）はこれだけの差を1と数える
SNAP_BINARY = 0.5                       # 路面かどうかの段の重み（写真の色はにじみが倍あって縁が路面側へ寄るので、場所は色の段で決める）
SNAP_C_W = 1.0                          # 色の段の縁らしさの上限
SNAP_C_FLOOR, SNAP_C_FULL = 0.3, 1.2    # 色の段がこれより小さければ0（舗装のむら）、これだけ上で1
SNAP_FOUND = 0.35                      # 縁らしさがこれ以上の所を「見つかった」と数える
SNAP_MIN_M, SNAP_MAX_M = 4.0, 3000.0
SNAP_MAX_POINTS = 20000
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


EDIT_MAX_LINES = 400
EDIT_MAX_POINTS = 200000                # 線・ゼブラの点の数の合計
EDIT_MAX_KERBS = 1000
EDIT_MAX_BOARDS = 300
BOARD_TEXT_MAX = 12


def _check_pts(pts, what):
    if not isinstance(pts, list) or len(pts) < 2:
        raise OutlineError(f'{what}の点が足りない')
    out = []
    for q in pts:
        if not (isinstance(q, (list, tuple)) and len(q) == 2):
            raise OutlineError(f'{what}の点の形が違う')
        la, lo = q
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (la, lo)) \
                or abs(la) > 90 or abs(lo) > 180:
            raise OutlineError(f'{what}の点の数が違う')
        out.append([round(float(la), 7), round(float(lo), 7)])
    return out


def check_edit(data):
    """手で直したコース図 {lines: [[[lat, lon], …]], kerbs: [{pts, ws, side} か前の形 {w, pts}], boards: [{lat, lon, text}]} の形を確かめて、決まった形にして返す"""
    if not isinstance(data, dict):
        raise OutlineError('形が違う')
    lines_, kerbs_, boards_ = data.get('lines'), data.get('kerbs', []), data.get('boards', [])
    if not isinstance(lines_, list) or not isinstance(kerbs_, list) or not isinstance(boards_, list):
        raise OutlineError('形が違う')
    if len(lines_) > EDIT_MAX_LINES or len(kerbs_) > EDIT_MAX_KERBS or len(boards_) > EDIT_MAX_BOARDS:
        raise OutlineError('数が多すぎる')
    lines_ = [_check_pts(ln, '縁') for ln in lines_]
    out_k = []
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    for k in kerbs_:
        if not isinstance(k, dict):
            raise OutlineError('ゼブラの形が違う')
        pts = _check_pts(k.get('pts'), 'ゼブラ')
        if 'ws' in k:       # 縁にくっつく形：pts は内側の端、ws は点ごとの幅（端は 0 まで細る）、side は外側の向き
            ws, side = k.get('ws'), k.get('side')
            if not isinstance(ws, list) or len(ws) != len(pts) or not all(num(w) and 0 <= w <= 5 for w in ws) or max(ws) <= 0:
                raise OutlineError('ゼブラの幅が違う')
            if side not in (1, -1):
                raise OutlineError('ゼブラの向きが違う')
            out_k.append({'pts': pts, 'ws': [round(float(w), 2) for w in ws], 'side': side})
            continue
        w = k.get('w', ZEBRA_W_M)           # 前の形：pts は帯の真ん中、幅は決まった w
        if not num(w) or not (0.2 <= w <= 5):
            raise OutlineError('ゼブラの幅が違う')
        out_k.append({'w': float(w), 'pts': pts})
    if sum(len(x) for x in lines_) + sum(len(k['pts']) for k in out_k) > EDIT_MAX_POINTS:
        raise OutlineError('点が多すぎる')
    out_b = []
    for b in boards_:
        if not isinstance(b, dict):
            raise OutlineError('看板の形が違う')
        la, lo = _check_pts([[b.get('lat'), b.get('lon')], [0, 0]], '看板')[0]      # 2点目は形を合わせるだけ
        text = b.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > BOARD_TEXT_MAX:
            raise OutlineError(f'看板の字は{BOARD_TEXT_MAX}字まで')
        out_b.append({'lat': la, 'lon': lo, 'text': text.strip()})
    return {'lines': lines_, 'kerbs': out_k, 'boards': out_b}


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

    # ---- 手で直したコース図（コースごと。作り方の版が変わっても残す）
    @staticmethod
    def edit_key(course, lat, lon):
        base = course or f'{float(np.nanmean(lat)):.3f},{float(np.nanmean(lon)):.3f}'
        return 'edit-' + hashlib.sha1(f'edit:{base}'.encode('utf-8')).hexdigest()[:16]

    def edited(self, course, lat, lon):
        try:
            out = json.loads((self.dir / f'{self.edit_key(course, lat, lon)}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        try:
            e = check_edit(out)
        except OutlineError:
            return None
        w = out.get('width_m')
        e['width_m'] = float(w) if isinstance(w, (int, float)) and not isinstance(w, bool) and 1 <= w <= 60 else None
        e['saved_at'] = out.get('saved_at') if isinstance(out.get('saved_at'), str) else None
        return e

    def save_edit(self, course, lat, lon, data, width_m=None):
        """手で直した {lines, kerbs, boards} を残す（形を確かめてから）。返り値は残した中身"""
        out = check_edit(data)
        out['width_m'] = width_m
        out['course'] = course
        out['saved_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        self.dir.mkdir(parents=True, exist_ok=True)
        f = self.dir / f'{self.edit_key(course, lat, lon)}.json'
        tmp = f.with_name(f'{f.name}.{os.getpid()}.{threading.get_ident()}.tmp')
        tmp.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, f)
        return out

    def clear_edit(self, course, lat, lon):
        try:
            (self.dir / f'{self.edit_key(course, lat, lon)}.json').unlink()
            return True
        except FileNotFoundError:
            return False

    def get(self, course, lat, lon, rebuild=False, t_all=None, lat_all=None, lon_all=None, laps=0, auto=False):
        """コース図。手で直したものがあればそちら（edited: True。自動で作り直さなくても出せる）。auto=True なら自動のものだけ"""
        if not auto:
            e = self.edited(course, lat, lon)
            if e is not None:
                return {'version': VERSION, 'course': course, 'lines': e['lines'], 'kerbs': e['kerbs'], 'boards': e['boards'],
                        'attribution': ATTRIBUTION, 'complete': True, 'edited': True, 'saved_at': e.get('saved_at'),
                        'width_m': e.get('width_m'), 'share': None, 'laps': laps}
        return self.get_auto(course, lat, lon, rebuild, t_all, lat_all, lon_all, laps)

    def get_auto(self, course, lat, lon, rebuild=False, t_all=None, lat_all=None, lon_all=None, laps=0):
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

    def sample(self, origin, px, py, cache=None):
        """原点からの m の点（同じ形の配列）の航空写真の色。(rgb (…, 3), 読めたか (…), 全部のタイルを読めたか)"""
        lat0, lon0 = origin
        kx = M_PER_DEG * math.cos(math.radians(lat0))
        X, Y = tile_xy(lat0 + py / M_PER_DEG, lon0 + px / kx, PHOTO_Z)
        TX, TY = X.astype(np.int64), Y.astype(np.int64)
        col = np.clip(((X - TX) * 256).astype(np.int64), 0, 255)
        row = np.clip(((Y - TY) * 256).astype(np.int64), 0, 255)
        rgb = np.zeros(px.shape + (3,), np.int16)
        known = np.zeros(px.shape, bool)
        cache, complete = (self._pcache if cache is None else cache), True
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

    @staticmethod
    def edge_at_kerbs(d, spans):
        """ゼブラのある所は、縁をゼブラの内側の端（舗装と縁石の境目＝コースの縁）へ寄せる。
        地図の道路縁は縁石の外の端に引かれていることが多く（そのままだとゼブラが道の帯の中に描かれる）、
        写真から取った縁は色の違う舗装で手前に止まることがある（ゼブラが縁から浮く）。
        区間の前後 ZEBRA_RAMP 個の横断面で、寄せる量をなだらかに0へ戻す"""
        n = len(d)
        out = d.copy()
        for side in (0, 1):
            shift = np.zeros(n)
            wsum = np.zeros(n)
            for run, off in spans[side]:
                delta = off - ZEBRA_W_M / 2                 # 縁から、ゼブラの内側の端まで
                if not np.isfinite(delta) or abs(delta) > ZEBRA_SHIFT_MAX_M:
                    continue
                idx = np.r_[np.arange(run[0] - ZEBRA_RAMP, run[0]), run, np.arange(run[-1] + 1, run[-1] + 1 + ZEBRA_RAMP)] % n
                w = np.r_[np.arange(1, ZEBRA_RAMP + 1) / (ZEBRA_RAMP + 1), np.ones(len(run)),
                          np.arange(ZEBRA_RAMP, 0, -1) / (ZEBRA_RAMP + 1)]
                shift[idx] += w * delta
                wsum[idx] += w
            m = wsum > 0
            shift[m] /= np.maximum(wsum[m], 1.0)            # 重なった区間は平均（1より小さい重みはそのまま＝なだらかに）
            out[:, side] = d[:, side] + shift
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

    # ---- なぞった線を縁へ吸い付かせる（SP-10-34）
    @staticmethod
    def course_pixels(rgb):
        """コースの路面（灰色の舗装と白線）なら True。赤い縁石・草・砂利・影は False（縁はゼブラの内側の端に置くため）"""
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
        white = (mn > 185) & (mx - mn < 20)
        grey = (mx - mn < 30) & (mn > 35) & (b - g >= -2) & (r - b <= 4)
        return white | grey

    def snap(self, pts, inside=0, segs=None, reach=None):
        """人がざっとなぞった線 pts [[lat, lon], …] を、近くの縁（航空写真で路面が切れる所）へ寄せた線にする。
        inside：コースの路面がなぞった向きの左（1）か右（-1）か（0 は分からない）。segs：地理院地図の道路縁（無ければ読む）。
        reach：横へ探す遠さ（m。画面の倍率から。無ければ SNAP_REACH_M）。
        1. なぞった線を SNAP_STEP_M おきにしてならし、各点から横へ ±reach を SNAP_RES_M おきに候補にする
        2. 候補ごとの「縁らしさ」：内と外の SNAP_WIN_M の窓（前後 ±SNAP_ALONG_M も入れる）で、
           色（明るさ・青み・赤み）の違い（点ごとの山の形で、写真のにじみの真ん中で最も大きい）と、路面（灰色の舗装・白線）の割合がコースの側でどれだけ多いか
           （半分の重み。向きを見るため）の大きいほう。色の段は、コースの側が路面らしいほど強く見る。白い筋（白線）も拾い、
           地図の道路縁（測った線）が通る候補には SNAP_GSI を足す
        3. 縁らしさ − なぞった線からの離れの損を、前後の点で横に跳ばないように（1点で SNAP_MAX_SHIFT_M まで、
           動いた分だけ損）まとめて最もよい並びを選ぶ（動的計画法）。縁が見つからない所は、なぞった線の近くに残る
        SUGO 11/8 で、同じ区間を横へ -1〜+1 m ずらしてなぞった5本が、吸い付いたあと中央値 0.13 m の内に集まった（上位10%で 0.3 m）
        返り値 {pts: [[lat, lon], …]（STEP_M おき）, found: 縁が見つかった点の割合, shift_m: なぞった線から動いた量の中央値}"""
        p = np.asarray(pts, float)
        if p.ndim != 2 or p.shape[1] != 2 or len(p) < 2 or len(p) > SNAP_MAX_POINTS or not np.isfinite(p).all() \
                or np.abs(p[:, 0]).max() > 85 or np.abs(p[:, 1]).max() > 180:
            raise OutlineError('なぞった線の形が違う')
        lat0, lon0 = float(p[:, 0].mean()), float(p[:, 1].mean())
        kx = M_PER_DEG * math.cos(math.radians(lat0))
        x, y = (p[:, 1] - lon0) * kx, (p[:, 0] - lat0) * M_PER_DEG
        s = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))))
        if s[-1] < SNAP_MIN_M:
            raise OutlineError(f'なぞった線が短すぎる（{SNAP_MIN_M:.0f} m より長くなぞってください）')
        if s[-1] > SNAP_MAX_M:
            raise OutlineError(f'なぞった線が長すぎる（{SNAP_MAX_M:.0f} m まで）')
        n = int(math.ceil(s[-1] / SNAP_STEP_M)) + 1
        ss = np.linspace(0.0, s[-1], n)
        gx, gy = np.interp(ss, s, x), np.interp(ss, s, y)
        if n >= 7:                          # 手の震えをならす（両端は動かさない）
            k = 5
            sx = np.convolve(np.r_[np.full(k // 2, gx[0]), gx, np.full(k // 2, gx[-1])], np.ones(k) / k, 'valid')
            sy = np.convolve(np.r_[np.full(k // 2, gy[0]), gy, np.full(k // 2, gy[-1])], np.ones(k) / k, 'valid')
            gx, gy = sx, sy
        tx, ty = np.gradient(gx), np.gradient(gy)
        norm = np.hypot(tx, ty)
        norm[norm == 0] = 1.0
        tx, ty = tx / norm, ty / norm
        nx, ny = -ty, tx                    # なぞった向きの左
        origin = (lat0, lon0)
        R = SNAP_REACH_M if reach is None or not math.isfinite(reach) else float(min(SNAP_REACH_MAX_M, max(SNAP_REACH_MIN_M, reach)))
        offs = np.arange(-R, R + 1e-9, SNAP_RES_M)
        win = int(round(SNAP_WIN_M / SNAP_RES_M))
        ext = np.arange(-R - SNAP_WIN_M, R + SNAP_WIN_M + 1e-9, SNAP_RES_M)
        al = np.arange(-SNAP_ALONG_M, SNAP_ALONG_M + 1e-9, 0.5)
        px = gx[:, None, None] + nx[:, None, None] * ext[None, :, None] + tx[:, None, None] * al[None, None, :]
        py = gy[:, None, None] + ny[:, None, None] * ext[None, :, None] + ty[:, None, None] * al[None, None, :]
        rgb, known, _ok = self.sample(origin, px, py, {})
        J = np.arange(len(offs))
        f = rgb.astype(float)
        feat = np.stack((f.mean(axis=3) / SNAP_C_LUM, (f[..., 2] - f[..., 1]) / SNAP_C_HUE,
                         (f[..., 0] - f[..., 2]) / SNAP_C_HUE, self.course_pixels(rgb).astype(float)), axis=-1)
        feat = np.where(known[..., None], feat, 0.0)
        # 横の位置 j の候補：ext の番号 j + win がその点。左の窓 [j+win, j+2win)、右の窓 [j, j+win) の平均（前後 ±SNAP_ALONG_M も入れる）
        cf = np.cumsum(np.concatenate((np.zeros((n, 1, 4)), feat.sum(axis=2)), axis=1), axis=1)
        ck = np.cumsum(np.c_[np.zeros(n), known.sum(axis=2)], axis=1)

        def wmean(a, b):
            c = ck[:, b] - ck[:, a]
            with np.errstate(invalid='ignore', divide='ignore'):
                return (cf[:, b] - cf[:, a]) / c[..., None], c
        m_l, cl = wmean(J + win, J + 2 * win)
        m_r, crr = wmean(J, J + win)
        enough = (cl >= win * len(al) * 0.5) & (crr >= win * len(al) * 0.5)
        # 色の段：内と外の窓の色（明るさ・青み・赤み）の違い。写真はにじむので、色の移り変わりの真ん中で最も大きくなる
        # 1で頭打ちにすると山が平らになって真ん中が分からなくなるので、点ごとにいちばん大きい所で割って山の形を残し、
        # その点の段の強さ（SNAP_C_FLOOR〜+SNAP_C_FULL で 0〜1）を掛ける
        raw = np.where(enough, np.sqrt(((m_l[..., :3] - m_r[..., :3]) ** 2).sum(axis=-1)), 0.0)
        raw = np.where(np.isfinite(raw), raw, 0.0)
        cmax = raw.max(axis=1, keepdims=True)
        contrast = raw / np.maximum(cmax, 1e-9) * np.clip((cmax - SNAP_C_FLOOR) / SNAP_C_FULL, 0, 1)
        # 向き：コースの側（inside）のほうが路面らしい段だけを強く見る（逆向きの段は弱く。路面どうしの段は半分）
        road_in = m_l[..., 3] - m_r[..., 3] if inside > 0 else m_r[..., 3] - m_l[..., 3] if inside < 0 else np.abs(m_l[..., 3] - m_r[..., 3])
        edge = np.maximum(SNAP_BINARY * road_in, SNAP_C_W * contrast * np.clip(0.5 + road_in, 0.1, 1.0))    # 路面かどうかの段（くっきり）と色の段（路面どうし・草の色が近い所）
        edge = np.where(enough & np.isfinite(edge), edge, 0.0)
        # 白線（縁に引かれた白い筋）：真ん中 ±SNAP_RES_M と、両脇の明るさの差
        lum = np.where(known, f.mean(axis=3), np.nan)
        sat = np.where(known, (rgb.max(axis=3) - rgb.min(axis=3)).astype(float), np.nan)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            lu, sa = np.nanmean(lum, axis=2), np.nanmean(sat, axis=2)
            c0 = J + win
            at = lambda ks: np.nanmean(np.stack([lu[:, np.clip(c0 + k, 0, lu.shape[1] - 1)] for k in ks]), axis=0)
            side_l, side_r, mid = at((3, 4, 5)), at((-3, -4, -5)), at((-1, 0, 1))
        line = np.clip((mid - np.maximum(side_l, side_r)) / SNAP_LINE_CONTRAST, 0, 1)
        line = np.where((sa[:, c0] < 40) & np.isfinite(line), line * SNAP_LINE, 0.0)
        edge = np.maximum(edge, line)
        # 地図の道路縁（測った線）が横切る所
        if segs is None:
            segs, _ = self.edges(origin, gx, gy)
        if len(segs):
            hits = self.ray_hits(gx, gy, nx, ny, tx, ty, segs, np.zeros((n, 2)), np.full((n, 2), R))
            for side, sg in ((0, 1.0), (1, -1.0)):
                h = sg * hits[:, side]
                ok = np.isfinite(h)
                bump = np.exp(-((offs[None, :] - h[:, None]) / SNAP_GSI_SIGMA_M) ** 2 / 2)
                edge = edge + np.where(ok[:, None], SNAP_GSI * bump, 0.0)
        best_here = edge.max(axis=1)
        found = best_here >= SNAP_FOUND
        data = -edge + SNAP_HINT * (np.abs(offs[None, :]) / R) ** SNAP_HINT_POW
        # 動的計画法（横へ1点で SNAP_MAX_SHIFT 番まで）
        K, mx = len(offs), int(round(SNAP_MAX_SHIFT_M / SNAP_RES_M))
        cost = data[0].copy()
        back = np.zeros((n, K), np.int64)
        for i in range(1, n):
            best = np.full(K, np.inf)
            arg = np.zeros(K, np.int64)
            for d in range(-mx, mx + 1):
                prev = np.full(K, np.inf)
                if d >= 0:
                    prev[d:] = cost[:K - d] if d else cost
                else:
                    prev[:d] = cost[-d:]
                c = prev + SNAP_MOVE * abs(d) * SNAP_RES_M
                m = c < best
                best[m] = c[m]
                arg[m] = (J - d)[m]
            cost = best + data[i]
            back[i] = arg
        path = np.zeros(n, np.int64)
        path[-1] = int(np.argmin(cost))
        for i in range(n - 1, 0, -1):
            path[i - 1] = back[i, path[i]]
        o = offs[path]
        if n >= 5:
            o = np.convolve(np.r_[o[:2][::-1], o, o[-2:][::-1]], np.ones(5) / 5, 'valid')
        ex, ey = gx + nx * o, gy + ny * o
        es = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(ex), np.diff(ey)))))
        m = max(2, int(math.ceil(es[-1] / STEP_M)) + 1)
        qs = np.linspace(0.0, es[-1], m)
        qx, qy = np.interp(qs, es, ex), np.interp(qs, es, ey)
        return {'pts': [[round(float(lat0 + b / M_PER_DEG), 7), round(float(lon0 + a / kx), 7)] for a, b in zip(qx, qy)],
                'found': round(float(found.mean()), 3), 'shift_m': round(float(np.median(np.abs(o))), 2)}

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
        kb = self.kerbs(origin, gx, gy, nx, ny, tx, ty, d)
        self._pcache = {}
        spans = [[(r, o) for r, o in self.zebra_spans(kb[:, side], d[:, side])
                  if abs(o - ZEBRA_W_M / 2) <= ZEBRA_SHIFT_MAX_M] for side in (0, 1)]     # 縁から離れすぎたものは見違い
        d = self.edge_at_kerbs(d, spans)
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
        kerbs = []
        for side, sg in ((0, 1.0), (1, -1.0)):
            for run, _off in spans[side]:
                c = d[run, side] + ZEBRA_W_M / 2            # 縁（＝ゼブラの内側の端）のすぐ外に帯の真ん中
                xs, ys = gx[run] + sg * nx[run] * c, gy[run] + sg * ny[run] * c
                kerbs.append({'w': ZEBRA_W_M,
                              'pts': [[round(float(lat0 + b / M_PER_DEG), 7), round(float(lon0 + a / kx), 7)] for a, b in zip(xs, ys)]})
        return {'version': VERSION, 'lines': lines, 'kerbs': kerbs, 'share': share, 'attribution': ATTRIBUTION,
                'complete': bool(ok_v and ok_p),
                'width_m': [round(float(np.nanpercentile(width, q)), 1) for q in (5, 50, 95)] if np.isfinite(width).any() else None}
