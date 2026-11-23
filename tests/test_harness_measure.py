"""scripts/harness_measure.py と harness_log.py の --harness-config 統合のテスト."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hl = _load("harness_log")
hm = _load("harness_measure")


def _file(tmp_path: Path, name: str, obj) -> Path:
    p = tmp_path / "in" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _record(logs, tmp_path, task_id, config, *, score=None, verdict=None, tokens=None, n="a"):
    """1 run を harness_log.save で書く（worker-report と harness-config を同じ呼びで揃える）。"""
    review = {}
    if score is not None:
        review["score"] = score
    if verdict is not None:
        review["verdict"] = verdict
    files = {"worker_report": _file(tmp_path, f"wr_{task_id}_{n}.json", {"status": "completed"})}
    if review:
        files["review"] = _file(tmp_path, f"rv_{task_id}_{n}.json", review)
    saved = hl.save(
        logs,
        "proj",
        task_id,
        "202610",
        files,
        harness_config=_file(tmp_path, f"cfg_{task_id}_{n}.json", config),
    )
    # 実測ファイルは save の transcript 経由だが、テストでは tokens を直接置く。
    # suffix は save の戻り値の harness-config 名から正確に取る（glob ソート順に頼らない）。
    if tokens is not None:
        cfg_name = next(s for s in saved if s.startswith("harness-config"))
        suffix = "" if cfg_name == "harness-config.json" else "." + cfg_name.split(".")[1]
        d = logs / "proj" / "202610" / task_id
        (d / f"tool-usage.measured{suffix}.json").write_text(
            json.dumps({"tokens_total": tokens}), encoding="utf-8"
        )


# --- harness_log.py: --harness-config の統合 -----------------------------------


def test_harness_config_saved_aligned_to_worker_report_suffix(tmp_path):
    logs = tmp_path / "logs"
    cfg1 = _file(tmp_path, "c1.json", {"toolset": "a"})
    cfg2 = _file(tmp_path, "c2.json", {"toolset": "b"})
    hl.save(
        logs,
        "proj",
        "T",
        "202610",
        {"worker_report": _file(tmp_path, "w1.json", {"n": 1})},
        harness_config=cfg1,
    )
    hl.save(
        logs,
        "proj",
        "T",
        "202610",
        {"worker_report": _file(tmp_path, "w2.json", {"n": 2})},
        harness_config=cfg2,
    )
    d = logs / "proj" / "202610" / "T"
    # worker-report.json ↔ harness-config.json、worker-report.2.json ↔ harness-config.2.json
    assert (d / "harness-config.json").exists()
    assert (d / "harness-config.2.json").exists()
    assert json.loads((d / "harness-config.json").read_text(encoding="utf-8"))["toolset"] == "a"
    assert json.loads((d / "harness-config.2.json").read_text(encoding="utf-8"))["toolset"] == "b"


# --- harness_measure.py --------------------------------------------------------


def test_summary_empty(tmp_path, capsys):
    assert hm.summary(tmp_path / "proj") == 0
    assert "run 総数: 0" in capsys.readouterr().out


def test_pairable_counts_only_tasks_with_both_values(tmp_path):
    logs = tmp_path / "logs"
    base = {"model": "m", "planning": "off", "context_policy": "auto"}
    _record(logs, tmp_path, "T1", {**base, "toolset": "a"})
    _record(logs, tmp_path, "T1", {**base, "toolset": "b"}, n="b")
    _record(logs, tmp_path, "T2", {**base, "toolset": "a"})
    runs = hm.all_runs(logs / "proj", "tokens")
    assert hm._pairable_count(runs, "toolset") == 1


def test_compare_pairs_and_deltas(tmp_path, capsys):
    logs = tmp_path / "logs"
    base = {"model": "m", "planning": "off", "context_policy": "auto"}
    _record(logs, tmp_path, "T1", {**base, "toolset": "a"}, score=0.5, tokens=1000)
    _record(logs, tmp_path, "T1", {**base, "toolset": "b"}, score=0.6, tokens=700, n="b")
    rc = hm.compare(logs / "proj", "toolset", "a", "b", model="m", metric="tokens")
    assert rc == 0
    out = capsys.readouterr().out
    assert "対になったタスク数: 1" in out
    assert "+0.100" in out  # Δscore
    assert "-300.0" in out  # Δcost (tokens)


def test_compare_verdict_maps_to_score(tmp_path, capsys):
    logs = tmp_path / "logs"
    base = {"model": "m", "planning": "off", "context_policy": "auto"}
    _record(logs, tmp_path, "T1", {**base, "toolset": "a"}, verdict="REQUEST_CHANGES")
    _record(logs, tmp_path, "T1", {**base, "toolset": "b"}, verdict="APPROVE", n="b")
    hm.compare(logs / "proj", "toolset", "a", "b", metric="tokens")
    assert "+1.000" in capsys.readouterr().out


def test_compare_unpaired_excluded(tmp_path, capsys):
    logs = tmp_path / "logs"
    base = {"model": "m", "planning": "off", "context_policy": "auto"}
    _record(logs, tmp_path, "T1", {**base, "toolset": "a"}, score=0.5)
    _record(logs, tmp_path, "T2", {**base, "toolset": "b"}, score=0.6)
    hm.compare(logs / "proj", "toolset", "a", "b")
    assert "対になったタスク数: 0" in capsys.readouterr().out
