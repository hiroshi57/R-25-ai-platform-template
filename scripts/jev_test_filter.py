"""git diff から、実行すべきテストファイルを Jev（TypeSafe の System One API）に選ばせる（お試し・助言用）.

使い方:
    python scripts/jev_test_filter.py                 # origin/main との差分（未コミット分も含む）で選ぶ
    python scripts/jev_test_filter.py --dry-run       # 送る内容の大きさだけ確かめる（通信しない・キー不要）
    python scripts/jev_test_filter.py --json          # 結果を JSON で
    python scripts/jev_test_filter.py --base <ref>    # 比べる基準を変える

API キーは環境変数 TYPESAFE_API_KEY から読む（表示もログもしない）。

位置づけ:
- ローカルで「まず走らせるテスト」を絞るための助言。CI は今までどおり全テストを走らせる
- 迷ったら多く走らせる側に倒す。Jev が「影響なし」とはっきり言ったもの（はいの確率 0.15 以下）だけを省く
- 次の場合は Jev に聞かずに全テストにする: テスト設定・CI 設定の変更 / 認証・秘密情報などに当たるパスの変更 /
  差分に秘密らしい文字列がある / 差分が大きすぎる / API キーがない / API の失敗・答えの形の不一致
- 外部 API に送るのは、変更ファイル名・差分・テストファイル名と、その要約（docstring の1行目と、
  プロジェクト内の import 先）だけ
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = "jev-latest"
SKIP_BELOW = 0.15            # これ以下だけを「影響なし」として省く
BATCH_SIZE = 50
MAX_DIFF_CHARS = 20_000      # 大きすぎる差分は判断の精度が落ちる（Jev 1.13 の既知の弱点）ので全件にする
PROJECT_PKGS = ("core", "service", "app_template", "scripts")

RUN_ALL_PATTERNS = ("conftest.py", "*/conftest.py", "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini",
                    "requirements*.txt", ".github/workflows/*")
SECRET_PATTERNS = re.compile(
    r"AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*[\"']?[^\s\"']{8,}",
    re.IGNORECASE,
)


def _sensitive_pattern() -> re.Pattern:
    """needs_full_diff.py と同じ「機密に当たるパス」の判定を使う（2か所でずれないように）."""
    path = Path(__file__).with_name("needs_full_diff.py")
    spec = importlib.util.spec_from_file_location("needs_full_diff", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SENSITIVE


def summarize_test(path: Path) -> dict:
    """テストファイルの要約: docstring の1行目と、プロジェクト内の import 先."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    imports: list[str] = []
    for node in tree.body:
        names = []
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        for n in names:
            if n.split(".")[0] in PROJECT_PKGS and n not in imports:
                imports.append(n)
    return {"purpose": doc[0] if doc else "", "imports": imports}


def _result(mode: str, reason: str, run: list[str], skipped: list | None = None, usage: dict | None = None,
            probabilities: dict | None = None, always_run: list[str] | None = None) -> dict:
    """plan の戻り値。probabilities（テストごとのはいの確率）と always_run はシャドーモードの記録に使う."""
    return {"mode": mode, "reason": reason, "run": run, "skipped": skipped or [], "usage": usage or {},
            "probabilities": probabilities or {}, "always_run": always_run or []}


def _all(tests: list[str], reason: str) -> dict:
    return _result("all", reason, sorted(tests))


def _is_test_file(path: str) -> bool:
    return fnmatch(Path(path).name, "test_*.py")


