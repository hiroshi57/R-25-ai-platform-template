// state-patch.test.mjs — 状態パッチの検証とマージ（提案C）と仮説記録（提案B）のテスト
// 実行: node --test agent-harness/test/

import { test } from "node:test";
import assert from "node:assert/strict";
import { applyPatch, parseStepOutput, WORKER_STATE_SCHEMA } from "../src/state-patch.mjs";
import { initState } from "../src/state.mjs";
import { buildContext } from "../src/context.mjs";

const baseState = () =>
  initState({ task_id: "feature_042", max_repairs: 1 });

test("パッチに書かれていないキーは残る（上書き事故の防止）", () => {
  const s0 = applyPatch(baseState(), { decisions: ["A"], artifacts: ["x.csv"] }).state;
  const r = applyPatch(s0, { status: "verified" });
  assert.equal(r.ok, true);
  assert.deepEqual(r.state.decisions, ["A"]);
  assert.deepEqual(r.state.artifacts, ["x.csv"]);
  assert.equal(r.state.status, "verified");
});

test("merge:append の配列は追加分だけを足し、既存要素を消さない", () => {
  const s0 = applyPatch(baseState(), { completed: ["implementation"] }).state;
  const r = applyPatch(s0, { completed: ["verification", "implementation"] });
  assert.equal(r.ok, true);
  assert.deepEqual(r.state.completed, ["implementation", "verification"]);
});

test("null は nullable なキーだけ削除できる", () => {
  const s0 = applyPatch(baseState(), { last_evidence: [{ name: "x", passed: true }] }).state;
  const ok = applyPatch(s0, { last_evidence: null });
  assert.equal(ok.ok, true);
  assert.equal("last_evidence" in ok.state, false);

  const ng = applyPatch(s0, { completed: null });
  assert.equal(ng.ok, false);
  assert.match(ng.errors[0], /削除できないキー: completed/);
});

test("型違反・未知キー・変更不可キーは状態を一切変えずに差し戻す（rollback）", () => {
  const s0 = baseState();
  const snapshot = structuredClone(s0);
  const r = applyPatch(s0, { repairs: "1", status: "verified", foo: 1, max_repairs: 9 });
  assert.equal(r.ok, false);
  assert.equal(r.errors.length, 3);
  assert.ok(r.errors.some((e) => e.includes("型が不正: repairs")));
  assert.ok(r.errors.some((e) => e.includes("未知のキー: foo")));
  assert.ok(r.errors.some((e) => e.includes("変更不可のキー: max_repairs")));
  // 正しい部分（status）も適用されない = 部分適用しない
  assert.deepEqual(r.state, snapshot);
  assert.deepEqual(s0, snapshot);
});

test("成功時も元の state オブジェクトは変更しない", () => {
  const s0 = baseState();
  const snapshot = structuredClone(s0);
  applyPatch(s0, { completed: ["implementation"], repairs: 1 });
  assert.deepEqual(s0, snapshot);
});

test("object 型はネストしたキーを null で削除できる", () => {
  const schema = { inventory: { type: "object" } };
  const s0 = { inventory: { shelf_42: "item_12", shelf_43: "item_13" } };
  const r = applyPatch(s0, { inventory: { shelf_42: null } }, schema);
  assert.equal(r.ok, true);
  assert.deepEqual(r.state.inventory, { shelf_43: "item_13" });
});

test("parseStepOutput: 推論を捨て、state_patch と action だけを返す", () => {
  const text = [
    "Reasoning: item_12 は shelf_42 にある。出荷する。",
    "```json",
    '{ "state_patch": { "inventory": { "shelf_42": null } }, "action": "Ship item_12 shelf_42" }',
    "```",
  ].join("\n");
  const r = parseStepOutput(text);
  assert.equal(r.ok, true);
  assert.equal(r.action, "Ship item_12 shelf_42");
  assert.deepEqual(r.state_patch, { inventory: { shelf_42: null } });
  assert.equal("reasoning" in r, false);
});

test("parseStepOutput: JSON 構文エラーと余計なキーは retry 可能なエラーになる", () => {
  const bad = parseStepOutput('```json\n{ "state_patch": {}, "action": "x", }\n```');
  assert.equal(bad.ok, false);
  assert.match(bad.errors[0], /JSON 構文エラー/);

  const extra = parseStepOutput('```json\n{ "state_patch": {}, "action": "x", "note": 1 }\n```');
  assert.equal(extra.ok, false);

  const none = parseStepOutput("Action: Ship item_12");
  assert.equal(none.ok, false);
});

test("提案B: initState は tested_hypotheses を持ち、スキーマに定義がある", () => {
  const s = baseState();
  assert.deepEqual(s.tested_hypotheses, []);
  for (const key of Object.keys(s)) {
    assert.ok(WORKER_STATE_SCHEMA[key], `schema に ${key} がない`);
  }
});

// --- 敵対的反証(2026-09-30)で見つかった5件の欠陥に対する回帰テスト ---

