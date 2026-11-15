import io
import math
import re
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import mvt, outline  # noqa: E402

LAT0, LON0 = 38.14, 140.78
KX = outline.M_PER_DEG * math.cos(math.radians(LAT0))
R_IN, R_OUT, R_CAR = 94.0, 106.0, 100.0     # 円いコース：内側の縁・外側の縁・走った線の半径（m）
PIT_R = 116.0                               # ピットロードの真ん中の半径（東の外、角度 ±0.4 rad）


def to_latlon(x, y):
    return LAT0 + np.asarray(y) / outline.M_PER_DEG, LON0 + np.asarray(x) / KX


def pit_radius(a):
    """ピットを通る周の半径：-0.55〜-0.4 rad でコースから出て、±0.4 rad は PIT_R、0.4〜0.55 rad でコースへ戻る"""
    a = np.asarray(a, float)
    up = np.clip((a + 0.55) / 0.15, 0, 1)
    down = np.clip((0.55 - a) / 0.15, 0, 1)
    return R_CAR + (PIT_R - R_CAR) * np.minimum(up, down)


def drive(t0, laps, pit=False, stop_s=0.0, spur=False, jitter=0.0, seed=0, garage=False):
    """(-π から左回りに) laps 周走った測位 (t, lat, lon)。0.1 秒おき、コースは 30 m/s、ピットロードは 15 m/s。
    pit：最後の周でピットを通る。stop_s：ピットの真ん中（0 rad）で止まる秒。spur：止まる前に外（半径 128 m）へ寄り道して戻る"""
    rng = np.random.default_rng(seed)
    xs, ys = [], []
    for k in range(laps):
        a = np.linspace(-math.pi, math.pi, 4000, endpoint=False)
        r = pit_radius(a) if (pit and k == laps - 1) else np.full_like(a, R_CAR)
        r = r + jitter * rng.standard_normal() * np.ones_like(a)
        x, y = r * np.cos(a), r * np.sin(a)
        if pit and k == laps - 1 and garage:
            # ガレージ（半径 128 m）へ 0 rad で入り、止まって、0.1 rad から出てくる（ガレージ前を素通りする回は無い）
            i, j = int(np.argmin(np.abs(a))), int(np.argmin(np.abs(a - 0.1)))
            out_r = np.linspace(PIT_R, 128.0, 60)
            ga = np.linspace(0.0, 0.1, 30)
            gx = np.r_[out_r * math.cos(0.0), np.full(int(stop_s * 10), 128.0), 128.0 * np.cos(ga), out_r[::-1] * math.cos(0.1)]
            gy = np.r_[out_r * math.sin(0.0), np.zeros(int(stop_s * 10)), 128.0 * np.sin(ga), out_r[::-1] * math.sin(0.1)]
            x = np.r_[x[:i], gx, x[j:]]
            y = np.r_[y[:i], gy, y[j:]]
        elif pit and k == laps - 1 and (stop_s or spur):
            i = int(np.argmin(np.abs(a)))
            extra = [np.full(int(stop_s * 10), x[i]), np.full(int(stop_s * 10), y[i])]
            if spur:
                rr = np.r_[np.linspace(PIT_R, 128.0, 80), np.full(int(stop_s * 10), 128.0), np.linspace(128.0, PIT_R, 80)]
                extra = [rr * math.cos(0.1), rr * math.sin(0.1)]
                i = int(np.argmin(np.abs(a - 0.1)))
            x = np.r_[x[:i], extra[0], x[i:]]
            y = np.r_[y[:i], extra[1], y[i:]]
        xs.append(x)
        ys.append(y)
    x, y = np.concatenate(xs), np.concatenate(ys)
    seg = np.r_[0.0, np.hypot(np.diff(x), np.diff(y))]
    rad = np.hypot(x, y)
    v = np.where(rad > R_OUT + 1, 15.0, 30.0)
    dt = np.where(seg < 1e-6, 0.1, seg / v)
    t = t0 + np.cumsum(dt)
    # 0.1 秒おきに並べ直す
    tt = np.arange(t[0], t[-1], 0.1)
    la, lo = to_latlon(np.interp(tt, t, x), np.interp(tt, t, y))
    return tt, la, lo


def circle(r, a0=0.0, a1=2 * math.pi, n=400):
    a = np.linspace(a0, a1, n)
    return to_latlon(r * np.cos(a), r * np.sin(a))


