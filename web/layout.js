// 枠の並び：枠どうしは重ならないまま、間の仕切りをドラッグして大きさを変える。
// - 仕切り：周の表｜グラフ（幅 px）、グラフ｜右の列（幅 割合）、動画／コース図（高さ 割合）。ダブルクリックでその仕切りだけ標準へ
// - 周の表は畳める（左へ寄せきる・◂ を押す）。畳むと左に細い帯が残り、押すと開く
// - 枠の ⤢（または見出しのダブルクリック）で、その枠だけを大きく出す。もう一度押すか Esc で戻す
// - 「配置」で決まった並べ方を選べる。手で変えた並びは次に開いたときも同じ（この画面の localStorage）
// - 動画の枠が横長なら、2本を左右に並べる（大きく出せるほうを選ぶ）
const Layout = (() => {
  const PRESETS = {
    std: { wl: 380, wr: 0.34, hv: 0.55, closed: false },
    video: { wl: 380, wr: 0.6, hv: 0.7, closed: true },
    graph: { wl: 380, wr: 0.26, hv: 0.5, closed: true },
  };
  const MIN_LAPS = 220, FOLD_AT = 120, MIN_GRAPH = 260, MIN_RIGHT = 240, GUT = 8, PAD = 8;
  const KEY = 'layout';
  let st = { ...PRESETS.std, preset: 'std' };
  let max = null;
  let L, R, sel;

  function save() { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) { /* 覚えなくてよい */ } }
  function load() {
    try {
      const j = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (j && typeof j === 'object') st = { ...st, ...j };
    } catch (e) { /* 標準で開く */ }
  }

  function apply() {
    L.style.setProperty('--wl', st.closed ? '30px' : `${Math.round(st.wl)}px`);
    L.style.setProperty('--wr', String(st.wr));
    R.style.setProperty('--hv', String(st.hv));
    L.classList.toggle('laps-closed', !!st.closed);
    sel.value = st.preset in PRESETS ? st.preset : 'mine';
    sel.querySelector('option[value=mine]').hidden = sel.value !== 'mine';
  }
  function changed() { st.preset = 'mine'; apply(); save(); }
  function usePreset(name) {
    const p = PRESETS[name];
    if (!p) return;
    st = { ...p, preset: name };
    setMax(null);
    apply(); save();
  }

  // 周の表の幅の上限：グラフと右の列が狭くなりすぎない所まで
  function inner() { return L.clientWidth - PAD * 2; }
  function maxLaps() { return Math.max(MIN_LAPS, inner() - GUT * 2 - MIN_GRAPH - inner() * st.wr); }
  // 周の表が横に切れずに収まる幅（表が無ければ null）
  function fitLaps() {
    const tables = [...L.querySelectorAll('.laps .laptable')];
    if (!tables.length) return null;
    const pane = L.querySelector('.laps'), cs = getComputedStyle(pane);
    const scrollbar = pane.offsetWidth - pane.clientWidth;
    const natural = (t) => { const old = t.style.width; t.style.width = 'max-content'; const w = t.scrollWidth; t.style.width = old; return w; };
    const w = Math.max(...tables.map(natural)) + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + scrollbar + 2;
    return Math.min(Math.max(w, MIN_LAPS), maxLaps());
  }
  function lapsWidth() { return st.closed ? 30 : st.wl; }

  function drag(gut, e) {
    if (e.button !== 0 || max) return;
    e.preventDefault();
    const kind = gut.dataset.gut, box = L.getBoundingClientRect(), rbox = R.getBoundingClientRect();
    gut.setPointerCapture(e.pointerId);
    gut.classList.add('dragging');
    document.body.classList.add('resizing', kind === 'v' ? 'row' : 'col');
    const move = (ev) => {
      if (kind === 'l') {
        const w = ev.clientX - box.left - PAD - GUT / 2;
        if (w < FOLD_AT) st.closed = true;
        else { st.closed = false; st.wl = Math.min(Math.max(w, MIN_LAPS), maxLaps()); }
      } else if (kind === 'r') {
        const w = box.right - PAD - ev.clientX - GUT / 2;
        const most = inner() - lapsWidth() - GUT * 2 - MIN_GRAPH;
        st.wr = Math.min(Math.max(w, MIN_RIGHT), Math.max(MIN_RIGHT, most)) / inner();
      } else {
        st.hv = Math.min(Math.max((ev.clientY - rbox.top) / rbox.height, 0.15), 0.85);
      }
      changed();
    };
    const up = () => {
      gut.classList.remove('dragging');
      document.body.classList.remove('resizing', 'row', 'col');
      gut.removeEventListener('pointermove', move);
      gut.removeEventListener('pointerup', up);
      gut.removeEventListener('pointercancel', up);
    };
    gut.addEventListener('pointermove', move);
    gut.addEventListener('pointerup', up);
    gut.addEventListener('pointercancel', up);
  }

  function resetGutter(kind) {
    const p = PRESETS.std;
    if (kind === 'l') { st.wl = fitLaps() || p.wl; st.closed = false; } else if (kind === 'r') st.wr = p.wr; else st.hv = p.hv;
    changed();
  }

  function nudge(kind, sign) {
    if (kind === 'l') { st.closed = false; st.wl = Math.min(Math.max(st.wl + sign * 20, MIN_LAPS), maxLaps()); }
    else if (kind === 'r') st.wr = Math.min(Math.max(st.wr - sign * 0.02, MIN_RIGHT / inner()), 0.75);
    else st.hv = Math.min(Math.max(st.hv + sign * 0.03, 0.15), 0.85);
    changed();
  }

  // 1つの枠を大きく出す（name＝null で戻す）
  function setMax(name) {
    max = name;
    if (name === 'laps' && st.closed) { st.closed = false; apply(); save(); }
    for (const el of document.querySelectorAll('.maxpath')) el.classList.remove('maxpath');
    if (!name) { delete L.dataset.max; delete R.dataset.max; }
    else {
      const pane = L.querySelector(`.pane[data-pane="${name}"]`);
      pane.classList.add('maxpath');
      L.dataset.max = name;
      if (pane.parentElement === R) { R.classList.add('maxpath'); R.dataset.max = name; } else delete R.dataset.max;
    }
    for (const b of document.querySelectorAll('.pbtn[data-max]')) {
      const on = !!name && b.closest('.pane').dataset.pane === name;
      b.textContent = on ? '⤡' : '⤢';
      b.title = on ? '元の並びに戻す（Esc でも戻る）' : 'この枠を大きくする（もう一度押すか Esc で戻す）。見出しのダブルクリックでも同じ';
    }
  }
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
    R = document.getElementById('rcol');
    sel = document.getElementById('layoutsel');
    load();
    apply();
    sel.onchange = () => { if (sel.value in PRESETS) usePreset(sel.value); };
    for (const g of L.querySelectorAll('.gutter')) {
      const kind = g.dataset.gut;
      g.addEventListener('pointerdown', (e) => drag(g, e));
      g.addEventListener('dblclick', () => resetGutter(kind));
      g.addEventListener('keydown', (e) => {
        const k = e.key;
        const sign = (kind === 'v' ? { ArrowUp: -1, ArrowDown: 1 } : { ArrowLeft: -1, ArrowRight: 1 })[k];
        if (sign) { e.preventDefault(); nudge(kind, sign); }
      });
    }
    document.getElementById('lapsfold').onclick = () => { st.closed = true; changed(); };
    document.getElementById('lapsopen').onclick = () => { st.closed = false; changed(); };
    for (const b of document.querySelectorAll('.pbtn[data-max]')) b.onclick = () => toggleMax(b.closest('.pane').dataset.pane);
    // 見出しの空いた所をダブルクリックしても大きくする（ボタン・入れ物の上では何もしない）
    for (const h of document.querySelectorAll('.phead')) {
      h.addEventListener('dblclick', (e) => {
        if (e.target.closest('button, select, input, label, details, .seg')) return;
        toggleMax(h.closest('.pane').dataset.pane);
      });
    }
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && max && !document.querySelector('dialog[open]')) setMax(null);
    });
    const vbox = document.getElementById('vbox');
    new ResizeObserver(() => arrangeVideo(vbox)).observe(vbox);
  }

  return { init, setMax, usePreset };
})();
Layout.init();
