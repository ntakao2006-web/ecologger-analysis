// カーソル（横軸の値）と、各周の「周の頭からの秒」の対応（SP-10-16。仕様 analysis_map_features.md 3-5）。
// グラフ・地図・動画・摩擦円が同じ決まりで動くよう、ここに1つにまとめる。
// - 置き方 'space'（同じ場所）：基準の周でいうと同じ距離の所を、その周が通った時刻
// - 置き方 'time'（同じ時間）：そろえる所からの経過時間が基準の周と同じ時刻。遅い周の点は後ろに離れていく
// - そろえる所 'lap'（周の頭）か 'section'（区間の頭。区間の線を越えた時刻を基準の周とそろえ、そこからの経過時間）
// - 横軸に合わせる 'axis'：距離の横軸なら同じ場所、時間の横軸なら同じ時間
// 時間の横軸で区間の頭にそろえると、各周の時刻は区間ごとに「基準の周がその区間の頭を越えた時刻」へずらして描く。
'use strict';

const Sync = (() => {
  let opts = null, data = null, ref = null, heads = [], refHeads = [0];

  function interp(xa, ya, x) {
    const n = xa ? xa.length : 0;
    if (!n || x === null || x === undefined || x < xa[0] || x > xa[n - 1]) return null;
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xa[m] <= x) lo = m; else hi = m; }
    const d = xa[hi] - xa[lo];
    return d > 0 ? ya[lo] + (ya[hi] - ya[lo]) * (x - xa[lo]) / d : ya[lo];
  }
  // null の点を抜いた [xa, ya]（xa は増える一方にそろえる）
  function pairs(xa, ya) {
    const a = [], b = [];
    if (!xa || !ya) return [a, b];
    for (let i = 0; i < xa.length; i++) {
      if (xa[i] === null || ya[i] === null) continue;
      if (a.length && xa[i] <= a[a.length - 1]) continue;
      a.push(xa[i]); b.push(ya[i]);
    }
    return [a, b];
  }
  function prep(lap) {
    if (!lap._sync) {
      const tr = lap.track;
      lap._sync = { s2t: tr ? pairs(tr.s, tr.t) : null, t2s: tr ? pairs(tr.t, tr.s) : null, heads: null };
    }
    return lap._sync;
  }
  function aligning() { return opts && opts.align() === 'section' && heads.length > 0 && !!ref; }
  // その周が各区間の頭（heads、基準の周の距離）を通った時刻。先頭は周の頭（0）
  function lapHeads(lap) {
    const p = prep(lap);
    if (!p.heads) p.heads = [0, ...heads.map((s) => (p.s2t ? interp(p.s2t[0], p.s2t[1], s) : null))];
    return p.heads;
  }
  // カーソル → 基準の周の時刻・距離
  function refTime(x) { return opts.x() === 'dist' ? (ref ? interp(prep(ref).s2t[0], prep(ref).s2t[1], x) : null) : x; }
  function refDist(x) { return opts.x() === 'dist' ? x : (ref ? interp(prep(ref).t2s[0], prep(ref).t2s[1], x) : null); }
  function within(lap, t) { return t !== null && t >= 0 && t <= lap.time_s ? t : null; }

  return {
    // o：{x() 'dist'|'time', align() 'lap'|'section', heads() 区間の頭の位置（基準の周の距離 m）の配列}
    init(o) { opts = o; },
    // 重ね合わせの返事を受ける（区間の線が変わったときも呼び直す）
    setData(d) {
      data = d;
      ref = d ? d.laps.find((l) => l.ref && l.track) || null : null;
      const hs = ((opts.heads && opts.heads()) || []).filter((v) => v !== null && v !== undefined && isFinite(v) && v > 0).sort((a, b) => a - b);
      heads = hs;
      for (const l of (d ? d.laps : [])) if (l._sync) l._sync.heads = null;
      refHeads = ref ? lapHeads(ref).map((v, i) => (i === 0 ? 0 : v)) : [0];
    },
    aligning,
    // 周 lap の、カーソル x のときの周の頭からの秒。mode：'axis'・'space'・'time'。分からなければ null
    lapTime(lap, x, mode = 'axis') {
      if (!opts || x === null || x === undefined || !lap) return null;
      const m = mode === 'axis' ? (opts.x() === 'dist' ? 'space' : 'time') : mode;
      if (m === 'space') {
        const s = refDist(x), p = prep(lap);
        if (s === null) return opts.x() === 'dist' ? null : within(lap, x);
        return p.s2t ? interp(p.s2t[0], p.s2t[1], s) : null;
      }
      const tr = refTime(x);
      if (tr === null) return null;
      if (!aligning()) return within(lap, tr);
      const lh = lapHeads(lap);
      let k = 0;
      for (let i = 1; i < refHeads.length; i++) if (refHeads[i] !== null && refHeads[i] <= tr && lh[i] !== null) k = i;
      return within(lap, tr - refHeads[k] + lh[k]);
    },
    // その周の頭からの秒 t → カーソル（横軸に合わせた置き方で。動画の再生がカーソルを動かすとき・時間の横軸でずらして描くとき）
    xOf(lap, t) {
      if (!opts || t === null || t === undefined || !lap) return null;
      if (opts.x() === 'dist') { const p = prep(lap); return p.t2s ? interp(p.t2s[0], p.t2s[1], t) : null; }
      if (!aligning()) return t;
      const lh = lapHeads(lap);
      let k = 0;
      for (let i = 1; i < lh.length; i++) if (lh[i] !== null && refHeads[i] !== null && lh[i] <= t) k = i;
      return t - lh[k] + refHeads[k];
    },
    // 基準の周の時刻 tr → カーソル（地図だけの再生でカーソルを進めるとき）
    xOfRef(tr) { return ref ? this.xOf(ref, tr) : tr; },
    refTime(x) { return opts ? refTime(x) : null; },
  };
})();
