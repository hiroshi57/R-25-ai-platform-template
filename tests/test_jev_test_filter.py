"""scripts/jev_test_filter.py のテスト（ネットワークには出ない。API 呼び出しは差し替える）."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("jev_test_filter", ROOT / "scripts" / "jev_test_filter.py")
jtf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jtf)

TESTS = ["tests/test_router.py", "tests/test_finops.py", "tests/test_notify_api.py"]
DIFF = "diff --git a/core/router.py b/core/router.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


def _answers(ps: dict[str, float]) -> dict:
    return {"model": "jev-1.13.0", "usage": {"input_tokens": 100, "output_tokens": 10},
            "answers": {qid: {"type": "noul", "noul": p} for qid, p in ps.items()}}


def _plan(changed, diff=DIFF, tests=TESTS, post=None, api_key="k", **kw):
    return jtf.plan(changed, diff, tests, api_key=api_key, post=post, summaries={}, **kw)


# --- コードで先に決めるもの（Jev には聞かない） ----------------------------------------

@pytest.mark.parametrize("path", ["tests/conftest.py", "pyproject.toml", "requirements.txt",
                                  ".github/workflows/ci.yml", "pytest.ini"])
def test_config_changes_run_everything_without_asking(path):
    called = []
    r = _plan([path], post=lambda payload: called.append(payload))
    assert r["mode"] == "all" and called == [] and path in r["reason"]


def test_changed_test_files_are_always_run():
    r = _plan(["tests/test_finops.py", "core/router.py"],
              post=lambda p: _answers({qid: 0.0 for qid in p["questions"]}))
    assert "tests/test_finops.py" in r["run"]
    assert all(s["test"] != "tests/test_finops.py" for s in r["skipped"])


def test_only_test_changes_need_no_api_call():
    called = []
    r = _plan(["tests/test_router.py"], post=lambda p: called.append(p))
    assert called == [] and r["mode"] == "selected" and r["run"] == ["tests/test_router.py"]


def test_sensitive_paths_are_never_sent():
    called = []
    r = _plan(["service/auth.py"], post=lambda p: called.append(p))
    assert called == [] and r["mode"] == "all" and "送らない" in r["reason"]


@pytest.mark.parametrize("secret", ["AKIAABCDEFGHIJKLMNOP", "-----BEGIN PRIVATE KEY-----",
                                    'api_key = "sk-live-0123456789abcdef"', "password: hunter2hunter2"])
def test_secret_looking_diff_is_never_sent(secret):
    called = []
    r = _plan(["core/router.py"], diff=DIFF + f"+{secret}\n", post=lambda p: called.append(p))
    assert called == [] and r["mode"] == "all"


def test_missing_api_key_runs_everything():
    r = _plan(["core/router.py"], api_key=None, post=lambda p: pytest.fail("呼ばれてはいけない"))
    assert r["mode"] == "all" and "TYPESAFE_API_KEY" in r["reason"]


def test_too_large_diff_runs_everything():
    r = _plan(["core/router.py"], diff="+" + "x" * 50, max_diff_chars=10,
              post=lambda p: pytest.fail("呼ばれてはいけない"))
    assert r["mode"] == "all"


def test_no_changes_runs_nothing():
    r = _plan([], diff="", post=lambda p: pytest.fail("呼ばれてはいけない"))
    assert r["mode"] == "selected" and r["run"] == []


# --- Jev に聞くもの --------------------------------------------------------------

def test_request_shape_is_one_call_with_one_noul_per_test():
    seen = {}

    def post(payload):
        seen.update(payload)
        return _answers({qid: 0.9 for qid in payload["questions"]})

    _plan(["core/router.py"], post=post)
    assert seen["model"] == "jev-latest"
    assert set(seen["state"]) == {"changed_files", "diff"}
    qs = list(seen["questions"].values())
    assert len(qs) == len(TESTS) and all(q["type"] == "noul" for q in qs)
    assert {q["instructions"]["test_file"] for q in qs} == set(TESTS)


def test_skips_only_clear_no_and_reports_probabilities():
    def post(payload):
        ids = {q["instructions"]["test_file"]: qid for qid, q in payload["questions"].items()}
        return _answers({ids["tests/test_router.py"]: 0.92, ids["tests/test_finops.py"]: 0.40,
                         ids["tests/test_notify_api.py"]: 0.05})

    r = _plan(["core/router.py"], post=post)
    assert r["mode"] == "selected"
    assert r["run"] == ["tests/test_finops.py", "tests/test_router.py"]          # 0.40 は迷い → 実行する
    assert r["skipped"] == [{"test": "tests/test_notify_api.py", "p": 0.05}]
    assert r["usage"]["input_tokens"] == 100


def test_large_test_sets_are_split_into_batches():
    tests = [f"tests/test_{i}.py" for i in range(7)]
    calls = []

    def post(payload):
        calls.append(len(payload["questions"]))
        return _answers({qid: 0.9 for qid in payload["questions"]})

    r = jtf.plan(["core/router.py"], DIFF, tests, api_key="k", post=post, summaries={}, batch_size=3)
    assert calls == [3, 3, 1] and len(r["run"]) == 7


@pytest.mark.parametrize("bad", [
    RuntimeError("HTTP 529"),
    {"answers": {}},                                            # 答えが足りない
    {"answers": {"q0": {"type": "choice"}}},                    # 型が違う
])
def test_any_api_problem_falls_back_to_running_everything(bad):
    def post(payload):
        if isinstance(bad, Exception):
            raise bad
        return bad

    r = _plan(["core/router.py"], post=post)
    assert r["mode"] == "all" and set(r["run"]) == set(TESTS)


def test_api_key_never_appears_in_output(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "super-secret-key-value")
    r = _plan(["core/router.py"], api_key="super-secret-key-value",
              post=lambda p: _answers({qid: 0.9 for qid in p["questions"]}))
    assert "super-secret-key-value" not in json.dumps(r)


def test_summary_reads_docstring_and_project_imports(tmp_path):
    f = tmp_path / "test_x.py"
    f.write_text('"""ルータの選択ロジックのテスト."""\nimport json\nfrom core.router import Router\n'
                 'from service import api\n', encoding="utf-8")
    s = jtf.summarize_test(f)
    assert s["purpose"] == "ルータの選択ロジックのテスト."
    assert s["imports"] == ["core.router", "service"]


# --- シャドーモード（テストは全部走らせ、予測と結果を記録して比べる） ------------------

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4" failures="1" errors="1">
  <testcase classname="tests.test_router" name="test_ok" time="0.01"/>
  <testcase classname="tests.test_router" name="test_bad" time="0.01"><failure message="boom"/></testcase>
  <testcase classname="tests.test_finops" name="test_ok" time="0.01"/>
  <testcase classname="tests.test_notify_api" name="test_err" time="0.01"><error message="setup"/></testcase>
</testsuite></testsuites>"""


