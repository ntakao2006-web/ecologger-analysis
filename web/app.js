// 画面の入口。裏（Python）とは /api/ のHTTPだけでやりとりする（pywebview の独自の呼び出しは使わない）
'use strict';

const state = { sessions: [], mode: 'app', picked: new Set() };
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
    cb.onchange = () => { if (cb.checked) state.picked.add(key); else state.picked.delete(key); renderPicked(); };
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
    if (usingApp && !x.logger) marks.push(el('span', { class: 'mark', title: 'ロガーはこの周を数えていない' }, 'ロガーに無い'));
    if (usingApp && x.logger && Math.abs(x.logger.diff_ms) > 5) {
      marks.push(el('span', { class: 'mark warn', title: `ロガーのタイム ${lapTime(x.logger.time_s)}` }, `ロガーと ${x.logger.diff_ms > 0 ? '+' : ''}${x.logger.diff_ms}ms`));
    }
    row.append(el('td', { class: 'marks' }, ...marks));
    body.append(row);
  }
  block.append(el('div', { class: 'tablewrap' }, el('table', { class: 'laptable' }, el('thead', {}, head), body)));
  if (usingApp) {
    const matched = new Set(laps.filter((x) => x.logger).map((x) => x.logger.lap));
    const only = s.laps.logger.filter((x) => !matched.has(x.lap)).map((x) => x.lap);
    if (only.length) block.append(el('p', { class: 'note' }, `ロガーの数えにだけある周：${only.join('、')}（コースの線では切れなかった）`));
  }
  if (s.notes.length) block.append(el('details', { class: 'notes' }, el('summary', {}, '覚え書き'), ...s.notes.map((n) => el('p', {}, n))));
  return block;
}

function renderPicked() {
  const names = [];
  for (const s of state.sessions) {
    for (const x of lapsOf(s)) if (state.picked.has(`${s.id}:${x.lap}`)) names.push(`${s.title} ${x.lap}周（${lapTime(x.time_s)}）`);
  }
  $('picked').textContent = names.length ? '選んだ周：' + names.join('、') : '';
}

// ---------------------------------------------------------------- 開く・閉じる
async function openPath(path) {
  status('読み込んでいます…');
  message(null);
  try {
    const j = await api('open', { path });
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
  try { await api('close', { id }); } catch (e) { /* 画面からは消す */ }
  state.sessions = state.sessions.filter((s) => s.id !== id);
  for (const k of [...state.picked]) if (k.startsWith(id + ':')) state.picked.delete(k);
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
    const s = await api('sessions');
    state.sessions = s.sessions || [];
    render();
    loadRecent();
  } catch (e) {
    status('裏の処理とつながっていません', 'ng');
  }
}

$('open').onclick = pickAndOpen;
$('recent').onchange = (e) => { const p = e.target.value; e.target.value = ''; if (p) openPath(p); };
for (const r of document.querySelectorAll('input[name=mode]')) r.onchange = () => { state.mode = r.value; render(); };
start();
