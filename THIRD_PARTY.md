# 使っている部品とライセンス

`python tools/check_licenses.py --md` の出力（Windows、2026-11-15）。部品を足したら作り直す。

| 名前 | 版 | ライセンス | 判定 |
|---|---|---|---|
| bottle | 0.13.4 | MIT / MIT License | OK |
| cffi | 2.1.1 | MIT-0 | OK |
| clr_loader | 0.3.1 | MIT License（LICENSE の頭） | OK |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | OK |
| pillow | 13.0.0 | MIT-CMU | OK |
| proxy_tools | 0.1.0 | MIT / MIT License | OK |
| pycparser | 3.12 | BSD-3-Clause | OK |
| pythonnet | 3.2.1 | MIT | OK |
| pywebview | 6.2.1 | BSD License | OK |
| typing_extensions | 4.16.0 | PSF-2.0 | OK |

画面の表示には、Windowsに入っている Microsoft Edge WebView2 ランタイムを使う（アプリには同梱していない）。

## 配る exe（`tools/build_exe.py`）に入るもの

上の部品のほかに、exe のフォルダー（`_internal/`）には次が入る。どれも配ってよいライセンス。

| もの | 出どころ | ライセンス |
|---|---|---|
| Python 本体と標準の部品（`python314.dll`・`base_library.zip`・`*.pyd`） | python.org | PSF-2.0 |
| OpenSSL（`libcrypto-3.dll`・`libssl-3.dll`） | Python に付属 | Apache-2.0 |
| libffi・zlib・bzip2・xz・zstd・expat | Python に付属 | MIT・Zlib・BSD 系・パブリックドメイン |
| Tcl/Tk（`tcl86t.dll`・`tk86t.dll`。フォルダーを選ぶ窓に使う） | Python に付属 | Tcl/Tk License（BSD 系） |
| `VCRUNTIME140.dll`・`VCRUNTIME140_1.dll` | Microsoft Visual C++ 再頒布可能パッケージ（Python に付属） | Microsoft の再頒布の条件で配ってよい |
| `Microsoft.Web.WebView2.Core.dll`・`.WinForms.dll`・`WebView2Loader.dll` | WebView2 SDK（pywebview に付属） | WebView2 SDK のライセンス（BSD 系。配ってよい） |
| `WebBrowserInterop.*.dll`・`Python.Runtime.dll` | pywebview・pythonnet に付属 | BSD・MIT |
| setuptools（PyInstaller が拾う） | PyPA | MIT |

- **PyInstaller**（exe を作る道具。版 6.22）は GPL-2.0-or-later だが、exe に入る起動の部分（bootloader）には
  「できた exe をどんなライセンスで配ってもよい」という例外が付いている。PyInstaller 自身のコードは exe に入らない。
  ふだんの `.venv` には入れず、exe を作るときだけの `.venv-build` に入れる（`tools/check_licenses.py` の対象外）。
- WebView2 **ランタイム**（Edge の表示エンジン）は同梱しない。Windows 10/11 にはふつう入っている。無い機器ではブラウザで開く。


## 使っているデータ（アプリには入れない）

- **航空写真**：国土地理院「地理院タイル」の全国最新写真（シームレス）。アプリには同梱せず、使う人の PC が見るときに取って
  `~/.ecologger-analysis/tiles/` に残す。地理院タイルの利用の決まりに従い、図の右下に「出典：国土地理院」を出す。
- **道路縁**：国土地理院「地理院地図Vector」（最適化ベクトルタイル）の道路縁。同じく使うときに取って
  `~/.ecologger-analysis/tiles/gsi-bvmap/` に残す。コース図（`ecoanalysis/outline.py`）はこれと航空写真を加工して作るので、
  図の右下に「国土地理院のデータを加工して作成」と出す。できたコース図は `~/.ecologger-analysis/outlines/` に残す。
