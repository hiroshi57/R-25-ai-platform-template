// adapters.mjs — 実ツール・アダプタ（安全側の既定 = ドライラン / 読取のみ）
//
// 【絶対規則】
//  - orchestrator.py は顧客Excelを書き換えるため、このアダプタは**絶対に実行しない**。
//    コマンド構築＋入力パス検証（読取）＋ログのみ。実行は人間承認後に人手で行う。
//  - Vercel は読取専用コマンドのみ。deploy は本番禁止のため実装しない。
//  - 別リポジトリ(yosikei-agents)へは一切書き込まない。

import { existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";

// orchestrator.py の起動コマンドを構築する（実行はしない）
export function buildOrchestratorCommand(caseObj) {
  const root = caseObj.tool_root;
  const entry = caseObj.tool_entry || "agents/orchestrator.py";
  const a = caseObj.tool_args || {};
  const argv = [
    "python", entry,
    "--fmt", a.fmt,
    "--raw", a.raw,
    "--tool", a.tool,
  ];
  if (a["backup-dir"]) argv.push("--backup-dir", a["backup-dir"]);

  const requiredKeys = ["fmt", "raw", "tool"];
  const inputs = requiredKeys.map((k) => ({
    key: k,
    value: a[k],
    exists: existsSync(path.join(root, a[k] || "")),
  }));

  return {
    cwd: root,
    argv,
    command: `cd "${root}" && ${argv.map((s) => (/\s/.test(s) ? `"${s}"` : s)).join(" ")}`,
    inputs,
    dry_run: true,
    executed: false,
    note: "orchestrator.py はドライランのため実行しない（顧客データ保護・本番禁止）",
  };
}

// Vercel 読取専用（既定はドライラン=コマンドのログのみ）
export function vercelRead(args = {}) {
  const argv = args.project
    ? ["vercel", "inspect", args.project]
    : ["vercel", "projects", "ls"];
  const command = argv.join(" ");

  if (!args.execute) {
    return { command, dry_run: true, executed: false, note: "読取専用（ドライラン=ログのみ）" };
  }
  // execute=true の場合のみ実際に読取コマンドを叩く（deploy 等の書込は含めない）
  try {
    const out = execFileSync(argv[0], argv.slice(1), { encoding: "utf8", timeout: 20000 });
    return { command, dry_run: false, executed: true, stdout: out.slice(0, 400) };
  } catch (e) {
    return { command, dry_run: false, executed: true, error: String(e.message || e).slice(0, 200) };
  }
}
