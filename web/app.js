// 画面の入口。裏（Python）とは /api/ のHTTPだけでやりとりする（pywebview の独自の呼び出しは使わない）
'use strict';

const state = { sessions: [], mode: 'app', picked: new Set(), xaxis: 'dist', x: 'dist', channels: null, ref: '', colors: new Map(), req: 0,
                overlay: null, refSession: null, draft: null, outedit: null, formulas: [], help: null, editing: null, rev: null, notice: 0,
                shiftSid: null, shiftWant: null, sumScope: 'run', oppPartner: 'best', oppMap: true };
const MAX_PICK = 8;            // 線の色の数（見分けのつく色は8つまで）
const SEC_LAPS = 4;            // 区間の表に並べる周の数
const SLOW_PCT = 5;            // 平均の周で外す遅さ（裏の sections.SLOW_RATIO と同じ）
const DEFAULT_CHANNELS = ['Speed', 'Throttle', 'RPM', 'LongAccel', 'LatAccel'];
const VAL_SLOW_PCT = 7;        // 周の表の「値」で色を付けるのは、タイムが中央値からこれより遅くない周だけ（アウトラップ・ピットの周を外す）
const VAL_FLAT_PCT = 2;        // 列の良い端と悪い端の差が、そのチャンネルの動く幅（周の中の最大の最大〜最小の最小）のこれより小さければ色を付けない
const STAT_NAMES = ['最大', '最小', '平均'];
// 周の表の見せ方（SP-10-39）：lapView は 'time'（セクター）か 'val'（グラフのチャンネルの周ごとの値）、lapStat は 0 最大・1 最小・2 平均、
// statDir はチャンネルごとの良い向き（1 大きいほど良い・-1 小さいほど良い。無ければ名前で決める）
state.lapView = 'time'; state.lapStat = 0; state.statDir = {}; state.lapStats = new Map(); state.lapStatsWait = new Set();
try {
  if (localStorage.getItem('lapview') === 'val') state.lapView = 'val';
  const st = Number(localStorage.getItem('lapstat')); if ([0, 1, 2].includes(st)) state.lapStat = st;
  const d = JSON.parse(localStorage.getItem('statdir') || 'null'); if (d && typeof d === 'object') state.statDir = d;
} catch (e) { /* 覚えていなくてよい */ }
try { const c = JSON.parse(localStorage.getItem('channels') || 'null'); if (Array.isArray(c)) state.channels = c; } catch (e) { /* 覚えていなくてよい */ }
// コース図の塗り（SP-10-12）。ranges は塗り方・チャンネルごとに手で決めた段の範囲 [低, 高]（null は自動）
state.paint = { mode: 'lap', ch: 'Speed', dotted: false, acc: false, marks: false, markCh: 'Speed', ranges: {}, curinfo: true };
try { const p = JSON.parse(localStorage.getItem('mappaint') || 'null'); if (p && typeof p === 'object') Object.assign(state.paint, p); } catch (e) { /* 覚えていなくてよい */ }
const $ = (id) => document.getElementById(id);
// 摩擦円に描く G の出どころ（SP-10-14）：'imu'（ふだん）か 'gnss'（測位から計算）
state.gsrc = 'imu';
try { if (localStorage.getItem('gsrc') === 'gnss') state.gsrc = 'gnss'; } catch (e) { /* 覚えていなくてよい */ }
// 再生とそろえ方（SP-10-16）：align は時間の横軸・「同じ時間」の置き方でそろえる所（'lap' 周の頭・'section' 区間の頭）、
// place は地図の点の置き方（'axis' 横軸に合わせる・'space' 同じ場所・'time' 同じ時間）
state.align = 'lap'; state.place = 'axis';
try {
  if (localStorage.getItem('align') === 'section') state.align = 'section';
  const pl = localStorage.getItem('place'); if (pl === 'space' || pl === 'time') state.place = pl;
} catch (e) { /* 覚えていなくてよい */ }
// 区間の頭の位置（基準の周の距離 m）
function splitHeads() {
  const L = mapLines();
  return (L && L.splits || []).map((ln) => { const r = CourseMap.refAt(ln.lat, ln.lon); return r ? r.s : null; });
}
// カーソルを、コース図と摩擦円の両方へ
function setCursors(x) { CourseMap.setCursor(x); GCircle.setCursor(x); }

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
  renderTable();
  renderPicked();
}

// 周の表だけを描き直す（グラフ・地図は取り直さない）
function renderTable() {
  const box = $('sessions');
  box.textContent = '';
  if (!state.sessions.length) {
    box.append(el('p', { class: 'empty' }, state.last && state.last.paths.length
      ? '「続きから」で、前回開いていた走行と選んでいた周をそのまま開きます。「別の走行…」で、ほかの走行のフォルダー（名前が .ecorun で終わるもの）を選びます。'
      : '「走行を開く…」で、走行のフォルダー（名前が .ecorun で終わるもの）を選びます。別の日の走行も続けて開けます。'));
  } else {
    const best = bests();
    for (const s of state.sessions) box.append(sessionBlock(s, best[s.course || s.id]));
  }
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
  block.append(summaryBar(s));

  const vals = state.lapView === 'val' ? lapValues(s, laps) : null;
  const nsec = vals ? 0 : Math.max(0, ...laps.map((x) => (x.sectors || []).length));
  const head = el('tr', {}, el('th', {}, ''), el('th', {}, '周'), el('th', {}, 'タイム'), el('th', { title: 'ベスト（同じコースの開いている走行の中で、測位の抜けた周を除く）との差' }, '差'));
  for (let i = 0; i < nsec; i++) head.append(el('th', {}, 'S' + (i + 1)));
  if (vals) for (const c of vals.cols) head.append(c.th);
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
    if (vals) for (const c of vals.cols) row.append(c.cell(x));
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
  if (vals && vals.note) block.append(el('p', { class: 'note' }, vals.note));
  if (usingApp || nsec) { const t = theoryLine(s, usingApp); if (t) block.append(t); }
  if (usingApp && s.line_name) block.append(el('p', { class: 'note' }, `線の版「${s.line_name}」で切っています（ロガーとは線が違うので、ロガーのタイムとは比べません）`));
  if (compareLogger) {
    const matched = new Set(laps.filter((x) => x.logger).map((x) => x.logger.lap));
    const only = s.laps.logger.filter((x) => !matched.has(x.lap)).map((x) => x.lap);
    if (only.length) block.append(el('p', { class: 'note' }, `ロガーの数えにだけある周：${only.join('、')}（コースの線では切れなかった）`));
  }
  if (s.notes.length) block.append(el('details', { class: 'notes' }, el('summary', {}, '覚え書き'), ...s.notes.map((n) => el('p', {}, n))));
  return block;
}

// ---------------------------------------------------------------- 周の表の「値」（SP-10-39・仕様 3-2・6-4）
// グラフに出しているチャンネルの、周ごとの最大・最小・平均。列ごとに、良い端を緑・悪い端を赤にする
// （色を付けるのは、測位の抜けが無く、タイムが中央値から VAL_SLOW_PCT % より遅くない周だけ）
function statDir(name) {
  const d = state.statDir[name];
  return d === 1 || d === -1 ? d : (/temp/i.test(name) && !/volt/i.test(name) ? -1 : 1);     // 温度は低いほど良い（温度の電圧は除く）
}
function fmtVal(v) {
  const a = Math.abs(v);
  const t = v.toFixed(a >= 100 ? 0 : a >= 10 ? 1 : 2);
  return /^-0(\.0+)?$/.test(t) ? t.slice(1) : t;     // 「-0.00」とは出さない
}

function lapValues(s, laps) {
  const have = new Map(s.channels.map((c) => [c.name, c]));
  for (const f of state.formulas) if (!have.has(f.name)) have.set(f.name, { name: f.name, label: f.name, unit: f.unit, calc: true, expr: f.expr });
  // グラフのチャンネルをまだ選んでいなければ、グラフと同じ既定の分
  const chans = state.channels === null ? DEFAULT_CHANNELS.filter((n) => have.has(n)).slice(0, 3) : state.channels.filter((n) => have.has(n));
  if (!chans.length) return { cols: [], note: 'グラフに出すチャンネル（グラフの枠の「チャンネル」）を選ぶと、その周ごとの値がここに並びます' };
  const sig = chans.map((n) => { const m = have.get(n); return m.calc ? `${n}=${m.expr}` : n; }).join('');
  const key = `${s.id}|${state.mode}|${state.rev}|${sig}`;
  const got = state.lapStats.get(key);
  if (!got) fetchLapStats(key, s.id, chans);
  const k = state.lapStat;
  // 色を付ける周：抜けが無く、遅すぎない
  const times = laps.filter((x) => !x.gap).map((x) => x.time_s).sort((a, b) => a - b);
  const med = times.length ? times[Math.floor((times.length - 1) / 2)] : null;
  const fair = (x) => !x.gap && med !== null && x.time_s <= med * (1 + VAL_SLOW_PCT / 100);
  const cols = chans.map((n) => {
    const m = have.get(n), dir = statDir(n);
    const raw = (x) => (got && got.laps[String(x.lap)] && got.laps[String(x.lap)][n]) || null;
    const val = (x) => { const r = raw(x); return r ? r[k] : null; };
    const rows = got ? laps.filter(fair).map(raw).filter(Boolean) : [];
    const pool = rows.map((r) => r[k]);
    const hi = pool.length > 1 ? Math.max(...pool) : null, lo = pool.length > 1 ? Math.min(...pool) : null;
    const span = rows.length ? Math.max(...rows.map((r) => r[0])) - Math.min(...rows.map((r) => r[1])) : 0;
    const flat = hi === null || !(hi - lo >= span * VAL_FLAT_PCT / 100) || hi === lo;
    const good = dir > 0 ? hi : lo, bad = dir > 0 ? lo : hi;
    const th = el('th', { class: 'valhead', title: `${m.label || n}${m.unit ? '（' + m.unit + '）' : ''}の周ごとの${STAT_NAMES[k]}。` +
      `${dir > 0 ? '大きい' : '小さい'}ほど良いとして、良い端を緑・悪い端を赤にしています（押すと向きを変えます）。` +
      `どの周もほとんど同じ値（差がこのチャンネルの動く幅の${VAL_FLAT_PCT}%に満たない）なら色を付けません` },
      `${m.label || n}${dir > 0 ? ' ↑' : ' ↓'}`);
    th.onclick = () => {
      state.statDir[n] = -dir;
      try { localStorage.setItem('statdir', JSON.stringify(state.statDir)); } catch (e) { /* 覚えなくてよい */ }
      renderTable();
    };
    const cell = (x) => {
      if (!got) return el('td', { class: 'num val' }, '…');
      const v = val(x);
      if (v === null) return el('td', { class: 'num val' }, '—');
      const on = !flat && fair(x);
      const cls = on && v === good ? ' faster' : on && v === bad ? ' slower' : '';
      return el('td', { class: 'num val' + cls }, fmtVal(v));
    };
    return { th, cell };
  });
  let note = null;
  if (got && Object.keys(got.errors).length) note = Object.entries(got.errors).map(([n, e]) => `${n}：${e}`).join('　');
  else if (got && got.fail) note = got.fail;
  return { cols, note };
}

