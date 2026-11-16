// コース図（自前の canvas）。選んだ周の走った線、コントロールライン・セクターの線、カーソルの位置の点。
// 図を押すと、基準の周のいちばん近い所へグラフのカーソルを動かす（車を追っているときは、車から離れている分だけ線に沿って先へ送る）。線を直すときは、押した所へ線を置く。
// 座標は、重ね合わせの返事の origin（基準の周の頭の位置）を原点にした m（東が x、北が y）。
// 航空写真（国土地理院のタイル）を下に敷ける。タイルは裏の /tiles/gsi/ から読み（裏が手元に残す）、
// 四隅を同じ座標へ直して置く（コースの広さなら、平面に直した誤差は1 mより小さい）。
// コース図（道路の両側の縁。裏の outline.py が国土地理院のデータから作る）を、周の線の下に薄く描ける。
// コース図を手で直すとき（outedit.js）は、その中身を描き、マウスをエディタへ渡す。
'use strict';

const CourseMap = (() => {
  const M = 111320.0, PAD = 16;
  let cv = null, data = null, opts = null, view = null, cursor = null, drag = null, moved = false;
  const tiles = new Map();      // 'z/x/y' → {img, state: 'loading'|'ok'|'ng'}
  let photoNote = null, pending = false, hover = null, paintNow = null;
  let highlights = [];                       // 伸びしろの大きい区間 [{a, b（基準の周の距離 m）, label}]（SP-10-41）
  let ruler = null;                          // 物差し {a: [x, y], b: [x, y]}（Shift を押しながら引いているあいだだけ）
  let outline = null, outlineNote = null;   // コース図 {lines: [[[lat, lon], …]]} と、作っている・作れないときの知らせ
  let outlineXY = null, outlineKey = null;  // コース図を m に直したもの（描くたびに直さない）
  let editor = null;                         // コース図のエディタ（OutlineEditor。直していないときは null）
  // 車を追う：オンのあいだは、描くたびに基準の周の車（カーソルの位置）を図の真ん中へ。倍率は変えない。
  // 図を手で引くと、リアルタイムの地図（tools/home_telemetry/map.html）と同じく追うのをやめる
  let follow = false;
  // 道具を選ぶジェスチャー（右ボタンを押したまま引く）。{x, y 押した所（画素）, dx, dy 引いた量}
  let gesture = null;
  const GESTURE_MIN = 24, GESTURE_R = 64;
  // 引いた向き：上を0に時計回りで8つ。引きが足りなければ -1
  function gestureSector(gs) {
    if (!gs || Math.hypot(gs.dx, gs.dy) < GESTURE_MIN) return -1;
    return Math.round(((Math.atan2(gs.dx, -gs.dy) * 180 / Math.PI + 360) % 360) / 45) % 8;
  }
  function drawGesture(g) {
    const items = editor && editor.active() && editor.GESTURE;
    if (!gesture || !items) return;
    const { x, y } = gesture, sel = gestureSector(gesture), cur = editor.tool(), acc = css('--accent') || '#3b82f6';
    g.save();
    g.beginPath(); g.arc(x, y, GESTURE_R + 40, 0, 2 * Math.PI); g.fillStyle = 'rgba(17, 24, 39, 0.55)'; g.fill();
    if (sel >= 0) {                  // 押した所から引いた先へ
      g.strokeStyle = acc; g.lineWidth = 2;
      g.beginPath(); g.moveTo(x, y); g.lineTo(x + gesture.dx, y + gesture.dy); g.stroke();
    }
    g.beginPath(); g.arc(x, y, 4, 0, 2 * Math.PI); g.fillStyle = '#ffffff'; g.fill();
    g.font = '12px "Yu Gothic UI", "Segoe UI", sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
    items.forEach(([key, label], i) => {
      const a = i * Math.PI / 4, px = x + Math.sin(a) * GESTURE_R, py = y - Math.cos(a) * GESTURE_R;
      const text = `${i + 1} ${label}`, tw = g.measureText(text).width + 14, th = 22;
      g.beginPath();
      if (g.roundRect) g.roundRect(px - tw / 2, py - th / 2, tw, th, 11); else g.rect(px - tw / 2, py - th / 2, tw, th);
      g.fillStyle = i === sel ? acc : '#1f2937'; g.fill();
      g.lineWidth = key === cur ? 2 : 1; g.strokeStyle = key === cur ? acc : 'rgba(255, 255, 255, 0.35)'; g.stroke();
      g.fillStyle = '#ffffff'; g.fillText(text, px, py + 1);
    });
    g.restore();
  }
  const FOLLOW_SPAN = 250;                   // 追い始めるとき、全体を見ている倍率ならここまで寄る（m）

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

  function curKey() { return opts.x() === 'dist' ? 's' : 't'; }
  function splitEditing() { return !!(opts.splitEditing && opts.splitEditing()); }
  // 周 lap の車の位置（m）。カーソルが無い・周の外なら null
  function carAt(lap) {
    if (cursor === null || !lap || !lap.track) return null;
    const t = carTime(lap);
    const p = t === undefined ? posAt(lap.track, curKey(), cursor) : t === null ? null : posAt(lap.track, 't', t);
    return p && isFinite(p[0]) && isFinite(p[1]) ? p : null;
  }
  // 周 lap の車を置く時刻（周の頭からの秒。点の置き方「同じ場所・同じ時間」と、そろえる所で決まる。SP-10-16）。
  // opts.lapTime が無ければ undefined（横軸の値でそのまま置く）
  function carTime(lap) { return opts.lapTime ? opts.lapTime(lap, cursor) : undefined; }
  // 追っているなら、図の真ん中を基準の周の車へ（直している・線を置いているとき、車を引いているあいだは動かさない）
  function centerOnCar() {
    if (!follow || (drag && (drag.car || drag.split)) || (editor && editor.active()) || opts.editing() || splitEditing()) return;
    const p = carAt(refLap());
    if (!p) return;
    if (followZoom) { followZoom = false; if (view.span > FOLLOW_SPAN * 2) view.span = FOLLOW_SPAN; }
    view.cx = p[0]; view.cy = p[1];
  }
  let followZoom = false;                    // 次に車が見つかったとき、全体を見ている倍率なら寄る
  function setFollow(on, tell) {
    follow = !!on;
    followZoom = follow;
    if (tell && opts.onFollow) opts.onFollow(follow);
  }

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
    // 倍率が数でないとき（図が描けないほど小さい）は敷かない。NaN のまま下へ進むと、比べがいつも偽になって z-- が終わらない
    if (!Number.isFinite(z)) return;
    z = Math.max(TILE_MIN_Z, Math.min(TILE_MAX_Z, z));
    const [lat1, lon1] = toLatLon(...U(0, 0, w, h)), [lat2, lon2] = toLatLon(...U(w, h, w, h));
    let x0, x1, y0, y1;
    for (;;) {
      x0 = Math.floor(lonTile(lon1, z)); x1 = Math.floor(lonTile(lon2, z));
      y0 = Math.floor(latTile(lat1, z)); y1 = Math.floor(latTile(lat2, z));
      if (!((x1 - x0 + 1) * (y1 - y0 + 1) > MAX_TILES) || z <= TILE_MIN_Z) break;   // 数でない範囲でも抜ける
      z--;
    }
    if (![x0, x1, y0, y1].every(Number.isFinite)) return;
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

  // 伸びしろの大きい区間：基準の周の線の上に太い帯と番号の丸（SP-10-41）
  function drawHighlights(g, w, h, photo) {
    const lap = refLap();
    if (!lap || !highlights.length) return;
    const t = lap.track;
    for (const hl of highlights) {
      g.beginPath();
      let on = false, mid = null, md = Infinity;
      for (let i = 0; i < t.x.length; i++) {
        const s = t.s[i];
        if (t.x[i] === null || s === null || s < hl.a || s > hl.b) { on = false; continue; }
        const [px, py] = P(t.x[i], t.y[i], w, h);
        if (on) g.lineTo(px, py); else { g.moveTo(px, py); on = true; }
        const dm = Math.abs(s - (hl.a + hl.b) / 2);
        if (dm < md) { md = dm; mid = [px, py]; }
      }
      g.lineCap = 'round'; g.lineJoin = 'round';
      g.strokeStyle = photo ? 'rgba(255, 255, 255, 0.38)' : (isDark() ? 'rgba(255, 255, 255, 0.22)' : 'rgba(17, 24, 39, 0.16)'); g.lineWidth = 20; g.stroke();
      g.lineCap = 'butt';
      if (!mid) continue;
      g.beginPath(); g.arc(mid[0], mid[1], 11, 0, Math.PI * 2);
      g.fillStyle = '#111827'; g.fill(); g.lineWidth = 2; g.strokeStyle = '#ffffff'; g.stroke();
      g.fillStyle = '#ffffff'; g.font = 'bold 12px "Yu Gothic UI", "Segoe UI", sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(hl.label, mid[0], mid[1] + 0.5);
    }
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
  const ZEBRA_MIN_PX = 4;          // ゼブラの帯も、この太さより細くしない
  // 描く中身（m）：{lines: [{pts: [[x, y]], closed}], kerbs: [{pts, ws, side} か前の形 {w, pts}], boards: [{x, y, text}]}
  function outlineView() {
    if (editor && editor.active()) return editor.view();
    if (!outline || !outline.lines) return null;
    const key = data.origin.join(',');
    if (outlineXY && outlineKey === key && outlineXY.src === outline) return outlineXY;
    const xy = (pts) => pts.map((q) => toXY(q[0], q[1]));
    outlineXY = { src: outline,
                  lines: outline.lines.map((ln, i) => ({ pts: xy(ln), closed: false, kind: (outline.kinds || [])[i] || 'edge' })),
                  kerbs: (outline.kerbs || []).filter((z) => z && z.pts).map((z) => (Array.isArray(z.ws)
                    ? { pts: xy(z.pts), ws: z.ws, side: z.side < 0 ? -1 : 1 } : { w: z.w || 1, pts: xy(z.pts) })),
                  boards: (outline.boards || []).map((bd) => { const [x, y] = toXY(bd.lat, bd.lon); return { x, y, text: bd.text }; }) };
    outlineKey = key;
    return outlineXY;
  }

  function drawOutline(g, w, h, photo) {
    const o = outlineView();
    if (!o) return;
    const dark = isDark();
    const path = (it) => {
      g.beginPath();
      it.pts.forEach((q, i) => { const [px, py] = P(q[0], q[1], w, h); if (i) g.lineTo(px, py); else g.moveTo(px, py); });
      if (it.closed) g.closePath();
    };
    g.save();
    g.lineJoin = 'round'; g.lineCap = 'round';
    // 線の種類：コースの縁はふだんの線、ピットロードは破線、その他の舗装（使っていないシケイン・エスケープゾーンなど）は点線。
    // 縁だけが引いて見たときの道の帯になる
    const edges = o.lines.filter((ln) => !ln.kind || ln.kind === 'edge'), extra = o.lines.filter((ln) => ln.kind === 'pit' || ln.kind === 'other');
    if (photo) {                       // 写真の上：暗い縁取りの上に白
      g.strokeStyle = 'rgba(0, 0, 0, 0.45)'; g.lineWidth = 3.5;
      for (const ln of edges) { path(ln); g.stroke(); }
    }
    // 引いて見るとコースの幅が数画素になり、縁が周の線の下に隠れる。そのときは縁を太らせて道の帯にする
    const roadPx = ((outline && outline.width_m && outline.width_m[1]) || 12) * scale(w, h);
    const fat = !photo && roadPx < ROAD_MIN_PX;
    g.strokeStyle = photo ? 'rgba(255, 255, 255, 0.9)' : fat ? (dark ? '#4b5563' : '#d4d9e0') : (dark ? '#6b7280' : '#a3abb6');
    g.lineWidth = photo ? 1.5 : fat ? ROAD_MIN_PX - roadPx + 2 : 2;
    for (const ln of edges) { path(ln); g.stroke(); }
    for (const ln of extra) {
      const pit = ln.kind === 'pit';
      if (photo) { g.setLineDash([]); g.strokeStyle = 'rgba(0, 0, 0, 0.45)'; g.lineWidth = 3; path(ln); g.stroke(); }
      g.setLineDash(pit ? [7, 4] : [2, 4]);
      g.strokeStyle = photo ? (pit ? 'rgba(253, 224, 71, 0.95)' : 'rgba(255, 255, 255, 0.8)')
                            : pit ? (dark ? '#d6d3d1' : '#78716c') : (dark ? '#6b7280' : '#a3abb6');
      g.lineWidth = pit ? 1.6 : 1.3;
      path(ln); g.stroke();
    }
    g.setLineDash([]);
    // ゼブラ：縁に沿った帯（幅は点ごと、端は細る）を、走る向きに1 mおきの赤白で（写真では縞を読み切れないので、ある区間だけを描く）
    if (o.kerbs && o.kerbs.length) {
      const k = scale(w, h);
      const white = photo ? 'rgba(255, 255, 255, 0.9)' : dark ? 'rgba(229, 231, 235, 0.5)' : 'rgba(255, 255, 255, 0.95)';
      const red = photo ? 'rgba(239, 68, 68, 0.95)' : dark ? 'rgba(248, 113, 113, 0.85)' : 'rgba(220, 38, 38, 0.7)';
      const rim = !dark && !photo ? 'rgba(156, 163, 175, 0.6)' : null;
      g.lineCap = 'butt';
      for (const z of o.kerbs) {
        if (!z || !z.pts || z.pts.length < 2) continue;
        drawKerb(g, kerbBand(z), k, white, red, rim, w, h);
      }
      g.setLineDash([]);
    }
    g.restore();
  }

  // ゼブラの帯の両側の端（m）：{a 内側, b 外側, wmax}。前の形 {w, pts} は帯の真ん中から両側へ w/2
  function kerbBand(z) {
    const n = z.pts.length, a = [], b = [];
    let wmax = 0;
    for (let i = 0; i < n; i++) {
      const p = z.pts[i], q0 = z.pts[Math.max(0, i - 1)], q1 = z.pts[Math.min(n - 1, i + 1)];
      const L = Math.hypot(q1[0] - q0[0], q1[1] - q0[1]) || 1, nx = -(q1[1] - q0[1]) / L, ny = (q1[0] - q0[0]) / L;
      if (z.ws) {
        const s = (z.side < 0 ? -1 : 1) * (z.ws[i] || 0);
        wmax = Math.max(wmax, Math.abs(s));
        a.push(p); b.push([p[0] + nx * s, p[1] + ny * s]);
      } else {
        const hw = (z.w || 1) / 2;
        wmax = Math.max(wmax, 2 * hw);
        a.push([p[0] - nx * hw, p[1] - ny * hw]); b.push([p[0] + nx * hw, p[1] + ny * hw]);
      }
    }
    return { a, b, wmax };
  }

  // 帯を白で塗り、内側の端に沿った距離で1 mおきに赤を重ねる。引いて見て帯が細いときは、真ん中の線を決まった太さの破線で
  function drawKerb(g, band, k, white, red, rim, w, h) {
    const { a, b } = band, n = a.length;
    if (band.wmax * k < ZEBRA_MIN_PX) {
      const mid = a.map((p, i) => [(p[0] + b[i][0]) / 2, (p[1] + b[i][1]) / 2]);
      const line = () => { g.beginPath(); mid.forEach((q, i) => { const [px, py] = P(q[0], q[1], w, h); if (i) g.lineTo(px, py); else g.moveTo(px, py); }); };
      const dash = Math.max(3, 1.0 * k);
      if (rim) { line(); g.setLineDash([]); g.strokeStyle = rim; g.lineWidth = ZEBRA_MIN_PX + 1.5; g.stroke(); }
      line(); g.setLineDash([]); g.strokeStyle = white; g.lineWidth = ZEBRA_MIN_PX; g.stroke();
      line(); g.setLineDash([dash, dash]); g.strokeStyle = red; g.stroke();
      return;
    }
    const poly = (pts) => { g.beginPath(); pts.forEach((q, i) => { const [px, py] = P(q[0], q[1], w, h); if (i) g.lineTo(px, py); else g.moveTo(px, py); }); g.closePath(); };
    g.setLineDash([]);
    poly(a.concat(b.slice().reverse()));
    g.fillStyle = white; g.fill();
    if (rim) { g.strokeStyle = rim; g.lineWidth = 1; g.stroke(); }
    // 内側の端に沿った距離 S。距離 s の所は、その線分の内側・外側の端を同じ割合で
    const S = [0];
    for (let i = 1; i < n; i++) S.push(S[i - 1] + Math.hypot(a[i][0] - a[i - 1][0], a[i][1] - a[i - 1][1]));
    const L = S[n - 1], step = Math.max(1, 3 / k);       // 縞は1 m（引いて見て3画素より細くなるときは太くする）
    let j = 0;
    const at = (arr, s) => {
      while (j > 0 && S[j] > s) j--;
      while (j < n - 2 && S[j + 1] < s) j++;
      const d = S[j + 1] - S[j], t = d > 0 ? Math.max(0, Math.min(1, (s - S[j]) / d)) : 0;
      return [arr[j][0] + (arr[j + 1][0] - arr[j][0]) * t, arr[j][1] + (arr[j + 1][1] - arr[j][1]) * t];
    };
    g.fillStyle = red;
    for (let s0 = step; s0 < L; s0 += 2 * step) {
      const s1 = Math.min(L, s0 + step), inA = [at(a, s0)], inB = [at(b, s0)];
      for (let i = 0; i < n; i++) if (S[i] > s0 && S[i] < s1) { inA.push(a[i]); inB.push(b[i]); }
      inA.push(at(a, s1)); inB.push(at(b, s1));
      poly(inA.concat(inB.reverse()));
      g.fill();
    }
  }

  // 距離の看板（コーナーの手前の「100」など）：白い板に黒い字。大きさは倍率によらず同じ
  function drawBoards(g, w, h) {
    const o = outlineView();
    if (!o || !o.boards || !o.boards.length) return;
    g.save();
    g.font = 'bold 10px "Yu Gothic UI", sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
    for (const bd of o.boards) {
      const [px, py] = P(bd.x, bd.y, w, h);
      if (px < -40 || py < -20 || px > w + 40 || py > h + 20) continue;
      const tw = Math.max(14, g.measureText(bd.text).width + 6);
      g.fillStyle = '#ffffff'; g.strokeStyle = '#1f2937'; g.lineWidth = 1.2;
      g.beginPath(); g.rect(px - tw / 2, py - 7, tw, 14); g.fill(); g.stroke();
      g.fillStyle = '#111827'; g.fillText(bd.text, px, py + 0.5);
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
  // 車（カーソルの位置）：向き ang（画面の角度。動かないときは右向き）へ尖った矢じり。r は先から真ん中まで（画素）
  function drawCar(g, px, py, ang, color, r, darkBg) {
    if (!isFinite(ang)) ang = 0;
    const c = Math.cos(ang), s = Math.sin(ang);
    const pt = (fx, fy) => [px + (fx * c - fy * s) * r, py + (fx * s + fy * c) * r];
    const shape = [pt(1, 0), pt(-0.75, 0.7), pt(-0.35, 0), pt(-0.75, -0.7)];
    g.save();
    g.beginPath(); g.arc(px, py, r * 1.45, 0, 2 * Math.PI);
    g.fillStyle = darkBg ? 'rgba(255, 255, 255, 0.22)' : 'rgba(17, 24, 39, 0.14)'; g.fill();
    g.beginPath(); shape.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath();
    g.lineJoin = 'round';
    g.lineWidth = 4.5; g.strokeStyle = darkBg ? '#111827' : '#ffffff'; g.stroke();
    g.fillStyle = color; g.fill();
    g.lineWidth = 1.5; g.strokeStyle = darkBg ? '#ffffff' : '#111827'; g.stroke();
    g.restore();
  }
  const LABEL_K = 0.8;      // 印に数を添えるのは、1 m が 0.8 画素より大きく見えるときだけ（引いた図では数が重なって読めない）
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
    if (!lap.ref && tr.off && tr.off[i] !== null && tr.off[i] !== undefined) out.push(`線のずれ　${fmtOff(tr.off[i])}`);
    if (!lap.ref && tr.dd && tr.dd[i] !== null) {
      out.push(`タイムの差　${tr.dd[i] > 0 ? '+' : ''}${tr.dd[i].toFixed(3)} 秒`);
      if (tr.rate && tr.rate[i] !== null) out.push(`増え方　${tr.rate[i] > 0 ? '+' : ''}${(tr.rate[i] * 1000).toFixed(1)} ms/m（${tr.rate[i] > 0 ? '損' : tr.rate[i] < 0 ? '得' : '同じ'}）`);
    }
    if (tr.hacc && tr.hacc[i] !== null) out.push(`測位の精度　±${tr.hacc[i].toFixed(2)} m${tr.bad && tr.bad[i] ? '（悪い）' : ''}`);
    return out;
  }
  function fmtOff(v) { return Math.abs(v) < 0.05 ? '0.0 m' : `${v > 0 ? '右' : '左'} ${Math.abs(v).toFixed(1)} m${Math.abs(v) < 1 ? '（1 m より小さく、読まない）' : ''}`; }
  // 増える一方の配列 track[key] で、x のときの track[name] の値（null をまたぐ・範囲の外は null）
  function valAt(track, key, name, x) {
    const xa = track[key], ya = track[name];
    if (!xa || !ya || !xa.length || x === null || x < xa[0] || x > xa[xa.length - 1]) return null;
    let lo = 0, hi = xa.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xa[m] <= x) lo = m; else hi = m; }
    const a = ya[lo], b = ya[hi];
    if (a === null || b === null || a === undefined || b === undefined) return a ?? b ?? null;
    const d = xa[hi] - xa[lo];
    return d > 0 ? a + (b - a) * (x - xa[lo]) / d : a;
  }
  // カーソルの所の数（SP-10-15）：基準の周の車の横に、周ごとの速さ・横のずれ・タイムの差
  function drawCursorInfo(g, w, h) {
    const key = curKey(), ref = refLap(), p = carAt(ref);
    if (!p) return;
    const rows = [];
    for (const lap of data.laps) {
      if (!lap.track || !carAt(lap)) continue;
      const tr = lap.track, ct = carTime(lap), kk = ct === undefined ? key : 't', xx = ct === undefined ? cursor : ct;
      const sp = valAt(tr, kk, 'sp', xx), parts = [short(lap) + (lap.ref ? '（基準）' : ''), sp === null ? '— km/h' : `${Math.round(sp)} km/h`];
      if (!lap.ref) {
        const off = valAt(tr, kk, 'off', xx), dd = valAt(tr, kk, 'dd', xx);
        parts.push(off === null ? '—' : Math.abs(off) < 0.05 ? '0.0 m' : `${off > 0 ? '右' : '左'} ${Math.abs(off).toFixed(1)} m`);
        parts.push(dd === null ? '—' : Math.abs(dd) < 0.0005 ? '0.000 秒' : `${dd > 0 ? '+' : '−'}${Math.abs(dd).toFixed(3)} 秒`);
      }
      rows.push({ lap, parts });
    }
    if (!rows.length) return;
    g.font = '12px "Yu Gothic UI", "Segoe UI", sans-serif';
    const cols = [0, 1, 2, 3].map((c) => Math.max(0, ...rows.map((r) => (r.parts[c] ? g.measureText(r.parts[c]).width : 0))));
    const gapX = 10, bw = 22 + cols.reduce((a, b) => a + b, 0) + gapX * 3 + 8, bh = rows.length * 17 + 8;
    const [px, py] = P(p[0], p[1], w, h);
    let bx = px + 18, by = py + 14;
    if (bx + bw > w - 4) bx = px - 18 - bw;
    if (by + bh > h - 4) by = py - 14 - bh;
    bx = Math.max(4, bx); by = Math.max(4, by);
    g.fillStyle = css('--panel'); g.globalAlpha = 0.92; g.fillRect(bx, by, bw, bh); g.globalAlpha = 1;
    g.strokeStyle = css('--line'); g.lineWidth = 1; g.strokeRect(bx + 0.5, by + 0.5, bw - 1, bh - 1);
    g.textBaseline = 'top';
    rows.forEach((r, k) => {
      const y = by + 5 + k * 17;
      g.fillStyle = opts.color(r.lap.key); g.fillRect(bx + 8, y + 5, 10, 3);
      let x = bx + 22;
      r.parts.forEach((t, c) => {
        g.fillStyle = c === 0 ? css('--text') : css('--muted');
        if (c === 0) { g.textAlign = 'left'; g.fillText(t, x, y); } else { g.textAlign = 'right'; g.fillText(t, x + cols[c], y); }
        x += cols[c] + gapX;
      });
    });
    g.textAlign = 'left';
  }
  // 物差し（SP-10-15）：Shift を押しながら引いた2点の距離
  function drawRuler(g, w, h) {
    const [ax, ay] = P(ruler.a[0], ruler.a[1], w, h), [bx, by] = P(ruler.b[0], ruler.b[1], w, h);
    const d = Math.hypot(ruler.b[0] - ruler.a[0], ruler.b[1] - ruler.a[1]);
    g.save();
    g.lineWidth = 4; g.strokeStyle = 'rgba(0, 0, 0, 0.5)';
    g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke();
    g.lineWidth = 2; g.strokeStyle = '#facc15';
    g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke();
    for (const [x, y] of [[ax, ay], [bx, by]]) { g.beginPath(); g.arc(x, y, 4, 0, 2 * Math.PI); g.fillStyle = '#facc15'; g.fill(); }
    const t = d >= 1000 ? `${(d / 1000).toFixed(2)} km` : d >= 100 ? `${d.toFixed(0)} m` : `${d.toFixed(1)} m`;
    g.font = '13px "Yu Gothic UI", "Segoe UI", sans-serif';
    const tw = g.measureText(t).width + 12, mx = (ax + bx) / 2, my = (ay + by) / 2 - 16;
    g.fillStyle = css('--panel'); g.fillRect(mx - tw / 2, my - 10, tw, 20);
    g.strokeStyle = '#facc15'; g.lineWidth = 1; g.strokeRect(mx - tw / 2 + 0.5, my - 9.5, tw - 1, 19);
    g.fillStyle = css('--text'); g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(t, mx, my);
    g.restore();
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
    // 枠を寄せて図が余白（PAD）より小さくなったら描かない（倍率が0や負になり、写真・印の計算が壊れる）。広げれば描き直す
    if (w - 2 * PAD < 8 || h - 2 * PAD < 8 || !view || !(view.span > 0)) return;
    if (!data.origin) {
      g.fillStyle = css('--muted'); g.font = '12px "Yu Gothic UI", sans-serif'; g.textAlign = 'center';
      g.fillText('基準の周の測位が足りないので、コース図を描けません', w / 2, h / 2);
      return;
    }
    centerOnCar();
    const photo = !!(opts.photo && opts.photo());
    if (photo) drawPhoto(g, w, h);
    drawOutline(g, w, h, photo);
    const p = paintOpts(), dark = photo || isDark();
    const tgt = target(p), vals = tgt ? series(tgt, p) : null;
    const rng = vals ? range(vals, p) : null;
    paintNow = { p, tgt, vals, rng, dark };
    if (p.acc) for (const lap of data.laps) drawAccuracy(g, lap, w, h);
    drawHighlights(g, w, h, photo);
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
    // 区間（コーナー）の線（SP-10-13）。細い破線で、札はその線から始まる区間の番号（名前があれば名前も）。直しているときは選んだ線を太く
    (L && L.splits || []).forEach((s, i) => {
      const sel = L.splitSel === i, label = (L.splitLabels && L.splitLabels[i]) || `区${i + 2}`;
      drawLine(g, s, w, h, sel ? css('--accent') : photo ? '#fde68a' : css('--sbest'), sel ? 3 : L.splitEdit ? 2 : 1.5, label, !sel);
    });
    g.shadowBlur = 0; g.shadowColor = 'transparent';
    if (L && L.selected !== null && L.selected !== undefined) {
      const sel = L.selected === 0 ? L.line : L.sectors[L.selected - 1];
      if (sel) drawLine(g, sel, w, h, css('--accent'), 3.5, null, true);
    }
    // カーソルの位置（車）。印の●と見分けがつくよう、走る向きを指す矢じりを、白黒の縁と薄い光の輪で描く
    if (cursor !== null) {
      const key = curKey(), dt = key === 's' ? 4 : 0.4;
      for (const lap of data.laps) {
        const p = carAt(lap);
        if (!p) continue;
        const [px, py] = P(p[0], p[1], w, h);
        const ct = carTime(lap), at = (d) => (ct === undefined ? posAt(lap.track, key, cursor + d * dt) : posAt(lap.track, 't', ct + d * 0.4));
        const a = at(1) || p, b = at(-1) || p;
        const [ax, ay] = P(a[0], a[1], w, h), [bx, by] = P(b[0], b[1], w, h);
        drawCar(g, px, py, Math.atan2(ay - by, ax - bx), opts.color(lap.key), lap.ref ? 11 : 9, photo || dark);
      }
    }
    drawBoards(g, w, h);
    if (editor && editor.active()) editor.overlay(g, (x, y) => P(x, y, w, h), scale(w, h), dark);
    drawLegend(g, w, h, photo);
    if (cursor !== null && p.curinfo && !(editor && editor.active())) drawCursorInfo(g, w, h);
    if (hover && !(editor && editor.active())) drawHover(g, w, h);
    if (ruler) drawRuler(g, w, h);
    // 縮尺
    const k = scale(w, h), m = [10, 20, 50, 100, 200, 500, 1000].find((v) => v * k >= 60) || 1000;
    g.strokeStyle = photo ? '#ffffff' : css('--muted'); g.fillStyle = g.strokeStyle; g.lineWidth = 2;
    g.beginPath(); g.moveTo(PAD, h - PAD); g.lineTo(PAD + m * k, h - PAD); g.stroke();
    g.font = '11px "Yu Gothic UI", sans-serif'; g.textAlign = 'left'; g.textBaseline = 'bottom';
    g.fillText(m >= 1000 ? `${m / 1000}km` : `${m}m`, PAD, h - PAD - 4);
    // 写真・コース図の出典（地理院のデータの決まり）と、読めない・作っているときの知らせ
    const hasOutline = !!((outline && outline.lines && outline.lines.length) || (editor && editor.active()));
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
    drawGesture(g);                  // いちばん上に
  }

  function nearestTrack(x, y) {
    const n = nearest(x, y), lap = refLap();
    return n && lap ? [lap.track.x[n.i], lap.track.y[n.i]] : null;
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

  // from の値 v の所の to の値（線形に補う。null の点は飛ばし、端の外は端の値）
  function interpAt(from, to, v) {
    let pi = -1;
    for (let i = 0; i < from.length; i++) {
      if (from[i] === null || to[i] === null) continue;
      if (from[i] >= v) {
        if (pi < 0) return to[i];
        const f = (v - from[pi]) / ((from[i] - from[pi]) || 1);
        return to[pi] + (to[i] - to[pi]) * f;
      }
      pi = i;
    }
    return pi < 0 ? null : to[pi];
  }
  // 周の線に沿って、横軸の値 v（距離 's' か時間 't'）から d m 先（負なら手前）の横軸の値。周の端で止まる
  function alongBy(track, key, v, d) {
    const s0 = key === 's' ? v : interpAt(track.t, track.s, v);
    return s0 === null ? null : interpAt(track.s, track[key], s0 + d);
  }

  // 画面の点 (mx, my) の近く（14画素の内）にある車の周。重なれば基準の周
  function pickCar(mx, my, w, h) {
    let best = null, bd = 14 * 14;
    for (const lap of data.laps) {
      const p = carAt(lap);
      if (!p) continue;
      const [px, py] = P(p[0], p[1], w, h), d = (px - mx) ** 2 + (py - my) ** 2;
      if (d < bd || (d === bd && lap.ref)) { bd = d; best = lap; }
    }
    return best;
  }
  // 周 lap の線で (x, y)（m）にいちばん近い所の横軸の値（線分へ下ろして間を補う）{v, i, d}。
  // 車を引くときは、前の番号 i0 の前後から探す（近くを並んで走る別の所へ跳ばないように）。そこが 15 m より遠いときだけ全体から
  function valueNear(lap, x, y, i0, key) {
    const t = lap.track, xa = t[key || curKey()], n = t.x.length;
    const search = (lo, hi) => {
      let best = null;
      for (let i = lo; i < hi; i++) {
        const ax = t.x[i], ay = t.y[i], bx = t.x[i + 1], by = t.y[i + 1];
        if (ax === null || bx === null || xa[i] === null || xa[i + 1] === null) continue;
        const vx = bx - ax, vy = by - ay, L2 = vx * vx + vy * vy;
        const f = L2 > 0 ? Math.max(0, Math.min(1, ((x - ax) * vx + (y - ay) * vy) / L2)) : 0;
        const d = Math.hypot(ax + vx * f - x, ay + vy * f - y);
        if (!best || d < best.d) best = { d, i, v: xa[i] + (xa[i + 1] - xa[i]) * f };
      }
      return best;
    };
    const win = Math.max(20, Math.round(n * 0.08));
    let b = i0 !== null && i0 !== undefined ? search(Math.max(0, i0 - win), Math.min(n - 1, i0 + win)) : null;
    if (!b || b.d > 15) { const all = search(0, n - 1); if (all && (!b || all.d < b.d)) b = all; }
    return b;
  }

  // 区間を直しているとき、画面の点 (mx, my) から 10 画素の内にある区間の線の番号（無ければ -1）
  function pickSplit(mx, my, w, h) {
    const L = opts.lines();
    let best = -1, bd = 10;
    (L && L.splits || []).forEach((s, i) => {
      const [a, b] = ends(s);
      const [ax, ay] = P(a[0], a[1], w, h), [bx, by] = P(b[0], b[1], w, h);
      const vx = bx - ax, vy = by - ay, L2 = vx * vx + vy * vy;
      const f = L2 > 0 ? Math.max(0, Math.min(1, ((mx - ax) * vx + (my - ay) * vy) / L2)) : 0;
      const d = Math.hypot(ax + vx * f - mx, ay + vy * f - my);
      if (d < bd) { bd = d; best = i; }
    });
    return best;
  }
  // 基準の周の、横軸の値 v（いまの横軸）の所に置く線 {lat, lon, bearing}。周の外なら null
  function lineAt(v) {
    const lap = refLap();
    if (!lap || v === null || v === undefined) return null;
    const key = curKey(), dv = key === 's' ? 4 : 0.4;
    const p = posAt(lap.track, key, v);
    if (!p) return null;
    const a = posAt(lap.track, key, v - dv) || p, b = posAt(lap.track, key, v + dv) || p;
    const [lat, lon] = toLatLon(p[0], p[1]);
    return { lat, lon, bearing: (Math.atan2(b[0] - a[0], b[1] - a[1]) * 180 / Math.PI + 360) % 360 };
  }

  function bind() {
    const editing = () => editor && editor.active() && data && data.origin;
    let pickReq = null;                       // 車を引いているあいだのカーソルの動かし（1こまに1回）
    // 追っているとき：押した所が車から離れている分（図の上の m）だけ、基準の周の線に沿ってカーソルを先へ送る。
    // 遠くを押せば大きく、車のすぐ横をかちかち押せば少しずつ進む（デジスパイスの送り方）。Shift を押しながらだと手前へ戻る
    const step = (ex, ey, back) => {
      if (!follow || opts.editing() || cursor === null || !data || !data.origin) return false;
      const lap = refLap(), p = carAt(lap);
      if (!p) return false;
      const r = cv.getBoundingClientRect();
      const [x, y] = U(ex - r.left, ey - r.top, r.width, r.height);
      const d = Math.hypot(x - p[0], y - p[1]);
      const v = alongBy(lap.track, curKey(), cursor, back ? -d : d);
      if (v === null || !isFinite(v)) return false;
      opts.onPick(v);
      return true;
    };
    const pickSoon = (v) => {
      const first = pickReq === null;
      pickReq = v;
      if (first) requestAnimationFrame(() => { const x = pickReq; pickReq = null; if (x !== null) opts.onPick(x); });
    };
    const geo = (r) => ({ k: scale(r.width, r.height), nearestTrack });
    // 道具の切り替え（コース図を直しているとき）：右ボタンを押したまま引いた向きの道具にする。引かずに放すと押しかけを取り消す
    cv.addEventListener('contextmenu', (e) => { if (editing()) e.preventDefault(); });
    cv.addEventListener('mousedown', (e) => {
      if (e.button !== 2 || !editing() || !editor.GESTURE) return;
      e.preventDefault();
      const r = cv.getBoundingClientRect();
      gesture = { x: e.clientX - r.left, y: e.clientY - r.top, dx: 0, dy: 0 };
      draw();
    });
    window.addEventListener('mousemove', (e) => {
      if (!gesture) return;
      const r = cv.getBoundingClientRect();
      gesture.dx = e.clientX - r.left - gesture.x; gesture.dy = e.clientY - r.top - gesture.y;
      later();
    });
    window.addEventListener('mouseup', (e) => {
      if (!gesture || e.button !== 2) return;
      const gs = gesture, i = gestureSector(gs);
      gesture = null;
      if (i >= 0 && editor && editor.active()) { if (opts.onTool) opts.onTool(editor.GESTURE[i][0]); } else if (editor) editor.cancelPending();
      draw();
    });
    cv.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, cx: view.cx, cy: view.cy }; moved = false;
      if (editing()) {
        const r = cv.getBoundingClientRect();
        const [x, y] = U(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
        if (editor.down(x, y, geo(r))) { drag.edit = true; cv.style.cursor = 'grabbing'; }
      } else if (data && data.origin && !opts.editing()) {
        const r = cv.getBoundingClientRect();
        if (e.shiftKey) { drag.ruler = U(e.clientX - r.left, e.clientY - r.top, r.width, r.height); hover = null; return; }   // 物差し
        const si = splitEditing() ? pickSplit(e.clientX - r.left, e.clientY - r.top, r.width, r.height) : -1;
        if (si >= 0) { drag.split = { i: si, at: null }; hover = null; cv.style.cursor = 'grabbing'; return; }
        const lap = pickCar(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
        if (lap) { drag.car = { lap, i: null }; hover = null; cv.style.cursor = 'grabbing'; }
      }
    });
    window.addEventListener('mouseup', (e) => {
      if (!drag) return;
      const was = drag; drag = null;
      if (was.edit) { editor.up(); cv.style.cursor = ''; draw(); return; }
      if (was.ruler) { ruler = null; draw(); if (moved) return; }   // 引かずに放したら、いつもの Shift＋押す（追っているなら手前へ戻る）
      if (was.car) { cv.style.cursor = ''; if (!moved) step(was.x, was.y, e.shiftKey); draw(); return; }
      if (was.split) { cv.style.cursor = ''; if (opts.onSplit) opts.onSplit(was.split.i, moved ? lineAt(was.split.at) : null, moved ? was.split.at : null); return; }   // 追っているなら、ここで車を真ん中へ戻す。引かずに押しただけなら少し送る
      if (moved || !data || !data.origin) return;
      const r = cv.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return;
      const [x, y] = U(was.x - r.left, was.y - r.top, r.width, r.height);
      if (editing()) { editor.click(x, y, geo(r)); editor.hoverAt(x, y, geo(r)); draw(); return; }
      if (step(was.x, was.y, e.shiftKey)) return;
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
      if (drag && drag.edit) {
        const r = cv.getBoundingClientRect();
        const [x, y] = U(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
        editor.move(x, y);
        later();
        return;
      }
      if (drag && drag.ruler) {               // 物差し：押した所からいまの所までの距離
        if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) moved = true;
        if (!moved) return;
        const r = cv.getBoundingClientRect();
        ruler = { a: drag.ruler, b: U(e.clientX - r.left, e.clientY - r.top, r.width, r.height) };
        later();
        return;
      }
      if (drag && drag.split) {               // 区間の線を引く：基準の周の線に沿って動かす（離したときに opts.onSplit で知らせる）
        moved = true;
        const r = cv.getBoundingClientRect();
        const [x, y] = U(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
        const b = valueNear(refLap(), x, y, drag.split.i0);
        const ln = b && lineAt(b.v);
        if (ln && opts.onSplitMove) { drag.split.i0 = b.i; drag.split.at = b.v; opts.onSplitMove(drag.split.i, ln); }
        return;
      }
      if (drag && drag.car) {                 // 車を引く：周の線に沿って、カーソル（グラフ・動画も）を動かす
        moved = true;
        const r = cv.getBoundingClientRect();
        const [x, y] = U(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
        const b = valueNear(drag.car.lap, x, y, drag.car.i);
        if (b) { drag.car.i = b.i; pickSoon(b.v); }
        return;
      }
      if (!drag && editing()) {
        const r = cv.getBoundingClientRect();
        const [x, y] = U(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
        cv.style.cursor = editor.hoverAt(x, y, geo(r)) || '';
        later();
        return;
      }
      if (!drag) {
        if (!data || !data.origin) return;
        const r = cv.getBoundingClientRect(), was = hover;
        const onCar = !opts.editing() && (pickCar(e.clientX - r.left, e.clientY - r.top, r.width, r.height) ||
                                          splitEditing() && pickSplit(e.clientX - r.left, e.clientY - r.top, r.width, r.height) >= 0);
        cv.style.cursor = onCar ? 'grab' : '';
        hover = pickHover(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
        const same = (a, b) => (a && b ? a.lap === b.lap && a.i === b.i && a.mark === b.mark : a === b);
        if (!same(was, hover)) later();
        return;
      }
      hover = null;
      const r = cv.getBoundingClientRect(), k = scale(r.width, r.height);
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) moved = true;
      if (!moved) return;
      if (follow) setFollow(false, true);     // 図を手で引いたら追うのをやめる
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
    cv.addEventListener('dblclick', () => { if (editing()) return; if (follow) setFollow(false, true); fit(); draw(); });   // 直しているときは押し直しで図が戻らないように
  }

  return {
    // o：{x() 'dist'|'time', color(key), lines() {line, sectors, splits, splitLabels, splitSel, splitEdit, selected, draft}, editing(), onPick(x), onPlace({lat,lon,bearing}), photo() 航空写真を敷くか,
    //     splitEditing() 区間の線を直しているか, onSplitMove(i, {lat,lon,bearing}) 引いている途中, onSplit(i, 線か null, 横軸の値) 離した（null は押しただけ）,
    //     paint() {mode 'lap'|'value'|'rate'|'delta', ch, chLabel, unit, lo, hi 段の範囲を手で（null は自動）, dotted, acc, marks, markLabel, markUnit,
    //              curinfo カーソルの所に周ごとの速さ・横のずれ・タイムの差を出すか},
    //     lapTime(lap, x) 周 lap の車を置く時刻（周の頭からの秒。無ければ横軸の値でそのまま置く）
    init(canvas, o) { cv = canvas; opts = o; bind(); },
    show(d, keepView) { const same = data && d && data.origin && d.origin && data.origin[0] === d.origin[0] && data.origin[1] === d.origin[1]; data = d; hover = null; if (!(keepView && same && view)) { fit(); followZoom = follow; } draw(); },
    // いま塗っている段の範囲（自動のときの数を入れ欄の見本に出すため）
    paintRange() { return paintNow && paintNow.rng; },
    setCursor(x) { cursor = x; draw(); },
    // 車を追う（オン・オフ）。図を手で引いて外れたときは opts.onFollow(false) で知らせる
    setFollow(on) { setFollow(on, false); draw(); },
    following: () => follow,
    // コース図（null で消す）と、図の上に出す知らせ（作っている・作れない）
    setOutline(o, note) { outline = o || null; outlineNote = note || null; outlineXY = null; draw(); },
    // コース図のエディタ（OutlineEditor か null）。直す中身の座標を出し入れするための変換も渡す
    setEditor(ed) { editor = ed || null; if (cv) cv.style.cursor = ''; draw(); },
    toXY: (lat, lon) => toXY(lat, lon),
    toLatLon: (x, y) => toLatLon(x, y),
    // 伸びしろの大きい区間を目立たせる（[{a, b, label}]、a・b は基準の周の距離 m。[] で消す）
    setHighlights(list) { highlights = Array.isArray(list) ? list : []; draw(); },
    // 印刷のレポート（web/report.js）が同じ中身で描き直すための読み出し：伸びしろの帯と、コース図を m に直したもの
    highlightList: () => highlights.slice(),
    outlineXY: () => (data && data.origin ? outlineView() : null),
    // 基準の周の横軸の値 a〜b の所が収まるように寄る（区間の表から寄るとき）。追うのはやめる
    fitRange(a, b) {
      const lap = data && data.origin && refLap();
      if (!lap) return;
      const t = lap.track, xa = t[curKey()];
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      for (let i = 0; i < t.x.length; i++) {
        if (t.x[i] === null || xa[i] === null || xa[i] < a || xa[i] > b) continue;
        x0 = Math.min(x0, t.x[i]); x1 = Math.max(x1, t.x[i]); y0 = Math.min(y0, t.y[i]); y1 = Math.max(y1, t.y[i]);
      }
      if (!isFinite(x0)) return;
      if (follow) setFollow(false, true);
      view = { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, span: Math.max(x1 - x0, y1 - y0, 80) * 1.25 };
      draw();
    },
    // 基準の周の、横軸の値 v の所に置く線（区間の線を足す・動かすため）
    lineAt: (v) => (data && data.origin ? lineAt(v) : null),
    // (lat, lon) を基準の周の線へ下ろした所の横軸の値 {s, t}（グラフに区間の線を描くため）。測位が無ければ null
    refAt(lat, lon) {
      const lap = data && data.origin && refLap();
      if (!lap) return null;
      const [x, y] = toXY(lat, lon), a = valueNear(lap, x, y, null, 's'), b = valueNear(lap, x, y, null, 't');
      return a && b ? { s: a.v, t: b.v, d: a.d } : null;
    },
    alongBy: (track, key, v, d) => alongBy(track, key, v, d),   // 試験から呼ぶため
    ready: () => !!(data && data.origin),
    // 画面の上の位置（clientX, clientY）。試しに押す所を決めるため
    toScreen(lat, lon) { const r = cv.getBoundingClientRect(); const [px, py] = P(...toXY(lat, lon), r.width, r.height); return [r.left + px, r.top + py]; },
    redraw() { draw(); },
    clear() { data = null; cursor = null; hover = null; if (cv) { const [g, w, h] = size(); g.clearRect(0, 0, w, h); } },
  };
})();
