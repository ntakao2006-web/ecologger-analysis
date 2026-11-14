"""入っている依存（requirements.txt から辿れるもの全部）のライセンスを一覧にし、決まりに合うか確かめる。

決まり（README「ライセンス」）：依存は MIT・BSD・Apache・ISC・PSF のどれかだけ。GPL・LGPL・AGPL は入れない。
それ以外・読めないものは「要確認」として止める（人が中身を見て、OKなら ALLOW_MANUAL に理由つきで足す）。

    python tools/check_licenses.py           一覧を出す。決まりに合わなければ終わりの番号が1
    python tools/check_licenses.py --md      THIRD_PARTY.md の表の形で出す
"""
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OK = re.compile(r'\b(MIT|BSD|Apache|ISC|PSF|Python Software Foundation|0BSD|Unlicense)\b', re.I)
NG = re.compile(r'\b(A?GPL|LGPL|General Public License)\b', re.I)
# 自動で読めないが、中身を見て決まりに合うと確かめたもの：{名前: 'ライセンス（確かめ方）'}
ALLOW_MANUAL = {}


def deps_of(name):
    try:
        reqs = metadata.requires(name) or []
    except metadata.PackageNotFoundError:
        return []
    out = []
    for r in reqs:
        if 'extra ==' in r:
            continue
        dep = re.split(r'[\s;<>=!~\[(]', r, maxsplit=1)[0]
        if ';' in r and not installed(dep):     # 条件つき（ほかのOS・古いPython向け）で、ここでは入っていないもの
            continue
        out.append(dep)
    return out


def installed(name):
    try:
        metadata.distribution(name)
        return True
    except metadata.PackageNotFoundError:
        return False


def walk(roots):
    seen, todo = {}, list(roots)
    while todo:
        n = todo.pop()
        key = n.lower().replace('-', '_')
        if key in seen:
            continue
        try:
            d = metadata.distribution(n)
        except metadata.PackageNotFoundError:
            seen[key] = None
            continue
        seen[key] = d
        todo += deps_of(n)
    return seen


def license_of(d):
    m = d.metadata
    lic = (m.get('License-Expression') or '').strip()
    if not lic:
        raw = (m.get('License') or '').strip()
        if raw and len(raw) < 80 and '\n' not in raw:
            lic = raw
    cls = [c.split('::')[-1].strip() for c in (m.get_all('Classifier') or []) if c.startswith('License ::')]
    if not lic and not cls:      # 表示が無いときは、同梱のライセンスのファイルの頭を読む
        for f in d.files or []:
            if 'licen' in str(f).lower() or 'copying' in str(f).lower():
                try:
                    head = d.locate_file(f).read_text(encoding='utf-8', errors='replace').strip().splitlines()[0]
                except (OSError, IndexError):
                    continue
                lic = f'{head}（{f.name} の頭）'
                break
    return lic, cls


def roots_from_requirements():
    out = []
    for line in (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            out.append(re.split(r'[\s;<>=!~\[(]', line, maxsplit=1)[0])
    return out


def check():
    rows, bad = [], False
    for key, d in sorted(walk(roots_from_requirements()).items()):
        if d is None:
            rows.append((key, '-', '入っていない', 'NG'))
            bad = True
            continue
        name, ver = d.metadata['Name'], d.version
        lic, cls = license_of(d)
        text = ' / '.join(x for x in [lic] + cls if x)
        norm = name.lower().replace('-', '_')
        if NG.search(text):
            verdict = 'NG'
        elif OK.search(text):
            verdict = 'OK'
        elif norm in ALLOW_MANUAL:
            text, verdict = ALLOW_MANUAL[norm], 'OK'
        else:
            verdict = '要確認'
        bad |= verdict != 'OK'
        rows.append((name, ver, text or '（表示なし）', verdict))
    return rows, bad


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    argv = sys.argv[1:] if argv is None else argv
    rows, bad = check()
    if '--md' in argv:
        print('| 名前 | 版 | ライセンス | 判定 |\n|---|---|---|---|')
        for r in rows:
            print('| ' + ' | '.join(r) + ' |')
    else:
        for r in rows:
            print(f'{r[3]:4} {r[0]} {r[1]}  {r[2]}')
        print('決まりに合いません' if bad else 'すべて決まりに合います')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
