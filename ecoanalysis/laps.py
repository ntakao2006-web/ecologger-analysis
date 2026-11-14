"""周を測位から切り直す。

ロガーが数えた周（session.json の laps）とは別に、コースの線で測位から周を切る。コントロールラインが
ロガーと解析の側で違うことがあり、別の日・別の機器の周を比べるには、同じ線で切る必要があるため。

線を越えた時刻は、線の手前と先の2つの測位から、線の上に来た瞬間を補った値（測位の時刻で）。
線は向きを持ち（bearing の向きへ越えたときだけ数える）、幅 width_m の内側だけを見る。
1秒より離れた2点のあいだでは越えたと見ない。越えたあと、線の中心から幅より遠くへ離れるまでは
次を数えない（止まって線の上でふらついても周が増えないように）。
EcoLogger のロガー（EcoCapture-Web/lap.py）と同じ考え方で、同じ線ならタイムは数msで合う。
"""
import math

import numpy as np

EARTH_M_PER_DEG = 111320.0
MAX_FIX_GAP_S = 1.0       # これより離れた2点のあいだでは線を越えたと見ない
MIN_LAP_S = 10.0          # これより短い周は数えない
MAX_LAP_S = 3600.0        # これより長い周（ピットで長く止まった・次の日）は周にしない
MATCH_S = 0.5             # ロガーの周と終わりがこれ以内なら、同じ周とみなす


def _local_xy(gnss, line):
    lat0, lon0 = line['lat'], line['lon']
    x = (np.asarray(gnss['lon'], float) - lon0) * EARTH_M_PER_DEG * math.cos(math.radians(lat0))
    y = (np.asarray(gnss['lat'], float) - lat0) * EARTH_M_PER_DEG
    return x, y


def valid_line(line):
    return isinstance(line, dict) and all(isinstance(line.get(k), (int, float)) for k in ('lat', 'lon', 'bearing', 'width_m')) \
        and line['width_m'] > 0


def crossings(gnss, line):
    """測位（列 t・lat・lon）が線を前向きに越えた時刻の配列"""
    if not gnss or not valid_line(line) or len(gnss['t']) < 2:
        return np.array([])
    t = np.asarray(gnss['t'], float)
    x, y = _local_xy(gnss, line)
    ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(t)
    t, x, y = t[ok], x[ok], y[ok]
    rad = math.radians(line['bearing'])
    nx, ny = math.sin(rad), math.cos(rad)
    d = x * nx + y * ny                     # 線から進む向きへの距離
    side = x * ny - y * nx                  # 線の中心から横への距離（+ が進む向きの右）
    far = np.hypot(x, y) > line['width_m']  # 次を数えてよい所（中心から幅より遠い）
    dt = np.diff(t)
    cand = np.flatnonzero((d[:-1] < 0) & (d[1:] >= 0) & (dt > 0) & (dt <= MAX_FIX_GAP_S))
    out, armed_from = [], 0
    for i in cand:
        if i < armed_from:
            continue
        f = -d[i] / (d[i + 1] - d[i])
        if abs(side[i] + (side[i + 1] - side[i]) * f) > line['width_m'] / 2:
            continue
        out.append(t[i] + dt[i] * f)
        nxt = np.flatnonzero(far[i + 1:])
        armed_from = i + 1 + nxt[0] if len(nxt) else len(t)
    return np.array(out)


def _sector_times(start, end, sector_crossings):
    """周の中のセクターのタイム。線ごとに周の中で最初に越えた時刻で区切る。越えていない所と、順に並ばない線から先は None"""
    if not sector_crossings:
        return []
    marks = []
    for c in sector_crossings:
        inside = c[(c > start) & (c < end)]
        marks.append(float(inside[0]) if len(inside) else None)
    bounds = [start] + marks + [end]
    out, broken = [], False
    for p, q in zip(bounds, bounds[1:]):
        if p is not None and q is not None and q <= p:
            broken = True                # 順に並ばない線があれば、そこから先は分からない
        out.append(None if broken or p is None or q is None else round(q - p, 3))
    return out


def cut(gnss, line, sectors=(), logger_laps=()):
    """測位から周を切る。[{lap, start, end, time_s, sectors, gap, logger}]
    lap はロガーの周と同じ周ならその番号、それ以外は前後から続き番号。logger はロガーの周 {lap, time_s, diff_ms} か None。
    gap は周の中に1秒より長い測位の抜けがあるか"""
    c = crossings(gnss, line)
    if len(c) < 2:
        return []
    sec_c = [crossings(gnss, s) for s in sectors] if sectors and all(valid_line(s) for s in sectors) else []
    t = np.asarray(gnss['t'], float)
    t = t[np.isfinite(t) & np.isfinite(np.asarray(gnss['lat'], float)) & np.isfinite(np.asarray(gnss['lon'], float))]
    laps = []
    for a, b in zip(c, c[1:]):
        dur = b - a
        if dur < MIN_LAP_S or dur > MAX_LAP_S:
            continue
        inside = t[(t >= a) & (t <= b)]
        gap = bool(len(inside) < 2 or np.max(np.diff(np.concatenate(([a], inside, [b])))) > MAX_FIX_GAP_S)
        laps.append({'start': round(float(a), 3), 'end': round(float(b), 3), 'time_s': round(float(dur), 3),
                     'sectors': _sector_times(a, b, sec_c), 'gap': gap, 'logger': None})
    _number(laps, list(logger_laps))
    return laps


def _closest(laps, logger_laps, limit):
    """周ごとに、終わりがいちばん近いロガーの周（limit(周) 秒以内、1対1）。{周の位置: ロガーの周}"""
    pairs = []
    for i, x in enumerate(laps):
        for k, g in enumerate(logger_laps):
            if isinstance(g, dict) and isinstance(g.get('end'), (int, float)) and abs(g['end'] - x['end']) <= limit(x):
                pairs.append((abs(g['end'] - x['end']), i, k))
    out, used = {}, set()
    for _, i, k in sorted(pairs):
        if i not in out and k not in used:
            out[i] = logger_laps[k]
            used.add(k)
    return out


def _number(laps, logger_laps):
    # ロガーと比べるのは、終わりが MATCH_S 以内で合う周だけ（同じ線で数えた同じ周）
    for i, g in _closest(laps, logger_laps, lambda x: MATCH_S).items():
        laps[i]['logger'] = {'lap': g['lap'], 'time_s': g['time_s'], 'diff_ms': round((laps[i]['time_s'] - g['time_s']) * 1000, 1)}
    # 番号は、終わりが周のタイムの半分以内のロガーの周から取る（線を動かしても番号が変わらないように）。
    # それも無い周は、いちばん近い番号つきの周から数える
    near = _closest(laps, logger_laps, lambda x: x['time_s'] / 2)
    anchors = [(i, g['lap'] - i) for i, g in near.items()]
    for i, x in enumerate(laps):
        if i in near:
            x['lap'] = near[i]['lap']
        elif anchors:
            j, off = min(anchors, key=lambda a: abs(a[0] - i))
            x['lap'] = i + off
        else:
            x['lap'] = i + 1


def for_session(s, line=None, sectors=None):
    """走行の周をコースの線で切る。line・sectors を渡さなければ書き出しに入っている線。(周の一覧, 使った線, 切れなかった理由)"""
    course = s.meta.get('course') or {}
    line = line or course.get('line')
    sectors = course.get('sectors') or [] if sectors is None else sectors
    if s.gnss is None:
        return None, None, '測位が入っていない'
    if not valid_line(line):
        return None, None, 'コースの線が入っていない'
    return cut(s.gnss, line, sectors, s.laps), line, None
