// policy.mjs — 層3 リスク別 権限ラダー
//
// リスクが高いほど摩擦を上げる。危険な結果を持つ操作は自動実行させない。
// （記事: 公開文書を読むことと顧客データに触れることは、同じ承認フローを通さない）

const LADDER = {
  low: "automatic",   // 影響が小さい: 自動
  med: "automatic",   // 中程度: 自動だが注記を残す
  high: "approval",   // 破壊的/顧客データ/送信/本番: 人間承認が必要
};

export function permissionFor(risk) {
  return LADDER[risk] || "approval";
}

export function isNoticed(risk) {
  return risk === "med";
}
