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

  return {
    VERDICT, BIG_GAP_S, SLOW_X,
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
