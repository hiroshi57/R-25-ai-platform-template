// harness.mjs — ループ本体（6層を結線する）
//
//   model.propose  (層2 context を入力に次の行動を提案)
//     -> gateway   (層3 提案・許可・実行を分離)
//     -> verify    (層5 証拠で accept/retry/escalate)
//     -> state     (層4 永続状態) / trace (層6)
//
// 実行: node agent-harness/src/harness.mjs

import { buildContext } from "./context.mjs";
import { propose } from "./model.mjs";
import { gateway } from "./gateway.mjs";
import { verify } from "./verify.mjs";
import {
  initState,
  saveState,
  appendTrace,
  loadContract,
  loadPermissions,
} from "./state.mjs";
import { applyPatchWithRetry } from "./state-patch.mjs";

const DECISION = "既存のエクスポートエンドポイントを再利用";
const ARTIFACT = "artifacts/export.csv";
const MAX_STEPS = 10;

const task = loadContract();
const permissions = loadPermissions();
let state = initState(task);

let step = 0;
let toolCalls = 0;
let stateChanges = 0;
let retries = 0;
let stopReason = "loop_exhausted";

// 状態の更新はすべてパッチで行い、検証・マージはランタイム（state-patch.mjs）が担う。
// 検証に落ちたパッチは状態を変えない（rollback）。そのうえで作り直させて再試行し、
// 上限まで落ち続けたら例外ではなくエスカレーションで終える。
//
// デモのパッチはコードが組むので、作り直しても同じものになる（= 3回落ちて escalate）。
// 本物のモデルを繋ぐときは、buildPatch の中でモデルを呼び、errors をプロンプトに入れる。
let patchRejection = null;

function commit(patch) {
  // 一度差し戻されたら、その周回の後続の更新は行わない（中途半端な状態を作らない）
  if (patchRejection) return false;
  const result = applyPatchWithRetry(state, () => patch);
  if (!result.ok) {
    patchRejection = result;
    return false;
  }
  state = result.state;
  return true;
}

while (step < MAX_STEPS) {
  step++;
  const context = buildContext(task, state);
  const request = propose(context, state);
  const obs = gateway({ request, permissions });
  toolCalls++;

  console.log(`[step ${step}] ${request.name} -> ${obs.status}`);

  if (obs.status === "permission_denied") {
    stopReason = "permission_denied";
    console.log(`  ⛔ 権限なし: ${request.name}`);
    break;
  }

  if (obs.status === "paused_for_human_approval") {
    stopReason = "human_approval_required";
    console.log(`  ⏸ 承認待ち: ${request.name}`);
    console.log(`     条件: ${obs.requires.join(", ")}`);
    console.log(`     preview: ${JSON.stringify(obs.preview)}`);
    break;
  }

  // obs.status === "ok": ツールが実行された
  if (request.name === "write_workspace") {
    commit({ artifacts: [ARTIFACT], decisions: [DECISION] });

    const result = verify(ARTIFACT);
    commit({ last_evidence: result.evidence });
    stateChanges++;

    if (result.status === "accept") {
      console.log(`  ✅ 全チェック通過: ${result.evidence.map((c) => c.name).join(", ")}`);
      commit({ completed: ["implementation", "verification"], status: "verified" });
    } else {
      console.log(`  ❌ 失敗: ${JSON.stringify(result.failed)}`);
      const names = result.failed.map((c) => c.name).join(", ");
      // 提案B: 何を試してどう落ちたかを構造化して残す（推論や観測の全文は残さない）。
      // ここに hypothesis を入れるから、次の propose が同じ候補を選び直さずに済む。
      commit({
        tested_hypotheses: [
          {
            attempt: state.repairs + 1,
            action: request.name,
            hypothesis: request.hypothesis ?? {},
            failed_checks: names,
          },
        ],
      });
      if (result.status === "retry" && state.repairs < task.max_repairs) {
        commit({ repairs: state.repairs + 1 });
        retries++;
        console.log(`  🔧 限定修復 ${state.repairs}/${task.max_repairs} 回目へ`);
      } else {
        stopReason = "escalate";
        console.log(`  ⤴ エスカレーション: 修復上限 or 修復不能 (${names})`);
        commit({ status: "escalated", open_risks: [`unresolved: ${names}`] });
        saveState(state);
        break;
      }
    }
  }

  // パッチが作り直しても通らなかった場合は、例外で落とさずエスカレーションで終える
  if (patchRejection) {
    stopReason = "escalate";
    console.log(`  ⤴ エスカレーション: 状態パッチが ${patchRejection.attempts} 回とも差し戻された`);
    console.log(`     理由: ${patchRejection.errors.join("; ")}`);
    state.status = "escalated";
    saveState(state);
    break;
  }

  saveState(state);
}

saveState(state);

const passed = (state.last_evidence ?? []).filter((c) => c.passed).length;
const failed = (state.last_evidence ?? []).filter((c) => !c.passed).length;

appendTrace({
  run_id: `run_${new Date().toISOString().slice(0, 10)}_${Math.floor(Math.random() * 9000 + 1000)}`,
  contract_version: "1",
  model_route: "deterministic-stub",
  context_sources: ["AGENTS.md", "context/product-rules.md"],
  tool_calls: toolCalls,
  state_changes: stateChanges,
  verification: { passed, failed },
  retries,
  stop_reason: stopReason,
  rollback_point: "git:initial",
});

console.log("");
console.log(`=== stop_reason: ${stopReason} / retries: ${retries} ===`);
