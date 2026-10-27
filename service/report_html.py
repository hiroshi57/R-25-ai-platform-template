"""観測性 HTMLレポート(標準ライブラリのみ).

セキュリティ: 埋め込む値はすべて html.escape する。旧実装は provider 名だけを
エスケープし mode 列は素通しだったため、カタログ由来の文字列が将来
外部入力になった時点で XSS になりうる状態だった。
"""
from __future__ import annotations

import html
from datetime import datetime, timezone


def _e(v) -> str:
    return html.escape(str(v), quote=True)


def _fmt_usd(v: float) -> str:
    """極小コストが $0.0000 と表示されて「無料」に見えるのを避ける."""
    v = float(v or 0)
    if 0 < v < 0.0001:
        return "&lt; $0.0001"
    return f"${v:,.4f}"


def build_html_report(summary: dict, provider_modes: dict[str, str] | None = None,
                      tenant: str = "-", unverified: list[str] | None = None) -> str:
    modes = provider_modes or {}
    prov_rows = ""
    total_cost = float(summary.get("total_cost_usd", 0) or 0)
    by_provider = summary.get("by_provider", {}) or {}
    for name, s in sorted(by_provider.items(), key=lambda kv: -kv[1].get("cost_usd", 0)):
        cost = float(s.get("cost_usd", 0) or 0)
        share = (cost / total_cost * 100) if total_cost > 0 else 0.0
        prov_rows += (
            f'<tr><td>{_e(name)}</td><td><span class="tag {_e(modes.get(name, "-"))}">'
            f'{_e(modes.get(name, "-"))}</span></td>'
            f'<td class="num">{_e(s.get("count", 0))}</td>'
            f'<td class="num">{_e(s.get("input_tokens", 0))}</td>'
            f'<td class="num">{_e(s.get("output_tokens", 0))}</td>'
            f'<td class="num">{_fmt_usd(cost)}</td>'
            f'<td class="num">{share:.1f}%</td></tr>')
    if not prov_rows:
        prov_rows = '<tr><td colspan="7" class="empty">まだ記録がありません</td></tr>'

    mock_names = [n for n, m in modes.items() if m == "mock"]
    banners = ""
    if mock_names:
        banners += (f'<div class="banner warn">⚠ {_e(", ".join(sorted(mock_names)))} は '
                    f'<b>mock</b> で動作中です。表示されているコスト・レイテンシは'
                    f'実際の API 実測値ではありません。</div>')
    if unverified:
        banners += (f'<div class="banner warn">⚠ {_e(", ".join(sorted(unverified)))} の単価は'
                    f'一次情報で未検証の初期値です。<code>AI_PLATFORM_PRICING_FILE</code> で'
                    f'実単価を注入してください。</div>')

    err_rate = float(summary.get("error_rate", 0) or 0) * 100
    p50 = float(summary.get("p50_latency_ms", 0) or 0)
    p95 = float(summary.get("p95_latency_ms", 0) or 0)
    p99 = float(summary.get("p99_latency_ms", 0) or 0)
    fallback_rate = float(summary.get("fallback_rate", 0) or 0) * 100
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI基盤 観測性レポート</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:24px;color:#1a1a2e;background:#fafbff}}
h1{{color:#4361ee;margin-bottom:4px}} .sub{{color:#666;font-size:13px;margin-bottom:16px}}
table{{border-collapse:collapse;width:100%;background:#fff}}
th,td{{border:1px solid #dde;padding:8px 10px;text-align:left}} th{{background:#eaeefb}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.empty{{text-align:center;color:#888}}
.metrics{{display:flex;gap:12px;margin:12px 0;flex-wrap:wrap}}
.metric{{flex:1;min-width:140px;background:#fff;border:1px solid #e3e7f5;border-radius:8px;
padding:12px;text-align:center}}
.val{{font-size:24px;font-weight:bold;color:#4361ee}}
.banner{{padding:10px 12px;border-radius:8px;margin:8px 0;font-size:13px}}
.banner.warn{{background:#fff6e0;border:1px solid #f0c36d}}
.tag{{padding:2px 8px;border-radius:10px;font-size:12px;background:#e8f0fe}}
.tag.mock{{background:#fdecea;color:#b3261e}} .tag.real{{background:#e6f4ea;color:#137333}}
</style></head><body>
<h1>AI基盤 観測性レポート</h1>
<div class="sub">テナント: <b>{_e(tenant)}</b> ／ 生成: {_e(generated)}</div>
{banners}
<div class="metrics">
  <div class="metric"><div>総リクエスト</div><div class="val">{_e(summary.get("count", 0))}</div></div>
  <div class="metric"><div>総コスト(USD)</div><div class="val">{_fmt_usd(total_cost)}</div></div>
  <div class="metric"><div>p50レイテンシ</div><div class="val">{p50:.0f}ms</div></div>
  <div class="metric"><div>p95レイテンシ</div><div class="val">{p95:.0f}ms</div></div>
  <div class="metric"><div>p99レイテンシ</div><div class="val">{p99:.0f}ms</div></div>
  <div class="metric"><div>フォールバック率</div><div class="val">{fallback_rate:.1f}%</div></div>
  <div class="metric"><div>エラー率</div><div class="val">{err_rate:.1f}%</div></div>
</div>
<h2>プロバイダ別</h2>
<table>
<tr><th>プロバイダ</th><th>mode</th><th>件数</th><th>入力トークン</th><th>出力トークン</th><th>コスト</th><th>コスト比</th></tr>
{prov_rows}
</table>
<p class="sub">レイテンシ統計は成功リクエストのみで算出しています(失敗は latency=0 のため分布を歪めます)。</p>
</body></html>"""
