"""The two-clock simulation engine (2609.11108, section 3).

World clock: fixed 60-simulated-minute pulses at which scheduled dynamics
(tourist arrivals, the grant) resolve and system state is snapshotted.
Agent clock: within each pulse every agent wakes, reasons (via the policy),
and acts once -- a staggered stream rather than lockstep rounds.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import money
from .agents import Agent, Memory
from .conditions import Condition
from .tools import Tools
from .world import Place, World


@dataclass
class RunResult:
    condition: str
    seed: int
    pulses: int
    panel: List[Dict[str, int]] = field(default_factory=list)          # per pulse x agent
    tool_stats: Dict[str, Dict[str, int]] = field(default_factory=dict)
    place_summary: List[Dict[str, int]] = field(default_factory=list)
    treated_ids: List[int] = field(default_factory=list)
    grant_pulse: Optional[int] = None
    ledger_entries: List[Dict[str, object]] = field(default_factory=list)


class Simulation:
    def __init__(
        self,
        condition: Condition,
        policy,
        seed: int = 0,
        n_agents: int = 100,
        memory_enabled: bool = True,
        places: Optional[List[Place]] = None,
        memory_factory=None,
    ) -> None:
        self.memory_factory = memory_factory
        self.condition = condition
        self.policy = policy
        self.seed = seed
        self.rng = random.Random(seed)
        self.n_agents = n_agents
        self.pulse = 0

        self.ledger = money.Ledger()
        self.world = World(self.rng, places=places)
        self.tools = Tools(self)
        self.agents: List[Agent] = []
        self.tool_stats: Dict[str, Dict[str, int]] = {}
        self.treated_ids: List[int] = []

        self._init_agents(memory_enabled)
        self._seed_money()

    # -- setup ------------------------------------------------------------
    def _init_agents(self, memory_enabled: bool) -> None:
        priced = self.world.priced_places()
        for aid in range(self.n_agents):
            spot = self.rng.choice(priced) if priced else None
            mem = self.memory_factory() if self.memory_factory is not None else Memory(enabled=memory_enabled)
            agent = Agent(
                agent_id=aid,
                x=spot.x if spot else 0.0,
                y=spot.y if spot else 0.0,
                memory=mem,
            )
            self.agents.append(agent)
        # Assign every priced place an owner; the nominal owner gets the price/
        # wage levers for that place (owns_business points at one for lever use).
        for place in priced:
            owner = place.place_id % self.n_agents
            place.owner_id = owner
            self.agents[owner].owns_business = place.place_id

    def _seed_money(self) -> None:
        for agent in self.agents:
            # Skewed initial wealth (lognormal) -> unequal starting distribution.
            wealth = int(math.exp(self.rng.normalvariate(9.9, 0.9)))
            wealth = max(2000, wealth)
            self.ledger.seed(money.agent_wallet(agent.agent_id), wealth)
        for place in self.world.priced_places():
            self.ledger.seed(money.biz_cash(place.place_id), self.rng.randint(1000, 5000))

    # -- pulse dynamics ---------------------------------------------------
    def _resolve_tourists(self) -> None:
        rate = self.condition.rate_at(self.pulse)
        count = int(rate) + (1 if self.rng.random() < (rate - int(rate)) else 0)
        priced = self.world.priced_places()
        for _ in range(count):
            place = self.rng.choice(priced)
            item = place.cheapest()
            if item is None:
                continue
            ok = self.ledger.transfer(
                self.pulse, "tourist_inflow_pool", money.biz_cash(place.place_id), item.price, "tourist_spend"
            )
            if ok:
                place.revenue += item.price
                place.n_transactions += 1

    def _resolve_grant(self) -> None:
        g = self.condition.grant
        if g is None or self.pulse != g.pulse:
            return
        recipients = self.rng.sample(range(self.n_agents), g.n_recipients)
        for aid in recipients:
            self.ledger.transfer(self.pulse, "government", money.agent_wallet(aid), g.amount, "grant")
            self.agents[aid].treated = True
            self.treated_ids.append(aid)

    def _settle_wages(self) -> None:
        # Every place pays its (flat, rarely-raised) wage to workers on shift,
        # from business cash. This is the wage channel that section 6 finds
        # does not scale with revenue.
        on_shift: Dict[int, List[Agent]] = {}
        for a in self.agents:
            if a.on_shift_at is not None:
                on_shift.setdefault(a.on_shift_at, []).append(a)
        for place_id, workers in on_shift.items():
            place = self.world.places[place_id]
            for w in workers:
                self.ledger.transfer(
                    self.pulse, money.biz_cash(place_id), money.agent_wallet(w.agent_id),
                    place.wage_per_shift, "wage",
                )

    def _record_tool(self, name: str, ok: bool) -> None:
        s = self.tool_stats.setdefault(name, {"calls": 0, "failures": 0})
        s["calls"] += 1
        if not ok:
            s["failures"] += 1

    def _agent_act(self, agent: Agent) -> None:
        name, kwargs = self.policy.choose(agent, self)
        fn = getattr(self.tools, name, None)
        if fn is None:
            self._record_tool(name, False)
            return
        result = fn(agent, **kwargs)
        self._record_tool(name, bool(result.ok))

    # -- run --------------------------------------------------------------
    def run(self, pulses: int, snapshot_every: int = 1) -> RunResult:
        result = RunResult(
            condition=self.condition.name,
            seed=self.seed,
            pulses=pulses,
            grant_pulse=self.condition.grant.pulse if self.condition.grant else None,
        )
        for p in range(pulses):
            self.pulse = p
            self._resolve_tourists()
            self._resolve_grant()
            self._settle_wages()
            order = list(range(self.n_agents))
            self.rng.shuffle(order)  # staggered, not lockstep
            for aid in order:
                self._agent_act(self.agents[aid])
            if p % snapshot_every == 0 or p == pulses - 1:
                for a in self.agents:
                    wallet = self.ledger.balance(money.agent_wallet(a.agent_id))
                    bank = self.ledger.balance(money.agent_bank(a.agent_id))
                    result.panel.append(
                        {"pulse": p, "agent_id": a.agent_id, "wallet": wallet, "bank": bank, "wealth": wallet + bank}
                    )

        result.tool_stats = self.tool_stats
        result.treated_ids = self.treated_ids
        result.ledger_entries = [e.as_row() for e in self.ledger.entries]
        for place in self.world.priced_places():
            result.place_summary.append(
                {
                    "place_id": place.place_id,
                    "revenue": place.revenue,
                    "n_transactions": place.n_transactions,
                    "n_reprices": sum(m.reprices for m in place.menu),
                    "menu_items": len(place.menu),
                    "wage_per_shift": place.wage_per_shift,
                }
            )
        return result
