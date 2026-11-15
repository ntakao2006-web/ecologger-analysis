"""選んだ周を重ねる：横軸の時間・距離と、基準の周とのタイムの差。

距離は基準の周の走った線に沿った距離（m）。ほかの周は、測位の点を基準の周の線へ投影して
「基準の周でいうと何m地点か」を出す（位置で合わせる）。こうすると、走る線が少し違っても
同じ場所どうしが並び、タイムの差は「同じ場所に何秒遅れて着いたか」になる。
周の始まりは0m、終わりは基準の周の長さにそろえる（どちらもコントロールラインの上）。
"""
import math

import numpy as np

from . import dynamics

EARTH_M_PER_DEG = 111320.0
MAX_POINTS = 6000         # 1周に送る点の数の上限（多ければ間引く）
MAX_TRACK = 3000          # コース図に送る測位の点の数の上限
GRID_M = 2.0              # タイムの差を出す距離の刻み
SEARCH_BACK, SEARCH_AHEAD = 5, 60   # 投影で探す基準の線の区間（直前に合った区間からの数）

# コース図の塗り・印（仕様 analysis_map_features.md 3-1）
RATE_M = 20.0             # タイムの差の増え方をならす幅（この幅の両端の差 ÷ 幅 = 1 m あたりの秒）
BAD_HACC_M = 1.5          # 測位の精度がこれより悪い所はグレー
BAD_GAP_S = 0.3           # 測位がこれより長く抜けた所もグレー
DECEL_G = -0.1            # 前後Gがこれより小さい所を点線（減速）
BRAKE_ON_G, BRAKE_HOLD_S, BRAKE_OFF_G = -0.25, 0.3, -0.05   # −0.25 G を0.3秒以上下回った所を1回のブレーキとし、
                                                            # その前後で −0.05 G を越えた所を踏み始め・離した所にする
ACCEL_SMOOTH_S = 0.2      # 前後Gをならす幅（IMU の細かい揺れで点線・印がちらつかないように）
EXTREME_SMOOTH_S = 0.5    # 極大・極小を拾う前にならす幅
EXTREME_SWING = 0.08      # 極大・極小とみなす戻りの大きさ（周の中の 2〜98% の幅に対する割合）
EDGE_S = 0.5              # 周の頭と終わりのこの秒の中の極大・極小は捨てる（線を越えた所はコーナーではない）
BRAKE_CHANNELS = ('Brake', 'BrakeOn')   # ブレーキの ON/OFF のチャンネル（あればこちらで踏み始めを拾う）
G_STEP_S = 0.1           # 摩擦円の点の間隔（SP-10-14）


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


def _at(kt, full_t, v):
    """周の行の値 v を、線の点の時刻 kt で読む（v が無ければ NaN）"""
    if v is None or len(full_t) < 2:
        return np.full(len(kt), np.nan)
    v = np.asarray(v, float)
    ok = np.isfinite(v)
    return np.interp(kt, full_t[ok], v[ok]) if ok.sum() >= 2 else np.full(len(kt), np.nan)


def _values(s_obj, m, names, calc):
    """周の行（m）のチャンネルの値。記録に無い名前は calc で計算する。計算できない理由は '_why' に"""
    out, rest = {}, []
    for c in dict.fromkeys(n for n in names if n):
        if c in s_obj.channels:
            out[c] = np.asarray(s_obj.channels[c], float)[m]
        elif c not in BRAKE_CHANNELS:
            rest.append(c)
    if calc and rest and m.any():
        vals, why = calc(s_obj, m, rest)
        out.update({c: np.asarray(v, float) for c, v in vals.items()})
        out['_why'] = why
    return out


def _smooth(t, x, w):
    """時刻 t の幅 w 秒の移動平均（両側）。NaN は前後の値で埋めてからならす"""
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    if ok.sum() < 2:
        return x
    x = np.interp(t, t[ok], x[ok])
    c = np.concatenate(([0.0], np.cumsum(x)))
    lo = np.searchsorted(t, t - w / 2, 'left')
    hi = np.searchsorted(t, t + w / 2, 'right')
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)


