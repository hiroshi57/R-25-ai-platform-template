// context.mjs — 層2 コンテキスト・コンパイラ
//
// 会話全文やDBを丸ごと渡さない。高信号な情報だけを、必要な順で組み立てる:
//   AGENTS.md(地図) -> 製品ルール -> 契約の目標/制約 -> 構造化した状態 -> 直近の証拠
// 過去の推論・観測の全文は入れない（SKILL.state 方式, arXiv:2608.26263）。
// 同じ修復を繰り返さないために必要な過去は、state.tested_hypotheses に構造化して残す。
//
// ★実験: 末尾の last_evidence 行を消すと、モデルは date_format の失敗を「見られなく」なり、
//   修復が起きない。コンテキストの設計が修復可能性を決めていることが分かる。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const read = (rel) => readFileSync(path.join(ROOT, rel), "utf8").trim();

// spec を渡すと、手順書を repo の文書から読まずにその文字列で差し替える。
// ベンチのように「測定値を repo の文書の増減で動かしたくない」場合に使う。
// 省略時の挙動は従来どおり（AGENTS.md と product-rules.md を読む）。
export function buildContext(task, state, { spec } = {}) {
  const procedure =
    spec !== undefined
      ? [spec]
      : [
          `# map (AGENTS.md)\n${read("AGENTS.md").split("\n").slice(0, 4).join("\n")}`,
          `# product-rules\n${read("context/product-rules.md")}`,
        ];
  return [
    ...procedure,
    `goal: ${task.goal}`,
    `constraints: ${task.constraints.join(" / ")}`,
    `completed: ${state.completed.join(", ") || "(none)"}`,
    `open_risks: ${state.open_risks.join(", ") || "(none)"}`,
    `tested_hypotheses: ${JSON.stringify(state.tested_hypotheses ?? [])}`,
    `last_evidence: ${JSON.stringify(state.last_evidence ?? null)}`,
  ].join("\n---\n");
}
