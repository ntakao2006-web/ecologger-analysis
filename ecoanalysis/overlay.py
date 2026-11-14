"""選んだ周を重ねる：横軸の時間・距離と、基準の周とのタイムの差。

距離は基準の周の走った線に沿った距離（m）。ほかの周は、測位の点を基準の周の線へ投影して
「基準の周でいうと何m地点か」を出す（位置で合わせる）。こうすると、走る線が少し違っても
同じ場所どうしが並び、タイムの差は「同じ場所に何秒遅れて着いたか」になる。
周の始まりは0m、終わりは基準の周の長さにそろえる（どちらもコントロールラインの上）。
"""
import math

import numpy as np

EARTH_M_PER_DEG = 111320.0
MAX_POINTS = 6000         # 1周に送る点の数の上限（多ければ間引く）
MAX_TRACK = 3000          # コース図に送る測位の点の数の上限
GRID_M = 2.0              # タイムの差を出す距離の刻み
SEARCH_BACK, SEARCH_AHEAD = 5, 60   # 投影で探す基準の線の区間（直前に合った区間からの数）


def _xy(lat, lon, origin):
    lat0, lon0 = origin
    return ((np.asarray(lon, float) - lon0) * EARTH_M_PER_DEG * math.cos(math.radians(lat0)),
            (np.asarray(lat, float) - lat0) * EARTH_M_PER_DEG)


def track(gnss, start, end, origin):
    """周の測位の点（始まり・終わりの時刻の位置を補って両端に足す）。(周の頭からの秒, x, y)。測位が足りなければ None"""
    if gnss is None:
        return None
    t = np.asarray(gnss['t'], float)
    lat, lon = np.asarray(gnss['lat'], float), np.asarray(gnss['lon'], float)
    ok = np.isfinite(t) & np.isfinite(lat) & np.isfinite(lon)
    t, lat, lon = t[ok], lat[ok], lon[ok]
    inside = (t > start) & (t < end)
    if inside.sum() < 2 or t[0] > start or t[-1] < end:
        return None
    ts = np.concatenate(([start], t[inside], [end]))
    la = np.interp(ts, t, lat)
    lo = np.interp(ts, t, lon)
    x, y = _xy(la, lo, origin)
    return ts - start, x, y


def cumulative(x, y):
    return np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))))


def project(ref_x, ref_y, ref_s, x, y):
    """点を基準の線へ投影した距離。前から順に、直前に合った区間の近くだけを探す"""
    n = len(ref_x) - 1
    out = np.empty(len(x))
    j = 0
    for i in range(len(x)):
        lo, hi = max(0, j - SEARCH_BACK), min(n, j + SEARCH_AHEAD)
        ax, ay = ref_x[lo:hi], ref_y[lo:hi]
        dx, dy = ref_x[lo + 1:hi + 1] - ax, ref_y[lo + 1:hi + 1] - ay
        L2 = dx * dx + dy * dy
        u = np.clip(np.where(L2 > 0, ((x[i] - ax) * dx + (y[i] - ay) * dy) / np.where(L2 > 0, L2, 1), 0), 0, 1)
        d2 = (ax + u * dx - x[i]) ** 2 + (ay + u * dy - y[i]) ** 2
        k = int(np.argmin(d2))
        j = lo + k
        out[i] = ref_s[j] + u[k] * (ref_s[j + 1] - ref_s[j])
    return out


def _monotone(s, length):
    """距離を増える一方にそろえ、両端を 0 と基準の長さに合わせる"""
    s = np.maximum.accumulate(np.clip(s, 0, length))
    s[0], s[-1] = 0.0, length
    return np.maximum.accumulate(s)


def _strict(s):
    """np.interp の横軸に使えるよう、止まっていた所（同じ距離が続く）をわずかにずらす"""
    return s + np.arange(len(s)) * 1e-9


def _nums(a, nd):
    return [None if not math.isfinite(v) else round(float(v), nd) for v in a]


def _thin(n, limit=MAX_POINTS):
    step = max(1, math.ceil(n / limit))
    idx = np.arange(0, n, step)
    if idx[-1] != n - 1:
        idx = np.append(idx, n - 1)
    return idx


def compare(items, channels, ref=None):
    """items：[{key, label, session, lap}]（lap は {start, end, time_s}）。ref：基準の周の番号（無ければ一番速い周）
    返す値：{ref, ref_length_m, distance: 距離が使えるか, laps: [{key, label, time_s, t, s, channels, delta}], grid}"""
    if not items:
        return {'ref': None, 'ref_length_m': None, 'distance': False, 'laps': [], 'grid': [], 'ref_t': [], 'origin': None}
    if ref is None or not 0 <= ref < len(items):
        ref = min(range(len(items)), key=lambda i: items[i]['lap']['time_s'])
    r = items[ref]
    g = r['session'].gnss
    origin = None
    if g is not None and len(g['lat']):
        k = int(np.nanargmin(np.abs(np.asarray(g['t']) - r['lap']['start'])))
        origin = (float(g['lat'][k]), float(g['lon'][k]))
    ref_track = track(g, r['lap']['start'], r['lap']['end'], origin) if origin else None
    distance = ref_track is not None
    if distance:
        rt, rx, ry = ref_track
        rs = cumulative(rx, ry)
        length = float(rs[-1])
        grid = np.arange(0.0, length, GRID_M)
        grid = np.append(grid, length)
        ref_t_at = np.interp(grid, _strict(rs), rt)
    out = []
    for i, it in enumerate(items):
        s_obj, lap = it['session'], it['lap']
        a, b = lap['start'], lap['end']
        m = (s_obj.t >= a) & (s_obj.t <= b)
        idx = np.flatnonzero(m)[_thin(int(m.sum()))] if m.any() else np.array([], int)
        tt = s_obj.t[idx] - a
        row = {'key': it['key'], 'label': it['label'], 'time_s': lap['time_s'], 'ref': i == ref,
               't': _nums(tt, 3), 's': None, 'delta': None, 'track': None,
               'channels': {c: _nums(s_obj.channels[c][idx], 4) for c in channels if c in s_obj.channels}}
        if distance:
            ft = fs = None
            if i == ref:
                ft, fx, fy, fs = rt, rx, ry, rs
            else:
                tr = track(s_obj.gnss, a, b, origin)
                if tr is not None:
                    ft, fx, fy = tr
                    fs = _monotone(project(rx, ry, rs, fx, fy), length)
            if fs is not None:
                k = _thin(len(ft), MAX_TRACK)      # コース図の線（基準の周の頭の位置を原点にした m）
                row['track'] = {'t': _nums(ft[k], 3), 's': _nums(fs[k], 1), 'x': _nums(fx[k], 2), 'y': _nums(fy[k], 2)}
                row['s'] = _nums(np.interp(tt, ft, fs), 1)
                row['delta'] = _nums(np.interp(grid, _strict(fs), ft) - ref_t_at, 3)
        out.append(row)
    return {'ref': ref, 'ref_length_m': round(length, 1) if distance else None, 'distance': distance, 'laps': out,
            'origin': list(origin) if distance else None,
            'grid': _nums(grid, 1) if distance else [], 'ref_t': _nums(ref_t_at, 3) if distance else []}
