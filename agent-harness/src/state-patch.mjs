// state-patch.mjs — 層4 状態パッチの検証とマージ（SKILL.state 方式）
//
// 着想: arXiv:2608.26263（SKILL.state）。モデルは状態を丸ごと書き直さず、差分（パッチ）だけを出す。
// パッチの検証と適用はランタイム（このファイル）が決定的に行い、モデルには任せない。
//
// ⚠️ 出典の扱い [2026-09-30]: この論文が示す効果量（「トークン 16.2 分の 1」等）は、定義上ありえない
// 挙動をするベースラインとの比であり、根拠としては採用しない。採用しているのは次の2点のみ。
//   (a) 履歴を毎回送らなければプロンプトが履歴長に依存しない、という計算量の議論
//   (b) 状態を「丸ごと書き直し」でなく「パッチ + ランタイム検証」にすると上書き事故を機械的に防げること
//
// 防いでいる失敗:
//   - 上書き事故 : パッチに書かれていないキーは必ず残る（merge）。削除は null の明示のみ。
//                  merge:"append" の配列は既定で「追加分だけ」を受け取り、既存要素を消せない。
//                  訂正が要るときだけ { $set: [...] } で明示的に置き換える。
//   - 型の取り違え: schema で型を検証する。配列は要素の型（items）まで見る。
//                  違反したら状態を一切変えずに差し戻す（rollback）。
//   - JSON 文法  : parseStepOutput が構文エラーを retry 可能なエラーとして返す。
//   - 状態の肥大 : append 配列は maxItems で頭打ちにし、古いものから捨てる。

const isPlainObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

// Worker ループの状態スキーマ（領域ごとに1回だけ定義し、タスク間で使い回す）。
//   type      : "string" | "number" | "array" | "object" | "any"
//   nullable  : true なら null（削除）を受け付ける
//   immutable : true ならパッチで変更できない（契約由来の値。同じ値の再送は許す）
//   merge     : "append" なら配列はパッチ値を末尾に追加（重複は除く）。既存要素は消えない
//   items     : 配列の要素に許す型。"object" | "string" | "number" | "any"
//   maxItems  : append 配列の上限。超えたら古いものから捨てる（状態の肥大を防ぐ）
export const WORKER_STATE_SCHEMA = {
  task_id: { type: "string", immutable: true },
  status: { type: "string" },
  completed: { type: "array", merge: "append", items: "string", maxItems: 50 },
  decisions: { type: "array", merge: "append", items: "any", maxItems: 50 },
  artifacts: { type: "array", merge: "append", items: "string", maxItems: 50 },
  open_risks: { type: "array", merge: "append", items: "string", maxItems: 50 },
  // 提案B: 試した仮説と結果。再試行時に同じ修復を繰り返さないために残す。
  // 上限は「直近の試行だけ見れば十分」という前提。超えた分は古い方から落ちる。
  tested_hypotheses: { type: "array", merge: "append", items: "object", maxItems: 20 },
  repairs: { type: "number" },
  max_repairs: { type: "number", immutable: true },
  last_evidence: { type: "any", nullable: true },
};

function typeOk(value, type) {
  if (type === "any") return value !== undefined;
  if (type === "array") return Array.isArray(value);
  if (type === "object") return isPlainObject(value);
  return typeof value === type;
}

// キーの並び順に依存しない正規化。JSON.stringify は挿入順に従うため、
// { a:1, b:2 } と { b:2, a:1 } が別物と判定されてしまう（重複除去が壊れる）。
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (isPlainObject(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

// 値の同一性（配列 append の重複除去と、immutable キーの再送判定に使う）
const same = (a, b) => canonical(a) === canonical(b);

// append 配列の指定を読み解く。
//   [...]          -> 追記
//   { $set: [...] } -> 置き換え（誤って入った要素を訂正する唯一の手段）
function readArrayPatch(key, rule, value, errors) {
  if (isPlainObject(value) && "$set" in value) {
    if (Object.keys(value).length !== 1) {
      errors.push(`${key}: $set は単独で指定する`);
      return null;
    }
    if (!Array.isArray(value.$set)) {
      errors.push(`${key}: $set には配列が必要（受け取った値: ${JSON.stringify(value.$set)}）`);
      return null;
    }
    return { mode: "set", items: value.$set };
  }
  if (!Array.isArray(value)) {
    errors.push(`型が不正: ${key} は array または { $set: [...] } が必要（受け取った値: ${JSON.stringify(value)}）`);
    return null;
  }
  return { mode: "append", items: value };
}

// 配列の要素の型を検査する。論文が挙げた失敗モード2（入れ子の型取り違え）は
// 最上位の型検査では捕まらないため、ここで要素まで見る。
function checkItems(key, rule, items, errors) {
  const itemType = rule.items ?? "any";
  items.forEach((item, i) => {
    if (!typeOk(item, itemType)) {
      errors.push(`型が不正: ${key}[${i}] は ${itemType} が必要（受け取った値: ${JSON.stringify(item) ?? "null"}）`);
    }
  });
}

function deepMerge(base, patch) {
  const out = { ...(isPlainObject(base) ? base : {}) };
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) delete out[k];
    else if (isPlainObject(v)) out[k] = deepMerge(out[k], v);
    else out[k] = v;
  }
  return out;
}

