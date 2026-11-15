// コース図（自前の canvas）。選んだ周の走った線、コントロールライン・セクターの線、カーソルの位置の点。
// 図を押すと、基準の周のいちばん近い所へグラフのカーソルを動かす。線を直すときは、押した所へ線を置く。
// 座標は、重ね合わせの返事の origin（基準の周の頭の位置）を原点にした m（東が x、北が y）。
// 航空写真（国土地理院のタイル）を下に敷ける。タイルは裏の /tiles/gsi/ から読み（裏が手元に残す）、
// 四隅を同じ座標へ直して置く（コースの広さなら、平面に直した誤差は1 mより小さい）。
'use strict';

const CourseMap = (() => {
  const M = 111320.0, PAD = 16;
  let cv = null, data = null, opts = null, view = null, cursor = null, drag = null, moved = false;
  const tiles = new Map();      // 'z/x/y' → {img, state: 'loading'|'ok'|'ng'}
  let photoNote = null, pending = false;

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

  // ---------------------------------------------------------------- 航空写真
  const TILE_MAX_Z = 18, TILE_MIN_Z = 2, MAX_TILES = 120;
  function tileLon(x, z) { return x / 2 ** z * 360 - 180; }
  function tileLat(y, z) { const n = Math.PI * (1 - 2 * y / 2 ** z); return Math.atan(Math.sinh(n)) * 180 / Math.PI; }
  function lonTile(lon, z) { return (lon + 180) / 360 * 2 ** z; }
  function latTile(lat, z) { const r = lat * Math.PI / 180; return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * 2 ** z; }
  function later() { if (pending) return; pending = true; requestAnimationFrame(() => { pending = false; draw(); }); }
  function tile(z, x, y) {
    const key = `${z}/${x}/${y}`;
    let t = tiles.get(key);
    if (!t) {
      const img = new Image();
      t = { img, state: 'loading' };
      img.onload = () => { t.state = 'ok'; later(); };
      img.onerror = () => { t.state = 'ng'; later(); };
      img.src = `/tiles/gsi/${key}.jpg`;
      tiles.set(key, t);
      if (tiles.size > 1500) tiles.delete(tiles.keys().next().value);     // 古いものから忘れる
    }
    return t;
  }
  // タイル (z, x, y) の範囲を、図の上の四角 [左, 上, 幅, 高さ] に
  function tileRect(z, x, y, w, h) {
    const [ax, ay] = P(...toXY(tileLat(y, z), tileLon(x, z)), w, h);
    const [bx, by] = P(...toXY(tileLat(y + 1, z), tileLon(x + 1, z)), w, h);
    return [ax, ay, bx - ax, by - ay];
  }
  function drawPhoto(g, w, h) {
    const dpr = window.devicePixelRatio || 1, k = scale(w, h), lat0 = data.origin[0];
    // 写真の1画素が画面の1画素くらいになるズーム（256画素のタイル）
    let z = Math.ceil(Math.log2(156543.03392 * Math.cos(lat0 * Math.PI / 180) * k * dpr));
    z = Math.max(TILE_MIN_Z, Math.min(TILE_MAX_Z, z));
    const [lat1, lon1] = toLatLon(...U(0, 0, w, h)), [lat2, lon2] = toLatLon(...U(w, h, w, h));
    let x0, x1, y0, y1;
    for (;;) {
      x0 = Math.floor(lonTile(lon1, z)); x1 = Math.floor(lonTile(lon2, z));
      y0 = Math.floor(latTile(lat1, z)); y1 = Math.floor(latTile(lat2, z));
      if ((x1 - x0 + 1) * (y1 - y0 + 1) <= MAX_TILES || z <= TILE_MIN_Z) break;
      z--;
    }
    let ok = 0, ng = 0;
    g.imageSmoothingQuality = 'high';
    for (let x = x0; x <= x1; x++) {
      for (let y = y0; y <= y1; y++) {
        const t = tile(z, x, y), [rx, ry, rw, rh] = tileRect(z, x, y, w, h);
        if (t.state === 'ok') { g.drawImage(t.img, rx, ry, rw + 0.5, rh + 0.5); ok++; continue; }
        if (t.state === 'ng') ng++;
        // 読めるまでは、手元にある粗いタイルの一部を引き伸ばして出す
        for (let d = 1; d <= 4 && z - d >= TILE_MIN_Z; d++) {
          const p = tiles.get(`${z - d}/${x >> d}/${y >> d}`);
          if (!p || p.state !== 'ok') continue;
          const n = 2 ** d, sw = 256 / n, sx = (x - ((x >> d) << d)) * sw, sy = (y - ((y >> d) << d)) * sw;
          g.drawImage(p.img, sx, sy, sw, sw, rx, ry, rw + 0.5, rh + 0.5);
          break;
        }
      }
    }
    // 線が見えるよう、写真を少し暗くする
    g.fillStyle = 'rgba(0, 0, 0, 0.28)';
    g.fillRect(0, 0, w, h);
    photoNote = ok === 0 && ng > 0 ? '航空写真を読めません（初めて見る所はネットが要ります）' : null;
  }

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
      g.fillStyle = color === css('--muted') || color === css('--text') ? css('--text') : color; g.font = '11px "Yu Gothic UI", "Segoe UI", sans-serif'; g.textAlign = 'left'; g.textBaseline = 'middle';
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
    const photo = !!(opts.photo && opts.photo());
    if (photo) drawPhoto(g, w, h);
    // 周の線（基準の周を最後に描いて上に出す）
    const order = data.laps.slice().sort((a, b) => (a.ref ? 1 : 0) - (b.ref ? 1 : 0));
    for (const lap of order) {
      if (!lap.track) continue;
      g.lineJoin = 'round';
      g.beginPath();
      let pen = false;
      for (let i = 0; i < lap.track.x.length; i++) {
        const x = lap.track.x[i], y = lap.track.y[i];
        if (x === null || y === null) { pen = false; continue; }
        const [px, py] = P(x, y, w, h);
        if (pen) g.lineTo(px, py); else { g.moveTo(px, py); pen = true; }
      }
      const lw = lap.ref ? 2 : 1.5;
      if (photo) { g.strokeStyle = 'rgba(0, 0, 0, 0.55)'; g.lineWidth = lw + 2.5; g.stroke(); }   // 写真の上では縁取る
      g.strokeStyle = opts.color(lap.key); g.lineWidth = lw; g.stroke();
    }
    // 線
    const L = opts.lines();
    const ink = photo ? '#ffffff' : css('--text'), ink2 = photo ? '#e5e7eb' : css('--muted');
    if (photo) { g.shadowColor = 'rgba(0, 0, 0, 0.9)'; g.shadowBlur = 3; }
    if (L && L.line) drawLine(g, L.line, w, h, ink, 2.5, 'コントロールライン', L.draft);
    (L && L.sectors || []).forEach((s, i) => drawLine(g, s, w, h, ink2, 2, `S${i + 1}｜S${i + 2}`, L.draft));
    g.shadowBlur = 0; g.shadowColor = 'transparent';
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
    g.strokeStyle = photo ? '#ffffff' : css('--muted'); g.fillStyle = g.strokeStyle; g.lineWidth = 2;
    g.beginPath(); g.moveTo(PAD, h - PAD); g.lineTo(PAD + m * k, h - PAD); g.stroke();
    g.font = '11px "Yu Gothic UI", sans-serif'; g.textAlign = 'left'; g.textBaseline = 'bottom';
    g.fillText(m >= 1000 ? `${m / 1000}km` : `${m}m`, PAD, h - PAD - 4);
    // 写真の出典（地理院タイルの決まり）と、読めないときの知らせ
    if (photo) {
      g.font = '10px "Yu Gothic UI", sans-serif'; g.textAlign = 'right'; g.textBaseline = 'bottom';
      const src = '出典：国土地理院', tw = g.measureText(src).width;
      g.fillStyle = 'rgba(0, 0, 0, 0.55)'; g.fillRect(w - tw - 10, h - 17, tw + 10, 17);
      g.fillStyle = '#ffffff'; g.fillText(src, w - 5, h - 3);
      if (photoNote) { g.textAlign = 'center'; g.textBaseline = 'top'; g.font = '12px "Yu Gothic UI", sans-serif'; g.fillStyle = '#fde68a'; g.fillText(photoNote, w / 2, 8); }
    }
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
    // o：{x() 'dist'|'time', color(key), lines() {line, sectors, selected, draft}, editing(), onPick(x), onPlace({lat,lon,bearing}), photo() 航空写真を敷くか}
    init(canvas, o) { cv = canvas; opts = o; bind(); },
    show(d, keepView) { const same = data && d && data.origin && d.origin && data.origin[0] === d.origin[0] && data.origin[1] === d.origin[1]; data = d; if (!(keepView && same && view)) fit(); draw(); },
    setCursor(x) { cursor = x; draw(); },
    redraw() { draw(); },
    clear() { data = null; cursor = null; if (cv) { const [g, w, h] = size(); g.clearRect(0, 0, w, h); } },
  };
})();
