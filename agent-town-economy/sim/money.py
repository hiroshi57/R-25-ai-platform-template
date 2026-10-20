"""Money-conserving double-entry ledger.

Reproduces the paper's central methodological guarantee (2609.11108, section 5):
total money in the system equals the sum of every internal balance at every
pulse, exactly, and each account's balance equals the signed sum of its own
transaction history.

All amounts are integer NPR (no fractional currency) so conservation is exact
by construction -- there is no floating-point drift to reconcile.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

# External accounts are money sources/sinks and may hold a negative balance:
#   genesis              -- seeds the initial money stock into internal accounts
#   tourist_inflow_pool  -- exogenous tourist demand enters here
#   government           -- cash grants (source) and taxes/sinks (destination)
EXTERNAL_ACCOUNTS = {"genesis", "tourist_inflow_pool", "government"}


def agent_wallet(agent_id: int) -> str:
    return f"agent:{agent_id}:wallet"


def agent_bank(agent_id: int) -> str:
    return f"agent:{agent_id}:bank"


def biz_cash(biz_id: int) -> str:
    return f"biz:{biz_id}:cash"


def biz_bank(biz_id: int) -> str:
    return f"biz:{biz_id}:bank"


def is_external(acct: str) -> bool:
    return acct in EXTERNAL_ACCOUNTS


def is_internal(acct: str) -> bool:
    return not is_external(acct)


@dataclass
class TxEntry:
    pulse: int
    src: str
    dst: str
    amount: int
    kind: str  # seed | wage | purchase | tourist_spend | grant | tax | deposit | withdraw

    def as_row(self) -> Dict[str, object]:
        return {
            "pulse": self.pulse,
            "src": self.src,
            "dst": self.dst,
            "amount": self.amount,
            "kind": self.kind,
        }


class Ledger:
    """Single source of truth for every rupee. Balances are only ever mutated
    through :meth:`transfer`, which simultaneously records a signed entry, so the
    offline validator can recompute every balance independently."""

    def __init__(self) -> None:
        self.entries: List[TxEntry] = []
        self.balances: Dict[str, int] = {}

    def ensure(self, acct: str) -> None:
        self.balances.setdefault(acct, 0)

    def seed(self, acct: str, amount: int) -> None:
        """Move `amount` from the external genesis account into an internal one."""
        if amount:
            self.transfer(0, "genesis", acct, amount, "seed")

    def transfer(self, pulse: int, src: str, dst: str, amount: int, kind: str) -> bool:
        """Move `amount` from src to dst. Returns False (and records nothing) if
        an internal source lacks the funds; external sources may go negative."""
        if amount <= 0 or src == dst:
            return False
        self.ensure(src)
        self.ensure(dst)
        if is_internal(src) and self.balances[src] < amount:
            return False
        self.balances[src] -= amount
        self.balances[dst] += amount
        self.entries.append(TxEntry(pulse, src, dst, amount, kind))
        return True

    def balance(self, acct: str) -> int:
        return self.balances.get(acct, 0)

    def total_internal(self) -> int:
        """totalMoneyInSystem: sum of every non-external balance."""
        return sum(v for a, v in self.balances.items() if is_internal(a))

    def total_all(self) -> int:
        """Sum across all accounts including external. Invariant: always 0."""
        return sum(self.balances.values())
