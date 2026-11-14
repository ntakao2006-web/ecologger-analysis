"""作りものの走行データ（ecorun 形式）を書く。試験と、本物の記録が無いときの画面づくりに使う。

    python -m ecoanalysis.sample <出力のフォルダー>.ecorun

楕円のコースを周ごとに少しずつ速さを変えて走る。位置は架空（どこのコースでもない）。動画は入れない。
"""
import gzip
import json
import math
import sys
from pathlib import Path

CENTER = (36.0, 140.0)     # 架空の場所
RX, RY = 400.0, 150.0      # 楕円の半径（m）


def _xy_to_latlon(x, y):
    lat = CENTER[0] + y / 111320.0
    lon = CENTER[1] + x / (111320.0 * math.cos(math.radians(CENTER[0])))
    return lat, lon


def write_sample(folder, laps=3, rate=20.0, lap_s=(62.0, 60.5, 61.2, 59.8, 60.9)):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    lead = 3.0
    times = [lap_s[i % len(lap_s)] for i in range(laps)]
    total = lead + sum(times) + 3.0

    def where(t):
        """t のときの (周の中の割合の通算, 速さの倍率)"""
        if t < lead:
            return 0.0, 1.0
        at = lead
        for i, s in enumerate(times):
            if t < at + s:
                return i + (t - at) / s, 60.0 / s
            at += s
        return laps + (t - at) / times[-1], 60.0 / times[-1]

    perim = 2 * math.pi * math.sqrt((RX * RX + RY * RY) / 2)
    ch_rows, g_rows = [], []
    n = int(total * rate)
    for k in range(n + 1):
        t = k / rate
        u, f = where(t)
        a = 2 * math.pi * u
        speed = perim / 60.0 * f * 3.6 * (0.8 + 0.2 * abs(math.cos(a)))
        lat_g = (speed / 3.6) ** 2 / 300.0 / 9.81 * math.sin(2 * a)
        ch_rows.append(f'{t:.3f},{speed:.2f},{3000 + 3000 * abs(math.cos(a)):.0f},{100 * abs(math.cos(a)):.1f},'
                       f'{0.3 * math.sin(a):.4f},{lat_g:.4f},{88 + t / 60:.1f},{int(abs(math.sin(a)) > 0.98)}')
    for k in range(int(total * 10) + 1):
        t = k / 10
        u, f = where(t)
        a = 2 * math.pi * u
        lat, lon = _xy_to_latlon(RX * math.sin(a), RY * math.cos(a))
        head = (math.degrees(math.atan2(RX * math.cos(a), -RY * math.sin(a))) + 360) % 360
        g_rows.append(f'{t:.3f},{lat:.8f},{lon:.8f},50.0,{perim / 60 * f * 3.6:.2f},{head:.2f},14,0.8')
    channels = [('Speed', 'km/h', 'number', '車速'), ('RPM', 'rpm', 'number', '回転数'), ('Throttle', '%', 'number', 'アクセル'),
                ('LongAccel', 'g', 'number', '前後G'), ('LatAccel', 'g', 'number', '横G'), ('WaterTemp', '°C', 'number', '水温'),
                ('ClutchDisengaged', '', 'state', 'クラッチを切った')]
    with gzip.open(folder / 'channels.csv.gz', 'wt', encoding='utf-8', newline='') as fh:
        fh.write('t,' + ','.join(c[0] for c in channels) + '\n' + '\n'.join(ch_rows) + '\n')
    with gzip.open(folder / 'gnss.csv.gz', 'wt', encoding='utf-8', newline='') as fh:
        fh.write('t,lat,lon,alt_m,speed_kmh,heading_deg,sats,hacc_m\n' + '\n'.join(g_rows) + '\n')
    lap_list, at = [], lead
    for i, s in enumerate(times, 1):
        lap_list.append({'lap': i, 'start': round(at, 3), 'end': round(at + s, 3), 'time_s': s,
                         'sectors': [round(s * 0.3, 3), round(s * 0.4, 3), round(s - round(s * 0.3, 3) - round(s * 0.4, 3), 3)]})
        at += s
    lat0, lon0 = _xy_to_latlon(0.0, RY)
    meta = {
        'format': 'ecorun', 'version': 1, 'created_by': 'ecoanalysis.sample',
        'title': '作りものの走行', 't0_utc': '2026-01-01T00:00:00.000Z', 'duration_s': round(n / rate, 3), 'clock': 'gnss',
        'course': {'name': '架空の楕円コース', 'line': {'lat': lat0, 'lon': lon0, 'bearing': 90.0, 'width_m': 30.0}, 'sectors': []},
        'channels_file': 'channels.csv.gz',
        'channels': [{'name': c[0], 'unit': c[1], 'kind': c[2], 'label': c[3]} for c in channels], 'rate_hz': rate,
        'gnss_file': 'gnss.csv.gz', 'gnss': {'source': 'sample', 'rate_hz': 10},
        'laps': lap_list, 'videos': [], 'notes': ['作りもの（ecoanalysis.sample）'],
    }
    (folder / 'session.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    return folder


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    print(write_sample(sys.argv[1]))
