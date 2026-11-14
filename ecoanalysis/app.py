"""解析アプリの入口：ローカルのサーバーを立て、pywebview の窓で開く。

    python -m ecoanalysis              専用の窓で開く
    python -m ecoanalysis --browser    いつものブラウザで開く（開発用・WebView2 が無いときの逃げ道）
"""
import argparse
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__, server

TITLE = 'EcoLogger 解析'
# 窓（WebView2）の作業場所。一時フォルダーに作らせると閉じたあと消しそこねて溜まるので、決まった所に置く
STORAGE = Path.home() / '.ecologger-analysis' / 'webview'


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ecoanalysis', description=TITLE)
    ap.add_argument('--browser', action='store_true', help='専用の窓でなく、いつものブラウザで開く')
    ap.add_argument('--port', type=int, default=0, help='待つ番号（既定：空いている番号）')
    a = ap.parse_args(argv)

    srv, url = server.start(a.port)
    if a.browser:
        webbrowser.open(url)
        print(f'{TITLE} {__version__}: {url}（Ctrl+C で終わる）')
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    try:
        import webview
    except ImportError:
        print('pywebview が入っていません（pip install -r requirements.txt）。--browser で開けます', file=sys.stderr)
        return 1
    win = webview.create_window(f'{TITLE} {__version__}', url, width=1400, height=900, min_size=(900, 600))

    def pick():
        got = win.create_file_dialog(webview.FileDialog.FOLDER)
        return str(got[0]) if got else None

    srv.store.picker = pick
    STORAGE.mkdir(parents=True, exist_ok=True)
    webview.start(private_mode=False, storage_path=str(STORAGE))   # 窓を閉じるまで戻らない
    srv.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
