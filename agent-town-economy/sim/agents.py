"""Agents and their persistent memory (ablated in one experimental arm)."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class MemoryEntry:
    pulse: int
    text: str


@dataclass
class Memory:
    """Persistent per-agent memory. When `enabled` is False the store is inert,
    reproducing the `no-memory` ablation arm (section 9)."""

    enabled: bool = True
    entries: List[MemoryEntry] = field(default_factory=list)
    writes: int = 0
    searches: int = 0
    reflects: int = 0

    def write(self, pulse: int, text: str) -> bool:
        if not self.enabled:
            return False
        self.entries.append(MemoryEntry(pulse, text))
        self.writes += 1
        return True

    def search(self, query: str) -> List[MemoryEntry]:
        if not self.enabled:
            return []
        self.searches += 1
        return [e for e in self.entries if query in e.text]

    def reflect(self) -> bool:
        if not self.enabled:
            return False
        self.reflects += 1
        return True


@dataclass
class Agent:
    agent_id: int
    x: float
    y: float
    owns_business: Optional[int] = None  # place_id if this agent owns a business
    memory: Memory = field(default_factory=Memory)
    on_shift_at: Optional[int] = None  # place_id currently working at
    in_conversations: int = 0  # active conversation count (concurrency-capped)
    treated: bool = False  # received the wealth grant

    @property
    def pos(self) -> Tuple[float, float]:
        return (self.x, self.y)
