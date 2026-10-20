"""The platform's core guarantee: money is conserved, exactly, always."""
import random

from sim import money
from sim.conditions import baseline, wealth_grant
from sim.engine import Simulation
from sim.policy import HeuristicPolicy
from analysis.validate import validate


def _run(condition, seed=1, pulses=60):
    policy = HeuristicPolicy(random.Random(seed + 7), mpc=0.2)
    return Simulation(condition, policy, seed=seed, n_agents=40).run(pulses)


def test_all_accounts_net_to_zero_every_entry():
    result = _run(baseline())
    running = {}
    for e in result.ledger_entries:
        running[e["src"]] = running.get(e["src"], 0) - e["amount"]
        running[e["dst"]] = running.get(e["dst"], 0) + e["amount"]
        assert sum(running.values()) == 0  # zero-sum after every single transfer


def test_per_agent_ledger_reconciliation():
    result = _run(wealth_grant())
    report = validate(result)
    assert report["checks"]["per_agent_reconciliation"]
    assert report["reconciliation_mismatches"] == 0
    assert report["all_passed"]


def test_no_internal_account_goes_negative():
    result = _run(baseline())
    assert validate(result)["checks"]["no_internal_negative"]


def test_money_enters_only_through_ledgered_external_accounts():
    result = _run(wealth_grant())
    assert validate(result)["checks"]["external_flows_are_ledgered"]
