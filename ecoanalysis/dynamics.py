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


# ---------------------------------------------------------------- 走行のチャンネルにする（SP-10-14、仕様 3-3）
# 測位から計算した値は、グラフ・計算チャンネル・地図の塗り・周の表で記録のチャンネルと同じに使えるよう、
# 開いたときに走行のチャンネル（記録の時刻の行）へ足す。センサーの値ではないので、名前と見出しで分かるようにする
LAT_NAME, LONG_NAME, RADIUS_NAME, GRIP_NAME = 'GpsLatG', 'GpsLongG', 'GpsRadius', 'GripUse'
DERIVED = (
    (LAT_NAME, 'lat_g', 'g', '横G（測位から計算）'),
    (LONG_NAME, 'long_g', 'g', '前後G（測位から計算）'),
    (RADIUS_NAME, 'radius_m', 'm', '旋回半径（測位から計算）'),
)
NEAR_S = 0.3              # 記録の行から最も近い測位がこれより離れていれば空欄（抜けをまたいで補わない）
IMU_SMOOTH_S = 0.3        # IMU の G をならす幅（摩擦円・使い方。細かい振動で包絡がふくらまないように。2章の下見と同じ）
ENV_STEP_DEG = 15         # 包絡の向きの刻み
ENV_Q = 99.0              # 包絡にする合成Gの百分位
ENV_MIN_POINTS = 20       # 1つの向きにこれより点が少なければ、その向きは両隣から補う
MOVING_KMH = 10.0         # 包絡・使い方は、これより速く走っている所だけ


def _near(t_rows, t_pts):
    """記録の行ごとに、いちばん近い測位までの秒"""
    j = np.clip(np.searchsorted(t_pts, t_rows), 1, len(t_pts) - 1)
    return np.minimum(np.abs(t_rows - t_pts[j - 1]), np.abs(t_pts[j] - t_rows))


def _on_rows(t_rows, t_pts, v, near):
    ok = np.isfinite(v)
    out = np.full(len(t_rows), np.nan)
    if ok.sum() >= 2:
        out = np.interp(t_rows, t_pts[ok], v[ok])
        # 空欄（直線の半径・遅い所）の点に近い行は空欄のまま（補って値を作らない）
        j = np.clip(np.searchsorted(t_pts, t_rows), 1, len(t_pts) - 1)
        bad = ~(ok[j - 1] & ok[j])
        out[bad | (near > NEAR_S)] = np.nan
    return out


def smooth(t, v, w):
    """時刻 t の幅 w 秒の移動平均。空欄は前後から埋めてからならす（全部空欄ならそのまま）"""
    v = np.asarray(v, float)
    ok = np.isfinite(v)
    if ok.sum() < 2:
        return v
    v = np.interp(t, t[ok], v[ok])
    c = np.concatenate(([0.0], np.cumsum(v)))
    lo = np.searchsorted(t, t - w / 2, 'left')
    hi = np.searchsorted(t, t + w / 2, 'right')
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)


def g_source(s, prefer='imu'):
    """摩擦円に使う (横G, 前後G, 'imu'|'gnss')（記録の行ごと。横Gは右へ曲がると＋、前後Gは加速が＋）。
    prefer='imu' でも IMU の横G・前後G（LatAccel・LongAccel）が無ければ測位から。どちらも無ければ None。
    ならした IMU の値は走行に覚える（重ね合わせを頼むたびに走行の全部をならさない）"""
    memo = s.__dict__.setdefault('_gsrc', {})
    if prefer not in memo:
        memo[prefer] = _g_source(s, prefer)
    return memo[prefer]


def _g_source(s, prefer):
    ch = s.channels
    if prefer != 'gnss' and 'LatAccel' in ch and 'LongAccel' in ch:
        return (smooth(s.t, ch['LatAccel'], IMU_SMOOTH_S), smooth(s.t, ch['LongAccel'], IMU_SMOOTH_S), 'imu')
    if LAT_NAME in ch and LONG_NAME in ch:
        return np.asarray(ch[LAT_NAME], float), np.asarray(ch[LONG_NAME], float), 'gnss'
    if 'LatAccel' in ch and 'LongAccel' in ch:
        return (smooth(s.t, ch['LatAccel'], IMU_SMOOTH_S), smooth(s.t, ch['LongAccel'], IMU_SMOOTH_S), 'imu')
    return None