def test_parse_junit_returns_failed_test_files():
    assert jtf.parse_junit(JUNIT) == {"tests/test_router.py", "tests/test_notify_api.py"}


def test_parse_junit_tolerates_broken_xml():
    assert jtf.parse_junit("<not xml") is None


def _pred(**ps):
    return [{"test": f"tests/test_{k}.py", "p": v} for k, v in ps.items()]


def test_compare_counts_misses_when_skipped_test_failed():
    preds = _pred(router=0.9, finops=0.05, notify_api=0.10)
    out = jtf.compare(preds, {"tests/test_router.py", "tests/test_notify_api.py"}, skip_below=0.15)
    assert out["missed"] == ["tests/test_notify_api.py"]          # 省くはずだったのに落ちた
    assert out["caught"] == ["tests/test_router.py"]
    assert out["would_skip"] == 2 and out["candidates"] == 3


def test_compare_ignores_always_run_tests_without_probability():
    preds = [{"test": "tests/test_router.py", "p": None}] + _pred(finops=0.9)
    out = jtf.compare(preds, {"tests/test_router.py"}, skip_below=0.15)
    assert out["missed"] == [] and out["candidates"] == 1


def _record(preds, failed, mode="selected"):
    return {"mode": mode, "predictions": preds, "failed_files": sorted(failed)}


