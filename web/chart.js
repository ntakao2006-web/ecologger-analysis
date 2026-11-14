// 周の重ね合わせのグラフ（自前の canvas。外のライブラリは使わない）
// 上から「基準の周とのタイムの差」（2周以上のとき）と、選んだチャンネルを1段ずつ。横軸は距離か時間で、全段で共通。
// ホイールで横に拡大・縮小、ドラッグで横に動かす、ダブルクリックで全体に戻す。マウスの位置の値を各段の見出しに出す。
'use strict';

const Chart = (() => {
  const PAD_L = 52, PAD_R = 10, PAD_T = 22, PAD_B = 4, AXIS_H = 22;
  let root = null, data = null, opts = null, view = null, viewX = null, cursorX = null, panels = [], drag = null;

  window.addEventListener('mouseup', () => { drag = null; });

  function css(name) { return getComputedStyle(document.body).getPropertyValue(name).trim(); }

  // 周の横軸（距離か時間）の配列
  function xs(lap) { return opts.x === 'dist' ? lap.s : lap.t; }

  function fullRange() {
    let hi = 0;
    for (const lap of data.laps) { const a = xs(lap); if (a && a.length) hi = Math.max(hi, a[a.length - 1] ?? 0); }
    return [0, hi || 1];
  }

  // 増える一方の配列で、x の値を補って読む（範囲の外・空欄は null）
  function valueAt(xa, ya, x) {
    if (!xa || !ya || !xa.length || x < xa[0] || x > xa[xa.length - 1]) return null;
    let lo = 0, hi = xa.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xa[m] <= x) lo = m; else hi = m; }
    const a = ya[lo], b = ya[hi];
    if (a === null || b === null) return a ?? b;
    const dx = xa[hi] - xa[lo];
    return dx > 0 ? a + (b - a) * (x - xa[lo]) / dx : a;
  }

  function niceTicks(lo, hi, n) {
    if (!(hi > lo)) return [lo];
    const raw = (hi - lo) / n, p = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((k) => k * p).find((k) => k >= raw) || 10 * p;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }

  function fmtX(v) {
    if (opts.x === 'dist') return v >= 1000 ? (v / 1000).toFixed(v % 1000 === 0 ? 0 : 1) + 'km' : Math.round(v) + 'm';
    const m = Math.floor(v / 60), s = v - m * 60;
    return m ? `${m}:${String(Math.round(s)).padStart(2, '0')}` : `${Math.round(s)}s`;
  }
  function fmtY(v) { const a = Math.abs(v); return a >= 1000 ? v.toFixed(0) : a >= 10 ? v.toFixed(0) : a >= 1 ? v.toFixed(1) : v.toFixed(2); }

  function series(panel, lap) {
    if (panel.kind === 'delta') {
      if (!lap.delta) return null;
      return { x: opts.x === 'dist' ? data.grid : data.ref_t, y: lap.delta };
    }
    const y = lap.channels[panel.name];
    return y ? { x: xs(lap), y } : null;
  }

  function buildPanels() {
    panels = [];
    const many = data.laps.filter((l) => l.delta).length >= 2;
    if (many) panels.push({ kind: 'delta', name: 'delta', label: '基準の周との差', unit: '秒', state: false });
    for (const c of opts.channels) panels.push({ kind: 'chan', name: c.name, label: c.label || c.name, unit: c.unit, state: c.kind === 'state' });
  }

  function layout() {
    root.textContent = '';
    const h = root.clientHeight || 400;
    const n = Math.max(1, panels.length);
    const each = Math.max(90, Math.floor((h - AXIS_H) / n));
    for (const p of panels) {
      const wrap = document.createElement('div');
      wrap.className = 'plot';
      const head = document.createElement('div');
      head.className = 'plothead';
      const cv = document.createElement('canvas');
      cv.style.height = each + 'px';
      wrap.append(head, cv);
      root.append(wrap);
      p.canvas = cv; p.head = head; p.h = each;
    }
    const axis = document.createElement('canvas');
    axis.className = 'xaxis';
    axis.style.height = AXIS_H + 'px';
    root.append(axis);
    root.axis = axis;
    for (const cv of root.querySelectorAll('canvas')) bind(cv);
  }

  function sizeCanvas(cv) {
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    return [g, w, h];
  }

  function px(x, w) { return PAD_L + (x - view[0]) / (view[1] - view[0]) * (w - PAD_L - PAD_R); }
  function xval(p, w) { return view[0] + (p - PAD_L) / (w - PAD_L - PAD_R) * (view[1] - view[0]); }

  function drawPanel(p) {
    const [g, w, h] = sizeCanvas(p.canvas);
    g.clearRect(0, 0, w, h);
    // 見える範囲の縦の幅
    let lo = Infinity, hi = -Infinity;
    for (const lap of data.laps) {
      const s = series(p, lap);
      if (!s || !s.x) continue;
      for (let i = 0; i < s.x.length; i++) {
        const x = s.x[i], y = s.y[i];
        if (x === null || y === null || x < view[0] || x > view[1]) continue;
        if (y < lo) lo = y; if (y > hi) hi = y;
      }
    }
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (p.kind === 'delta') { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const padY = (hi - lo) * 0.08; lo -= padY; hi += padY;
    const py = (y) => PAD_T + (hi - y) / (hi - lo) * (h - PAD_T - PAD_B);
    // 目盛りと格子（控えめに）
    g.font = '11px "Yu Gothic UI", "Segoe UI", sans-serif';
    g.fillStyle = css('--muted'); g.strokeStyle = css('--grid'); g.lineWidth = 1;
    g.textAlign = 'right'; g.textBaseline = 'middle';
    for (const v of niceTicks(lo, hi, 3)) {
      const y = Math.round(py(v)) + 0.5;
      g.beginPath(); g.moveTo(PAD_L, y); g.lineTo(w - PAD_R, y); g.stroke();
      g.fillText(fmtY(v), PAD_L - 6, y);
    }
    for (const v of niceTicks(view[0], view[1], 8)) {
      const x = Math.round(px(v, w)) + 0.5;
      g.beginPath(); g.moveTo(x, PAD_T); g.lineTo(x, h - PAD_B); g.stroke();
    }
    if (p.kind === 'delta') {     // 0 の線（基準の周）
      g.strokeStyle = css('--muted'); const y = Math.round(py(0)) + 0.5;
      g.beginPath(); g.moveTo(PAD_L, y); g.lineTo(w - PAD_R, y); g.stroke();
    }
    // 線（基準の周を最後に描いて上に出す）
    g.save();
    g.beginPath(); g.rect(PAD_L, 0, w - PAD_L - PAD_R, h); g.clip();
    const order = data.laps.map((l, i) => i).sort((a, b) => (data.laps[a].ref ? 1 : 0) - (data.laps[b].ref ? 1 : 0));
    for (const i of order) {
      const lap = data.laps[i], s = series(p, lap);
      if (!s || !s.x) continue;
      g.strokeStyle = opts.color(lap.key); g.lineWidth = lap.ref ? 2 : 1.5; g.lineJoin = 'round';
      g.beginPath();
      let pen = false, lastY = null;
      for (let k = 0; k < s.x.length; k++) {
        const x = s.x[k], y = s.y[k];
        if (x === null || y === null) { pen = false; continue; }
        const X = px(x, w), Y = py(y);
        if (X < PAD_L - 50 || X > w + 50) { pen = false; lastY = Y; continue; }
        if (!pen) { g.moveTo(X, Y); pen = true; }
        else if (p.state) { g.lineTo(X, lastY); g.lineTo(X, Y); }
        else g.lineTo(X, Y);
        lastY = Y;
      }
      g.stroke();
    }
    g.restore();
    // カーソル
    if (cursorX !== null && cursorX >= view[0] && cursorX <= view[1]) {
      const x = Math.round(px(cursorX, w)) + 0.5;
      g.strokeStyle = css('--text'); g.lineWidth = 1;
      g.beginPath(); g.moveTo(x, PAD_T - 4); g.lineTo(x, h - PAD_B); g.stroke();
    }
    drawHead(p);
  }

  function drawHead(p) {
    p.head.textContent = '';
    const name = document.createElement('span');
    name.className = 'pname';
    name.textContent = p.unit ? `${p.label}（${p.unit}）` : p.label;
    p.head.append(name);
    if (cursorX === null) return;
    for (const lap of data.laps) {
      const s = series(p, lap);
      const v = s ? valueAt(s.x, s.y, cursorX) : null;
      const item = document.createElement('span');
      item.className = 'pval';
      const sw = document.createElement('i');
      sw.style.background = opts.color(lap.key);
      item.append(sw, v === null ? '—' : p.kind === 'delta' ? (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(3) : fmtY(v));
      p.head.append(item);
    }
  }

  function drawAxis() {
    const cv = root.axis;
    const [g, w, h] = sizeCanvas(cv);
    g.clearRect(0, 0, w, h);
    g.font = '11px "Yu Gothic UI", "Segoe UI", sans-serif';
    g.fillStyle = css('--muted'); g.textAlign = 'center'; g.textBaseline = 'top';
    for (const v of niceTicks(view[0], view[1], 8)) g.fillText(fmtX(v), px(v, w), 4);
    if (cursorX !== null && cursorX >= view[0] && cursorX <= view[1]) {
      const x = px(cursorX, w), t = fmtX(cursorX);
      const tw = g.measureText(t).width + 8;
      g.fillStyle = css('--panel'); g.fillRect(x - tw / 2, 2, tw, 16);
      g.fillStyle = css('--text'); g.fillText(t, x, 4);
    }
  }

  function draw() { if (!data) return; for (const p of panels) drawPanel(p); drawAxis(); }

  function bind(cv) {
    cv.addEventListener('mousemove', (e) => {
      const r = cv.getBoundingClientRect(), x = xval(e.clientX - r.left, r.width);
      if (drag) {
        const dx = (e.clientX - drag.px) / (r.width - PAD_L - PAD_R) * (drag.view[1] - drag.view[0]);
        setView(drag.view[0] - dx, drag.view[1] - dx);
        return;
      }
      cursorX = x; draw();
      if (opts.onCursor) opts.onCursor(cursorX);
    });
    cv.addEventListener('mouseleave', () => { drag = null; cursorX = null; draw(); if (opts.onCursor) opts.onCursor(null); });
    cv.addEventListener('mousedown', (e) => { drag = { px: e.clientX, view: view.slice() }; e.preventDefault(); });
    cv.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = cv.getBoundingClientRect(), x = xval(e.clientX - r.left, r.width);
      const k = e.deltaY > 0 ? 1.25 : 0.8;
      setView(x - (x - view[0]) * k, x + (view[1] - x) * k);
    }, { passive: false });
    cv.addEventListener('dblclick', () => { view = fullRange(); draw(); });
  }

  function setView(a, b) {
    const [lo, hi] = fullRange();
    const span = Math.min(hi - lo, Math.max(b - a, (hi - lo) / 2000));
    a = Math.max(lo, Math.min(a, hi - span));
    view = [a, a + span];
    draw();
  }

  return {
    // data：/api/overlay の返事。o：{x: 'dist'|'time', channels: [{name,label,unit,kind}], color(key)}
    show(el, d, o, keepView) {
      root = el; data = d; opts = o;
      buildPanels(); layout();
      const full = fullRange();
      view = keepView && view && viewX === o.x && view[1] <= full[1] * 1.001 ? view : full;
      if (viewX !== o.x) cursorX = null;
      viewX = o.x;
      draw();
    },
    redraw() { if (data && root) { layout(); draw(); } },
    clear() { data = null; panels = []; if (root) root.textContent = ''; },
  };
})();
