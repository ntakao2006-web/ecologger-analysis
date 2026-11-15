"""測位から計算する横G・前後G・旋回半径（仕様 analysis_map_features.md 2章・3-3）。

位置を0.5秒の移動平均でならしてから、時刻で2回微分する。NC のロガー（測位10Hz）の SUGO 11/8 で、
IMU の横G（0.3秒でならしたもの）と相関 0.997、差の rms 0.05 G だった（ならさないと 0.08 G、0.9秒だと 0.07 G。
この作りで31〜33周を測り直すと 0.996・0.06 G）。

- 前後Gは受信機の速さ（speed_kmh）を0.5秒でならして微分する（IMU と相関 0.96、差の rms 0.08 G。
  位置の2回微分の向きの成分は揺れが大きい）。速さが無い記録だけ位置から出す
- 横Gは**右へ曲がると＋**（IMU の LatAccel と同じ向き）
- 旋回半径は、ほぼまっすぐな所（MAX_RADIUS_M より大きい）と遅い所（MIN_KMH より遅い）では出さない（NaN）。
  直線では横Gが小さく、半径の値が暴れるため（AiM の GPS_Radius も約 600 m の外は0にしている）
- 測位の間隔が MAX_STEP_S より粗い記録（スマホの1Hzなど）では作らない。0.5秒でならす計算ができないため
"""
import math

import numpy as np

EARTH_M_PER_DEG = 111320.0
G = 9.80665
SMOOTH_S = 0.5            # 位置をならす幅
MAX_STEP_S = 0.25         # 測位の間隔（中央値）がこれより粗ければ作らない
MAX_GAP_S = 1.0           # これより長い抜けの両側は別々にならす（抜けをまたいで平均しない）
MAX_RADIUS_M = 600.0
MIN_KMH = 10.0


def _smooth_runs(t, v, w, runs):
    out = np.empty_like(v)
    for a, b in runs:
        tt, vv = t[a:b], v[a:b]
        c = np.concatenate(([0.0], np.cumsum(vv)))
        lo = np.searchsorted(tt, tt - w / 2, 'left')
        hi = np.searchsorted(tt, tt + w / 2, 'right')
        out[a:b] = (c[hi] - c[lo]) / np.maximum(hi - lo, 1)
    return out


def _grad_runs(v, t, runs):
    out = np.full(len(v), np.nan)
    for a, b in runs:
        if b - a >= 3:
            out[a:b] = np.gradient(v[a:b], t[a:b])
    return out


def from_gnss(gnss):
    """測位（列 t・lat・lon）から {t, lat_g, long_g, radius_m}（どれも測位の点ごと）。作れなければ None"""
    if gnss is None or len(gnss.get('t', ())) < 5:
        return None
    t = np.asarray(gnss['t'], float)
    lat, lon = np.asarray(gnss['lat'], float), np.asarray(gnss['lon'], float)
    sp = np.asarray(gnss['speed_kmh'], float) if 'speed_kmh' in gnss else np.full(len(t), np.nan)
    ok = np.isfinite(t) & np.isfinite(lat) & np.isfinite(lon)
    t, lat, lon, sp = t[ok], lat[ok], lon[ok], sp[ok]
    if len(t) < 5:
        return None
    dt = np.diff(t)
    if not np.all(dt > 0) or float(np.median(dt)) > MAX_STEP_S:
        return None
    lat0 = float(lat[0])
    x = (lon - lon[0]) * EARTH_M_PER_DEG * math.cos(math.radians(lat0))
    y = (lat - lat[0]) * EARTH_M_PER_DEG
    cut = np.flatnonzero(dt > MAX_GAP_S) + 1
    edges = np.concatenate(([0], cut, [len(t)]))
    runs = list(zip(edges[:-1], edges[1:]))
    xs, ys = _smooth_runs(t, x, SMOOTH_S, runs), _smooth_runs(t, y, SMOOTH_S, runs)
    vx, vy = _grad_runs(xs, t, runs), _grad_runs(ys, t, runs)
    ax, ay = _grad_runs(vx, t, runs), _grad_runs(vy, t, runs)
    v = np.hypot(vx, vy)
    with np.errstate(invalid='ignore', divide='ignore'):
        k = (vx * ay - vy * ax) / v ** 3            # 曲率（左へ曲がると＋）
        lat_g = -v * v * k / G                      # 右へ曲がると＋にそろえる
        long_g = (vx * ax + vy * ay) / v / G       # 受信機の速さが無いときだけ、位置から
        radius = 1.0 / np.abs(k)
    good = np.isfinite(sp)
    if good.sum() >= 5:                             # 前後Gは受信機の速さの微分（位置の2回微分より揺れない）
        spf = np.interp(t, t[good], sp[good]) / 3.6
        long_g = _grad_runs(_smooth_runs(t, spf, SMOOTH_S, runs), t, runs) / G
    slow = ~(v * 3.6 >= MIN_KMH)
    lat_g[slow] = np.nan
    long_g[slow] = np.nan
    radius[slow | ~(radius <= MAX_RADIUS_M)] = np.nan
    return {'t': t, 'lat_g': lat_g, 'long_g': long_g, 'radius_m': radius}
