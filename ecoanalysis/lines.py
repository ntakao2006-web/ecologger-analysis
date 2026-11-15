"""コースの線の版（名前つき）を手元に残す。

走行の書き出しに入っている線（ロガーと同じ線。正はEcoLoggerのコースの設定）とは別に、解析の側で
コントロールライン・セクターの線を動かしたものを、コースごとに名前をつけて残す。その場限りにしない。
どの版を使うか（使わなければ書き出しの線）もコースごとに覚え、同じコースの走行は全部その線で周を切る。

置き場：~/.ecologger-analysis/lines.json
  {"<コースの名前>": {"active": "<版の名前>" | null,
                      "variants": {"<版の名前>": {"line": {...}, "sectors": [{...}], "splits": [{...}], "saved_utc": "..."}},
                      "splits": [{...}]}}
splits は区間（コーナー）の線（SP-10-13）。版を使っているときはその版の splits、使っていなければコースの splits
（その場で置いた分）を使う。前のファイル（splits が無い）は区間の線が無いものとして読む。
"""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .laps import valid_line

MAX_NAME = 40
MAX_SPLITS = 60


class LineError(ValueError):
    pass


def clean_line(x):
    if not valid_line(x):
        raise LineError('線は lat・lon・bearing・width_m の数で書く')
    return {'lat': round(float(x['lat']), 8), 'lon': round(float(x['lon']), 8),
            'bearing': round(float(x['bearing']) % 360, 2), 'width_m': round(float(x['width_m']), 1)}


class Lines:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _read(self):
        try:
            d = json.loads(self.path.read_text(encoding='utf-8'))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, d):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.path)

    def names(self, course):
        c = self._read().get(course) or {}
        return sorted((c.get('variants') or {}).keys())

    def active(self, course):
        """使っている版 (名前, 線, セクターの線)。書き出しの線を使っていれば None"""
        c = self._read().get(course) or {}
        name = c.get('active')
        v = (c.get('variants') or {}).get(name) if name else None
        if not v:
            return None
        try:
            return name, clean_line(v.get('line')), [clean_line(s) for s in v.get('sectors') or []]
        except LineError:
            return None

    def splits(self, course):
        """区間の線。版を使っていればその版の、使っていなければその場で置いた分"""
        c = self._read().get(course) or {}
        name = c.get('active')
        v = (c.get('variants') or {}).get(name) if name else None
        raw = (v or {}).get('splits') if v else c.get('splits')
        try:
            return [clean_line(x) for x in raw or []]
        except LineError:
            return []

    def set_splits(self, course, splits):
        """区間の線を入れ替える（使っている版か、その場で置いた分）"""
        if not isinstance(course, str) or not course:
            raise LineError('コースの名前が分からない走行では、区間の線を残せない')
        if not isinstance(splits, list) or len(splits) > MAX_SPLITS:
            raise LineError(f'区間の線は{MAX_SPLITS}本までの並び')
        clean = [clean_line(x) for x in splits]
        with self.lock:
            d = self._read()
            c = d.setdefault(course, {'active': None, 'variants': {}})
            v = (c.get('variants') or {}).get(c.get('active')) if c.get('active') else None
            (v if v is not None else c)['splits'] = clean
            self._write(d)

    def save(self, course, name, line, sectors, splits=None):
        """版を残して使う。splits を渡さなければ、いま使っている区間の線をそのまま持っていく"""
        if not isinstance(course, str) or not course:
            raise LineError('コースの名前が分からない走行では、線の版を残せない')
        name = name.strip() if isinstance(name, str) else ''
        if not name or len(name) > MAX_NAME:
            raise LineError(f'版の名前は1〜{MAX_NAME}文字')
        keep = self.splits(course) if splits is None else [clean_line(x) for x in splits]
        entry = {'line': clean_line(line), 'sectors': [clean_line(s) for s in (sectors or [])], 'splits': keep,
                 'saved_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
        with self.lock:
            d = self._read()
            c = d.setdefault(course, {'active': None, 'variants': {}})
            c.setdefault('variants', {})[name] = entry
            c['active'] = name
            self._write(d)

    def use(self, course, name):
        with self.lock:
            d = self._read()
            c = d.setdefault(course, {'active': None, 'variants': {}})
            if name is not None and name not in (c.get('variants') or {}):
                raise LineError(f'線の版「{name}」が無い')
            c['active'] = name
            self._write(d)

    def delete(self, course, name):
        with self.lock:
            d = self._read()
            c = d.get(course) or {}
            if name not in (c.get('variants') or {}):
                raise LineError(f'線の版「{name}」が無い')
            del c['variants'][name]
            if c.get('active') == name:
                c['active'] = None
            self._write(d)
