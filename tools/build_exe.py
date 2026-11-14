"""配る形を作る：PyInstaller で dist/EcoLogger-Analysis/（exe と部品のフォルダー）と、その zip。

    python tools/build_exe.py            作る（初めは .venv-build を作って部品を入れる）
    python tools/build_exe.py --check    作ったexeを裏で起こし、画面の入口と /api/health が返るかを確かめて閉じる

- PyInstaller はふだんの .venv に入れず、exe を作るときだけの .venv-build に入れる。
  PyInstaller のライセンスは GPL-2.0-or-later だが、exe に入る起動の部分（bootloader）には
  「どんなライセンスのアプリを作って配ってもよい」という例外が付いている。PyInstaller 自身のコードは exe に入らない。
  ふだんの .venv の部品は tools/check_licenses.py の決まり（MIT・BSD・Apache など）のまま保つ。
- 1つのファイルにまとめる形（--onefile）は、起こすたびに一時フォルダーへ広げるので遅く、ウイルス対策に
  止められやすい。フォルダーの形（--onedir）にして zip で配る。
- WebView2 ランタイムは同梱しない（Windows 10/11 にはふつう入っている。無ければブラウザで開く。app.py）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / '.venv-build'
PY = VENV / 'Scripts' / 'python.exe'
NAME = 'EcoLogger-Analysis'
DIST = ROOT / 'dist'
PYINSTALLER = 'pyinstaller>=6.10,<7'


def version():
    text = (ROOT / 'ecoanalysis' / '__init__.py').read_text(encoding='utf-8')
    return text.split("__version__ = '", 1)[1].split("'", 1)[0]


def ensure_venv():
    if not PY.exists():
        print('exe を作る場所（.venv-build）を作ります')
        subprocess.run([sys.executable, '-m', 'venv', str(VENV)], check=True)
    subprocess.run([str(PY), '-m', 'pip', 'install', '-q', '--disable-pip-version-check', '-r', str(ROOT / 'requirements.txt'), PYINSTALLER],
                   check=True)


def build():
    ensure_venv()
    sep = os.pathsep
    args = [str(PY), '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed', '--name', NAME,
            '--distpath', str(DIST), '--workpath', str(ROOT / 'build'), '--specpath', str(ROOT / 'build'),
            '--paths', str(ROOT),
            '--add-data', f'{ROOT / "web"}{sep}web',
            '--collect-submodules', 'ecoanalysis',
            '--exclude-module', 'matplotlib', '--exclude-module', 'pandas', '--exclude-module', 'PyQt5',
            '--exclude-module', 'PySide6', '--exclude-module', 'IPython',
            str(ROOT / 'tools' / 'exe_main.py')]
    t = time.time()
    subprocess.run(args, check=True, cwd=ROOT)
    out = DIST / NAME
    for name in ('LICENSE', 'THIRD_PARTY.md', 'README.md'):
        shutil.copy2(ROOT / name, out / name)
    zpath = DIST / f'{NAME}-{version()}-win64.zip'
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(out.rglob('*')):
            if f.is_file():
                z.write(f, Path(NAME) / f.relative_to(out))
    size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
    print(f'できました（{time.time() - t:.0f}秒）：{out}\\{NAME}.exe（フォルダー {size / 1e6:.0f}MB）')
    print(f'配る zip：{zpath}（{zpath.stat().st_size / 1e6:.0f}MB）')
    return out


def check(timeout=60):
    """作った exe を --browser なしで起こすと窓が出てしまうので、--port を決めて起こし、/api/health を見てから止める"""
    exe = DIST / NAME / f'{NAME}.exe'
    if not exe.exists():
        print('exe がありません。先に python tools/build_exe.py')
        return 1
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    p = subprocess.Popen([str(exe), '--port', str(port)])
    try:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                req = urllib.request.Request(f'http://127.0.0.1:{port}/api/health', headers={'X-EcoAnalysis': '1'})
                with urllib.request.urlopen(req, timeout=2) as r:
                    j = json.loads(r.read())
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as r:
                    page = r.read().decode('utf-8')
                ok = j.get('ok') and 'EcoLogger 解析' in page
                print(f'起動まで {time.time() - t0:.1f}秒：版 {j.get("version")}、表示 {j.get("mode")}、画面 {"OK" if ok else "NG"}')
                return 0 if ok else 1
            except OSError:
                time.sleep(0.5)
        print('時間内に返事が無い')
        return 1
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
        # 止めたので exe は片づけられない。exe が書いた「開いている」の印を消す（残っても次の起動はすぐ自分で開くが、紛らわしい）
        running = Path.home() / '.ecologger-analysis' / 'running.json'
        try:
            if json.loads(running.read_text(encoding='utf-8')).get('pid') == p.pid:
                running.unlink()
        except (OSError, ValueError):
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description='配る形（exe と zip）を作る')
    ap.add_argument('--check', action='store_true', help='作った exe が起きて返事をするかだけ確かめる')
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if a.check:
        return check()
    build()
    return 0


if __name__ == '__main__':
    sys.exit(main())
