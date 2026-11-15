// コース図のエディタ。自動で作ったコース図（道路の縁・ゼブラ・距離の看板）を手で直す。
// 座標はコース図（map.js）と同じ m（東が x、北が y）。図への描き方とマウスは map.js が受け持ち、
// ここは中身と道具だけを持つ（map.js の setEditor へ渡す）。直したものは裏がコースごとに残し、自動のものより先に出す。
//
// ゼブラは {pts, ws, side}：pts は内側の端（コースの縁の上の点）、ws は点ごとの幅（m、端は細くして 0 まで）、
// 外側の端は pts から走る向きの左（side = 1）か右（side = -1）へ ws だけ離れた所。縁にくっついていて、
// 縁を動かす・なめらかにする・まっすぐにすると一緒に動く。前の形 {w, pts（帯の真ん中）} も読む。
'use strict';

const OutlineEditor = (() => {
  const PICK_PX = 12;        // 点・線を拾うのはこの画素の内
  const STEP_M = 2;          // まっすぐにした所・つないだ所の点の間隔（自動の横断面と同じ）
  const UNDO_MAX = 60;
  const GLUE_M = 0.3;        // ゼブラの内側の点が縁からこの距離の内なら、その縁にくっついているとみる
  const RESNAP_M = 6;        // まっすぐにした縁へ、ゼブラの点を載せ直すのはこの距離まで
  const END_KEEP_M = 4;      // ゼブラの端を伸び縮みさせるとき、端からこの長さの幅の形（細り）を端と一緒に動かす
  const MIN_LEN_M = 1;       // ゼブラはこの長さより短くしない
  const LEGACY_SNAP_M = 1;   // 前の形のゼブラを読むとき、内側の端をこの距離の内の縁へ載せる
  const W_MAX = 5;
  const TRACE_STEP_PX = 3;    // なぞる：この画素より動いたら点を足す
  const TRACE_REACH_PX = 24;  // なぞる：横へ探す幅（画面の画素。m に直して裏へ送る）
  const TRACE_ATTACH_PX = 30; // なぞる：両端がこの画素の内に同じ縁があれば、そのあいだを置き換える
  const TRACE_MIN_M = 4;
  const TOOLS = {
    move: '縁の点をつかんで動かします。なめらかさの距離の内の点も、離れるほど少しずつ一緒に動きます（0 なら1点だけ）。ゼブラは外側の端をつかむと幅が変わり（なめらかさの距離の内も少しずつ）、両端の四角をつかむと縁に沿って伸び縮みします。つかめない所を引くと図が動きます',
    smooth: '同じ縁の上を2か所押すと、そのあいだの細かいガタガタをならします。カーブの形は残します。同じ所を2回押すと線1本まるごと。ならす幅は「なめらかさ」の半分で、押し直すほどなめらかになります。ゼブラは縁と一緒に動きます',
    straight: '同じ縁の上を2か所押すと、そのあいだをまっすぐにします（輪の線は短いほうのあいだ）。ゼブラは縁と一緒に動きます',
    erase: '同じ線の上を2か所押すと、そのあいだを消します（線が2本に分かれます）。同じゼブラの上を2か所押すと、ゼブラのそのあいだを消します',
    join: '線の端を押してから、つなぎたい所（別の線の端か、同じ線のもう一方の端）を押すと、まっすぐにつなぎます',
    zebraAdd: '縁の上を2か所押すと、そのあいだの縁の外側にゼブラを足します（幅・端の細りは下の欄）。ゼブラは縁にくっつき、縁を動かすと一緒に動きます',
    zebraDel: '消したいゼブラを押します',
    board: '押した所に看板（字は下の欄）を置きます。看板を押すと消します',
    trace: 'コースの縁をマウスでざっとなぞると、近くの縁（航空写真で路面が切れる所・色が変わる所・白線、地図の道路縁）へ吸い付いた線にします。探す幅は画面の倍率で決まり（寄るほど細かく）、縁が見つからない所はなぞったまま。なぞり始めと終わりが同じ縁の近くなら、そのあいだを置き換え（ゼブラも載り直す）、そうでなければ新しい縁として足します',
  };
  // 道具の切り替え：図の上で右ボタンを押したまま引いた向き（上から時計回りに8つ）。数字キーの 1〜8 も同じ順
  const GESTURE = [
    ['move', '点を動かす'], ['smooth', 'なめらかに'], ['straight', 'まっすぐ'], ['join', 'つなぐ'],
    ['erase', '消す'], ['zebraAdd', 'ゼブラを足す'], ['zebraDel', 'ゼブラを消す'], ['board', '看板'],
  ];

  let st = null;             // {lines: [{pts, closed}], kerbs: [{pts, ws, side}], boards: [{x, y, text}], undo: [], tool, pend, grab, hover, dirty}
  let ui = null;             // {radius(), zebraW(), zebraTaper(), boardText(), onChange(), snap(pts, inside, reach) → Promise<{pts, found, shift_m}>, onTrace(msg, ok)}

  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const clampW = (w) => (Number.isFinite(w) ? Math.min(W_MAX, Math.max(0, w)) : 1);

  // 線分 a→b へ q を下ろした所 {d, t}
  function segProj(a, b, q) {
    const vx = b[0] - a[0], vy = b[1] - a[1], L2 = vx * vx + vy * vy;
    const t = L2 > 0 ? Math.max(0, Math.min(1, ((q[0] - a[0]) * vx + (q[1] - a[1]) * vy) / L2)) : 0;
    return { d: Math.hypot(a[0] + vx * t - q[0], a[1] + vy * t - q[1]), t };
  }
  // 点 k での、走る向き（点の順）の左の向き
  function normalAt(pts, k) {
    const n = pts.length, a = pts[Math.max(0, k - 1)], b = pts[Math.min(n - 1, k + 1)];
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    return [-(b[1] - a[1]) / L, (b[0] - a[0]) / L];
  }
  function cumLen(pts) {
    const S = [0];
    for (let k = 1; k < pts.length; k++) S.push(S[k - 1] + dist(pts[k - 1], pts[k]));
    return S;
  }
  // ゼブラの外側の端・帯の真ん中
  function edgeOf(z, f) {
    return z.pts.map((p, k) => { const v = normalAt(z.pts, k), s = z.side * z.ws[k] * f; return [p[0] + v[0] * s, p[1] + v[1] * s]; });
  }
  const outerOf = (z) => edgeOf(z, 1);
  const midOf = (z) => edgeOf(z, 0.5);
  // 端を細くした幅：両端から T の内で 0 → w（まっすぐに）
  function taperWidths(pts, w, T) {
    const S = cumLen(pts), L = S[S.length - 1];
    return S.map((s) => (T > 0 ? w * Math.max(0, Math.min(1, s / T, (L - s) / T)) : w));
  }

  // ---------------------------------------------------------------- 出し入れ
  // いちばん近い縁の上の点 {d, p}
  function nearestOnLines(lines, q) {
    let best = { d: Infinity, p: q };
    for (const l of lines) {
      const n = l.pts.length;
      for (let i = 0; i < (l.closed ? n : n - 1); i++) {
        const a = l.pts[i], b = l.pts[(i + 1) % n], r = segProj(a, b, q);
        if (r.d < best.d) best = { d: r.d, p: [a[0] + (b[0] - a[0]) * r.t, a[1] + (b[1] - a[1]) * r.t] };
      }
    }
    return best;
  }
  const nearestLineDist = (lines, q) => nearestOnLines(lines, q).d;
  function kerbFrom(z, xy, lines) {
    if (!z || !Array.isArray(z.pts) || z.pts.length < 2) return null;
    const pts = xy(z.pts);
    if (Array.isArray(z.ws) && z.ws.length === pts.length) return { pts, ws: z.ws.map((w) => clampW(+w)), side: z.side < 0 ? -1 : 1 };
    // 前の形（帯の真ん中の線と決まった幅）：縁に近い側を内側にする
    const w = clampW(+z.w || 1), m = Math.floor(pts.length / 2), nm = normalAt(pts, m);
    const dIn = (s) => nearestLineDist(lines, [pts[m][0] - s * nm[0] * w / 2, pts[m][1] - s * nm[1] * w / 2]);
    const side = dIn(1) <= dIn(-1) ? 1 : -1;
    // 内側の端は、縁から LEGACY_SNAP_M の内なら縁の上へ載せる（縁にくっつけて、縁と一緒に動くように）
    const inner = pts.map((p, k) => {
      const v = normalAt(pts, k), q = [p[0] - side * v[0] * w / 2, p[1] - side * v[1] * w / 2], s = nearestOnLines(lines, q);
      return s.d <= LEGACY_SNAP_M ? s.p : q;
    });
    return { pts: inner, ws: pts.map(() => w), side };
  }

  function load(outline, toXY, o) {
    ui = o;
    const xy = (pts) => pts.map((q) => toXY(q[0], q[1]));
    const lines = (outline.lines || []).map((ln) => {
      const pts = xy(ln);
      const closed = pts.length > 3 && dist(pts[0], pts[pts.length - 1]) < 0.01;
      if (closed) pts.pop();
      return { pts, closed };
    });
    const kerbs = (outline.kerbs || []).map((z) => kerbFrom(z, xy, lines)).filter(Boolean);
    const boards = (outline.boards || []).map((b) => { const [x, y] = toXY(b.lat, b.lon); return { x, y, text: b.text }; });
    st = { lines, kerbs, boards, undo: [], redo: [], tool: 'move', pend: null, grab: null, hover: null, dirty: false, trace: null, busy: [] };
  }

  function dump(toLatLon) {
    const ll = (pts) => pts.map((p) => toLatLon(p[0], p[1]));
    return {
      lines: st.lines.filter((l) => l.pts.length >= 2).map((l) => ll(l.closed ? l.pts.concat([l.pts[0]]) : l.pts)),
      kerbs: st.kerbs.filter((z) => z.pts.length >= 2).map((z) => ({ pts: ll(z.pts), ws: z.ws.map((w) => Math.round(w * 100) / 100), side: z.side })),
      boards: st.boards.map((b) => { const [lat, lon] = toLatLon(b.x, b.y); return { lat, lon, text: b.text }; }),
    };
  }

  // いまの中身（図へ描く形）
  function view() { return st && { lines: st.lines, kerbs: st.kerbs, boards: st.boards }; }

  const stateNow = () => JSON.stringify({ lines: st.lines, kerbs: st.kerbs, boards: st.boards });
  function snapshot() {
    st.undo.push(stateNow());
    if (st.undo.length > UNDO_MAX) st.undo.shift();
    st.redo = [];                    // 新しく直したら、やり直しの分は捨てる
  }
  function changed() { st.dirty = true; if (ui && ui.onChange) ui.onChange(); }
  function restore(json) {
    const o = JSON.parse(json);
    st.lines = o.lines; st.kerbs = o.kerbs; st.boards = o.boards; st.pend = null; st.grab = null;
    changed();
  }
  function undo() {
    if (!st || !st.undo.length) return false;
    st.redo.push(stateNow());
    restore(st.undo.pop());
    return true;
  }
  function redo() {
    if (!st || !st.redo.length) return false;
    st.undo.push(stateNow());
    restore(st.redo.pop());
    return true;
  }

  // ---------------------------------------------------------------- ゼブラを縁にくっつける
  // 縁 line の上に載っているゼブラの点：[{z, k, j, t}]（線分 j の t の所）
  function glue(line) {
    const P = line.pts, n = P.length, segs = line.closed ? n : n - 1, out = [];
    if (segs < 1 || !st.kerbs.length) return out;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of P) { x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]); }
    for (const z of st.kerbs) {
      for (let k = 0; k < z.pts.length; k++) {
        const q = z.pts[k];
        if (q[0] < x0 - GLUE_M || q[0] > x1 + GLUE_M || q[1] < y0 - GLUE_M || q[1] > y1 + GLUE_M) continue;
        let best = null;
        for (let j = 0; j < segs; j++) {
          const r = segProj(P[j], P[(j + 1) % n], q);
          if (r.d <= GLUE_M && (!best || r.d < best.d)) best = { j, t: r.t, d: r.d };
        }
        if (best) out.push({ z, k, j: best.j, t: best.t });
      }
    }
    return out;
  }
  // 点の番号が変わらない直し（点を動かす・ならす）のあと：同じ線分の同じ所へ
  function applyGlue(line, glued) {
    const P = line.pts, n = P.length;
    for (const g of glued) {
      const a = P[g.j], b = P[(g.j + 1) % n];
      if (a && b) g.z.pts[g.k] = [a[0] + (b[0] - a[0]) * g.t, a[1] + (b[1] - a[1]) * g.t];
    }
  }
  // 点の番号が変わる直し（まっすぐ）のあと：いちばん近い所へ載せ直す
  function resnap(line, glued) {
    const P = line.pts, n = P.length, segs = line.closed ? n : n - 1;
    for (const g of glued) {
      const q = g.z.pts[g.k];
      let best = null;
      for (let j = 0; j < segs; j++) {
        const a = P[j], b = P[(j + 1) % n], r = segProj(a, b, q);
        if (r.d <= RESNAP_M && (!best || r.d < best.d)) best = { d: r.d, p: [a[0] + (b[0] - a[0]) * r.t, a[1] + (b[1] - a[1]) * r.t] };
      }
      if (best) g.z.pts[g.k] = best.p;
    }
  }

  // 縁に沿った距離（線の頭から）。{S, L}（輪なら L は1周）
  function lineLen(line) {
    const S = cumLen(line.pts), n = line.pts.length;
    return { S, L: line.closed ? S[n - 1] + dist(line.pts[n - 1], line.pts[0]) : S[n - 1] };
  }
  function paramOf(line, cl, q) {           // q を縁へ下ろした所の、縁に沿った距離
    const P = line.pts, n = P.length;
    let best = null;
    for (let j = 0; j < (line.closed ? n : n - 1); j++) {
      const r = segProj(P[j], P[(j + 1) % n], q);
      if (!best || r.d < best.d) best = { d: r.d, s: cl.S[j] + r.t * dist(P[j], P[(j + 1) % n]) };
    }
    return best;
  }
  function pointAt(line, cl, s) {
    const P = line.pts, n = P.length, segs = line.closed ? n : n - 1;
    s = line.closed ? ((s % cl.L) + cl.L) % cl.L : Math.max(0, Math.min(cl.L, s));
    for (let j = 0; j < segs; j++) {
      const s1 = j + 1 < n ? cl.S[j + 1] : cl.L;
      if (s <= s1 || j === segs - 1) {
        const a = P[j], b = P[(j + 1) % n], L = s1 - cl.S[j], t = L > 0 ? Math.max(0, Math.min(1, (s - cl.S[j]) / L)) : 0;
        return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      }
    }
    return P[n - 1].slice();
  }
  // 縁の s0 から向き dir（+1 頭→終わり、-1 逆）へ len だけの点（縁の点と両端）
  function pathAlong(line, cl, s0, dir, len) {
    const P = line.pts, out = [pointAt(line, cl, s0)], vs = [];
    for (let j = 0; j < P.length; j++) {
      let u = (cl.S[j] - s0) * dir;
      if (line.closed) u = ((u % cl.L) + cl.L) % cl.L;
      if (u > 1e-6 && u < len - 1e-6) vs.push([u, j]);
    }
    vs.sort((a, b) => a[0] - b[0]);
    for (const [, j] of vs) out.push(P[j].slice());
    out.push(pointAt(line, cl, s0 + dir * len));
    return out;
  }
  // ゼブラが載っている縁（真ん中の点のいちばん近い縁）
  function lineUnder(z) {
    const q = z.pts[Math.floor(z.pts.length / 2)];
    let best = null;
    for (const l of st.lines) {
      const n = l.pts.length;
      for (let i = 0; i < (l.closed ? n : n - 1); i++) {
        const d = segProj(l.pts[i], l.pts[(i + 1) % n], q).d;
        if (!best || d < best.d) best = { d, l };
      }
    }
    return best && best.d <= 1 ? best.l : null;
  }
  function interp(X, Y, x) {
    if (x <= X[0]) return Y[0];
    for (let i = 1; i < X.length; i++) if (x <= X[i]) { const t = X[i] > X[i - 1] ? (x - X[i - 1]) / (X[i] - X[i - 1]) : 0; return Y[i - 1] + (Y[i] - Y[i - 1]) * t; }
    return Y[Y.length - 1];
  }

  // ---------------------------------------------------------------- 拾う
  // いちばん近い縁の点 {kind 'line', idx, i, d}（m）
  function pickVertex(x, y, tol) {
    let best = null;
    st.lines.forEach((it, idx) => it.pts.forEach((p, i) => {
      const d = Math.hypot(p[0] - x, p[1] - y);
      if (d <= tol && (!best || d < best.d)) best = { kind: 'line', idx, i, d };
    }));
    return best;
  }
  // ゼブラのつかみ所：両端（内側の端の頭・終わり、kind 'kerbEnd'）と外側の端の点（kind 'kerbW'）。端を先に
  function pickHandle(x, y, tol) {
    let end = null, w = null;
    st.kerbs.forEach((z, idx) => {
      const m = z.pts.length;
      for (const i of [0, m - 1]) {
        const d = Math.hypot(z.pts[i][0] - x, z.pts[i][1] - y);
        if (d <= tol && (!end || d < end.d)) end = { kind: 'kerbEnd', idx, i, d };
      }
      outerOf(z).forEach((p, i) => {
        const d = Math.hypot(p[0] - x, p[1] - y);
        if (d <= tol && (!w || d < w.d)) w = { kind: 'kerbW', idx, i, d };
      });
    });
    return end || w;
  }
  // 線分まで含めていちばん近い所（点の番号はその線分の近いほうの端）。ゼブラは帯の真ん中の線で、帯の内なら拾う
  function pickPath(x, y, tol, kinds) {
    let best = null;
    const scan = (pts, closed, kind, idx, extra) => {
      const n = pts.length;
      for (let i = 0; i < (closed ? n : n - 1); i++) {
        const r = segProj(pts[i], pts[(i + 1) % n], [x, y]);
        const d = Math.max(0, r.d - extra);
        if (d <= tol && (!best || d < best.d)) best = { kind, idx, i: r.t < 0.5 ? i : (i + 1) % n, d };
      }
    };
    if (kinds.includes('line')) st.lines.forEach((l, idx) => scan(l.pts, l.closed, 'line', idx, 0));
    if (kinds.includes('kerb')) st.kerbs.forEach((z, idx) => scan(midOf(z), false, 'kerb', idx, Math.max(...z.ws) / 2));
    return best;
  }
  const itemOf = (p) => (p.kind === 'line' ? st.lines[p.idx] : st.kerbs[p.idx]);
  const isClosed = (p) => p.kind === 'line' && st.lines[p.idx].closed;
  function posOf(p) {                // 印を出す所
    const it = itemOf(p);
    if (!it || !it.pts[p.i]) return null;
    if (p.kind === 'kerbW') return outerOf(it)[p.i];
    if (p.kind === 'kerb') return midOf(it)[p.i];
    return it.pts[p.i];
  }

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
    const glued = glue(it);
    const a = it.pts[from % n], b = it.pts[to % n];
    if (!closed) {
      it.pts = it.pts.slice(0, from + 1).concat(between(a, b), it.pts.slice(to));
    } else {                        // from を頭に回してから、頭〜to を入れ替える
      const r = it.pts.slice(from).concat(it.pts.slice(0, from));
      const t = to - from;
      it.pts = [r[0]].concat(between(a, b), r.slice(t));
    }
    resnap(it, glued);
    return true;
  }

  // ならす：線に沿った距離で重みをつけた平均（ガウス、幅 σ）を2回重ね、2·G(x) − G(G(x)) をとる。
  // 1回だけの平均はカーブを内側へ縮めるので、その縮みを打ち消してカーブの形を残す（細かい波だけを消す）。
  // 区間の外の点も平均には使い（動かさない）、区間の両端は動かさず、端から σ のあいだで少しずつ効かせてつなぎ目に角を作らない。
  function smooth(p, q) {
    const it = itemOf(p), n = it.pts.length, closed = isClosed(p);
    if (n < 3) return false;
    const sigma = Math.max(1, ui.radius() / 2), reach = 3 * sigma;
    let from, to, ring = false;
    if (p.i === q.i) {               // 同じ所を2回：線1本まるごと
      if (closed) { from = 0; to = n; ring = true; } else { from = 0; to = n - 1; }
    } else ({ from, to } = span(it, closed, p.i, q.i));
    if (to - from < 2) return false;
    const P = it.pts;
    const S = cumLen(P);             // 頭からの、線に沿った距離
    const L = closed ? S[n - 1] + dist(P[n - 1], P[0]) : S[n - 1];
    const gauss = (src, k) => {     // 点 k のまわりの重みつき平均（輪は回り込み、開いた線は端で止める）
      let sx = 0, sy = 0, sw = 0;
      const one = (j, d) => { const w = Math.exp(-(d * d) / (2 * sigma * sigma)); sx += w * src[j][0]; sy += w * src[j][1]; sw += w; };
      one(k, 0);
      for (const dir of [1, -1]) {
        for (let c = 1; c < n; c++) {
          let j = k + dir * c;
          if (closed) j = ((j % n) + n) % n; else if (j < 0 || j >= n) break;
          let d = Math.abs(S[j] - S[k]);
          if (closed) d = Math.min(d, L - d);
          if (d > reach || (closed && 2 * c > n)) break;
          one(j, d);
        }
      }
      return [sx / sw, sy / sw];
    };
    const g1 = P.map((pt, j) => gauss(P, j));   // 1回目は線全体（数千点 × 窓の十数点なので軽い）
    // 区間の中の、端からの距離（σ のあいだで効きを 0 → 1）
    const along = [0];
    for (let k = from + 1; k <= to; k++) along.push(along[along.length - 1] + dist(P[(k - 1) % n], P[k % n]));
    const total = along[along.length - 1], ramp = Math.min(sigma, total / 2);
    snapshot();
    const glued = glue(it);
    const out = P.map((pt) => pt.slice());
    for (let k = from; k <= to; k++) {
      const i = k % n;
      let w = 1;
      if (!ring) {
        const e = Math.min(along[k - from], total - along[k - from]);
        const t = ramp > 0 ? Math.min(1, e / ramp) : 1;
        w = t * t * (3 - 2 * t);
      }
      if (w <= 0) continue;
      const g2 = gauss(g1, i);
      const sx = 2 * g1[i][0] - g2[0], sy = 2 * g1[i][1] - g2[1];
      out[i] = [P[i][0] + w * (sx - P[i][0]), P[i][1] + w * (sy - P[i][1])];
    }
    it.pts = out;
    applyGlue(it, glued);
    return true;
  }

  function erase(p, q) {
    const it = itemOf(p), n = it.pts.length, closed = isClosed(p);
    const { from, to } = span(it, closed, p.i, q.i);
    if (to - from < 1) return false;
    snapshot();
    if (p.kind === 'kerb') {        // ゼブラ：そのあいだを抜いて、前後の2本に
      const made = [[0, from], [to, n - 1]].filter(([a, b]) => b - a >= 1)
        .map(([a, b]) => ({ pts: it.pts.slice(a, b + 1), ws: it.ws.slice(a, b + 1), side: it.side }));
      st.kerbs.splice(p.idx, 1, ...made);
      return true;
    }
    let pieces;
    if (!closed) {
      pieces = [it.pts.slice(0, from + 1), it.pts.slice(to)];
    } else {                        // 輪を開く：to から回って from まで
      const r = it.pts.slice(from).concat(it.pts.slice(0, from));
      pieces = [r.slice(to - from).concat([r[0]])];
    }
    st.lines.splice(p.idx, 1, ...pieces.filter((x) => x.length >= 2).map((pts) => ({ pts, closed: false })));
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
    for (let k = from; k <= to; k++) pts.push(it.pts[k % n].slice());
    // 外向き：走った線（基準の周）から離れる向き。真ん中の点で決める
    const m = Math.floor(pts.length / 2), mid = pts[m], nm = normalAt(pts, m);
    const tr = geo.nearestTrack(mid[0], mid[1]);
    const side = tr && (mid[0] - tr[0]) * nm[0] + (mid[1] - tr[1]) * nm[1] < 0 ? -1 : 1;
    snapshot();
    st.kerbs.push({ pts, ws: taperWidths(pts, ui.zebraW(), ui.zebraTaper ? ui.zebraTaper() : 0), side });
    return true;
  }

  // ---------------------------------------------------------------- なぞって吸い付かせる
  // コースの側（走った線のある側）が、なぞった向きの左なら 1、右なら -1（走った線が無ければ 0）
  function insideOf(pts, geo) {
    if (!geo || !geo.nearestTrack) return 0;
    let v = 0;
    const step = Math.max(1, Math.floor(pts.length / 9));
    for (let k = step; k < pts.length - 1; k += step) {
      const tr = geo.nearestTrack(pts[k][0], pts[k][1]);
      if (!tr) continue;
      const a = pts[k - 1], b = pts[k + 1], tx = b[0] - a[0], ty = b[1] - a[1];
      v += Math.sign(tx * (tr[1] - pts[k][1]) - ty * (tr[0] - pts[k][0]));
    }
    return v > 0 ? 1 : v < 0 ? -1 : 0;
  }
  // 線の上で q にいちばん近い点の番号と距離
  function nearestIndex(pts, q) {
    let bi = -1, bd = Infinity;
    for (let i = 0; i < pts.length; i++) { const d = dist(pts[i], q); if (d < bd) { bd = d; bi = i; } }
    return { i: bi, d: bd };
  }
  // 吸い付いた線 snapped（m の点の並び）を図へ入れる。両端の attach（m）の内に同じ縁があれば、そのあいだを置き換え
  // （輪なら、なぞった長さに近いほうのあいだ）、無ければ新しい縁として足す。{how: 'replace'|'add'}
  function applyTrace(snapped, attach) {
    if (!st || snapped.length < 2) return null;
    const A = snapped[0], B = snapped[snapped.length - 1];
    let best = null;
    st.lines.forEach((l, idx) => {
      if (l.pts.length < 2) return;
      const a = nearestIndex(l.pts, A), b = nearestIndex(l.pts, B);
      if (a.d <= attach && b.d <= attach && a.i !== b.i && (!best || a.d + b.d < best.d)) best = { idx, ia: a.i, ib: b.i, d: a.d + b.d };
    });
    snapshot();
    if (!best) {
      st.lines.push({ pts: snapped.map((q) => q.slice()), closed: false });
      changed();
      return { how: 'add' };
    }
    const it = st.lines[best.idx], n = it.pts.length, glued = glue(it);
    let ia = best.ia, ib = best.ib;
    const s = snapped.map((q) => q.slice()), L = cumLen(s)[s.length - 1];
    if (!it.closed) {
      if (ia > ib) { s.reverse(); [ia, ib] = [ib, ia]; }
      it.pts = it.pts.slice(0, ia).concat(s, it.pts.slice(ib + 1));
    } else {
      const arc = (i, j) => { let d = 0; for (let k = i; k % n !== j; k++) d += dist(it.pts[k % n], it.pts[(k + 1) % n]); return d; };
      if (Math.abs(arc(ib, ia) - L) < Math.abs(arc(ia, ib) - L)) { s.reverse(); [ia, ib] = [ib, ia]; }
      const keep = [];
      for (let k = ib + 1; k % n !== ia; k++) keep.push(it.pts[k % n]);
      it.pts = s.concat(keep);
    }
    resnap(it, glued);
    changed();
    return { how: 'replace' };
  }
  function traceDown(x, y, geo) {
    st.trace = { pts: [[x, y]], geo };
    return true;
  }
  function traceMove(x, y) {
    const tr = st.trace, last = tr.pts[tr.pts.length - 1];
    if (dist(last, [x, y]) * tr.geo.k >= TRACE_STEP_PX) tr.pts.push([x, y]);
  }
  function traceUp() {
    const tr = st.trace;
    st.trace = null;
    if (!tr || tr.pts.length < 2) return;
    const pts = tr.pts, len = cumLen(pts)[pts.length - 1];
    if (len < TRACE_MIN_M) { if (ui.onTrace) ui.onTrace(`なぞった線が短すぎます（${TRACE_MIN_M} m より長くなぞってください）`, false); return; }
    if (!ui.snap) return;
    const k = tr.geo.k, mine = st, job = { pts };
    st.busy.push(job);
    if (ui.onTrace) ui.onTrace('吸い付かせています…', true);
    ui.snap(pts, insideOf(pts, tr.geo), TRACE_REACH_PX / k).then((r) => {
      if (st !== mine) return;              // 直すのをやめた・開き直した
      st.busy = st.busy.filter((j) => j !== job);
      const res = applyTrace(r.pts, TRACE_ATTACH_PX / k);
      if (ui.onTrace) ui.onTrace(`${res.how === 'replace' ? 'そのあいだの縁を置き換えました' : '新しい縁として足しました'}（縁が見つかった所 ${Math.round(r.found * 100)} %、なぞった線から動かした量 ${r.shift_m.toFixed(1)} m。戻すは Ctrl+Z）`, true);
    }).catch((e) => {
      if (st !== mine) return;
      st.busy = st.busy.filter((j) => j !== job);
      if (ui.onTrace) ui.onTrace(`吸い付かせられませんでした（${(e && e.message) || e}）`, false);
    });
  }

  // ---------------------------------------------------------------- マウス（map.js から。x・y は m、geo {k 画素/m, nearestTrack}）
  function tolM(geo) { return PICK_PX / geo.k; }

  // 点を動かすでつかめる所：縁の点か、ゼブラの端・外側の端の近いほう（ゼブラの端は縁の点より先）
  function pickGrab(x, y, tol) {
    const a = pickVertex(x, y, tol), b = pickHandle(x, y, tol);
    if (a && b) return b.kind === 'kerbEnd' || b.d <= a.d ? b : a;
    return a || b;
  }

  // 点 i から、点の並びに沿った距離（輪なら近いほうの向きで）
  function alongFrom(pts, closed, i) {
    const n = pts.length, s = new Array(n).fill(Infinity);
    if (closed) {
      const fwd = new Array(n).fill(0);
      for (let c = 1; c < n; c++) fwd[(i + c) % n] = fwd[(i + c - 1) % n] + dist(pts[(i + c - 1) % n], pts[(i + c) % n]);
      const L = fwd[(i + n - 1) % n] + dist(pts[(i + n - 1) % n], pts[i]);
      for (let k = 0; k < n; k++) s[k] = Math.min(fwd[k], L - fwd[k]);
    } else {
      s[i] = 0;
      for (let k = i + 1; k < n; k++) s[k] = s[k - 1] + dist(pts[k - 1], pts[k]);
      for (let k = i - 1; k >= 0; k--) s[k] = s[k + 1] + dist(pts[k + 1], pts[k]);
    }
    return s;
  }
  const falloff = (s, R, here) => (here ? 1 : R > 0 && s < R ? (1 - (s / R) ** 2) ** 2 : 0);

  // 押した：つかんだら true（図を動かさない）
  function down(x, y, geo) {
    if (st && st.tool === 'trace') return traceDown(x, y, geo);
    if (!st || st.tool !== 'move') return false;
    const p = pickGrab(x, y, tolM(geo));
    if (!p) return false;
    const g = { p, x0: x, y0: y, keepRedo: st.redo, R: Math.max(0, ui.radius()) };
    if (p.kind === 'line') {
      const it = st.lines[p.idx];
      Object.assign(g, { orig: it.pts.map((q) => q.slice()), s: alongFrom(it.pts, it.closed, p.i), glued: glue(it) });
    } else if (p.kind === 'kerbW') {
      const z = st.kerbs[p.idx];
      Object.assign(g, { origW: z.ws.slice(), nrm: normalAt(z.pts, p.i), s: alongFrom(z.pts, false, p.i) });
    } else {                         // ゼブラの端：縁に沿って伸び縮み
      const z = st.kerbs[p.idx], line = lineUnder(z);
      if (!line || z.pts.length < 2) return false;
      const fixedIsLast = p.i === 0;
      const ord = fixedIsLast ? z.pts.slice().reverse() : z.pts.slice();     // 動かない端から
      const cl = lineLen(line), sF = paramOf(line, cl, ord[0]).s, s2 = paramOf(line, cl, ord[1]).s;
      let dir = s2 - sF;
      if (line.closed && Math.abs(dir) > cl.L / 2) dir = -dir;
      Object.assign(g, { line, cl, sF, dir: dir >= 0 ? 1 : -1, fixedIsLast, U: cumLen(ord),
                         W: fixedIsLast ? z.ws.slice().reverse() : z.ws.slice(), origPts: z.pts.map((q) => q.slice()) });
    }
    snapshot();
    st.grab = g;
    return true;
  }

  function move(x, y) {
    if (st && st.trace) { traceMove(x, y); return; }
    const g = st && st.grab;
    if (!g) return;
    const dx = x - g.x0, dy = y - g.y0;
    if (g.p.kind === 'line') {
      const it = st.lines[g.p.idx];
      for (let k = 0; k < it.pts.length; k++) {
        const w = falloff(g.s[k], g.R, k === g.p.i);
        it.pts[k] = [g.orig[k][0] + w * dx, g.orig[k][1] + w * dy];
      }
      applyGlue(it, g.glued);
    } else if (g.p.kind === 'kerbW') {
      const z = st.kerbs[g.p.idx], d = (dx * g.nrm[0] + dy * g.nrm[1]) * z.side;
      for (let k = 0; k < z.ws.length; k++) z.ws[k] = clampW(g.origW[k] + falloff(g.s[k], g.R, k === g.p.i) * d);
    } else {
      const z = st.kerbs[g.p.idx], { line, cl } = g;
      let len = (paramOf(line, cl, [x, y]).s - g.sF) * g.dir;
      if (line.closed) {
        len = ((len % cl.L) + cl.L) % cl.L;
        if (len > cl.L - MIN_LEN_M) len = MIN_LEN_M;            // 1周を越えたら、縮めすぎとみる
      } else len = Math.min(len, g.dir > 0 ? cl.L - g.sF : g.sF);
      len = Math.max(MIN_LEN_M, len);
      const pts = pathAlong(line, cl, g.sF, g.dir, len), U = cumLen(pts);
      const Lold = g.U[g.U.length - 1], Lnew = U[U.length - 1], R = Math.min(END_KEEP_M, Lold / 2, Lnew / 2);
      const wOld = (u) => interp(g.U, g.W, u);
      // 動く端から R の内の幅の形（細り）は端と一緒に動き、伸ばした所は手前の幅で埋める
      const ws = U.map((u) => (u >= Lnew - R ? wOld(Lold - (Lnew - u)) : u <= Lold - R ? wOld(u) : wOld(Lold - R)));
      z.pts = g.fixedIsLast ? pts.reverse() : pts;
      z.ws = g.fixedIsLast ? ws.reverse() : ws;
    }
  }
  function up() {
    if (st && st.trace) { traceUp(); return; }
    if (!st || !st.grab) return;
    const g = st.grab;
    st.grab = null;
    let moved;
    if (g.p.kind === 'line') moved = dist(st.lines[g.p.idx].pts[g.p.i], g.orig[g.p.i]) > 1e-6;
    else if (g.p.kind === 'kerbW') moved = st.kerbs[g.p.idx].ws.some((w, k) => Math.abs(w - g.origW[k]) > 1e-6);
    else { const z = st.kerbs[g.p.idx]; moved = z.pts.length !== g.origPts.length || z.pts.some((q, k) => dist(q, g.origPts[k]) > 1e-6); }
    if (!moved) { st.undo.pop(); st.redo = g.keepRedo; } else changed();   // 動かさなければ無かったことに
  }

  // 押して離した（動かさずに）
  function click(x, y, geo) {
    if (!st) return;
    const tol = tolM(geo), t = st.tool;
    let done = false;
    if (t === 'smooth' || t === 'straight' || t === 'erase' || t === 'zebraAdd') {
      const kinds = t === 'erase' ? ['line', 'kerb'] : ['line'];
      const p = pickPath(x, y, tol, kinds);
      if (!p) { st.pend = null; return; }
      if (!st.pend) { st.pend = p; return; }
      const a = st.pend;
      st.pend = null;
      if (a.kind !== p.kind || a.idx !== p.idx) { st.pend = p; return; }   // 別の線なら、押し直しの1か所目にする
      done = t === 'smooth' ? smooth(a, p) : t === 'straight' ? straighten(a, p) : t === 'erase' ? erase(a, p) : zebraAdd(a, p, geo);
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
      const keepRedo = st.redo;
      snapshot();
      if (hit >= 0) st.boards.splice(hit, 1);
      else {
        const text = ui.boardText();
        if (!text) { st.undo.pop(); st.redo = keepRedo; return; }
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
    if (t === 'move') st.hover = pickGrab(x, y, tol);
    else if (t === 'join') st.hover = pickEnd(x, y, tol);
    else if (t === 'zebraDel') st.hover = pickPath(x, y, tol, ['kerb']);
    else if (t === 'board' || t === 'trace') st.hover = null;
    else st.hover = pickPath(x, y, tol, t === 'erase' ? ['line', 'kerb'] : ['line']);
    return st.hover ? (t === 'move' ? 'grab' : 'pointer') : t === 'board' ? 'copy' : t === 'trace' ? 'crosshair' : null;
  }

  // 図の上に、点・押した1か所目・拾える所を描く（コース図そのものは map.js が view() から描く）
  function overlay(g, P, k, dark) {
    if (!st) return;
    const dot = (pt, r, fill, stroke) => { const [px, py] = P(pt[0], pt[1]); g.beginPath(); g.arc(px, py, r, 0, 2 * Math.PI); g.fillStyle = fill; g.fill(); if (stroke) { g.strokeStyle = stroke; g.lineWidth = 1.5; g.stroke(); } };
    const box = (pt, r, fill, stroke) => { const [px, py] = P(pt[0], pt[1]); g.fillStyle = fill; g.strokeStyle = stroke; g.lineWidth = 1.5; g.fillRect(px - r, py - r, 2 * r, 2 * r); g.strokeRect(px - r, py - r, 2 * r, 2 * r); };
    g.save();
    if (k * STEP_M >= 6) {          // 寄ったときだけ点を出す
      const c = dark ? 'rgba(255, 255, 255, 0.8)' : 'rgba(31, 41, 55, 0.7)';
      for (const it of st.lines) for (const pt of it.pts) dot(pt, 1.6, c);
      if (st.tool === 'move') for (const z of st.kerbs) for (const pt of outerOf(z)) dot(pt, 1.6, '#dc2626');   // ゼブラの幅をつかむ所
    }
    for (const l of st.lines) if (!l.closed && l.pts.length) {   // 開いた線の端（つなぐ所）
      for (const pt of [l.pts[0], l.pts[l.pts.length - 1]]) dot(pt, 4, '#f59e0b', dark ? '#111827' : '#ffffff');
    }
    if (st.tool === 'move' && k * STEP_M >= 3) {                 // ゼブラの両端（伸び縮みさせる所）
      for (const z of st.kerbs) for (const pt of [z.pts[0], z.pts[z.pts.length - 1]]) box(pt, 3.5, '#dc2626', dark ? '#111827' : '#ffffff');
    }
    const stroke = (pts, color, dash) => {    // なぞっている線・裏で吸い付かせている線
      if (pts.length < 2) return;
      g.strokeStyle = color; g.lineWidth = 2; g.setLineDash(dash);
      g.beginPath(); pts.forEach((q, i) => { const [px, py] = P(q[0], q[1]); if (i) g.lineTo(px, py); else g.moveTo(px, py); }); g.stroke();
      g.setLineDash([]);
    };
    for (const j of st.busy) stroke(j.pts, '#3b82f6', [4, 4]);
    if (st.trace) stroke(st.trace.pts, '#3b82f6', []);
    const H = st.hover && posOf(st.hover);
    if (H) dot(H, 6, 'rgba(59, 130, 246, 0.35)', '#3b82f6');
    const D = st.pend && posOf(st.pend);
    if (D) dot(D, 6, '#ef4444', '#ffffff');
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
    st.redo = st.redo.map((u) => JSON.stringify(mv(JSON.parse(u))));
    st.pend = null; st.grab = null; st.hover = null; st.trace = null; st.busy = [];
  }

  return {
    TOOLS, GESTURE, remap,
    load, dump, view, undo, redo, down, move, up, click, hoverAt, overlay, applyTrace,
    active: () => !!st,
    stop() { st = null; },
    dirty: () => !!(st && st.dirty),
    canUndo: () => !!(st && st.undo.length),
    canRedo: () => !!(st && st.redo.length),
    // 押しかけの1か所目（まっすぐ・消す・つなぐ…）を取り消す。取り消したら true
    cancelPending() { if (!st || !st.pend) return false; st.pend = null; return true; },
    tool: () => st && st.tool,
    setTool(t) { if (st && TOOLS[t]) { st.tool = t; st.pend = null; st.hover = null; st.trace = null; } },
    // 試験用：中身の数
    counts: () => st && { lines: st.lines.length, points: st.lines.reduce((a, l) => a + l.pts.length, 0), kerbs: st.kerbs.length, boards: st.boards.length },
  };
})();