async function fetchLapStats(key, id, channels) {
  if (state.lapStatsWait.has(key)) return;
  state.lapStatsWait.add(key);
  try {
    const j = await api('lapstats', { id, mode: state.mode, channels });
    state.lapStats.set(key, j.ok ? { laps: j.laps, errors: j.errors } : { laps: {}, errors: {}, fail: j.error });
  } catch (e) {
    state.lapStats.set(key, { laps: {}, errors: {}, fail: '裏の処理とつながっていません' });
  } finally { state.lapStatsWait.delete(key); }
  if (state.lapStats.size > 40) state.lapStats.delete(state.lapStats.keys().next().value);
  renderTable();
}

function renderLapView() {
  for (const r of document.querySelectorAll('input[name=lapview]')) r.checked = r.value === state.lapView;
  $('lapstat').value = String(state.lapStat);
  $('lapstat').hidden = state.lapView !== 'val';
}

// ---------------------------------------------------------------- 走行のまとめ（SP-10-40・仕様 3-7）
// 周の表の上に1段：ベスト・理論ベスト・連続ベスト・ベストと理論ベストの差・上位3周と5周の平均と幅（計算は web/summary.js）。
// 使う周は、ふだんはこの走行だけ。同じコースの走行をほかにも開いていれば「同じコースの全部」に切り替えられる
function summaryBar(s) {
  const same = s.course ? state.sessions.filter((x) => x.course === s.course) : [s];
  const all = state.sumScope === 'course' && same.length > 1;
  const runs = (all ? same : [s]).map((x) => ({ laps: lapsOf(x), rolling: state.mode === 'app' && x.laps.app ? x.rolling : null }));
  const r = RunSummary.of(runs);
  const box = el('div', { class: 'runsum' });
  if (same.length > 1) {
    const sel = el('select', { title: 'まとめに使う周。タイヤやコースの状態が違う日の周と比べると届かない数になるので、ふだんはこの走行だけ' },
      el('option', { value: 'run' }, 'この走行'), el('option', { value: 'course' }, `同じコースの${same.length}走行`));
    sel.value = all ? 'course' : 'run';
    sel.onchange = () => { state.sumScope = sel.value; renderTable(); };
    box.append(sel);
  }
  if (!r.n) { box.append(el('span', { class: 'note' }, 'まとめに使える周がありません（測位の抜けた周は使いません）')); return box; }
  const item = (name, v, title, extra) => el('span', { class: 'sumitem', title }, name + ' ', el('b', {}, lapTime(v)), ...(extra || []));
  box.append(item('ベスト', r.best, `${r.n}周のうち一番速い周（測位の抜けた周は使わない）`));
  if (r.theory !== null) {
    const kind = r.theoryKind === 'splits' ? '区間' : 'セクター';
    const d = el('span', { class: 'diff' + (r.bigGap ? ' bad' : '') }, ` (ベストと ${gap(r.gapToTheory)})`);
    box.append(item(`理論ベスト（${kind}）`, r.theory,
      `${kind}ごとのベストを足したもの。括弧はベストとの差。` + (r.bigGap ? `差が${RunSummary.BIG_GAP_S.toFixed(1)}秒を超えています：詰まった周があったか、${kind}ごとに走りがばらついているかを見ます` : ''), [d]));
  }
  if (r.rolling !== null) box.append(item('連続ベスト', r.rolling, '周の境目をまたいで、続けて走った区間を1周ぶんつないだときの最速'));
  for (const t of [r.top3, r.top5]) {
    if (!t) continue;
    const extra = [el('span', { class: 'diff' }, ` 幅 ${t.range.toFixed(3)}`)];
    if (t.n === 3 && r.verdict) extra.push(el('span', { class: `verdict ${r.verdict.cls}` }, r.verdict.text));
    box.append(item(`上位${t.n}周 平均`, t.mean,
      `速い順に${t.n}周の平均と、幅（その中の最も遅い周 − 最も速い周）。ベストの${Math.round(RunSummary.SLOW_X * 100)}%より遅い周（ピットに入った・止まった周）は入れない。` +
      (t.n === 3 ? '上位3周の幅の目安（Garmin の使い方の資料）：0.5秒以下は上出来、0.5〜1.0秒はまあまあ、1.0〜2.0秒は安定が最優先、2.0秒より上はわけを探す' : ''), extra));
  }
  return box;
}

// ---------------------------------------------------------------- 区間（コーナー）の表と理論ベスト（SP-10-13・仕様 3-2）
// 理論ベスト・連続ベスト・平均の周は、裏が同じコースの開いている走行どうしで出したもの（stats）
function theoryLine(s, usingApp) {
  const st = s.stats;
  if (!st) return null;
  const parts = [];
  const sp = usingApp && st.splits && st.splits.best.length ? st.splits : null;
  if (sp && sp.theory !== null) parts.push(['理論ベスト（区間）', sp.theory, '区間ごとのベストを足したもの']);
  if (st.sectors && st.sectors.theory !== null) parts.push(['理論ベスト（セクター）', st.sectors.theory, 'セクターごとのベストを足したもの']);
  if (sp && sp.rolling !== null) parts.push(['連続ベスト', sp.rolling, '周の境目をまたいで、続けて走った区間を1周ぶんつないだときの最速']);
  if (sp && sp.average !== null) parts.push(['平均の周', sp.average, `区間ごとの平均を足したもの。区間タイムがその区間の中央値より${SLOW_PCT}%以上遅い周（詰まった・ピット）は、その区間だけ外す`]);
  if (!parts.length) return null;
  const p = el('p', { class: 'theory', title: '同じコースの開いている走行の周から（測位の抜けた周を除く）。括弧はベストの周との差' });
  parts.forEach(([name, v, why], i) => {
    if (i) p.append('　');
    p.append(el('span', { title: why }, name + ' ', el('b', {}, lapTime(v)),
                st.best_lap !== null ? el('span', { class: 'diff' }, ` (${gap(v - st.best_lap)})`) : ''));
  });
  return p;
}

// 区間の表に出す周：選んだ周のうち、基準の周と同じコースで区間の数が出ている周（基準の周を先に、SEC_LAPS 周まで）
function sectionLaps() {
  const picks = pickedLaps();
  const o = state.overlay;
  const refKey = o && o.laps[o.ref] ? o.laps[o.ref].key : null;
  const ref = picks.find((p) => p.key === refKey) || picks.slice().sort((a, b) => a.time_s - b.time_s)[0];
  if (!ref) return { ref: null, laps: [] };
  const course = ref.s.course || ref.s.id;
  const rows = picks.filter((p) => (p.s.course || p.s.id) === course).map((p) => ({ ...p, x: lapsOf(p.s).find((x) => x.lap === p.lap) }));
  rows.sort((a, b) => (a.key === ref.key ? -1 : b.key === ref.key ? 1 : 0));
  return { ref, laps: rows.filter((r) => r.x && r.x.sec && r.x.sec.length).slice(0, SEC_LAPS) };
}

