// model.test.mjs — 提案B: 試した仮説が「実際に次の判断を変える」ことの検証
//
// tested_hypotheses を状態に書くだけでは意味がない。読む側が無ければ挙動は変わらない。
// ここでは propose() が tested_hypotheses を実際に消費していることを確かめる。

import { test } from "node:test";
import assert from "node:assert/strict";
import { propose } from "../src/model.mjs";
import { buildContext } from "../src/context.mjs";
import { applyPatch } from "../src/state-patch.mjs";
import { initState } from "../src/state.mjs";

const TASK = { goal: "CSV エクスポートを追加する", constraints: ["新しい依存を足さない"] };
const baseState = () => initState({ task_id: "feature_042", max_repairs: 3 });

const DATE_FAILED = [
  { name: "date_format", passed: false, rule: "ISO 8601" },
  { name: "fixture_match", passed: false },
];

// 失敗の証拠だけがある状態（tested_hypotheses は空）
function stateWithEvidenceOnly() {
  return applyPatch(baseState(), { last_evidence: DATE_FAILED }).state;
}

// 失敗の証拠に加えて「slash を試して失敗した」が記録されている状態
function stateWithTriedSlash() {
  return applyPatch(stateWithEvidenceOnly(), {
    tested_hypotheses: [
      { attempt: 1, hypothesis: { date_mode: "slash" }, failed_checks: "date_format, fixture_match" },
    ],
  }).state;
}

test("buildContext: spec を渡すと repo の文書を読まずに固定の手順書を使う", () => {
  const s = baseState();
  const withSpec = buildContext(TASK, s, { spec: "# spec\n固定の手順書" });
  assert.match(withSpec, /固定の手順書/);
  // 既定で入る repo 由来の見出しが消えていること（= ファイルを読んでいない）
  assert.doesNotMatch(withSpec, /# map \(AGENTS\.md\)/);
  assert.doesNotMatch(withSpec, /# product-rules/);
  // 状態側の項目は今までどおり入る
  assert.match(withSpec, /tested_hypotheses:/);
  assert.match(withSpec, /last_evidence:/);

  // 既定（spec なし）は従来どおり repo の文書を読む
  const def = buildContext(TASK, s);
  assert.match(def, /# map \(AGENTS\.md\)/);
  assert.match(def, /# product-rules/);
});

test("propose は試した仮説を返す（ランタイムが記録できる）", () => {
  const s = baseState();
  const r = propose(buildContext(TASK, s), s);
  assert.equal(r.name, "write_workspace");
  assert.ok(r.hypothesis, "hypothesis を返していない");
  assert.equal(typeof r.hypothesis.date_mode, "string");
});

test("証拠が無ければ最初の候補を出す", () => {
  const s = baseState();
  const r = propose(buildContext(TASK, s), s);
  assert.equal(r.hypothesis.date_mode, "slash");
  assert.match(r.args.content, /2026\/09\/11/);
});

test("提案B の本体: 失敗した仮説は二度提案しない", () => {
  const s = stateWithTriedSlash();
  const r = propose(buildContext(TASK, s), s);
  assert.notEqual(r.hypothesis.date_mode, "slash");
  assert.equal(r.hypothesis.date_mode, "iso");
  assert.match(r.args.content, /2026-09-11/);
});

test("提案B が効いている証拠: tested_hypotheses を隠すと同じ仮説を繰り返す", () => {
  const s = stateWithTriedSlash();
  const full = buildContext(TASK, s);
  // context から tested_hypotheses 行だけを落とす = 機能を外した場合の再現
  const hidden = full.replace(/tested_hypotheses: .*/, "tested_hypotheses: (hidden)");

  assert.equal(propose(full, s).hypothesis.date_mode, "iso", "見えていれば先へ進む");
  assert.equal(propose(hidden, s).hypothesis.date_mode, "slash", "見えなければ同じ失敗を繰り返す");
});

test("候補を使い切ったら最後の候補に留まる（無限に増やさない）", () => {
  const s = applyPatch(stateWithEvidenceOnly(), {
    tested_hypotheses: [
      { attempt: 1, hypothesis: { date_mode: "slash" }, failed_checks: "x" },
      { attempt: 2, hypothesis: { date_mode: "iso" }, failed_checks: "x" },
    ],
  }).state;
  const r = propose(buildContext(TASK, s), s);
  assert.equal(r.hypothesis.date_mode, "iso");
});

test("壊れた tested_hypotheses でも落ちない", () => {
  const s = stateWithEvidenceOnly();
  const broken = buildContext(TASK, s).replace(/tested_hypotheses: .*/, "tested_hypotheses: [{壊れ");
  assert.doesNotThrow(() => propose(broken, s));
});
