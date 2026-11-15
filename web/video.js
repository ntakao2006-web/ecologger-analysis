// 動画（2周を上下に並べる）。動画の秒は、書き出しの sync（[動画の秒, 周の頭からの秒]）から補って出す。手で合わせる所は無い。
// 止まっているとき：グラフ・コース図のカーソルに合わせて、2本ともその所（距離か時間）へ動かす。
// 再生するとき：上の動画が先に立ち、その秒からカーソルを動かす。下の動画は同じ所（距離なら同じ地点）を追う。
'use strict';

const VideoPane = (() => {
  const DRIFT_S = 0.3, FPS = 15;
  let opts = null, laps = [], slots = [], playing = false, raf = 0, lastTick = 0, lastX = null;

  // 増える一方の xa で x のときの ya（範囲の外は null）
  function interp(xa, ya, x) {
    const n = xa.length;
    if (!n || x === null || x < xa[0] || x > xa[n - 1]) return null;
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xa[m] <= x) lo = m; else hi = m; }
    const d = xa[hi] - xa[lo];
    return d > 0 ? ya[lo] + (ya[hi] - ya[lo]) * (x - xa[lo]) / d : ya[lo];
  }
  // null の点を抜いた [xa, ya]（xa は増える一方にそろえる）
  function pairs(xa, ya) {
    const a = [], b = [];
    for (let i = 0; i < xa.length; i++) {
      if (xa[i] === null || ya[i] === null) continue;
      if (a.length && xa[i] <= a[a.length - 1]) continue;
      a.push(xa[i]); b.push(ya[i]);
    }
    return [a, b];
  }

  // 横軸の値 x → その周の頭からの秒。距離なら、その周の走った線で同じ地点を通った時刻
  function lapTimeAt(lap, x) {
    if (x === null || x === undefined) return null;
    if (opts.x() !== 'dist') return x >= 0 && x <= lap.time_s ? x : null;
    return lap._s2t ? interp(lap._s2t[0], lap._s2t[1], x) : null;
  }
  function xAt(lap, t) {
    if (opts.x() !== 'dist') return t;
    return lap._t2s ? interp(lap._t2s[0], lap._t2s[1], t) : null;
  }
  function segAt(lap, t) {
    if (t === null) return null;
    return (lap.video || []).find((v) => t >= v.from - 1e-3 && t <= v.to + 1e-3) || null;
  }
  function videoSec(seg, t) { return interp(seg._t, seg._v, t); }
  function lapSec(seg, v) { return interp(seg._v, seg._t, v); }

  // ---------------------------------------------------------------- 1本ぶん
  function makeSlot(box, name, sound) {
    const sel = document.createElement('select');
    sel.setAttribute('aria-label', `${name}の動画の周`);
    const snd = document.createElement('label');
    snd.className = 'vsound';
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = sound;
    snd.append(cb, ' 音');
    const sw = document.createElement('i');
    sw.className = 'vsw';
    const pos = document.createElement('span');
    pos.className = 'vpos';
    const head = document.createElement('div');
    head.className = 'vhead';
    head.append(sw, sel, pos, snd);
    const video = document.createElement('video');
    video.preload = 'auto'; video.playsInline = true; video.muted = !sound;
    const miss = document.createElement('div');
    miss.className = 'vmiss'; miss.hidden = true;
    const frame = document.createElement('div');
    frame.className = 'vframe';
    frame.append(video, miss);
    const wrap = document.createElement('div');
    wrap.className = 'vslot';
    wrap.append(head, frame);
    box.append(wrap);
    const slot = { name, sel, cb, sw, pos, video, miss, wrap, key: null, url: null, seg: null, busy: false, want: null };
    cb.onchange = () => { video.muted = !cb.checked; };
    sel.onchange = () => { slot.key = sel.value || null; slot.url = null; refresh(); seekAll(lastX); };
    video.addEventListener('loadedmetadata', () => { slot.busy = false; applyWant(slot); });
    video.addEventListener('seeked', () => { slot.busy = false; applyWant(slot); });
    video.addEventListener('error', () => { slot.busy = false; showMiss(slot, '動画を読めません'); });
    return slot;
  }

  function lapOf(slot) { return laps.find((l) => l.key === slot.key) || null; }

  function showMiss(slot, text) {
    slot.miss.hidden = !text;
    slot.miss.textContent = text || '';
    slot.video.style.visibility = text ? 'hidden' : 'visible';
  }

  // 動画の秒へ動かす。読み込み中・動かしている途中なら、最後の頼みだけを覚えておき、終わったら動かす
  function setTime(slot, seg, sec) {
    if (slot.url !== seg.url) {
      slot.url = seg.url; slot.seg = seg; slot.busy = true; slot.want = sec;
      slot.video.src = seg.url;
      return;
    }
    slot.seg = seg;
    if (slot.busy) { slot.want = sec; return; }
    if (Math.abs(slot.video.currentTime - sec) < 0.02) return;
    slot.busy = true; slot.want = null;
    slot.video.currentTime = sec;
  }
  function applyWant(slot) {
    if (slot.want === null || slot.busy) return;
    const s = slot.want; slot.want = null;
    if (Math.abs(slot.video.currentTime - s) < 0.02) return;
    slot.busy = true;
    slot.video.currentTime = s;
  }

  // 横軸の値 x の所へ1本を動かす。動画が無ければ「無い」と出す
  function seekSlot(slot, x) {
    const lap = lapOf(slot);
    if (!lap) return null;
    // カーソルがまだ無ければ、その周の動画の頭を出しておく
    const t = x === null ? ((lap.video || [])[0] || { from: null }).from : lapTimeAt(lap, x);
    slot.pos.textContent = t === null ? '' : `周の頭から ${t.toFixed(2)}秒`;
    if (t === null) { showMiss(slot, opts.x() === 'dist' ? 'この地点はこの周の測位から外れています' : 'この周の外です'); return null; }
    const seg = segAt(lap, t);
    if (!seg) { showMiss(slot, 'この所の動画はありません'); return null; }
    const sec = videoSec(seg, t);
    if (sec === null) { showMiss(slot, 'この所の動画はありません'); return null; }
    showMiss(slot, null);
    setTime(slot, seg, sec);
    return sec;
  }

  function seekAll(x) {
    lastX = x;
    for (const s of slots) seekSlot(s, x);
  }

  // ---------------------------------------------------------------- 再生
  function tick(now) {
    raf = 0;
    if (!playing) return;
    const [a, b] = slots;
    const lapA = lapOf(a);
    if (!lapA || !a.seg || a.video.paused || a.video.ended) {
      // 動画の切れ目なら、次の動画へ移って続ける
      if (lapA && a.seg && (a.video.ended || a.video.currentTime >= a.seg._v[a.seg._v.length - 1] - 0.05)) {
        const next = (lapA.video || []).find((v) => v.from > a.seg.from);
        if (next) { setTime(a, next, videoSec(next, next.from)); a.video.play().catch(() => {}); raf = requestAnimationFrame(tick); return; }
      }
      if (!a.video.paused || a.busy) { raf = requestAnimationFrame(tick); return; }
      stop(); return;
    }
    const t = lapSec(a.seg, a.video.currentTime);
    if (t === null || t >= lapA.time_s) {
      const xe = xAt(lapA, lapA.time_s);         // 周の終わりで止める。カーソルも終わりへ
      if (t !== null && xe !== null) { lastX = xe; opts.onTime(xe); }
      stop(); return;
    }
    const x = xAt(lapA, t);
    a.pos.textContent = `周の頭から ${t.toFixed(2)}秒`;
    if (x !== null && now - lastTick >= 1000 / FPS) {
      lastTick = now; lastX = x;
      opts.onTime(x);
    }
    follow(b, x, a.video.playbackRate);
    raf = requestAnimationFrame(tick);
  }

  // 下の動画を、上の動画の所 x に合わせ続ける。小さなずれは速さで詰め、大きなずれは動かす
  function follow(slot, x, rate) {
    const lap = lapOf(slot);
    if (!lap) return;
    const t = lapTimeAt(lap, x);
    const seg = segAt(lap, t);
    const sec = seg ? videoSec(seg, t) : null;
    if (sec === null) {
      if (!slot.video.paused) slot.video.pause();
      showMiss(slot, 'この所の動画はありません');
      return;
    }
    showMiss(slot, null);
    slot.pos.textContent = `周の頭から ${t.toFixed(2)}秒`;
    if (slot.url !== seg.url || slot.busy) { setTime(slot, seg, sec); return; }
    const diff = sec - slot.video.currentTime;
    if (Math.abs(diff) > DRIFT_S) { setTime(slot, seg, sec); slot.video.playbackRate = rate; }
    else slot.video.playbackRate = rate * (1 + Math.max(-0.5, Math.min(0.5, diff * 2)));
    if (slot.video.paused) slot.video.play().catch(() => {});
  }

  function play() {
    const [a] = slots;
    if (!lapOf(a)) return;
    let x = lastX;
    const lapA = lapOf(a);
    // カーソルが無い・周の外・動画の無い所なら、周の頭（動画のある最初の所）から
    if (x === null || segAt(lapA, lapTimeAt(lapA, x)) === null) {
      const first = (lapA.video || [])[0];
      if (!first) return;
      x = xAt(lapA, first.from);
      if (x === null) return;
      opts.onTime(x);
    }
    seekAll(x);
    a.video.playbackRate = opts.rate();
    playing = true;
    a.video.play().catch(() => { stop(); });
    opts.onPlayState(true);
    if (!raf) raf = requestAnimationFrame(tick);
  }

  function stop() {
    playing = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    for (const s of slots) { s.video.pause(); s.video.playbackRate = 1; }
    opts.onPlayState(false);
    seekAll(lastX);         // 下の動画のずれを詰めて止める
  }

  // ---------------------------------------------------------------- 選ぶ周
  function refresh() {
    for (const s of slots) {
      const lap = lapOf(s);
      s.sw.style.background = lap ? opts.color(lap.key) : 'transparent';
      s.wrap.classList.toggle('none', !lap);
      if (!lap) { s.video.removeAttribute('src'); s.video.load(); s.url = null; s.seg = null; showMiss(s, '周を選んでください'); s.pos.textContent = ''; }
    }
  }

  function fillSelects() {
    const withVideo = laps.filter((l) => l.video && l.video.length);
    const keys = withVideo.map((l) => l.key);
    const [a, b] = slots;
    const ref = withVideo.find((l) => l.ref);
    if (!keys.includes(a.key)) a.key = (ref || withVideo[0] || {}).key || null;
    if (b.key !== null && !keys.includes(b.key)) b.key = null;
    if (b.key === null) b.key = (withVideo.find((l) => l.key !== a.key) || {}).key || null;
    for (const s of slots) {
      s.sel.textContent = '';
      const none = document.createElement('option');
      none.value = ''; none.textContent = '（出さない）';
      s.sel.append(none);
      for (const l of withVideo) {
        const o = document.createElement('option');
        o.value = l.key; o.textContent = l.label + (l.ref ? '（基準）' : '');
        s.sel.append(o);
      }
      s.sel.value = s.key || '';
    }
    return withVideo.length;
  }

  return {
    // o：{x() 'dist'|'time', color(key), rate() 再生の速さ, onTime(x) 再生中にカーソルを動かす, onPlayState(bool)}
    init(box, o) {
      opts = o;
      slots = [makeSlot(box, '上', true), makeSlot(box, '下', false)];
    },
    // 重ね合わせの返事を受ける。動画のある周の数を返す
    show(data) {
      laps = (data && data.laps) || [];
      for (const l of laps) {
        l._s2t = l.track ? pairs(l.track.s, l.track.t) : null;
        l._t2s = l.track ? pairs(l.track.t, l.track.s) : null;
        for (const v of l.video || []) { v._v = v.sync.map((p) => p[0]); v._t = v.sync.map((p) => p[1]); }
      }
      const n = fillSelects();
      if (playing && !lapOf(slots[0])) stop();
      refresh();
      // どこを出すかは、呼ぶ側が続けて cursor() で渡す
      return n;
    },
    // カーソルが動いた。kind：'hover'（なぞった）・'pin'（押して留めた）・'leave'（外れて留めた所へ戻った）
    cursor(x, kind) {
      if (playing) {
        if (kind !== 'pin' || x === null) return;
        lastX = x;
        const a = slots[0], lap = lapOf(a), t = lap ? lapTimeAt(lap, x) : null, seg = lap ? segAt(lap, t) : null;
        if (seg) setTime(a, seg, videoSec(seg, t));
        return;
      }
      if (x === null && kind === 'leave') return;
      seekAll(x);
    },
    // 2周を、それぞれの周の頭からの秒 t の瞬間で止めて並べる [{key, t}]（区間の表のブレーキの欄から。SP-10-38）。
    // 次にカーソルが動けば、ふだんの「同じ所」に戻る
    moments(list) {
      if (playing) stop();
      list.slice(0, slots.length).forEach((m, i) => {
        const slot = slots[i], lap = laps.find((l) => l.key === m.key);
        if (!lap) return;
        slot.key = m.key; slot.sel.value = m.key;
      });
      refresh();
      list.slice(0, slots.length).forEach((m, i) => {
        const slot = slots[i], lap = lapOf(slot), seg = lap ? segAt(lap, m.t) : null;
        slot.pos.textContent = `周の頭から ${m.t.toFixed(2)}秒`;
        if (!seg) { showMiss(slot, 'この瞬間の動画がありません'); return; }
        showMiss(slot, null);
        setTime(slot, seg, videoSec(seg, m.t));
      });
    },
    toggle() { if (playing) stop(); else play(); },
    setRate(r) { if (playing) slots[0].video.playbackRate = r; },
    playing() { return playing; },
    clear() { if (playing) stop(); laps = []; lastX = null; fillSelects(); refresh(); },
  };
})();
