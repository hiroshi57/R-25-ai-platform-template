// model.mjs — モデルの代役（決定的スタブ）
//
// 本物のモデルは確率的だが、ここでは検証可能なように決定的にふるまう。
// モデルは2つの情報を *コンテキストから* 読む。どちらが欠けても修復が止まる。
//
//   1. last_evidence      : 直さなければならないか（失敗の証拠）
//      - date_format 失敗が見えていない → 最初の候補のまま
//      - date_format 失敗が見えている   → 別の候補へ進む
//   2. tested_hypotheses  : 次に何を試すか（すでに試して失敗した候補）
//      - 見えていれば、失敗済みの候補を飛ばして次へ進む
//      - 見えていなければ、同じ候補を選び直して同じ失敗を繰り返す
//
// 1 を消すと「証拠が戻らなければ修復できない」、2 を消すと「何を試したか覚えていなければ
// 同じ失敗を繰り返す」がそれぞれ観測できる（README の「★自分で壊して学ぶ」）。

// 日付形式の候補。モデルはこの順に試す。正解を直接知っているわけではない。
const DATE_MODES = ["slash", "iso"];

const SOURCE = [
  { date: "2026-09-11", region: "tokyo", revenue: 120 },
  { date: "2026-09-11", region: "osaka", revenue: 80 },
  { date: "2026-09-12", region: "tokyo", revenue: 95 },
];

function renderDate(iso, mode) {
  return mode === "iso" ? iso : iso.replaceAll("-", "/");
}

export function buildCsv(mode) {
  const header = "date,region,revenue";
  const rows = SOURCE.map((r) => `${renderDate(r.date, mode)},${r.region},${r.revenue}`);
  return [header, ...rows].join("\n") + "\n";
}

// コンテキスト文字列に「date_format の失敗証拠」が含まれているか。
// これが見えて初めてモデルは ISO へ切り替える。
function sawDateFailure(context) {
  return /"name":"date_format","passed":false/.test(context);
}

// コンテキストに載った tested_hypotheses から、すでに試して失敗した候補を読む。
// 壊れた JSON でも落ちない（読めなければ「何も試していない」と同じ扱いにする）。
function failedDateModes(context) {
  const line = /tested_hypotheses: (.*)/.exec(context);
  if (!line) return [];
  try {
    const entries = JSON.parse(line[1]);
    if (!Array.isArray(entries)) return [];
    return entries.map((e) => e?.hypothesis?.date_mode).filter(Boolean);
  } catch {
    return [];
  }
}

export function propose(context, state) {
  if (!state.completed.includes("verification")) {
    // 証拠が無いうちは最初の候補。証拠が見えたら、まだ試していない候補へ進む。
    // 候補を使い切ったら最後の候補に留まる（提案が無限に増えないようにする）。
    const failed = failedDateModes(context);
    const mode = sawDateFailure(context)
      ? (DATE_MODES.find((m) => !failed.includes(m)) ?? DATE_MODES.at(-1))
      : DATE_MODES[0];
    return {
      name: "write_workspace",
      args: { path: "artifacts/export.csv", content: buildCsv(mode) },
      // ランタイムが tested_hypotheses に記録するための「何を試したか」
      hypothesis: { date_mode: mode },
    };
  }
  // 検証を通過したら、成果を送る（送信は承認が要る＝層3で止まる）
  return {
    name: "send_message",
    args: { final_content_preview: "CSV エクスポートを追加しました（artifacts/export.csv）" },
  };
}
