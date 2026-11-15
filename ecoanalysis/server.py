"""画面（web/）と裏の処理をつなぐローカルのHTTPサーバー。

画面とPythonのやりとりはここを通すだけにして、pywebview の独自の呼び出し（js_api）には頼らない。
こうしておくと、あとで Electron や Tauri へ移すときも、画面のコードをそのまま使える。
外からつながらないよう、127.0.0.1 だけで待つ。
ほかのWebページが裏からこのサーバーを呼ばないよう、/api/ は決まった見出し（X-EcoAnalysis）が付いた
呼び出しだけを受ける（ほかのページからこの見出しを付けて呼ぶと、ブラウザが先に問い合わせて止める）。
宛先の名前（Host）も 127.0.0.1・localhost だけを受ける。
"""
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from . import __version__, dynamics, formula, laps, lines, outline, overlay, sections, session, shifts, tiles

WEB = Path(__file__).resolve().parent.parent / 'web'
HOME = Path.home() / '.ecologger-analysis'
API_HEADER = 'X-EcoAnalysis'
MAX_BODY = 1 << 20
RECENT_KEEP = 10
LAST_KEEP, LAST_LAPS = 20, 12   # 前回の続きに覚える走行・周の数（開いておける数・重ねられる周の数より多め）
CHUNK = 1 << 20
MAX_RANGE = 8 << 20     # 途中から読む頼みに一度で返す上限。終わりを書かない頼み（bytes=N-）に残り全部を流し続けると、
                        # 画面がつながりを握ったまま読まなくなり、早送り・巻き戻しが止まる（Chromiumで確かめた）
MEDIA = re.compile(r'^/media/(s\d+)/(\d+)$')
TILE = re.compile(r'^/tiles/gsi/(\d{1,2})/(\d{1,6})/(\d{1,6})\.jpg$')
RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')
mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('video/mp4', '.mp4')


def _stamp(path):
    """走行の session.json の更新時刻（書き出し直されたかを見る）"""
    try:
        return (Path(path) / 'session.json').stat().st_mtime_ns
    except OSError:
        return None