function renderSections() {
  const box = $('secbox');
  box.textContent = '';
  CourseMap.setHighlights([]);            // 伸びしろの区間は、下で出せたときだけ描き直す
  if (state.secdraft) { box.hidden = false; renderSecEdit(box); return; }
  const { ref, laps } = sectionLaps();
  const s = ref && ref.s;
  box.hidden = !s || state.mode !== 'app' || !s.laps.app;
  if (box.hidden) return;
  const splits = s.splits || [];
  const auto = el('button', { type: 'button', title: `基準の周（${ref.lap}周）のブレーキの踏み始めに区間の線を置き、同じコースの全部の周をその線で切ります。` +
    '前後Gもブレーキも無い記録では、直線の最高速の所に置きます。線はコースごとに、使っている線の版（版を使っていなければ、その場で置いた分）に残ります' },
  splits.length ? '区間を置き直す' : '区間を自動で置く');
  auto.onclick = () => sectionRequest('auto', { id: ref.id, lap: ref.lap }, auto);
  const edit = el('button', { type: 'button', title: '区間の線を手で足す・消す・動かし、区間に名前を付けます（地図の線はつかんで動かせます）' }, '区間を直す');
  edit.onclick = startSecEdit;
  edit.disabled = !s.course || !state.refSession;
  const head = el('div', { class: 'sechead' }, el('h3', {}, '区間'), auto, edit);
  if (splits.length) {
    const clear = el('button', { type: 'button', title: 'このコースの区間の線を消します' }, '区間を消す');
    clear.onclick = () => { if (confirm('このコースの区間の線を消しますか？')) sectionRequest('set', { course: s.course, splits: [] }, clear); };
    head.append(clear);
  }
  box.append(head);
  if (!s.course) { auto.disabled = true; box.append(el('p', { class: 'note' }, 'コースの名前が分からない走行なので、区間の線を残せません')); return; }
  if (!splits.length) { box.append(el('p', { class: 'note' }, `区間の線がまだありません。「区間を自動で置く」で、基準の周（${ref.lap}周）のブレーキの踏み始めに置きます`)); return; }
  box.append(oppBlock(s, splits.length + 1));
  if (!laps.length) { box.append(el('p', { class: 'note' }, '選んだ周に区間の数がありません（測位が足りない周）')); return; }
  const st = s.stats && s.stats.splits;
  const n = laps[0].x.sec.length;
  const th = (text, title) => el('th', title ? { title } : {}, text);
  const thead = el('tr', {}, th('区間'), th('周'), th('タイム', '区間の線から次の区間の線まで（秒）'),
    th('差', 'その区間のベスト（同じコースの開いている走行の中で）との差'),
    th('入口', '区間の頭の線を越えたときの速さ（km/h）'),
    th('ブレーキ', '区間の頭の線からブレーキの踏み始めまで（m、＋は奥で踏んだ）。区間の線は、置いたときの基準の周の踏み始めにある'),
    th('ボトム', '区間の中の最小の速さ（km/h）'), th('位置', 'ボトムの所が区間の頭から何 m か（手前なら脱出重視、奥なら進入重視）'),
    th('半径', 'ボトムの前後2秒の最小の旋回半径（m、測位から計算）'), th('出口', '次の区間の線を越えたときの速さ（km/h）'));
  const body = el('tbody');
  const refRow = laps[0];
  const has = (v) => v !== null && v !== undefined;
  const num = (v, d = 1) => (has(v) ? v.toFixed(d) : '—');
  const signed = (v) => (has(v) ? (v > 0.05 ? '+' : v < -0.05 ? '−' : '') + Math.abs(v).toFixed(1) : '—');
  for (let i = 0; i < n; i++) {
    const best = st ? st.best[i] : null;
    laps.forEach((r, k) => {
      const t = (r.x.splits || [])[i], m = r.x.sec[i] || {};
      const rt = (refRow.x.splits || [])[i];
      let cls = 'num time';
      if (has(t) && has(best) && Math.abs(t - best) < 0.0005) cls += ' sbest';
      else if (k > 0 && has(t) && has(rt)) cls += t < rt - 0.0005 ? ' faster' : t > rt + 0.0005 ? ' slower' : '';
      const brakeCell = el('td', { class: 'num' + (i > 0 && has(m.brake_t) ? ' brakecell' : '') }, i === 0 ? '' : signed(m.brake_m));
      if (i > 0 && has(m.brake_t)) {
        brakeCell.title = 'ブレーキの踏み始めの瞬間で、2周の動画を止めて並べます（基準の周と、この周。基準の周の行なら2番目の周と）';
        brakeCell.onclick = (e) => { e.stopPropagation(); brakeMoments(i, laps, k); };
      }
      const tr = el('tr', { class: 'secrow' + (k === 0 ? ' secfirst' : ''), title: '押すと、地図・グラフ・動画をこの区間へ動かします' },
        el('td', { class: 'sec' }, ...(k === 0 ? [playBtn(i, n), String(i + 1) + ((s.split_names || [])[i] ? ' ' + s.split_names[i] : '')] : [''])),
        el('td', { class: 'num', title: `${r.s.title} ${r.lap}周${k === 0 ? '（基準）' : ''}` },
           el('span', { class: 'lapdot', style: `background:${colorFor(r.key)}` }), String(r.lap)),
        el('td', { class: cls }, has(t) ? t.toFixed(3) : '—'),
        el('td', { class: 'num diff' }, has(best) && has(t) ? gap(t - best) : ''),
        el('td', { class: 'num' }, num(m.entry)), brakeCell,
        el('td', { class: 'num' }, num(m.min_kmh)), el('td', { class: 'num' }, num(m.min_at_m, 0)),
        el('td', { class: 'num' }, num(m.min_radius_m, 0)), el('td', { class: 'num' }, num(m.exit)));
      tr.onclick = () => goSection(i, n);
      body.append(tr);
    });
    if (st && has(st.mean[i])) {
      body.append(el('tr', { class: 'secstat' }, el('td', {}, ''), el('td', { colspan: '9' },
        `ベスト ${num(best, 3)}　平均 ${num(st.mean[i], 3)}${has(st.sd[i]) ? `　ばらつき ±${num(st.sd[i], 3)}` : ''}` +
        `（${st.used[i]}周。中央値より${SLOW_PCT}%以上遅い周は外す）`)));
    }
  }
  box.append(el('div', { class: 'tablewrap' }, el('table', { class: 'laptable sectable' }, el('thead', {}, thead), body)));
  box.append(el('p', { class: 'note' }, `行を押すとその区間へ、ブレーキの数を押すと2周の動画をその瞬間で並べます。区間1はコントロールラインから最初の区間の線まで。区間の線は地図に「区2」などの札で出ます。` +
    `色：紫＝その区間のベスト、緑・赤＝基準の周（${refRow.lap}周）より速い・遅い。選んだ周のうち${SEC_LAPS}周まで`));
}

// ---------------------------------------------------------------- 伸びしろの大きい区間（SP-10-41・仕様 3-7）
// 区間ごとに「比べる相手（ベストの周か平均の周）の区間タイム − その区間のベスト」を出し、大きい順に3つを地図の上で番号つきの帯にして、
// 一覧にする（押すとその区間へ寄る）。それぞれに、その区間のベストの周と相手の違いをブレーキ・頂点・速さの数で（計算は web/summary.js）。
// 使う周は走行のまとめと同じ（この走行か、同じコースの全部）
function oppBlock(s, n) {
  const same = s.course ? state.sessions.filter((x) => x.course === s.course) : [s];
  const all = state.sumScope === 'course' && same.length > 1;
  const runs = (all ? same : [s]).map((x) => ({ id: x.id, laps: lapsOf(x) }));
  const o = RunSummary.opportunities(runs, state.oppPartner);
  const wrap = el('div', { class: 'opps' });
  const sel = el('select', { title: 'ベストの周：ベストを縮めたいとき（ベストの周がどの区間で、ほかの周のその区間のベストより遅いか）。平均の周：安定させたいとき（区間ごとの平均。中央値より5%以上遅い周はその区間だけ外す）' },
    el('option', { value: 'best' }, 'ベストの周'), el('option', { value: 'mean' }, '平均の周'));
  sel.value = state.oppPartner;
  sel.onchange = () => { state.oppPartner = sel.value; renderSections(); };
  const onMap = el('input', { type: 'checkbox' });
  onMap.checked = state.oppMap;
  onMap.onchange = () => { state.oppMap = onMap.checked; renderSections(); };
  wrap.append(el('div', { class: 'opphead' }, el('b', {}, '伸びしろ'), ' 比べる相手 ', sel,
    el('label', { title: '大きい順に3つの区間を、地図の上で番号つきの帯にします' }, onMap, ' 地図に出す'),
    el('span', { class: 'note' }, all ? `同じコースの${same.length}走行の周から` : 'この走行の周から（周の表の上の「まとめ」で切り替え）')));
  if (!o || !o.items.length) {
    wrap.append(el('p', { class: 'note' }, o ? 'どの区間も、比べる相手がその区間のベストです' : '区間タイムがそろった周がありません（測位の抜けた周は使いません）'));
    return wrap;
  }
  const lapName = (L) => {
    if (!L) return '平均の周';
    const x = state.sessions.find((v) => v.id === L.id);
    return all && x ? `${jstDate(x.t0_utc).slice(5, 10).replace('-', '/')} ${L.lap}周` : `${L.lap}周`;
  };
  const has = (v) => v !== null && v !== undefined && isFinite(v);
  const sgn = (v, d) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d);
  const where = (v, d) => (Math.abs(v) < 0.5 ? '同じ所' : `${Math.abs(v).toFixed(d)} m ${v > 0 ? '奥' : '手前'}`);
  const names = s.split_names || [];
  const hl = [];
  o.items.forEach((it, k) => {
    const r = sectionRange(it.i, n, 's');
    if (r) hl.push({ a: r[0], b: r[1], label: String(k + 1) });
    const nums = [];
    if (has(it.brake)) nums.push(el('span', { title: 'ブレーキの踏み始め（区間の線から m）の違い。奥＝ベストの周の方が遅くまで踏まなかった' }, `ブレーキ ${where(it.brake, 1)}`));
    if (has(it.apexKmh)) {
      nums.push(el('span', { title: 'ボトム（区間の中の最小の速さ）の違いと、その位置の違い' },
        `ボトム ${sgn(it.apexKmh, 1)} km/h` + (has(it.apexAt) ? `（${where(it.apexAt, 0)}）` : '')));
    }
    const lat = apexLateral(it, r);
    if (lat !== null) {
      nums.push(el('span', { title: 'ボトムの所の、基準の周の線からの横のずれの違い（右が＋）。1 m より小さい違いは測位の誤差の内' },
        `横 ${lat >= 0 ? '右' : '左'} ${Math.abs(lat).toFixed(1)} m${Math.abs(lat) < 1 ? '（読まない）' : ''}`));
    }
    if (has(it.entry) || has(it.exit)) {
      nums.push(el('span', { title: '区間の頭・終わりの線を越えたときの速さの違い' },
        `入口 ${has(it.entry) ? sgn(it.entry, 1) : '—'}・出口 ${has(it.exit) ? sgn(it.exit, 1) : '—'} km/h`));
    }
    const row = el('div', { class: 'opp', title: '押すと、地図・グラフ・動画をこの区間へ動かします' },
      el('span', { class: 'oppno' }, String(k + 1)),
      el('span', { class: 'oppname' }, `区間${it.i + 1}${names[it.i] ? ' ' + names[it.i] : ''}`),
      el('b', { class: 'opploss' }, `${it.loss.toFixed(3)} 秒`),
      el('span', { class: 'note' }, `ベスト ${lapName(it.bestLap)} ${it.bestTime.toFixed(3)} ／ ${lapName(it.partnerLap)} ${it.partnerTime.toFixed(3)}`),
      el('div', { class: 'oppnums' }, el('span', { class: 'note' }, `${lapName(it.bestLap)}は`), ...nums));
    row.onclick = () => goSection(it.i, n);
    wrap.append(row);
  });
  const partner = o.partner === 'best' ? `ベストの周 ${lapName(o.partnerLap)} ${lapTime(o.partnerTime)}` : `平均の周 ${lapTime(o.partnerTime)}`;
  wrap.append(el('p', { class: 'note' }, `区間ごとの、ベストとの差の合計 ${o.total.toFixed(3)} 秒（${o.sections}区間。${partner} − 区間ごとのベストの和）。数はその区間のベストの周から見た違い`));
  CourseMap.setHighlights(state.oppMap ? hl : []);
  return wrap;
}

