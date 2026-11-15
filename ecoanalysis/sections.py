"""区間（コーナー）ごとの切り目・数・理論ベスト（仕様 analysis_map_features.md 3-2、SP-10-13）。

**区間**＝コーナーごとの切り目で区切った所。セクター（公式の区切り）とは別の組として持つ。
切り目は、基準の周のブレーキの踏み始め（ブレーキのチャンネルがあればそれ、無ければ前後G）に置き、
全部の周に同じ線を使う（周ごとに切ると、区間の数も場所もそろわない。2章の下見）。前後Gもブレーキも無い記録は、
直線の最高速の所に置く（最高速の所＝ブレーキの始まり）。区間は「そのコーナーのブレーキの踏み始め」から
「次のコーナーのブレーキの踏み始めの手前」まで。S字・続くコーナーで踏まない所は1区間になる。

切り目はセクターの線と同じ「線」（位置・向き・幅）で持ち、全部の周を線を越えた時刻で切る（laps.cut）。
1周の区間は、コントロールライン→1本目の線→…→最後の線→コントロールライン の、線の数＋1個。
"""
import math

import numpy as np

from . import laps as lapmod
from . import overlay

EARTH_M_PER_DEG = 111320.0
SPLIT_WIDTH_M = 30.0      # 区間の線の幅（デジスパイスも 15 m×両側）
MIN_APART_M = 50.0        # 切り目どうし・コントロールラインとこれより近い切り目は置かない
HEADING_S = 0.5           # 線の向きを決める前後の秒
BRAKE_SEARCH_BACK_S = 3.0 # 区間の頭より前のブレーキの踏み始めもこの秒までは拾う（基準より手前で踏んだ周）
BOTTOM_RADIUS_S = 2.0     # ボトムの前後この秒の中の最小の旋回半径
SLOW_RATIO = 1.05         # 平均の周：区間の中央値よりこれだけ遅い区間タイムはその区間だけ外す（詰まった・ピット）


def _speed(s):
    """(時刻, 速さ km/h)。車速のチャンネル Speed があればそれ、無ければ測位の速さ"""
    if 'Speed' in s.channels:
        v = np.asarray(s.channels['Speed'], float)
        ok = np.isfinite(v)
        if ok.sum() >= 2:
            return s.t[ok], v[ok]
    g = s.gnss
    if g is None or 'speed_kmh' not in g:
        return None
    t, v = np.asarray(g['t'], float), np.asarray(g['speed_kmh'], float)
    ok = np.isfinite(t) & np.isfinite(v)
    return (t[ok], v[ok]) if ok.sum() >= 2 else None


def _brake_times(s, a, b):
    """周の中（a〜b、セッションの時刻）のブレーキの踏み始めの時刻。拾えなければ None（前後Gもブレーキも無い）"""
    m = (s.t >= a) & (s.t <= b)
    if m.sum() < 3:
        return None
    t = s.t[m]
    brake = next((np.asarray(s.channels[c], float)[m] for c in overlay.BRAKE_CHANNELS if c in s.channels), None)
    ax = np.asarray(s.channels['LongAccel'], float)[m] if 'LongAccel' in s.channels else None
    if brake is None and ax is None:
        return None
    return [on for on, _ in overlay.brakes(t, ax, brake)]


def _track(s, a, b):
    """周の測位の線 (セッションの時刻, x, y, 周の頭からの距離)。原点は周の頭"""
    g = s.gnss
    t = np.asarray(g['t'], float)
    k = int(np.nanargmin(np.abs(t - a)))
    origin = (float(g['lat'][k]), float(g['lon'][k]))
    tr = overlay.track(g, a, b, origin)
    if tr is None:
        return None, origin
    ft, x, y = tr
    return (ft + a, x, y, overlay.cumulative(x, y)), origin


def _line_at(when, tr, origin):
    """時刻 when の位置に、進む向きの線を置く"""
    ft, x, y, _ = tr
    px, py = float(np.interp(when, ft, x)), float(np.interp(when, ft, y))
    ax_, ay_ = float(np.interp(when - HEADING_S, ft, x)), float(np.interp(when - HEADING_S, ft, y))
    bx, by = float(np.interp(when + HEADING_S, ft, x)), float(np.interp(when + HEADING_S, ft, y))
    lat0, lon0 = origin
    return {'lat': round(lat0 + py / EARTH_M_PER_DEG, 8),
            'lon': round(lon0 + px / (EARTH_M_PER_DEG * math.cos(math.radians(lat0))), 8),
            'bearing': round(math.degrees(math.atan2(bx - ax_, by - ay_)) % 360, 1), 'width_m': SPLIT_WIDTH_M}


