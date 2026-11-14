// コース図（自前の canvas）。選んだ周の走った線、コントロールライン・セクターの線、カーソルの位置の点。
// 図を押すと、基準の周のいちばん近い所へグラフのカーソルを動かす。線を直すときは、押した所へ線を置く。
// 座標は、重ね合わせの返事の origin（基準の周の頭の位置）を原点にした m（東が x、北が y）。
'use strict';

const CourseMap = (() => {
  const M = 111320.0, PAD = 16;
  let cv = null, data = null, opts = null, view = null, cursor = null, drag = null, moved = false;

  function css(name) { return getComputedStyle(document.body).getPropertyValue(name).trim(); }

  function toXY(lat, lon) {
    const [lat0, lon0] = data.origin;
    return [(lon - lon0) * M * Math.cos(lat0 * Math.PI / 180), (lat - lat0) * M];
  }
  function toLatLon(x, y) {
    const [lat0, lon0] = data.origin;
    return [lat0 + y / M, lon0 + x / (M * Math.cos(lat0 * Math.PI / 180))];
  }

  function refLap() { return data.laps.find((l) => l.ref && l.track) || data.laps.find((l) => l.track); }

  function fit() {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const lap of data.laps) {
      if (!lap.track) continue;
      for (let i = 0; i < lap.track.x.length; i++) {
        const x = lap.track.x[i], y = lap.track.y[i];
        if (x === null || y === null) continue;
        x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      }
    }
    if (!isFinite(x0)) { x0 = -100; x1 = 100; y0 = -100; y1 = 100; }
    view = { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, span: Math.max(x1 - x0, y1 - y0, 50) * 1.08 };
  }

  function size() {
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    return [g, w, h];
  }
  function scale(w, h) { return Math.min(w - 2 * PAD, h - 2 * PAD) / view.span; }
  function P(x, y, w, h) { const k = scale(w, h); return [w / 2 + (x - view.cx) * k, h / 2 - (y - view.cy) * k]; }
  function U(px, py, w, h) { const k = scale(w, h); return [view.cx + (px - w / 2) / k, view.cy - (py - h / 2) / k]; }

  // 線（中心・向き・幅）の両端
  function ends(line) {
    const [cx, cy] = toXY(line.lat, line.lon);
    const r = line.bearing * Math.PI / 180, half = line.width_m / 2;
    const ux = Math.cos(r), uy = -Math.sin(r);      // 進む向き（北から時計回り）に直角な向き
    return [[cx - ux * half, cy - uy * half], [cx + ux * half, cy + uy * half], [cx, cy], [Math.sin(r), Math.cos(r)]];
  }

  // 増える一方の配列 xa で、x のときの位置
  function posAt(track, key, x) {
    const xa = track[key];
    if (!xa || !xa.length || x < xa[0] || x > xa[xa.length - 1]) return null;
    let lo = 0, hi = xa.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xa[m] <= x) lo = m; else hi = m; }
    const d = xa[hi] - xa[lo], f = d > 0 ? (x - xa[lo]) / d : 0;
    return [track.x[lo] + (track.x[hi] - track.x[lo]) * f, track.y[lo] + (track.y[hi] - track.y[lo]) * f];
  }

  function drawLine(g, line, w, h, color, width, label, dashed) {
    const [a, b, c, dir] = ends(line);
    const [ax, ay] = P(a[0], a[1], w, h), [bx, by] = P(b[0], b[1], w, h);
    g.strokeStyle = color; g.lineWidth = width; g.setLineDash(dashed ? [5, 4] : []);
    g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke();
    g.setLineDash([]);
    // 進む向きの小さな矢印
    const [cx, cy] = P(c[0], c[1], w, h);
    g.beginPath(); g.moveTo(cx, cy); g.lineTo(cx + dir[0] * 12, cy - dir[1] * 12); g.stroke();
    if (label) {
      g.fillStyle = css('--text'); g.font = '11px "Yu Gothic UI", "Segoe UI", sans-serif'; g.textAlign = 'left'; g.textBaseline = 'middle';
      g.fillText(label, bx + 4, by);
    }
  }

  function draw() {
    if (!cv || !data) return;
    const [g, w, h] = size();
    g.clearRect(0, 0, w, h);
    if (!data.origin) {
      g.fillStyle = css('--muted'); g.font = '12px "Yu Gothic UI", sans-serif'; g.textAlign = 'center';
      g.fillText('基準の周の測位が足りないので、コース図を描けません', w / 2, h / 2);
      return;
    }
    // 周の線（基準の周を最後に描いて上に出す）
    const order = data.laps.slice().sort((a, b) => (a.ref ? 1 : 0) - (b.ref ? 1 : 0));
    for (const lap of order) {
      if (!lap.track) continue;
      g.strokeStyle = opts.color(lap.key); g.lineWidth = lap.ref ? 2 : 1.5; g.lineJoin = 'round';
      g.beginPath();
      let pen = false;
      for (let i = 0; i < lap.track.x.length; i++) {
        const x = lap.track.x[i], y = lap.track.y[i];
        if (x === null || y === null) { pen = false; continue; }
        const [px, py] = P(x, y, w, h);
        if (pen) g.lineTo(px, py); else { g.moveTo(px, py); pen = true; }
      }
      g.stroke();
    }
    // 線
    const L = opts.lines();
    if (L && L.line) drawLine(g, L.line, w, h, css('--text'), 2.5, 'コントロールライン', L.draft);
    (L && L.sectors || []).forEach((s, i) => drawLine(g, s, w, h, css('--muted'), 2, `S${i + 1}｜S${i + 2}`, L.draft));
    if (L && L.selected !== null && L.selected !== undefined) {
      const sel = L.selected === 0 ? L.line : L.sectors[L.selected - 1];
      if (sel) drawLine(g, sel, w, h, css('--accent'), 3.5, null, true);
    }
    // カーソルの位置
    if (cursor !== null) {
      const key = opts.x() === 'dist' ? 's' : 't';
      for (const lap of data.laps) {
        if (!lap.track) continue;
        const p = posAt(lap.track, key, cursor);
        if (!p) continue;
        const [px, py] = P(p[0], p[1], w, h);
        g.fillStyle = opts.color(lap.key); g.strokeStyle = css('--panel'); g.lineWidth = 2;
        g.beginPath(); g.arc(px, py, lap.ref ? 6 : 5, 0, 2 * Math.PI); g.fill(); g.stroke();
      }
    }
    // 縮尺
    const k = scale(w, h), m = [10, 20, 50, 100, 200, 500, 1000].find((v) => v * k >= 60) || 1000;
    g.strokeStyle = css('--muted'); g.fillStyle = css('--muted'); g.lineWidth = 2;
    g.beginPath(); g.moveTo(PAD, h - PAD); g.lineTo(PAD + m * k, h - PAD); g.stroke();
    g.font = '11px "Yu Gothic UI", sans-serif'; g.textAlign = 'left'; g.textBaseline = 'bottom';
    g.fillText(m >= 1000 ? `${m / 1000}km` : `${m}m`, PAD, h - PAD - 4);
  }

  // 基準の周で、押した所にいちばん近い点（と、そこでの進む向き）
  function nearest(x, y) {
    const lap = refLap();
    if (!lap) return null;
    const t = lap.track;
    let best = -1, bd = Infinity;
    for (let i = 0; i < t.x.length; i++) {
      if (t.x[i] === null) continue;
      const d = (t.x[i] - x) ** 2 + (t.y[i] - y) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
    if (best < 0) return null;
    const i0 = Math.max(0, best - 2), i1 = Math.min(t.x.length - 1, best + 2);
    const bearing = (Math.atan2(t.x[i1] - t.x[i0], t.y[i1] - t.y[i0]) * 180 / Math.PI + 360) % 360;
    return { i: best, dist: Math.sqrt(bd), s: t.s[best], t: t.t[best], bearing };
  }

  function bind() {
    cv.addEventListener('mousedown', (e) => { drag = { x: e.clientX, y: e.clientY, cx: view.cx, cy: view.cy }; moved = false; });
    window.addEventListener('mouseup', (e) => {
      if (!drag) return;
      const was = drag; drag = null;
      if (moved || !data || !data.origin) return;
      const r = cv.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return;
      const [x, y] = U(was.x - r.left, was.y - r.top, r.width, r.height);
      const near = nearest(x, y);
      if (opts.editing()) {
        const [lat, lon] = toLatLon(x, y);
        opts.onPlace({ lat, lon, bearing: near ? near.bearing : 0 });
      } else if (near && near.dist < view.span / 10) {
        opts.onPick(opts.x() === 'dist' ? near.s : near.t);
      }
    });
    cv.addEventListener('mousemove', (e) => {
      if (!drag) return;
      const r = cv.getBoundingClientRect(), k = scale(r.width, r.height);
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) moved = true;
      if (!moved) return;
      view.cx = drag.cx - (e.clientX - drag.x) / k; view.cy = drag.cy + (e.clientY - drag.y) / k; draw();
    });
    cv.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = cv.getBoundingClientRect();
      const [x, y] = U(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
      const f = e.deltaY > 0 ? 1.25 : 0.8;
      view.span = Math.max(20, view.span * f);
      view.cx = x + (view.cx - x) * f; view.cy = y + (view.cy - y) * f;
      draw();
    }, { passive: false });
    cv.addEventListener('dblclick', () => { fit(); draw(); });
  }

  return {
    // o：{x() 'dist'|'time', color(key), lines() {line, sectors, selected, draft}, editing(), onPick(x), onPlace({lat,lon,bearing})}
    init(canvas, o) { cv = canvas; opts = o; bind(); },
    show(d, keepView) { const same = data && d && data.origin && d.origin && data.origin[0] === d.origin[0] && data.origin[1] === d.origin[1]; data = d; if (!(keepView && same && view)) fit(); draw(); },
    setCursor(x) { cursor = x; draw(); },
    redraw() { draw(); },
    clear() { data = null; cursor = null; if (cv) { const [g, w, h] = size(); g.clearRect(0, 0, w, h); } },
  };
})();
