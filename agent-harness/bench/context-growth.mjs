// context-growth.mjs — プロンプトの伸び方を手元で実測する
//
// 目的: 「履歴を毎回送らなければ、プロンプトが履歴の長さに依存しない」という主張だけを、
// この repo の実装で確かめる。出典論文（arXiv:2608.26263）が出している効果量は使わない
// （比較相手のベースラインが定義上ありえない挙動を示すため。提案書 §7 を参照）。
//
// 測るもの:
//   A: state   = 本 repo の buildContext（手順書 + 構造化した状態 + 直近の証拠のみ）
//   B: history = 比較用に、過去の観測と行動を全部ためて渡す素朴な方式
//
// どちらも同じ手順書（SPEC）・同じ観測列を使う。違いは「履歴を渡すかどうか」だけ。
// 手順書は固定文字列なので、repo の文書を編集しても測定値は動かない。
//
// 実行: node agent-harness/bench/context-growth.mjs

import { buildContext } from "../src/context.mjs";
import { applyPatch } from "../src/state-patch.mjs";
import { initState } from "../src/state.mjs";

// 手順書（P）。両方式に同じものを渡す。
// repo の文書を読むと、他案件の編集で測定値が動いてしまうため固定文字列にする。
// 以前の版は A だけが手順書を含み、B は含んでいなかった。比較が不公平だったので直した。
const SPEC = `# spec
あなたは倉庫の在庫を管理するエージェントである。
- 行動は Store / Ship / Move / Wait のいずれか
- 棚が空であることを確認してから Store する
- 日付は ISO 8601 で書く
- 新しい依存関係を追加しない`;

const TASK = {
  goal: "分析ダッシュボードに CSV エクスポートを追加する",
  constraints: ["パブリック API を保持する", "データベース スキーマを変更しない", "新しい依存関係を追加しない"],
};

// 決定的な観測列。実行するたびに同じ結果になる。
function observation(t) {
  const kinds = [
    `Shipment arrived containing item_${t}`,
    `Customer ordered item_${t - 1 > 0 ? t - 1 : 1}`,
    `Maintenance required on shelf_${t % 50}`,
  ];
  return kinds[t % kinds.length];
}

const action = (t) => `write_workspace artifacts/export_${t}.csv`;

// A: 状態だけを渡す（本 repo の実装）
function statePrompt(state, t) {
  return `${buildContext(TASK, state, { spec: SPEC })}\n---\nlatest_observation: ${observation(t)}`;
}

// B: 過去の観測と行動を全部ためて渡す
function historyPrompt(history, t) {
  const lines = history.map((h, i) => `Observation: ${h.obs}\nAction: ${h.act}`).join("\n");
  return [
    SPEC,
    `goal: ${TASK.goal}`,
    `constraints: ${TASK.constraints.join(" / ")}`,
    `history:\n${lines}`,
    `latest_observation: ${observation(t)}`,
  ].join("\n---\n");
}

function run(horizon) {
  let state = initState({ task_id: "bench", max_repairs: 3 });
  const history = [];
  let stateChars = 0;
  let historyChars = 0;
  let maxState = 0;
  let maxHistory = 0;

  for (let t = 1; t <= horizon; t++) {
    const a = statePrompt(state, t);
    const b = historyPrompt(history, t);
    stateChars += a.length;
    historyChars += b.length;
    maxState = Math.max(maxState, a.length);
    maxHistory = Math.max(maxHistory, b.length);

    // 状態側は「次に必要な分だけ」を残す。履歴側は全部ためる。
    const patch = {
      last_evidence: [{ step: t, passed: t % 4 !== 0 }],
      ...(t % 4 === 0
        ? { tested_hypotheses: [{ attempt: t, hypothesis: { mode: `m${t % 3}` }, failed_checks: "check_a" }] }
        : {}),
    };
    const applied = applyPatch(state, patch);
    if (!applied.ok) throw new Error(`bench のパッチが不正: ${applied.errors.join("; ")}`);
    state = applied.state;

    history.push({ obs: observation(t), act: action(t) });
  }

  return {
    horizon,
    avgState: Math.round(stateChars / horizon),
    avgHistory: Math.round(historyChars / horizon),
    maxState,
    maxHistory,
    totalState: stateChars,
    totalHistory: historyChars,
  };
}

// 既定の地平線。引数で上書きできる: node ... 400 800
const horizons = process.argv.slice(2).map(Number).filter((n) => Number.isInteger(n) && n > 0);
const rows = (horizons.length > 0 ? horizons : [10, 25, 50, 100, 200]).map(run);

const pad = (v, w) => String(v).padStart(w);
console.log("プロンプト文字数（A: 状態のみ / B: 履歴を全部渡す）");
console.log("");
console.log("  T     A平均   A最大      B平均      B最大        A累計         B累計   累計比");
for (const r of rows) {
  const ratio = (r.totalHistory / r.totalState).toFixed(1);
  console.log(
    `${pad(r.horizon, 4)} ${pad(r.avgState, 8)} ${pad(r.maxState, 7)} ${pad(r.avgHistory, 10)} ${pad(r.maxHistory, 10)} ${pad(r.totalState, 12)} ${pad(r.totalHistory, 13)} ${pad(ratio + "x", 8)}`,
  );
}

const first = rows[0];
const last = rows.at(-1);
console.log("");
console.log(`A の1回あたり: T=${first.horizon} で ${first.avgState} 文字 → T=${last.horizon} で ${last.avgState} 文字`);
console.log(`B の1回あたり: T=${first.horizon} で ${first.avgHistory} 文字 → T=${last.horizon} で ${last.avgHistory} 文字`);
console.log("");
console.log("※ これは「プロンプトが履歴の長さに依存しないか」だけの実測。");
console.log("※ 正解率が上がるかどうかは、この測定では何も言えない。");
