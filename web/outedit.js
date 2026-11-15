// コース図のエディタ。自動で作ったコース図（道路の縁・ゼブラ・距離の看板）を手で直す。
// 座標はコース図（map.js）と同じ m（東が x、北が y）。図への描き方とマウスは map.js が受け持ち、
// ここは中身と道具だけを持つ（map.js の setEditor へ渡す）。直したものは裏がコースごとに残し、自動のものより先に出す。
'use strict';

const OutlineEditor = (() => {
  const PICK_PX = 12;        // 点・線を拾うのはこの画素の内
  const STEP_M = 2;          // まっすぐにした所・つないだ所の点の間隔（自動の横断面と同じ）
  const UNDO_MAX = 60;
  const TOOLS = {
    move: '縁・ゼブラの点をつかんで動かします。なめらかさの距離の内の点も、離れるほど少しずつ一緒に動きます（0 なら1点だけ）。つかめない所を引くと図が動きます',
    straight: '同じ線の上を2か所押すと、そのあいだをまっすぐにします（輪の線は短いほうのあいだ）',
    erase: '同じ線の上を2か所押すと、そのあいだを消します（線が2本に分かれます）。ゼブラにも使えます',
    join: '線の端を押してから、つなぎたい所（別の線の端か、同じ線のもう一方の端）を押すと、まっすぐにつなぎます',
    zebraAdd: '縁の上を2か所押すと、そのあいだの縁の外側にゼブラを足します（幅は下の欄）',
    zebraDel: '消したいゼブラを押します',
    board: '押した所に看板（字は下の欄）を置きます。看板を押すと消します',
  };

  let st = null;             // {lines: [{pts, closed}], kerbs: [{w, pts}], boards: [{x, y, text}], undo: [], tool, pend, grab, hover, dirty}
  let ui = null;             // {radius(), zebraW(), boardText(), onChange()}

  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

  // ---------------------------------------------------------------- 出し入れ
  function load(outline, toXY, o) {
    ui = o;
    const xy = (pts) => pts.map((q) => toXY(q[0], q[1]));
    const lines = (outline.lines || []).map((ln) => {
      const pts = xy(ln);
      const closed = pts.length > 3 && dist(pts[0], pts[pts.length - 1]) < 0.01;
      if (closed) pts.pop();
      return { pts, closed };
    });
    const kerbs = (outline.kerbs || []).filter((z) => z && z.pts && z.pts.length >= 2).map((z) => ({ w: z.w || 1, pts: xy(z.pts) }));
    const boards = (outline.boards || []).map((b) => { const [x, y] = toXY(b.lat, b.lon); return { x, y, text: b.text }; });
    st = { lines, kerbs, boards, undo: [], tool: 'move', pend: null, grab: null, hover: null, dirty: false };
  }

  function dump(toLatLon) {
    const ll = (pts) => pts.map((p) => toLatLon(p[0], p[1]));
    return {
      lines: st.lines.filter((l) => l.pts.length >= 2).map((l) => ll(l.closed ? l.pts.concat([l.pts[0]]) : l.pts)),
      kerbs: st.kerbs.filter((z) => z.pts.length >= 2).map((z) => ({ w: z.w, pts: ll(z.pts) })),
      boards: st.boards.map((b) => { const [lat, lon] = toLatLon(b.x, b.y); return { lat, lon, text: b.text }; }),
    };
  }

  // いまの中身（図へ描く形）
  function view() { return st && { lines: st.lines, kerbs: st.kerbs, boards: st.boards }; }

  function snapshot() {
    st.undo.push(JSON.stringify({ lines: st.lines, kerbs: st.kerbs, boards: st.boards }));
    if (st.undo.length > UNDO_MAX) st.undo.shift();
  }
  function changed() { st.dirty = true; if (ui && ui.onChange) ui.onChange(); }
  function undo() {
    if (!st || !st.undo.length) return false;
    const o = JSON.parse(st.undo.pop());
    st.lines = o.lines; st.kerbs = o.kerbs; st.boards = o.boards; st.pend = null; st.grab = null;
    changed();
    return true;
  }

  // ---------------------------------------------------------------- 拾う
  // いちばん近い点 {kind 'line'|'kerb', idx, i, d}（m）。kinds：拾う種類
  function pickVertex(x, y, tol, kinds) {
    let best = null;
    const scan = (arr, kind) => arr.forEach((it, idx) => it.pts.forEach((p, i) => {
      const d = Math.hypot(p[0] - x, p[1] - y);
      if (d <= tol && (!best || d < best.d)) best = { kind, idx, i, d };
    }));
    if (kinds.includes('line')) scan(st.lines, 'line');
    if (kinds.includes('kerb')) scan(st.kerbs, 'kerb');
    return best;
  }
  // 線分まで含めていちばん近い所（点の番号はその線分の近いほうの端）
  function pickPath(x, y, tol, kinds) {
    let best = null;
    const scan = (arr, kind) => arr.forEach((it, idx) => {
      const n = it.pts.length, closed = kind === 'line' && it.closed;
      for (let i = 0; i < (closed ? n : n - 1); i++) {
        const a = it.pts[i], b = it.pts[(i + 1) % n];
        const vx = b[0] - a[0], vy = b[1] - a[1], L2 = vx * vx + vy * vy;
        const t = L2 > 0 ? Math.max(0, Math.min(1, ((x - a[0]) * vx + (y - a[1]) * vy) / L2)) : 0;
        const d = Math.hypot(a[0] + vx * t - x, a[1] + vy * t - y);
        if (d <= tol && (!best || d < best.d)) best = { kind, idx, i: t < 0.5 ? i : (i + 1) % n, d };
      }
    });
    if (kinds.includes('line')) scan(st.lines, 'line');
    if (kinds.includes('kerb')) scan(st.kerbs, 'kerb');
    return best;
  }
  const itemOf = (p) => (p.kind === 'line' ? st.lines[p.idx] : st.kerbs[p.idx]);
  const isClosed = (p) => p.kind === 'line' && st.lines[p.idx].closed;

  // 線の上の2点 i・j のあいだ。開いた線は番号の小さいほうから、輪は短いほう。{from, to}（to は from から先へ数えた番号。輪なら n を越えてよい）
  function span(it, closed, i, j) {
    const n = it.pts.length;
    if (!closed) return { from: Math.min(i, j), to: Math.max(i, j) };
    const len = (a, b) => { let s = 0; for (let k = a; k < b; k++) s += dist(it.pts[k % n], it.pts[(k + 1) % n]); return s; };
    const f = (j - i + n) % n;
    return len(i, i + f) <= len(j, j + n - f) ? { from: i, to: i + f } : { from: j, to: j + n - f };
  }

  function between(a, b) {          // a から b へ STEP_M おきの点（両端は入れない）
    const d = dist(a, b), m = Math.max(0, Math.ceil(d / STEP_M) - 1), out = [];
    for (let k = 1; k <= m; k++) { const t = k / (m + 1); out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]); }
    return out;
  }

  // ---------------------------------------------------------------- 道具
  function straighten(p, q) {
    const it = itemOf(p), n = it.pts.length, closed = isClosed(p);
    const { from, to } = span(it, closed, p.i, q.i);
    if (to - from < 1) return false;
    snapshot();
    const a = it.pts[from % n], b = it.pts[to % n];
    if (!closed) {
      it.pts = it.pts.slice(0, from + 1).concat(between(a, b), it.pts.slice(to));
    } else {                        // from を頭に回してから、頭〜to を入れ替える
      const r = it.pts.slice(from).concat(it.pts.slice(0, from));
      const t = to - from;
      it.pts = [r[0]].concat(between(a, b), r.slice(t));
    }
    return true;
  }

  function erase(p, q) {
    const it = itemOf(p), n = it.pts.length, closed = isClosed(p);
    const { from, to } = span(it, closed, p.i, q.i);
    if (to - from < 1) return false;
    snapshot();
    const arr = p.kind === 'line' ? st.lines : st.kerbs;
    let pieces;
    if (!closed) {
      pieces = [it.pts.slice(0, from + 1), it.pts.slice(to)];
    } else {                        // 輪を開く：to から回って from まで
      const r = it.pts.slice(from).concat(it.pts.slice(0, from));
      pieces = [r.slice(to - from).concat([r[0]])];
    }
    const made = pieces.filter((x) => x.length >= 2).map((pts) => (p.kind === 'line' ? { pts, closed: false } : { w: it.w, pts }));
    arr.splice(p.idx, 1, ...made);
    return true;
  }

  // 開いた線の端（頭 0・終わり 1）を拾う
  function pickEnd(x, y, tol) {
    let best = null;
    st.lines.forEach((l, idx) => {
      if (l.closed || l.pts.length < 2) return;
      [[0, l.pts[0]], [1, l.pts[l.pts.length - 1]]].forEach(([end, pt]) => {
        const d = Math.hypot(pt[0] - x, pt[1] - y);
        if (d <= tol && (!best || d < best.d)) best = { kind: 'line', idx, end, i: end ? l.pts.length - 1 : 0, d };
      });
    });
    return best;
  }

  function join(p, q) {
    const A = st.lines[p.idx], B = st.lines[q.idx];
    if (p.idx === q.idx) {
      if (p.end === q.end) return false;
      snapshot();
      A.pts = A.pts.concat(between(A.pts[A.pts.length - 1], A.pts[0]));
      A.closed = true;
      return true;
    }
    snapshot();
    const a = p.end ? A.pts : A.pts.slice().reverse();   // a の終わりから
    const b = q.end ? B.pts.slice().reverse() : B.pts;   // b の頭へ
    const merged = a.concat(between(a[a.length - 1], b[0]), b);
    const keep = Math.min(p.idx, q.idx), drop = Math.max(p.idx, q.idx);
    st.lines[keep] = { pts: merged, closed: false };
    st.lines.splice(drop, 1);
    return true;
  }

  function zebraAdd(p, q, geo) {
    const it = st.lines[p.idx], n = it.pts.length;
    const { from, to } = span(it, it.closed, p.i, q.i);
    if (to - from < 1) return false;
    const pts = [];
    for (let k = from; k <= to; k++) pts.push(it.pts[k % n]);
    const w = ui.zebraW();
    // 外向き：走った線（基準の周）から離れる向き。真ん中の点で決める
    const nrm = (k) => {
      const a = pts[Math.max(0, k - 1)], b = pts[Math.min(pts.length - 1, k + 1)];
      const L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
      return [-(b[1] - a[1]) / L, (b[0] - a[0]) / L];
    };
    const mid = pts[Math.floor(pts.length / 2)], nm = nrm(Math.floor(pts.length / 2));
    const tr = geo.nearestTrack(mid[0], mid[1]);
    let sg = 1;
    if (tr) sg = (mid[0] - tr[0]) * nm[0] + (mid[1] - tr[1]) * nm[1] >= 0 ? 1 : -1;
    snapshot();
    st.kerbs.push({ w, pts: pts.map((pt, k) => { const v = nrm(k); return [pt[0] + sg * v[0] * w / 2, pt[1] + sg * v[1] * w / 2]; }) });
    return true;
  }

  // ---------------------------------------------------------------- マウス（map.js から。x・y は m、geo {k 画素/m, nearestTrack}）
  function tolM(geo) { return PICK_PX / geo.k; }

  // 押した：つかんだら true（図を動かさない）
  function down(x, y, geo) {
    if (!st || st.tool !== 'move') return false;
    const p = pickVertex(x, y, tolM(geo), ['line', 'kerb']);
    if (!p) return false;
    snapshot();
    const it = itemOf(p), n = it.pts.length, closed = isClosed(p);
    // つかんだ点からの、線に沿った距離（輪なら近いほうの向きで）
    const s = new Array(n).fill(Infinity);
    if (closed) {
      const fwd = new Array(n).fill(0);
      for (let c = 1; c < n; c++) fwd[(p.i + c) % n] = fwd[(p.i + c - 1) % n] + dist(it.pts[(p.i + c - 1) % n], it.pts[(p.i + c) % n]);
      const L = fwd[(p.i + n - 1) % n] + dist(it.pts[(p.i + n - 1) % n], it.pts[p.i]);
      for (let k = 0; k < n; k++) s[k] = Math.min(fwd[k], L - fwd[k]);
    } else {
      s[p.i] = 0;
      for (let k = p.i + 1; k < n; k++) s[k] = s[k - 1] + dist(it.pts[k - 1], it.pts[k]);
      for (let k = p.i - 1; k >= 0; k--) s[k] = s[k + 1] + dist(it.pts[k + 1], it.pts[k]);
    }
    st.grab = { p, x0: x, y0: y, orig: it.pts.map((q) => q.slice()), s, R: Math.max(0, ui.radius()) };
    return true;
  }

  function move(x, y) {
    const g = st && st.grab;
    if (!g) return;
    const it = itemOf(g.p), dx = x - g.x0, dy = y - g.y0;
    for (let k = 0; k < it.pts.length; k++) {
      const s = g.s[k];
      const w = k === g.p.i ? 1 : g.R > 0 && s < g.R ? (1 - (s / g.R) ** 2) ** 2 : 0;
      it.pts[k] = [g.orig[k][0] + w * dx, g.orig[k][1] + w * dy];
    }
  }
  function up() {
    if (!st || !st.grab) return;
    const g = st.grab;
    st.grab = null;
    const it = itemOf(g.p);
    if (dist(it.pts[g.p.i], g.orig[g.p.i]) < 1e-6) st.undo.pop(); else changed();
  }

  // 押して離した（動かさずに）
  function click(x, y, geo) {
    if (!st) return;
    const tol = tolM(geo), t = st.tool;
    let done = false;
    if (t === 'straight' || t === 'erase' || t === 'zebraAdd') {
      const kinds = t === 'zebraAdd' ? ['line'] : ['line', 'kerb'];
      const p = pickPath(x, y, tol, kinds);
      if (!p) { st.pend = null; return; }
      if (!st.pend) { st.pend = p; return; }
      const a = st.pend;
      st.pend = null;
      if (a.kind !== p.kind || a.idx !== p.idx) { st.pend = p; return; }   // 別の線なら、押し直しの1か所目にする
      done = t === 'straight' ? straighten(a, p) : t === 'erase' ? erase(a, p) : zebraAdd(a, p, geo);
    } else if (t === 'join') {
      if (!st.pend) { st.pend = pickEnd(x, y, tol); return; }
      const q = pickEnd(x, y, tol);
      const a = st.pend;
      st.pend = null;
      if (q) done = join(a, q);
    } else if (t === 'zebraDel') {
      const p = pickPath(x, y, tol, ['kerb']);
      if (p) { snapshot(); st.kerbs.splice(p.idx, 1); done = true; }
    } else if (t === 'board') {
      const hit = st.boards.findIndex((b) => Math.hypot(b.x - x, b.y - y) <= tol * 1.5);
      snapshot();
      if (hit >= 0) st.boards.splice(hit, 1);
      else {
        const text = ui.boardText();
        if (!text) { st.undo.pop(); return; }
        st.boards.push({ x, y, text });
      }
      done = true;
    }
    if (done) changed();
  }

  // 何の上にいるか（カーソルの形と、つかめる点の印）
  function hoverAt(x, y, geo) {
    if (!st) return null;
    const tol = tolM(geo), t = st.tool;
    if (t === 'move') st.hover = pickVertex(x, y, tol, ['line', 'kerb']);
    else if (t === 'join') st.hover = pickEnd(x, y, tol);
    else if (t === 'zebraDel') st.hover = pickPath(x, y, tol, ['kerb']);
    else if (t === 'board') st.hover = null;
    else st.hover = pickPath(x, y, tol, t === 'zebraAdd' ? ['line'] : ['line', 'kerb']);
    return st.hover ? (t === 'move' ? 'grab' : 'pointer') : t === 'board' ? 'copy' : null;
  }

  // 図の上に、点・押した1か所目・拾える所を描く（コース図そのものは map.js が view() から描く）
  function overlay(g, P, k, dark) {
    if (!st) return;
    const dot = (pt, r, fill, stroke) => { const [px, py] = P(pt[0], pt[1]); g.beginPath(); g.arc(px, py, r, 0, 2 * Math.PI); g.fillStyle = fill; g.fill(); if (stroke) { g.strokeStyle = stroke; g.lineWidth = 1.5; g.stroke(); } };
    g.save();
    if (k * STEP_M >= 6) {          // 寄ったときだけ点を出す
      const c = dark ? 'rgba(255, 255, 255, 0.8)' : 'rgba(31, 41, 55, 0.7)';
      for (const it of st.lines.concat(st.kerbs)) for (const pt of it.pts) dot(pt, 1.6, c);
    }
    for (const l of st.lines) if (!l.closed && l.pts.length) {   // 開いた線の端（つなぐ所）
      for (const pt of [l.pts[0], l.pts[l.pts.length - 1]]) dot(pt, 4, '#f59e0b', dark ? '#111827' : '#ffffff');
    }
    const H = st.hover;
    if (H) {
      const it = itemOf(H);
      if (it && it.pts[H.i]) dot(it.pts[H.i], 6, 'rgba(59, 130, 246, 0.35)', '#3b82f6');
    }
    if (st.pend) {
      const it = itemOf(st.pend);
      if (it && it.pts[st.pend.i]) dot(it.pts[st.pend.i], 6, '#ef4444', '#ffffff');
    }
    g.restore();
  }

  // 図の原点が変わったとき：中身と戻す分の点を f(x, y) → [x, y] で移す
  function remap(f) {
    if (!st) return;
    const mv = (o) => {
      for (const l of o.lines) l.pts = l.pts.map((p) => f(p[0], p[1]));
      for (const z of o.kerbs) z.pts = z.pts.map((p) => f(p[0], p[1]));
      for (const b of o.boards) { const [x, y] = f(b.x, b.y); b.x = x; b.y = y; }
      return o;
    };
    mv(st);
    st.undo = st.undo.map((u) => JSON.stringify(mv(JSON.parse(u))));
    st.pend = null; st.grab = null; st.hover = null;
  }

  return {
    TOOLS, remap,
    load, dump, view, undo, down, move, up, click, hoverAt, overlay,
    active: () => !!st,
    stop() { st = null; },
    dirty: () => !!(st && st.dirty),
    canUndo: () => !!(st && st.undo.length),
    tool: () => st && st.tool,
    setTool(t) { if (st && TOOLS[t]) { st.tool = t; st.pend = null; st.hover = null; } },
    // 試験用：中身の数
    counts: () => st && { lines: st.lines.length, points: st.lines.reduce((a, l) => a + l.pts.length, 0), kerbs: st.kerbs.length, boards: st.boards.length },
  };
})();
