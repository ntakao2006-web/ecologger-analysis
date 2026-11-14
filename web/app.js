// 画面の入口。裏（Python）とは /api/ のHTTPだけでやりとりする（pywebview の独自の呼び出しは使わない）
'use strict';

async function health() {
  const el = document.getElementById('status');
  try {
    const r = await fetch('/api/health');
    const j = await r.json();
    el.textContent = `準備できました（v${j.version}）`;
    el.className = 'status ok';
  } catch (e) {
    el.textContent = '裏の処理とつながっていません';
    el.className = 'status ng';
  }
}

health();
