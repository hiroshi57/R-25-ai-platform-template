"""scripts/needs_full_diff.py のテスト（提案 C: 全文 diff を読むかを外部の証跡だけで決める）."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("needs_full_diff", ROOT / "scripts" / "needs_full_diff.py")
nfd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nfd)

OK_LOG = "105 passed in 2.55s\n"


def _z(*records: str) -> bytes:
    """git diff --numstat -z の出力を組み立てる."""
    return b"".join(r.encode() + b"\0" for r in records)


# --- parse_numstat_z -------------------------------------------------------

def test_parse_plain_binary_and_rename_records():
    raw = _z("10\t2\tsrc/a.py", "-\t-\tlogo.png", "3\t1\t", "old/name.py", "new/name.py")
    assert nfd.parse_numstat_z(raw) == [(10, 2, "src/a.py"), (0, 0, "logo.png"), (3, 1, "new/name.py")]


# --- 生成ファイルの除外 -------------------------------------------------------

def test_generated_files_are_excluded_including_top_level_dist():
    for path in ("dist/app.js", "web/dist/app.js", "package-lock.json", "poetry.lock", "a.min.js", "build/x.o"):
        assert nfd.is_generated(path), path
    for path in ("src/clock.py", "src/distance.py", "docs/build-guide.md"):
        assert not nfd.is_generated(path), path


# --- decide ----------------------------------------------------------------

def _decide(entries, log=OK_LOG, **kw):
    return nfd.decide(entries, stdout_log=log, **kw)


def test_small_change_with_passing_log_does_not_need_full_diff():
    r = _decide([(20, 5, "core/router.py")])
    assert r["needs_full_diff"] is False and r["reasons"] == [] and r["changed_lines"] == 25


def test_over_threshold_counts_only_non_generated_lines():
    r = _decide([(250, 0, "core/a.py"), (5000, 0, "dist/bundle.js")])
    assert r["needs_full_diff"] is False and r["changed_lines"] == 250
    assert r["excluded_files"] == ["dist/bundle.js"]
    r = _decide([(250, 51, "core/a.py")])
    assert r["needs_full_diff"] is True and any("300" in x for x in r["reasons"])


def test_sensitive_path_triggers_even_for_tiny_change():
    for path in ("service/auth.py", ".env.example", "config/secrets.yaml", ".claude/settings.json",
                 ".github/workflows/ci.yml", "infra/main.tf"):
        r = _decide([(1, 0, path)])
        assert r["needs_full_diff"] is True and r["sensitive_files"] == [path], path


def test_missing_or_failing_verification_log_triggers():
    assert any("検証コマンド" in x for x in _decide([(1, 0, "a.py")], log=None)["reasons"])
    assert _decide([(1, 0, "a.py")], log="")["needs_full_diff"] is True
    failing = "FAILED tests/test_x.py::test_y\n1 failed, 3 passed\n"
    assert any("失敗" in x for x in _decide([(1, 0, "a.py")], log=failing)["reasons"])


def test_reviewer_and_contract_flags_trigger():
    assert _decide([(1, 0, "a.py")], reviewer_flagged=True)["needs_full_diff"] is True
    assert _decide([(1, 0, "a.py")], security_sensitive=True)["needs_full_diff"] is True


def test_worker_self_report_is_not_an_input():
    # 自己申告（self_review.verified など）を受け取る引数そのものを持たないこと
    import inspect
    params = set(inspect.signature(nfd.decide).parameters)
    assert not any("verified" in p or "self_review" in p for p in params)
