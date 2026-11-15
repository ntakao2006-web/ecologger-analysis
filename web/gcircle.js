// 摩擦円の枠「G」（SP-10-14。仕様 analysis_map_features.md 3-3）。自前の canvas。
// - 横軸は横G（右へ曲がると右＝＋）、縦軸は前後G（加速が上）。IMU があればその値（0.3秒でならす）、無ければ測位から計算した値
// - 選んだ周の全部の点を薄く、カーソルの所の点を濃く大きく、カーソルの所までの直前2秒を線で
// - 目安の線：選んだ周の、向きごと（15°刻み）の合成Gの 99% を結んだ線（包絡。裏が出す friction.env）
// - 枠の下に、カーソルの所の横G・前後G・合成G・旋回半径・速さを周ごとに
'use strict';

const GCircle = (() => {
  const TRAIL_S = 2.0, PAD = 22;
  let cv = null, info = null, note = null, data = null, opts = null, cursor = null;

  function css(name) { return getComputedStyle(document.body).getPropertyValue(name).trim(); }

  function sizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    return [g, w, h];
  }

  // 増える一方の配列 xa で、x のときの ya の値（範囲の外・空欄は null）
  function valueAt(xa, ya, x) {
    if (!xa || !ya || !xa.length || x === null || x < xa[0] || x > xa[xa.length - 1]) return null;
    let lo = 0, hi = xa.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xa[m] <= x) lo = m; else hi = m; }
    const a = ya[lo], b = ya[hi];
    if (a === null || b === null) return a ?? b;
    const dx = xa[hi] - xa[lo];
    return dx > 0 ? a + (b - a) * (x - xa[lo]) / dx : a;
  }

  // カーソル（横軸の値）を、その周の頭からの秒に直す
  function lapTimeAt(lap, x) {
    if (x === null || x === undefined) return null;
    if (opts.x() === 'dist') return lap.s ? valueAt(lap.s, lap.t, x) : null;
    return x;
  }

  function short(lap) {
    const s = lap.label.split(' ').pop();
    return data.laps.filter((l) => l.label.split(' ').pop() === s).length > 1 ? lap.label : s;
  }

  function range() {
    let m = 1.0;
    for (const lap of data.laps) {
      const g = lap.g;
      if (!g) continue;
      for (let i = 0; i < g.x.length; i++) {
        if (g.x[i] !== null) m = Math.max(m, Math.abs(g.x[i]));
        if (g.y[i] !== null) m = Math.max(m, Math.abs(g.y[i]));
      }
    }
    return Math.min(3, Math.ceil(m * 4) / 4 + 0.1);
  }

  function draw() {
    if (!cv) return;
    const [g, w, h] = sizeCanvas();
    g.clearRect(0, 0, w, h);
    if (!data || !data.laps.some((l) => l.g)) return;
    const R = range(), half = Math.max(10, Math.min(w, h) / 2 - PAD), cx = w / 2, cy = h / 2, k = half / R;
    const P = (x, y) => [cx + x * k, cy - y * k];
    const muted = css('--muted'), grid = css('--line'), text = css('--text');
    g.font = '11px "Yu Gothic UI", "Segoe UI", sans-serif';
    // 目盛り：0.5 G ごとの円と十字
    g.lineWidth = 1; g.strokeStyle = grid;
    for (let r = 0.5; r <= R + 1e-9; r += 0.5) { g.beginPath(); g.arc(cx, cy, r * k, 0, Math.PI * 2); g.stroke(); }
    g.beginPath(); g.moveTo(cx - half, cy); g.lineTo(cx + half, cy); g.moveTo(cx, cy - half); g.lineTo(cx, cy + half); g.stroke();
    g.fillStyle = muted; g.textAlign = 'left'; g.textBaseline = 'bottom';
    for (let r = 0.5; r <= R + 1e-9; r += 0.5) g.fillText(`${r.toFixed(1)}G`, cx + r * k * 0.707 + 2, cy - r * k * 0.707);
    g.textAlign = 'center'; g.textBaseline = 'top'; g.fillText('加速', cx, cy - half - PAD + 4);
    g.textBaseline = 'bottom'; g.fillText('減速', cx, cy + half + PAD - 4);
    g.textAlign = 'left'; g.textBaseline = 'middle'; g.fillText('右', cx + half + 4, cy);
    g.textAlign = 'right'; g.fillText('左', cx - half - 4, cy);
    // 全部の点（薄く）
    for (const lap of data.laps) {
      const d = lap.g;
      if (!d) continue;
      g.fillStyle = opts.color(lap.key); g.globalAlpha = lap.ref ? 0.45 : 0.3;
      for (let i = 0; i < d.x.length; i++) {
        if (d.x[i] === null || d.y[i] === null) continue;
        const [px, py] = P(d.x[i], d.y[i]);
        g.fillRect(px - 1, py - 1, 2, 2);
      }
    }
    g.globalAlpha = 1;
    // 包絡（向きごとの 99%）
    const env = data.friction && data.friction.env;
    if (env && env.length) {
      const n = env.length, step = 2 * Math.PI / n;
      g.beginPath();
      for (let i = 0; i <= n; i++) {
        const a = (i % n + 0.5) * step, r = env[i % n];
        const [px, py] = P(r * Math.cos(a), r * Math.sin(a));
        if (i) g.lineTo(px, py); else g.moveTo(px, py);
      }
      g.setLineDash([5, 4]); g.strokeStyle = text; g.globalAlpha = 0.55; g.lineWidth = 1.5; g.stroke();
      g.setLineDash([]); g.globalAlpha = 1;
    }
    // カーソルの所：直前2秒の線と、濃く大きな点
    for (const lap of data.laps) {
      const d = lap.g, tc = lapTimeAt(lap, cursor);
      if (!d || tc === null) continue;
      const color = opts.color(lap.key);
      g.beginPath();
      let on = false;
      for (let i = 0; i < d.t.length; i++) {
        if (d.t[i] < tc - TRAIL_S) continue;
        if (d.t[i] > tc) break;
        if (d.x[i] === null || d.y[i] === null) { on = false; continue; }
        const [px, py] = P(d.x[i], d.y[i]);
        if (on) g.lineTo(px, py); else { g.moveTo(px, py); on = true; }
      }
      g.strokeStyle = color; g.lineWidth = lap.ref ? 2.5 : 2; g.stroke();
      const x = valueAt(d.t, d.x, tc), y = valueAt(d.t, d.y, tc);
      if (x === null || y === null) continue;
      const [px, py] = P(x, y);
      g.beginPath(); g.arc(px, py, lap.ref ? 7 : 6, 0, Math.PI * 2);
      g.fillStyle = color; g.fill(); g.lineWidth = 2; g.strokeStyle = css('--panel'); g.stroke();
    }
  }

  function f(v, d) { return v === null || v === undefined ? '—' : v.toFixed(d); }

  function renderInfo() {
    if (!info) return;
    info.textContent = '';
    if (!data) return;
    const laps = data.laps.filter((l) => l.g);
    if (!laps.length) return;
    const tb = document.createElement('table');
    tb.className = 'laptable gtable';
    tb.innerHTML = '<thead><tr><th></th><th>横G</th><th>前後G</th><th>合成G</th><th title="測位から計算した旋回半径。ほぼまっすぐな所（600 m より大きい）と遅い所は出しません">半径 m</th><th>速さ km/h</th></tr></thead>';
    const body = document.createElement('tbody');
    for (const lap of laps) {
      const d = lap.g, tc = lapTimeAt(lap, cursor);
      const x = valueAt(d.t, d.x, tc), y = valueAt(d.t, d.y, tc), r = valueAt(d.t, d.r, tc), v = valueAt(d.t, d.v, tc);
      const tr = document.createElement('tr');
      const name = document.createElement('td');
      name.className = 'sec';
      const dot = document.createElement('span');
      dot.className = 'lapdot'; dot.style.background = opts.color(lap.key);
      name.append(dot, short(lap) + (lap.ref ? '（基準）' : ''));
      tr.append(name);
      const lat = x === null ? '—' : `${x >= 0 ? '右' : '左'} ${Math.abs(x).toFixed(2)}`;
      for (const s of [lat, f(y, 2), x === null || y === null ? '—' : Math.hypot(x, y).toFixed(2), r === null ? '—' : String(Math.round(r)), f(v, 0)]) {
        const td = document.createElement('td'); td.className = 'num'; td.textContent = s; tr.append(td);
      }
      body.append(tr);
    }
    tb.append(body);
    info.append(tb);
  }

  function renderNote() {
    if (!note) return;
    const fr = data && data.friction;
    let t = '';
    if (fr && fr.src) {
      if (fr.want === 'imu' && fr.src !== 'imu') t = 'IMU の横G・前後Gが無い周は、測位から計算した値で描いています（センサーの値ではありません）。';
      else if (fr.src.includes('gnss')) t = '測位の位置から計算した値です（センサーの値ではありません。位置を0.5秒でならして微分）。';
      else t = 'IMU の横G・前後G（0.3秒でならす）。';
      t += ' 破線は選んだ周の、向きごとの合成Gの 99%（包絡）。';
    } else if (data && data.laps.length) t = 'G を出せる記録がありません（IMU の横G・前後Gも、10Hz 程度の測位もない）。';
    note.textContent = t;
  }

  return {
    // canvas：描く所、infoEl：数の表、noteEl：断り書き。o：{x() 'dist'|'time', color(key)}
    init(canvas, infoEl, noteEl, o) {
      cv = canvas; info = infoEl; note = noteEl; opts = o;
      new ResizeObserver(() => draw()).observe(cv);
    },
    show(d) { data = d; renderNote(); renderInfo(); draw(); },
    setCursor(x) { cursor = x === undefined ? null : x; renderInfo(); draw(); },
    redraw() { draw(); },
    clear() { data = null; cursor = null; renderNote(); renderInfo(); draw(); },
  };
})();
