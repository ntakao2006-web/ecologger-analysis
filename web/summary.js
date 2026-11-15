// 走行のまとめ（SP-10-40。仕様 analysis_map_features.md 3-7）。周の表の上に1段で出す数を、周の一覧から計算する。
// Garmin Catalyst の「走ったらまず見る」画面の考え方：ベスト・理論ベスト・連続ベスト・ベストと理論ベストの差・上位3周と5周の平均と幅。
// 上位3周の幅の目安は、Garmin が配る使い方の資料の数を借りる（0.5秒以下は上出来、0.5〜1.0 はまあまあ、1.0〜2.0 は安定が最優先、
// 2.0 より上はわけを探す）。Garmin の「Top 3 Var」の計算の中身は書かれていないので、ここでは幅（最も遅い周 − 最も速い周）で出す。
'use strict';

const RunSummary = (() => {
  const VERDICT = [[0.5, 'good', '上出来'], [1.0, 'ok', 'まあまあ'], [2.0, 'warn', '安定が最優先'], [Infinity, 'bad', 'わけを探す']];
  const SLOW_X = 1.3;            // 上位の周の平均に入れない遅さ（ベストのこの倍より遅い周＝ピットに入った・止まった周）
  const BIG_GAP_S = 1.0;          // ベストと理論ベストの差がこれを超えたら、詰まりか走りのばらつきかを考える（同じ資料）

  function has(v) { return v !== null && v !== undefined && isFinite(v); }

  // 区間（splits）かセクター（sectors）の、区間ごとのベストの和。全部の区間がそろった周が無ければ null
  function theory(laps, key) {
    const n = Math.max(0, ...laps.map((x) => (x[key] || []).length));
    if (!n) return null;
    let sum = 0;
    for (let i = 0; i < n; i++) {
      const c = laps.filter((x) => (x[key] || []).length === n && has(x[key][i])).map((x) => x[key][i]);
      if (!c.length) return null;
      sum += Math.min(...c);
    }
    return sum;
  }

  function top(times, k) {
    if (times.length < k) return null;
    const t = times.slice(0, k);
    return { n: k, mean: t.reduce((a, b) => a + b, 0) / k, range: t[k - 1] - t[0] };
  }

  function verdict(range) {
    if (!has(range)) return null;
    const v = VERDICT.find(([lim]) => range <= lim);
    return { cls: v[1], text: v[2] };
  }

  const MEAN_SLOW = 1.05;         // 平均の周：区間タイムがその区間の中央値よりこれだけ遅い周は、その区間だけ外す（裏の sections.SLOW_RATIO と同じ）
  const OPP_N = 3;                // 伸びしろの区間を出す数
  const FIELDS = ['entry', 'exit', 'brake_m', 'min_kmh', 'min_at_m'];

  function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : null; }
  function diff(a, b) { return has(a) && has(b) ? a - b : null; }

  // 伸びしろの大きい区間（SP-10-41。仕様 3-7）。runs：[{id, laps: [{lap, time_s, gap?, splits, sec}]}]、partner：'best'（ベストの周）か 'mean'（平均の周）
  // 区間ごとに「比べる相手の区間タイム − その区間のベスト」を出し、大きい順に OPP_N 個。それぞれに、その区間のベストの周と相手の違いを
  // ブレーキ（踏み始めの位置 m、＋はベストの周が奥で踏んだ）・頂点（ボトムの速さ・位置）・速さ（入口・出口）の数で付ける
  function opportunities(runs, partner) {
    const all = runs.flatMap((r) => (r.laps || []).filter((x) => !x.gap && has(x.time_s)).map((x) => ({ ...x, id: r.id })));
    const n = Math.max(0, ...all.map((x) => (x.splits || []).length));
    const pool = all.filter((x) => n >= 2 && x.splits && x.splits.length === n && x.splits.every(has));
    if (!pool.length) return null;
    const bestLap = pool.reduce((a, b) => (b.time_s < a.time_s ? b : a));
    const items = [];
    for (let i = 0; i < n; i++) {
      const b = pool.reduce((a, x) => (x.splits[i] < a.splits[i] ? x : a));
      let pt, pm, pl = null;
      if (partner === 'mean') {
        const c = pool.map((x) => x.splits[i]).sort((x, y) => x - y);
        const med = c.length % 2 ? c[c.length >> 1] : (c[c.length / 2 - 1] + c[c.length / 2]) / 2;
        const keep = pool.filter((x) => x.splits[i] < med * MEAN_SLOW);
        pt = mean(keep.map((x) => x.splits[i]));
        pm = {};
        for (const f of FIELDS) pm[f] = mean(keep.map((x) => (x.sec || [])[i]).filter((m) => m && has(m[f])).map((m) => m[f]));
      } else {
        pt = bestLap.splits[i]; pm = (bestLap.sec || [])[i] || {}; pl = { id: bestLap.id, lap: bestLap.lap };
      }
      const bm = (b.sec || [])[i] || {};
      items.push({ i, loss: pt - b.splits[i], partnerTime: pt, bestTime: b.splits[i], bestLap: { id: b.id, lap: b.lap }, partnerLap: pl,
                   brake: i === 0 ? null : diff(bm.brake_m, pm.brake_m), apexKmh: diff(bm.min_kmh, pm.min_kmh), apexAt: diff(bm.min_at_m, pm.min_at_m),
                   entry: diff(bm.entry, pm.entry), exit: diff(bm.exit, pm.exit), bestApexAt: has(bm.min_at_m) ? bm.min_at_m : null,
                   partnerApexAt: has(pm.min_at_m) ? pm.min_at_m : null });
    }
    const top = items.filter((x) => x.loss > 0.0005).sort((a, b) => b.loss - a.loss).slice(0, OPP_N);
    return { partner: partner === 'mean' ? 'mean' : 'best', partnerLap: partner === 'mean' ? null : { id: bestLap.id, lap: bestLap.lap },
             partnerTime: partner === 'mean' ? items.reduce((a, x) => a + x.partnerTime, 0) : bestLap.time_s,
             total: items.reduce((a, x) => a + Math.max(0, x.loss), 0), sections: n, items: top };
  }

  return {
    VERDICT, BIG_GAP_S, SLOW_X, opportunities,
    // runs：[{laps: [{time_s, gap?, splits?, sectors?}], rolling: {time_s} | null}]（まとめに使う走行）
    // 返す値：{n（使った周）, best, theory, theoryKind 'splits'|'sectors'|null, rolling, gapToTheory, top3, top5, verdict, bigGap}
    of(runs) {
      const laps = runs.flatMap((r) => (r.laps || []).filter((x) => !x.gap && has(x.time_s)));
      const times = laps.map((x) => x.time_s).sort((a, b) => a - b);
      const out = { n: laps.length, best: times.length ? times[0] : null, theory: null, theoryKind: null, rolling: null,
                    gapToTheory: null, top3: null, top5: null, verdict: null, bigGap: false };
      const fast = times.filter((t) => t <= times[0] * SLOW_X);
      out.top3 = top(fast, 3); out.top5 = top(fast, 5);
      for (const key of ['splits', 'sectors']) {
        const t = theory(laps, key);
        if (t !== null) { out.theory = t; out.theoryKind = key; break; }
      }
      const rs = runs.map((r) => r.rolling && r.rolling.time_s).filter(has);
      if (rs.length) out.rolling = Math.min(...rs);
      if (has(out.best) && has(out.theory)) { out.gapToTheory = out.best - out.theory; out.bigGap = out.gapToTheory > BIG_GAP_S; }
      out.verdict = out.top3 ? verdict(out.top3.range) : null;
      return out;
    },
  };
})();

if (typeof module !== 'undefined') module.exports = RunSummary;
