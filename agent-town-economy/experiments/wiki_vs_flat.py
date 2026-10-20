"""Does a better memory *representation* break the transmission failure?

The paper (2609.11108) found removing agents' flat memory changed no economic
outcome. WFM (2609.18182) argues the problem is representation, not memory per
se: an LLM Wiki lets an agent reason over connections a flat store cannot.

This experiment holds everything fixed -- same world, same seed, same policy
(WikiPolicy) -- and flips ONLY the memory representation between `flat` and
`wiki`. If the wiki arm starts pulling the wage/price levers and circulating
windfalls while the flat arm does not, memory representation, not memory
presence, is what matters.

    python experiments/wiki_vs_flat.py
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.metrics import compute_metrics, marginal_propensity_to_consume  # noqa: E402
from analysis.validate import validate  # noqa: E402
from sim.conditions import get_condition  # noqa: E402
from sim.engine import Simulation  # noqa: E402
from sim.policy import WikiPolicy  # noqa: E402
from sim.wiki_memory import WikiMemory  # noqa: E402

LEVERS = ["set_wage", "set_price", "buy_food", "start_shift", "invite_to_talk"]


def lever_usage(result):
    out = {}
    for name in LEVERS:
        s = result.tool_stats.get(name, {"calls": 0, "failures": 0})
        out[name] = {"calls": s["calls"], "failures": s["failures"]}
    return out


def run(mode: str, condition_name: str, args) -> dict:
    policy = WikiPolicy(random.Random(args.seed + 7))
    sim = Simulation(
        get_condition(condition_name), policy, seed=args.seed,
        n_agents=args.n_agents,
        memory_factory=lambda: WikiMemory(mode=mode),
    )
    result = sim.run(args.pulses)
    m = compute_metrics(result)
    row = {
        "memory": mode,
        "condition": condition_name,
        "validated": validate(result)["all_passed"],
        "wage_share_of_revenue": m["wage_share_of_revenue"],
        "items_repriced_pct": m["items_repriced_pct"],
        "businesses_trading": m["businesses_trading"],
        "gini_terminal": m["gini_terminal"],
        "persistence": m["persistence"],
        "levers": lever_usage(result),
    }
    mpc = marginal_propensity_to_consume(result)
    if mpc is not None:
        row["mpc"] = mpc["mpc"]
        row["excess_spend"] = mpc["excess_spend"]
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-agents", type=int, default=40)
    ap.add_argument("--pulses", type=int, default=150)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="experiments/results_wiki.json")
    args = ap.parse_args()

    rows = []
    for mode in ["flat", "wiki"]:
        for cond in ["baseline", "tourism-high", "wealth-grant"]:
            row = run(mode, cond, args)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False))

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), args.out)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
