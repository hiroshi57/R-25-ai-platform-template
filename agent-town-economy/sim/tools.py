"""The fixed 19-action toolset (2609.11108, section 3).

The paper names ten tools explicitly (buy_food, set_price, start_shift,
pay_agent, write_memory, search_memory, reflect, invite_to_talk, accept_invite,
leave_conversation) plus observe_place, and states the toolset has 19 actions
without listing all of them. The remaining names here are a faithful-in-spirit
reconstruction of the four categories (navigation / economy / memory / social)
and are marked INFERRED. Every call returns a ToolResult so per-tool success
rates (section 9) can be measured.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from . import money

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Simulation
    from .agents import Agent

# Category map. Names marked INFERRED are not spelled out in the paper.
TOOL_CATEGORIES = {
    # navigation
    "observe_place": "navigation",
    "observe_agents": "navigation",  # INFERRED
    "go_to": "navigation",  # INFERRED
    "wander": "navigation",  # INFERRED
    # economy
    "buy_food": "economy",
    "set_price": "economy",
    "start_shift": "economy",
    "end_shift": "economy",  # INFERRED
    "pay_agent": "economy",
    "set_wage": "economy",  # INFERRED
    "deposit": "economy",  # INFERRED
    "withdraw": "economy",  # INFERRED
    # memory
    "write_memory": "memory",
    "search_memory": "memory",
    "reflect": "memory",
    # social
    "invite_to_talk": "social",
    "accept_invite": "social",
    "leave_conversation": "social",
    "send_message": "social",  # INFERRED
}
assert len(TOOL_CATEGORIES) == 19, "toolset must have 19 actions"

# Platform concurrency cap that drives the social-tool failure (section 9):
# invite_to_talk fails when the target is already in this many conversations.
CONVERSATION_CAP = 1


@dataclass
class ToolResult:
    ok: bool
    reason: str = ""


class Tools:
    """Stateless dispatcher operating on the simulation's world + ledger."""

    def __init__(self, sim: "Simulation") -> None:
        self.sim = sim

    # -- navigation -------------------------------------------------------
    def observe_place(self, agent: "Agent") -> ToolResult:
        place = self.sim.world.nearest_priced(agent.pos)
        return ToolResult(place is not None)

    def observe_agents(self, agent: "Agent") -> ToolResult:
        return ToolResult(True)

    def go_to(self, agent: "Agent", place_id: Optional[int] = None) -> ToolResult:
        if place_id is None or place_id not in self.sim.world.places:
            return ToolResult(False, "no target")
        p = self.sim.world.places[place_id]
        agent.x, agent.y = p.x, p.y
        return ToolResult(True)

    def wander(self, agent: "Agent") -> ToolResult:
        agent.x += self.sim.rng.uniform(-50, 50)
        agent.y += self.sim.rng.uniform(-50, 50)
        return ToolResult(True)

    # -- economy ----------------------------------------------------------
    def buy_food(self, agent: "Agent", buyer_external: bool = False) -> ToolResult:
        place = self.sim.world.nearest_priced(agent.pos) or self.sim.world.nearest_priced((0, 0))
        if place is None:
            return ToolResult(False, "no place")
        item = place.cheapest()
        if item is None:
            return ToolResult(False, "no item")
        src = "tourist_inflow_pool" if buyer_external else money.agent_wallet(agent.agent_id)
        kind = "tourist_spend" if buyer_external else "purchase"
        ok = self.sim.ledger.transfer(self.sim.pulse, src, money.biz_cash(place.place_id), item.price, kind)
        if ok:
            place.revenue += item.price
            place.n_transactions += 1
        return ToolResult(ok, "" if ok else "insufficient funds")

    def set_price(self, agent: "Agent") -> ToolResult:
        if agent.owns_business is None:
            return ToolResult(False, "not an owner")
        place = self.sim.world.places[agent.owns_business]
        if not place.menu:
            return ToolResult(False, "no menu")
        item = self.sim.rng.choice(place.menu)
        item.price = max(10, int(item.price * self.sim.rng.uniform(1.05, 1.3)))
        item.reprices += 1
        return ToolResult(True)

    def start_shift(self, agent: "Agent") -> ToolResult:
        place = self.sim.world.nearest_priced(agent.pos)
        if place is None:
            return ToolResult(False, "no workplace")
        agent.on_shift_at = place.place_id
        return ToolResult(True)

    def end_shift(self, agent: "Agent") -> ToolResult:
        if agent.on_shift_at is None:
            return ToolResult(False, "not on shift")
        agent.on_shift_at = None
        return ToolResult(True)

    def pay_agent(self, owner: "Agent", worker: "Agent") -> ToolResult:
        if owner.owns_business is None:
            return ToolResult(False, "not an owner")
        place = self.sim.world.places[owner.owns_business]
        wage = place.wage_per_shift
        ok = self.sim.ledger.transfer(
            self.sim.pulse, money.biz_cash(place.place_id), money.agent_wallet(worker.agent_id), wage, "wage"
        )
        return ToolResult(ok, "" if ok else "business cannot cover wage")

    def set_wage(self, agent: "Agent") -> ToolResult:
        if agent.owns_business is None:
            return ToolResult(False, "not an owner")
        place = self.sim.world.places[agent.owns_business]
        place.wage_per_shift = int(place.wage_per_shift * self.sim.rng.uniform(1.05, 1.25))
        return ToolResult(True)

    def deposit(self, agent: "Agent") -> ToolResult:
        w = money.agent_wallet(agent.agent_id)
        amt = self.sim.ledger.balance(w) // 2
        ok = self.sim.ledger.transfer(self.sim.pulse, w, money.agent_bank(agent.agent_id), amt, "deposit")
        return ToolResult(ok)

    def withdraw(self, agent: "Agent") -> ToolResult:
        b = money.agent_bank(agent.agent_id)
        amt = self.sim.ledger.balance(b) // 2
        ok = self.sim.ledger.transfer(self.sim.pulse, b, money.agent_wallet(agent.agent_id), amt, "withdraw")
        return ToolResult(ok)

    # -- memory -----------------------------------------------------------
    def write_memory(self, agent: "Agent", text: str = "note") -> ToolResult:
        return ToolResult(agent.memory.write(self.sim.pulse, text))

    def search_memory(self, agent: "Agent", query: str = "note") -> ToolResult:
        if not agent.memory.enabled:
            return ToolResult(False, "memory disabled")
        agent.memory.search(query)
        return ToolResult(True)

    def reflect(self, agent: "Agent") -> ToolResult:
        return ToolResult(agent.memory.reflect())

    # -- social -----------------------------------------------------------
    def invite_to_talk(self, agent: "Agent") -> ToolResult:
        others = [a for a in self.sim.agents if a.agent_id != agent.agent_id]
        if not others:
            return ToolResult(False, "no one nearby")
        target = self.sim.rng.choice(others)
        if target.in_conversations >= CONVERSATION_CAP:
            # "you are already in too many conversations" -- the dominant failure.
            return ToolResult(False, "target at conversation cap")
        target.in_conversations += 1
        agent.in_conversations += 1
        return ToolResult(True)

    def accept_invite(self, agent: "Agent") -> ToolResult:
        if agent.in_conversations == 0:
            return ToolResult(False, "no pending invite")
        return ToolResult(True)

    def leave_conversation(self, agent: "Agent") -> ToolResult:
        if agent.in_conversations == 0:
            return ToolResult(False, "not in a conversation")
        agent.in_conversations -= 1
        return ToolResult(True)

    def send_message(self, agent: "Agent") -> ToolResult:
        if agent.in_conversations == 0:
            return ToolResult(False, "not in a conversation")
        return ToolResult(True)
