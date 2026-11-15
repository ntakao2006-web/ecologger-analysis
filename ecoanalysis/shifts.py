"""走行ごとの線の平行移動（SP-10-17。仕様 analysis_map_features.md 3-6）を手元に残す。

別の日・別の時間帯の走行を重ねると、測位の長い間のずれで線が平行に 1〜2 m ずれることがある（SUGO 11/7 と 11/8 で
中央 1.4 m）。走行ごとに東・北へ何 m 動かすかを覚え、重ね合わせで線の点に足してから距離・横のずれを計算する。
周の切り方（線を越えた時刻）・コースの線・コース図は動かさない。

置き場：~/.ecologger-analysis/shifts.json
  {"<走行のフォルダーの絶対パス>": {"e": 東へ m, "n": 北へ m, "how": "hand" | "auto", "saved_utc": "..."}}
0 に戻した走行は消す。
"""
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path

MAX_M = 50.0          # これより大きく動かすのは測位のずれではない
STEP_M = 0.01         # 覚える細かさ


class ShiftError(ValueError):
    pass


def clean(e, n):
    try:
        e, n = float(e), float(n)
    except (TypeError, ValueError):
        raise ShiftError('動かす量は東・北の m の数で書く') from None
    if not (math.isfinite(e) and math.isfinite(n)) or abs(e) > MAX_M or abs(n) > MAX_M:
        raise ShiftError(f'動かす量は東・北それぞれ ±{MAX_M:.0f} m まで')
    return round(round(e / STEP_M) * STEP_M, 2) + 0.0, round(round(n / STEP_M) * STEP_M, 2) + 0.0


class Shifts:
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

    def get(self, key):
        """(東 m, 北 m)。覚えていなければ (0, 0)"""
        v = self._read().get(key)
        if not isinstance(v, dict):
            return 0.0, 0.0
        try:
            return clean(v.get('e', 0), v.get('n', 0))
        except ShiftError:
            return 0.0, 0.0

    def how(self, key):
        v = self._read().get(key)
        return v.get('how') if isinstance(v, dict) else None

    def set(self, key, e, n, how='hand'):
        e, n = clean(e, n)
        with self.lock:
            d = self._read()
            if e == 0 and n == 0:
                d.pop(key, None)
            else:
                d[key] = {'e': e, 'n': n, 'how': 'auto' if how == 'auto' else 'hand',
                          'saved_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
            self._write(d)
        return e, n
