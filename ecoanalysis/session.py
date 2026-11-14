"""走行データ（ecorun 形式、docs/format.md）を読み、形が決まりどおりかを確かめる。

    s = load('SUGO_20261108_31-33.ecorun')     # 形が違えば SessionError（理由を全部並べる）
    s.t, s.channels['RPM'], s.gnss['lat'], s.laps, s.videos
    video_seconds(s.videos[0]['sync'], t)       # その時刻の動画の秒（無ければ None）
"""
import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

FORMAT = 'ecorun'
VERSIONS = {1}
GNSS_COLUMNS = ['t', 'lat', 'lon', 'alt_m', 'speed_kmh', 'heading_deg', 'sats', 'hacc_m']
NAME = re.compile(r'^[A-Za-z0-9_]+$')
SLACK_S = 1.0     # 周の時刻が、チャンネルの範囲からこれだけ外れるのは許す
CACHE_VERSION = 1  # 覚えた表の形を変えたら上げる
CACHE_KEEP = 20    # 覚えておく表の数（古いものから消す）


class SessionError(Exception):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('走行データを開けません：\n' + '\n'.join(f'- {p}' for p in self.problems))


@dataclass
class Session:
    path: Path
    meta: dict
    t: np.ndarray
    channels: dict = field(default_factory=dict)      # 名前 → 値の配列（無い所は NaN）
    gnss: dict = None                                 # 列 → 配列（測位が無ければ None）
    laps: list = field(default_factory=list)
    videos: list = field(default_factory=list)

    @property
    def title(self):
        return self.meta.get('title') or self.path.stem


# ---------------------------------------------------------------- 小物
def safe_file(root, name):
    """フォルダーの中のファイルだけ。外を指す名前（.. や絶対パス）は None"""
    if not isinstance(name, str) or not name or '\\' in name or name.startswith('/') or ':' in name:
        return None
    root = Path(root).resolve()
    p = (root / name).resolve()
    return p if root in p.parents else None


