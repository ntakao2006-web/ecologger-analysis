// 画面の入口。裏（Python）とは /api/ のHTTPだけでやりとりする（pywebview の独自の呼び出しは使わない）
'use strict';

const state = { sessions: [], mode: 'app', picked: new Set(), xaxis: 'dist', x: 'dist', channels: null, ref: '', colors: new Map(), req: 0,
                overlay: null, refSession: null, draft: null, formulas: [], help: null, editing: null, rev: null, notice: 0 };
const MAX_PICK = 8;            // 線の色の数（見分けのつく色は8つまで）
const DEFAULT_CHANNELS = ['Speed', 'Throttle', 'RPM', 'LongAccel', 'LatAccel'];
try { const c = JSON.parse(localStorage.getItem('channels') || 'null'); if (Array.isArray(c)) state.channels = c; } catch (e) { /* 覚えていなくてよい */ }
// コース図の塗り（SP-10-12）。ranges は塗り方・チャンネルごとに手で決めた段の範囲 [低, 高]（null は自動）
state.paint = { mode: 'lap', ch: 'Speed', dotted: false, acc: false, marks: false, markCh: 'Speed', ranges: {} };
try { const p = JSON.parse(localStorage.getItem('mappaint') || 'null'); if (p && typeof p === 'object') Object.assign(state.paint, p); } catch (e) { /* 覚えていなくてよい */ }
const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const opt = { headers: { 'X-EcoAnalysis': '1' } };
  if (body !== undefined) {
    opt.method = 'POST';
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const r = await fetch('/api/' + path, opt);
  return r.json();
}

function status(text, kind) {
  const e = $('status');
  e.textContent = text;
  e.className = 'status' + (kind ? ' ' + kind : '');
}

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e[k] = v;
    else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  for (const k of kids) if (k !== null && k !== undefined) e.append(k);
  return e;
}

function message(text, problems) {
  const box = $('message');
  box.textContent = '';
  box.hidden = !text;
  if (!text) return;
  box.append(el('p', {}, text));
  if (problems && problems.length) box.append(el('ul', {}, ...problems.map((x) => el('li', {}, x))));
  box.append(el('button', { type: 'button', onclick: () => message(null) }, '閉じる'));
}

// ---------------------------------------------------------------- 書き方
function lapTime(s) {
  if (s === null || s === undefined) return '—';
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return m ? m + ':' + r.toFixed(3).padStart(6, '0') : r.toFixed(3);
}
function gap(s) {
  if (s === null || s === undefined || !isFinite(s)) return '';
  if (Math.abs(s) < 0.0005) return '0.000';
  return (s > 0 ? '+' : '−') + Math.abs(s).toFixed(3);
}
function jstDate(iso) {
  const d = new Date(new Date(iso).getTime() + 9 * 3600e3);
  return d.toISOString().slice(0, 16).replace('T', ' ');
}

// ---------------------------------------------------------------- 周
function lapsOf(s) {
  return state.mode === 'app' && s.laps.app ? s.laps.app : s.laps.logger;
}

// コースごとのベスト（周とセクター）。同じコースの走行どうしで比べる。測位の抜けた周は除く
function bests() {
  const out = {};
  for (const s of state.sessions) {
    const key = s.course || s.id;
    const b = out[key] || (out[key] = { lap: Infinity, sectors: [] });
    for (const x of lapsOf(s)) {
      if (x.gap) continue;
      b.lap = Math.min(b.lap, x.time_s);
      (x.sectors || []).forEach((v, i) => { if (v !== null) b.sectors[i] = Math.min(b.sectors[i] ?? Infinity, v); });
    }
  }
  return out;
}

function render() {
  const box = $('sessions');
  box.textContent = '';
  if (!state.sessions.length) {
    box.append(el('p', { class: 'empty' }, '「走行を開く…」で、走行のフォルダー（名前が .ecorun で終わるもの）を選びます。別の日の走行も続けて開けます。'));
  } else {
    const best = bests();
    for (const s of state.sessions) box.append(sessionBlock(s, best[s.course || s.id]));
  }
  renderPicked();
}