def auto(s, lap):
    """基準の周 lap（{start, end}）のブレーキの踏み始めに区間の線を置く。
    返す値：{splits: [線], how: 'brake'|'speed', at_m: [周の頭からの距離]}。置けなければ ValueError（理由の文）"""
    if s.gnss is None:
        raise ValueError('測位が入っていないので、区間の線を置けません')
    a, b = lap['start'], lap['end']
    tr, origin = _track(s, a, b)
    if tr is None:
        raise ValueError('この周の測位が足りないので、区間の線を置けません')
    ft, _, _, dist = tr
    length = float(dist[-1])
    times, how = _brake_times(s, a, b), 'brake'
    if times is None:                            # 前後Gもブレーキも無い：直線の最高速の所
        sp = _speed(s)
        if sp is None:
            raise ValueError('前後G・ブレーキ・速さのどれも無いので、区間の線を置けません')
        st, sv = sp
        m = (st >= a) & (st <= b)
        times = [w for w, _, kind in overlay.extremes(st[m], sv[m]) if kind == 'max']
        how = 'speed'
    at, out = [], []
    for w in sorted(times):
        d = float(np.interp(w, ft, dist))
        if d < MIN_APART_M or length - d < MIN_APART_M or (at and d - at[-1] < MIN_APART_M):
            continue
        at.append(d)
        out.append(_line_at(w, tr, origin))
    if not out:
        raise ValueError('この周ではブレーキの踏み始めが見つからないので、区間の線を置けません' if how == 'brake'
                         else 'この周では直線の最高速の所が見つからないので、区間の線を置けません')
    return {'splits': out, 'how': how, 'at_m': [round(d, 1) for d in at]}


def _bounds(lap):
    """周の区間の境目の時刻 [周の頭, 線1, …, 周の終わり]。越えていない線は None"""
    return [lap['start'], *lap.get('split_t', []), lap['end']]


def metrics(s, laps, dyn=None):
    """周ごとの区間の数 [{entry, exit, brake_m, min_kmh, min_at_m, min_radius_m}]（区間の数だけ）を laps の各周の 'sec' に入れる。
    entry・exit：区間の頭・終わりの線を越えたときの速さ（km/h）。brake_m：区間の頭の線からブレーキの踏み始めまでの距離（m、＋は奥）。
    min_kmh・min_at_m：区間の中の最小の速さと、その所の区間の頭からの距離。min_radius_m：ボトムの前後2秒の最小の旋回半径（測位から）"""
    sp = _speed(s)
    for lap in laps:
        bounds = _bounds(lap)
        n = len(bounds) - 1
        if n < 2 or s.gnss is None or sp is None:
            lap['sec'] = []
            continue
        tr, _ = _track(s, lap['start'], lap['end'])
        if tr is None:
            lap['sec'] = [None] * n
            continue
        ft, _, _, dist = tr
        st, sv = sp
        brakes = _brake_times(s, lap['start'] - BRAKE_SEARCH_BACK_S, lap['end']) or []

        def d_at(w):
            return float(np.interp(w, ft, dist))

        def v_at(w):
            return float(np.interp(w, st, sv))
        out = []
        for p, q in zip(bounds, bounds[1:]):
            if p is None or q is None or q <= p:
                out.append(None)
                continue
            m = (st >= p) & (st <= q)
            row = {'entry': round(v_at(p), 1), 'exit': round(v_at(q), 1), 'brake_m': None,
                   'min_kmh': None, 'min_at_m': None, 'min_radius_m': None}
            near = [w for w in brakes if p - BRAKE_SEARCH_BACK_S <= w <= p + (q - p) / 2]
            if near and p > lap['start']:          # 周の頭（コントロールライン）の区間はブレーキの切り目ではない
                w = min(near, key=lambda w: abs(w - p))
                row['brake_m'] = round(d_at(w) - d_at(p) if w >= lap['start'] else -_back_m(st, sv, w, p), 1)
            if m.sum() >= 1:
                k = int(np.argmin(sv[m]))
                tmin = float(st[m][k])
                row['min_kmh'] = round(float(sv[m][k]), 1)
                row['min_at_m'] = round(d_at(tmin) - d_at(p), 1)
                if dyn is not None:
                    dm = (dyn['t'] >= tmin - BOTTOM_RADIUS_S) & (dyn['t'] <= tmin + BOTTOM_RADIUS_S)
                    r = dyn['radius_m'][dm]
                    r = r[np.isfinite(r)]
                    if len(r):
                        row['min_radius_m'] = round(float(r.min()), 1)
            out.append(row)
        lap['sec'] = out
    return laps


