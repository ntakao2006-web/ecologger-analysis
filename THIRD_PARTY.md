# 使っている部品とライセンス

`python tools/check_licenses.py --md` の出力（Windows、2026-11-14）。部品を足したら作り直す。

| 名前 | 版 | ライセンス | 判定 |
|---|---|---|---|
| bottle | 0.13.4 | MIT / MIT License | OK |
| cffi | 2.1.1 | MIT-0 | OK |
| clr_loader | 0.3.1 | MIT License（LICENSE の頭） | OK |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | OK |
| proxy_tools | 0.1.0 | MIT / MIT License | OK |
| pycparser | 3.12 | BSD-3-Clause | OK |
| pythonnet | 3.2.1 | MIT | OK |
| pywebview | 6.2.1 | BSD License | OK |
| typing_extensions | 4.16.0 | PSF-2.0 | OK |

画面の表示には、Windowsに入っている Microsoft Edge WebView2 ランタイムを使う（アプリには同梱していない）。