def plan(changed: list[str], diff: str, tests: list[str], api_key: str | None, post,
         summaries: dict | None = None, skip_below: float = SKIP_BELOW, batch_size: int = BATCH_SIZE,
         max_diff_chars: int = MAX_DIFF_CHARS) -> dict:
    """実行するテストを決める。post(payload) は API を呼んで応答の dict を返す関数."""
    if not changed:
        return _result("selected", "変更なし", [])

    hit = [p for p in changed if any(fnmatch(p, pat) for pat in RUN_ALL_PATTERNS)]
    if hit:
        return _all(tests, f"テスト設定・CI 設定の変更があるため全件: {hit}")

    always = sorted({p for p in changed if _is_test_file(p) and p in tests} |
                    {p for p in changed if _is_test_file(p) and Path(p).parts[:1] == ("tests",)})
    code_changes = [p for p in changed if not _is_test_file(p)]
    if not code_changes:
        return _result("selected", "変更はテストファイルだけ", always, always_run=always)

    sensitive = [p for p in changed if _sensitive_pattern().search(p)]
    if sensitive:
        return _all(tests, f"認証・秘密情報などに当たるパスの変更があるため外部 API に送らない: {sensitive}")
    if SECRET_PATTERNS.search(diff or ""):
        return _all(tests, "差分に秘密らしい文字列があるため外部 API に送らない")
    if len(diff or "") > max_diff_chars:
        return _all(tests, f"差分が {len(diff)} 文字で {max_diff_chars} を超えるため全件")
    if not api_key:
        return _all(tests, "環境変数 TYPESAFE_API_KEY が設定されていないため全件")

    candidates = [t for t in tests if t not in always]
    probs: dict[str, float] = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        for start in range(0, len(candidates), batch_size):
            batch = candidates[start:start + batch_size]
            questions = {}
            for i, t in enumerate(batch):
                summary = (summaries.get(t) if summaries is not None else summarize_test(ROOT / t)) or {}
                questions[f"q{start + i}"] = {
                    "type": "noul",
                    "instructions": {
                        "test_file": t,
                        "test_summary": summary,
                        "question": ("Could the code change in `diff` plausibly change the result of "
                                     "the tests in `test_file`? "
                                     "Consider what `test_file` imports and exercises."),
                    },
                    "criteria": {
                        "true": "The change touches code this test exercises, directly or through imports",
                        "false": "The change is unrelated to anything this test exercises",
                    },
                }
            payload = {"model": MODEL, "state": {"changed_files": code_changes, "diff": diff},
                       "questions": questions}
            resp = post(payload)
            answers = resp.get("answers") if isinstance(resp, dict) else None
            if not isinstance(answers, dict):
                raise ValueError("answers がない")
            for qid, t in zip(questions, batch):
                a = answers.get(qid)
                p = a.get("noul") if isinstance(a, dict) and a.get("type") == "noul" else None
                if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
                    raise ValueError(f"{qid} の答えが noul の形ではない")
                probs[t] = float(p)
            for k in usage:
                usage[k] += int((resp.get("usage") or {}).get(k, 0))
    except Exception as e:  # noqa: BLE001 — どんな失敗でも全件実行に倒す
        return _all(tests, f"Jev の呼び出しに失敗したため全件（{type(e).__name__}: {e}）")

    run = sorted(set(always) | {t for t, p in probs.items() if p > skip_below})
    skipped = sorted(({"test": t, "p": round(p, 4)} for t, p in probs.items() if p <= skip_below),
                     key=lambda x: x["test"])
    return _result("selected", f"Jev のはいの確率が {skip_below} 以下のものだけ省いた", run, skipped, usage,
                   probabilities={t: round(p, 4) for t, p in probs.items()}, always_run=always)


# --- シャドーモード（テストは全部走らせ、Jev の予測と実際の結果を記録して比べる） ------------

SHADOW_DIR = "jev-shadow"                       # .claude/harness-logs/jev-shadow/<YYYYMM>/（.gitignore 対象）
SWEEP_THRESHOLDS = (0.05, 0.10, 0.15, 0.20, 0.30, 0.50)
MIN_FAILURES = 10                               # 閾値を提案するのに必要な「テストファイルの失敗」の観測数


def parse_junit(text: str) -> set[str] | None:
    """pytest の JUnit XML から、失敗またはエラーのあったテストファイルを返す。読めなければ None."""
    import xml.etree.ElementTree as ET  # noqa: S405 — 自分で走らせた pytest の出力だけを読む

    try:
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError:
        return None
    failed = set()
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            module = case.get("classname", "")
            parts = [p for p in module.split(".") if p]
            # classname は "tests.test_x" か "tests.test_x.TestClass"。
            # test_ で始まる要素までをファイルとみなす
            idx = next((i for i, p in enumerate(parts) if p.startswith("test_")), None)
            if idx is not None:
                failed.add("/".join(parts[:idx + 1]) + ".py")
    return failed


def compare(predictions: list[dict], failed: set[str], skip_below: float) -> dict:
    """予測と結果を比べる。p が None（必ず実行したテスト）は比べる対象に入れない."""
    asked = [x for x in predictions if x.get("p") is not None]
    skip = {x["test"] for x in asked if x["p"] <= skip_below}
    run = {x["test"] for x in asked if x["p"] > skip_below}
    return {"candidates": len(asked), "would_skip": len(skip),
            "missed": sorted(skip & failed),              # 省くはずだったのに落ちた（見落とし）
            "caught": sorted(run & failed)}               # 実行する側に入れていて落ちた