function sessionBlock(s, best) {
  const laps = lapsOf(s);
  const usingApp = state.mode === 'app' && !!s.laps.app;
  const compareLogger = usingApp && !s.line_name;     // 線の版を使っているときは、ロガーと線が違うので比べない
  const g = s.gnss;
  const block = el('div', { class: 'session' },
    el('div', { class: 'shead' },
      el('div', { class: 'stitle', title: s.path }, s.title),
      el('button', { type: 'button', class: 'x', title: 'この走行を閉じる', 'aria-label': 'この走行を閉じる', onclick: () => closeSession(s.id) }, '×')),
    el('div', { class: 'sinfo' },
      `${jstDate(s.t0_utc)}　周 ${laps.length}　測位 ${g ? `${g.points.toLocaleString()}点（${g.rate_hz ?? '?'}Hz・${g.source === 'sd' ? 'SD' : 'ロガーの記録'}）` : 'なし'}　読み込み ${s.load_s}秒`));
  if (state.mode === 'app' && !s.laps.app) block.append(el('p', { class: 'note' }, `コースの線で切れないので、ロガーの数えを出しています（${s.laps.app_reason}）`));
  if (!laps.length) { block.append(el('p', { class: 'empty' }, '周がありません')); return block; }

  const nsec = Math.max(0, ...laps.map((x) => (x.sectors || []).length));
  const head = el('tr', {}, el('th', {}, ''), el('th', {}, '周'), el('th', {}, 'タイム'), el('th', { title: 'ベスト（同じコースの開いている走行の中で、測位の抜けた周を除く）との差' }, '差'));
  for (let i = 0; i < nsec; i++) head.append(el('th', {}, 'S' + (i + 1)));
  head.append(el('th', {}, ''));
  const body = el('tbody');
  for (const x of laps) {
    const key = `${s.id}:${x.lap}`;
    const cb = el('input', { type: 'checkbox', 'aria-label': `${x.lap}周目を選ぶ` });
    cb.checked = state.picked.has(key);
    cb.onchange = () => {
      if (cb.checked && state.picked.size >= MAX_PICK) { cb.checked = false; status(`重ねられるのは${MAX_PICK}周までです`, 'ng'); return; }
      if (cb.checked) state.picked.add(key); else { state.picked.delete(key); state.colors.delete(key); }
      renderPicked();
    };
    const isBest = best && !x.gap && Math.abs(x.time_s - best.lap) < 0.0005;
    const row = el('tr', { class: isBest ? 'best' : '' },
      el('td', {}, cb),
      el('td', { class: 'num' }, String(x.lap)),
      el('td', { class: 'num time' }, lapTime(x.time_s)),
      el('td', { class: 'num diff' }, best ? gap(x.time_s - best.lap) : ''));
    for (let i = 0; i < nsec; i++) {
      const v = (x.sectors || [])[i];
      const has = v !== null && v !== undefined;
      const sb = best && has && !x.gap && Math.abs(v - (best.sectors[i] ?? Infinity)) < 0.0005;
      row.append(el('td', { class: 'num sec' + (sb ? ' sbest' : '') }, has ? v.toFixed(3) : '—'));
    }
    const marks = [];
    if (x.gap) marks.push(el('span', { class: 'mark warn', title: '周の中に1秒より長い測位の抜けがある。ベストからは外す' }, '測位の抜け'));
    if (compareLogger && !x.logger) marks.push(el('span', { class: 'mark', title: 'ロガーはこの周を数えていない' }, 'ロガーに無い'));
    if (compareLogger && x.logger && Math.abs(x.logger.diff_ms) > 5) {
      marks.push(el('span', { class: 'mark warn', title: `ロガーのタイム ${lapTime(x.logger.time_s)}` }, `ロガーと ${x.logger.diff_ms > 0 ? '+' : ''}${x.logger.diff_ms}ms`));
    }
    row.append(el('td', { class: 'marks' }, ...marks));
    body.append(row);
  }
  block.append(el('div', { class: 'tablewrap' }, el('table', { class: 'laptable' }, el('thead', {}, head), body)));
  if (usingApp && s.line_name) block.append(el('p', { class: 'note' }, `線の版「${s.line_name}」で切っています（ロガーとは線が違うので、ロガーのタイムとは比べません）`));
  if (compareLogger) {
    const matched = new Set(laps.filter((x) => x.logger).map((x) => x.logger.lap));
    const only = s.laps.logger.filter((x) => !matched.has(x.lap)).map((x) => x.lap);
    if (only.length) block.append(el('p', { class: 'note' }, `ロガーの数えにだけある周：${only.join('、')}（コースの線では切れなかった）`));
  }
  if (s.notes.length) block.append(el('details', { class: 'notes' }, el('summary', {}, '覚え書き'), ...s.notes.map((n) => el('p', {}, n))));
  return block;
}

// ---------------------------------------------------------------- 重ね合わせ
function colorFor(key) {
  if (!state.colors.has(key)) {
    const used = new Set(state.colors.values());
    let slot = 1;
    while (used.has(slot) && slot < MAX_PICK) slot++;
    state.colors.set(key, slot);
  }
  return getComputedStyle(document.body).getPropertyValue('--s' + state.colors.get(key)).trim();
}