def _runs(flag):
    """True が続く所の (始まり, 終わり) の番号（終わりは含まない）"""
    d = np.diff(np.concatenate(([0], np.asarray(flag, int), [0])))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def brakes(t, ax=None, brake=None):
    """ブレーキの踏み始めと離した所の時刻 [(on, off)]。brake（ON/OFF）があればそれ、無ければ前後G（g）で拾う"""
    out = []
    if brake is not None:
        b = np.nan_to_num(np.asarray(brake, float)) > 0.5
        for i, j in _runs(b):
            if t[j - 1] - t[i] >= 0.1:             # ON/OFF は前後Gより短くても本物。チャタリングだけ捨てる
                out.append((float(t[i]), float(t[min(j, len(t) - 1)])))
        return out
    if ax is None:
        return out
    a = _smooth(t, ax, ACCEL_SMOOTH_S)
    after = -1
    for i, j in _runs(a < BRAKE_ON_G):
        if i <= after or t[j - 1] - t[i] < BRAKE_HOLD_S:
            continue
        start = i                                   # 踏み始め＝そこから戻って −0.05 G を上回る最後の所
        while start > after + 1 and a[start - 1] < BRAKE_OFF_G:
            start -= 1
        back = np.flatnonzero(a[j:] > BRAKE_OFF_G)
        k = j + int(back[0]) if len(back) else len(t) - 1
        out.append((float(t[start]), float(t[k])))
        after = k
    return out


def extremes(t, v):
    """極大・極小 [(時刻, 値, 'max'|'min')]。ならした値の山と谷のうち、そこから幅の EXTREME_SWING 以上戻った所だけ"""
    v = _smooth(t, v, EXTREME_SMOOTH_S)
    ok = np.flatnonzero(np.isfinite(v))
    if len(ok) < 3:
        return []
    lo, hi = np.percentile(v[ok], [2, 98])
    h = max((hi - lo) * EXTREME_SWING, 1e-9)
    found = []
    mode, k = 0, ok[0]          # mode 1：山を探している、-1：谷を探している、0：まだ向きが決まらない
    hi_k = lo_k = ok[0]
    for i in ok[1:]:
        if mode == 0:
            hi_k = i if v[i] > v[hi_k] else hi_k
            lo_k = i if v[i] < v[lo_k] else lo_k
            if v[hi_k] - v[i] >= h:
                found.append((hi_k, 'max'))
                mode, k = -1, i
            elif v[i] - v[lo_k] >= h:
                found.append((lo_k, 'min'))
                mode, k = 1, i
        elif mode > 0:
            if v[i] >= v[k]:
                k = i
            elif v[k] - v[i] >= h:
                found.append((k, 'max'))
                mode, k = -1, i
        else:
            if v[i] <= v[k]:
                k = i
            elif v[i] - v[k] >= h:
                found.append((k, 'min'))
                mode, k = 1, i
    a, b = t[0] + EDGE_S, t[-1] - EDGE_S
    return [(float(t[i]), float(v[i]), kind) for i, kind in found if a <= t[i] <= b]


def _gnss_quality(gnss, ts):
    """線の点の時刻 ts（セッションの時刻）ごとの (hacc_m, 悪いか)。
    悪い＝精度が BAD_HACC_M より悪いか、その点から次の測位までが BAD_GAP_S より長い（点から次の点への線をグレーにする）"""
    t = np.asarray(gnss['t'], float)
    ok = np.isfinite(t) & np.isfinite(np.asarray(gnss['lat'], float)) & np.isfinite(np.asarray(gnss['lon'], float))
    hacc = np.asarray(gnss['hacc_m'], float)[ok] if 'hacc_m' in gnss else np.full(ok.sum(), np.nan)
    t = t[ok]
    good = np.isfinite(hacc)
    h = np.interp(ts, t[good], hacc[good]) if good.sum() else np.full(len(ts), np.nan)
    j = np.clip(np.searchsorted(t, ts + 1e-6, 'right'), 1, len(t) - 1)
    gap = (t[j] - t[j - 1]) > BAD_GAP_S
    return h, (np.nan_to_num(h, nan=0.0) > BAD_HACC_M) | gap


def _rate(grid, delta):
    """タイムの差の増え方（1 m あたりの秒）。RATE_M の幅の両端の差 ÷ 幅。＋は損（基準より遅れが増えている）"""
    a = np.clip(grid - RATE_M / 2, 0, grid[-1])
    b = np.clip(grid + RATE_M / 2, 0, grid[-1])
    w = b - a
    return np.where(w > 0, (np.interp(b, grid, delta) - np.interp(a, grid, delta)) / np.where(w > 0, w, 1), 0.0)


