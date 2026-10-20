"""Self-hosted LLM vs heuristic: does a real model break the transmission failure?

Runs the same world (real OSM geography, matched seed) under both policies and
both tourism regimes, and reports whether the LLM actually exercises the
price/wage levers (set_price, set_wage) and spends (buy_food) more than the
heuristic -- the paper's open, falsifiable question (2609.11108, section 10).

Backend: a self-hosted Ollama server (OpenAI-compatible, no key, no quota).

    python experiments/llm_vs_heuristic.py --geojson data/lakeside.geojson \
        --n-agents 10 --pulses 24 --model qwen2.5:0.5b-instruct
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.metrics import compute_metrics  # noqa: E402
from analysis.validate import validate  # noqa: E402
from sim.conditions import get_condition  # noqa: E402
from sim.engine import Simulation  # noqa: E402
from sim.llm_backend import OpenAICompatibleBackend  # noqa: E402
from sim.osm import load_places_from_geojson  # noqa: E402
from sim.policy import HeuristicPolicy, LLMPolicy  # noqa: E402

LEVERS = ["set_price", "set_wage", "buy_food", "invite_to_talk", "start_shift"]


def lever_usage(result):
    out = {}
    for name in LEVERS:
        s = result.tool_stats.get(name, {"calls": 0, "failures": 0})
        out[name] = {"calls": s["calls"], "failures": s["failures"]}
    return out


def run(policy_kind, condition_name, args, places):
    seed = args.seed
    heuristic = HeuristicPolicy(random.Random(seed + 7), mpc=args.mpc)
    if policy_kind == "llm":
        backend = OpenAICompatibleBackend(
            base_url=args.base_url, model=args.model, api_key="ollama",
            max_tokens=args.max_tokens, temperature=0.0,
        )
        policy = LLMPolicy(backend, fallback=heuristic)
    else:
        policy = heuristic

    t0 = time.time()
    sim = Simulation(
        get_condition(condition_name), policy, seed=seed,
        n_agents=args.n_agents, places=places,
    )
    result = sim.run(args.pulses)
    dt = time.time() - t0

    m = compute_metrics(result)
    return {
        "policy": policy_kind,
        "condition": condition_name,
        "seconds": round(dt, 1),
        "validated": validate(result)["all_passed"],
        "malformed": getattr(policy, "malformed", 0),
        "wage_share_of_revenue": m["wage_share_of_revenue"],
        "items_repriced_pct": m["items_repriced_pct"],
        "businesses_trading": m["businesses_trading"],
        "gini_terminal": m["gini_terminal"],
        "persistence": m["persistence"],
        "levers": lever_usage(result),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", default="data/lakeside.geojson")
    ap.add_argument("--n-agents", type=int, default=10)
    ap.add_argument("--pulses", type=int, default=24)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mpc", type=float, default=0.035)
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--model", default="qwen2.5:0.5b-instruct")
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    places = load_places_from_geojson(args.geojson, random.Random(args.seed + 101))
    print(f"loaded {len(places)} real OSM places from {args.geojson}", flush=True)

    rows = []
    for policy_kind in ["heuristic", "llm"]:
        for condition in ["tourism-low", "tourism-high"]:
            print(f"running {policy_kind} / {condition} ...", flush=True)
            row = run(policy_kind, condition, args, places)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
        print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
