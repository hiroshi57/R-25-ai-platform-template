// verifiers.mjs — 層5 種類別 証拠ゲート
//
// 種類ごとに「証拠」を変える。判定は accept / retry / escalate の3分岐で共通。
//   CODE     : テスト/typecheck 相当
//   UI       : レイアウト要素/レスポンシブ
//   DATA     : スキーマ/鮮度/突合（ドライランでは前提条件を検証）
//   RESEARCH : 出典カバレッジ/矛盾なし
//   SUPPORT  : PII/ポリシー
//
// ヨシケイ(DATA)はドライランのため、出力の突合ではなく
// 「計画・入力・ツール配線が正しいか」を検証する（実データには触れない）。

import { existsSync } from "node:fs";
import path from "node:path";

function check(name, passed, detail = {}) {
  return passed ? { name, passed } : { name, passed, ...detail };
}

function decide(evidence) {
  const failed = evidence.filter((c) => !c.passed);
  if (failed.length === 0) return { status: "accept", evidence, failed };
  // no_new_dependency / pii_leak のような hard 失敗はローカル修復不能
  const hard = ["no_new_dependency", "pii_leak", "input_missing"];
  const repairable = failed.every((c) => !hard.includes(c.name));
  return { status: repairable ? "retry" : "escalate", evidence, failed };
}

// --- DATA（ヨシケイ CRレポート ドライラン）---
function verifyData(caseObj, ctx) {
  const root = caseObj.tool_root;
  const entry = path.join(root, caseObj.tool_entry || "");
  const args = caseObj.tool_args || {};
  const required = ["fmt", "raw", "tool"];
  const argsOk = required.every((k) => typeof args[k] === "string" && args[k].length > 0);

  const ev = [
    check("tool_exists", existsSync(entry), { entry }),
    check("command_well_formed", argsOk, { required }),
    check("inputs_declared", Object.keys(args).length >= 3, { got: Object.keys(args) }),
  ];
  // 入力の実在は「情報」として付す（ドライランでは blocking しない）
  const inputsPresent = required.every((k) => existsSync(path.join(root, args[k] || "")));
  ev.push({ name: "inputs_present", passed: true, note: inputsPresent ? "present" : "実行時に用意(ドライランでは対象外)" });
  return decide(ev);
}

// --- CODE ---
function verifyCode(caseObj, ctx) {
  const text = ctx.artifactText || "";
  const ev = [
    check("syntax_ok", ctx.syntaxOk === true, { note: "node --check" }),
    check("exports_target", new RegExp(`export\\s+function\\s+${caseObj.symbol || "\\w+"}`).test(text), {}),
  ];
  return decide(ev);
}

// --- UI ---
function verifyUi(caseObj, ctx) {
  const text = ctx.artifactText || "";
  const ev = [
    check("has_layout", /class=["'][^"']*card/.test(text), {}),
    check("responsive", /@media/.test(text), { note: "mobile viewport" }),
  ];
  return decide(ev);
}

// --- RESEARCH ---
function verifyResearch(caseObj, ctx) {
  const text = ctx.artifactText || "";
  const citations = (text.match(/\[出典/g) || []).length;
  const ev = [
    check("citations>=2", citations >= 2, { got: citations }),
    check("no_contradiction", !/\[矛盾/.test(text), {}),
  ];
  return decide(ev);
}

// --- SUPPORT ---
function verifySupport(caseObj, ctx) {
  const text = ctx.artifactText || "";
  const hasEmail = /[\w.+-]+@[\w-]+\.[\w.-]+/.test(text);
  const hasPhone = /\d{2,4}-\d{2,4}-\d{3,4}/.test(text);
  const ev = [
    check("pii_leak", !(hasEmail || hasPhone), { note: "本文に生のPIIを含めない" }),
    check("policy_disclaimer", /確認|承認/.test(text), {}),
  ];
  return decide(ev);
}

const REGISTRY = {
  DATA: verifyData,
  CODE: verifyCode,
  UI: verifyUi,
  RESEARCH: verifyResearch,
  SUPPORT: verifySupport,
};

export function verifyByType(type, caseObj, ctx = {}) {
  const fn = REGISTRY[type];
  if (!fn) return { status: "escalate", evidence: [{ name: "unknown_type", passed: false, type }], failed: [{ name: "unknown_type" }] };
  return fn(caseObj, ctx);
}