def test_sweep_reports_misses_and_savings_per_threshold():
    recs = [_record(_pred(a=0.04, b=0.12, c=0.9), {"tests/test_b.py"}),
            _record(_pred(a=0.02, b=0.30, c=0.8), set())]
    rows = {r["threshold"]: r for r in jtf.sweep(recs, thresholds=(0.05, 0.15, 0.5))}
    assert rows[0.05]["missed"] == 0 and rows[0.05]["skip_rate"] == pytest.approx(2 / 6)
    assert rows[0.15]["missed"] == 1                                # b(0.12) を省いて見落とす
    assert rows[0.5]["missed"] == 1 and rows[0.5]["skip_rate"] == pytest.approx(4 / 6)


def test_sweep_skips_records_without_predictions():
    recs = [_record([], {"tests/test_a.py"}, mode="all"), _record(_pred(a=0.9), set())]
    assert jtf.sweep(recs, thresholds=(0.15,))[0]["records"] == 1


def test_recommend_waits_until_enough_failures_are_observed():
    recs = [_record(_pred(a=0.9, b=0.01), {"tests/test_a.py"})]
    r = jtf.recommend(recs, min_failures=10)
    assert r["threshold"] is None and "まだ" in r["reason"]


def test_recommend_picks_highest_threshold_with_zero_misses():
    recs = [_record(_pred(a=0.9, b=0.01, c=0.2), {"tests/test_a.py"}) for _ in range(10)]
    recs += [_record(_pred(a=0.25, b=0.01), {"tests/test_a.py"}) for _ in range(2)]
    r = jtf.recommend(recs, min_failures=10, thresholds=(0.05, 0.1, 0.2, 0.3))
    assert r["threshold"] == 0.2                                   # 0.3 だと a(0.25) を見落とす


def test_shadow_run_writes_record_and_returns_pytest_exit_code(tmp_path):
    result = {"mode": "selected", "reason": "x", "run": ["tests/test_router.py"],
              "skipped": [{"test": "tests/test_finops.py", "p": 0.05}],
              "probabilities": {"tests/test_router.py": 0.9, "tests/test_finops.py": 0.05},
              "always_run": [], "usage": {"input_tokens": 1, "output_tokens": 1}}

    def runner(junit_path):
        Path(junit_path).write_text(JUNIT, encoding="utf-8")
        return 1

    code, rec_path = jtf.shadow_run(result, runner, logs_dir=tmp_path, base="abc", changed=["core/router.py"],
                                    skip_below=0.15)
    rec = json.loads(Path(rec_path).read_text(encoding="utf-8"))
    assert code == 1
    assert rec["failed_files"] == ["tests/test_notify_api.py", "tests/test_router.py"]
    assert rec["comparison"]["caught"] == ["tests/test_router.py"] and rec["comparison"]["missed"] == []
    assert {p["test"] for p in rec["predictions"]} == {"tests/test_router.py", "tests/test_finops.py"}
    assert Path(rec_path).parent.parent.name == "jev-shadow"


def test_shadow_run_records_unknown_outcome_when_junit_missing(tmp_path):
    result = {"mode": "all", "reason": "x", "run": [], "skipped": [], "probabilities": {},
              "always_run": [], "usage": {}}
    code, rec_path = jtf.shadow_run(result, lambda p: 2, logs_dir=tmp_path, base="abc", changed=[],
                                    skip_below=0.15)
    rec = json.loads(Path(rec_path).read_text(encoding="utf-8"))
    assert code == 2 and rec["failed_files"] is None


def test_plan_exposes_probabilities_and_always_run_for_shadow():
    def post(payload):
        return _answers({qid: 0.5 for qid in payload["questions"]})
    r = _plan(["tests/test_finops.py", "core/router.py"], post=post)
    assert r["always_run"] == ["tests/test_finops.py"]
    assert set(r["probabilities"]) == {"tests/test_router.py", "tests/test_notify_api.py"}