// 選んだ周（選んだ順）。いまの切り方に無い周は外す
function pickedLaps() {
  const out = [];
  for (const key of state.picked) {
    const [id, n] = key.split(':');
    const s = state.sessions.find((x) => x.id === id);
    const lap = s && lapsOf(s).find((x) => String(x.lap) === n);
    if (lap) out.push({ key, id, lap: lap.lap, s, time_s: lap.time_s });
  }
  return out;
}

function channelMeta(picks) {
  const seen = new Map();
  for (const p of picks) for (const c of p.s.channels) if (!seen.has(c.name)) seen.set(c.name, c);
  for (const f of state.formulas) if (!seen.has(f.name)) seen.set(f.name, { name: f.name, label: f.name, unit: f.unit, kind: 'number', calc: true, expr: f.expr });
  return seen;
}

function renderChannelList(meta) {
  const box = $('chanlist');
  box.textContent = '';
  if (state.channels === null) state.channels = DEFAULT_CHANNELS.filter((n) => meta.has(n)).slice(0, 3);
  const item = (name, c) => {
    const cb = el('input', { type: 'checkbox' });
    cb.checked = state.channels.includes(name);
    cb.onchange = () => {
      if (cb.checked && state.channels.length >= 12) { cb.checked = false; status('チャンネルは12までです', 'ng'); return; }
      setChannels(cb.checked ? [...state.channels, name] : state.channels.filter((n) => n !== name));
      renderPicked();
    };
    return el('label', {}, cb, ` ${c.label || name}${c.unit ? `（${c.unit}）` : ''}`);
  };
  for (const [name, c] of meta) if (!c.calc) box.append(item(name, c));
  box.append(el('div', { class: 'calchead' }, '計算チャンネル'));
  for (const [name, c] of meta) {
    if (!c.calc) continue;
    box.append(el('div', { class: 'calcrow', title: c.expr }, item(name, c),
      el('button', { type: 'button', class: 'link', onclick: () => openCalc(name) }, '直す')));
  }
  box.append(el('button', { type: 'button', class: 'link', onclick: () => openCalc(null) }, '＋ 計算チャンネルを作る'));
}

function setChannels(list) {
  state.channels = list;
  try { localStorage.setItem('channels', JSON.stringify(state.channels)); } catch (e) { /* 覚えなくてよい */ }
}

// ---------------------------------------------------------------- コース図の塗り
function savePaint() { try { localStorage.setItem('mappaint', JSON.stringify(state.paint)); } catch (e) { /* 覚えなくてよい */ } }
function rangeKey() { const p = state.paint; return p.mode === 'value' ? `value:${p.ch}` : p.mode; }
function numberChannels(meta) { return [...meta.values()].filter((c) => c.kind === 'number'); }

function renderPaintControls(meta) {
  const p = state.paint, nums = numberChannels(meta);
  const fill = (sel, value) => {
    sel.textContent = '';
    for (const c of nums) sel.append(el('option', { value: c.name }, `${c.label || c.name}${c.unit ? `（${c.unit}）` : ''}`));
    if (!nums.some((c) => c.name === value) && nums.length) value = nums.some((c) => c.name === 'Speed') ? 'Speed' : nums[0].name;
    sel.value = value;
    return value;
  };
  if (meta.size) { p.ch = fill($('paintch'), p.ch); p.markCh = fill($('markch'), p.markCh); }
  $('paintsel').value = p.mode;
  $('paintch').hidden = p.mode !== 'value';
  $('paintrange').hidden = p.mode === 'lap';
  $('markch').hidden = !p.marks;
  $('decdot').checked = p.dotted; $('accring').checked = p.acc; $('markson').checked = p.marks;
  const r = p.ranges[rangeKey()] || [null, null];
  $('paintlo').value = r[0] === null ? '' : r[0];
  $('painthi').value = r[1] === null ? '' : r[1];
}

// 自動の段の範囲を、入れ欄の見本に出す
function showAutoRange() {
  const r = CourseMap.paintRange(), d = state.paint.mode === 'value' ? 1 : 3;
  $('paintlo').placeholder = r ? `自動 ${r[0].toFixed(d)}` : '自動';
  $('painthi').placeholder = r ? `自動 ${r[1].toFixed(d)}` : '自動';
}

