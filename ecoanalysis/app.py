"""解析アプリの入口：ローカルのサーバーを立て、pywebview の窓で開く。

    python -m ecoanalysis [走行 ...]          専用の窓で開く（走行＝.ecorun のフォルダー。続けて開いておく）
    python -m ecoanalysis --browser           いつものブラウザで開く（開発用）
    EcoLogger-Analysis.exe [走行 ...]         配る形（tools/build_exe.py で作る）

- もう開いているアプリがあれば、走行はそちらへ渡して開き、窓を前に出して終わる（2つ目の窓を作らない）。
  開いているアプリの番号は ~/.ecologger-analysis/running.json に置く。
- 専用の窓には Windows の Microsoft Edge WebView2 ランタイムを使う。無い機器では pywebview が古い IE の表示へ
  黙って切り替わり、この画面が動かないので、先に確かめて、無ければいつものブラウザで開く。
  ブラウザで開いたときは、画面が数秒ごとに裏へ知らせ、知らせが途絶えたら（タブを閉じたら）終わる。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__, server

TITLE = 'EcoLogger Analysis'
# 窓（WebView2）の作業場所。一時フォルダーに作らせると閉じたあと消しそこねて溜まるので、決まった所に置く
STORAGE = server.HOME / 'webview'
RUNNING = server.HOME / 'running.json'
IDLE_S = 45            # ブラウザで開いたとき、画面からの知らせがこれだけ途絶えたら終わる
FIRST_S = 180          # ブラウザが一度も知らせてこないまま、これだけ経ったら終わる
WEBVIEW2_MIN = (86, 0, 622, 0)
WEBVIEW2_KEYS = ('{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',   # ランタイム
                 '{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}',   # Beta
                 '{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}',   # Dev
                 '{65C35B14-6C1D-4122-AC46-7148CC9D6497}')   # Canary


def webview2_version():
    """入っている WebView2 ランタイムの版（無ければ None）。pywebview と同じレジストリの所を見る"""
    try:
        import winreg
    except ImportError:
        return None
    best = None
    for root, prefix in ((winreg.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\EdgeUpdate\Clients'),
                         (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients'),
                         (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\EdgeUpdate\Clients')):
        for key in WEBVIEW2_KEYS:
            try:
                with winreg.OpenKey(root, rf'{prefix}\{key}') as k:
                    pv, _ = winreg.QueryValueEx(k, 'pv')
                v = tuple(int(x) for x in str(pv).split('.'))
            except (OSError, ValueError):
                continue
            if v >= WEBVIEW2_MIN and (best is None or v > best):
                best = v
    return best


def pick_folder(out, start=None):
    """フォルダーを選ぶ窓を出し、選んだ所を out へ書く（exe のときの、別のプロセスでの呼ばれ方）。start：最初に開く所"""
    import tkinter
    import tkinter.filedialog
    r = tkinter.Tk()
    r.withdraw()
    r.attributes('-topmost', True)
    got = tkinter.filedialog.askdirectory(title='走行のフォルダー（.ecorun）を選ぶ', initialdir=start or None) or ''
    Path(out).write_text(got, encoding='utf-8')
    return 0


def hand_over(paths, timeout=120):
    """もう開いているアプリへ走行を渡す。渡せたら True"""
    try:
        info = json.loads(RUNNING.read_text(encoding='utf-8'))
        port = int(info['port'])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/handover', data=json.dumps({'paths': paths}).encode('utf-8'),
                                 headers={server.API_HEADER: '1', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return bool(json.loads(r.read()).get('ok'))
    except (OSError, ValueError, urllib.error.URLError):
        return False


def write_running(port):
    try:
        server.HOME.mkdir(parents=True, exist_ok=True)
        RUNNING.write_text(json.dumps({'port': port, 'pid': os.getpid(), 'version': __version__}), encoding='utf-8')
    except OSError:
        pass


def clear_running():
    try:
        if json.loads(RUNNING.read_text(encoding='utf-8')).get('pid') == os.getpid():
            RUNNING.unlink()
    except (OSError, ValueError):
        pass


def run_in_browser(srv, url, why):
    """いつものブラウザで開き、画面からの知らせが途絶えるまで待つ"""
    store = srv.store
    store.mode, store.mode_note = 'browser', why
    webbrowser.open(url)
    if sys.stdout:
        print(f'{TITLE} {__version__}: {url}（ブラウザのタブを閉じると、しばらくして終わる。Ctrl+C でもよい）')
    started = time.time()
    try:
        while True:
            time.sleep(2)
            seen = store.last_seen
            if (seen and time.time() - seen > IDLE_S) or (not seen and time.time() - started > FIRST_S):
                break
    except KeyboardInterrupt:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ecoanalysis', description=TITLE)
    ap.add_argument('paths', nargs='*', help='開く走行（.ecorun のフォルダー）')
    ap.add_argument('--browser', action='store_true', help='専用の窓でなく、いつものブラウザで開く')
    ap.add_argument('--port', type=int, default=0, help='待つ番号（既定：空いている番号）')
    ap.add_argument('--pick-folder', metavar='FILE', help=argparse.SUPPRESS)
    ap.add_argument('--pick-start', metavar='DIR', help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.pick_folder:
        return pick_folder(a.pick_folder, a.pick_start)

    paths = [str(Path(p).expanduser().resolve()) for p in a.paths]
    if paths and hand_over(paths):
        return 0

    srv, url = server.start(a.port)
    write_running(srv.server_address[1])
    try:
        srv.store.open_many(paths)
        if a.browser:
            run_in_browser(srv, url, None)
            return 0
        if webview2_version() is None:
            run_in_browser(srv, url, 'この機器には Microsoft Edge WebView2 ランタイムが無いので、いつものブラウザで開いています。'
                                     '入れると専用の窓で開きます（Microsoft の「WebView2 ランタイム」の配布ページから）。'
                                     'このタブを閉じると、しばらくしてアプリも終わります。')
            return 0
        try:
            import webview
        except ImportError:
            run_in_browser(srv, url, 'pywebview が入っていないので、いつものブラウザで開いています（pip install -r requirements.txt）')
            return 0
        win = webview.create_window(f'{TITLE} {__version__}', url, width=1400, height=900, min_size=(900, 600))

        def pick(start=None):
            got = win.create_file_dialog(webview.FileDialog.FOLDER, directory=start or '')
            return str(got[0]) if got else None

        def raise_window():
            try:
                win.restore()
                win.show()
                win.on_top = True
                win.on_top = False
            except Exception:  # noqa: BLE001  前に出せなくても、走行は開けている
                pass

        srv.store.picker = pick
        srv.store.raiser = raise_window
        STORAGE.mkdir(parents=True, exist_ok=True)
        webview.start(gui='edgechromium', private_mode=False, storage_path=str(STORAGE))   # 窓を閉じるまで戻らない
        return 0
    finally:
        clear_running()
        srv.shutdown()


if __name__ == '__main__':
    sys.exit(main())