// 伸びしろの区間の、ボトムの所の横のずれの違い（ベストの周 − 比べる周。2周とも重ねていて、相手がベストの周のときだけ）
function apexLateral(it, r) {
  const o = state.overlay;
  if (!o || !o.distance || !r || !it.partnerLap || it.bestApexAt === null || it.partnerApexAt === null) return null;
  const find = (L) => o.laps.find((l) => l.key === `${L.id}:${L.lap}`);
  const a = find(it.bestLap), b = find(it.partnerLap);
  if (!a || !b || !a.off || !b.off) return null;
  const at = (lap, sd) => {
    const g = o.grid;
    let lo = 0, hi = g.length - 1;
    if (sd < g[0] || sd > g[hi]) return null;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (g[m] <= sd) lo = m; else hi = m; }
    const u = lap.off[lo], v = lap.off[hi];
    if (u === null || v === null) return u ?? v;
    return g[hi] > g[lo] ? u + (v - u) * (sd - g[lo]) / (g[hi] - g[lo]) : u;
  };
  const x = at(a, r[0] + it.bestApexAt), y = at(b, r[0] + it.partnerApexAt);
  return x === null || y === null ? null : x - y;
}

// 区間 i（n 区間のうち）の、基準の周の横軸の範囲 [頭, 終わり]。分からなければ null。k：'s'（距離）・'t'（時間）。無ければ今の横軸
function sectionRange(i, n, k) {
  const o = state.overlay, L = mapLines();
  if (!o || !L || !L.splits) return null;
  const key = k || (state.x === 'dist' ? 's' : 't');

  const refLap = o.laps[o.ref];
  const end = key === 's' ? o.ref_length_m : refLap && refLap.time_s;
  const heads = L.splits.map((ln) => { const r = CourseMap.refAt(ln.lat, ln.lon); return r ? r[key] : null; });
  const a = i === 0 ? 0 : heads[i - 1], b = i >= n - 1 ? end : heads[i];
  return a === null || a === undefined || b === null || b === undefined ? null : [a, b];
}

// 行を押した：地図をその区間に寄せ、グラフの横の範囲をその区間（前後に少し余らせる）にし、カーソル・動画を区間の頭へ
function goSection(i, n) {
  const r = sectionRange(i, n);
  if (!r) { status('この区間の所が分かりません（基準の周の測位が足りない）', 'ng'); return; }
  Chart.zoom(r[0], r[1]);
  CourseMap.fitRange(r[0], r[1]);
  Chart.pin(r[0]); setCursors(r[0]); VideoPane.cursor(r[0], 'pin');
}

// ---------------------------------------------------------------- 区間を再生する（SP-10-16）
// 区間の頭から動画とカーソルを動かし、区間の終わりで止める。動画が無ければ、カーソル（地図の点・グラフ）だけを動かす
function playBtn(i, n) {
  const b = el('button', { type: 'button', class: 'secplay', title: 'この区間を頭から再生し、終わりで止めます（動画が無ければ地図の点とグラフのカーソルだけを動かします。速さは動画の枠の倍率）' }, '▶');
  b.onclick = (e) => { e.stopPropagation(); playSection(i, n); };
  return b;
}
function stopCursorPlay() {
  if (state.cursorPlay) { cancelAnimationFrame(state.cursorPlay.raf); state.cursorPlay = null; }
}
function playSection(i, n) {
  stopCursorPlay();
  const r = sectionRange(i, n);
  if (!r) { status('この区間の所が分かりません（基準の周の測位が足りない）', 'ng'); return; }
  goSection(i, n);
  if (VideoPane.playRange(r[0], r[1])) { status(`区間${i + 1}を動画と一緒に再生しています（区間の終わりで止まります）`, 'ok'); return; }
  const t0 = Sync.refTime(r[0]), t1 = Sync.refTime(r[1]);
  if (t0 === null || t1 === null) { status('この区間の時刻が分かりません', 'ng'); return; }
  const rate = +$('vrate').value || 1, start = performance.now();
  const step = (now) => {
    const tr = Math.min(t1, t0 + (now - start) / 1000 * rate), x = Sync.xOfRef(tr);
    if (x !== null) { Chart.pin(x); setCursors(x); }
    if (tr >= t1) { state.cursorPlay = null; status(`区間${i + 1}の終わりで止めました`, 'ok'); return; }
    state.cursorPlay.raf = requestAnimationFrame(step);
  };
  state.cursorPlay = { raf: requestAnimationFrame(step) };
  status(`区間${i + 1}を再生しています（動画が無いので、地図の点とグラフのカーソルだけ。グラフか地図を押すと止まります）`, 'ok');
}

// ブレーキの数を押した：基準の周と、押した周（基準の周の行なら2番目の周）の動画を、それぞれのブレーキの踏み始めの瞬間で止めて並べる
function brakeMoments(i, rows, k) {
  const a = rows[0], b = k === 0 ? rows[1] : rows[k];
  const pick = [a, b].filter(Boolean).map((r) => ({ key: r.key, lap: r.lap, t: (r.x.sec[i] || {}).brake_t }));
  const missing = pick.filter((p) => p.t === null || p.t === undefined);
  if (missing.length) status(`${missing.map((p) => p.lap + '周').join('・')}はこの区間でブレーキを踏んでいません`, 'ng');
  const ok = pick.filter((p) => p.t !== null && p.t !== undefined);
  if (!ok.length) return;
  // カーソルは押した周の踏み始めの所（基準の周の横軸で）
  const me = ok.find((p) => p.key === (k === 0 ? a : rows[k]).key) || ok[0];
  const lap = state.overlay && state.overlay.laps.find((l) => l.key === me.key);
  let x = me.t;
  if (state.x === 'dist' && lap && lap.track) x = interpLine(lap.track.t, lap.track.s, me.t);
  if (x !== null) { Chart.pinOnly(x); setCursors(x); }
  VideoPane.moments(ok.map((p) => ({ key: p.key, t: p.t })));
  if (!missing.length) status(`ブレーキの踏み始めで、${ok.map((p) => p.lap + '周').join('と')}の動画を止めて並べました（カーソルを動かすと、いつもの「同じ所」に戻ります）`, 'ok');
}

// 増える一方の xa（null は飛ばす）で、x のときの ya
function interpLine(xa, ya, x) {
  let pi = -1;
  for (let i = 0; i < xa.length; i++) {
    if (xa[i] === null || ya[i] === null) continue;
    if (xa[i] >= x) {
      if (pi < 0) return ya[i];
      return ya[pi] + (ya[i] - ya[pi]) * (x - xa[pi]) / ((xa[i] - xa[pi]) || 1);
    }
    pi = i;
  }
  return pi < 0 ? null : ya[pi];
}

async function sectionRequest(action, body, btn) {
  btn.disabled = true;
  status(action === 'auto' ? '区間の線を置いています…' : '区間の線を消しています…');
  try {
    const j = await api('sections/' + action, body);
    if (j.rev !== undefined) state.rev = j.rev;
    if (!j.ok) { status(j.error, 'ng'); return; }
    state.sessions = j.sessions;
    status(action === 'auto'
      ? `区間の線を${j.auto.count}本置き、周を切り直しました（${j.auto.how === 'brake' ? 'ブレーキの踏み始め' : '直線の最高速の所'}。周の頭から ${j.auto.at_m.map((v) => Math.round(v)).join('・')} m）`
      : '区間の線を消しました', 'ok');
    render();
  } catch (e) { status('裏の処理とつながっていません', 'ng'); } finally { btn.disabled = false; }
}

// ---------------------------------------------------------------- 区間の線を手で直す（SP-10-37・仕様 3-2）
// 直している中身：state.secdraft = {course, head（区間1の名前）, items: [{line, name（その線から始まる区間の名前）}], sel（選んだ線の番号か -1）, dirty}
// 線は基準の周の線に沿った順に並べる。足す＝カーソルの所で切る（区間を2つに分ける）、消す＝前の区間とつなぐ、
// 動かす＝地図で線をつかんで引くか、選んだ線をカーソルの所へ。保存すると同じコースの全部の周を切り直す
const SPLIT_WIDTH_M = 30;      // 区間の線の幅（裏の sections.SPLIT_WIDTH_M と同じ）
const SPLIT_NEAR_M = 10;       // ほかの線・コントロールラインとこれより近い所には足さない

function splitLabel(i, name) { return `区${i + 2}${name ? ' ' + name : ''}`; }
function refPos(line) { const r = CourseMap.refAt(line.lat, line.lon); return r ? r.s : null; }

function sortDraft() {
  const d = state.secdraft, selLine = d.sel >= 0 ? d.items[d.sel] : null;
  d.items.sort((a, b) => (refPos(a.line) ?? 0) - (refPos(b.line) ?? 0));
  d.sel = selLine ? d.items.indexOf(selLine) : -1;
}

function startSecEdit() {
  const s = state.refSession;
  if (!s || !s.course) return;
  if (state.draft || state.outedit) { status('線を直している・コース図を直している間は、区間を直せません', 'ng'); return; }
  const names = s.split_names || [];
  state.secdraft = { course: s.course, head: names[0] || '', sel: -1, dirty: false,
                     items: (s.splits || []).map((line, i) => ({ line: { ...line }, name: names[i + 1] || '' })) };
  sortDraft();
  secChanged(false);
}

function secChanged(dirty) {
  if (dirty) state.secdraft.dirty = true;
  CourseMap.redraw();
  updateMarks();
  renderSections();
}

