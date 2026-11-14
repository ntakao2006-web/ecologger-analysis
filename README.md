# EcoLogger 解析

自作の車載データロガー（EcoLogger）の走行を解析するデスクトップアプリ。作っている途中（0.0.1：窓が開くだけ）。

## めざすもの

- 周の重ね合わせ（横軸は時間・距離で切り替え）と、基準の周とのタイムの差
- コース図とカーソル（図を押すと、グラフも動画もその位置へ）
- カーソルに合わせて動く動画。2周の動画を同じ距離（同じ時間）で並べて比べる。**動画の位置はロガーが残す「動画の秒と測位の時刻の対応表」から出し、手で合わせない**
- 計算チャンネル（式を入れて新しいチャンネルを作る）

ロガーとの境目は、走行1回ぶんのファイルの形式だけ（物理量の表・周・動画の対応表・コースの線）。形式は [docs/format.md](docs/format.md)（ecorun、版1）。

## 動かし方（Windows）

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m ecoanalysis
```

- 専用の窓で開く（pywebview。表示はWindowsに入っている Microsoft Edge WebView2 ランタイム）
- `--browser` を付けると、いつものブラウザで開く（開発用。WebView2 が無いときの逃げ道）
- 「走行を開く…」で走行のフォルダー（`.ecorun`）を選ぶ。別の日の走行も続けて開ける。最近開いたものは上の一覧から開ける
- 周の表は「コースの線で切った周」（ふだん）と「ロガーの数え」を切り替えられる。ベストとの差は、同じコースの開いている走行どうしで比べる
- 読んだ表は `~/.ecologger-analysis/cache/` に覚え、2回目からはすぐ開く（初回は決勝2時間半ぶんで数秒）
- 試験：`.venv\Scripts\python -m unittest discover -s tests`
- 作りものの走行データ：`.venv\Scripts\python -m ecoanalysis.sample data\sample.ecorun`（本物の記録が無いときの画面づくり用。`data/` はGitに入れない）

## 作り

- `ecoanalysis/server.py`：画面と裏の処理をつなぐローカルのHTTPサーバー（127.0.0.1だけで待つ）
- `ecoanalysis/app.py`：入口。サーバーを立てて pywebview の窓で開く
- `ecoanalysis/session.py`：走行データ（ecorun）を読み、形が決まりどおりかを確かめる
- `ecoanalysis/laps.py`：コースの線で、測位から周を切り直す（線を越えた時刻＝線の手前と先の2点から補った値）。ロガーが数えた周と並べて出す
- `ecoanalysis/sample.py`：作りものの走行データを書く
- `web/`：画面（HTML・CSS・JavaScript）

画面とPythonのやりとりは `/api/` のHTTPだけにしてあり、pywebview 独自の呼び出しは使わない。あとで Electron や Tauri へ移すときも、画面をそのまま使える。

## ライセンス

MIT（`LICENSE`）。使っている部品とそのライセンスは `THIRD_PARTY.md`。

部品は MIT・BSD・Apache・ISC・PSF のライセンスのものだけを使い、GPL・LGPL・AGPL のものは入れない。足すときは `python tools/check_licenses.py` を通す（試験にも入っている）。
