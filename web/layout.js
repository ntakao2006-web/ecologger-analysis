// 枠の並び。枠はいつも敷き詰めたまま重ならない（隙間もできない）。
// - 並びは「左右に割る・上下に割る」を入れ子にした木で持つ：{d: 'row'|'col', k: [子…], s: [割合…]}、葉は枠の名前
// - 仕切りをドラッグすると両隣の大きさが変わる。細く寄せきるとその枠を畳み（細い帯が残る）、帯を押すと開く。
//   ダブルクリックで両隣を同じ大きさに（周の表の隣なら、表がちょうど収まる幅に）
// - 枠の見出しをドラッグして、ほかの枠の上下左右の端へ落とすとそこへ動く。真ん中へ落とすと入れ替わる。
//   画面の外周へ落とすと、その辺いっぱいの枠になる
// - 枠の ⤢（または見出しのダブルクリック）で、その枠だけを大きく出す。もう一度押すか Esc で戻る
// - 「配置」で決まった並べ方を選べる。手で変えた並びは次に開いたときも同じ（この画面の localStorage）
// - 動画の枠が横長なら、2本を左右に並べる（大きく出せるほうを選ぶ）
const Layout = (() => {
  const NAMES = ['laps', 'graph', 'video', 'map', 'g'];
  const TITLES = { laps: '周の表', graph: 'グラフ', video: '動画', map: 'コース図', g: 'G' };
  // 摩擦円の枠（g）は、コース図の枠を左右に割って右に置く（SP-10-14）
  const mapG = (m) => ({ d: 'row', k: ['map', 'g'], s: [m, 1 - m] });
  const right = (v, m) => ({ d: 'col', k: ['video', mapG(0.62)], s: [v, m] });
  const PRESETS = {
    std: { tree: { d: 'row', k: ['laps', 'graph', right(0.55, 0.45)], s: [0.27, 0.39, 0.34] }, closed: [] },
    video: { tree: { d: 'row', k: ['laps', 'graph', right(0.7, 0.3)], s: [0.27, 0.4, 0.6] }, closed: ['laps', 'g'] },
    graph: { tree: { d: 'row', k: ['laps', 'graph', right(0.5, 0.5)], s: [0.27, 0.74, 0.26] }, closed: ['laps', 'g'] },
    side: { tree: { d: 'row', k: ['laps', { d: 'col', k: ['graph', { d: 'row', k: ['video', 'map', 'g'], s: [0.5, 0.3, 0.2] }], s: [0.5, 0.5] }], s: [0.27, 0.73] }, closed: [] },
  };
  const GUT = 8, FOLDED = 30, FOLD_AT = 70, MIN_PANE = 150, EDGE = 22, KEY = 'layout2';
  const clone = (o) => JSON.parse(JSON.stringify(o));
  let st = { ...clone(PRESETS.std), preset: 'std' };
  let max = null;
  let L, sel, holder, panes = {};

  // ------------------------------------------------------------------ 木
  function leaves(n, out = []) { if (typeof n === 'string') out.push(n); else n.k.forEach((c) => leaves(c, out)); return out; }
  function valid(t) {
    try { const l = leaves(t); return l.length === NAMES.length && NAMES.every((n) => l.includes(n)) && check(t); } catch (e) { return false; }
  }
  function check(n) {
    if (typeof n === 'string') return true;
    return (n.d === 'row' || n.d === 'col') && Array.isArray(n.k) && Array.isArray(n.s) && n.k.length >= 2 && n.k.length === n.s.length
      && n.s.every((v) => typeof v === 'number' && v > 0) && n.k.every(check);
  }
  function norm(n) {
    if (typeof n === 'string') return n;
    // 子を整え、同じ向きの子は1段にまとめる
    let k = [], s = [];
    n.k.forEach((c, i) => {
      c = norm(c);
      if (typeof c !== 'string' && c.d === n.d) {
        const tot = c.s.reduce((a, b) => a + b, 0);
        c.k.forEach((cc, j) => { k.push(cc); s.push(n.s[i] * c.s[j] / tot); });
      } else { k.push(c); s.push(n.s[i]); }
    });
    if (k.length === 1) return k[0];
    const tot = s.reduce((a, b) => a + b, 0);
    return { d: n.d, k, s: s.map((v) => v / tot) };
  }
  function remove(n, name) {
    if (n === name) return null;
    if (typeof n === 'string') return n;
    const k = [], s = [];
    n.k.forEach((c, i) => { const r = remove(c, name); if (r !== null) { k.push(r); s.push(n.s[i]); } });
    if (!k.length) return null;
    return k.length === 1 ? k[0] : { d: n.d, k, s };
  }
  // target の side（left/right/top/bottom）へ name を置く
  function insert(n, target, name, side) {
    const axis = side === 'left' || side === 'right' ? 'row' : 'col', before = side === 'left' || side === 'top';
    if (n === target) return { d: axis, k: before ? [name, target] : [target, name], s: [0.5, 0.5] };
    if (typeof n === 'string') return n;
    const k = [], s = [];
    n.k.forEach((c, i) => {
      if (c === target && n.d === axis) {
        const half = n.s[i] / 2;
        if (before) { k.push(name, c); s.push(half, half); } else { k.push(c, name); s.push(half, half); }
      } else { k.push(insert(c, target, name, side)); s.push(n.s[i]); }
    });
    return { d: n.d, k, s };
  }
  function swap(n, a, b) {
    if (n === a) return b;
    if (n === b) return a;
    if (typeof n === 'string') return n;
    return { d: n.d, k: n.k.map((c) => swap(c, a, b)), s: n.s.slice() };
  }
  function move(name, target, side) {
    let t;
    if (side === 'center') t = swap(st.tree, name, target);
    else if (target === null) {             // 画面の外周
      const rest = remove(st.tree, name), axis = side === 'left' || side === 'right' ? 'row' : 'col', before = side === 'left' || side === 'top';
      t = { d: axis, k: before ? [name, rest] : [rest, name], s: before ? [0.3, 0.7] : [0.7, 0.3] };
    } else t = insert(remove(st.tree, name), target, name, side);
    st.tree = norm(t);
    st.closed = st.closed.filter((x) => x !== name);
    changed();
  }

  // ------------------------------------------------------------------ 描く
  function save() { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) { /* 覚えなくてよい */ } }
  function load() {
    try {
      const j = JSON.parse(localStorage.getItem(KEY) || 'null');
      // G の枠ができる前に覚えた並び：コース図の右に G を足す
      if (j && j.tree && !leaves(j.tree).includes('g') && leaves(j.tree).includes('map')) j.tree = insert(j.tree, 'map', 'g', 'right');
      if (j && valid(j.tree)) st = { tree: norm(j.tree), closed: (j.closed || []).filter((n) => NAMES.includes(n)), preset: j.preset || 'mine' };
    } catch (e) { /* 標準で開く */ }
  }
  const isClosed = (name) => st.closed.includes(name);
  // 枠の伸び方。割合（足して1）をそのまま flex-grow にすると、畳んだ枠の分だけ合計が1より小さくなり、
  // flexbox は余りを配りきらずに隙間を残す（周の表を畳むと右に空きができた）。比は同じまま大きな数にして、いつも敷き詰める
  const grow = (s) => `${s * 1000} 1 0px`;

  function build(n, dir) {
    if (typeof n === 'string') {
      const el = panes[n];
      const folded = isClosed(n) && !max;
      el.classList.toggle('folded', folded);
      el.classList.toggle('vert', folded && dir === 'row');
      return el;
    }
    const box = document.createElement('div');
    box.className = `split ${n.d}`;
    n.k.forEach((c, i) => {
      if (i) {
        const g = document.createElement('div');
        g.className = `gutter ${n.d === 'row' ? 'gv' : 'gh'}`;
        g.setAttribute('role', 'separator');
        g.setAttribute('aria-orientation', n.d === 'row' ? 'vertical' : 'horizontal');
        g.tabIndex = 0;
        g.title = 'ドラッグで大きさを変える（細く寄せきると畳む）。ダブルクリックで両隣を同じ大きさに';
        g._at = { node: n, i: i - 1 };
        box.append(g);
      }
      const el = build(c, n.d);
      el.style.flex = typeof c === 'string' && isClosed(c) && !max ? `0 0 ${FOLDED}px` : grow(n.s[i]);
      box.append(el);
    });
    return box;
  }

  function render() {
    for (const n of NAMES) panes[n].classList.remove('folded', 'vert');
    // 見えない枠も文書の中に置いたままにする（外すと動画が止まる）
    const shown = max ? [max] : NAMES;
    const root = max ? panes[max] : build(st.tree, null);
    root.style.flex = '1 1 0px';
    for (const n of NAMES) if (!shown.includes(n)) holder.append(panes[n]);
    L.replaceChildren(root, holder);
    for (const g of L.querySelectorAll('.gutter')) bindGutter(g);
    sel.value = st.preset in PRESETS ? st.preset : 'mine';
    sel.querySelector('option[value=mine]').hidden = sel.value !== 'mine';
    L.classList.toggle('maxed', !!max);
    for (const b of document.querySelectorAll('.pbtn[data-max]')) {
      const on = !!max && b.closest('.pane').dataset.pane === max;
      b.textContent = on ? '⤡' : '⤢';
      b.title = on ? '元の並びに戻す（Esc でも戻る）' : 'この枠を大きくする（もう一度押すか Esc で戻す）。見出しのダブルクリックでも同じ';
    }
  }
  function changed() { st.preset = 'mine'; render(); save(); }
  function usePreset(name) {
    const p = PRESETS[name];
    if (!p) return;
    st = { ...clone(p), preset: name };
    max = null;
    render(); save();
  }

  // ------------------------------------------------------------------ 仕切り
  function childEls(g) {
    const a = g.previousElementSibling, b = g.nextElementSibling;
    return [a, b];
  }
  function leafOf(el) { return el.classList.contains('pane') ? el.dataset.pane : null; }
  function live(el, name, s, row) {
    const folded = !!name && isClosed(name);
    if (name) { el.classList.toggle('folded', folded); el.classList.toggle('vert', folded && row); }
    el.style.flex = folded ? `0 0 ${FOLDED}px` : grow(s);
  }

  function bindGutter(g) {
    const { node, i } = g._at, row = node.d === 'row';
    g.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const [A, B] = childEls(g), ra = A.getBoundingClientRect(), rb = B.getBoundingClientRect();
      const start = row ? ra.left : ra.top, end = row ? rb.right : rb.bottom;
      const tot = node.s[i] + node.s[i + 1], la = leafOf(A), lb = leafOf(B);
      let moved = false;
      g.setPointerCapture(e.pointerId);
      g.classList.add('dragging');
      document.body.classList.add('resizing', row ? 'col' : 'row');
      const mv = (ev) => {
        const pos = row ? ev.clientX : ev.clientY;
        moved = true;
        const a = pos - start - GUT / 2, b = end - pos - GUT / 2;
        const foldA = la && a < FOLD_AT, foldB = lb && b < FOLD_AT && !foldA;
        st.closed = st.closed.filter((x) => x !== la && x !== lb);
        if (foldA) st.closed.push(la);
        if (foldB) st.closed.push(lb);
        if (!foldA && !foldB) {
          const aa = Math.max(a, MIN_PANE), bb = Math.max(b, MIN_PANE);
          node.s[i] = tot * aa / (aa + bb); node.s[i + 1] = tot - node.s[i];
        }
        // ドラッグの間は作り直さず（仕切りが消えてしまう）、両隣の大きさだけ変える
        live(A, la, node.s[i], row); live(B, lb, node.s[i + 1], row);
      };
      const up = () => {
        if (moved) changed();
        g.classList.remove('dragging');
        document.body.classList.remove('resizing', 'row', 'col');
        g.removeEventListener('pointermove', mv);
        g.removeEventListener('pointerup', up);
        g.removeEventListener('pointercancel', up);
      };
      g.addEventListener('pointermove', mv);
      g.addEventListener('pointerup', up);
      g.addEventListener('pointercancel', up);
    });
    g.addEventListener('dblclick', () => {
      const [A, B] = childEls(g), la = leafOf(A), lb = leafOf(B);
      st.closed = st.closed.filter((x) => x !== la && x !== lb);
      const tot = node.s[i] + node.s[i + 1];
      const fit = la === 'laps' || lb === 'laps' ? fitLaps() : null;
      if (fit) {
        // 周の表の幅を、表がちょうど収まる px にする（両隣の合計の中で）
        const span = (row ? A.getBoundingClientRect().width + B.getBoundingClientRect().width : A.getBoundingClientRect().height + B.getBoundingClientRect().height);
        const f = Math.min(Math.max(fit / span, 0.1), 0.9);
        if (la === 'laps') { node.s[i] = tot * f; node.s[i + 1] = tot - node.s[i]; } else { node.s[i + 1] = tot * f; node.s[i] = tot - node.s[i + 1]; }
      } else { node.s[i] = node.s[i + 1] = tot / 2; }
      changed();
    });
    g.addEventListener('keydown', (e) => {
      const sign = (row ? { ArrowLeft: -1, ArrowRight: 1 } : { ArrowUp: -1, ArrowDown: 1 })[e.key];
      if (!sign) return;
      e.preventDefault();
      const tot = node.s[i] + node.s[i + 1], d = tot * 0.04 * sign;
      node.s[i] = Math.min(Math.max(node.s[i] + d, tot * 0.1), tot * 0.9); node.s[i + 1] = tot - node.s[i];
      changed();
      L.querySelectorAll('.gutter').forEach((x) => { if (x._at.node === node && x._at.i === i) x.focus(); });
    });
  }

  // 周の表が横に切れずに収まる幅（表が無ければ null）
  function fitLaps() {
    const pane = panes.laps, tables = [...pane.querySelectorAll('.laptable')];
    if (!tables.length) return null;
    const cs = getComputedStyle(pane), scrollbar = pane.offsetWidth - pane.clientWidth;
    const natural = (t) => { const old = t.style.width; t.style.width = 'max-content'; const w = t.scrollWidth; t.style.width = old; return w; };
    return Math.max(...tables.map(natural)) + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + scrollbar + 2;
  }

  // ------------------------------------------------------------------ 枠を動かす
  let zone, ghost;
  function where(x, y, from) {
    const box = L.getBoundingClientRect();
    if (x - box.left < EDGE) return { target: null, side: 'left', rect: { left: box.left, top: box.top, width: box.width * 0.3, height: box.height } };
    if (box.right - x < EDGE) return { target: null, side: 'right', rect: { left: box.right - box.width * 0.3, top: box.top, width: box.width * 0.3, height: box.height } };
    if (y - box.top < EDGE) return { target: null, side: 'top', rect: { left: box.left, top: box.top, width: box.width, height: box.height * 0.3 } };
    if (box.bottom - y < EDGE) return { target: null, side: 'bottom', rect: { left: box.left, top: box.bottom - box.height * 0.3, width: box.width, height: box.height * 0.3 } };
    const el = document.elementFromPoint(x, y), pane = el && el.closest('.layout .pane');
    if (!pane || pane.dataset.pane === from) return null;
    const r = pane.getBoundingClientRect(), rx = (x - r.left) / r.width, ry = (y - r.top) / r.height;
    const d = { left: rx, right: 1 - rx, top: ry, bottom: 1 - ry };
    let side = Object.keys(d).reduce((a, b) => (d[a] <= d[b] ? a : b));
    if (d[side] > 0.3) side = 'center';
    const rect = { left: r.left, top: r.top, width: r.width, height: r.height };
    if (side === 'left') rect.width /= 2;
    if (side === 'right') { rect.left += r.width / 2; rect.width /= 2; }
    if (side === 'top') rect.height /= 2;
    if (side === 'bottom') { rect.top += r.height / 2; rect.height /= 2; }
    return { target: pane.dataset.pane, side, rect };
  }
  const SIDE_TEXT = { left: '左へ', right: '右へ', top: '上へ', bottom: '下へ', center: 'と入れ替え' };

  function bindHeader(h) {
    const name = h.closest('.pane').dataset.pane;
    h.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || max || e.target.closest('button, select, input, label, details, .seg, a')) return;
      const x0 = e.clientX, y0 = e.clientY;
      let on = false, hit = null;
      const mv = (ev) => {
        if (!on) {
          if (Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - y0) < 6) return;
          on = true;
          document.body.classList.add('moving');
          ghost.hidden = false;
        }
        hit = where(ev.clientX, ev.clientY, name);
        ghost.style.left = `${ev.clientX + 12}px`; ghost.style.top = `${ev.clientY + 12}px`;
        if (hit) {
          Object.assign(zone.style, { left: `${hit.rect.left}px`, top: `${hit.rect.top}px`, width: `${hit.rect.width}px`, height: `${hit.rect.height}px` });
          zone.hidden = false;
          ghost.textContent = hit.target === null ? `${TITLES[name]}を画面の${SIDE_TEXT[hit.side].replace('へ', '')}いっぱいに`
            : hit.side === 'center' ? `${TITLES[name]}を${TITLES[hit.target]}${SIDE_TEXT.center}` : `${TITLES[name]}を${TITLES[hit.target]}の${SIDE_TEXT[hit.side]}`;
        } else { zone.hidden = true; ghost.textContent = `${TITLES[name]}を動かす（ほかの枠の上下左右か真ん中へ）`; }
      };
      const up = () => {
        window.removeEventListener('pointermove', mv);
        window.removeEventListener('pointerup', up);
        window.removeEventListener('pointercancel', cancel);
        document.body.classList.remove('moving');
        zone.hidden = true; ghost.hidden = true;
        if (on && hit) move(name, hit.target, hit.side);
      };
      const cancel = () => { hit = null; up(); };
      // 見出しの外へ出てからの動きも拾うよう、窓全体で受ける
      window.addEventListener('pointermove', mv);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', cancel);
    });
    // 見出しの空いた所をダブルクリックしても大きくする（ボタン・入れ物の上では何もしない）
    h.addEventListener('dblclick', (e) => {
      if (e.target.closest('button, select, input, label, details, .seg')) return;
      toggleMax(name);
    });
  }

  function setMax(name) { max = name; render(); }
  function toggleMax(name) { setMax(max === name ? null : name); }

  // 動画2本：上下と左右のうち、1本を大きく出せるほうへ並べる
  function arrangeVideo(box) {
    const w = box.clientWidth, h = box.clientHeight, head = 24, gap = 6, ar = 16 / 9;
    if (!w || !h) return;
    const stacked = Math.min(w, ((h - gap) / 2 - head) * ar);
    const side = Math.min((w - gap) / 2, (h - head) * ar);
    box.classList.toggle('side', side > stacked * 1.05);
  }

  function init() {
    L = document.getElementById('layout');
    sel = document.getElementById('layoutsel');
    for (const n of NAMES) {
      const p = L.querySelector(`.pane[data-pane="${n}"]`);
      panes[n] = p;
      const tab = document.createElement('button');
      tab.type = 'button'; tab.className = 'foldtab'; tab.textContent = `${TITLES[n]} ▸`; tab.title = `${TITLES[n]}を開く`;
      tab.onclick = () => { st.closed = st.closed.filter((x) => x !== n); changed(); };
      p.prepend(tab);
      const h = p.querySelector('.phead');
      h.querySelector('h2').title = '見出しをドラッグして、ほかの枠の上下左右（真ん中なら入れ替え）へ動かせます。ダブルクリックで大きく';
      bindHeader(h);
    }
    holder = document.createElement('div');
    holder.className = 'holder';
    zone = Object.assign(document.createElement('div'), { className: 'dropzone', hidden: true });
    ghost = Object.assign(document.createElement('div'), { className: 'dragghost', hidden: true });
    document.body.append(zone, ghost);
    load();
    render();
    sel.onchange = () => { if (sel.value in PRESETS) usePreset(sel.value); };
    document.getElementById('lapsfold').onclick = () => { if (!isClosed('laps')) st.closed.push('laps'); max = null; changed(); };
    for (const b of document.querySelectorAll('.pbtn[data-max]')) b.onclick = () => toggleMax(b.closest('.pane').dataset.pane);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && max && !document.querySelector('dialog[open]')) setMax(null);
    });
    const vbox = document.getElementById('vbox');
    new ResizeObserver(() => arrangeVideo(vbox)).observe(vbox);
  }

  return { init, setMax, usePreset, _tree: () => clone(st.tree) };
})();
Layout.init();