// カーソルの所に区間の線を足す（その区間を2つに分ける）
function secCut() {
  const d = state.secdraft, v = Chart.cursor();
  const ln = v === null ? null : CourseMap.lineAt(v);
  if (!ln) { status('グラフ・地図・動画でカーソルを動かしてから押してください（基準の周の上の所に足します）', 'ng'); return; }
  const line = { lat: +ln.lat.toFixed(8), lon: +ln.lon.toFixed(8), bearing: +ln.bearing.toFixed(1), width_m: SPLIT_WIDTH_M };
  const at = refPos(line), len = state.overlay && state.overlay.ref_length_m;
  if (at === null || at < SPLIT_NEAR_M || (len && len - at < SPLIT_NEAR_M) ||
      d.items.some((x) => Math.abs((refPos(x.line) ?? -1e9) - at) < SPLIT_NEAR_M)) {
    status(`ほかの区間の線・コントロールラインから${SPLIT_NEAR_M} m の内には足せません`, 'ng'); return;
  }
  const item = { line, name: '' };
  d.items.push(item);
  sortDraft();
  d.sel = d.items.indexOf(item);
  secChanged(true);
}

// 選んだ線をカーソルの所へ動かす
function secMoveToCursor() {
  const d = state.secdraft, v = Chart.cursor();
  if (d.sel < 0) return;
  const ln = v === null ? null : CourseMap.lineAt(v);
  if (!ln) { status('グラフ・地図・動画でカーソルを動かしてから押してください', 'ng'); return; }
  d.items[d.sel].line = { lat: +ln.lat.toFixed(8), lon: +ln.lon.toFixed(8), bearing: +ln.bearing.toFixed(1), width_m: SPLIT_WIDTH_M };
  sortDraft();
  secChanged(true);
}

// 区間 k（0 始まり）の頭の線を消して、前の区間とつなぐ（名前は前の区間のものが残る）
function secJoin(k) {
  const d = state.secdraft;
  if (k < 1 || k > d.items.length) return;
  d.items.splice(k - 1, 1);
  d.sel = -1;
  secChanged(true);
}

async function secSave(btn) {
  const d = state.secdraft;
  btn.disabled = true;
  try {
    const names = [d.head, ...d.items.map((x) => x.name)].map((x) => x.trim());
    const j = await api('sections/set', { course: d.course, splits: d.items.map((x) => x.line), names: d.items.length && names.some((x) => x) ? names : [] });
    if (j.rev !== undefined) state.rev = j.rev;
    if (!j.ok) { status(j.error, 'ng'); return; }
    state.sessions = j.sessions;
    state.secdraft = null;
    status(`区間の線を${d.items.length}本で保存し、周を切り直しました`, 'ok');
    render();
    CourseMap.redraw();
  } catch (e) { status('裏の処理とつながっていません', 'ng'); } finally { btn.disabled = false; }
}

function secCancel() {
  if (state.secdraft && state.secdraft.dirty && !confirm('直した区間の線を保存せずにやめますか？')) return;
  state.secdraft = null;
  CourseMap.redraw();
  updateMarks();
  renderSections();
}

// 地図で区間の線を引いている途中・離したとき
function onSplitMove(i, ln) {
  const d = state.secdraft;
  if (!d || !d.items[i]) return;
  d.items[i].line = { lat: +ln.lat.toFixed(8), lon: +ln.lon.toFixed(8), bearing: +ln.bearing.toFixed(1), width_m: SPLIT_WIDTH_M };
  d.sel = i;
  CourseMap.redraw();
  updateMarks();
}
function onSplit(i, ln) {
  const d = state.secdraft;
  if (!d || !d.items[i]) return;
  if (ln) { onSplitMove(i, ln); sortDraft(); secChanged(true); return; }
  d.sel = d.sel === i ? -1 : i;              // 押しただけ：選ぶ（もう一度押すと外す）
  secChanged(false);
}

// グラフに区間の線を縦に描く（基準の周の上の位置）
function updateMarks() {
  if (!state.overlay) { Chart.setMarks([]); return; }
  Sync.setData(state.overlay);            // 区間の線が変わったら、そろえる所も変わる
  for (const l of state.overlay.laps) l._tx = null;
  $('alignsel').disabled = !splitHeads().length;
  const key = state.x === 'dist' ? 's' : 't';
  const L = mapLines();
  const out = (L && L.splits || []).map((line, i) => {
    const r = CourseMap.refAt(line.lat, line.lon);
    return { x: r ? r[key] : null, label: (L.splitLabels && L.splitLabels[i]) || splitLabel(i, ''), sel: L.splitSel === i };
  });
  Chart.setMarks(out);
  $('zerosec').disabled = !out.length;
  Chart.setZero($('zerosec').checked && out.length > 0);
}

// 直している間の区間の枠
function renderSecEdit(box) {
  const d = state.secdraft;
  const btn = (text, title, fn, dis) => { const b = el('button', { type: 'button', title }, text); b.onclick = () => fn(b); b.disabled = !!dis; return b; };
  const saveB = btn('保存', '区間の線と名前を、使っている線の版（版を使っていなければその場で置いた分）に残し、同じコースの全部の周を切り直します（Ctrl+S でも）', secSave, !d.dirty);
  saveB.id = 'secsave';
  box.append(el('div', { class: 'sechead' }, el('h3', {}, '区間を直す'),
    btn('カーソルの所で切る', 'グラフ・地図・動画で動かしたカーソルの所（基準の周の上）に区間の線を足し、その区間を2つに分けます', secCut),
    btn('選んだ線をカーソルの所へ', '選んだ区間の線を、カーソルの所へ動かします', secMoveToCursor, d.sel < 0),
    btn('選んだ線を消す', '選んだ区間の線を消して、その前後の区間を1つにつなぎます（Delete キーでも）', () => secJoin(d.sel + 1), d.sel < 0),
    el('span', { class: 'grow' }),
    saveB,
    btn('やめる', '直した分を捨てて、元の区間の線に戻します', secCancel)));
  box.append(el('p', { class: 'note' }, '地図の区間の線はつかんで基準の周の線に沿って動かせます。押すと選びます。グラフには縦の破線で出ます。' +
    '名前（「1コーナー」「ハイポイント」など）は区間の表と地図の札に出ます'));
  const len = state.overlay && state.overlay.ref_length_m;
  const bounds = [0, ...d.items.map((x) => refPos(x.line)), len ?? null];
  const body = el('tbody');
  for (let k = 0; k <= d.items.length; k++) {
    const sel = k > 0 && d.sel === k - 1;
    const input = el('input', { type: 'text', maxlength: '30', placeholder: '名前（なくてもよい）', 'aria-label': `区間${k + 1}の名前`, class: 'secname' });
    input.value = k === 0 ? d.head : d.items[k - 1].name;
    input.oninput = () => { if (k === 0) d.head = input.value; else d.items[k - 1].name = input.value; d.dirty = true;
                            saveB.disabled = false; CourseMap.redraw(); updateMarks(); };
    const a = bounds[k], b = bounds[k + 1];
    const row = el('tr', { class: sel ? 'secsel' : '' },
      el('td', { class: 'sec' }, String(k + 1)),
      el('td', {}, input),
      el('td', { class: 'num', title: '基準の周の上での長さ' }, a !== null && b !== null ? `${Math.round(b - a)} m` : ''),
      el('td', { class: 'num diff', title: '区間の頭の線の、基準の周の頭からの距離' }, k === 0 ? 'コントロールライン' : a !== null ? `${Math.round(a)} m から` : ''),
      el('td', {}, k === 0 ? '' : btn(sel ? '選んでいる' : '頭の線を選ぶ', 'この区間の頭の線を選びます（地図とグラフで太く出ます）',
                                      () => { d.sel = sel ? -1 : k - 1; secChanged(false); })),
      el('td', {}, k === 0 ? '' : btn('前とつなぐ', 'この区間の頭の線を消して、前の区間とつなぎます', () => secJoin(k))));
    body.append(row);
  }
  box.append(el('div', { class: 'tablewrap' }, el('table', { class: 'laptable sectable secedit' },
    el('thead', {}, el('tr', {}, el('th', {}, '区間'), el('th', {}, '名前'), el('th', {}, '長さ'), el('th', {}, '頭'), el('th', {}, ''), el('th', {}, ''))), body)));
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
    return el('label', c.derived ? { title: DERIVED_NOTE[c.derived] } : {}, cb, ` ${c.label || name}${c.unit ? `（${c.unit}）` : ''}`);
  };
  for (const [name, c] of meta) if (!c.calc && !c.derived) box.append(item(name, c));
  if ([...meta.values()].some((c) => c.derived)) {
    box.append(el('div', { class: 'calchead', title: 'センサーの値ではなく、記録から計算した値です' }, '測位・G から計算（センサーの値ではない）'));
    for (const [name, c] of meta) if (c.derived) box.append(item(name, c));
  }
  box.append(el('div', { class: 'calchead' }, '計算チャンネル'));
  for (const [name, c] of meta) {
    if (!c.calc) continue;
    box.append(el('div', { class: 'calcrow', title: c.expr }, item(name, c),
      el('button', { type: 'button', class: 'link', onclick: () => openCalc(name) }, '直す')));
  }
  box.append(el('button', { type: 'button', class: 'link', onclick: () => openCalc(null) }, '＋ 計算チャンネルを作る'));
}

// 計算して足したチャンネルの断り書き（SP-10-14）
const DERIVED_NOTE = {
  gnss: '測位の位置を0.5秒でならして微分した値です（センサーの値ではありません）。旋回半径は、ほぼまっすぐな所（600 m より大きい）と 10 km/h より遅い所では出しません',
  grip: '摩擦円の使い方：その所の合成Gを、その走行の同じ向きの合成Gの 99%（包絡）で割った値です（センサーの値ではありません）。100% を超える所もあります。10 km/h より遅い所は出しません',
};