function paintForMap() {
  const p = state.paint, meta = state.meta || new Map(), c = meta.get(p.ch), mc = meta.get(p.markCh);
  const r = p.ranges[rangeKey()] || [null, null];
  return { mode: p.mode, ch: p.ch, chLabel: c ? c.label || c.name : p.ch, unit: c ? c.unit : '', lo: r[0], hi: r[1],
           dotted: p.dotted, acc: p.acc, marks: p.marks, markLabel: mc ? mc.label || mc.name : p.markCh, markUnit: mc ? mc.unit : '' };
}

// 塗りを変えたとき：裏に頼み直しが要る（塗る値・印のチャンネルが変わった）なら頼み直す。要らなければ描き直すだけ
function paintChanged() {
  savePaint();
  if (state.meta) renderPaintControls(state.meta);
  const j = state.overlay, want = state.paint.mode === 'value' ? state.paint.ch : null;
  if (j && (want && j.paint !== want || j.mark !== state.paint.markCh)) { renderPicked(); return; }
  CourseMap.redraw(); showAutoRange();
}

function renderRefList(picks) {
  const sel = $('refsel');
  sel.textContent = '';
  sel.append(el('option', { value: '' }, '一番速い周'));
  for (const p of picks) sel.append(el('option', { value: p.key }, `${p.s.title} ${p.lap}周（${lapTime(p.time_s)}）`));
  if (!picks.some((p) => p.key === state.ref)) state.ref = '';
  sel.value = state.ref;
}

function renderLegend(data) {
  const box = $('legend');
  box.textContent = '';
  for (const lap of data.laps) {
    box.append(el('span', { class: 'lg' + (lap.ref ? ' ref' : '') }, el('i', { style: `background:${colorFor(lap.key)}` }),
      `${lap.label}　${lapTime(lap.time_s)}${lap.ref ? '（基準）' : ''}${!lap.ref && data.distance && !lap.delta ? '（測位が足りず距離で合わせられない）' : ''}`));
  }
  if (!data.distance) box.append(el('span', { class: 'note' }, '基準の周の測位が足りないので、横軸は時間だけです'));
  const why = new Map();
  for (const lap of data.laps) for (const [name, text] of Object.entries(lap.calc_errors || {})) if (!why.has(name)) why.set(name, text);
  for (const [name, text] of why) box.append(el('span', { class: 'note warnnote' }, `計算チャンネル「${name}」を出せない周があります（${text}）`));
}

async function renderPicked() {
  const picks = pickedLaps();
  const empty = !picks.length;
  $('graphEmpty').hidden = !empty;
  $('mapEmpty').hidden = !empty;
  if (empty) {
    Chart.clear(); CourseMap.clear(); VideoPane.clear(); state.overlay = null; state.refSession = null; showVideo(0);
    $('legend').textContent = ''; renderRefList([]); $('chanlist').textContent = ''; renderLineControls(); return;
  }
  const meta = channelMeta(picks);
  state.meta = meta;
  renderChannelList(meta);
  renderRefList(picks);
  renderPaintControls(meta);
  const ref = state.ref ? picks.findIndex((p) => p.key === state.ref) : null;
  const req = ++state.req;
  let j;
  try {
    j = await api('overlay', { laps: picks.map((p) => ({ id: p.id, lap: p.lap })), mode: state.mode,
                               channels: state.channels.filter((n) => meta.has(n)), ref: ref === null || ref < 0 ? null : ref,
                               paint: state.paint.mode === 'value' ? state.paint.ch : null, mark: state.paint.markCh });
  } catch (e) { status('裏の処理とつながっていません', 'ng'); return; }
  if (req !== state.req) return;                  // 新しい頼みが後から出ている
  if (!j.ok) { status(j.error, 'ng'); return; }
  const x = j.distance ? state.xaxis : 'time';
  renderLegend(j);
  state.overlay = j; state.x = x;
  const refKey = j.laps[j.ref] && j.laps[j.ref].key;
  state.refSession = state.sessions.find((s) => refKey && refKey.startsWith(s.id + ':')) || null;
  Chart.show($('plots'), j, { x, channels: state.channels.filter((n) => meta.has(n)).map((n) => meta.get(n)), color: colorFor,
                              onCursor: (v, kind) => { CourseMap.setCursor(v); VideoPane.cursor(v, kind); } }, true);
  CourseMap.show(j, true);
  CourseMap.setCursor(Chart.cursor());
  showAutoRange();
  showVideo(VideoPane.show(j));
  VideoPane.cursor(Chart.cursor(), 'pin');
  renderLineControls();
}

