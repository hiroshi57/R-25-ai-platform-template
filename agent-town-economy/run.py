"""CLI entrypoint: run a condition (or the tourism sweep), validate, report.

Examples
--------
    python run.py --condition baseline --pulses 336 --seed 1
    python run.py --condition wealth-grant --pulses 336 --seed 1      # prints MPC
    python run.py --sweep --pulses 336 --seeds 3                      # margin decomposition
    python run.py --condition baseline --pulses 336 --no-memory       # ablation arm

    # real OSM geography (fetch once, then load):
    python data/fetch_osm.py
    python run.py --condition baseline --osm-geojson data/lakeside.geojson

    # real LLM policy (any OpenAI-compatible endpoint, incl. self-hosted vLLM):
    export LLM_API_KEY=sk-...
    python run.py --condition baseline --pulses 60 \
        --llm-base-url https://api.openai.com/v1 --llm-model gpt-4o-mini
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analysis.metrics import (  # noqa: E402
    compute_metrics,
    margin_decomposition,
    marginal_propensity_to_consume,
)
from analysis.validate import validate  # noqa: E402
from sim.conditions import get_condition  # noqa: E402
from sim.engine import Simulation  # noqa: E402
from sim.policy import HeuristicPolicy, LLMPolicy  # noqa: E402


def _load_places(args, seed):
    if not args.osm_geojson:
        return None
    from sim.osm import load_places_from_geojson

    return load_places_from_geojson(args.osm_geojson, random.Random(seed + 101))


def _make_policy(args, seed):
    heuristic = HeuristicPolicy(
        random.Random(seed + 7),
        reprice_prob=args.reprice_prob,
        wage_raise_prob=args.wage_raise_prob,
        mpc=args.mpc,
    )
    if not args.llm_base_url:
        return heuristic
    from sim.llm_backend import OpenAICompatibleBackend

    backend = OpenAICompatibleBackend(
        base_url=args.llm_base_url,
        model=args.llm_model,
        api_key=os.environ.get("LLM_API_KEY"),
        temperature=args.llm_temperature,
        max_tokens=args.llm_max_tokens,
    )
    return LLMPolicy(backend, fallback=heuristic)


def run_one(args, condition_name, seed):
    condition = get_condition(condition_name)
    policy = _make_policy(args, seed)
    sim = Simulation(
        condition, policy, seed=seed,
        n_agents=args.n_agents,
        memory_enabled=not args.no_memory,
        places=_load_places(args, seed),
    )
    result = sim.run(args.pulses)
    if isinstance(policy, LLMPolicy):
        print(f"[llm] malformed/forfeited generations: {policy.malformed}")
        ranked = sorted(result.tool_stats.items(), key=lambda kv: -kv[1]["calls"])
        summary = {name: f"{s['calls']}c/{s['failures']}f" for name, s in ranked}
        print(f"[llm] tool choices (calls/failures): {json.dumps(summary, ensure_ascii=False)}")
    return result


def _print(title, obj):
    print(f"\n== {title} ==")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM-agent town economy (repro of arXiv:2609.11108)")
    ap.add_argument("--condition", default="baseline")
    ap.add_argument("--pulses", type=int, default=336, help="336 = 2 simulated weeks")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--seeds", type=int, default=3, help="number of seeds for --sweep")
    ap.add_argument("--n-agents", type=int, default=100, help="fewer keeps LLM runs cheap")
    ap.add_argument("--no-memory", action="store_true", help="run the memory-ablation arm")
    ap.add_argument("--reprice-prob", type=float, default=0.001)
    ap.add_argument("--wage-raise-prob", type=float, default=0.001)
    ap.add_argument("--mpc", type=float, default=0.035)
    ap.add_argument("--sweep", action="store_true", help="tourism low/high sweep + margin decomposition")
    ap.add_argument("--osm-geojson", default=None, help="path to a GeoJSON of real OSM footprints")
    ap.add_argument("--llm-base-url", default=None, help="OpenAI-compatible base URL (enables LLM policy)")
    ap.add_argument("--llm-model", default="gpt-4o-mini")
    ap.add_argument("--llm-temperature", type=float, default=0.0)
    ap.add_argument("--llm-max-tokens", type=int, default=1024,
                    help="reasoning models need a large budget or replies come back empty")
    args = ap.parse_args()

    if args.sweep:
        lows = [run_one(args, "tourism-low", s) for s in range(args.seeds)]
        highs = [run_one(args, "tourism-high", s) for s in range(args.seeds)]
        _print("validation (low seed 0)", validate(lows[0]))
        _print("metrics low (seed 0)", compute_metrics(lows[0]))
        _print("metrics high (seed 0)", compute_metrics(highs[0]))
        _print("margin decomposition (seed 0)", margin_decomposition(lows[0], highs[0]))
        return 0

    result = run_one(args, args.condition, args.seed)
    _print("validation", validate(result))
    _print("metrics", compute_metrics(result))
    mpc = marginal_propensity_to_consume(result)
    if mpc is not None:
        _print("marginal propensity to consume", mpc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