def sweep(records: list[dict], thresholds=SWEEP_THRESHOLDS) -> list[dict]:
    """閾値ごとに、見落とし（省いたのに落ちた）の数と、省けたテストの割合を出す."""
    usable = [r for r in records if r.get("failed_files") is not None
              and any(x.get("p") is not None for x in r.get("predictions", []))]
    rows = []
    for th in thresholds:
        missed = skipped = candidates = 0
        for r in usable:
            c = compare(r["predictions"], set(r["failed_files"]), th)
            missed += len(c["missed"])
            skipped += c["would_skip"]
            candidates += c["candidates"]
        rows.append({"threshold": th, "records": len(usable), "missed": missed,
                     "skip_rate": skipped / candidates if candidates else 0.0})   # 丸めは表示のときだけ
    return rows


def recommend(records: list[dict], min_failures: int = MIN_FAILURES, thresholds=SWEEP_THRESHOLDS) -> dict:
    """見落とし0を保てる最大の閾値を提案する。失敗の観測が少ないうちは提案しない."""
    observed = sum(len(r["failed_files"]) for r in records
                   if r.get("failed_files") and any(x.get("p") is not None for x in r.get("predictions", [])))
    rows = sweep(records, thresholds)
    if observed < min_failures:
        return {"threshold": None, "failures_observed": observed, "rows": rows,
                "reason": (f"失敗したテストファイルの観測が {observed} 件で、"
                           f"{min_failures} 件に満たないため、まだ決めない")}
    safe = [r for r in rows if r["missed"] == 0]
    if not safe:
        return {"threshold": None, "failures_observed": observed, "rows": rows,
                "reason": "どの閾値でも見落としが出るため、テストの選別には使わない"}
    best = max(safe, key=lambda r: r["threshold"])
    return {"threshold": best["threshold"], "failures_observed": observed, "rows": rows,
            "reason": f"見落とし0で省ける割合 {best['skip_rate']:.0%}"}


def shadow_run(result: dict, runner, logs_dir: Path, base: str, changed: list[str],
               skip_below: float) -> tuple[int, Path]:
    """全テストを走らせ（runner(junit_path) が終了コードを返す）、予測と結果を1件の記録として残す."""
    from datetime import datetime

    now = datetime.now()
    d = Path(logs_dir) / SHADOW_DIR / now.strftime("%Y%m")
    d.mkdir(parents=True, exist_ok=True)
    junit = d / f".junit-{now.strftime('%Y%m%d-%H%M%S-%f')}.xml"
    code = runner(junit)
    failed = parse_junit(junit.read_text(encoding="utf-8")) if junit.is_file() else None
    junit.unlink(missing_ok=True)

    predictions = ([{"test": t, "p": None} for t in result.get("always_run", [])] +
                   [{"test": t, "p": p} for t, p in sorted(result.get("probabilities", {}).items())])
    record = {
        "created_at": now.astimezone().isoformat(timespec="seconds"),
        "base": base, "changed_files": changed, "mode": result["mode"], "reason": result["reason"],
        "skip_below": skip_below, "usage": result.get("usage", {}),
        "predictions": predictions,
        "failed_files": sorted(failed) if failed is not None else None,   # None = 結果を読めなかった
        "pytest_exit_code": code,
        "comparison": compare(predictions, failed, skip_below) if failed is not None else None,
    }
    path = d / f"{now.strftime('%Y%m%d-%H%M%S-%f')}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code, path