function showVideo(n) {
  $('vbox').hidden = !n;
  $('vplay').disabled = !n;
  $('videoEmpty').hidden = !!n;
  $('videoEmpty').textContent = state.picked.size && !n
    ? '選んだ周に動画がありません（動画が入っているのは、書き出しのときに動画を添えた走行だけです）'
    : '選んだ周に動画があれば、ここに2周を並べます（枠が横長なら左右、縦長なら上下）。グラフやコース図のカーソルに合わせて動きます。';
}

// ---------------------------------------------------------------- 計算チャンネル
let checkTimer = 0, checkReq = 0;

async function loadFormulas() {
  try {
    const j = await api('formulas');
    if (j.ok) { state.formulas = j.formulas; state.help = j.help; }
  } catch (e) { /* 無くても動く */ }
}

function insertAtCursor(text) {
  const ta = $('calcexpr');
  const a = ta.selectionStart ?? ta.value.length, b = ta.selectionEnd ?? a;
  ta.value = ta.value.slice(0, a) + text + ta.value.slice(b);
  ta.focus();
  ta.selectionStart = ta.selectionEnd = a + text.length;
  scheduleCheck();
}

// 式に書くときの名前（記号や空白がある・関数と同じ名前なら [名前]）
function refName(n) {
  const fn = state.help && state.help.funcs.some((f) => f.name === n.toLowerCase());
  return /^[^\W\d][\w.]*$/u.test(n) && !fn ? n : `[${n}]`;
}

function renderCalcHelp() {
  const box = $('calchelp');
  box.textContent = '';
  const names = new Set();
  for (const s of state.sessions) for (const c of s.channels) names.add(c.name);
  for (const f of state.formulas) if (f.name !== state.editing) names.add(f.name);
  const chips = (list) => el('div', { class: 'chips' }, ...list);
  box.append(el('div', { class: 'helpsec' }, 'チャンネル'),
    chips([...names].map((n) => el('button', { type: 'button', class: 'chip', onclick: () => insertAtCursor(refName(n)) }, n))));
  if (state.help) {
    box.append(el('div', { class: 'helpsec' }, '関数'),
      chips(state.help.funcs.map((f) => el('button', { type: 'button', class: 'chip', title: f.text, onclick: () => insertAtCursor(f.name + '(') }, f.name))),
      el('div', { class: 'helpsec' }, '定数'),
      chips(state.help.consts.map((c) => el('button', { type: 'button', class: 'chip', title: c.text, onclick: () => insertAtCursor(c.name) }, c.name))));
  }
  box.append(el('p', { class: 'note' }, '四則 + - * / %（余り） ^（べき）、比較 < <= > >= == !=（成り立てば1）、and or not。記号や空白のある名前は [名前]。integ・deriv・smooth・time は周ごとに計算し、integ は周の頭で0から始まる。関数の上にマウスを置くと説明が出る'));
}

function openCalc(name) {
  const f = state.formulas.find((x) => x.name === name);
  state.editing = f ? f.name : null;
  $('calctitle').textContent = f ? `計算チャンネル「${f.name}」を直す` : '計算チャンネルを作る';
  $('calcname').value = f ? f.name : '';
  $('calcunit').value = f ? f.unit : '';
  $('calcexpr').value = f ? f.expr : '';
  $('calcdel').hidden = !f;
  $('calcmsg').textContent = ''; $('calcmsg').className = 'calcmsg';
  renderCalcHelp();
  $('calcdlg').showModal();
  (f ? $('calcexpr') : $('calcname')).focus();
  if (f) scheduleCheck();
}

function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(checkCalc, 250);
}

function fmtNum(v) { return v === null ? '—' : Math.abs(v) >= 1000 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3); }

async function checkCalc() {
  const expr = $('calcexpr').value, msg = $('calcmsg');
  if (!expr.trim()) { msg.textContent = ''; msg.className = 'calcmsg'; return; }
  const req = ++checkReq;
  let j;
  try { j = await api('formulas/check', { expr, name: state.editing }); } catch (e) { return; }
  if (req !== checkReq) return;
  if (!j.ok) {
    const has = j.pos !== null && j.pos !== undefined;
    msg.textContent = (has ? `${j.pos + 1}文字目：` : '') + j.error;
    msg.className = 'calcmsg ng';
    return;
  }
  const p = j.preview;
  msg.className = 'calcmsg ok';
  msg.textContent = p
    ? `読めました。試しに「${p.title}」の全体で計算：${fmtNum(p.min)} 〜 ${fmtNum(p.max)}（値のある所 ${Math.round(p.valid * 100)}%）`
    : '読めました（走行を開くと、試しに計算した値の幅を出します）';
}

