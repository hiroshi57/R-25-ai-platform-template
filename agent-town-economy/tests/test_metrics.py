"""Metric sanity + reproduction of the paper's qualitative mechanisms."""
import random

from sim.conditions import get_condition, tourism_high, tourism_low
from sim.engine import Simulation
from sim.policy import HeuristicPolicy
from analysis.metrics import (
    compute_metrics,
    gini,
    margin_decomposition,
    marginal_propensity_to_consume,
    spearman,
)


def _run(condition, seed=1, pulses=120, mpc=0.035):
    policy = HeuristicPolicy(random.Random(seed + 7), mpc=mpc)
    return Simulation(condition, policy, seed=seed, n_agents=60).run(pulses)


def test_gini_bounds():
    assert gini([1, 1, 1, 1]) == 0.0
    assert 0.0 < gini([0, 0, 0, 100]) <= 1.0


def test_spearman_perfect_rank():
    assert round(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 3) == 1.0
    assert round(spearman([1, 2, 3, 4], [40, 30, 20, 10]), 3) == -1.0


def test_margin_decomposition_multiplies_to_total():
    low = _run(tourism_low())
    high = _run(tourism_high())
    d = margin_decomposition(low, high)
    # extensive x intensive == revenue multiplier (exact identity from money conservation)
    assert abs(d["product_check"] - d["revenue_multiplier"]) < 0.02
    # more tourism -> more revenue
    assert d["revenue_multiplier"] > 1.0


def test_price_stickiness_reproduced():
    # With a tiny reprice probability, almost no items are ever repriced.
    m = compute_metrics(_run(get_condition("tourism-high")))
    assert m["items_repriced_pct"] < 5.0


def test_low_mpc_windfall_is_hoarded():
    result = _run(get_condition("wealth-grant"), mpc=0.035)
    mpc = marginal_propensity_to_consume(result)
    assert mpc is not None
    # Marginal propensity to consume stays far below the 0.2-0.5 human range.
    assert mpc["mpc"] < 0.15
