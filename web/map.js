// コース図（自前の canvas）。選んだ周の走った線、コントロールライン・セクターの線、カーソルの位置の点。
// 図を押すと、基準の周のいちばん近い所へグラフのカーソルを動かす。線を直すときは、押した所へ線を置く。
// 座標は、重ね合わせの返事の origin（基準の周の頭の位置）を原点にした m（東が x、北が y）。
// 航空写真（国土地理院のタイル）を下に敷ける。タイルは裏の /tiles/gsi/ から読み（裏が手元に残す）、
// 四隅を同じ座標へ直して置く（コースの広さなら、平面に直した誤差は1 mより小さい）。
// コース図（道路の両側の縁。裏の outline.py が国土地理院のデータから作る）を、周の線の下に薄く描ける。
'use strict';

const CourseMap = (() => {
  const M = 111320.0, PAD = 16;
  let cv = null, data = null, opts = null, view = null, cursor = null, drag = null, moved = false;
  const tiles = new Map();      // 'z/x/y' → {img, state: 'loading'|'ok'|'ng'}
  let photoNote = null, pending = false, hover = null, paintNow = null;
  let outline = null, outlineNote = null;   // コース図 {lines: [[[lat, lon], …]]} と、作っている・作れないときの知らせ

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

  // ---------------------------------------------------------------- 線を値で塗る（仕様 analysis_map_features.md 3-1）
  // 値は1方向の段（青ひと色、薄い→濃い。暗い地では濃い→薄い）、タイムの差は0を真ん中にした両方向の段（得は青・損は赤）
  const SEQ = { light: ['#9ec5f4', '#5598e7', '#256abf', '#184f95', '#0d366b'], dark: ['#1c5cab', '#3987e5', '#6da7ec', '#9ec5f4', '#cde2fb'] };
  const DIV = { light: ['#1c5cab', '#e4e3df', '#c62f2e'], dark: ['#5598e7', '#d9d8d3', '#ee7a79'] };
  const BAD = '#9ca3af';
  const MARK_NAMES = { on: '▼ ブレーキの踏み始め', off: '▲ ブレーキを離した所', min: '● いちばん低い所', max: '○ いちばん高い所' };

  // コース図：道路の縁だけを薄く（周の線がコース幅のどこを通ったかを読むための下地）
  const ROAD_MIN_PX = 9;           // コース図の道の帯は、引いて見てもこの太さ（画素）より細くしない
  const ZEBRA_MIN_PX = 5;          // ゼブラも、この太さより細くしない
  // ゼブラ（赤白の縁石）の塗り：赤の斜めの縞を薄く
  const zebraFill = {};
  function zebra(g, photo, dark) {
    const k = photo ? 'p' : dark ? 'd' : 'l';
    if (!zebraFill[k]) {
      const c = document.createElement('canvas'); c.width = c.height = 8;
      const q = c.getContext('2d');
      q.fillStyle = photo ? 'rgba(255, 255, 255, 0.4)' : dark ? 'rgba(229, 231, 235, 0.22)' : 'rgba(255, 255, 255, 0.7)';
      q.fillRect(0, 0, 8, 8);
      q.strokeStyle = photo ? 'rgba(239, 68, 68, 0.9)' : dark ? 'rgba(248, 113, 113, 0.7)' : 'rgba(220, 38, 38, 0.55)';
      q.lineWidth = 3;
      q.beginPath(); q.moveTo(-2, 10); q.lineTo(10, -2); q.moveTo(-2, 2); q.lineTo(2, -2); q.moveTo(6, 10); q.lineTo(10, 6); q.stroke();
      zebraFill[k] = g.createPattern(c, 'repeat');
    }
    return zebraFill[k];
  }

  function drawOutline(g, w, h, photo) {
    if (!outline || !outline.lines) return;
    const dark = isDark();
    const path = (pts) => { g.beginPath(); pts.forEach((q, i) => { const [px, py] = P(...toXY(q[0], q[1]), w, h); if (i) g.lineTo(px, py); else g.moveTo(px, py); }); };
    g.save();
    g.lineJoin = 'round'; g.lineCap = 'round';
    if (photo) {                       // 写真の上：暗い縁取りの上に白
      g.strokeStyle = 'rgba(0, 0, 0, 0.45)'; g.lineWidth = 3.5;
      for (const ln of outline.lines) { path(ln); g.stroke(); }
    }
    // 引いて見るとコースの幅が数画素になり、縁が周の線の下に隠れる。そのときは縁を太らせて道の帯にする
    const roadPx = ((outline.width_m && outline.width_m[1]) || 12) * scale(w, h);
    const fat = !photo && roadPx < ROAD_MIN_PX;
    g.strokeStyle = photo ? 'rgba(255, 255, 255, 0.9)' : fat ? (dark ? '#4b5563' : '#d4d9e0') : (dark ? '#6b7280' : '#a3abb6');
    g.lineWidth = photo ? 1.5 : fat ? ROAD_MIN_PX - roadPx + 2 : 2;
    for (const ln of outline.lines) { path(ln); g.stroke(); }
    if (outline.kerbs && outline.kerbs.length) {
      // 縁石の幅は1 mほど。細く見えるときは縁取りで太らせて、最低 ZEBRA_MIN_PX の縞にする
      const fill = zebra(g, photo, dark), pad = ZEBRA_MIN_PX - 1.2 * scale(w, h);
      g.fillStyle = fill; g.strokeStyle = fill; g.lineWidth = pad;
      for (const k of outline.kerbs) { path(k); g.closePath(); g.fill(); if (pad > 0) g.stroke(); }
    }
    g.restore();
  }

  function paintOpts() { return (opts.paint && opts.paint()) || { mode: 'lap' }; }
  function isDark() {
    const c = css('--bg').replace('#', '');
    return c.length === 6 && (parseInt(c.slice(0, 2), 16) + parseInt(c.slice(2, 4), 16) + parseInt(c.slice(4, 6), 16)) / 3 < 128;
  }
  // 塗る周：値なら基準の周、タイムの差なら比べる周（基準の次に選んだ周）
  function target(p) {
    if (p.mode === 'value') return data.laps.find((l) => l.ref && l.track) || null;
    if (p.mode === 'rate' || p.mode === 'delta') return data.laps.find((l) => !l.ref && l.track) || null;
    return null;
  }
  function series(lap, p) {
    const tr = lap.track;
    if (p.mode === 'value') return tr.v || null;
    if (p.mode === 'rate') return tr.rate ? tr.rate.map((v) => (v === null ? null : v * 1000)) : null;   // ms/m
    if (p.mode === 'delta') return tr.dd || null;
    return null;
  }
  function quantile(sorted, q) {
    const f = (sorted.length - 1) * q, i = Math.floor(f);
    return i + 1 < sorted.length ? sorted[i] + (sorted[i + 1] - sorted[i]) * (f - i) : sorted[i];
  }
  // 色の段の範囲 [低, 高]。ふだんは周の中の 2〜98%。手で入れた数があればそれ。タイムの差は0を挟む
  function range(vals, p) {
    const v = vals.filter((x) => x !== null && isFinite(x)).sort((a, b) => a - b);
    if (!v.length) return null;
    let lo = quantile(v, 0.02), hi = quantile(v, 0.98);
    if (p.mode !== 'value') { const m = Math.max(Math.abs(lo), Math.abs(hi), 1e-6); lo = -m; hi = m; }
    if (p.lo !== null && p.lo !== undefined && isFinite(p.lo)) lo = p.lo;
    if (p.hi !== null && p.hi !== undefined && isFinite(p.hi)) hi = p.hi;
    if (p.mode !== 'value') { lo = Math.min(lo, -1e-6); hi = Math.max(hi, 1e-6); }
    if (!(hi > lo)) hi = lo + 1e-6;
    return [lo, hi];
  }
  function hex(c) { return [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)); }
  function mix(stops, f) {
    f = Math.max(0, Math.min(1, f)) * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(f)), a = hex(stops[i]), b = hex(stops[i + 1]), u = f - i;
    return `rgb(${a.map((x, k) => Math.round(x + (b[k] - x) * u)).join(',')})`;
  }
  // 段の中の位置（0〜1）。両方向の段は0が0.5
  function frac(kind, v, rng) {
    if (kind === 'seq') return (v - rng[0]) / (rng[1] - rng[0]);
    return v < 0 ? 0.5 - 0.5 * Math.min(1, v / rng[0]) : 0.5 + 0.5 * Math.min(1, v / rng[1]);
  }
  const STEPS = 48;          // 色を段に丸める（同じ色の線をまとめて描くため）
  function rampColor(kind, a, b, rng, dark) {
    const v = a === null ? b : b === null ? a : (a + b) / 2;
    if (v === null || v === undefined || !isFinite(v)) return BAD;
    const f = Math.round(frac(kind, v, rng) * STEPS) / STEPS;
    return mix(kind === 'seq' ? SEQ[dark ? 'dark' : 'light'] : DIV[dark ? 'dark' : 'light'], f);
  }

  // 周の線。o：{width, color(i) 点 i から i+1 の色, casing 縁の色, alpha, dotted 減速を点線}。測位の質が悪い所は細いグレー
  function strokeLap(g, lap, w, h, o) {
    const tr = lap.track, n = tr.x.length, pts = new Array(n);
    for (let i = 0; i < n; i++) pts[i] = tr.x[i] === null || tr.y[i] === null ? null : P(tr.x[i], tr.y[i], w, h);
    g.lineJoin = 'round';
    const bad = (i) => !!(tr.bad && tr.bad[i]);      // 点 i の印は、i から次の点までの線のこと
    if (o.casing) {
      g.setLineDash([]); g.lineCap = 'round'; g.strokeStyle = o.casing; g.lineWidth = o.width + 2.5;
      g.beginPath();
      for (let i = 0; i < n - 1; i++) {
        if (!pts[i] || !pts[i + 1] || bad(i)) continue;
        g.moveTo(...pts[i]); g.lineTo(...pts[i + 1]);
      }
      g.stroke();
    }
    g.globalAlpha = o.alpha || 1;
    let along = 0, key = null, start = 0;
    const flush = () => { if (key !== null) g.stroke(); key = null; };
    for (let i = 0; i < n - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      if (!a || !b) { flush(); continue; }
      const isBad = bad(i), dashed = !isBad && o.dotted && tr.dec && tr.dec[i] && tr.dec[i + 1];
      const color = isBad ? BAD : o.color(i), k = `${color}|${dashed ? 1 : 0}|${isBad ? 1 : 0}`;
      if (k !== key) {
        flush();
        key = k; start = along;
        g.strokeStyle = color; g.lineWidth = isBad ? 1.5 : o.width;
        g.setLineDash(dashed ? [Math.max(4, o.width * 1.6), Math.max(3, o.width)] : []); g.lineDashOffset = -start;
        g.lineCap = dashed ? 'butt' : 'round';
        g.beginPath(); g.moveTo(...a);
      }
      g.lineTo(...b);
      along += Math.hypot(b[0] - a[0], b[1] - a[1]);
    }
    flush();
    g.setLineDash([]); g.lineDashOffset = 0; g.globalAlpha = 1; g.lineCap = 'butt';
  }

  // 測位の精度の円（点ごとに hacc_m の大きさ）。線のずれが測位のせいか走りのせいかを見分ける
  function drawAccuracy(g, lap, w, h) {
    const tr = lap.track;
    if (!tr || !tr.hacc) return;
    const k = scale(w, h);
    g.fillStyle = opts.color(lap.key); g.globalAlpha = 0.14;
    g.beginPath();
    for (let i = 0; i < tr.x.length; i++) {
      const r = tr.hacc[i] * k;
      if (tr.x[i] === null || !(r >= 0.75)) continue;
      const [px, py] = P(tr.x[i], tr.y[i], w, h);
      g.moveTo(px + r, py); g.arc(px, py, r, 0, 2 * Math.PI);
    }
    g.fill(); g.globalAlpha = 1;
  }

  function markShape(g, kind, px, py, color, ring) {
    const r = 6;
    g.lineWidth = 2; g.strokeStyle = ring; g.fillStyle = color;
    g.beginPath();
    if (kind === 'on') { g.moveTo(px - r, py - r * 0.8); g.lineTo(px + r, py - r * 0.8); g.lineTo(px, py + r * 0.9); g.closePath(); }
    else if (kind === 'off') { g.moveTo(px - r, py + r * 0.8); g.lineTo(px + r, py + r * 0.8); g.lineTo(px, py - r * 0.9); g.closePath(); }
    else g.arc(px, py, r - 1, 0, 2 * Math.PI);
    g.stroke();                                   // 地の色の縁（重なった印を分ける）
    if (kind === 'max') { g.fillStyle = ring; g.fill(); g.lineWidth = 2.5; g.strokeStyle = color; g.beginPath(); g.arc(px, py, r - 2.5, 0, 2 * Math.PI); g.stroke(); }
    else g.fill();
  }
  const LABEL_K = 0.8;       // 印に数を添えるのは、1 m が 0.8 画素より大きく見えるときだけ（引いた図では数が重なって読めない）
  // 周の名前の短い形（走行の名前を除いた「33周」）。同じ周の番号が2つの走行にあれば長い名前のまま
  function short(lap) {
    const s = lap.label.split(' ').pop();
    return data.laps.filter((l) => l.label.split(' ').pop() === s).length > 1 ? lap.label : s;
  }
  function num(v) { return v === null || v === undefined ? '' : Math.abs(v) >= 10 ? String(Math.round(v)) : v.toFixed(2); }
  // 周ごとの印（踏み始め▼・離した所▲・いちばん低い●・高い○）と数。数は周ごとに少しずらして重なりにくく
  function drawMarks(g, lap, w, h, photo, n) {
    if (!lap.track || !lap.marks) return;
    const color = opts.color(lap.key), ring = photo ? '#111827' : css('--panel');
    g.font = '11px "Yu Gothic UI", "Segoe UI", sans-serif'; g.textAlign = 'left'; g.textBaseline = 'middle';
    const labels = scale(w, h) >= LABEL_K;
    for (const m of lap.marks) {
      const [px, py] = P(m.x, m.y, w, h);
      markShape(g, m.k, px, py, color, ring);
      const text = num(m.v);
      if (!text || !labels) continue;
      const ty = py + (n % 4) * 12 - 6;
      g.lineWidth = 3; g.strokeStyle = photo ? 'rgba(0, 0, 0, 0.8)' : css('--panel'); g.strokeText(text, px + 9, ty);
      g.fillStyle = photo ? '#ffffff' : css('--text'); g.fillText(text, px + 9, ty);
    }
  }

  // 凡例（左下、縮尺の上）
  function drawLegend(g, w, h, photo) {
    const { p, tgt, vals, rng, dark } = paintNow;
    const rows = [];
    const any = (k) => data.laps.some((l) => l.track && l.track[k] && l.track[k].some((x) => x));
    if (p.mode !== 'lap') {
      if (!tgt) rows.push({ text: p.mode === 'value' ? '基準の周の測位が足りず塗れません' : '比べる周がありません（周を2つ以上選ぶ）' });
      else if (!vals || !rng) rows.push({ text: p.mode === 'value' ? `「${p.chLabel || p.ch}」の値がありません${tgt.paint_error ? `（${tgt.paint_error}）` : ''}` : 'タイムの差を出せません' });
      else {
        const ref = data.laps.find((l) => l.ref), vs = `${short(tgt)}（基準 ${ref ? short(ref) : ''} との差）`;
        const title = p.mode === 'value' ? `${p.chLabel || p.ch}${p.unit ? `（${p.unit}）` : ''}・${short(tgt)}（基準）`
          : p.mode === 'rate' ? `タイムの差の増え方（ms/m）・${vs}` : `タイムの差（秒）・${vs}`;
        rows.push({ text: title });
        rows.push({ ramp: p.mode === 'value' ? 'seq' : 'div' });
      }
    }
    if (any('bad')) rows.push({ sample: 'bad', text: '測位の質が悪い所' });
    if (p.dotted && any('dec')) rows.push({ sample: 'dot', text: '減速（前後G < −0.1 G）' });
    if (p.marks) rows.push({ text: `▼踏み始め ▲離した所 ●低い ○高い（${p.markLabel || '車速'}）${scale(w, h) < LABEL_K ? '　近づけると数が出ます' : ''}` });
    if (!rows.length) return;
    g.font = '11px "Yu Gothic UI", "Segoe UI", sans-serif';
    const lh = 16, rw = 150;
    const bw = Math.max(rw + 70, ...rows.map((r) => (r.text ? g.measureText(r.text).width + (r.sample ? 30 : 0) : 0))) + 12;
    const bh = rows.reduce((s, r) => s + (r.ramp ? 24 : lh), 0) + 8;
    let x = PAD - 4, y = h - PAD - 26 - bh;
    g.fillStyle = photo ? 'rgba(0, 0, 0, 0.6)' : css('--panel'); g.globalAlpha = photo ? 1 : 0.9;
    g.fillRect(x, y, bw, bh); g.globalAlpha = 1;
    const ink = photo ? '#ffffff' : css('--text'), ink2 = photo ? '#e5e7eb' : css('--muted');
    x += 6; y += 4;
    g.textAlign = 'left'; g.textBaseline = 'middle';
    for (const r of rows) {
      if (r.ramp) {
        for (let i = 0; i < rw; i++) { g.fillStyle = mix(r.ramp === 'seq' ? SEQ[dark ? 'dark' : 'light'] : DIV[dark ? 'dark' : 'light'], i / (rw - 1)); g.fillRect(x + i, y + 2, 1, 8); }
        g.fillStyle = ink2; g.textBaseline = 'top';
        const f = (v) => (p.mode === 'delta' ? (v > 0 ? '+' : '') + v.toFixed(2) : p.mode === 'rate' ? (v > 0 ? '+' : '') + v.toFixed(1) : num(v));
        g.textAlign = 'left'; g.fillText(p.mode === 'value' ? f(rng[0]) : `得 ${f(rng[0])}`, x, y + 11);
        g.textAlign = 'right'; g.fillText(p.mode === 'value' ? f(rng[1]) : `${f(rng[1])} 損`, x + rw, y + 11);
        if (p.mode !== 'value') { g.textAlign = 'center'; g.fillText('0', x + rw / 2, y + 11); }
        g.textAlign = 'left'; g.textBaseline = 'middle';
        y += 24;
        continue;
      }
      let tx = x;
      if (r.sample) {
        g.strokeStyle = r.sample === 'bad' ? BAD : ink; g.lineWidth = r.sample === 'bad' ? 1.5 : 3;
        g.setLineDash(r.sample === 'dot' ? [5, 3] : []);
        g.beginPath(); g.moveTo(x, y + lh / 2); g.lineTo(x + 24, y + lh / 2); g.stroke(); g.setLineDash([]);
        tx = x + 30;
      }
      g.fillStyle = r === rows[0] && !r.sample ? ink : ink2; g.fillText(r.text, tx, y + lh / 2);
      y += lh;
    }
  }

  // カーソルを当てた所の値（吹き出し）
  function hoverText() {
    const { lap, i, mark } = hover, tr = lap.track, p = paintNow.p, out = [short(lap) + (lap.ref ? '（基準）' : '')];
    if (mark) {
      out.push(`${MARK_NAMES[mark.k]}${mark.v !== null ? `　${num(mark.v)}${mark.k === 'on' || mark.k === 'off' ? ' km/h' : p.markUnit ? ' ' + p.markUnit : ''}` : ''}`);
      out.push(`${Math.round(mark.s)} m 地点`);
      return out;
    }
    if (tr.s[i] !== null) out.push(`${Math.round(tr.s[i])} m 地点`);
    if (tr.v && tr.v[i] !== null && tr.v[i] !== undefined) out.push(`${p.chLabel || p.ch}　${num(tr.v[i])}${p.unit ? ' ' + p.unit : ''}`);
    if (!lap.ref && tr.dd && tr.dd[i] !== null) {
      out.push(`タイムの差　${tr.dd[i] > 0 ? '+' : ''}${tr.dd[i].toFixed(3)} 秒`);
      if (tr.rate && tr.rate[i] !== null) out.push(`増え方　${tr.rate[i] > 0 ? '+' : ''}${(tr.rate[i] * 1000).toFixed(1)} ms/m（${tr.rate[i] > 0 ? '損' : tr.rate[i] < 0 ? '得' : '同じ'}）`);
    }
    if (tr.hacc && tr.hacc[i] !== null) out.push(`測位の精度　±${tr.hacc[i].toFixed(2)} m${tr.bad && tr.bad[i] ? '（悪い）' : ''}`);
    return out;
  }
  function drawHover(g, w, h) {
    const lines = hoverText(), tr = hover.lap.track;
    const [px, py] = hover.mark ? P(hover.mark.x, hover.mark.y, w, h) : P(tr.x[hover.i], tr.y[hover.i], w, h);
    g.strokeStyle = opts.color(hover.lap.key); g.lineWidth = 2;
    g.beginPath(); g.arc(px, py, 8, 0, 2 * Math.PI); g.stroke();
    g.font = '12px "Yu Gothic UI", "Segoe UI", sans-serif';
    const bw = Math.max(...lines.map((s) => g.measureText(s).width)) + 16, bh = lines.length * 17 + 8;
    let bx = px + 14, by = py - bh / 2;
    if (bx + bw > w - 4) bx = px - 14 - bw;
    by = Math.max(4, Math.min(h - bh - 4, by));
    g.fillStyle = css('--panel'); g.strokeStyle = css('--line'); g.lineWidth = 1;
    g.fillRect(bx, by, bw, bh); g.strokeRect(bx + 0.5, by + 0.5, bw - 1, bh - 1);
    g.textAlign = 'left'; g.textBaseline = 'top';
    lines.forEach((s, k) => { g.fillStyle = k === 0 ? css('--text') : css('--muted'); g.fillText(s, bx + 8, by + 5 + k * 17); });
  }
  // 画面の点 (mx, my) にいちばん近い線の点か印（10画素の中）
  function pickHover(mx, my, w, h) {
    let best = null, bd = 64;
    if (paintNow && paintNow.p.marks) {            // 印が近ければ印を先に
      for (const lap of data.laps) for (const m of (lap.track && lap.marks) || []) {
        const [px, py] = P(m.x, m.y, w, h), d = (px - mx) ** 2 + (py - my) ** 2;
        if (d < bd) { bd = d; best = { lap, mark: m }; }
      }
      if (best) return best;
    }
    bd = 100;
    for (const lap of data.laps) {
      const tr = lap.track;
      if (!tr) continue;
      for (let i = 0; i < tr.x.length; i++) {
        if (tr.x[i] === null) continue;
        const [px, py] = P(tr.x[i], tr.y[i], w, h), d = (px - mx) ** 2 + (py - my) ** 2;
        if (d < bd || d === bd && paintNow && lap === paintNow.tgt) { bd = d; best = { lap, i }; }
      }
    }
    return best;
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
    drawOutline(g, w, h, photo);
    const p = paintOpts(), dark = photo || isDark();
    const tgt = target(p), vals = tgt ? series(tgt, p) : null;
    const rng = vals ? range(vals, p) : null;
    paintNow = { p, tgt, vals, rng, dark };
    if (p.acc) for (const lap of data.laps) drawAccuracy(g, lap, w, h);
    // 周の線（塗る周か基準の周を最後に描いて上に出す）
    const top = tgt || data.laps.find((l) => l.ref);
    const order = data.laps.slice().sort((a, b) => (a === top ? 1 : 0) - (b === top ? 1 : 0));
    for (const lap of order) {
      if (!lap.track) continue;
      if (lap === tgt && vals && rng) {
        strokeLap(g, lap, w, h, { width: 4, casing: dark ? 'rgba(0, 0, 0, 0.6)' : 'rgba(55, 65, 81, 0.5)', dotted: p.dotted,
                                  color: (i) => rampColor(p.mode === 'value' ? 'seq' : 'div', vals[i], vals[i + 1], rng, dark) });
      } else {
        const c = opts.color(lap.key), faint = !!tgt;
        strokeLap(g, lap, w, h, { width: faint ? 1 : lap.ref ? 2 : 1.5, casing: photo ? 'rgba(0, 0, 0, 0.55)' : null,
                                  alpha: faint ? 0.75 : 1, dotted: p.dotted, color: () => c });
      }
    }
    if (p.marks) for (const lap of data.laps) drawMarks(g, lap, w, h, photo, data.laps.indexOf(lap));
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
    drawLegend(g, w, h, photo);
    if (hover) drawHover(g, w, h);
    // 縮尺
    const k = scale(w, h), m = [10, 20, 50, 100, 200, 500, 1000].find((v) => v * k >= 60) || 1000;
    g.strokeStyle = photo ? '#ffffff' : css('--muted'); g.fillStyle = g.strokeStyle; g.lineWidth = 2;
    g.beginPath(); g.moveTo(PAD, h - PAD); g.lineTo(PAD + m * k, h - PAD); g.stroke();
    g.font = '11px "Yu Gothic UI", sans-serif'; g.textAlign = 'left'; g.textBaseline = 'bottom';
    g.fillText(m >= 1000 ? `${m / 1000}km` : `${m}m`, PAD, h - PAD - 4);
    // 写真・コース図の出典（地理院のデータの決まり）と、読めない・作っているときの知らせ
    const hasOutline = !!(outline && outline.lines && outline.lines.length);
    if (photo || hasOutline) {
      g.font = '10px "Yu Gothic UI", sans-serif'; g.textAlign = 'right'; g.textBaseline = 'bottom';
      const src = photo ? (hasOutline ? '出典：国土地理院（コース図は加工して作成）' : '出典：国土地理院') : 'コース図：国土地理院のデータを加工して作成';
      const tw = g.measureText(src).width;
      g.fillStyle = photo ? 'rgba(0, 0, 0, 0.55)' : css('--panel'); g.fillRect(w - tw - 10, h - 17, tw + 10, 17);
      g.fillStyle = photo ? '#ffffff' : css('--muted'); g.fillText(src, w - 5, h - 3);
    }
    const note = (photo && photoNote) || outlineNote;
    if (note) {
      g.textAlign = 'center'; g.textBaseline = 'top'; g.font = '12px "Yu Gothic UI", sans-serif';
      g.fillStyle = photo ? '#fde68a' : css('--muted'); g.fillText(note, w / 2, 8);
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
    cv.addEventListener('mouseleave', () => { if (hover) { hover = null; draw(); } });
    cv.addEventListener('mousemove', (e) => {
      if (!drag) {
        if (!data || !data.origin) return;
        const r = cv.getBoundingClientRect(), was = hover;
        hover = pickHover(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
        const same = (a, b) => (a && b ? a.lap === b.lap && a.i === b.i && a.mark === b.mark : a === b);
        if (!same(was, hover)) later();
        return;
      }
      hover = null;
      const r = cv.getBoundingClientRect(), k = scale(r.width, r.height);
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) moved = true;
      if (!moved) return;
      view.cx = drag.cx - (e.clientX - drag.x) / k; view.cy = drag.cy + (e.clientY - drag.y) / k; draw();
    });
    cv.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = cv.getBoundingClientRect();
      const [x, y] = U(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
      const span = Math.max(20, view.span * (e.deltaY > 0 ? 1.25 : 0.8)), f = span / view.span;   // 寄りきったら中心も動かさない
      view.span = span;
      view.cx = x + (view.cx - x) * f; view.cy = y + (view.cy - y) * f;
      draw();
    }, { passive: false });
    cv.addEventListener('dblclick', () => { fit(); draw(); });
  }

  return {
    // o：{x() 'dist'|'time', color(key), lines() {line, sectors, selected, draft}, editing(), onPick(x), onPlace({lat,lon,bearing}), photo() 航空写真を敷くか,
    //     paint() {mode 'lap'|'value'|'rate'|'delta', ch, chLabel, unit, lo, hi 段の範囲を手で（null は自動）, dotted, acc, marks, markLabel, markUnit}}
    init(canvas, o) { cv = canvas; opts = o; bind(); },
    show(d, keepView) { const same = data && d && data.origin && d.origin && data.origin[0] === d.origin[0] && data.origin[1] === d.origin[1]; data = d; hover = null; if (!(keepView && same && view)) fit(); draw(); },
    // いま塗っている段の範囲（自動のときの数を入れ欄の見本に出すため）
    paintRange() { return paintNow && paintNow.rng; },
    setCursor(x) { cursor = x; draw(); },
    // コース図（null で消す）と、図の上に出す知らせ（作っている・作れない）
    setOutline(o, note) { outline = o || null; outlineNote = note || null; draw(); },
    redraw() { draw(); },
    clear() { data = null; cursor = null; hover = null; if (cv) { const [g, w, h] = size(); g.clearRect(0, 0, w, h); } },
  };
})();
