// patch-retry.test.mjs — 差し戻されたパッチを再試行する（A-1）
//
// ルールは「検証に落ちたパッチは状態を変えずに差し戻す。そのうえで再試行させる」と定めている。
// 参照実装は差し戻しまでは行っていたが、再試行せずに例外で停止していた。ここを埋める。

import { test } from "node:test";
import assert from "node:assert/strict";
import { applyPatchWithRetry } from "../src/state-patch.mjs";
import { initState } from "../src/state.mjs";

const baseState = () => initState({ task_id: "feature_042", max_repairs: 1 });

test("1回目が落ちても、エラーを見て直した2回目で成功する", () => {
  const seen = [];
  const r = applyPatchWithRetry(baseState(), (attempt, errors) => {
    seen.push({ attempt, errors });
    // 1回目はわざと型違反。2回目で直す
    return attempt === 1 ? { repairs: "1" } : { repairs: 1 };
  });

  assert.equal(r.ok, true);
  assert.equal(r.attempts, 2);
  assert.equal(r.state.repairs, 1);
  // 1回目の失敗内容が2回目に渡っている
  assert.deepEqual(seen[0].errors, []);
  assert.match(seen[1].errors[0], /型が不正: repairs/);
});

test("落ちた試行は状態を変えない（部分適用もしない）", () => {
  const before = baseState();
  const snapshot = structuredClone(before);
  const r = applyPatchWithRetry(before, (attempt) =>
    attempt === 1 ? { status: "verified", repairs: "x" } : { status: "verified" },
  );
  assert.equal(r.ok, true);
  assert.equal(r.state.status, "verified");
  // 呼び出し元の state は書き換えない
  assert.deepEqual(before, snapshot);
});

test("上限まで落ち続けたら失敗を返す（例外を投げない）", () => {
  const before = baseState();
  const snapshot = structuredClone(before);
  let calls = 0;
  const r = applyPatchWithRetry(
    before,
    () => {
      calls++;
      return { repairs: "ずっと型違反" };
    },
    { maxAttempts: 3 },
  );

  assert.equal(r.ok, false);
  assert.equal(r.attempts, 3);
  assert.equal(calls, 3);
  assert.ok(r.errors.length > 0);
  assert.deepEqual(r.state, snapshot, "失敗時は元の状態をそのまま返す");
});

test("1回で通れば再試行しない", () => {
  let calls = 0;
  const r = applyPatchWithRetry(baseState(), () => {
    calls++;
    return { status: "verified" };
  });
  assert.equal(r.ok, true);
  assert.equal(r.attempts, 1);
  assert.equal(calls, 1);
});

test("maxAttempts の既定は3（同じ原因の自動修正は3回まで）", () => {
  let calls = 0;
  applyPatchWithRetry(baseState(), () => {
    calls++;
    return { 未知のキー: 1 };
  });
  assert.equal(calls, 3);
});

test("パッチの作り手が例外を投げても、状態を壊さず失敗として返す", () => {
  const before = baseState();
  const snapshot = structuredClone(before);
  const r = applyPatchWithRetry(before, () => {
    throw new Error("パッチ生成に失敗");
  });
  assert.equal(r.ok, false);
  assert.match(r.errors[0], /パッチ生成に失敗/);
  assert.deepEqual(r.state, snapshot);
});
