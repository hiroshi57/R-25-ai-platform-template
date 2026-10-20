"""Macro / distributional metrics (2609.11108, sections 6-9).

Gini, wealth-rank persistence, quintile mobility, wage pass-through, the exact
extensive/intensive margin decomposition, and the marginal propensity to
consume out of a windfall.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from sim import money
from sim.engine import RunResult


def gini(values: List[float]) -> float:
    xs = sorted(v for v in values if v is not None)
    n = len(xs)
    if n == 0:
        return 0.0
    total = sum(xs)
    if total == 0:
        return 0.0
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return (2 * cum) / (n * total) - (n + 1) / n


def _rank(xs: List[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x: List[float], y: List[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return float("nan")
    rx, ry = _rank(x), _rank(y)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def _quintile(sorted_vals: List[float], v: float) -> int:
    n = len(sorted_vals)
    idx = sum(1 for s in sorted_vals if s < v)
    return min(4, int(5 * idx / n)) if n else 0


def _wealth_by_pulse(result: RunResult):
    by_pulse: Dict[int, Dict[int, int]] = defaultdict(dict)
    for r in result.panel:
        by_pulse[r["pulse"]][r["agent_id"]] = r["wealth"]
    return by_pulse


def compute_metrics(result: RunResult) -> Dict[str, object]:
    by_pulse = _wealth_by_pulse(result)
    pulses = sorted(by_pulse)
    first, last = pulses[0], pulses[-1]
    initial = [by_pulse[first][a] for a in sorted(by_pulse[first])]
    final = [by_pulse[last][a] for a in sorted(by_pulse[last])]

    persistence = spearman(initial, final)

    si, sf = sorted(initial), sorted(final)
    moved = sum(1 for a in range(len(initial)) if _quintile(si, initial[a]) != _quintile(sf, final[a]))
    quintile_mobility = moved / len(initial) if initial else 0.0

    revenue = sum(p["revenue"] for p in result.place_summary)
    wages = sum(int(e["amount"]) for e in result.ledger_entries if e["kind"] == "wage")
    total_items = sum(p["menu_items"] for p in result.place_summary)
    repriced = sum(1 for p in result.place_summary if p["n_reprices"] > 0)

    return {
        "condition": result.condition,
        "seed": result.seed,
        "gini_terminal": round(gini(final), 4),
        "persistence": round(persistence, 4),
        "quintile_mobility": round(quintile_mobility, 4),
        "median_wealth": sorted(final)[len(final) // 2] if final else 0,
        "business_revenue": revenue,
        "wages_paid": wages,
        "wage_share_of_revenue": round(wages / revenue, 4) if revenue else 0.0,
        "businesses_trading": sum(1 for p in result.place_summary if p["n_transactions"] > 0),
        "menu_items": total_items,
        "items_repriced_pct": round(100 * repriced / total_items, 4) if total_items else 0.0,
    }


def margin_decomposition(low: RunResult, high: RunResult) -> Dict[str, float]:
    """Exact extensive x intensive decomposition of the revenue response."""
    def stats(r: RunResult):
        trading = [p for p in r.place_summary if p["n_transactions"] > 0]
        rev = sum(p["revenue"] for p in r.place_summary)
        n = len(trading)
        return rev, n, (rev / n if n else 0.0)

    rev_l, n_l, per_l = stats(low)
    rev_h, n_h, per_h = stats(high)
    extensive = n_h / n_l if n_l else float("nan")
    intensive = per_h / per_l if per_l else float("nan")
    total = rev_h / rev_l if rev_l else float("nan")
    return {
        "revenue_multiplier": round(total, 3),
        "extensive_margin": round(extensive, 3),
        "intensive_margin": round(intensive, 3),
        "product_check": round(extensive * intensive, 3),
    }


def marginal_propensity_to_consume(result: RunResult) -> Optional[Dict[str, float]]:
    """Treated-vs-control excess spending after the grant (section 7)."""
    if result.grant_pulse is None or not result.treated_ids:
        return None
    grant_amounts = [int(e["amount"]) for e in result.ledger_entries if e["kind"] == "grant"]
    grant = grant_amounts[0] if grant_amounts else 0
    treated = set(result.treated_ids)

    spend: Dict[int, int] = defaultdict(int)
    for e in result.ledger_entries:
        if e["kind"] == "purchase" and int(e["pulse"]) >= result.grant_pulse:
            src = str(e["src"])
            if src.startswith("agent:"):
                aid = int(src.split(":")[1])
                spend[aid] += int(e["amount"])

    n_total = 1 + max((int(str(e["src"]).split(":")[1]) for e in result.ledger_entries
                        if str(e["src"]).startswith("agent:")), default=0)
    t_spend = [spend[a] for a in range(n_total) if a in treated]
    c_spend = [spend[a] for a in range(n_total) if a not in treated]
    mean_t = sum(t_spend) / len(t_spend) if t_spend else 0.0
    mean_c = sum(c_spend) / len(c_spend) if c_spend else 0.0
    excess = mean_t - mean_c
    return {
        "grant": grant,
        "mean_spend_treated": round(mean_t, 1),
        "mean_spend_control": round(mean_c, 1),
        "excess_spend": round(excess, 1),
        "mpc": round(excess / grant, 4) if grant else 0.0,
    }