async function saveCalc() {
  const name = $('calcname').value.trim(), old = state.editing;
  if (!name) { $('calcmsg').textContent = '名前を入れてください'; $('calcmsg').className = 'calcmsg ng'; return; }
  let j;
  try { j = await api('formulas/save', { name, unit: $('calcunit').value, expr: $('calcexpr').value, old }); }
  catch (e) { status('裏の処理とつながっていません', 'ng'); return; }
  if (!j.ok) {
    const has = j.pos !== null && j.pos !== undefined;
    $('calcmsg').textContent = (has ? `${j.pos + 1}文字目：` : '') + j.error; $('calcmsg').className = 'calcmsg ng'; return;
  }
  state.formulas = j.formulas;
  let list = (state.channels || []).map((n) => (n === old ? name : n));
  if (!list.includes(name) && list.length < 12) list = [...list, name];     // 作った計算チャンネルはすぐ出す
  setChannels(list);
  $('calcdlg').close();
  status(`計算チャンネル「${name}」を残しました`, 'ok');
  renderPicked();
}

async function deleteCalc() {
  const name = state.editing;
  if (!name || !confirm(`計算チャンネル「${name}」を消しますか？`)) return;
  let j;
  try { j = await api('formulas/delete', { name }); } catch (e) { status('裏の処理とつながっていません', 'ng'); return; }
  if (!j.ok) { $('calcmsg').textContent = j.error; $('calcmsg').className = 'calcmsg ng'; return; }
  state.formulas = j.formulas;
  setChannels((state.channels || []).filter((n) => n !== name));
  $('calcdlg').close();
  status(`計算チャンネル「${name}」を消しました`, 'ok');
  renderPicked();
}

// ---------------------------------------------------------------- コースの線の版
// 線はコースごと。いま見ている基準の周の走行のコースについて、版を選ぶ・直す・残す・消す
function renderLineControls() {
  const s = state.refSession, sel = $('linesel');
  sel.textContent = '';
  sel.disabled = $('editline').disabled = !s || !s.course || !state.overlay || !state.overlay.origin;
  $('delline').hidden = !s || !s.line_name || !!state.draft;
  if (!s) return;
  sel.append(el('option', { value: '' }, '書き出しの線（ロガーと同じ）'));
  for (const n of s.line_names) sel.append(el('option', { value: n }, n));
  sel.value = s.line_name || '';
}

function mapLines() {
  if (state.draft) return { line: state.draft.line, sectors: state.draft.sectors, selected: state.draft.selected, draft: true };
  const s = state.refSession;
  return s ? { line: s.line, sectors: s.sectors, selected: null, draft: false } : null;
}

function renderDraft() {
  const d = state.draft, box = $('lineedit');
  box.hidden = !d;
  document.querySelector('.map').classList.toggle('editing', !!d);
  $('editline').disabled = !!d || $('linesel').disabled;
  $('delline').hidden = true;
  if (!d) { CourseMap.redraw(); renderLineControls(); return; }
  const t = $('linetarget');
  t.textContent = '';
  t.append(el('option', { value: '0' }, 'コントロールライン'));
  d.sectors.forEach((_, i) => t.append(el('option', { value: String(i + 1) }, `セクターの線 ${i + 1}（S${i + 1}｜S${i + 2}）`)));
  t.value = String(d.selected);
  const cur = d.selected === 0 ? d.line : d.sectors[d.selected - 1];
  $('linewidth').value = cur ? cur.width_m : '';
  $('delsector').disabled = d.selected === 0;
  CourseMap.redraw();
}

function startEdit() {
  const s = state.refSession;
  if (!s) return;
  const copy = (x) => (x ? { ...x } : null);
  state.draft = { course: s.course, line: copy(s.line) || { lat: 0, lon: 0, bearing: 0, width_m: 30 },
                  sectors: (s.sectors || []).map(copy), selected: 0 };
  $('linename').value = s.line_name || '';
  renderDraft();
}

function placeLine(p) {
  const d = state.draft;
  if (!d) return;
  const width = d.selected === 0 ? d.line.width_m : (d.sectors[d.selected - 1] || {}).width_m || 30;
  const line = { lat: +p.lat.toFixed(8), lon: +p.lon.toFixed(8), bearing: +p.bearing.toFixed(1), width_m: width };
  if (d.selected === 0) d.line = line; else d.sectors[d.selected - 1] = line;
  renderDraft();
}

