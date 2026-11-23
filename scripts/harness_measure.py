#!/usr/bin/env python3
"""harness_measure.py — ハーネス構成の「対応のある比較」と在庫サマリ.

harness_log.py が保存したログを読み、
  summary … いま何件・どの構成が溜まっているか（対応比較ができる状態か）
  compare … ある部品（toolset など）の2値を、同じタスクで対応比較する
を出す。採否の判断はしない。数値を並べるだけ（decision-boundaries）。

根拠ルール: harness-components ルール1（部品は1つずつ・同じタスク群・対応比較・平均で判断しない）、
            harness-retro 提案12/15（ノイズ下限 / Application・Transfer 分離）。

読むログの配置（harness_log.py save）:
  <logs_dir>/<project>/<YYYYMM>/<task_id>/
    worker-report{s}.json / review{s}.json / harness-config{s}.json / tool-usage.measured{s}.json
  1回の run = 1つの suffix s（''/'.2'/'.3'...）。harness-config{s}.json がある suffix だけを run とみなす。

メトリクス（--metric。既定 tokens）:
  tokens         … tool-usage.measured{s}.json の tokens_total（実測。self-report ではない）
  calls          … measured の read+search+bash+edit の合計（実測）
  largest_output … measured の largest_output_lines
  reread         … measured の reread_files の件数
score:
  review{s}.json から。数値(score/overall/average) > 観点別スコア平均 > 判定(APPROVE=1/REQUEST_CHANGES=0)

使い方:
  python harness_measure.py summary <logs_dir>/<project>
  python harness_measure.py compare <logs_dir>/<project> --component toolset --a predefined --b action-fusion
         [--model <id>] [--metric tokens|calls|largest_output|reread]
"""
import argparse
import json
import re
import statistics
import sys
from pathlib import Path

CONFIG_KEYS = ("model", "toolset", "planning", "context_policy")
METRICS = ("tokens", "calls", "largest_output", "reread")
_CFG_RE = re.compile(r"^harness-config(\.\d+)?\.json$")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def task_dirs(project_root: Path):
    """<YYYYMM>/<task_id> の2階層を昇順で返す。harness-config を1つ以上持つものだけ。"""
    if not project_root.exists():
        return []
    out = []
    for month in sorted(p for p in project_root.iterdir() if p.is_dir()):
        for task in sorted(p for p in month.iterdir() if p.is_dir()):
            if any(_CFG_RE.match(f.name) for f in task.iterdir() if f.is_file()):
                out.append(task)
    return out


def _suffix(cfg_name: str) -> str:
    """harness-config.2.json → '.2'、harness-config.json → ''."""
    m = _CFG_RE.match(cfg_name)
    return (m.group(1) or "") if m else ""


def extract_score(review):
    if not isinstance(review, dict):
        return None
    for key in ("score", "overall", "average"):
        v = review.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    for key in ("観点別スコア", "scores", "criteria"):
        v = review.get(key)
        if isinstance(v, dict):
            nums = [x for x in v.values() if isinstance(x, (int, float))]
            if nums:
                return statistics.mean(nums)
    verdict = review.get("verdict") or review.get("decision") or review.get("route")
    if isinstance(verdict, str):
        u = verdict.upper()
        if "APPROVE" in u:
            return 1.0
        if "REQUEST_CHANGES" in u or "REQUEST-CHANGES" in u or "REJECT" in u:
            return 0.0
    return None


def extract_cost(measured, metric):
    if not isinstance(measured, dict):
        return None
    if metric == "tokens":
        v = measured.get("tokens_total")
        return float(v) if isinstance(v, (int, float)) else None
    if metric == "calls":
        keys = ("read_calls", "search_calls", "bash_calls", "edit_calls")
        nums = [measured[k] for k in keys if isinstance(measured.get(k), (int, float))]
        return float(sum(nums)) if nums else None
    if metric == "largest_output":
        v = measured.get("largest_output_lines")
        return float(v) if isinstance(v, (int, float)) else None
    if metric == "reread":
        v = measured.get("reread_files")
        return float(len(v)) if isinstance(v, list) else None
    return None


def read_runs(task_dir: Path, metric: str):
    """1つの task_id ディレクトリから、suffix ごとの run を返す。"""
    runs = []
    for f in sorted(task_dir.iterdir()):
        if not (f.is_file() and _CFG_RE.match(f.name)):
            continue
        s = _suffix(f.name)
        cfg = load_json(f) or {}
        config = {k: cfg.get(k) for k in CONFIG_KEYS}
        review = load_json(task_dir / f"review{s}.json")
        measured = load_json(task_dir / f"tool-usage.measured{s}.json")
        runs.append({
            "task_id": task_dir.name,
            "config": config,
            "score": extract_score(review),
            "cost": extract_cost(measured, metric),
        })
    return runs


def all_runs(project_root: Path, metric: str):
    out = []
    for d in task_dirs(project_root):
        out.extend(read_runs(d, metric))
    return out


def _other_keys(component):
    return tuple(k for k in CONFIG_KEYS if k != component)


def _pairable_count(runs, component):
    buckets = {}
    for r in runs:
        fixed = tuple(r["config"].get(k) for k in _other_keys(component))
        buckets.setdefault((r["task_id"], fixed), set()).add(r["config"].get(component))
    return sum(1 for vals in buckets.values() if len([v for v in vals if v is not None]) >= 2)