def parse_utc(text):
    try:
        return datetime.fromisoformat(str(text).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def increasing(values):
    return all(b > a for a, b in zip(values, values[1:]))


def read_table(path, cache=None):
    """CSV（.gz も）→ (見出し, 列ごとの float の配列)。空欄は NaN。
    cache（フォルダー）を渡すと、読んだ表をそこに覚え、ファイルが同じ（場所・大きさ・更新時刻）なら次からそれを使う"""
    path = Path(path)
    key = None
    if cache is not None:
        st = path.stat()
        key = hashlib.sha1(f'{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{CACHE_VERSION}'.encode()).hexdigest()[:20]
        hit = Path(cache) / f'{key}.npz'
        try:
            with np.load(hit, allow_pickle=False) as z:
                header = [str(h) for h in z['header']]
                data = z['data']
            os.utime(hit)          # よく使うものを残す目印
            return header, [data[:, i] for i in range(data.shape[1])]
        except (OSError, KeyError, ValueError):
            pass
    raw = path.read_bytes()
    if path.name.endswith('.gz'):
        raw = gzip.decompress(raw)
    text = raw.decode('utf-8').replace('\r', '')
    head, _, body = text.partition('\n')
    header = next(csv.reader([head]), []) if head else []
    if not header:
        return [], []
    body = '\n' + body.strip('\n')
    if body == '\n':
        data = np.empty((0, len(header)))
    else:
        for _ in range(2):                       # 空欄 → nan（,,, の続きも2回で埋まる）
            body = body.replace(',,', ',nan,')
        body = body.replace('\n,', '\nnan,').replace(',\n', ',nan\n')
        if body.endswith(','):
            body += 'nan'
        try:
            data = np.loadtxt(io.StringIO(body), delimiter=',', ndmin=2, dtype=float)
        except ValueError as e:
            raise ValueError(f'{path.name}：数として読めない所か、列の数が見出しと違う行がある（{e}）'[:300])
        if data.shape[1] != len(header):
            raise ValueError(f'{path.name}：列の数が見出しと違う')
    if key is not None:
        try:
            Path(cache).mkdir(parents=True, exist_ok=True)
            tmp = Path(cache) / f'{key}.tmp.npz'
            np.savez(tmp, header=np.array(header), data=data)
            os.replace(tmp, Path(cache) / f'{key}.npz')
            _prune(Path(cache))
        except OSError:
            pass
    return header, [data[:, i] for i in range(data.shape[1])]


def _prune(cache, keep=CACHE_KEEP):
    files = sorted(cache.glob('*.npz'), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        try:
            f.unlink()
        except OSError:
            pass


def video_seconds(sync, t):
    """t（秒）のときの動画の秒。sync の範囲の外なら None"""
    ts = [p[1] for p in sync]
    if not sync or t < ts[0] or t > ts[-1]:
        return None
    return float(np.interp(t, ts, [p[0] for p in sync]))


def session_time(sync, video_s):
    vs = [p[0] for p in sync]
    if not sync or video_s < vs[0] or video_s > vs[-1]:
        return None
    return float(np.interp(video_s, vs, [p[1] for p in sync]))


# ---------------------------------------------------------------- 確かめる
def check_meta(meta, root):
    """session.json の中身の確かめ（ファイルの中までは見ない）。問題の一覧を返す"""
    p = []
    if not isinstance(meta, dict):
        return ['session.json の中身が {…} の形でない']
    if meta.get('format') != FORMAT:
        p.append(f'format が "{FORMAT}" でない')
    if meta.get('version') not in VERSIONS:
        p.append(f'知らない版（version={meta.get("version")!r}。読めるのは {sorted(VERSIONS)}）')
        return p
    if parse_utc(meta.get('t0_utc')) is None:
        p.append('t0_utc が時刻として読めない')
    if not is_num(meta.get('duration_s')) or meta['duration_s'] < 0:
        p.append('duration_s が0以上の数でない')
    if meta.get('clock') not in ('gnss', 'host'):
        p.append('clock が "gnss" か "host" でない')
    if not safe_file(root, meta.get('channels_file')):
        p.append('channels_file がフォルダーの中のファイルを指していない')
    chans = meta.get('channels')
    if not isinstance(chans, list) or not chans:
        p.append('channels が空か、配列でない')
    else:
        names = []
        for c in chans:
            if not isinstance(c, dict) or not NAME.match(str(c.get('name', ''))):
                p.append(f'チャンネルの名前が英数字と _ でない：{c!r}')
                continue
            names.append(c['name'])
            if not isinstance(c.get('unit'), str):
                p.append(f'{c["name"]}：unit が文字列でない')
            if c.get('kind') not in ('number', 'state'):
                p.append(f'{c["name"]}：kind が "number" か "state" でない')
        if len(set(names)) != len(names):
            p.append('チャンネルの名前が重なっている')
    if 'gnss_file' in meta and not safe_file(root, meta.get('gnss_file')):
        p.append('gnss_file がフォルダーの中のファイルを指していない')
    laps = meta.get('laps')
    if not isinstance(laps, list):
        p.append('laps が配列でない')
    else:
        for x in laps:
            ok = isinstance(x, dict) and isinstance(x.get('lap'), int) and all(is_num(x.get(k)) for k in ('start', 'end', 'time_s'))
            if not ok:
                p.append(f'周の書き方が違う：{x!r}')
                continue
            if not x['start'] < x['end'] or x['time_s'] <= 0:
                p.append(f'{x["lap"]}周目：始まり・終わり・タイムがおかしい')
            if is_num(meta.get('duration_s')) and (x['start'] < -SLACK_S or x['end'] > meta['duration_s'] + SLACK_S):
                p.append(f'{x["lap"]}周目：チャンネルの時間の外にある')
            sec = x.get('sectors', [])
            if not isinstance(sec, list) or not all(s is None or is_num(s) for s in sec):
                p.append(f'{x["lap"]}周目：sectors が数（または null）の配列でない')
    vids = meta.get('videos')
    if not isinstance(vids, list):
        p.append('videos が配列でない')
    else:
        for v in vids:
            f = safe_file(root, v.get('file')) if isinstance(v, dict) else None
            if not f:
                p.append(f'動画のファイルがフォルダーの中を指していない：{v!r}'[:200])
                continue
            if not f.is_file():
                p.append(f'動画のファイルが無い：{v["file"]}')
            sync = v.get('sync')
            if not (isinstance(sync, list) and len(sync) >= 2 and all(isinstance(s, list) and len(s) == 2 and all(map(is_num, s)) for s in sync)):
                p.append(f'{v["file"]}：sync が [動画の秒, t] の組2つ以上でない')
            elif not (increasing([s[0] for s in sync]) and increasing([s[1] for s in sync])):
                p.append(f'{v["file"]}：sync の動画の秒と t が増える一方でない')
    return p


# ---------------------------------------------------------------- 読む
def load(path, cache=None):
    root = Path(path)
    meta_path = root / 'session.json'
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise SessionError([f'{root.name} に session.json が無い'])
    except (OSError, ValueError) as e:
        raise SessionError([f'session.json が読めない（{e}）'])
    problems = check_meta(meta, root)
    if problems:
        raise SessionError(problems)

    names = [c['name'] for c in meta['channels']]
    try:
        header, cols = read_table(safe_file(root, meta['channels_file']), cache)
    except (OSError, ValueError) as e:
        raise SessionError([f'チャンネルのファイルが読めない（{e}）'])
    if header != ['t'] + names:
        problems.append('チャンネルのファイルの見出しが、channels の順と違う')
    elif len(cols[0]) == 0:
        problems.append('チャンネルのファイルに行が無い')
    elif not np.all(np.diff(cols[0]) > 0):
        problems.append('チャンネルの t が増える一方でない')
    gnss = None
    if 'gnss_file' in meta:
        try:
            gh, gc = read_table(safe_file(root, meta['gnss_file']), cache)
        except (OSError, ValueError) as e:
            raise SessionError([f'測位のファイルが読めない（{e}）'])
        if gh != GNSS_COLUMNS:
            problems.append(f'測位のファイルの見出しが {GNSS_COLUMNS} でない')
        elif len(gc[0]) and not np.all(np.diff(gc[0]) > 0):
            problems.append('測位の t が増える一方でない')
        else:
            gnss = dict(zip(gh, gc))
    if problems:
        raise SessionError(problems)
    return Session(path=root, meta=meta, t=cols[0], channels=dict(zip(names, cols[1:])), gnss=gnss,
                   laps=meta['laps'], videos=meta['videos'])
