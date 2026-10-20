"""LLM-Wiki memory: multi-hop retrieval, flat degradation, and lever effect."""
import random

from sim.conditions import baseline, tourism_high
from sim.engine import Simulation
from sim.policy import WikiPolicy
from sim.wiki_memory import WikiMemory
from analysis.validate import validate


def test_wiki_retrieves_action_one_hop():
    m = WikiMemory(mode="wiki")
    assert "raise_price" in m.retrieve_actions(["high_demand"])
    assert "raise_wage" in m.retrieve_actions(["sold_out"])
    assert "buy_food" in m.retrieve_actions(["received_wage"])


def test_flat_retrieves_nothing():
    m = WikiMemory(mode="flat")
    assert m.retrieve_actions(["high_demand"]) == set()
    assert m.retrieve_actions(["sold_out"]) == set()


def test_reasoning_path_is_multihop():
    m = WikiMemory(mode="wiki")
    path = m.reasoning_path("sold_out")
    # sold_out -> raise_wage -> worker_spending -> second_round_demand
    assert path[:2] == ["sold_out", "raise_wage"]
    assert "worker_spending" in path


def _run(mode, condition, seed=1, pulses=120, n=30):
    policy = WikiPolicy(random.Random(seed + 7))
    sim = Simulation(condition, policy, seed=seed, n_agents=n,
                     memory_factory=lambda: WikiMemory(mode=mode))
    return sim.run(pulses)


def test_wiki_arm_pulls_levers_flat_does_not():
    flat = _run("flat", tourism_high())
    wiki = _run("wiki", tourism_high())

    def calls(res, tool):
        return res.tool_stats.get(tool, {"calls": 0})["calls"]

    # Flat memory: the paper's result -- levers stay unused.
    assert calls(flat, "set_wage") == 0
    # Wiki memory: the agent now actually raises wages and/or prices.
    assert calls(wiki, "set_wage") > 0 or calls(wiki, "set_price") > 0


def test_money_conserved_in_both_arms():
    for mode in ("flat", "wiki"):
        res = _run(mode, baseline())
        assert validate(res)["all_passed"]
