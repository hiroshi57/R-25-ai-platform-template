"""Independent, offline revalidation (2609.11108, section 5).

Recomputes every balance a second time, directly from the raw ledger entries,
and reconciles it against the wealth the engine recorded in the panel. This is
the paper's per-agent ledger reconciliation: the change in an account's balance
must equal the signed sum of its own transaction history, exactly.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from sim import money
from sim.engine import RunResult


def _replay_balances(entries: List[Dict[str, object]]) -> Dict[str, int]:
    bal: Dict[str, int] = defaultdict(int)
    for e in entries:
        bal[e["src"]] -= int(e["amount"])
        bal[e["dst"]] += int(e["amount"])
    return dict(bal)


def validate(result: RunResult) -> Dict[str, object]:
    entries = result.ledger_entries
    bal = _replay_balances(entries)

    checks: Dict[str, bool] = {}

    # 1. Every transaction is zero-sum, so all accounts (incl. external) net 0.
    checks["all_accounts_sum_zero"] = sum(bal.values()) == 0

    # 2. No internal account ever ends negative.
    checks["no_internal_negative"] = all(
        v >= 0 for a, v in bal.items() if money.is_internal(a)
    )

    # 3. Per-agent ledger reconciliation against the recorded panel.
    final_pulse = max((r["pulse"] for r in result.panel), default=0)
    recorded = {r["agent_id"]: r["wealth"] for r in result.panel if r["pulse"] == final_pulse}
    mismatches = 0
    for aid, wealth in recorded.items():
        recomputed = bal.get(money.agent_wallet(aid), 0) + bal.get(money.agent_bank(aid), 0)
        if recomputed != wealth:
            mismatches += 1
    checks["per_agent_reconciliation"] = mismatches == 0

    # 4. Money entered only through the two ledgered external accounts.
    external_kinds = {e["kind"] for e in entries if money.is_external(e["src"]) or money.is_external(e["dst"])}
    allowed = {"seed", "grant", "tourist_spend", "tax"}
    checks["external_flows_are_ledgered"] = external_kinds.issubset(allowed)

    return {
        "condition": result.condition,
        "seed": result.seed,
        "n_entries": len(entries),
        "checks": checks,
        "all_passed": all(checks.values()),
        "reconciliation_mismatches": mismatches,
        "total_internal": sum(v for a, v in bal.items() if money.is_internal(a)),
    }