def summary(project_root: Path):
    runs = all_runs(project_root, "tokens")
    print(f"[harness-measure] project: {project_root.as_posix()}")
    print(f"[harness-measure] run 総数: {len(runs)}")
    if not runs:
        print("  まだ harness-config 付きの run がありません。")
        print("  harness_log.py save に --harness-config を付けて記録してください。")
        print("  対応比較の開始条件: 同じ部品の2値について、対になるタスクが 20 件以上。")
        return 0
    for key in CONFIG_KEYS:
        vals = {}
        for r in runs:
            v = r["config"].get(key)
            vals[v] = vals.get(v, 0) + 1
        print(f"  {key}: " + ", ".join(f"{k}×{n}" for k, n in sorted(vals.items(), key=lambda x: str(x[0]))))
    task_ids = {r["task_id"] for r in runs}
    with_score = sum(1 for r in runs if r["score"] is not None)
    with_cost = sum(1 for r in runs if r["cost"] is not None)
    print(f"  論理タスク数(distinct task_id): {len(task_ids)}")
    print(f"  score 取得済み: {with_score} / cost(tokens) 取得済み: {with_cost}")
    print("  対応比較の見込み(部品ごと):")
    for key in CONFIG_KEYS:
        pairs = _pairable_count(runs, key)
        flag = "✓ 20件到達" if pairs >= 20 else f"あと {max(0, 20 - pairs)} 件"
        print(f"    {key}: 対になるタスク {pairs} 件（{flag}）")
    return 0


def compare(project_root: Path, component, a, b, model=None, metric="tokens"):
    runs = all_runs(project_root, metric)
    if model is not None:
        runs = [r for r in runs if r["config"].get("model") == model]
    cells = {}
    for r in runs:
        cval = r["config"].get(component)
        if cval not in (a, b):
            continue
        fixed = tuple(r["config"].get(k) for k in _other_keys(component))
        cells.setdefault((r["task_id"], fixed), {}).setdefault(cval, []).append(r)

    rows = []
    for (task_id, _f), byval in cells.items():
        if a not in byval or b not in byval:
            continue

        def cell(val, field):
            xs = [r[field] for r in byval[val] if r[field] is not None]
            return statistics.median(xs) if xs else None
        sa, sb = cell(a, "score"), cell(b, "score")
        ca, cb = cell(a, "cost"), cell(b, "cost")
        rows.append({"task_id": task_id, "score_a": sa, "score_b": sb, "cost_a": ca, "cost_b": cb,
                     "d_score": (sb - sa) if (sa is not None and sb is not None) else None,
                     "d_cost": (cb - ca) if (ca is not None and cb is not None) else None})
    _print_compare(component, a, b, model, metric, rows)
    return 0


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _print_compare(component, a, b, model, metric, rows):
    print(f"[harness-measure] compare: {component} = '{a}' vs '{b}'" + (f"  (model={model})" if model else ""))
    print(f"  メトリクス: score=review由来 / cost={metric}（b − a で正なら b が増加）")
    print(f"  対になったタスク数: {len(rows)}")
    if not rows:
        print("  対がありません。同じタスクを両方の構成で記録してください。")
        return
    print(f"  {'task_id':<28} {'Δscore':>8} {'Δcost':>12}")
    for r in sorted(rows, key=lambda x: str(x["task_id"])):
        ds = "-" if r["d_score"] is None else f"{r['d_score']:+.3f}"
        dc = "-" if r["d_cost"] is None else f"{r['d_cost']:+.1f}"
        print(f"  {str(r['task_id']):<28} {ds:>8} {dc:>12}")
    md_s, md_c = _med([r["d_score"] for r in rows]), _med([r["d_cost"] for r in rows])
    n_s = len([r for r in rows if r["d_score"] is not None])
    n_c = len([r for r in rows if r["d_cost"] is not None])
    print(f"  --- 中央値 Δscore = {('-' if md_s is None else f'{md_s:+.3f}')} (n={n_s}) / "
          f"中央値 Δcost = {('-' if md_c is None else f'{md_c:+.1f}')} (n={n_c})")
    if min(n_s, n_c) < 20:
        print(f"  目安: 件数不足(対 {min(n_s, n_c)} 件 < 20)。harness-components ルール1 により差を結論にしない")
    elif md_c is not None and md_c < 0 and md_s is not None and md_s >= 0:
        print("  目安: b はコスト減かつスコア非悪化。受理ゲート(提案12/15)で δ と Transfer を確認")
    elif md_c is not None and md_c >= 0:
        print("  目安: b でコストは減っていない")
    elif md_s is not None and md_s < 0:
        print("  目安: b でスコアが下がっている")


def parse_args(argv):
    ap = argparse.ArgumentParser(description="ハーネス構成の対応比較と在庫サマリ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary")
    s.add_argument("project_root")
    c = sub.add_parser("compare")
    c.add_argument("project_root")
    c.add_argument("--component", required=True, choices=CONFIG_KEYS)
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    c.add_argument("--model", default=None)
    c.add_argument("--metric", default="tokens", choices=METRICS)
    return ap.parse_args(argv)


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    if args.cmd == "summary":
        return summary(Path(args.project_root))
    return compare(Path(args.project_root), args.component, args.a, args.b,
                   model=args.model, metric=args.metric)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