# ---- ベクトルタイルを書く（試験用の最小の書き手）
def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7f
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(f, w, payload):
    if w == 0:
        return _varint(f << 3) + _varint(payload)
    return _varint((f << 3) | 2) + _varint(len(payload)) + payload


def _zz(n):
    return (n << 1) ^ (n >> 63)


def encode_tile(lines, z, X, Y, ftcode=2201, level='2500', ext=4096):
    """lines：[(lat 配列, lon 配列)]。層 road に道路縁として入れる"""
    keys = [b'ftCode', b'orgGILvl']
    vals = [_field(6, 0, _zz(ftcode)), _field(1, 2, level.encode())]
    feats = b''
    for la, lo in lines:
        tx, ty = outline.tile_xy(la, lo, z)
        px = np.round((tx - X) * ext).astype(int)
        py = np.round((ty - Y) * ext).astype(int)
        cmds = [(1 & 7) | (1 << 3), _zz(int(px[0])), _zz(int(py[0])), (2 & 7) | ((len(px) - 1) << 3)]
        for i in range(1, len(px)):
            cmds += [_zz(int(px[i] - px[i - 1])), _zz(int(py[i] - py[i - 1]))]
        geom = b''.join(_varint(c) for c in cmds)
        tags = _varint(0) + _varint(0) + _varint(1) + _varint(1)
        feats += _field(2, 2, _field(2, 2, tags) + _field(3, 0, 2) + _field(4, 2, geom))
    layer = _field(1, 2, b'road') + feats + b''.join(_field(3, 2, k) for k in keys) + \
        b''.join(_field(4, 2, v) for v in vals) + _field(5, 0, ext) + _field(15, 0, 2)
    return _field(3, 2, layer)


class Photos:
    """航空写真の代わり：半径 R_IN〜R_OUT を灰色の舗装、ほかを草の緑にした JPEG"""
    def __init__(self, offline=False, kerb=False, pit=False):
        self.offline = offline
        self.kerb = kerb
        self.pit = pit
        self.calls = 0

    def get(self, z, x, y):
        self.calls += 1
        if self.offline:
            return 'offline', None
        from PIL import Image
        j, i = np.meshgrid(np.arange(256), np.arange(256))
        la, lo = outline.tile_latlon(x + (j + 0.5) / 256, y + (i + 0.5) / 256, z)
        r = np.hypot((lo - LON0) * KX, (la - LAT0) * outline.M_PER_DEG)
        img = np.zeros((256, 256, 3), np.uint8)
        img[...] = (90, 100, 82)
        img[(r > R_IN) & (r < R_OUT)] = (100, 102, 106)
        if self.pit:        # 東の外にピットロード（半径 PIT_R ± 3 m の舗装）と、コースとつなぐ道
            a = np.arctan2((la - LAT0) * outline.M_PER_DEG, (lo - LON0) * KX)
            img[(np.abs(r - PIT_R) < 3) & (np.abs(a) < 0.46)] = (100, 102, 106)
            img[(r > R_OUT) & (np.abs(r - pit_radius(a)) < 3) & (np.abs(a) > 0.38) & (np.abs(a) < 0.58)] = (100, 102, 106)
        if self.kerb:
            a = np.arctan2((la - LAT0) * outline.M_PER_DEG, (lo - LON0) * KX)
            # 外の縁のすぐ外（0.2〜0.8 rad）に赤白のゼブラ（1 m ごとに交互）。色は写真のにじんだ赤白
            band = (r > R_OUT) & (r < R_OUT + 1.2) & (a > 0.2) & (a < 0.8)
            red = (np.floor(a * R_OUT) % 2 == 0)
            img[band & red] = (150, 128, 134)
            img[band & ~red] = (225, 222, 222)
            # 内の縁のすぐ内（-0.8〜-0.2 rad）は途切れない白線：ゼブラではない
            img[(r > R_IN - 1.0) & (r < R_IN) & (a > -0.8) & (a < -0.2)] = (225, 222, 222)
        b = io.BytesIO()
        Image.fromarray(img).save(b, 'JPEG', quality=95)
        return 'ok', b.getvalue()


class OutlineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.urls = []
        self.edge_lines = [circle(R_IN, -math.pi / 2, math.pi / 2), circle(R_OUT, -math.pi / 2, math.pi / 2)]   # 東の半分だけ地図にある

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, url):
        self.urls.append(url)
        z, x, y = map(int, re.search(r'/(\d+)/(\d+)/(\d+)\.pbf$', url).groups())
        return 200, encode_tile(self.edge_lines, z, x, y)

    def radii(self, out):
        rs = []
        for ln in out['lines']:
            a = np.asarray(ln)
            rs.append(np.hypot((a[:, 1] - LON0) * KX, (a[:, 0] - LAT0) * outline.M_PER_DEG))
        return rs

    def test_mvt_roundtrip(self):
        la, lo = circle(R_OUT, 0, 1, 20)
        z = outline.BV_Z
        tx, ty = outline.tile_xy(la, lo, z)
        X, Y = int(tx[0]), int(ty[0])
        layers = mvt.read(encode_tile([(la, lo)], z, X, Y))
        ext, feats = layers['road']
        self.assertEqual(ext, 4096)
        gtype, props, parts = feats[0]
        self.assertEqual((gtype, props), (2, {'ftCode': 2201, 'orgGILvl': '2500'}))
        p = np.asarray(parts[0], float)
        back = outline.tile_latlon(X + p[:, 0] / ext, Y + p[:, 1] / ext, z)
        self.assertLess(np.max(np.abs(back[0] - la)) * outline.M_PER_DEG, 0.1)

    def test_mvt_rejects_broken(self):
        with self.assertRaises(mvt.TileError):
            mvt.read(b'\x1a\x05\x0a')

    def test_edges_from_map_and_photo(self):
        o = outline.Outlines(self.tmp.name, Photos(), fetch=self.fetch)
        la, lo = circle(R_CAR, 0, 2 * math.pi, 800)
        out = o.build(la, lo)
        self.assertTrue(out['complete'])
        self.assertGreater(out['share']['gsi'], 0.35)       # 東の半分は地図の縁
        self.assertGreater(out['share']['photo'], 0.35)     # 西の半分は写真から
        self.assertLess(out['share']['none'], 0.02)
        rs = np.concatenate(self.radii(out))
        inner, outer = rs[rs < R_CAR], rs[rs > R_CAR]
        self.assertLess(np.median(np.abs(inner - R_IN)), 0.5)
        self.assertLess(np.median(np.abs(outer - R_OUT)), 0.5)
        self.assertLess(np.percentile(np.abs(np.r_[inner - R_IN, outer - R_OUT]), 95), 1.5)
        self.assertAlmostEqual(out['width_m'][1], R_OUT - R_IN, delta=1.0)

    def test_inner_line_crossed_by_cars_is_not_an_edge(self):
        # コースの中（半径 102 m）にも地図の線がある。ほかの周はそれを越えて走っている → 縁にしない
        self.edge_lines = [circle(R_IN), circle(102.0), circle(R_OUT)]
        o = outline.Outlines(self.tmp.name, Photos(), fetch=self.fetch)
        la, lo = circle(R_CAR, 0, 2 * math.pi, 800)
        t_all, la_all, lo_all = [], [], []
        for k, r in enumerate((99.0, 100.0, 101.0, 103.5, 103.8, 104.0)):     # 6回通った。半分は 102 m より外
            a, b = circle(r, 0, 2 * math.pi, 600)
            t_all.append(k * 1000 + np.arange(600) * 0.1)
            la_all.append(a)
            lo_all.append(b)
        out = o.build(la, lo, np.concatenate(t_all), np.concatenate(la_all), np.concatenate(lo_all))
        rs = np.concatenate(self.radii(out))
        outer = rs[rs > R_CAR]
        self.assertLess(np.median(np.abs(outer - R_OUT)), 0.5)
        # 走った幅を見ないと、コースの中の線を縁にしてしまう
        plain = np.concatenate(self.radii(o.build(la, lo)))
        self.assertLess(np.median(np.abs(plain[plain > R_CAR] - 102.0)), 0.5)

    def test_a_stop_does_not_count_as_many_passes(self):
        # 1回だけ外（半径 110 m）で長く止まった：点は多いが1回として数える
        w = outline.Outlines.used_width
        origin, gx, gy, nx, ny = outline.guide(*circle(R_CAR, 0, 2 * math.pi, 800))
        t_all, la_all, lo_all = [], [], []
        for k in range(10):
            a, b = circle(R_CAR + 0.5, 0, 2 * math.pi, 600)
            t_all.append(k * 1000 + np.arange(600) * 0.1)
            la_all.append(a)
            lo_all.append(b)
        sa, sb = to_latlon(np.full(5000, 110.0), np.zeros(5000))      # 東の端で止まっている
        t_all.append(20000 + np.arange(5000) * 0.1)
        la_all.append(sa)
        lo_all.append(sb)
        used = w(origin, gx, gy, nx, ny, np.concatenate(t_all), np.concatenate(la_all), np.concatenate(lo_all))
        self.assertLess(used.max(), 2.0)

    def test_cached_per_course_and_rebuilt_for_more_laps(self):
        photos = Photos()
        o = outline.Outlines(self.tmp.name, photos, fetch=self.fetch)
        la, lo = circle(R_CAR, 0, 2 * math.pi, 800)
        a = o.get('円コース', la, lo, laps=3)
        n_urls = len(self.urls)
        o2 = outline.Outlines(self.tmp.name, Photos(), fetch=self.fetch)    # 開き直しても残っている
        b = o2.get('円コース', la, lo, laps=3)
        self.assertEqual(a['lines'], b['lines'])
        self.assertEqual(len(self.urls), n_urls)
        self.assertEqual(b['laps'], 3)
        c = o2.get('円コース', la, lo, laps=10)             # 周の多い走行なら作り直す
        self.assertEqual(c['laps'], 10)
        self.assertEqual(o2.get('円コース', la, lo, laps=5)['laps'], 10)

    def test_offline_photo_is_not_cached(self):
        o = outline.Outlines(self.tmp.name, Photos(offline=True), fetch=self.fetch)
        la, lo = circle(R_CAR, 0, 2 * math.pi, 800)
        out = o.get('円コース', la, lo)
        self.assertFalse(out['complete'])
        self.assertGreater(out['share']['none'], 0.35)       # 写真が要る西の半分は線が無い
        self.assertFalse(any((Path(self.tmp.name) / 'outlines').glob('*.json')))

    def test_kerbs_from_photo(self):
        o = outline.Outlines(self.tmp.name, Photos(kerb=True), fetch=self.fetch)
        la, lo = circle(R_CAR, 0, 2 * math.pi, 800)
        out = o.build(la, lo)
        self.assertTrue(out['kerbs'])
        pts = np.concatenate([np.asarray(k['pts']) for k in out['kerbs']])
        self.assertTrue(all(k['w'] == outline.ZEBRA_W_M for k in out['kerbs']))
        self.assertEqual(len(out['kerbs']), 1)                     # 途切れずに1本の帯
        x, y = (pts[:, 1] - LON0) * KX, (pts[:, 0] - LAT0) * outline.M_PER_DEG
        r, a = np.hypot(x, y), np.arctan2(y, x)
        self.assertTrue(np.all((a > 0.1) & (a < 0.9)))          # 赤白の所だけ。白線の所は拾わない
        self.assertTrue(np.all((r > R_OUT - 1.0) & (r < R_OUT + 2.0)))
        self.assertGreater(np.ptp(a), 0.4)                       # ゼブラのほとんどの長さを覆う
        self.assertEqual(outline.Outlines(self.tmp.name, Photos(), fetch=self.fetch).build(la, lo)['kerbs'], [])

    def test_measured_again_from_the_middle(self):
        # 走行ラインが内の縁に触れて少しまたぐ（クリップ）。内の縁は途中で 19 m 途切れ、その奥に別の道の縁がある
        a = np.linspace(0, 2 * math.pi, 1200, endpoint=False)
        r = 100.0 - 6.3 * np.maximum(0.0, np.cos(a - 1.0)) ** 8          # a=1 で 93.7 m（縁 94 m の 0.3 m 内）
        la, lo = to_latlon(r * np.cos(a), r * np.sin(a))
        self.edge_lines = [circle(R_IN, -1.0, 2.0), circle(R_IN, 2.2, 2 * math.pi - 1.0), circle(R_OUT), circle(80.0, 1.9, 2.3)]
        o = outline.Outlines(self.tmp.name, Photos(offline=True), fetch=self.fetch)    # 写真には頼らない
        out = o.build(la, lo)
        rs = self.radii(out)
        inner = [x for x in rs if np.median(x) < R_CAR]
        self.assertEqual(len(inner), 1)                                   # 切れずに1本
        self.assertLess(np.max(np.abs(inner[0] - R_IN)), 0.5)             # 別の道の縁（80 m）を拾わない
        self.assertGreater(len(inner[0]), 0.97 * 2 * math.pi * R_IN / outline.STEP_M)
        self.assertGreater(out['share']['filled'], 0.0)

    def test_bump_at_a_side_road_is_removed(self):
        # 内の縁が脇道との合流で 1.5 m 奥へ曲がり込む（長さ 20 m ほど）。コースの縁ではないので削る
        a = np.linspace(0, 2 * math.pi, 2000)
        rr = R_IN - 1.5 * np.exp(-((a - 2.0) / 0.08) ** 2)
        self.edge_lines = [to_latlon(rr * np.cos(a), rr * np.sin(a)), circle(R_OUT)]
        o = outline.Outlines(self.tmp.name, Photos(offline=True), fetch=self.fetch)
        out = o.build(*circle(R_CAR, 0, 2 * math.pi, 800))
        inner = [x for x in self.radii(out) if np.median(x) < R_CAR]
        self.assertEqual(len(inner), 1)
        self.assertLess(np.max(np.abs(inner[0] - R_IN)), 0.3)

    def test_edge_moves_to_the_inner_side_of_the_kerb(self):
        # 地図の外の縁が縁石の外の端（R_OUT+1.2 m）に引かれている。ゼブラの所は、縁を縁石の内側の端（R_OUT）へ寄せ、帯はその外
        self.edge_lines = [circle(R_IN), circle(R_OUT + 1.2)]
        o = outline.Outlines(self.tmp.name, Photos(kerb=True), fetch=self.fetch)
        la, lo = circle(R_CAR, 0, 2 * math.pi, 800)
        out = o.build(la, lo)
        outer = [np.asarray(x) for x in out['lines'] if np.median(self.radii({'lines': [x]})[0]) > R_CAR]
        self.assertEqual(len(outer), 1)
        p = outer[0]
        r = np.hypot((p[:, 1] - LON0) * KX, (p[:, 0] - LAT0) * outline.M_PER_DEG)
        a = np.arctan2((p[:, 0] - LAT0) * outline.M_PER_DEG, (p[:, 1] - LON0) * KX)
        mid = (a > 0.35) & (a < 0.65)                                      # ゼブラの真ん中あたり
        far = (a < -0.5) | (a > 1.5)                                       # ゼブラから離れた所
        self.assertLess(np.max(np.abs(r[mid] - R_OUT)), 0.4)
        self.assertLess(np.max(np.abs(r[far] - (R_OUT + 1.2))), 0.3)
        z = np.concatenate([np.asarray(k['pts']) for k in out['kerbs']])
        rz = np.hypot((z[:, 1] - LON0) * KX, (z[:, 0] - LAT0) * outline.M_PER_DEG)
        self.assertLess(np.median(np.abs(rz - (R_OUT + outline.ZEBRA_W_M / 2))), 0.3)    # 帯は縁の外側に接する

    def test_hand_edit_is_kept_per_course_and_wins(self):
        o = outline.Outlines(self.tmp.name, Photos(), fetch=self.fetch)
        la, lo = circle(R_CAR, 0, 2 * math.pi, 800)
        auto = o.get('円コース', la, lo, laps=3)
        self.assertNotIn('edited', auto)
        ln = [[LAT0, LON0], [round(LAT0 + 0.0001, 7), round(LON0 + 0.0001, 7)]]
        o.save_edit('円コース', la, lo, {'lines': [ln], 'kerbs': [{'w': 1.0, 'pts': ln}],
                                          'boards': [{'lat': LAT0, 'lon': LON0, 'text': ' 100 '}]}, width_m=12.0)
        o2 = outline.Outlines(self.tmp.name, Photos(offline=True), fetch=self.fetch)     # 開き直し、写真が取れなくても出る
        e = o2.get('円コース', la, lo, laps=3)
        self.assertTrue(e['edited'])
        self.assertEqual(e['lines'], [ln])
        self.assertEqual(e['boards'], [{'lat': LAT0, 'lon': LON0, 'text': '100'}])
        self.assertEqual(e['width_m'], 12.0)
        self.assertNotIn('edited', o2.get('円コース', la, lo, laps=3, auto=True))           # 自動のものも取れる
        self.assertIsNone(o2.edited('別のコース', la, lo))
        self.assertTrue(o2.clear_edit('円コース', la, lo))                                  # 自動に戻す
        self.assertNotIn('edited', o2.get('円コース', la, lo, laps=3))

    def test_hand_edit_shape_is_checked(self):
        ok = [[LAT0, LON0], [LAT0, LON0 + 0.0001]]
        bad = [
            {'lines': 'x'},
            {'lines': [[[LAT0, LON0]]]},                                      # 点が1つ
            {'lines': [[[LAT0, float('nan')], [LAT0, LON0]]]},
            {'lines': [[[95, LON0], [LAT0, LON0]]]},
            {'lines': [ok], 'kerbs': [{'w': 9, 'pts': ok}]},
            {'lines': [ok], 'kerbs': [{'pts': ok, 'ws': [1], 'side': 1}]},             # 幅の数が点と合わない
            {'lines': [ok], 'kerbs': [{'pts': ok, 'ws': [1, 6], 'side': 1}]},          # 幅が広すぎる
            {'lines': [ok], 'kerbs': [{'pts': ok, 'ws': [0, 0], 'side': 1}]},          # 幅が全部 0
            {'lines': [ok], 'kerbs': [{'pts': ok, 'ws': [1, 1], 'side': 0}]},          # 向きが無い
            {'lines': [ok], 'boards': [{'lat': LAT0, 'lon': LON0, 'text': 'x' * 13}]},
            {'lines': [ok], 'boards': [{'lat': LAT0, 'lon': LON0, 'text': '  '}]},
            {'lines': [ok] * (outline.EDIT_MAX_LINES + 1)},
            {'lines': [ok], 'kinds': ['pit', 'edge']},                         # 種類の数が線と合わない
            {'lines': [ok], 'kinds': ['road']},                                # 知らない種類
            {'lines': [ok], 'kinds': 'pit'},
        ]
        for d in bad:
            with self.assertRaises(outline.OutlineError, msg=str(d)[:60]):
                outline.check_edit(d)
        self.assertEqual(outline.check_edit({'lines': [ok]})['kerbs'], [])
        # 線の種類：無ければ全部コースの縁（前に保存したもの）。ピットロード・その他の舗装も持てる
        self.assertEqual(outline.check_edit({'lines': [ok]})['kinds'], ['edge'])
        self.assertEqual(outline.check_edit({'lines': [ok, ok, ok], 'kinds': ['edge', 'pit', 'other']})['kinds'], ['edge', 'pit', 'other'])
        # 縁にくっつく形（点ごとの幅・端は 0）と前の形の両方が通る
        k = outline.check_edit({'lines': [ok], 'kerbs': [{'pts': ok, 'ws': [0, 1.234], 'side': -1}, {'w': 1, 'pts': ok}]})['kerbs']
        self.assertEqual(k, [{'pts': ok, 'ws': [0.0, 1.23], 'side': -1}, {'w': 1.0, 'pts': ok}])

    # ---- なぞった線を縁へ吸い付かせる（SP-10-34）
    def stroke(self, r, a0, a1, wobble=0.4, n=120):
        a = np.linspace(a0, a1, n)
        rr = r + wobble * np.sin(a * 37.0)                  # 手の揺れ（±wobble m、十数 m の波）
        la, lo = to_latlon(rr * np.cos(a), rr * np.sin(a))
        return np.stack((la, lo), axis=1).tolist()

    def radius_of(self, pts):
        a = np.asarray(pts)
        return np.hypot((a[:, 1] - LON0) * KX, (a[:, 0] - LAT0) * outline.M_PER_DEG)

    def test_snap_to_the_photo_edge(self):
        o = outline.Outlines(self.tmp.name, Photos(), fetch=self.fetch)
        # 西の半分（地図の縁が無い所）：外の縁の 1.5 m 外の草の上を、左回りに（コースは左）なぞる
        r = o.snap(self.stroke(R_OUT + 1.5, 0.6 * math.pi, 0.9 * math.pi), inside=1, reach=3)
        rs = self.radius_of(r['pts'])
        self.assertLess(np.median(np.abs(rs - R_OUT)), 0.4)
        self.assertLess(np.percentile(np.abs(rs - R_OUT), 90), 0.8)
        self.assertGreater(r['found'], 0.9)
        self.assertAlmostEqual(r['shift_m'], 1.5, delta=0.5)
        a = np.asarray(r['pts'])
        step = np.hypot(np.diff(a[:, 0]) * outline.M_PER_DEG, np.diff(a[:, 1]) * KX)
        self.assertLess(step.max(), outline.STEP_M + 0.01)          # 点は STEP_M おき
        # 内の縁：舗装の上（縁の 1.2 m 内）を左回りに（コースは右）
        r = o.snap(self.stroke(R_IN + 1.2, 0.6 * math.pi, 0.9 * math.pi), inside=-1, reach=3)
        self.assertLess(np.median(np.abs(self.radius_of(r['pts']) - R_IN)), 0.4)

    def test_snap_keeps_the_stroke_where_nothing_is_found(self):
        o = outline.Outlines(self.tmp.name, Photos(offline=True), fetch=self.fetch)
        src = self.stroke(R_OUT + 1.5, 0.6 * math.pi, 0.9 * math.pi, wobble=0.0)
        r = o.snap(src, inside=1, segs=np.zeros((0, 4)), reach=3)
        self.assertLess(np.median(np.abs(self.radius_of(r['pts']) - (R_OUT + 1.5))), 0.1)
        self.assertEqual(r['found'], 0.0)

    def test_snap_refuses_bad_strokes(self):
        o = outline.Outlines(self.tmp.name, Photos(), fetch=self.fetch)
        for bad in ([], [[1, 2]], [[38.14, 140.78], [38.14, 140.78]], [['a', 'b'], [1, 2]], [[95, 0], [96, 0]],
                    self.stroke(R_OUT, 0, 0.0001)):
            with self.assertRaises((outline.OutlineError, ValueError, TypeError)):
                o.snap(bad)

    # ---- ピットロード（SP-10-36）
    def pit_data(self, passes=3, spur=False, garage=False):
        parts = [drive(0.0, 20, jitter=0.4, seed=1)]          # ピットを通る回は1割より少なく（コースの縁の USED_Q）
        for k in range(passes):
            parts.append(drive(10000.0 * (k + 1), 2, pit=True, stop_s=60.0 if (k == 0 or garage) else 0.0, spur=spur and k == 1,
                               seed=k + 2, garage=garage))
        return [np.concatenate(x) for x in zip(*parts)]

    def test_pit_lane_when_every_pass_goes_into_the_garage(self):
        # どの回もガレージへ入って別の所から出てくる：寄り道を切り、曲がっている点を使わないので、ガレージ前でも中心線は膨らまない
        o = outline.Outlines(self.tmp.name, Photos(pit=True), fetch=self.fetch)
        la, lo = circle(R_CAR, -math.pi, math.pi, 800)
        t, a, b = self.pit_data(3, garage=True)
        edges = [np.stack(circle(R_IN, 0, 2 * math.pi, 800), axis=1).tolist(), np.stack(circle(R_OUT, 0, 2 * math.pi, 800), axis=1).tolist()]
        r = o.pit_lane(la, lo, edges, t, a, b)
        self.assertEqual(r['passes'], 3)
        c = np.asarray(r['center'])
        ang = np.arctan2((c[:, 0] - LAT0) * outline.M_PER_DEG, (c[:, 1] - LON0) * KX)
        rc = self.radius_of(r['center'])
        mid = np.abs(ang) < 0.35
        self.assertLess(np.abs(rc[mid] - PIT_R).max(), 0.6)
        self.assertLess(rc.max(), PIT_R + 1.0)
        rs = np.concatenate([self.radius_of(ln) for ln in r['lines']])
        self.assertLess(rs.max(), PIT_R + 4.0)                       # 縁もガレージへ膨らまない

    def test_cut_loops_keeps_hairpins(self):
        cut = outline.Outlines._cut_loops
        # 半径 10 m の半円（ヘアピン）は残す
        a = np.linspace(0, math.pi, 60)
        hp = np.r_[np.c_[np.linspace(-40, 0, 40), np.full(40, -10.0)], np.c_[10 * np.sin(a), -10 * np.cos(a)], np.c_[np.linspace(0, -40, 40), np.full(40, 10.0)]]
        self.assertEqual(len(cut(hp)), len(hp))
        # まっすぐの道から 12 m 外へ出て、10 m 先から戻る寄り道は切る
        road = np.c_[np.arange(0, 100.0), np.zeros(100)]
        spur = np.r_[road[:40], np.c_[np.full(12, 40.0), np.arange(1, 13.0)], np.c_[np.arange(41, 50.0), np.full(9, 12.0)],
                     np.c_[np.full(12, 50.0), np.arange(12, 0, -1.0)], road[50:]]
        p = cut(spur)
        self.assertLess(np.abs(p[:, 1]).max(), 1e-9)

    def test_pit_lane_from_passes(self):
        o = outline.Outlines(self.tmp.name, Photos(pit=True), fetch=self.fetch)
        la, lo = circle(R_CAR, -math.pi, math.pi, 800)
        t, a, b = self.pit_data(3, spur=True)
        edges = [np.stack(circle(R_IN, 0, 2 * math.pi, 800), axis=1).tolist(), np.stack(circle(R_OUT, 0, 2 * math.pi, 800), axis=1).tolist()]
        r = o.pit_lane(la, lo, edges, t, a, b)
        self.assertEqual(r['passes'], 3)
        c = np.asarray(r['center'])
        ang = np.arctan2((c[:, 0] - LAT0) * outline.M_PER_DEG, (c[:, 1] - LON0) * KX)
        rc = self.radius_of(r['center'])
        mid = np.abs(ang) < 0.35
        self.assertGreater(mid.sum(), 10)
        self.assertLess(np.abs(rc[mid] - PIT_R).max(), 0.6)         # ガレージへの寄り道（半径 128 m）には曲がらない
        self.assertLess(rc.max(), PIT_R + 1.0)
        self.assertLess(ang.min(), -0.45)                            # 入口から出口まで
        self.assertGreater(ang.max(), 0.45)
        rs = np.concatenate([self.radius_of(ln) for ln in r['lines']])
        self.assertGreater(rs.min(), R_OUT)                          # コースの中には描かない
        inner, outer = rs[rs < PIT_R], rs[rs > PIT_R]
        self.assertLess(np.median(np.abs(inner - (PIT_R - 3))), 0.6)
        self.assertLess(np.median(np.abs(outer - (PIT_R + 3))), 0.6)
        self.assertGreater(r['found'], 0.5)

    def test_pit_lane_needs_passes(self):
        o = outline.Outlines(self.tmp.name, Photos(pit=True), fetch=self.fetch)
        la, lo = circle(R_CAR, -math.pi, math.pi, 800)
        edges = [np.stack(circle(R_IN, 0, 2 * math.pi, 800), axis=1).tolist(), np.stack(circle(R_OUT, 0, 2 * math.pi, 800), axis=1).tolist()]
        t, a, b = self.pit_data(0)
        with self.assertRaisesRegex(outline.OutlineError, 'ピット'):
            o.pit_lane(la, lo, edges, t, a, b)
        t, a, b = self.pit_data(1)
        with self.assertRaisesRegex(outline.OutlineError, '1回だけ'):
            o.pit_lane(la, lo, edges, t, a, b)
        with self.assertRaises(outline.OutlineError):
            o.pit_lane(la, lo, edges, None, None, None)

    def test_auto_outline_gets_pit_lines(self):
        o = outline.Outlines(self.tmp.name, Photos(pit=True), fetch=self.fetch)
        la, lo = circle(R_CAR, -math.pi, math.pi, 800)
        t, a, b = self.pit_data(2)
        out = o.build(la, lo, t, a, b)
        self.assertEqual(len(out['kinds']), len(out['lines']))
        self.assertIn('pit', out['kinds'])
        self.assertEqual(out['pit']['passes'], 2)
        edge = [ln for ln, k in zip(out['lines'], out['kinds']) if k == 'edge']
        rs = np.concatenate([self.radius_of(ln) for ln in edge])
        self.assertLess(np.median(np.abs(rs[rs > R_CAR] - R_OUT)), 0.5)   # コースの縁はピットを数えずに作る
        outline.check_edit({'lines': out['lines'], 'kinds': out['kinds']})
        # ピットを通っていなければ、コース図は出してピットロードは無い
        t, a, b = self.pit_data(0)
        out = o.build(la, lo, t, a, b)
        self.assertNotIn('pit', out['kinds'])
        self.assertEqual(out['pit']['passes'], 0)
        self.assertTrue(out['pit']['error'])

    def test_short_guide_is_refused(self):
        o = outline.Outlines(self.tmp.name, Photos(), fetch=self.fetch)
        with self.assertRaises(outline.OutlineError):
            o.build(*to_latlon(np.arange(20.0), np.zeros(20)))


if __name__ == '__main__':
    unittest.main()