async function lineRequest(action, body) {
  try {
    const j = await api('lines/' + action, body);
    if (j.rev !== undefined) state.rev = j.rev;
    if (!j.ok) { status(j.error, 'ng'); return false; }
    const byId = new Map(j.sessions.map((x) => [x.id, x]));
    state.sessions = state.sessions.map((x) => byId.get(x.id) || x);
    status(action === 'save' ? `線の版「${body.name}」を残し、周を切り直しました` : '線を切り替え、周を切り直しました', 'ok');
    render();
    return true;
  } catch (e) { status('裏の処理とつながっていません', 'ng'); return false; }
}

// ---------------------------------------------------------------- 開く・閉じる
async function openPath(path) {
  status('読み込んでいます…');
  message(null);
  try {
    const j = await api('open', { path });
    if (j.rev !== undefined) state.rev = j.rev;
    if (!j.ok) { status('開けませんでした', 'ng'); message(j.error, j.problems); return; }
    if (!state.sessions.some((s) => s.id === j.session.id)) state.sessions.push(j.session);
    status(`開きました：${j.session.title}`, 'ok');
    render();
    loadRecent();
  } catch (e) {
    status('裏の処理とつながっていません', 'ng');
  }
}

async function pickAndOpen() {
  const btn = $('open');
  btn.disabled = true;
  try {
    const j = await api('pick', {});
    if (j.ok && j.path) await openPath(j.path);
  } catch (e) {
    status('裏の処理とつながっていません', 'ng');
  } finally {
    btn.disabled = false;
  }
}

async function closeSession(id) {
  try { const j = await api('close', { id }); if (j.rev !== undefined) state.rev = j.rev; } catch (e) { /* 画面からは消す */ }
  state.sessions = state.sessions.filter((s) => s.id !== id);
  for (const k of [...state.picked]) if (k.startsWith(id + ':')) { state.picked.delete(k); state.colors.delete(k); }
  render();
}

async function loadRecent() {
  const sel = $('recent');
  try {
    const j = await api('recent');
    sel.textContent = '';
    sel.append(el('option', { value: '' }, '最近開いた走行'));
    for (const p of j.recent || []) sel.append(el('option', { value: p, title: p }, p.split(/[\\/]/).pop()));
  } catch (e) { /* つながっていなければ空のまま */ }
}

async function start() {
  try {
    const j = await api('health');
    status(`準備できました（v${j.version}）`, 'ok');
    state.rev = j.rev; state.notice = j.notice ? j.notice.id : 0;
    if (j.mode === 'browser' && j.mode_note) $('modenote').textContent = j.mode_note;
    $('modenote').hidden = !(j.mode === 'browser' && j.mode_note);
    if (j.notice) message(j.notice.text, j.notice.problems);
    await loadFormulas();
    const s = await api('sessions');
    state.sessions = s.sessions || [];
    render();
    loadRecent();
  } catch (e) {
    status('裏の処理とつながっていません', 'ng');
  }
  setInterval(watch, 3000);
}

// 3秒ごとに裏へ知らせる。ほかのアプリ（EcoLoggerアプリなど）から走行が渡されて rev が変わったら、一覧を読み直す。
// ブラウザで開いているときは、この知らせが途絶えると裏が終わる（タブを閉じたら終わる）
async function watch() {
  let j;
  try { j = await api('health'); } catch (e) { status('裏の処理とつながっていません（アプリを閉じたか、終わった）', 'ng'); return; }
  if (j.notice && j.notice.id !== state.notice) { state.notice = j.notice.id; message(j.notice.text, j.notice.problems); }
  if (j.rev === state.rev) return;
  state.rev = j.rev;
  try {
    const s = await api('sessions');
    const before = new Set(state.sessions.map((x) => x.id));
    state.sessions = s.sessions || [];
    const added = state.sessions.filter((x) => !before.has(x.id));
    for (const k of [...state.picked]) if (!state.sessions.some((x) => k.startsWith(x.id + ':'))) { state.picked.delete(k); state.colors.delete(k); }
    if (added.length) status(`開きました：${added.map((x) => x.title).join('、')}`, 'ok');
    render();
    loadRecent();
  } catch (e) { /* 次の知らせで読み直す */ }
}

