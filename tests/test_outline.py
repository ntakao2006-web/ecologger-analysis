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


def to_latlon(x, y):
    return LAT0 + np.asarray(y) / outline.M_PER_DEG, LON0 + np.asarray(x) / KX


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
    def __init__(self, offline=False, kerb=False):
        self.offline = offline
        self.kerb = kerb
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
        pts = np.concatenate([np.asarray(k) for k in out['kerbs']])
        x, y = (pts[:, 1] - LON0) * KX, (pts[:, 0] - LAT0) * outline.M_PER_DEG
        r, a = np.hypot(x, y), np.arctan2(y, x)
        self.assertTrue(np.all((a > 0.1) & (a < 0.9)))          # 赤白の所だけ。白線の所は拾わない
        self.assertTrue(np.all((r > R_OUT - 1.0) & (r < R_OUT + 2.0)))
        self.assertGreater(np.ptp(a), 0.4)                       # ゼブラのほとんどの長さを覆う
        self.assertEqual(outline.Outlines(self.tmp.name, Photos(), fetch=self.fetch).build(la, lo)['kerbs'], [])

    def test_short_guide_is_refused(self):
        o = outline.Outlines(self.tmp.name, Photos(), fetch=self.fetch)
        with self.assertRaises(outline.OutlineError):
            o.build(*to_latlon(np.arange(20.0), np.zeros(20)))


if __name__ == '__main__':
    unittest.main()