function setChannels(list) {
  state.channels = list;
  try { localStorage.setItem('channels', JSON.stringify(state.channels)); } catch (e) { /* 覚えなくてよい */ }
  if (state.lapView === 'val') renderTable();  // 周の表の「値」の列も変わる
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
  $('decdot').checked = p.dotted; $('accring').checked = p.acc; $('markson').checked = p.marks; $('curinfo').checked = p.curinfo !== false;
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
           dotted: p.dotted, acc: p.acc, marks: p.marks, markLabel: mc ? mc.label || mc.name : p.markCh, markUnit: mc ? mc.unit : '',
           curinfo: p.curinfo !== false };
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
  saveLast();
  const picks = pickedLaps();
  const empty = !picks.length;
  $('graphEmpty').hidden = !empty;
  $('mapEmpty').hidden = !empty;
  if (empty) {
    Chart.clear(); CourseMap.clear(); VideoPane.clear(); GCircle.clear(); $('gEmpty').hidden = false; state.overlay = null; state.refSession = null; showVideo(0);
    $('legend').textContent = ''; renderRefList([]); $('chanlist').textContent = ''; renderLineControls(); renderSections(); renderShiftControls(); return;
  }
  const meta = channelMeta(picks);
  state.meta = meta;
  renderChannelList(meta);
  renderRefList(picks);
  renderPaintControls(meta);
  const ref = refIndex(picks);
  const req = ++state.req;
  let j;
  try {
    j = await api('overlay', { laps: picks.map((p) => ({ id: p.id, lap: p.lap })), mode: state.mode,
                               channels: state.channels.filter((n) => meta.has(n)), ref,
                               paint: state.paint.mode === 'value' ? state.paint.ch : null, mark: state.paint.markCh,
                               gsrc: state.gsrc });
  } catch (e) { status('裏の処理とつながっていません', 'ng'); return; }
  if (req !== state.req) return;                  // 新しい頼みが後から出ている
  if (!j.ok) { status(j.error, 'ng'); return; }
  const x = j.distance ? state.xaxis : 'time';
  renderLegend(j);
  state.overlay = j; state.x = x;
  const refKey = j.laps[j.ref] && j.laps[j.ref].key;
  state.refSession = state.sessions.find((s) => refKey && refKey.startsWith(s.id + ':')) || null;
  CourseMap.show(j, true);              // 先に地図へ渡す（区間の頭の位置を基準の周の線から出すため）
  Sync.setData(j);
  Chart.show($('plots'), j, { x, channels: state.channels.filter((n) => meta.has(n)).map((n) => meta.get(n)), color: colorFor, offset: $('offline').checked,
                              shift: x === 'time' && state.align === 'section' ? (lap, t) => Sync.xOf(lap, t) : null,
                              lapTime: (lap, v) => Sync.lapTime(lap, v, 'axis'),
                              onCursor: (v, kind) => { if (kind === 'pin') stopCursorPlay(); setCursors(v); VideoPane.cursor(v, kind); } }, true);
  GCircle.show(j);
  $('gEmpty').hidden = j.laps.some((l) => l.g);
  setCursors(Chart.cursor());
  loadOutline();
  showAutoRange();
  showVideo(VideoPane.show(j));
  VideoPane.cursor(Chart.cursor(), 'pin');
  renderLineControls();
  renderSections();
  renderShiftControls();
  updateMarks();
}

// 基準の周の番号（選んだ周の並びの中。決めていなければ null＝一番速い周）
function refIndex(picks) {
  const i = state.ref ? picks.findIndex((p) => p.key === state.ref) : -1;
  return i < 0 ? null : i;
}

