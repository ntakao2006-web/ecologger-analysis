// 印刷向けの1枚のレポート（SP-10-43。仕様 analysis_map_features.md 3-8）。いま選んでいる周・基準の周・区間の線・まとめの使う周のまま、
// A4 横1枚に「走行の名前・コース図・速さとタイムの差のグラフ・走行のまとめ・伸びしろ・区間の表・周の表」を並べ、ブラウザの印刷で紙か PDF に出す。
// 画面の暗い色づかいにかかわらず、紙は白地。地図とグラフはこのファイルで描き直す（画面の枠の大きさ・航空写真に左右されないように）。
// 入りきらないときは字を小さくして1枚に収める（FONT_MIN まで）
'use strict';

const Report = (() => {
  const LIGHT = ['', '#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'];   // 周の色（明るい地の版。style.css の --s1〜8）
  const FONT_MAX = 10.5, FONT_MIN = 7, FONT_STEP = 0.25;   // ページの字の大きさ（px）。入りきるまで小さくする
  const CHART_LAPS = 4;          // グラフに重ねる周の数（基準の周を先に）
  const SEC_LAPS = 3;            // 区間の表に並べる周の数（基準の周を先に）
  const MAP_PAD_M = 25;          // コース図の、走った線のまわりの余白（m）
  const SPEED_STOPS = [[37, 99, 235], [245, 158, 11], [220, 38, 38]];   // 速さの色：遅い＝青 → 黄 → 速い＝赤
  let root = null;

  const has = (v) => v !== null && v !== undefined && isFinite(v);
  const color = (key) => LIGHT[state.colors.get(key) || 1] || LIGHT[1];
  // 周の短い名前：「31周」。別の走行の周が混ざるときは日付を前に（走行の名前は見出しに1回だけ出す）
  function name(key) {
    const [id, n] = String(key).split(':');
    const ids = new Set(state.overlay.laps.map((l) => String(l.key).split(':')[0]));
    const s = state.sessions.find((x) => x.id === id);
    return ids.size > 1 && s ? `${jstDate(s.t0_utc).slice(5, 10).replace('-', '/')} ${n}周` : `${n}周`;
  }

  // 開けるか（開けないときはわけ）。基準の周の線（距離）が要る
  function why() {
    const o = state.overlay;
    if (!o || !o.laps.length) return '周の表で周を選ぶと、レポートを作れます';
    if (!o.distance) return '測位が無い周なので、レポートを作れません（コース図・距離のグラフが要ります）';
    if (state.draft || state.secdraft || state.outedit) return '線・区間・コース図を直している間は作れません（保存かやめるのあとで）';
    return null;
  }

  function open() {
    const w = why();
    if (w) { status(w, 'ng'); return; }
    close();
    root = el('div', { id: 'reportview', class: 'report' });
    const print = el('button', { type: 'button', class: 'primary', title: '印刷の窓が開きます。PDF にするときは、プリンターに「Microsoft Print to PDF」か「PDF に保存」を選びます。用紙は A4 横' }, '印刷・PDF…');
    print.onclick = () => window.print();
    const shut = el('button', { type: 'button' }, '閉じる');
    shut.onclick = close;
    root.append(el('div', { class: 'rtools' }, el('b', {}, 'レポート（A4 横1枚）'), print, shut,
      el('span', { class: 'note' }, 'いま選んでいる周・基準の周・区間の線・まとめに使う周のままで作ります。変えたいときは閉じて選び直し、もう一度「レポート」を押します。Esc で閉じます')));
    const page = el('div', { class: 'rpage' });
    root.append(page);
    document.body.append(root);
    document.body.classList.add('reportopen');
    build(page);
    fit(page);
    draw(page);
    document.addEventListener('keydown', onKey);
  }

  function close() {
    if (!root) return;
    root.remove(); root = null;
    document.body.classList.remove('reportopen');
    document.removeEventListener('keydown', onKey);
  }
  function onKey(e) { if (e.key === 'Escape') close(); }

  // ---------------------------------------------------------------- 中身
  function build(page) {
    const o = state.overlay, s = state.refSession || pickedLaps()[0].s;
    const ref = o.laps[o.ref];
    const others = o.laps.filter((l, i) => i !== o.ref);
    const sub = [s.course && !String(s.title).includes(s.course) ? `コース ${s.course}` : null, jstDate(s.t0_utc), `${Math.round(s.duration_s / 60)}分`,
                 `基準 ${name(ref.key)} ${lapTime(ref.time_s)}`, others.length ? `比べる ${others.slice(0, CHART_LAPS - 1).map((l) => `${name(l.key)} ${lapTime(l.time_s)}`).join('・')}` : null]
      .filter(Boolean).join('　');
    page.append(el('div', { class: 'rhead' }, el('h2', {}, s.title), el('span', { class: 'rsub' }, sub),
      el('span', { class: 'rmade' }, `作成 ${jstDate(new Date().toISOString())}　EcoLogger Analysis`)));
    const left = el('div', { class: 'rleft' },
      el('div', { class: 'rbox rmapbox' }, el('h3', {}, 'コース図（基準の周を速さで塗る）'), el('canvas', { class: 'rmap' })),
      el('div', { class: 'rbox rchartbox' }, el('h3', {}, '速さとタイムの差（横軸は基準の周の距離）'), el('canvas', { class: 'rchart' })));
    const right = el('div', { class: 'rright' });
    right.append(section('まとめ', summaryBlock(s)));
    const opp = oppBlockCopy();
    if (opp) right.append(section('伸びしろ', opp));
    const sec = sectionTable(s);
    if (sec) right.append(section('区間', sec));
    right.append(section(`周（この走行の${lapsOf(s).length}周。紫＝ベスト、点＝選んでいる周）`, lapGrid(s)));
    page.append(el('div', { class: 'rbody' }, left, right));
  }

  function section(title, body) { return el('div', { class: 'rbox' }, el('h3', {}, title), body); }

  // 画面の操作の部品（選ぶ・チェック・ボタン）を、選んでいる中身の字に置き換える（紙では押せないため）
  function freeze(node) {
    for (const x of node.querySelectorAll('select')) x.replaceWith(el('span', { class: 'rpick' }, x.selectedOptions[0] ? x.selectedOptions[0].textContent : ''));
    for (const x of node.querySelectorAll('label')) if (!x.textContent.trim() || x.querySelector('input')) x.remove();   // 「地図に出す」などの切り替え
    for (const x of node.querySelectorAll('button, input')) x.remove();
    for (const x of node.querySelectorAll('[title]')) x.removeAttribute('title');
    return node;
  }

  function summaryBlock(s) {
    const box = freeze(summaryBar(s));
    box.classList.add('rsum');
    return box;
  }

  // 伸びしろ：画面の区間の枠に出ている分を写す（同じ計算・同じ比べる相手）
  function oppBlockCopy() {
    const src = document.querySelector('#secbox .opps');
    if (!src || $('secbox').hidden) return null;
    const box = freeze(src.cloneNode(true));
    const head = box.querySelector('.opphead');
    if (head) { const b = head.querySelector('b'); if (b) b.remove(); }
    return box;
  }

  // 区間の表（基準の周を先に SEC_LAPS 周）：区間ごとのベストと、周ごとのタイム（紫＝その区間のベスト、緑・赤＝基準の周より速い・遅い）、ボトムの速さ
  function sectionTable(s) {
    if (state.mode !== 'app' || !s.laps.app || !(s.splits || []).length) return null;
    const { laps } = sectionLaps();
    if (!laps.length) return null;
    const rows = laps.slice(0, SEC_LAPS), st = s.stats && s.stats.splits;
    const n = rows[0].x.sec.length, names = s.split_names || [];
    const head = el('tr', {}, el('th', {}, '区間'), el('th', {}, 'ベスト'),
      ...rows.map((r, k) => el('th', {}, el('span', { class: 'lapdot', style: `background:${color(r.key)}` }), `${r.lap}周${k ? '' : '（基準）'}`)),
      el('th', {}, 'ボトム km/h'));
    const body = el('tbody');
    for (let i = 0; i < n; i++) {
      const best = st ? st.best[i] : null, rt = (rows[0].x.splits || [])[i];
      const cells = rows.map((r, k) => {
        const t = (r.x.splits || [])[i];
        let cls = 'num';
        if (has(t) && has(best) && Math.abs(t - best) < 0.0005) cls += ' sbest';
        else if (k > 0 && has(t) && has(rt)) cls += t < rt - 0.0005 ? ' faster' : t > rt + 0.0005 ? ' slower' : '';
        return el('td', { class: cls }, has(t) ? t.toFixed(3) + (has(best) && Math.abs(t - best) >= 0.0005 ? ` (${gap(t - best)})` : '') : '—');
      });
      const bottoms = rows.map((r) => { const m = r.x.sec[i] || {}; return has(m.min_kmh) ? m.min_kmh.toFixed(0) : '—'; }).join(' / ');
      body.append(el('tr', {}, el('td', {}, `${i + 1}${names[i] ? ' ' + names[i] : ''}`), el('td', { class: 'num' }, has(best) ? best.toFixed(3) : '—'),
        ...cells, el('td', { class: 'num' }, bottoms)));
    }
    const wrap = el('div', {});
    wrap.append(el('table', { class: 'rtable' }, el('thead', {}, head), body));
    wrap.append(el('p', { class: 'rnote' }, '括弧はその区間のベストとの差。紫＝その区間のベスト、緑・赤＝基準の周より速い・遅い。ボトムは区間の中の最小の速さ（並びは左の周の順）'));
    return wrap;
  }

  // 周の表：この走行の全部の周を段組みで（紫の太字＝ベスト、色の点＝選んでいる周、灰色＝測位の抜けた周）
  function lapGrid(s) {
    const laps = lapsOf(s);
    const ok = laps.filter((x) => !x.gap && has(x.time_s));
    const best = ok.length ? Math.min(...ok.map((x) => x.time_s)) : null;
    const grid = el('div', { class: 'rlaps' });
    for (const x of laps) {
      const key = `${s.id}:${x.lap}`, picked = state.picked.has(key);
      const isBest = has(best) && !x.gap && Math.abs(x.time_s - best) < 0.0005;
      grid.append(el('div', { class: 'rlap' + (x.gap ? ' rgap' : '') + (isBest ? ' rbest' : '') },
        el('span', { class: 'rlapno' }, picked ? el('span', { class: 'lapdot', style: `background:${color(key)}` }) : '', String(x.lap)),
        el('span', { class: 'rlapt' }, lapTime(x.time_s)),
        el('span', { class: 'rlapg' }, x.gap ? '抜け' : has(best) && !isBest ? shortGap(x.time_s - best) : '')));
    }
    return grid;
  }

  // 周の表の差：100秒を超える（赤旗・ピットで止まった周）は秒の整数で（欄からはみ出さないように）
  function shortGap(d) { return Math.abs(d) >= 100 ? `+${Math.round(d)}` : (Math.abs(d) < 0.0005 ? '0.000' : (d > 0 ? '+' : '−') + Math.abs(d).toFixed(3)); }

  // 入りきるまで字を小さくする（ページの下の端からはみ出さないように）
  function fit(page) {
    const body = page.querySelector('.rbody');
    let f = FONT_MAX;
    page.style.fontSize = f + 'px';
    while (f > FONT_MIN && (body.scrollHeight > body.clientHeight + 1 || page.querySelector('.rright').scrollHeight > body.clientHeight + 1)) {
      f -= FONT_STEP;
      page.style.fontSize = f + 'px';
    }
    page.dataset.font = f.toFixed(2);
    page.classList.toggle('roverflow', body.scrollHeight > body.clientHeight + 1);
  }

  // ---------------------------------------------------------------- 地図とグラフ（紙の解像度で描く）
  function canvasFor(cv) {
    const r = cv.getBoundingClientRect(), k = 3;            // 印刷でにじまないよう、画面の3倍の画素で描く
    cv.width = Math.max(1, Math.round(r.width * k)); cv.height = Math.max(1, Math.round(r.height * k));
    const g = cv.getContext('2d');
    g.setTransform(k, 0, 0, k, 0, 0);
    return { g, w: r.width, h: r.height };
  }

  function draw(page) {
    drawMap(page.querySelector('.rmap'));
    drawChart(page.querySelector('.rchart'));
  }

  function speedColor(f) {
    f = Math.max(0, Math.min(1, f));
    const seg = f < 0.5 ? 0 : 1, u = f < 0.5 ? f * 2 : (f - 0.5) * 2;
    const a = SPEED_STOPS[seg], b = SPEED_STOPS[seg + 1];
    return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * u)).join(',')})`;
  }
  function pct(vals, p) {
    const a = vals.filter(has).sort((x, y) => x - y);
    return a.length ? a[Math.min(a.length - 1, Math.max(0, Math.round((a.length - 1) * p)))] : null;
  }

  function drawMap(cv) {
    const { g, w, h } = canvasFor(cv);
    const o = state.overlay, ref = o.laps[o.ref], t = ref && ref.track;
    if (!t) return;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (let i = 0; i < t.x.length; i++) {
      if (t.x[i] === null) continue;
      x0 = Math.min(x0, t.x[i]); x1 = Math.max(x1, t.x[i]); y0 = Math.min(y0, t.y[i]); y1 = Math.max(y1, t.y[i]);
    }
    x0 -= MAP_PAD_M; x1 += MAP_PAD_M; y0 -= MAP_PAD_M; y1 += MAP_PAD_M;
    const legendH = 16, sc = Math.min(w / (x1 - x0), (h - legendH) / (y1 - y0));
    const ox = (w - (x1 - x0) * sc) / 2, oy = (h - legendH - (y1 - y0) * sc) / 2;
    const P = (x, y) => [ox + (x - x0) * sc, oy + (y1 - y) * sc];
    const inBox = (q) => q[0] >= x0 && q[0] <= x1 && q[1] >= y0 && q[1] <= y1;
    g.lineJoin = 'round'; g.lineCap = 'round';
    // コース図（道路の縁）：薄いグレー。ピットロードは破線、その他の舗装は点線
    const ov = CourseMap.outlineXY && CourseMap.outlineXY();
    if (ov && $('outline').checked) {
      for (const ln of ov.lines) {
        const kind = ln.kind || 'edge';
        g.setLineDash(kind === 'pit' ? [4, 3] : kind === 'other' ? [1, 3] : []);
        g.strokeStyle = kind === 'edge' ? '#b8c0cb' : '#cfd5dd'; g.lineWidth = kind === 'edge' ? 0.9 : 0.7;
        g.beginPath();
        let on = false;
        for (const q of ln.pts) {
          if (!inBox(q)) { on = false; continue; }
          const [px, py] = P(q[0], q[1]);
          if (on) g.lineTo(px, py); else { g.moveTo(px, py); on = true; }
        }
        g.stroke();
      }
      g.setLineDash([]);
    }
    // 伸びしろの大きい区間：太い薄い帯（番号は線の上に）
    const hls = (CourseMap.highlightList && CourseMap.highlightList()) || [];
    const mids = [];
    for (const hl of hls) {
      g.beginPath();
      let on = false, mid = null, md = Infinity;
      for (let i = 0; i < t.x.length; i++) {
        if (t.x[i] === null || t.s[i] === null || t.s[i] < hl.a || t.s[i] > hl.b) { on = false; continue; }
        const [px, py] = P(t.x[i], t.y[i]);
        if (on) g.lineTo(px, py); else { g.moveTo(px, py); on = true; }
        const dm = Math.abs(t.s[i] - (hl.a + hl.b) / 2);
        if (dm < md) { md = dm; mid = [px, py]; }
      }
      g.strokeStyle = 'rgba(17, 24, 39, 0.13)'; g.lineWidth = 11; g.stroke();
      if (mid) mids.push([mid, hl.label]);
    }
    // ほかの周：細く周の色で
    for (const l of o.laps) {
      if (l === ref || !l.track) continue;
      g.strokeStyle = color(l.key); g.globalAlpha = 0.55; g.lineWidth = 0.8;
      line(g, l.track, P);
      g.globalAlpha = 1;
    }
    // 基準の周：速さで塗る（その周の 2〜98%）
    const lo = pct(t.sp || [], 0.02), hi = pct(t.sp || [], 0.98);
    g.lineWidth = 2.2;
    for (let i = 1; i < t.x.length; i++) {
      if (t.x[i] === null || t.x[i - 1] === null) continue;
      const v = t.sp ? t.sp[i] : null;
      g.strokeStyle = has(v) && has(lo) && hi > lo ? speedColor((v - lo) / (hi - lo)) : '#6b7280';
      const [ax, ay] = P(t.x[i - 1], t.y[i - 1]), [bx, by] = P(t.x[i], t.y[i]);
      g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke();
    }
    // コントロールラインと区間の線：基準の周の線の上に印と札
    const L = mapLines();
    const marks = [];
    if (L && L.line) marks.push([L.line, 'ゴール', '#111827']);
    (L && L.splits || []).forEach((ln, i) => marks.push([ln, (L.splitLabels || [])[i] || `区${i + 2}`, '#4b5563']));
    g.font = '600 7.5px "Yu Gothic UI", "Segoe UI", sans-serif'; g.textBaseline = 'middle';
    for (const [ln, label, c] of marks) {
      const r = CourseMap.refAt(ln.lat, ln.lon);
      if (!r) continue;
      const k = nearestIdx(t, r.s);
      if (k < 0) continue;
      const k2 = Math.min(t.x.length - 1, k + 2), k1 = Math.max(0, k - 2);
      const dx = t.x[k2] - t.x[k1], dy = t.y[k2] - t.y[k1], dl = Math.hypot(dx, dy) || 1;
      const nx = -dy / dl, ny = dx / dl;                     // 進む向きの左
      const [px, py] = P(t.x[k], t.y[k]);
      const a = P(t.x[k] + nx * 9, t.y[k] + ny * 9), b = P(t.x[k] - nx * 9, t.y[k] - ny * 9);
      g.strokeStyle = c; g.lineWidth = 1.2; g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke();
      const lx = px + (a[0] - px) * 1.35, ly = py + (a[1] - py) * 1.35;
      g.textAlign = lx < px ? 'right' : 'left';
      g.lineWidth = 2.5; g.strokeStyle = '#ffffff'; g.strokeText(label, lx, ly);
      g.fillStyle = c; g.fillText(label, lx, ly);
    }
    // 伸びしろの番号
    g.font = 'bold 8px "Yu Gothic UI", "Segoe UI", sans-serif'; g.textAlign = 'center';
    for (const [[mx, my], label] of mids) {
      g.beginPath(); g.arc(mx, my, 6, 0, Math.PI * 2); g.fillStyle = '#111827'; g.fill();
      g.lineWidth = 1.2; g.strokeStyle = '#ffffff'; g.stroke();
      g.fillStyle = '#ffffff'; g.fillText(label, mx, my + 0.3);
    }
    // 凡例（速さ）と縮尺
    if (has(lo) && hi > lo) {
      const lw = Math.min(140, w * 0.35), lx = 4, ly = h - legendH + 4;
      for (let i = 0; i < lw; i++) { g.fillStyle = speedColor(i / lw); g.fillRect(lx + i, ly, 1.2, 5); }
      g.fillStyle = '#374151'; g.font = '7px "Yu Gothic UI", "Segoe UI", sans-serif'; g.textAlign = 'left'; g.textBaseline = 'top';
      g.fillText(`${lo.toFixed(0)} km/h`, lx, ly + 6);
      g.textAlign = 'right'; g.fillText(`${hi.toFixed(0)} km/h`, lx + lw, ly + 6);
    }
    const bar = niceStep(80 / sc);
    const bx = w - 6 - bar * sc, by = h - legendH + 6;
    g.strokeStyle = '#374151'; g.lineWidth = 1; g.beginPath(); g.moveTo(bx, by); g.lineTo(bx + bar * sc, by); g.stroke();
    g.fillStyle = '#374151'; g.textAlign = 'center'; g.textBaseline = 'top'; g.fillText(`${bar} m`, bx + bar * sc / 2, by + 2);
    g.textAlign = 'right'; g.textBaseline = 'top'; g.fillText('北が上', w - 4, 2);
  }

  function line(g, tr, P) {
    g.beginPath();
    let on = false;
    for (let i = 0; i < tr.x.length; i++) {
      if (tr.x[i] === null) { on = false; continue; }
      const [px, py] = P(tr.x[i], tr.y[i]);
      if (on) g.lineTo(px, py); else { g.moveTo(px, py); on = true; }
    }
    g.stroke();
  }
  function nearestIdx(t, s) {
    let k = -1, d = Infinity;
    for (let i = 0; i < t.s.length; i++) if (t.s[i] !== null && t.x[i] !== null && Math.abs(t.s[i] - s) < d) { d = Math.abs(t.s[i] - s); k = i; }
    return k;
  }
  function niceStep(v) {
    const p = Math.pow(10, Math.floor(Math.log10(v))), m = v / p;
    return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
  }

  // グラフ：上の段は速さ（km/h）、下の段は基準の周とのタイムの差（秒、＋は遅れ）。横軸は基準の周の距離。区間の頭に縦の破線
  function drawChart(cv) {
    const { g, w, h } = canvasFor(cv);
    const o = state.overlay, ref = o.laps[o.ref];
    const laps = [ref, ...o.laps.filter((l, i) => i !== o.ref)].filter((l) => l && l.track).slice(0, CHART_LAPS);
    const len = o.ref_length_m || 1;
    const others = laps.slice(1).filter((l) => l.delta && l.delta.some(has));
    const L = 30, R = 6, T = 4, B = 14, gapH = 8;
    const dh = others.length ? Math.max(28, (h - T - B - gapH) * 0.34) : 0;
    const sh = h - T - B - (others.length ? dh + gapH : 0);
    const X = (s) => L + (s / len) * (w - L - R);
    g.font = '7px "Yu Gothic UI", "Segoe UI", sans-serif';
    // 速さの段
    let vmax = 0, vmin = Infinity;
    for (const l of laps) for (const v of l.track.sp || []) if (has(v)) { vmax = Math.max(vmax, v); vmin = Math.min(vmin, v); }
    if (!isFinite(vmin)) return;
    const vs = niceStep((vmax - vmin) / 4 || 10), v0 = Math.floor(vmin / vs) * vs, v1 = Math.ceil(vmax / vs) * vs;
    const Ys = (v) => T + sh - ((v - v0) / (v1 - v0 || 1)) * sh;
    axes(g, L, T, w - R, T + sh, v0, v1, vs, Ys, 'km/h');
    // 区間の頭
    const heads = typeof splitHeads === 'function' ? splitHeads() : [];
    g.setLineDash([2, 2]); g.strokeStyle = '#9ca3af'; g.lineWidth = 0.6;
    heads.forEach((s, i) => {
      if (!has(s)) return;
      g.beginPath(); g.moveTo(X(s), T); g.lineTo(X(s), h - B); g.stroke();
      g.fillStyle = '#6b7280'; g.textAlign = 'left'; g.textBaseline = 'top'; g.fillText(`区${i + 2}`, X(s) + 1.5, T);
    });
    g.setLineDash([]);
    for (const l of laps.slice().reverse()) {
      g.strokeStyle = color(l.key); g.lineWidth = l === ref ? 1.3 : 0.9;
      g.beginPath();
      let on = false;
      for (let i = 0; i < l.track.s.length; i++) {
        const s = l.track.s[i], v = (l.track.sp || [])[i];
        if (!has(s) || !has(v)) { on = false; continue; }
        if (on) g.lineTo(X(s), Ys(v)); else { g.moveTo(X(s), Ys(v)); on = true; }
      }
      g.stroke();
    }
    // タイムの差の段
    if (others.length) {
      const top = T + sh + gapH, bot = top + dh;
      let dhi = 0.1, dlo = -0.1;                              // 0 を挟んで、出た分だけの範囲にする（遅れだけなら上だけ広く）
      for (const l of others) for (const v of l.delta) if (has(v)) { dhi = Math.max(dhi, v); dlo = Math.min(dlo, v); }
      const ds = niceStep((dhi - dlo) / 3), d1 = Math.ceil(dhi / ds) * ds, d0 = Math.floor(dlo / ds) * ds;
      const Yd = (v) => bot - ((v - d0) / (d1 - d0)) * dh;
      axes(g, L, top, w - R, bot, d0, d1, ds, Yd, '秒');
      g.strokeStyle = '#6b7280'; g.lineWidth = 0.6; g.beginPath(); g.moveTo(L, Yd(0)); g.lineTo(w - R, Yd(0)); g.stroke();
      for (const l of others) {
        g.strokeStyle = color(l.key); g.lineWidth = 1;
        g.beginPath();
        let on = false;
        o.grid.forEach((s, i) => {
          const v = l.delta[i];
          if (!has(s) || !has(v)) { on = false; return; }
          if (on) g.lineTo(X(s), Yd(v)); else { g.moveTo(X(s), Yd(v)); on = true; }
        });
        g.stroke();
      }
      g.fillStyle = '#6b7280'; g.textAlign = 'right'; g.textBaseline = 'top';
      g.fillText('基準の周との差（＋は遅れ）', w - R, top + 1);
    }
    // 横軸（m）
    const xs = niceStep(len / 6);
    g.fillStyle = '#374151'; g.textAlign = 'center'; g.textBaseline = 'top';
    for (let s = 0; s <= len; s += xs) g.fillText(s >= 1000 ? `${(s / 1000).toFixed(s % 1000 ? 1 : 0)} km` : `${s} m`, X(s), h - B + 3);
    // 周の色の札
    g.textAlign = 'right'; g.textBaseline = 'top';
    let rx = w - R;
    for (const l of laps.slice().reverse()) {
      const text = name(l.key) + (l === ref ? '（基準）' : '');
      const tw = g.measureText(text).width;
      g.fillStyle = '#374151'; g.fillText(text, rx, T + 1);
      g.fillStyle = color(l.key); g.fillRect(rx - tw - 9, T + 3, 7, 3);
      rx -= tw + 16;
    }
  }

  function axes(g, x0, y0, x1, y1, v0, v1, step, Y, unit) {
    g.strokeStyle = '#e5e7eb'; g.lineWidth = 0.5; g.fillStyle = '#374151'; g.textAlign = 'right'; g.textBaseline = 'middle';
    for (let v = v0; v <= v1 + step * 1e-6; v += step) {
      const y = Y(v);
      g.beginPath(); g.moveTo(x0, y); g.lineTo(x1, y); g.stroke();
      g.fillText(Math.abs(v) < 1e-9 ? '0' : (step < 1 ? v.toFixed(step < 0.1 ? 2 : 1) : v.toFixed(0)), x0 - 3, y);
    }
    g.strokeStyle = '#9ca3af'; g.strokeRect(x0, y0, x1 - x0, y1 - y0);
    g.save(); g.translate(7, (y0 + y1) / 2); g.rotate(-Math.PI / 2); g.textAlign = 'center'; g.fillText(unit, 0, 0); g.restore();
  }

  return { open, close, why, isOpen: () => !!root, _pure: { speedColor, pct, niceStep, shortGap } };   // _pure は試験用
})();
if (typeof module !== 'undefined') module.exports = Report;
