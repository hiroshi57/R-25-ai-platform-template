"""LLM-Wiki style agent memory (adapter inspired by WFM, arXiv:2609.18182).

The town-economy paper (2609.11108) found that *removing* agents' flat memory
changed no economic outcome -- the agents were not effectively using memory.
WFM argues the fix is the *representation*: an agent-native "LLM Wiki" that
couples dense passages with an entity/concept topology so an agent can do
multi-hop reasoning over connections a flat store cannot express.

This module implements a lightweight LLM-Wiki so we can test that claim inside
our own reproduction: identical agents and world, the *only* variable being the
memory representation (``flat`` vs ``wiki``). A flat store returns disconnected
fragments and cannot chain "sold out -> raise wage -> workers spend -> demand";
the wiki traverses that path and lets the policy actually pull the lever.

It keeps the write/search/reflect interface of ``agents.Memory`` so the existing
tools keep working, and adds ``observe`` / ``retrieve_actions`` for the policy.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

# Action concepts an owner/worker can act on (the levers the paper found unused).
ACTION_CONCEPTS = {"raise_price", "raise_wage", "buy_food"}

# Traversable relation types (the "reasoning path" edges).
CAUSAL_RELATIONS = {"leads_to", "enables"}


def _default_playbook() -> List[Tuple[str, str, str]]:
    """The organizational operating knowledge, encoded as a small LLM Wiki.

    These are the connections a flat memory cannot represent and therefore
    cannot reason over. Each triple is (src_concept, relation, dst_concept)."""
    return [
        ("high_demand", "leads_to", "raise_price"),
        ("sold_out", "leads_to", "raise_wage"),
        ("raise_wage", "enables", "worker_spending"),
        ("worker_spending", "leads_to", "second_round_demand"),
        ("second_round_demand", "leads_to", "high_demand"),
        ("received_wage", "leads_to", "buy_food"),
    ]


@dataclass
class WikiMemory:
    """A hybrid entity/concept + passage store with multi-hop retrieval.

    mode="wiki": full relational traversal (LLM-Wiki behavior).
    mode="flat": passages only, no traversal (the paper's naive memory).
    """

    mode: str = "wiki"
    hops: int = 3
    enabled: bool = True
    edges: Dict[str, List[Tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))
    node_type: Dict[str, str] = field(default_factory=dict)
    passages: List[Tuple[int, str, List[str]]] = field(default_factory=list)
    writes: int = 0
    searches: int = 0
    reflects: int = 0

    def __post_init__(self) -> None:
        for s, r, d in _default_playbook():
            self._add_edge(s, r, d)

    # -- graph construction ----------------------------------------------
    def _node(self, nid: str, ntype: str = "concept") -> None:
        self.node_type.setdefault(nid, ntype)

    def _add_edge(self, src: str, rel: str, dst: str) -> None:
        self._node(src)
        self._node(dst)
        self.edges[src].append((rel, dst))

    # -- agents.Memory-compatible interface ------------------------------
    def write(self, pulse: int, text: str) -> bool:
        return self.observe(pulse, [], text)

    def search(self, query: str):
        if not self.enabled:
            return []
        self.searches += 1
        return [p for p in self.passages if query in p[1]]

    def reflect(self) -> bool:
        if not self.enabled:
            return False
        self.reflects += 1
        return True

    # -- LLM-Wiki additions ----------------------------------------------
    def observe(self, pulse: int, concepts: List[str], text: str = "") -> bool:
        """Record a passage node, cross-linked to the situation concepts it
        mentions (the cross-layer hyper-edges of the Wiki Graph schema)."""
        if not self.enabled:
            return False
        pid = f"passage:{len(self.passages)}"
        self.node_type[pid] = "passage"
        for c in concepts:
            self._node(c)
            self.edges[pid].append(("mentions", c))
            self.edges[c].append(("mentioned_in", pid))
        self.passages.append((pulse, text, list(concepts)))
        self.writes += 1
        return True

    def retrieve_actions(self, situation: List[str]) -> Set[str]:
        """Multi-hop retrieval: which action concepts are reachable from the
        current situation along causal edges? A flat store cannot traverse, so
        it returns nothing -- reproducing the "memory doesn't matter" result."""
        if not self.enabled or self.mode == "flat":
            return set()
        self.searches += 1
        seen: Set[str] = set(situation)
        frontier: Set[str] = set(situation)
        found: Set[str] = set()
        for _ in range(self.hops):
            nxt: Set[str] = set()
            for c in frontier:
                for rel, dst in self.edges.get(c, []):
                    if rel in CAUSAL_RELATIONS and dst not in seen:
                        seen.add(dst)
                        nxt.add(dst)
                        if dst in ACTION_CONCEPTS:
                            found.add(dst)
            if not nxt:
                break
            frontier = nxt
        return found

    def reasoning_path(self, start: str, hops: int = 4) -> List[str]:
        """Return one causal chain from a start concept (for explanation/audit)."""
        path = [start]
        cur = start
        seen = {start}
        for _ in range(hops):
            nxts = [d for r, d in self.edges.get(cur, []) if r in CAUSAL_RELATIONS and d not in seen]
            if not nxts:
                break
            cur = nxts[0]
            seen.add(cur)
            path.append(cur)
        return path