def _back_m(st, sv, w, p):
    """時刻 w から p まで走った距離（速さを足して。周の頭より前で測位の線が無い所用）"""
    m = (st >= w) & (st <= p)
    if m.sum() < 2:
        return 0.0
    v, tt = sv[m] / 3.6, st[m]
    return float(np.sum(np.diff(tt) * (v[1:] + v[:-1]) / 2))


def rolling(gnss, line, splits, max_s=lapmod.MAX_LAP_S):
    """連続ベスト：周の境目をまたいで、続いた区間を1周ぶん（線の数＋1区間）つないだときの最速。
    {time_s, start, from}（from は始めた線の番号：0 がコントロールライン）か None。
    1秒より長い測位の抜けをまたぐものは数えない"""
    if not splits:
        return None
    marks = [(float(w), 0) for w in lapmod.crossings(gnss, line)]
    for i, sp in enumerate(splits):
        marks += [(float(w), i + 1) for w in lapmod.crossings(gnss, sp)]
    marks.sort()
    n = len(splits) + 1
    if len(marks) <= n:
        return None
    t = np.asarray(gnss['t'], float)
    t = t[np.isfinite(t)]
    gaps = t[1:][np.diff(t) > lapmod.MAX_FIX_GAP_S]       # 抜けの終わりの時刻
    best = None
    for k in range(len(marks) - n):
        ok = all(marks[k + j + 1][1] == (marks[k + j][1] + 1) % n for j in range(n))
        if not ok:
            continue
        a, b = marks[k][0], marks[k + n][0]
        dur = b - a
        if dur < lapmod.MIN_LAP_S or dur > max_s or np.any((gaps > a) & (gaps <= b)):
            continue
        if best is None or dur < best['time_s']:
            best = {'time_s': round(dur, 3), 'start': round(a, 3), 'from': marks[k][1]}
    return best


def summarize(lap_lists, rollings=()):
    """同じコースの開いている走行の周（app の周の一覧の並び）から、区間とセクターの理論ベスト・連続ベスト・平均の周。
    測位の抜けた周は使わない。平均の周は、区間タイムがその区間の中央値より SLOW_RATIO 倍以上遅い周を、その区間だけ外す。
    返す値：{best_lap, sectors: {best, theory}, splits: {best, theory, mean, sd, used, average, rolling}}"""
    pool = [x for laps in lap_lists for x in (laps or []) if not x.get('gap')]
    out = {'best_lap': min((x['time_s'] for x in pool), default=None)}
    for key in ('sectors', 'splits'):
        n = max((len(x.get(key) or []) for x in pool), default=0)
        best, theory = [], 0.0
        cols = [[x[key][i] for x in pool if len(x.get(key) or []) == n and x[key][i] is not None] for i in range(n)]
        for c in cols:
            best.append(round(min(c), 3) if c else None)
        theory = round(sum(best), 3) if n and all(v is not None for v in best) else None
        part = {'best': best, 'theory': theory}
        if key == 'splits':
            mean, sd, used = [], [], []
            for c in cols:
                if not c:
                    mean.append(None), sd.append(None), used.append(0)
                    continue
                med = float(np.median(c))
                keep = [v for v in c if v < med * SLOW_RATIO]
                mean.append(round(float(np.mean(keep)), 3))
                sd.append(round(float(np.std(keep)), 3) if len(keep) > 1 else None)
                used.append(len(keep))
            part.update({'mean': mean, 'sd': sd, 'used': used,
                         'average': round(sum(mean), 3) if n and all(v is not None for v in mean) else None})
            rs = [r for r in rollings if r]
            part['rolling'] = min((r['time_s'] for r in rs), default=None)
        out[key] = part
    return out