// パッチを検証して適用する。
// 戻り値: { ok: true, state } | { ok: false, state: <元の状態のまま>, errors: [...] }
// 元の state オブジェクトは変更しない（失敗時の rollback を保証するため）。
export function applyPatch(state, patch, schema = WORKER_STATE_SCHEMA) {
  const errors = [];
  if (!isPlainObject(patch)) {
    return { ok: false, state, errors: ["patch はオブジェクトである必要があります"] };
  }

  for (const [key, value] of Object.entries(patch)) {
    const rule = schema[key];
    if (!rule) {
      errors.push(`未知のキー: ${key}`);
      continue;
    }
    if (rule.immutable) {
      // 値が現状と同じなら、モデルが読んだ値をそのまま返しただけなので受け入れる。
      // ここで弾くと、無害な echo でパッチ全体が落ちて再試行が空回りする。
      if (!same(state[key], value)) errors.push(`変更不可のキー: ${key}`);
      continue;
    }
    if (value === null) {
      if (!rule.nullable) {
        errors.push(
          rule.merge === "append"
            ? `削除できないキー: ${key}（訂正するなら { $set: [...] } を使う）`
            : `削除できないキー: ${key}`,
        );
      }
      continue;
    }
    if (rule.type === "array" && rule.merge === "append") {
      const parsed = readArrayPatch(key, rule, value, errors);
      if (parsed) checkItems(key, rule, parsed.items, errors);
      continue;
    }
    if (!typeOk(value, rule.type)) {
      errors.push(`型が不正: ${key} は ${rule.type} が必要（受け取った値: ${JSON.stringify(value)}）`);
      continue;
    }
    if (rule.type === "array") checkItems(key, rule, value, errors);
  }

  if (errors.length > 0) return { ok: false, state, errors };

  const next = structuredClone(state);
  for (const [key, value] of Object.entries(patch)) {
    const rule = schema[key];
    if (rule.immutable) continue; // 検証済み: 現状と同じ値なので書かない
    if (value === null) {
      delete next[key];
    } else if (rule.type === "array" && rule.merge === "append") {
      const parsed = readArrayPatch(key, rule, value, []);
      let current = parsed.mode === "set" ? [] : Array.isArray(next[key]) ? next[key] : [];
      for (const item of parsed.items) {
        if (!current.some((c) => same(c, item))) current.push(structuredClone(item));
      }
      // 上限を超えた分は古い方から捨てる。状態が試行回数に比例して伸びるのを防ぐ。
      if (rule.maxItems && current.length > rule.maxItems) {
        current = current.slice(current.length - rule.maxItems);
      }
      next[key] = current;
    } else if (rule.type === "object") {
      next[key] = deepMerge(next[key], value);
    } else {
      next[key] = structuredClone(value);
    }
  }
  return { ok: true, state: next };
}

// 差し戻されたパッチを作り直させて再試行する。
//
// 検証に落ちたパッチは状態を変えない（applyPatch の保証）。そのうえで、落ちた理由を
// 作り手に返してもう一度作らせる。本物のモデルを繋ぐときは buildPatch の中で
// モデルを呼び、errors をプロンプトに入れる。
//
//   buildPatch(attempt, errors) -> patch
//     attempt : 1 から始まる試行回数
//     errors  : 前回の差し戻し理由（初回は []）
//
// 戻り値: { ok, state, attempts, errors }
//   ok:false のとき state は呼び出し時のまま（部分適用しない）。例外は投げない。
//   既定の maxAttempts は 3（「同じ原因の自動修正は最大3回」に合わせる）。
export function applyPatchWithRetry(state, buildPatch, { maxAttempts = 3, schema = WORKER_STATE_SCHEMA } = {}) {
  let errors = [];
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    let patch;
    try {
      patch = buildPatch(attempt, errors);
    } catch (e) {
      // 作り手が落ちても状態は壊さない。理由を残して次の試行へ。
      errors = [`パッチ生成で例外: ${e.message}`];
      continue;
    }
    const result = applyPatch(state, patch, schema);
    if (result.ok) return { ok: true, state: result.state, attempts: attempt, errors: [] };
    errors = result.errors;
  }
  return { ok: false, state, attempts: maxAttempts, errors };
}

// モデル出力から ```json ブロックを取り出し、{ state_patch, action } を返す。
// 推論部分（ブロックの外側）は返さない = 次のプロンプトに持ち越さない。
// 戻り値: { ok: true, state_patch, action } | { ok: false, errors: [...] }
export function parseStepOutput(text) {
  const match = /```json\s*([\s\S]*?)```/.exec(String(text));
  if (!match) return { ok: false, errors: ["```json ブロックがありません"] };

  let parsed;
  try {
    parsed = JSON.parse(match[1]);
  } catch (e) {
    return { ok: false, errors: [`JSON 構文エラー: ${e.message}`] };
  }
  if (!isPlainObject(parsed)) return { ok: false, errors: ["JSON のトップレベルはオブジェクトが必要"] };

  const keys = Object.keys(parsed).sort();
  if (keys.length !== 2 || keys[0] !== "action" || keys[1] !== "state_patch") {
    return { ok: false, errors: [`キーは state_patch と action の2つだけが必要（受け取ったキー: ${keys.join(", ")}）`] };
  }
  if (!isPlainObject(parsed.state_patch)) return { ok: false, errors: ["state_patch はオブジェクトが必要"] };
  if (typeof parsed.action !== "string") return { ok: false, errors: ["action は文字列が必要"] };

  return { ok: true, state_patch: parsed.state_patch, action: parsed.action };
}