def load_shadow_records(logs_dir: Path) -> list[dict]:
    out = []
    for f in sorted((Path(logs_dir) / SHADOW_DIR).glob("*/*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def http_post(payload: dict, api_key: str, base_url: str | None = None, timeout: float = 15.0) -> dict:
    """POST /v1/systemone。実装は core/jev_client.py に一本化している（429 / 529 は送り直す）."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from core.jev_client import post_systemone

    return post_systemone(payload, api_key=api_key, base_url=base_url, timeout=timeout)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,  # noqa: S603,S607
                          text=True, encoding="utf-8", errors="replace").stdout


def _default_base() -> str:
    for ref in ("origin/main", "origin/master", "main", "master"):
        try:
            return _git("merge-base", "HEAD", ref).strip()
        except subprocess.CalledProcessError:
            continue
    raise SystemExit("比べる基準が見つかりません。--base を指定してください")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", help="比べる基準（既定: origin/main との merge-base）")
    p.add_argument("--tests-glob", default="tests/test_*.py")
    p.add_argument("--skip-below", type=float, default=SKIP_BELOW)
    p.add_argument("--dry-run", action="store_true", help="通信せず、送る内容の大きさだけ表示する")
    p.add_argument("--json", action="store_true")
    p.add_argument("--shadow", action="store_true",
                   help="全テストを走らせ、Jev の予測と実際の結果を記録する（終了コードは pytest のもの）")
    p.add_argument("--shadow-report", action="store_true",
                   help="シャドーモードの記録を集計し、閾値ごとの見落としと省ける割合を出す")
    p.add_argument("--logs-dir", type=Path, default=ROOT / ".claude" / "harness-logs")
    a = p.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.shadow_report:
        records = load_shadow_records(a.logs_dir)
        rec = recommend(records)
        if a.json:
            print(json.dumps({"records": len(records), **rec}, ensure_ascii=False, indent=2))
            return 0
        print(f"シャドーモードの記録: {len(records)} 件"
              f"（失敗したテストファイルの観測 {rec['failures_observed']} 件）")
        for r in rec["rows"]:
            print(f"  閾値 {r['threshold']:.2f}: 見落とし {r['missed']} 件 / 省ける割合 {r['skip_rate']:.0%}")
        print(f"提案: {rec['threshold'] if rec['threshold'] is not None else 'なし'}（{rec['reason']}）")
        return 0

    base = a.base or _default_base()
    changed = [c for c in _git("diff", "--name-only", base).splitlines() if c.strip()]
    code = [c for c in changed if not _is_test_file(c)]
    diff = _git("diff", "-U3", base, "--", *code) if code else ""
    tests = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob(a.tests_glob))

    sent: list[dict] = []
    if a.dry_run:
        def post(payload):
            sent.append(payload)
            return {"answers": {q: {"type": "noul", "noul": 1.0} for q in payload["questions"]}}
        result = plan(changed, diff, tests, api_key="dry-run", post=post, skip_below=a.skip_below)
        result = {"dry_run": True, "requests": len(sent),
                  "questions": sum(len(s["questions"]) for s in sent),
                  "state_chars": sum(len(json.dumps(s["state"], ensure_ascii=False)) for s in sent),
                  "would_send": bool(sent), "reason_if_not_sent": None if sent else result["reason"]}
    else:
        key = os.environ.get("TYPESAFE_API_KEY")
        result = plan(changed, diff, tests, api_key=key, post=lambda payload: http_post(payload, key),
                      skip_below=a.skip_below)
    result["base"] = base

    if a.shadow and not a.dry_run:
        print(f"[shadow] Jev の判定: {result['mode']}（{result['reason']}）。テストは全件走らせます")

        def runner(junit_path: Path) -> int:
            cmd = [sys.executable, "-m", "pytest", f"--junitxml={junit_path}"]
            return subprocess.run(cmd, cwd=ROOT).returncode  # noqa: S603 — 自分の Python で pytest を走らせるだけ

        code_, path = shadow_run(result, runner, a.logs_dir, base, changed, a.skip_below)
        rec = json.loads(path.read_text(encoding="utf-8"))
        cmp_ = rec["comparison"]
        if cmp_ is None:
            print(f"[shadow] テスト結果を読めなかったので比較できません。記録: {path}")
        else:
            print(f"[shadow] 失敗したテストファイル {len(rec['failed_files'])} 件 / "
                  f"Jev が省くはずだった {cmp_['would_skip']} 件のうち、"
                  f"落ちたもの（見落とし） {len(cmp_['missed'])} 件")
            for m in cmp_["missed"]:
                print(f"  見落とし: {m}")
            print(f"[shadow] 記録: {path}")
        return code_

    if a.json or a.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"[{result['mode']}] {result['reason']}")
    for s in result["skipped"]:
        print(f"  省略: {s['test']}（はいの確率 {s['p']}）")
    if result["mode"] == "all":
        print("python -m pytest")
    elif result["run"]:
        print("python -m pytest " + " ".join(result["run"]))
    else:
        print("実行するテストはありません")
    if result.get("usage"):
        u = result["usage"]
        print(f"使用トークン: 入力 {u.get('input_tokens', 0)} / 出力 {u.get('output_tokens', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
