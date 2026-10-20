"""Money-conserving spatial LLM-agent economy -- reproduction of arXiv:2609.11108.

Faithful: the conserved-money ledger, two-clock engine, 19-tool interface,
treatment conditions, and the analysis pipeline.
Approximated: real Pokhara OSM geography (synthetic grid) and the LLM decision
policy (heuristic by default, LLM pluggable).
"""
from . import agents, conditions, engine, llm_backend, money, osm, policy, tools, world

__all__ = [
    "agents", "conditions", "engine", "llm_backend", "money",
    "osm", "policy", "tools", "world",
]
