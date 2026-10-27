// verify.mjs — 層5 証拠ゲート
//
// 「良さそう」は停止条件ではない。「チェックが全部通った」が停止条件。
// 判定は accept / retry / escalate の3分岐。
//   - すべて通過        -> accept
//   - 修復可能な失敗のみ -> retry
//   - 修復不能な失敗あり -> escalate
//
// ★実験: date_format チェックの行を消すと、証拠から「日付形式」が消える。
//   すると fixture_match だけが落ち、モデルは失敗の内容を推測できず修復を空振りする。
//   検証の粒度が、そのまま修復可能性を決めている。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

function check(name, passed, detail = {}) {
  return passed ? { name, passed } : { name, passed, ...detail };
}

export function verify(artifactRelPath) {
  // 改行コードを正規化（Windows の CRLF でも一致判定が壊れないように）
  const norm = (s) => s.replace(/\r\n/g, "\n");
  const text = norm(readFileSync(path.join(ROOT, artifactRelPath), "utf8"));
  const fixture = norm(readFileSync(path.join(ROOT, "checks/fixture.csv"), "utf8"));

  const lines = text.trim().split("\n");
  const header = lines[0];
  const dataRows = lines.slice(1);
  const isoDates = dataRows.every((r) => /^\d{4}-\d{2}-\d{2},/.test(r));

  const evidence = [
    check("output_schema", header === "date,region,revenue", { got: header }),
    check("date_format", isoDates, { rule: "context/product-rules.md: ISO 8601" }),
    check("fixture_match", text.trim() === fixture.trim(), {
      expected_head: fixture.trim().split("\n")[1],
      got_head: text.trim().split("\n")[1],
    }),
    check("no_new_dependency", true),
  ];

  const failed = evidence.filter((c) => !c.passed);
  if (failed.length === 0) return { status: "accept", evidence, failed };

  // no_new_dependency 以外の失敗はローカル修復可能とみなす
  const repairable = failed.every((c) => c.name !== "no_new_dependency");
  return { status: repairable ? "retry" : "escalate", evidence, failed };
}