$('open').onclick = pickAndOpen;
$('recent').onchange = (e) => { const p = e.target.value; e.target.value = ''; if (p) openPath(p); };
for (const r of document.querySelectorAll('input[name=mode]')) r.onchange = () => { state.mode = r.value; render(); };
for (const r of document.querySelectorAll('input[name=xaxis]')) r.onchange = () => { state.xaxis = r.value; renderPicked(); };
$('refsel').onchange = (e) => { state.ref = e.target.value; renderPicked(); };
new ResizeObserver(() => Chart.redraw()).observe($('plots'));
CourseMap.init($('mapcv'), {
  x: () => state.x, color: colorFor, lines: mapLines, editing: () => !!state.draft,
  onPick: (v) => { Chart.pin(v); CourseMap.setCursor(v); VideoPane.cursor(v, 'pin'); },
  onPlace: placeLine, photo: () => $('photo').checked, paint: paintForMap,
});
$('paintsel').onchange = (e) => { state.paint.mode = e.target.value; paintChanged(); };
$('paintch').onchange = (e) => { state.paint.ch = e.target.value; paintChanged(); };
$('markch').onchange = (e) => { state.paint.markCh = e.target.value; paintChanged(); };
$('decdot').onchange = (e) => { state.paint.dotted = e.target.checked; paintChanged(); };
$('accring').onchange = (e) => { state.paint.acc = e.target.checked; paintChanged(); };
$('markson').onchange = (e) => { state.paint.marks = e.target.checked; paintChanged(); };
for (const id of ['paintlo', 'painthi']) {
  $(id).onchange = () => {
    const v = (x) => (x.trim() === '' || !isFinite(+x) ? null : +x);
    const r = [v($('paintlo').value), v($('painthi').value)];
    if (r[0] === null && r[1] === null) delete state.paint.ranges[rangeKey()]; else state.paint.ranges[rangeKey()] = r;
    paintChanged();
  };
}
// 航空写真を敷くか（ふだんは敷く。選び直しは覚える）
try { $('photo').checked = localStorage.getItem('photo') !== '0'; } catch (e) { $('photo').checked = true; }
$('photo').onchange = () => { try { localStorage.setItem('photo', $('photo').checked ? '1' : '0'); } catch (e) { /* 覚えなくてよい */ } CourseMap.redraw(); };
new ResizeObserver(() => CourseMap.redraw()).observe($('mapcv'));
VideoPane.init($('vbox'), {
  x: () => state.x, color: colorFor, rate: () => +$('vrate').value,
  onTime: (x) => { Chart.pin(x); CourseMap.setCursor(x); },
  onPlayState: (on) => { $('vplay').textContent = on ? '⏸ 止める' : '▶ 再生'; },
});
$('vplay').onclick = () => VideoPane.toggle();
// チャンネルの一覧は、外を押すか Esc で閉じる
document.addEventListener('pointerdown', (e) => { const d = document.querySelector('.chanpick'); if (d.open && !d.contains(e.target)) d.open = false; });
document.addEventListener('keydown', (e) => { const d = document.querySelector('.chanpick'); if (e.key === 'Escape' && d.open) d.open = false; });
$('calcexpr').oninput = scheduleCheck;
$('calcsave').onclick = saveCalc;
$('calcdel').onclick = deleteCalc;
$('calccancel').onclick = () => $('calcdlg').close();
$('calcexpr').onkeydown = (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveCalc(); } };
$('vrate').onchange = (e) => VideoPane.setRate(+e.target.value);
$('linesel').onchange = (e) => { const s = state.refSession; if (s) lineRequest('use', { course: s.course, name: e.target.value || null }); };
$('editline').onclick = startEdit;
$('cancelline').onclick = () => { state.draft = null; renderDraft(); };
$('linetarget').onchange = (e) => { state.draft.selected = +e.target.value; renderDraft(); };
$('linewidth').onchange = (e) => {
  const d = state.draft, w = +e.target.value;
  if (!d || !(w >= 5 && w <= 300)) return;
  if (d.selected === 0) d.line.width_m = w; else d.sectors[d.selected - 1].width_m = w;
  CourseMap.redraw();
};
$('addsector').onclick = () => {
  const d = state.draft;
  d.sectors.push({ ...(d.sectors[d.sectors.length - 1] || d.line), width_m: 30 });
  d.selected = d.sectors.length;
  status('足したセクターの線を置く所を、図で押してください', 'ok');
  renderDraft();
};
$('delsector').onclick = () => { const d = state.draft; if (d.selected > 0) { d.sectors.splice(d.selected - 1, 1); d.selected = 0; renderDraft(); } };
$('saveline').onclick = async () => {
  const d = state.draft, name = $('linename').value.trim();
  if (!name) { status('版の名前を入れてください', 'ng'); return; }
  if (await lineRequest('save', { course: d.course, name, line: d.line, sectors: d.sectors })) { state.draft = null; renderDraft(); }
};
$('delline').onclick = async () => {
  const s = state.refSession;
  if (s && s.line_name && confirm(`線の版「${s.line_name}」を消しますか？（書き出しの線に戻ります）`)) lineRequest('delete', { course: s.course, name: s.line_name });
};
start();