test("欠陥1: append 配列は $set で訂正できる（誤って入った要素を取り消せる）", () => {
  const s0 = applyPatch(baseState(), {
    tested_hypotheses: [{ attempt: 1, note: "誤り" }, { attempt: 2, note: "正しい" }],
  }).state;
  const r = applyPatch(s0, { tested_hypotheses: { $set: [{ attempt: 2, note: "正しい" }] } });
  assert.equal(r.ok, true);
  assert.deepEqual(r.state.tested_hypotheses, [{ attempt: 2, note: "正しい" }]);

  // $set で全消しもできる
  const cleared = applyPatch(s0, { tested_hypotheses: { $set: [] } });
  assert.deepEqual(cleared.state.tested_hypotheses, []);

  // $set の中身も型検査される
  const bad = applyPatch(s0, { tested_hypotheses: { $set: "配列ではない" } });
  assert.equal(bad.ok, false);
  assert.match(bad.errors[0], /\$set/);

  // 素の配列は今までどおり追記（既存要素は消えない）
  const appended = applyPatch(s0, { tested_hypotheses: [{ attempt: 3 }] });
  assert.equal(appended.state.tested_hypotheses.length, 3);
});

test("欠陥2: キー順が違う同一内容は重複とみなす", () => {
  let s = applyPatch(baseState(), { decisions: [{ a: 1, b: 2 }] }).state;
  s = applyPatch(s, { decisions: [{ b: 2, a: 1 }] }).state;
  assert.equal(s.decisions.length, 1);

  // 入れ子でもキー順に依存しない
  let t = applyPatch(baseState(), { decisions: [{ x: { p: 1, q: 2 } }] }).state;
  t = applyPatch(t, { decisions: [{ x: { q: 2, p: 1 } }] }).state;
  assert.equal(t.decisions.length, 1);

  // 内容が違うものは別要素として残る
  const u = applyPatch(t, { decisions: [{ x: { p: 1, q: 3 } }] }).state;
  assert.equal(u.decisions.length, 2);
});

test("欠陥3: 配列の要素の型を検査する（論文の失敗モード2）", () => {
  const bad = applyPatch(baseState(), { tested_hypotheses: ["文字列", 42] });
  assert.equal(bad.ok, false);
  assert.match(bad.errors[0], /tested_hypotheses\[0\]/);

  const badNull = applyPatch(baseState(), { tested_hypotheses: [null] });
  assert.equal(badNull.ok, false);

  const ok = applyPatch(baseState(), { tested_hypotheses: [{ attempt: 1 }] });
  assert.equal(ok.ok, true);

  // completed は文字列の配列
  const badStr = applyPatch(baseState(), { completed: [{ not: "string" }] });
  assert.equal(badStr.ok, false);
  assert.match(badStr.errors[0], /completed\[0\]/);
});

test("欠陥4: 値が同一の immutable キーは受け入れる（無害な echo で止まらない）", () => {
  const s0 = baseState();
  const echo = applyPatch(s0, { task_id: "feature_042", status: "verified" });
  assert.equal(echo.ok, true);
  assert.equal(echo.state.status, "verified");
  assert.equal(echo.state.task_id, "feature_042");

  // 値が違えば今までどおり拒否
  const changed = applyPatch(s0, { task_id: "other" });
  assert.equal(changed.ok, false);
  assert.match(changed.errors[0], /変更不可のキー: task_id/);
});

test("欠陥5: append 配列は maxItems で上限を保ち、古いものから捨てる", () => {
  let s = baseState();
  for (let i = 1; i <= 40; i++) {
    s = applyPatch(s, { tested_hypotheses: [{ attempt: i, failed_checks: "x" }] }).state;
  }
  const cap = WORKER_STATE_SCHEMA.tested_hypotheses.maxItems;
  assert.ok(cap > 0, "maxItems が定義されている");
  assert.equal(s.tested_hypotheses.length, cap);
  // 残るのは新しい方
  assert.equal(s.tested_hypotheses.at(-1).attempt, 40);
  assert.equal(s.tested_hypotheses[0].attempt, 40 - cap + 1);
});

test("欠陥5: 状態の大きさは試行回数に対して頭打ちになる", () => {
  const after = (n) => {
    let s = baseState();
    for (let i = 1; i <= n; i++) {
      s = applyPatch(s, { tested_hypotheses: [{ attempt: i, failed_checks: "x" }] }).state;
    }
    return { items: s.tested_hypotheses.length, chars: JSON.stringify(s).length };
  };
  const a = after(100);
  const b = after(200);
  // 件数は完全に頭打ち
  assert.equal(a.items, b.items);
  // 文字数は試行番号の桁数ぶんしか増えない（比例して伸びない）
  assert.ok(b.chars - a.chars < 30, `増分 ${b.chars - a.chars} 文字`);
  // 修正前は 30 回で初期の 7 倍に膨らんでいた。頭打ち後は 200 回でも十分小さい
  assert.ok(b.chars < 1200, `${b.chars} 文字`);
});

test("提案B: 試した仮説がコンテキストに入り、次の試行で見える", () => {
  const s = applyPatch(baseState(), {
    tested_hypotheses: [{ attempt: 1, hypothesis: "slash 形式の日付", result: "failed: date_format" }],
  }).state;
  const context = buildContext(
    { goal: "g", constraints: ["c"] },
    s,
  );
  assert.match(context, /tested_hypotheses: .*slash 形式の日付.*failed: date_format/);
});