def _thin(n, limit=MAX_POINTS):
    step = max(1, math.ceil(n / limit))
    idx = np.arange(0, n, step)
    if idx[-1] != n - 1:
        idx = np.append(idx, n - 1)
    return idx


def _paint_and_marks(row, s_obj, m, a, line, kt, paint, mark, calc):
    """周の線の塗る値（track.v）・減速の点（track.dec）と、地図の上の印（marks）を row に入れる"""
    ft, fx, fy, fs = line
    tr = row['track']
    full_t = s_obj.t[m] - a
    vals = _values(s_obj, m, [paint, mark, 'LongAccel', 'Speed', *BRAKE_CHANNELS], calc)
    if paint:
        tr['v'] = _nums(_at(kt, full_t, vals.get(paint)), 4)
        why = vals.get('_why', {}).get(paint) or (None if paint in vals else 'この走行に無いチャンネル')
        if why:
            row['paint_error'] = why
    ax = vals.get('LongAccel')
    if ax is not None and len(full_t) > 1:
        tr['dec'] = [int(x) for x in _at(kt, full_t, _smooth(full_t, ax, ACCEL_SMOOTH_S)) < DECEL_G]
    if len(full_t) < 3:
        return
    speed = vals.get('Speed')
    if speed is None and s_obj.gnss is not None:          # 車速のチャンネルが無ければ測位の速さ
        gt = np.asarray(s_obj.gnss['t'], float) - a
        gv = np.asarray(s_obj.gnss['speed_kmh'], float)
        ok = np.isfinite(gt) & np.isfinite(gv)
        speed = np.interp(full_t, gt[ok], gv[ok]) if ok.sum() >= 2 else None
    brake = next((vals[c] for c in BRAKE_CHANNELS if c in vals), None)

    def place(kind, when, v):
        return {'k': kind, 't': round(when, 3), 's': round(float(np.interp(when, ft, fs)), 1),
                'x': round(float(np.interp(when, ft, fx)), 2), 'y': round(float(np.interp(when, ft, fy)), 2),
                'v': None if v is None or not math.isfinite(v) else round(float(v), 2)}

    def speed_at(when):
        return None if speed is None else float(_at(np.array([when]), full_t, speed)[0])
    marks = []
    for on, off in brakes(full_t, ax, brake):
        marks += [place('on', on, speed_at(on)), place('off', off, speed_at(off))]
    mv = speed if mark == 'Speed' else vals.get(mark)
    if mv is not None:
        marks += [place(kind, when, v) for when, v, kind in extremes(full_t, mv)]
    row['marks'] = sorted(marks, key=lambda x: x['t'])


def friction_points(s_obj, a, b, prefer):
    """摩擦円の点（周の頭からの秒 t・横G x・前後G y・旋回半径 r・速さ v を G_STEP_S ごと）と、使った G の出どころ。
    作れなければ (None, None)"""
    src = dynamics.g_source(s_obj, prefer)
    m = (s_obj.t >= a) & (s_obj.t <= b)
    if src is None or m.sum() < 2:
        return None, None
    lat, lg, how = src
    t = s_obj.t[m]
    tg = np.arange(t[0], t[-1] + 1e-9, G_STEP_S)
    rad = s_obj.channels.get(dynamics.RADIUS_NAME)
    sp = dynamics._speed_rows(s_obj)

    def on(v):
        if v is None:
            return [None] * len(tg)
        v = np.asarray(v, float)[m]
        out = np.interp(tg, t, v)
        j = np.clip(np.searchsorted(t, tg), 1, len(t) - 1)
        out[~(np.isfinite(v[j - 1]) & np.isfinite(v[j]))] = np.nan     # 空欄をまたいで補わない（直線の半径など）
        return out
    return {'t': _nums(tg - a, 2), 'x': _nums(on(lat), 3), 'y': _nums(on(lg), 3), 'r': _nums(on(rad), 1),
            'v': _nums(on(sp), 1)}, how


def _envelope(rows):
    xs, ys, vs = [], [], []
    for r in rows:
        g = r.get('g')
        if g:
            xs += g['x']; ys += g['y']; vs += g['v']
    f = lambda a: np.array([np.nan if v is None else v for v in a], float)
    if not xs:
        return None
    v = f(vs)
    env = dynamics.envelope(f(xs), f(ys), ~(v < dynamics.MOVING_KMH))
    return None if env is None else _nums(env, 3)


