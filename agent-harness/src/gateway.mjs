// gateway.mjs — 層3 ツール・ゲートウェイ
//
// モデルは「行動を要求」するだけ。実行できるかはポリシーが決める。
// 提案(model) -> 許可(authorize) -> 実行(registry) を分離する。
// 失敗はターミナル出力を丸投げせず、構造化された observation として返す。

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { vercelRead } from "./adapters.mjs";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

const registry = {
  read_files: (args) => ({
    content: readFileSync(path.join(ROOT, args.path), "utf8"),
  }),
  write_workspace: (args) => {
    const target = path.join(ROOT, args.path);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, args.content, "utf8");
    return { path: args.path, bytes: Buffer.byteLength(args.content, "utf8") };
  },
  send_message: (args) => ({ sent: true, preview: args.final_content_preview }),
  // 読取専用アダプタ（automatic）。既定はドライラン=コマンドのログのみ。
  vercel_read: (args) => vercelRead(args),
  // 破壊的アダプタ（approval）。承認前にゲートで止まる想定。誤って実行されないよう防御。
  run_orchestrator: () => {
    throw new Error("run_orchestrator は承認必須。ドライランでは自動実行しない。");
  },
};

function authorize(request, permissions) {
  const p = permissions[request.name];
  if (!p) return "deny";
  if (p.mode === "automatic") return "allow";
  if (p.mode === "approval") return "approval_required";
  return "deny";
}

export function gateway({ request, permissions }) {
  const decision = authorize(request, permissions);

  if (decision === "deny") {
    return { status: "permission_denied", tool: request.name };
  }
  if (decision === "approval_required") {
    return {
      status: "paused_for_human_approval",
      tool: request.name,
      requires: permissions[request.name].requires,
      preview: request.args,
    };
  }
  // automatic -> 実行
  const result = registry[request.name](request.args);
  return { status: "ok", tool: request.name, result };
}
