// classifier.mjs — 層0 分類器（トリアージ）
//
// 案件(複雑な処理)を受け取り、2軸+分割可否で仕分ける。
//   - type: CODE / UI / DATA / RESEARCH / SUPPORT （層5の検証を切替）
//   - risk: low / med / high                       （層3の承認要否を切替）
//   - split: 複雑な案件はサブタスクへ分割           （層4で進捗を保持）
// 決定的なキーワード規則で判定する（説明可能・再現可能）。

// 判定順に意味がある（先にマッチした type を採用）
const TYPE_RULES = [
  ["SUPPORT", ["サポート", "問い合わせ", "返信", "顧客対応"]],
  ["RESEARCH", ["調査", "リサーチ", "出典", "competitor", "比較"]],
  ["UI", ["ui", "画面", "ダッシュボード", "レイアウト", "スクショ", "card"]],
  ["DATA", ["csv", "excel", "データ", "スキーマ", "レポート", "集計", "export"]],
  ["CODE", ["実装", "関数", "util", "バグ", "api", "リファクタ"]],
];

const RISK_HIGH = ["送信", "本番", "デプロイ", "削除", "個人情報", "pii", "決済", "顧客データ"];
const RISK_MED = ["外部", "公開", "スキーマ変更", "メール", "書き込み", "顧客提出"];

function haystack(c) {
  return [c.title, c.goal, ...(c.tags || [])].join(" ").toLowerCase();
}

function detectType(h) {
  for (const [type, kws] of TYPE_RULES) {
    if (kws.some((k) => h.includes(k.toLowerCase()))) return type;
  }
  return "CODE"; // 既定
}

function detectRisk(h, c) {
  // final_action が破壊的なら底上げ
  const destructive = ["run_orchestrator", "deploy", "send_message", "delete_data"];
  if (c.final_action && destructive.includes(c.final_action)) return "high";
  if (RISK_HIGH.some((k) => h.includes(k.toLowerCase()))) return "high";
  if (RISK_MED.some((k) => h.includes(k.toLowerCase()))) return "med";
  return "low";
}

function detectSplit(h, c) {
  if (Array.isArray(c.subtasks) && c.subtasks.length > 1) return true;
  if (Array.isArray(c.phases) && c.phases.length > 1) return true;
  return h.includes("complex") || h.includes("複雑");
}

export function classify(c) {
  const h = haystack(c);
  const type = detectType(h);
  const risk = detectRisk(h, c);
  const split = detectSplit(h, c);
  const units = split ? (c.subtasks || c.phases || []) : [];
  return {
    type,
    risk,
    split,
    complexity: split ? "high" : "low",
    units,
    reason: {
      type: `keyword-match(${type})`,
      risk: c.final_action ? `final_action=${c.final_action}` : `signal(${risk})`,
      split: split ? `${units.length} 単位に分割` : "単一タスク",
    },
  };
}