def envelope(lat, lon_g, moving=None):
    """向きごと（ENV_STEP_DEG 刻み。0°＝右、90°＝加速）の合成Gの ENV_Q 百分位。点の少ない向きは両隣から補う。
    返す値：長さ 360/ENV_STEP_DEG の配列（作れなければ None）"""
    lat, lon_g = np.asarray(lat, float), np.asarray(lon_g, float)
    ok = np.isfinite(lat) & np.isfinite(lon_g)
    if moving is not None:
        ok &= moving
    n = 360 // ENV_STEP_DEG
    if ok.sum() < ENV_MIN_POINTS * 3:
        return None
    a = np.degrees(np.arctan2(lon_g[ok], lat[ok])) % 360
    r = np.hypot(lat[ok], lon_g[ok])
    b = np.minimum((a // ENV_STEP_DEG).astype(int), n - 1)
    env = np.full(n, np.nan)
    for k in range(n):
        rk = r[b == k]
        if len(rk) >= ENV_MIN_POINTS:
            env[k] = float(np.percentile(rk, ENV_Q))
    good = np.flatnonzero(np.isfinite(env))
    if not len(good):
        return None
    idx = np.arange(n)
    env = np.interp(idx, good, env[good], period=n)
    return env


def env_at(env, lat, lon_g):
    """点ごとの、その向きの包絡の値（向きの真ん中どうしを線でつなぐ）"""
    n = len(env)
    a = np.degrees(np.arctan2(lon_g, lat)) % 360
    pos = a / (360 / n) - 0.5
    return np.interp(pos, np.arange(n), env, period=n)


def attach(s):
    """走行 s に、測位から計算した横G・前後G・旋回半径と、摩擦円の使い方（%）のチャンネルを足す（もうある名前は足さない）。
    s.meta['channels'] にも見出しを足す（derived: 'gnss'。センサーの値ではない印）。返す値：from_gnss の結果（作れなければ None）"""
    dyn = from_gnss(s.gnss)
    metas = s.meta.setdefault('channels', [])
    if dyn is not None:
        near = _near(s.t, dyn['t'])
        for name, key, unit, label in DERIVED:
            if name in s.channels:
                continue
            s.channels[name] = _on_rows(s.t, dyn['t'], dyn[key], near)
            metas.append({'name': name, 'unit': unit, 'kind': 'number', 'label': label, 'derived': 'gnss'})
    src = g_source(s)
    if src is not None and GRIP_NAME not in s.channels:
        lat, lon_g, how = src
        speed = _speed_rows(s)
        moving = speed >= MOVING_KMH if speed is not None else None
        env = envelope(lat, lon_g, moving)
        if env is not None:
            with np.errstate(invalid='ignore', divide='ignore'):
                use = np.hypot(lat, lon_g) / env_at(env, lat, lon_g) * 100.0
            if moving is not None:
                use[~moving] = np.nan
            s.channels[GRIP_NAME] = use
            metas.append({'name': GRIP_NAME, 'unit': '%', 'kind': 'number', 'derived': 'grip', 'source': how,
                          'label': '摩擦円の使い方' + ('（IMU）' if how == 'imu' else '（測位から計算）')})
    return dyn


def _speed_rows(s):
    """記録の行ごとの速さ（km/h）。Speed のチャンネル、無ければ測位の速さ"""
    if 'Speed' in s.channels:
        return np.nan_to_num(np.asarray(s.channels['Speed'], float), nan=0.0)
    g = s.gnss
    if g is not None and 'speed_kmh' in g:
        gt, gv = np.asarray(g['t'], float), np.asarray(g['speed_kmh'], float)
        ok = np.isfinite(gt) & np.isfinite(gv)
        if ok.sum() >= 2:
            return np.interp(s.t, gt[ok], gv[ok])
    return None