class Store:
    """開いている走行（id → 走行と、アプリで切った周）"""

    def __init__(self, home=None):
        self.home = Path(HOME if home is None else home)     # 呼ぶときに読む（試験が HOME を差し替えられるように）
        self.lock = threading.Lock()
        self.items = {}
        self.next_id = 1
        self.picker = None          # フォルダーを選ぶ窓（app.py が pywebview の窓を渡す）
        self.raiser = None          # 窓を前に出す（同じく）
        self.rev = 0                # 開いている走行・周が変わるたびに増やす（画面はこれを見て読み直す）
        self.notice = None          # 画面に出す知らせ {id, text, problems}（ほかから渡された走行を開けなかったときなど）
        self.mode, self.mode_note = 'window', None   # 'browser'：いつものブラウザで開いている
        self.last_seen = None       # 画面から最後に知らせがあった時刻
        self.lines = lines.Lines(self.home / 'lines.json')
        self.shifts = shifts.Shifts(self.home / 'shifts.json')    # 走行ごとの線の平行移動（SP-10-17）
        self.formulas = formula.Formulas(self.home / 'channels.json')
        self.tiles = tiles.Tiles(self.home)
        self.outlines = outline.Outlines(self.home, self.tiles)

    @property
    def cache(self):
        return self.home / 'cache'

    def open(self, path):
        path = Path(path).expanduser()
        if path.name == 'session.json':
            path = path.parent
        key = str(path.resolve())
        stamp = _stamp(path)
        old = None
        with self.lock:
            for sid, it in self.items.items():
                if it['key'] == key:
                    if it.get('stamp') == stamp:
                        return summary(sid, it)
                    old = sid                # 書き出し直された走行：同じ番号のまま読み直す
        t = time.perf_counter()
        s = session.load(path, self.cache)
        it = {'key': key, 'session': s, 'load_s': None, 'stamp': stamp,
              'dyn': dynamics.attach(s)}      # 測位から計算するチャンネル（横G・前後G・旋回半径・摩擦円の使い方。SP-10-14）
        self._cut(it)
        it['load_s'] = round(time.perf_counter() - t, 2)
        with self.lock:
            if old and old in self.items:
                sid = old
            else:
                sid = f's{self.next_id}'
                self.next_id += 1
            self.items[sid] = it
            self.rev += 1
        self.remember(key)
        return summary(sid, it)

    def _cut(self, it):
        """そのコースで使っている線の版（無ければ書き出しの線）と区間の線で周を切り、区間ごとの数と連続ベストを出す"""
        s = it['session']
        course = (s.meta.get('course') or {}).get('name')
        act = self.lines.active(course) if course else None
        it['line_names'] = self.lines.names(course) if course else []
        splits = self.lines.splits(course) if course else []
        it['split_names'] = self.lines.split_names(course) if course and splits else []
        if act:
            name, line, sectors = act
            it['laps'], it['line'], it['why'] = laps.for_session(s, line, sectors, splits)
            it['sectors'], it['line_name'] = sectors, name
        else:
            it['laps'], it['line'], it['why'] = laps.for_session(s, splits=splits)
            it['sectors'], it['line_name'] = (s.meta.get('course') or {}).get('sectors') or [], None
        it['splits'], it['rolling'] = (splits if it['laps'] is not None else []), None
        if it['laps'] and splits:
            if 'dyn' not in it:
                it['dyn'] = dynamics.from_gnss(s.gnss)
            sections.metrics(s, it['laps'], it['dyn'])
            it['rolling'] = sections.rolling(s.gnss, it['line'], splits)

    def _recut_course(self, course):
        with self.lock:
            for it in self.items.values():
                if (it['session'].meta.get('course') or {}).get('name') == course:
                    self._cut(it)
            self.rev += 1

    def sections_auto(self, sid, lap_no):
        """区間の線を、走行 sid の lap_no 周目（コースの線で切った周）のブレーキの踏み始めに置く。同じコースの走行を切り直す"""
        with self.lock:
            it = self.items.get(sid)
            if it is None:
                raise KeyError('開いていない走行')
            course = (it['session'].meta.get('course') or {}).get('name')
            lap = next((x for x in it['laps'] or [] if x['lap'] == lap_no), None)
        if not course:
            raise ValueError('コースの名前が分からない走行では、区間の線を残せません')
        if lap is None:
            raise ValueError(f'コースの線で切った周に {lap_no}周目がありません')
        found = sections.auto(it['session'], lap)
        self.lines.set_splits(course, found['splits'])
        self._recut_course(course)
        return {'how': found['how'], 'at_m': found['at_m'], 'count': len(found['splits'])}

    def set_splits(self, course, splits, names=None):
        self.lines.set_splits(course, splits, names)
        self._recut_course(course)

    def set_lines(self, action, course, name=None, line=None, sectors=None):
        """線の版を残す（save）・使う（use、name=None なら書き出しの線）・消す（delete）。同じコースの走行の周を切り直す"""
        if action == 'save':
            self.lines.save(course, name, line, sectors)
        elif action == 'use':
            self.lines.use(course, name)
        elif action == 'delete':
            self.lines.delete(course, name)
        else:
            raise lines.LineError('知らない頼み')
        self._recut_course(course)
        return self.list()

    def close(self, sid):
        with self.lock:
            gone = self.items.pop(sid, None) is not None
            if gone:
                self.rev += 1
            return gone

    def notify(self, text, problems=None):
        with self.lock:
            self.notice = {'id': (self.notice or {}).get('id', 0) + 1, 'text': text, 'problems': list(problems or [])}
            self.rev += 1

    def open_many(self, paths):
        """走行を続けて開く。開けないものは知らせに残す（ほかのアプリから渡されたとき・起動のときに使う）"""
        for p in paths:
            try:
                self.open(p)
            except session.SessionError as e:
                self.notify(f'{Path(p).name} を開けません', e.problems)
            except OSError as e:
                self.notify(f'{Path(p).name} を開けません（{e}）')

    def health(self):
        self.last_seen = time.time()
        with self.lock:
            return {'ok': True, 'app': 'ecologger-analysis', 'version': __version__, 'rev': self.rev,
                    'mode': self.mode, 'mode_note': self.mode_note, 'notice': self.notice}

    def _items(self, picks, mode):
        """picks：[{id, lap}] → overlay.compare の items（走行の平行移動つき）。周が見つからなければ KeyError"""
        items = []
        with self.lock:
            for p in picks:
                it = self.items.get(p.get('id')) if isinstance(p, dict) else None
                if it is None:
                    raise KeyError(f'開いていない走行：{p!r}'[:200])
                use_app = mode == 'app' and it['laps'] is not None
                pool = it['laps'] if use_app else it['session'].laps
                lap = next((x for x in pool if x['lap'] == p.get('lap')), None)
                if lap is None:
                    raise KeyError(f'{it["session"].title} に {p.get("lap")}周目が無い')
                items.append({'key': f'{p["id"]}:{lap["lap"]}', 'label': f'{it["session"].title} {lap["lap"]}周',
                              'session': it['session'], 'lap': lap, 'sid': p['id'], 'shift': self.shifts.get(it['key'])})
        return items

    def overlay(self, picks, mode, channels, ref=None, paint=None, mark='Speed', gsrc=None):
        """picks：[{id, lap}]。mode：'app'（コースの線で切った周）か 'logger'。周が見つからなければ KeyError"""
        items = self._items(picks, mode)
        compiled = self.formulas.compiled()

        def calc(s, rows, names):
            return formula.compute(names, s.channels, s.t, rows, compiled)
        out = overlay.compare(items, channels, ref, calc, paint, mark, gsrc)
        for item, lap in zip(items, out['laps']):
            lap['video'] = lap_videos(item['sid'], item['session'], item['lap'])
            lap['shift'] = list(item['shift'])
        return out

    def _shift_key(self, sid):
        with self.lock:
            it = self.items.get(sid)
        if it is None:
            raise KeyError(f'開いていない走行：{sid!r}'[:200])
        return it['key']

    def set_shift(self, sid, e, n):
        """走行の線を東へ e・北へ n m 動かす（0, 0 で戻す）。ShiftError・KeyError"""
        e, n = self.shifts.set(self._shift_key(sid), e, n)
        with self.lock:
            self.rev += 1
        return {'id': sid, 'shift': [e, n]}

    def auto_shift(self, sid, picks, mode, ref=None):
        """選んだ周のうち走行 sid の周を、基準の周の線へ合わせて覚える（SP-10-17）。ValueError・KeyError"""
        key = self._shift_key(sid)
        items = self._items(picks, mode)
        if ref is None or not 0 <= ref < len(items):
            ref = min(range(len(items)), key=lambda i: items[i]['lap']['time_s']) if items else None
        if ref is None:
            raise ValueError('周を選んでください')
        if items[ref]['sid'] == sid:
            raise ValueError('基準の周の走行は、ほかを合わせる相手なので自動では動かせません（矢印キーなら動かせます）')
        target = [i for i, x in enumerate(items) if x['sid'] == sid]
        if not target:
            raise ValueError('その走行の周を選んでいません')
        r = overlay.align(items, ref, target)
        e, n = self.shifts.set(key, r['e'], r['n'], how='auto')
        with self.lock:
            self.rev += 1
        return {'id': sid, 'shift': [e, n], 'before': r['before'], 'after': r['after'], 'laps': r['laps'], 'from': r['from']}

    def lap_stats(self, sid, mode, channels):
        """周ごとのチャンネルの最大・最小・平均（SP-10-39。周の表の「値」）。
        mode：'app'（コースの線で切った周）か 'logger'。channels：記録のチャンネルか計算チャンネルの名前。
        返す値：{laps: {周の番号: {名前: [最大, 最小, 平均] か None}}, errors: {名前: 計算できない理由}}"""
        with self.lock:
            it = self.items.get(sid)
            if it is None:
                raise KeyError('開いていない走行')
            s = it['session']
            pool = it['laps'] if mode == 'app' and it['laps'] is not None else s.laps
            pool = [dict(x) for x in pool]
        compiled = self.formulas.compiled()
        rec = [c for c in channels if c in s.channels]
        calc = [c for c in channels if c not in s.channels]
        out, errors = {}, {}
        for lap in pool:
            m = (s.t >= lap['start']) & (s.t <= lap['end'])
            vals = {c: np.asarray(s.channels[c], float)[m] for c in rec}
            if calc and m.any():
                got, why = formula.compute(calc, s.channels, s.t, m, compiled)
                vals.update({c: np.asarray(v, float) for c, v in got.items()})
                errors.update(why)
            row = {}
            for c in channels:
                v = vals.get(c)
                v = v[np.isfinite(v)] if v is not None and v.dtype.kind == 'f' else None
                row[c] = None if v is None or not len(v) else [round(float(v.max()), 4), round(float(v.min()), 4), round(float(v.mean()), 4)]
            out[str(lap['lap'])] = row
        for c in channels:
            if c not in s.channels and c not in compiled:
                errors[c] = 'この走行に無いチャンネル'
        return {'laps': out, 'errors': errors}

    # 計算チャンネル
    def _outline_inputs(self, sid):
        """コース図を作るもの：(コース名, 案内の周の lat, lon, 全部の走行の t, lat, lon, 周の数)。
        同じコースで開いている走行を全部まとめて使う：案内はアプリで切った周のいちばん速い周、走った幅は全部の走行の測位
        （周が多いほど、コースの中の線を見分けやすい）"""
        with self.lock:
            it = self.items.get(sid)
            if it is None:
                raise KeyError(f'開いていない走行：{sid!r}'[:200])
            course = (it['session'].meta.get('course') or {}).get('name')
            same = [it] + [x for k, x in self.items.items() if k != sid and course and
                           (x['session'].meta.get('course') or {}).get('name') == course]
            same = [(x['session'], list(x['laps'] or [])) for x in same]
        t_all, la_all, lo_all, best, n_laps = [], [], [], None, 0
        for k, (s, pool) in enumerate(same):
            g = s.gnss
            if not g or not pool:
                continue
            t = np.asarray(g['t'], float)
            b = min(pool, key=lambda x: x['time_s'])
            if best is None or b['time_s'] < best[0]:
                m = (t >= b['start']) & (t <= b['end'])
                best = (b['time_s'], np.asarray(g['lat'], float)[m], np.asarray(g['lon'], float)[m])
            t_all.append(t - np.nanmin(t) + k * 1e7)        # 走行ごとに時刻を離す（通った回を走行をまたいでつなげない）
            la_all.append(np.asarray(g['lat'], float))
            lo_all.append(np.asarray(g['lon'], float))
            n_laps += len(pool)
        if best is None:
            raise outline.OutlineError('周が無いので、コース図を作れません')
        return course, best[1], best[2], np.concatenate(t_all), np.concatenate(la_all), np.concatenate(lo_all), n_laps

    def outline(self, sid, rebuild=False, auto=False):
        """その走行のコースのコース図（道路の縁）。手で直したものがあればそちら。auto=True なら自動のものだけ"""
        course, la, lo, t_all, la_all, lo_all, n = self._outline_inputs(sid)
        return self.outlines.get(course, la, lo, rebuild, t_all, la_all, lo_all, laps=n, auto=auto)

    def outline_save(self, sid, data):
        """手で直したコース図を残す。幅（引いた図で道の帯にする太さ）は自動のものから写す"""
        course, la, lo, *_ = self._outline_inputs(sid)
        auto = self.outlines.cached(self.outlines.key(course, la, lo))
        self.outlines.save_edit(course, la, lo, data, (auto or {}).get('width_m'))
        return self.outline(sid)

    def outline_pit(self, sid, edges=None):
        """ピットロードの線を走行の測位から作る（SP-10-36。手で直したコース図へ足すため。残さない）。
        edges：コースの縁（エディタで直している途中のもの。無ければいまのコース図の、種類がコースの縁の線）"""
        course, la, lo, t_all, la_all, lo_all, _n = self._outline_inputs(sid)
        if edges is None:
            o = self.outline(sid)
            kinds = o.get('kinds') or ['edge'] * len(o['lines'])
            edges = [ln for ln, k in zip(o['lines'], kinds) if k == 'edge']
        else:
            edges = outline.check_edit({'lines': edges})['lines']
        return self.outlines.pit_lane(la, lo, edges, t_all, la_all, lo_all)

    def outline_reset(self, sid):
        """手で直したものを消して、自動のものへ戻す"""
        course, la, lo, *_ = self._outline_inputs(sid)
        self.outlines.clear_edit(course, la, lo)
        return self.outline(sid)

    def channel_names(self):
        """開いている走行の、元のチャンネルの名前"""
        with self.lock:
            return {c for it in self.items.values() for c in it['session'].channels}

    def set_formula(self, action, name, unit=None, expr=None, old=None):
        if action == 'save':
            self.formulas.save(name, unit, expr, old, self.channel_names())
        elif action == 'delete':
            self.formulas.delete(name)
        else:
            raise formula.FormulaError('知らない頼み')
        return self.formulas.list()

    def check_formula(self, expr, name=None):
        """式を確かめ、開いている走行で試しに計算する。{ok, error, pos, refs, missing, preview: {title, min, max, valid}}"""
        try:
            tree = formula.parse(expr)
        except formula.FormulaError as e:
            return {'ok': False, 'error': str(e), 'pos': e.pos}
        compiled = {k: v for k, v in self.formulas.compiled().items() if k != name}
        compiled['[check]'] = (tree, '')      # 計算チャンネルの名前に [ ] は使えないので、ぶつからない
        names = sorted(formula.refs(tree))
        known = self.channel_names() | set(compiled) | set(formula.CONSTS)
        out = {'ok': True, 'refs': names, 'missing': [n for n in names if n not in known], 'preview': None}
        with self.lock:
            first = next(iter(self.items.values()), None)
        if first is not None:
            s = first['session']
            vals, why = formula.compute(['[check]'], s.channels, s.t, slice(None), compiled)
            if why:
                out.update(ok=False, error=why['[check]'], pos=None)
            else:
                v = vals['[check]']
                ok = np.isfinite(v)
                out['preview'] = {'title': s.title, 'valid': round(float(ok.mean()), 3) if len(v) else 0,
                                  'min': float(np.min(v[ok])) if ok.any() else None,
                                  'max': float(np.max(v[ok])) if ok.any() else None}
        return out

    def media(self, sid, idx):
        """開いている走行の idx 本目の動画のファイル。無ければ None"""
        with self.lock:
            it = self.items.get(sid)
        if it is None:
            return None
        s = it['session']
        if not 0 <= idx < len(s.videos):
            return None
        f = session.safe_file(s.path, s.videos[idx].get('file'))
        return f if f and f.is_file() else None

    def list(self):
        """開いている走行の一覧。stats は同じコースの開いている走行どうしの理論ベスト・連続ベスト・平均の周（SP-10-13）"""
        with self.lock:
            out = [summary(sid, it) for sid, it in self.items.items()]
            groups = {}
            for sid, it in self.items.items():
                groups.setdefault((it['session'].meta.get('course') or {}).get('name') or sid, []).append(it)
            stats = {k: sections.summarize([x['laps'] for x in v], [x.get('rolling') for x in v]) for k, v in groups.items()}
        for x in out:
            x['stats'] = stats.get(x['course'] or x['id'])
        return out

    # 最近開いた走行
    def recent(self):
        try:
            paths = json.loads((self.home / 'recent.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        if not isinstance(paths, list):
            return []
        return [p for p in paths if isinstance(p, str) and (Path(p) / 'session.json').is_file()][:RECENT_KEEP]

    def remember(self, key):
        paths = [key] + [p for p in self.recent() if p != key]
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            (self.home / 'recent.json').write_text(json.dumps(paths[:RECENT_KEEP], ensure_ascii=False, indent=1), encoding='utf-8')
        except OSError:
            pass

    # 前回の続き：閉じる前に開いていた走行と選んでいた周。{paths: [走行], laps: [[走行, 周], …]}（画面が変わるたびに書く）
    def last(self):
        try:
            o = json.loads((self.home / 'last.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            o = None
        if not isinstance(o, dict) or not isinstance(o.get('paths'), list):
            return {'paths': [], 'laps': []}
        # もう無い走行（消した・動かした）は落とす
        paths = [p for p in o['paths'] if isinstance(p, str) and (Path(p) / 'session.json').is_file()][:LAST_KEEP]
        laps = [x for x in (o.get('laps') if isinstance(o.get('laps'), list) else [])
                if isinstance(x, list) and len(x) == 2 and x[0] in paths and isinstance(x[1], int) and not isinstance(x[1], bool)]
        return {'paths': paths, 'laps': laps[:LAST_LAPS]}

    def save_last(self, paths, laps):
        """形が違えば ValueError"""
        if not isinstance(paths, list) or not isinstance(laps, list) or not all(isinstance(p, str) for p in paths):
            raise ValueError('paths・laps の形が違う')
        paths = paths[:LAST_KEEP]
        if not all(isinstance(x, list) and len(x) == 2 and x[0] in paths and isinstance(x[1], int) and not isinstance(x[1], bool) for x in laps):
            raise ValueError('laps の形が違う')
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            (self.home / 'last.json').write_text(json.dumps({'paths': paths, 'laps': laps[:LAST_LAPS]}, ensure_ascii=False, indent=1), encoding='utf-8')
        except OSError:
            pass

    def pick_start(self):
        """フォルダーを選ぶ窓を最初に開く所：いちばん最近開いた走行の入っているフォルダー（無ければ None）"""
        for p in self.recent():
            parent = Path(p).parent
            if parent.is_dir():
                return str(parent)
        return None

    def pick(self):
        """フォルダーを選んでもらう（前回開いた走行のフォルダーから）。選ばなければ None"""
        start = self.pick_start()
        if self.picker:
            return self.picker(start)
        return pick_with_tk(start)


def pick_with_tk(start=None):
    """ブラウザで開いているとき（pywebview の窓が無いとき）の、フォルダーを選ぶ窓。別のプロセスで出す。start：最初に開く所。
    exe（PyInstaller）のときは -c が使えないので、exe 自身を --pick-folder で呼び、選んだ所をファイルで受け取る"""
    if getattr(sys, 'frozen', False):
        import tempfile
        fd, out = tempfile.mkstemp(suffix='.txt', prefix='ecoanalysis_pick_')
        os.close(fd)
        try:
            subprocess.run([sys.executable, '--pick-folder', out] + (['--pick-start', start] if start else []), timeout=600)
            return Path(out).read_text(encoding='utf-8').strip() or None
        except (OSError, subprocess.TimeoutExpired):
            return None
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass
    code = ('import sys, tkinter as t, tkinter.filedialog as f\n'
            'r = t.Tk(); r.withdraw(); r.attributes("-topmost", True)\n'
            'print(f.askdirectory(title="走行のフォルダー（.ecorun）を選ぶ", initialdir=(sys.argv[1] if len(sys.argv) > 1 else None)) or "")\n')
    try:
        out = subprocess.run([sys.executable, '-c', code] + ([start] if start else []), capture_output=True, text=True, encoding='utf-8', timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def lap_videos(sid, s, lap):
    """その周に重なる動画。[{url, sync: [[動画の秒, 周の頭からの秒], …], from, to}]（周の頭からの秒の順）。

    sync は周の範囲とその前後1組だけに絞る。from・to は動画がある周の中の範囲（周の頭からの秒）。
    """
    out = []
    for i, v in enumerate(s.videos):
        sync = v['sync']
        ts = [p[1] for p in sync]
        lo, hi = max(lap['start'], ts[0]), min(lap['end'], ts[-1])
        if hi <= lo:
            continue
        a = max(0, next(k for k, t in enumerate(ts) if t >= lo) - 1)
        b = min(len(ts) - 1, next(k for k, t in enumerate(ts) if t >= hi))
        out.append({'url': f'/media/{sid}/{i}', 'from': round(lo - lap['start'], 3), 'to': round(hi - lap['start'], 3),
                    'sync': [[round(sync[k][0], 3), round(sync[k][1] - lap['start'], 3)] for k in range(a, b + 1)]})
    out.sort(key=lambda x: x['from'])
    return out


def formula_help():
    return {'funcs': [{'name': k, 'text': v[5]} for k, v in formula.FUNCS.items()],
            'consts': [{'name': k, 'text': v[0]} for k, v in formula.CONSTS.items()]}


def summary(sid, it):
    s = it['session']
    m = s.meta
    course = m.get('course') or {}
    g = s.gnss
    return {
        'id': sid, 'path': it['key'], 'title': s.title, 't0_utc': m['t0_utc'], 'duration_s': m['duration_s'],
        'clock': m['clock'], 'course': course.get('name'), 'line': it['line'], 'sectors': it['sectors'],
        'line_name': it['line_name'], 'export_line': {'line': course.get('line'), 'sectors': course.get('sectors') or []},
        'line_names': it['line_names'],
        'sector_count': len(it['sectors']), 'splits': it.get('splits') or [], 'split_names': it.get('split_names') or [],
        'rolling': it.get('rolling'),
        'channels': m['channels'], 'rows': int(len(s.t)), 'rate_hz': m.get('rate_hz'),
        'gnss': None if g is None else {**(m.get('gnss') or {}), 'points': int(len(g['t']))},
        'videos': len(s.videos), 'notes': m.get('notes') or [], 'load_s': it['load_s'],
        'laps': {'app': it['laps'], 'app_reason': it['why'],
                 'logger': [{k: x.get(k) for k in ('lap', 'start', 'end', 'time_s', 'sectors')} for x in s.laps]},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = 'EcoLoggerAnalysis/' + __version__
    store = None

    def log_message(self, fmt, *args):      # 端末を静かに保つ
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def _host_ok(self):
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0].strip('[]').lower()
        return host in ('127.0.0.1', 'localhost')

    def _api_ok(self):
        if not self._host_ok() or self.headers.get(API_HEADER) != '1':
            self._json({'ok': False, 'error': 'forbidden'}, 403)
            return False
        return True

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            raise ValueError('大きすぎる')
        raw = self.rfile.read(n) if n else b'{}'
        obj = json.loads(raw.decode('utf-8'))
        if not isinstance(obj, dict):
            raise ValueError('{…} の形でない')
        return obj

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith('/api/'):
            if not self._api_ok():
                return
            if path == '/api/health':
                return self._json(self.store.health())
            if path == '/api/sessions':
                return self._json({'ok': True, 'sessions': self.store.list()})
            if path == '/api/recent':
                return self._json({'ok': True, 'recent': self.store.recent()})
            if path == '/api/last':
                return self._json({'ok': True, 'last': self.store.last()})
            if path == '/api/formulas':
                return self._json({'ok': True, 'formulas': self.store.formulas.list(), 'help': formula_help()})
            return self._json({'ok': False, 'error': 'not found'}, 404)
        if not self._host_ok():
            return self._send(403, b'forbidden', 'text/plain; charset=utf-8')
        m = MEDIA.match(path)
        if m:
            return self._media(m.group(1), int(m.group(2)))
        m = TILE.match(path)
        if m:
            return self._tile(*(int(v) for v in m.groups()))
        rel = 'index.html' if path in ('', '/') else path.lstrip('/')
        f = (WEB / rel).resolve()
        if WEB not in f.parents or not f.is_file():      # web/ の外は出さない
            return self._send(404, b'not found', 'text/plain; charset=utf-8')
        ctype = mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        self._send(200, f.read_bytes(), ctype)

    def _tile(self, z, x, y):
        """航空写真のタイル。<img> は見出しを付けられないので、Host だけ見る（tiles.py）"""
        state, body = self.store.tiles.get(z, x, y)
        if state != 'ok':
            code = {'bad': 400, 'none': 404}.get(state, 503)
            return self._send(code, state.encode('ascii'), 'text/plain; charset=utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'max-age=86400')
        self.end_headers()
        self.wfile.write(body)

    def _media(self, sid, idx):
        """動画を渡す。<video> は見出しを付けられないので、Host だけ見る。開いている走行の動画だけを出す。
        途中から読む頼み（Range）に答える（早送り・巻き戻しに要る）"""
        f = self.store.media(sid, idx)
        if f is None:
            return self._send(404, b'not found', 'text/plain; charset=utf-8')
        size = f.stat().st_size
        a, b, code = 0, size - 1, 200
        rng = self.headers.get('Range')
        if rng:
            m = RANGE.match(rng.strip())
            if not m or (not m.group(1) and not m.group(2)):
                return self._unsatisfiable(size)
            if m.group(1):
                a = int(m.group(1))
                b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:                                   # bytes=-N：最後の N バイト
                a = max(0, size - int(m.group(2)))
            if a >= size or a > b:
                return self._unsatisfiable(size)
            b = min(b, a + MAX_RANGE - 1)           # 頼まれたより短く返してよい（画面は続きをまた頼む）
            code = 206
        self.send_response(code)
        self.send_header('Content-Type', mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(b - a + 1))
        if code == 206:
            self.send_header('Content-Range', f'bytes {a}-{b}/{size}')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            with open(f, 'rb') as fh:
                fh.seek(a)
                left = b - a + 1
                while left > 0:
                    chunk = fh.read(min(CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                    # 早送りで、画面が途中で読むのをやめた

    def _unsatisfiable(self, size):
        self.send_response(416)
        self.send_header('Content-Range', f'bytes */{size}')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body, err = self._body(), None     # 断るときも先に読み切る（読まずに返すと、つながりが切れることがある）
        except (ValueError, UnicodeDecodeError) as e:
            body, err = None, e
        if not self._api_ok():
            return
        if err is not None:
            return self._json({'ok': False, 'error': f'送られた中身が読めない（{err}）'}, 400)
        if path == '/api/open':
            p = body.get('path')
            if not isinstance(p, str) or not p.strip():
                return self._json({'ok': False, 'error': 'path が無い'}, 400)
            try:
                s = self.store.open(p.strip().strip('"'))
                return self._json({'ok': True, 'session': s, 'rev': self.store.rev})
            except session.SessionError as e:
                return self._json({'ok': False, 'error': '走行データを開けません', 'problems': e.problems}, 400)
            except OSError as e:
                return self._json({'ok': False, 'error': f'開けません（{e}）'}, 400)
        if path == '/api/overlay':
            picks, chans, ref = body.get('laps'), body.get('channels') or [], body.get('ref')
            paint, mark = body.get('paint') or None, body.get('mark') or 'Speed'      # コース図の塗り（SP-10-12）
            gsrc = body.get('gsrc') or None                                           # 摩擦円（SP-10-14）
            if gsrc not in (None, 'imu', 'gnss'):
                return self._json({'ok': False, 'error': 'gsrc は imu か gnss'}, 400)
            if not isinstance(picks, list) or not all(isinstance(c, str) for c in chans) or not (ref is None or isinstance(ref, int)) \
                    or not (paint is None or isinstance(paint, str) and len(paint) <= 100) or not (isinstance(mark, str) and len(mark) <= 100):
                return self._json({'ok': False, 'error': 'laps・channels・ref・paint・mark の形が違う'}, 400)
            try:
                return self._json({'ok': True, **self.store.overlay(picks[:12], body.get('mode', 'app'), chans[:12], ref,
                                                                    paint, mark, gsrc)})
            except KeyError as e:
                return self._json({'ok': False, 'error': str(e.args[0])}, 400)
        if path == '/api/outline/snap':          # なぞった線を縁へ吸い付かせる（SP-10-34）。走行の中身は使わない
            pts, inside, reach = body.get('pts'), body.get('inside', 0), body.get('reach')
            if inside not in (-1, 0, 1) or not (reach is None or isinstance(reach, (int, float)) and not isinstance(reach, bool)):
                return self._json({'ok': False, 'error': 'inside・reach の形が違う'}, 400)
            try:
                return self._json({'ok': True, **self.store.outlines.snap(pts, inside, None, reach)})
            except (outline.OutlineError, TypeError, ValueError) as e:
                return self._json({'ok': False, 'error': str(e) if isinstance(e, outline.OutlineError) else 'なぞった線の形が違う'}, 400)
        if path == '/api/outline/pit':           # ピットロードを測位から作る（SP-10-36）。手で直したコース図へ足す
            sid, edges = body.get('id'), body.get('edges')
            if not isinstance(sid, str) or not (edges is None or isinstance(edges, list)):
                return self._json({'ok': False, 'error': 'id・edges の形が違う'}, 400)
            try:
                return self._json({'ok': True, 'pit': self.store.outline_pit(sid, edges)})
            except KeyError as e:
                return self._json({'ok': False, 'error': str(e.args[0])}, 400)
            except outline.OutlineError as e:
                return self._json({'ok': False, 'error': str(e)}, 400)
        if path in ('/api/outline', '/api/outline/save', '/api/outline/reset'):
            sid, rebuild = body.get('id'), bool(body.get('rebuild'))
            if not isinstance(sid, str):
                return self._json({'ok': False, 'error': 'id が無い'}, 400)
            try:
                if path == '/api/outline/save':
                    o = self.store.outline_save(sid, body.get('outline'))
                elif path == '/api/outline/reset':
                    o = self.store.outline_reset(sid)
                else:
                    o = self.store.outline(sid, rebuild, bool(body.get('auto')))
                return self._json({'ok': True, 'outline': o})
            except KeyError as e:
                return self._json({'ok': False, 'error': str(e.args[0])}, 400)
            except outline.OutlineError as e:
                return self._json({'ok': False, 'error': str(e)}, 400)
            except OSError as e:
                return self._json({'ok': False, 'error': f'残せませんでした（{e.strerror or e}）'}, 500)
        if path == '/api/lapstats':              # 周ごとのチャンネルの最大・最小・平均（SP-10-39）
            sid, mode, chans = body.get('id'), body.get('mode', 'app'), body.get('channels')
            if not isinstance(sid, str) or mode not in ('app', 'logger') or not isinstance(chans, list) \
                    or len(chans) > 24 or not all(isinstance(c, str) and len(c) <= 100 for c in chans):
                return self._json({'ok': False, 'error': 'id・mode・channels の形が違う'}, 400)
            try:
                return self._json({'ok': True, **self.store.lap_stats(sid, mode, chans)})
            except KeyError as e:
                return self._json({'ok': False, 'error': str(e.args[0])}, 400)
        if path == '/api/sections/auto':         # 区間の線を基準の周のブレーキの踏み始めに置く（SP-10-13）
            sid, lap = body.get('id'), body.get('lap')
            if not isinstance(sid, str) or not isinstance(lap, int) or isinstance(lap, bool):
                return self._json({'ok': False, 'error': 'id・lap の形が違う'}, 400)
            try:
                found = self.store.sections_auto(sid, lap)
            except (KeyError, ValueError, lines.LineError) as e:
                return self._json({'ok': False, 'error': str(e.args[0])}, 400)
            return self._json({'ok': True, 'auto': found, 'sessions': self.store.list(), 'rev': self.store.rev})
        if path == '/api/sections/set':          # 区間の線と名前を入れ替える（[] で消す。SP-10-37 の直し）
            try:
                self.store.set_splits(body.get('course'), body.get('splits'), body.get('names'))
            except lines.LineError as e:
                return self._json({'ok': False, 'error': str(e)}, 400)
            return self._json({'ok': True, 'sessions': self.store.list(), 'rev': self.store.rev})
        if path in ('/api/shift/set', '/api/shift/auto'):   # 走行の線の平行移動（SP-10-17）
            try:
                if path == '/api/shift/set':
                    r = self.store.set_shift(body.get('id'), body.get('e'), body.get('n'))
                else:
                    picks = body.get('laps')
                    if not isinstance(picks, list) or not picks:
                        return self._json({'ok': False, 'error': '周を選んでください'}, 400)
                    ref = body.get('ref')
                    r = self.store.auto_shift(body.get('id'), picks[:12], body.get('mode', 'app'),
                                              ref if isinstance(ref, int) and not isinstance(ref, bool) else None)
            except KeyError as e:
                return self._json({'ok': False, 'error': str(e.args[0] if e.args else e)}, 404)
            except (shifts.ShiftError, ValueError) as e:
                return self._json({'ok': False, 'error': str(e)}, 400)
            return self._json({'ok': True, **r, 'rev': self.store.rev})
        if path.startswith('/api/lines/'):
            try:
                sessions = self.store.set_lines(path.rsplit('/', 1)[1], body.get('course'), body.get('name'),
                                                body.get('line'), body.get('sectors'))
            except lines.LineError as e:
                return self._json({'ok': False, 'error': str(e)}, 400)
            return self._json({'ok': True, 'sessions': sessions, 'rev': self.store.rev})
        if path == '/api/formulas/check':
            expr = body.get('expr')
            if not isinstance(expr, str):
                return self._json({'ok': False, 'error': 'expr が無い'}, 400)
            return self._json(self.store.check_formula(expr, body.get('name')))
        if path.startswith('/api/formulas/'):
            try:
                items = self.store.set_formula(path.rsplit('/', 1)[1], body.get('name'), body.get('unit'),
                                               body.get('expr'), body.get('old'))
            except formula.FormulaError as e:
                return self._json({'ok': False, 'error': str(e), 'pos': e.pos}, 400)
            return self._json({'ok': True, 'formulas': items})
        if path == '/api/close':
            return self._json({'ok': self.store.close(body.get('id')), 'rev': self.store.rev})
        if path == '/api/handover':
            paths = body.get('paths')
            if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
                return self._json({'ok': False, 'error': 'paths の形が違う'}, 400)
            self.store.open_many(paths[:20])
            if self.store.raiser:
                self.store.raiser()
            return self._json({'ok': True})
        if path == '/api/pick':
            return self._json({'ok': True, 'path': self.store.pick()})
        if path == '/api/last':
            try:
                self.store.save_last(body.get('paths'), body.get('laps'))
            except ValueError as e:
                return self._json({'ok': False, 'error': str(e)}, 400)
            return self._json({'ok': True})
        return self._json({'ok': False, 'error': 'not found'}, 404)


def start(port=0, store=None):
    """裏でサーバーを立て、(server, url) を返す。port=0 なら空いている番号を使う"""
    handler = type('BoundHandler', (Handler,), {'store': store or Store()})
    srv = ThreadingHTTPServer(('127.0.0.1', port), handler)
    srv.daemon_threads = True
    srv.store = handler.store
    threading.Thread(target=srv.serve_forever, name='http', daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}/'