def compare(items, channels, ref=None, calc=None, paint=None, mark='Speed', gsrc=None):
    """items：[{key, label, session, lap}]（lap は {start, end, time_s}）。ref：基準の周の番号（無ければ一番速い周）
    calc：計算チャンネル。calc(session, rows, names) → (値 名前→rows の行の配列, 計算できない理由 名前→文)。
    周の行だけで計算する（積分は周の頭で0から）。
    paint：コース図の線を塗るチャンネル（線の点ごとの値を track.v に入れる）。mark：極大・極小の印を拾うチャンネル
    返す値：{ref, ref_length_m, distance: 距離が使えるか, laps: [{key, label, time_s, t, s, channels, calc_errors, delta,
    track: {t, s, x, y, dd, rate, hacc, bad, v?, dec?}, marks: [{k, t, s, x, y, v}]}], grid}
    track の dd は基準の周とのタイムの差、rate はその増え方（秒/m、＋は損）、bad は測位の質が悪い点（1）、
    dec は減速している点（1。前後Gがあるとき）。marks の k は on（ブレーキの踏み始め）・off（離した所）・min・max、
    v は on/off なら速さ、min/max なら mark の値。
    gsrc：摩擦円の G の出どころ（'imu'・'gnss'。None なら摩擦円の点を作らない）。作ると周ごとに g（friction_points）、
    全体に friction: {src, env（選んだ周の向きごとの合成Gの99%。0°＝右、ENV_STEP_DEG 刻み）, step}"""
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
    out, srcs = [], set()
    for i, it in enumerate(items):
        s_obj, lap = it['session'], it['lap']
        a, b = lap['start'], lap['end']
        m = (s_obj.t >= a) & (s_obj.t <= b)
        k_rows = _thin(int(m.sum())) if m.any() else np.array([], int)
        idx = np.flatnonzero(m)[k_rows]
        tt = s_obj.t[idx] - a
        row = {'key': it['key'], 'label': it['label'], 'time_s': lap['time_s'], 'ref': i == ref,
               't': _nums(tt, 3), 's': None, 'delta': None, 'track': None, 'marks': [], 'calc_errors': {},
               'channels': {c: _nums(s_obj.channels[c][idx], 4) for c in channels if c in s_obj.channels}}
        rest = [c for c in channels if c not in s_obj.channels]
        if calc and rest and m.any():
            vals, why = calc(s_obj, m, rest)
            row['channels'].update({c: _nums(v[k_rows], 4) for c, v in vals.items()})
            row['calc_errors'] = why
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
                kt, ks = ft[k], fs[k]
                tr = row['track'] = {'t': _nums(kt, 3), 's': _nums(ks, 1), 'x': _nums(fx[k], 2), 'y': _nums(fy[k], 2)}
                row['s'] = _nums(np.interp(tt, ft, fs), 1)
                delta = np.interp(grid, _strict(fs), ft) - ref_t_at
                row['delta'] = _nums(delta, 3)
                tr['dd'] = _nums(np.interp(ks, grid, delta), 3)
                tr['rate'] = _nums(np.interp(ks, grid, _rate(grid, delta)), 5)
                hacc, bad = _gnss_quality(s_obj.gnss, kt + a)
                tr['hacc'] = _nums(hacc, 2)
                tr['bad'] = [int(x) for x in bad]
                _paint_and_marks(row, s_obj, m, a, (ft, fx, fy, fs), kt, paint, mark, calc)
        if gsrc:
            row['g'], how = friction_points(s_obj, a, b, gsrc)
            if how:
                srcs.add(how)
        out.append(row)
    friction = None
    if gsrc:
        friction = {'want': gsrc, 'src': '+'.join(sorted(srcs)) or None, 'env': _envelope(out), 'step': dynamics.ENV_STEP_DEG}
    return {'ref': ref, 'ref_length_m': round(length, 1) if distance else None, 'distance': distance, 'laps': out,
            'paint': paint, 'mark': mark, 'friction': friction,
            'origin': list(origin) if distance else None,
            'grid': _nums(grid, 1) if distance else [], 'ref_t': _nums(ref_t_at, 3) if distance else []}