// ---------------------------------------------------------------- 走行の線の平行移動（SP-10-17。仕様 3-6）
// 「ずらす」で走行を選ぶと、矢印キーで 0.1 m（Shift で 1 m）ずつ線を動かす（北が上）。押し終わって少ししてから裏へ送り、
// 重ね合わせを頼み直す。「基準に合わせる」は、その走行の選んだ周の線を基準の周の線へ横のずれが最小になるよう動かす
function shiftOf(sid) {
  if (state.shiftWant && state.shiftWant.id === sid) return [state.shiftWant.e, state.shiftWant.n];
  const l = state.overlay && state.overlay.laps.find((x) => x.key.startsWith(sid + ':'));
  return l && l.shift ? l.shift : [0, 0];
}
function fmtShift(v) { return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(2)}`; }
function renderShiftControls() {
  const sel = $('shiftsel'), j = state.overlay;
  const ids = j ? [...new Set(j.laps.map((l) => l.key.split(':')[0]))] : [];
  if (state.shiftSid && !ids.includes(state.shiftSid)) state.shiftSid = null;
  sel.textContent = '';
  sel.append(el('option', { value: '' }, '動かさない'));
  for (const id of ids) {
    const s = state.sessions.find((x) => x.id === id), [e, n] = shiftOf(id);
    sel.append(el('option', { value: id }, (s ? s.title : id) + (e || n ? `（東 ${fmtShift(e)}・北 ${fmtShift(n)} m）` : '')));
  }
  sel.value = state.shiftSid || '';
  sel.disabled = !j || !j.distance;
  const on = !!state.shiftSid, [e, n] = on ? shiftOf(state.shiftSid) : [0, 0];
  $('shiftval').hidden = $('shiftauto').hidden = $('shiftzero').hidden = !on;
  $('shiftval').textContent = `東 ${fmtShift(e)} m・北 ${fmtShift(n)} m`;
  const isRef = on && state.refSession && state.refSession.id === state.shiftSid;
  $('shiftauto').disabled = isRef;
  $('shiftauto').title = isRef ? '基準の周の走行は、ほかの走行を合わせる相手なので自動では動かせません（矢印キーなら動かせます）'
    : '選んだこの走行の周の線を、基準の周の線へ、横のずれが一番小さくなるように平行に動かします（走る線が違う所も平均に入るので、航空写真の上でも確かめてください）';
  $('shiftzero').disabled = !e && !n;
}
let shiftTimer = 0;
async function sendShift() {
  const w = state.shiftWant;
  if (!w) return;
  let j;
  try { j = await api('shift/set', { id: w.id, e: w.e, n: w.n }); } catch (err) { status('裏の処理とつながっていません', 'ng'); return; }
  if (state.shiftWant === w) state.shiftWant = null;
  if (!j.ok) { status(j.error, 'ng'); renderShiftControls(); return; }
  state.rev = j.rev;
  await renderPicked();
}
function nudgeShift(de, dn) {
  const id = state.shiftSid;
  if (!id) return;
  const [e, n] = shiftOf(id);
  state.shiftWant = { id, e: Math.round((e + de) * 100) / 100, n: Math.round((n + dn) * 100) / 100 };
  renderShiftControls();
  clearTimeout(shiftTimer);
  shiftTimer = setTimeout(sendShift, 250);
}
$('shiftsel').onchange = (e) => {
  state.shiftSid = e.target.value || null;
  e.target.blur();                    // 矢印キーを選ぶ欄でなく線へ
  renderShiftControls();
  if (state.shiftSid) status('矢印キーで線を 0.1 m、Shift を押しながらで 1 m 動かします（北が上）', 'ok');
};
$('shiftzero').onclick = () => {
  if (!state.shiftSid) return;
  clearTimeout(shiftTimer);
  state.shiftWant = { id: state.shiftSid, e: 0, n: 0 };
  sendShift();
};
$('shiftauto').onclick = async () => {
  const id = state.shiftSid, picks = pickedLaps();
  if (!id || !picks.length) return;
  clearTimeout(shiftTimer);
  if (state.shiftWant) await sendShift();
  let j;
  try { j = await api('shift/auto', { id, laps: picks.map((p) => ({ id: p.id, lap: p.lap })), mode: state.mode, ref: refIndex(picks) }); }
  catch (err) { status('裏の処理とつながっていません', 'ng'); return; }
  if (!j.ok) { status(j.error, 'ng'); return; }
  state.rev = j.rev;
  status(`基準の周の線へ合わせました：東 ${fmtShift(j.shift[0])} m・北 ${fmtShift(j.shift[1])} m（${j.laps}周の、基準の線からの横のずれの中央 ${j.before.toFixed(2)} → ${j.after.toFixed(2)} m）`, 'ok');
  await renderPicked();
};
document.addEventListener('keydown', (e) => {
  if (!state.shiftSid || state.outedit || state.draft || state.secdraft || document.querySelector('dialog[open]')) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable || (t.classList && t.classList.contains('gutter')))) return;
  const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[e.key];
  if (!d) return;
  e.preventDefault();
  const step = e.shiftKey ? 1 : 0.1;
  nudgeShift(d[0] * step, d[1] * step);
});

// コース図（道路の縁）：基準の周の走行のコースで頼む。コースごとに裏が残すので、2回目からはすぐ出る
async function loadOutline() {
  const s = state.refSession;
  if (state.outedit && (!s || s.id !== state.outedit.sid || !$('outline').checked)) {
    stopOutlineEdit();
    status('基準の走行が変わったので、コース図を直すのをやめました（保存していない分は消えました）', 'ng');
  }
  if (!$('outline').checked || !s) { CourseMap.setOutline(null); renderOutlineControls(); return; }
  if (state.outedit && state.overlay && state.overlay.origin && state.outedit.origin.join() !== state.overlay.origin.join()) {
    // 選んだ周が変わって図の原点が動いた：直している中身を新しい原点へ移す
    const [la0, lo0] = state.outedit.origin, M = 111320.0, c = M * Math.cos(la0 * Math.PI / 180);
    OutlineEditor.remap((x, y) => CourseMap.toXY(la0 + y / M, lo0 + x / c));
    state.outedit.origin = state.overlay.origin.slice();
  }
  if (state.outlineFor === s.id && state.outlineData) { CourseMap.setOutline(state.outlineData); renderOutlineControls(); return; }
  if (state.outlineFor === s.id) return;            // 作っている
  const id = s.id;
  state.outlineFor = id; state.outlineData = null;
  CourseMap.setOutline(null, 'コース図を作っています…（初めてのコースは数秒〜十数秒）');
  renderOutlineControls();
  let j;
  try { j = await api('outline', { id }); } catch (e) { j = { ok: false, error: '裏の処理とつながっていません' }; }
  if (state.outlineFor !== id) return;              // そのあいだに基準の走行が変わった
  if (!j.ok) { state.outlineFor = null; CourseMap.setOutline(null, `コース図を作れません（${j.error}）`); return; }
  state.outlineData = j.outline;
  renderOutlineControls();
  if (!$('outline').checked) { CourseMap.setOutline(null); return; }
  CourseMap.setOutline(j.outline, j.outline.complete ? null : 'コース図：読めなかった地図・写真があります（ネットにつながると作り直します）');
  if (!j.outline.complete) state.outlineFor = null;   // 次に描くときに頼み直す
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
  status(`計算チャンネル「${name}」を保存しました`, 'ok');
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
  sel.disabled = $('editline').disabled = !s || !s.course || !state.overlay || !state.overlay.origin || !!state.outedit;
  $('delline').hidden = !s || !s.line_name || !!state.draft;
  if (!s) return;
  sel.append(el('option', { value: '' }, '書き出しの線（ロガーと同じ）'));
  for (const n of s.line_names) sel.append(el('option', { value: n }, n));
  sel.value = s.line_name || '';
}

function mapLines() {
  if (state.draft) return { line: state.draft.line, sectors: state.draft.sectors, splits: [], selected: state.draft.selected, draft: true };
  const d = state.secdraft;
  if (d && state.refSession) {
    return { line: state.refSession.line, sectors: state.refSession.sectors, selected: null, draft: false, splitEdit: true, splitSel: d.sel,
             splits: d.items.map((x) => x.line), splitLabels: d.items.map((x, i) => splitLabel(i, x.name)) };
  }
  const s = state.refSession;
  return s ? { line: s.line, sectors: s.sectors, splits: state.mode === 'app' ? s.splits || [] : [], selected: null, draft: false,
               splitLabels: (s.splits || []).map((_, i) => splitLabel(i, (s.split_names || [])[i + 1])) } : null;
}

function renderDraft() {
  const d = state.draft, box = $('lineedit');
  box.hidden = !d;
  document.querySelector('.map').classList.toggle('editing', !!d);
  $('editline').disabled = !!d || $('linesel').disabled;
  $('delline').hidden = true;
  renderOutlineControls();
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
  if (state.secdraft) { status('区間を直している間は、線を直せません（区間の「保存」か「やめる」を先に）', 'ng'); return; }
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
    status((action === 'save' ? `線の版「${body.name}」を保存し、周を切り直しました` : '線を切り替え、周を切り直しました') +
      '（動画の位置も新しい周の頭に合わせ直しました）', 'ok');
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
    try { state.sessions = (await api('sessions')).sessions || []; }        // 同じコースの理論ベストなどを読み直す
    catch (e) { if (!state.sessions.some((s) => s.id === j.session.id)) state.sessions.push(j.session); }
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

// ---------------------------------------------------------------- 前回の続き
// 開いている走行と選んでいる周を、変わるたびに裏へ覚えさせる（何も開いていないときは、前の分を残す）。
// 次に開いたとき「続きから」で、同じ走行を開き直して同じ周を選ぶ
const baseName = (p) => p.split(/[\\/]/).pop().replace(/\.ecorun$/i, '');
let lastTimer = 0;
function saveLast() {
  if (state.resuming || !state.sessions.length) return;
  clearTimeout(lastTimer);
  lastTimer = setTimeout(async () => {
    const byId = new Map(state.sessions.map((s) => [s.id, s.path]));
    const laps = [...state.picked].map((k) => { const i = k.lastIndexOf(':'); return [byId.get(k.slice(0, i)), Number(k.slice(i + 1))]; })
      .filter(([p, n]) => p && Number.isInteger(n));
    state.last = { paths: state.sessions.map((s) => s.path), laps };
    renderResume();
    try { await api('last', state.last); } catch (e) { /* 覚えられなくてもよい */ }
  }, 400);
}

function renderResume() {
  const last = state.last || { paths: [], laps: [] };
  const todo = last.paths.filter((p) => !state.sessions.some((s) => s.path === p));
  const b = $('resume');
  b.hidden = !todo.length || !!state.resuming;
  $('open').textContent = b.hidden ? '走行を開く…' : '別の走行…';
  if (b.hidden) return;
  const names = last.paths.map(baseName);
  b.textContent = `続きから：${names[0]}${names.length > 1 ? ` ほか${names.length - 1}` : ''}`;
  b.title = `前回開いていた走行を開き直し、選んでいた周（${last.laps.length}周）を選びます\n${last.paths.join('\n')}`;
}

async function loadLast() {
  try { const j = await api('last'); state.last = j.last || null; } catch (e) { state.last = null; }
  renderResume();
}

async function resume() {
  const last = state.last;
  if (!last || !last.paths.length) return;
  state.resuming = true;            // 開き直している途中の形を、前回の分として覚えないように
  renderResume();
  try {
    for (const p of last.paths) if (!state.sessions.some((s) => s.path === p)) await openPath(p);
    for (const [p, n] of last.laps) {
      const s = state.sessions.find((x) => x.path === p);
      if (s && state.picked.size < MAX_PICK && lapsOf(s).some((x) => x.lap === n)) state.picked.add(`${s.id}:${n}`);
    }
  } finally {
    state.resuming = false;
  }
  render();
  if (state.sessions.length) status(`前回の続きを開きました（走行 ${last.paths.filter((p) => state.sessions.some((s) => s.path === p)).length}・周 ${state.picked.size}）`, 'ok');
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
    await loadLast();               // 何も開いていないときの案内を「続きから」に合わせるため、先に読む
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

// ---------------------------------------------------------------- コース図を直す（outedit.js）
function renderOutlineControls() {
  const o = state.outlineData, e = state.outedit;
  $('editoutline').disabled = !!e || !!state.draft || !o || !$('outline').checked || !state.refSession ||
                              state.outlineFor !== (state.refSession && state.refSession.id) || !CourseMap.ready();
  $('editoutline').textContent = o && o.edited ? 'コース図を直す（直したもの）' : 'コース図を直す';
  $('outedit').hidden = !e;
  document.querySelector('.map').classList.toggle('editing', !!(e || state.draft));
  if (!e) return;
  const t = OutlineEditor.tool();
  for (const b of document.querySelectorAll('#outedit [data-tool]')) b.classList.toggle('on', b.dataset.tool === t);
  $('oehelp').textContent = `${OutlineEditor.TOOLS[t] || ''}　｜　道具は図の上で右ボタンを押したまま引く向きか、1〜9・0 のキーでも切り替わります（9 はなぞる、0 は線の種類）。Ctrl+S 保存・Ctrl+Z 戻す・Ctrl+Y やり直す・Esc 押しかけを取り消す`;
  $('oeundo').disabled = !OutlineEditor.canUndo();
  $('oeredo').disabled = !OutlineEditor.canRedo();
  $('oesave').disabled = !OutlineEditor.dirty();
  $('oereset').hidden = !(o && o.edited);
}

function startOutlineEdit() {
  const s = state.refSession, o = state.outlineData;
  if (!s || !o || state.draft) return;
  if (state.secdraft) { status('区間を直している間は、コース図を直せません（区間の「保存」か「やめる」を先に）', 'ng'); return; }
  OutlineEditor.load(o, CourseMap.toXY, {
    radius: () => { const v = +$('oeradius').value; return isFinite(v) ? Math.min(60, Math.max(0, v)) : 10; },
    zebraW: () => { const v = +$('oezebraw').value; return isFinite(v) && v >= 0.2 && v <= 5 ? v : 1; },
    zebraTaper: () => { const v = +$('oezebrataper').value; return isFinite(v) ? Math.min(20, Math.max(0, v)) : 2; },
    boardText: () => $('oeboard').value.trim().slice(0, 12),
    lineKind: () => $('oekind').value,
    onChange: renderOutlineControls,
    // なぞった線（m）を裏で縁へ吸い付かせる。返す点も m
    snap: async (pts, inside, reach) => {
      let j;
      try { j = await api('outline/snap', { pts: pts.map((q) => CourseMap.toLatLon(q[0], q[1])), inside, reach }); } catch (e) { throw new Error('裏の処理とつながっていません'); }
      if (!j.ok) throw new Error(j.error);
      return { pts: j.pts.map((q) => CourseMap.toXY(q[0], q[1])), found: j.found, shift_m: j.shift_m };
    },
    onTrace: (msg, ok) => { status(msg, ok ? 'ok' : 'ng'); CourseMap.redraw(); },
  });
  state.outedit = { sid: s.id, origin: state.overlay.origin.slice() };
  CourseMap.setEditor(OutlineEditor);
  renderOutlineControls();
  renderLineControls();
  status(o.edited ? '前に直したコース図を開きました' : '自動で作ったコース図を開きました。直したら「保存」（Ctrl+S）を押してください', 'ok');
}

function stopOutlineEdit() {
  OutlineEditor.stop();
  state.outedit = null;
  CourseMap.setEditor(null);
  renderOutlineControls();
  renderLineControls();
}

async function outlineRequest(action, body) {
  let j;
  try { j = await api('outline/' + action, body); } catch (e) { j = { ok: false, error: '裏の処理とつながっていません' }; }
  if (!j.ok) { status(`コース図を${action === 'save' ? '保存できません' : '戻せません'}（${j.error}）`, 'ng'); return false; }
  state.outlineData = j.outline;
  state.outlineFor = state.refSession && state.refSession.id;
  stopOutlineEdit();
  CourseMap.setOutline(j.outline);
  return true;
}

$('editoutline').onclick = startOutlineEdit;
function setOutlineTool(t) { OutlineEditor.setTool(t); renderOutlineControls(); CourseMap.redraw(); }
for (const b of document.querySelectorAll('#outedit [data-tool]')) b.onclick = () => setOutlineTool(b.dataset.tool);
$('oeundo').onclick = () => { if (OutlineEditor.undo()) CourseMap.redraw(); };
$('oeredo').onclick = () => { if (OutlineEditor.redo()) CourseMap.redraw(); };
$('oecancel').onclick = () => {
  if (OutlineEditor.dirty() && !confirm('直した分を保存せずにやめますか？')) return;
  stopOutlineEdit();
  CourseMap.setOutline(state.outlineData);
};
$('oesave').onclick = async () => {
  const s = state.refSession;
  if (!s || !state.outedit) return;
  if (await outlineRequest('save', { id: s.id, outline: OutlineEditor.dump(CourseMap.toLatLon) })) status('直したコース図を保存しました（このコースでは次からこちらが出ます）', 'ok');
};
// ピットロードを足す（SP-10-36）：直している途中のコースの縁を裏へ送り、その外を通ったピットの通過から作った線を足す
$('oepit').onclick = async () => {
  const s = state.refSession;
  if (!s || !state.outedit) return;
  const had = OutlineEditor.linesOf('pit').length;
  const replace = had > 0 && confirm(`前からあるピットロードの線が${had}本あります。消して置き換えますか？（キャンセルなら残したまま足します）`);
  const btn = $('oepit');
  btn.disabled = true;
  status('ピットに入って出た通過から、ピットロードを作っています（十数秒かかります）…', 'ok');
  let j;
  try {
    j = await api('outline/pit', { id: s.id, edges: OutlineEditor.linesOf('edge').map((pts) => pts.map((q) => CourseMap.toLatLon(q[0], q[1]))) });
  } catch (e) { j = { ok: false, error: '裏の処理とつながっていません' }; }
  btn.disabled = false;
  if (!state.outedit) return;
  if (!j.ok) { status(`ピットロードを作れません（${j.error}）`, 'ng'); return; }
  const n = OutlineEditor.addLines(j.pit.lines.map((ln) => ln.map((q) => CourseMap.toXY(q[0], q[1]))), 'pit', replace);
  CourseMap.redraw();
  status(`ピットロードの縁を${n}本足しました（ピットを通った${j.pit.passes}回から。写真の境目が見つかった所 ${Math.round(j.pit.found * 100)} %）。` +
         `コースの縁と重なる所は描いていません。よければ「保存」を押してください`, 'ok');
};
$('oereset').onclick = async () => {
  const s = state.refSession;
  if (!s || !confirm('手で直したコース図を消して、自動で作ったものに戻しますか？（消した分は戻せません）')) return;
  if (await outlineRequest('reset', { id: s.id })) status('自動で作ったコース図に戻しました', 'ok');
};
// ショートカット。Ctrl+S：保存（コース図・線の版・計算チャンネル。画面そのものを保存する窓は出さない）。
// コース図を直しているとき：Ctrl+Z 戻す、Ctrl+Y・Ctrl+Shift+Z やり直す、1〜8 道具（右ボタンのジェスチャーと同じ順）、9 なぞる、0 線の種類、Esc 押しかけを取り消す。
// 字を打つ欄の中では、Ctrl+S のほかはその欄に任せる
document.addEventListener('keydown', (e) => {
  const ctrl = e.ctrlKey || e.metaKey, key = e.key.toLowerCase(), t = e.target;
  const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
  if (ctrl && !e.altKey && key === 's') {
    e.preventDefault();
    if ($('calcdlg').open) saveCalc();
    else if (state.outedit) { if (!$('oesave').disabled) $('oesave').click(); }
    else if (state.draft) $('saveline').click();
    else if (state.secdraft && $('secsave') && !$('secsave').disabled) $('secsave').click();
    return;
  }
  if (state.secdraft && !typing) {           // 区間を直しているとき：Delete で選んだ線を消す、Esc で選ぶのをやめる
    if ((e.key === 'Delete' || e.key === 'Backspace') && state.secdraft.sel >= 0) { e.preventDefault(); secJoin(state.secdraft.sel + 1); }
    else if (e.key === 'Escape' && state.secdraft.sel >= 0) { state.secdraft.sel = -1; secChanged(false); }
    return;
  }
  if (!state.outedit || typing) return;
  if (ctrl && key === 'z' && !e.shiftKey) { e.preventDefault(); if (OutlineEditor.undo()) CourseMap.redraw(); }
  else if (ctrl && (key === 'y' || (key === 'z' && e.shiftKey))) { e.preventDefault(); if (OutlineEditor.redo()) CourseMap.redraw(); }
  else if (!ctrl && !e.altKey && /^[1-8]$/.test(e.key)) { e.preventDefault(); setOutlineTool(OutlineEditor.GESTURE[+e.key - 1][0]); }
  else if (!ctrl && !e.altKey && e.key === '9') { e.preventDefault(); setOutlineTool('trace'); }   // なぞる（ジェスチャーの8つの向きには入れていない）
  else if (!ctrl && !e.altKey && e.key === '0') { e.preventDefault(); setOutlineTool('kind'); }    // 線の種類
  else if (e.key === 'Escape' && OutlineEditor.cancelPending()) CourseMap.redraw();
});
// 保存していない直しがあるまま閉じようとしたら聞く
window.addEventListener('beforeunload', (e) => {
  if ((state.outedit && OutlineEditor.dirty()) || (state.secdraft && state.secdraft.dirty)) { e.preventDefault(); e.returnValue = ''; }
});

$('open').onclick = pickAndOpen;
$('resume').onclick = resume;
$('recent').onchange = (e) => { const p = e.target.value; e.target.value = ''; if (p) openPath(p); };
for (const r of document.querySelectorAll('input[name=mode]')) r.onchange = () => { state.mode = r.value; render(); };
for (const r of document.querySelectorAll('input[name=lapview]')) {
  r.onchange = () => {
    state.lapView = r.value;
    try { localStorage.setItem('lapview', state.lapView); } catch (e) { /* 覚えなくてよい */ }
    renderLapView(); renderTable();
  };
}
$('lapstat').onchange = () => {
  state.lapStat = Number($('lapstat').value);
  try { localStorage.setItem('lapstat', String(state.lapStat)); } catch (e) { /* 覚えなくてよい */ }
  renderTable();
};
renderLapView();
for (const r of document.querySelectorAll('input[name=xaxis]')) r.onchange = () => { state.xaxis = r.value; renderPicked(); };
$('refsel').onchange = (e) => { state.ref = e.target.value; renderPicked(); };
// 区間の頭で差を0にするか（ふだんは切。選び直しは覚える）
try { $('zerosec').checked = localStorage.getItem('zerosec') === '1'; } catch (e) { $('zerosec').checked = false; }
// そろえる所・点の置き方（SP-10-16。選び直しは覚える）
$('alignsel').value = state.align;
$('alignsel').onchange = (e) => {
  state.align = e.target.value === 'section' ? 'section' : 'lap';
  try { localStorage.setItem('align', state.align); } catch (er) { /* 覚えなくてよい */ }
  renderPicked();
};
$('placesel').value = state.place;
$('placesel').onchange = (e) => {
  state.place = ['space', 'time'].includes(e.target.value) ? e.target.value : 'axis';
  try { localStorage.setItem('place', state.place); } catch (er) { /* 覚えなくてよい */ }
  CourseMap.redraw();
};
// 線のずれの段（SP-10-15。ふだんは切。選び直しは覚える）
try { $('offline').checked = localStorage.getItem('offline') === '1'; } catch (e) { $('offline').checked = false; }
$('offline').onchange = () => { try { localStorage.setItem('offline', $('offline').checked ? '1' : '0'); } catch (e) { /* 覚えなくてよい */ } renderPicked(); };
$('zerosec').onchange = () => { try { localStorage.setItem('zerosec', $('zerosec').checked ? '1' : '0'); } catch (e) { /* 覚えなくてよい */ } updateMarks(); };
new ResizeObserver(() => Chart.redraw()).observe($('plots'));
CourseMap.init($('mapcv'), {
  x: () => state.x, color: colorFor, lines: mapLines, editing: () => !!state.draft,
  onPick: (v) => { stopCursorPlay(); Chart.pin(v); setCursors(v); VideoPane.cursor(v, 'pin'); },
  onPlace: placeLine, photo: () => $('photo').checked, paint: paintForMap,
  onFollow: (on) => { $('follow').checked = on; saveFollow(); },
  onTool: (t) => setOutlineTool(t),
  splitEditing: () => !!state.secdraft, onSplitMove, onSplit,
  lapTime: (lap, x) => Sync.lapTime(lap, x, state.place),
});
// 車を追うか（ふだんは追わない。図を手で引くと外れる。選び直しは覚える）
function saveFollow() { try { localStorage.setItem('follow', $('follow').checked ? '1' : '0'); } catch (e) { /* 覚えなくてよい */ } }
try { $('follow').checked = localStorage.getItem('follow') === '1'; } catch (e) { $('follow').checked = false; }
CourseMap.setFollow($('follow').checked);
$('follow').onchange = () => { saveFollow(); CourseMap.setFollow($('follow').checked); };
$('paintsel').onchange = (e) => { state.paint.mode = e.target.value; paintChanged(); };
$('paintch').onchange = (e) => { state.paint.ch = e.target.value; paintChanged(); };
$('markch').onchange = (e) => { state.paint.markCh = e.target.value; paintChanged(); };
$('decdot').onchange = (e) => { state.paint.dotted = e.target.checked; paintChanged(); };
$('accring').onchange = (e) => { state.paint.acc = e.target.checked; paintChanged(); };
$('markson').onchange = (e) => { state.paint.marks = e.target.checked; paintChanged(); };
$('curinfo').onchange = (e) => { state.paint.curinfo = e.target.checked; paintChanged(); };
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
// コース図を描くか（ふだんは描く。選び直しは覚える）
try { $('outline').checked = localStorage.getItem('outline') !== '0'; } catch (e) { $('outline').checked = true; }
$('outline').onchange = () => { try { localStorage.setItem('outline', $('outline').checked ? '1' : '0'); } catch (e) { /* 覚えなくてよい */ } loadOutline(); };
new ResizeObserver(() => CourseMap.redraw()).observe($('mapcv'));
Sync.init({ x: () => state.x, align: () => state.align, heads: splitHeads });
VideoPane.init($('vbox'), {
  x: () => state.x, color: colorFor, rate: () => +$('vrate').value,
  onTime: (x) => { Chart.pin(x); setCursors(x); },
  onPlayState: (on) => { $('vplay').textContent = on ? '⏸ 止める' : '▶ 再生'; },
});
$('vplay').onclick = () => VideoPane.toggle();
GCircle.init($('gcv'), $('ginfo'), $('gnote'), { x: () => state.x, color: colorFor });
$('gsrcsel').value = state.gsrc;
$('gsrcsel').onchange = (e) => {
  state.gsrc = e.target.value === 'gnss' ? 'gnss' : 'imu';
  try { localStorage.setItem('gsrc', state.gsrc); } catch (er) { /* 覚えなくてよい */ }
  renderPicked();
};
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
$('editline').onclick = () => { if (!state.outedit) startEdit(); };
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
