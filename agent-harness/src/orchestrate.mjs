// orchestrate.mjs — 案件を自動で「分類 → 6層処理」する司令塔
//
//   層0 分類器   案件を type / risk / split に仕分ける
//   層1 契約     type ごとの done_when を確定
//   層2 文脈     読取専用ツール(vercel_read)で環境を確認（gateway=automatic）
//   層5 検証     type 別の証拠ゲート（DATAはドライランで前提条件を検証）
//   層3 ゲート   final_action を risk で判定。high は人間承認で停止（実行しない）
//   層4/6       state/cases.json と runs/traces.jsonl に記録
//
// 実行: node agent-harness/src/orchestrate.mjs
// 安全既定: ドライラン。orchestrator.py / deploy は自動実行しない。

import { readFileSync, writeFileSync, mkdirSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { classify } from "./classifier.mjs";
import { verifyByType } from "./verifiers.mjs";
import { permissionFor, isNoticed } from "./policy.mjs";
import { gateway } from "./gateway.mjs";
import { buildOrchestratorCommand } from "./adapters.mjs";
import { appendTrace } from "./state.mjs";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const permissions = JSON.parse(readFileSync(path.join(ROOT, "tools/permissions.json"), "utf8"));

// type ごとの完了条件（層1 契約）
const DONE_WHEN = {
  DATA: ["入力が揃っている", "コマンドが正しく構築されている", "出力がスキーマ準拠(実行後)"],
  CODE: ["構文チェック通過", "対象シンボルをexport"],
  UI: ["レイアウト要素あり", "レスポンシブ対応"],
  RESEARCH: ["出典2件以上", "矛盾なし"],
  SUPPORT: ["PII非混入", "ポリシー準拠"],
};

function loadCases() {
  const dir = path.join(ROOT, "cases");
  return readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => JSON.parse(readFileSync(path.join(dir, f), "utf8")));
}

function bar(t) {
  console.log("\n" + "─".repeat(64) + "\n" + t + "\n" + "─".repeat(64));
}

const results = [];

for (const c of loadCases()) {
  bar(`案件: ${c.id}  「${c.title}」`);

  // 層0 分類
  const cls = classify(c);
  console.log(`  [層0 分類] type=${cls.type}  risk=${cls.risk}  split=${cls.split}` +
    (cls.split ? ` (${cls.units.length}単位)` : ""));
  console.log(`            理由: ${cls.reason.type} / ${cls.reason.risk} / ${cls.reason.split}`);

  // 層1 契約
  const doneWhen = DONE_WHEN[cls.type] || [];
  console.log(`  [層1 契約] done_when: ${doneWhen.join(" / ")}`);

  // 層2 文脈（読取専用ツール）
  for (const r of c.context_reads || []) {
    const obs = gateway({ request: { name: r.tool, args: {} }, permissions });
    const res = obs.result || {};
    console.log(`  [層2 文脈] ${r.tool} -> ${obs.status}  ${res.dry_run ? "(dry-run)" : ""} ${res.command ? "$ " + res.command : ""}`);
  }

  // 層5 検証（type別。DATAはドライランで前提条件）
  const v = verifyByType(cls.type, c, { /* ドライラン: 実データに触れない */ });
  if (v.status === "accept") {
    console.log(`  [層5 検証] accept: ${v.evidence.map((e) => e.name).join(", ")}`);
  } else {
    console.log(`  [層5 検証] ${v.status}: 失敗 ${JSON.stringify(v.failed)}`);
  }

  // 層3 ゲート（final_action を risk で判定）
  let stop = "verified";
  let plannedCommand = null;
  if (v.status !== "accept") {
    stop = "escalate";
    console.log(`  [層3 ゲート] スキップ（検証未通過）`);
  } else if (c.final_action) {
    const perm = permissionFor(cls.risk);
    if (perm === "approval") {
      stop = "human_approval_required";
      // 破壊的アクションはゲートで停止。ドライランでコマンドだけ構築（実行しない）
      if (c.final_action === "run_orchestrator") {
        const cmd = buildOrchestratorCommand(c);
        plannedCommand = cmd.command;
        console.log(`  [層3 ゲート] ${c.final_action} -> 承認待ち（自動実行しない / dry-run）`);
        console.log(`            必要条件: ${permissions[c.final_action].requires.join(", ")}`);
        console.log(`            入力検証: ` + cmd.inputs.map((i) => `${i.key}=${i.exists ? "OK" : "未(実行時)"}`).join(" "));
        console.log(`            計画コマンド: $ ${cmd.command}`);
      } else {
        console.log(`  [層3 ゲート] ${c.final_action} -> 承認待ち`);
      }
    } else {
      stop = "auto_executed";
      console.log(`  [層3 ゲート] ${c.final_action} -> 自動実行${isNoticed(cls.risk) ? "（med: 注記を記録）" : ""}`);
    }
  }

  results.push({
    id: c.id, type: cls.type, risk: cls.risk, split: cls.split,
    units: cls.units.length, verify: v.status, stop, planned_command: plannedCommand,
  });

  appendTrace({
    run_id: `orc_${new Date().toISOString().slice(0, 10)}_${c.id}`,
    case_id: c.id, model_route: "classifier+router", classified: cls,
    verify: v.status, stop_reason: stop, dry_run: true,
  });
}

// 層4 状態の保存
const statePath = path.join(ROOT, "state/cases.json");
mkdirSync(path.dirname(statePath), { recursive: true });
writeFileSync(statePath, JSON.stringify({ dry_run: true, cases: results }, null, 2) + "\n", "utf8");

// サマリ
bar("サマリ（自動分類 → 6層処理 / ドライラン）");
console.log("  案件".padEnd(22) + "種別".padEnd(10) + "リスク".padEnd(8) + "分割".padEnd(7) + "検証".padEnd(10) + "停止理由");
for (const r of results) {
  console.log(
    ("  " + r.id).padEnd(22) +
    r.type.padEnd(10) +
    r.risk.padEnd(8) +
    (r.split ? `${r.units}単位` : "-").padEnd(7) +
    r.verify.padEnd(10) +
    r.stop
  );
}
console.log("\n  ※ 高リスク(顧客データ書込)は承認ゲートで停止。orchestrator.py は実行していません。");
