
from core import BudgetGuard, RoutingStrategy, detect_cost_anomaly, project_month_end


def test_project_month_end():
    # 10日で$50消費 -> 30日着地は$150
    assert project_month_end(50.0, 10, 30) == 150.0


def test_budget_status_levels():
    g = BudgetGuard(budget_usd=100, warn_ratio=0.8, critical_ratio=1.0)
    # 10日で$40 -> 着地$120 > 予算 -> critical
    assert g.status(40, 10, 30).alert_level == "critical"
    # 10日で$28 -> 着地$84 -> warn(0.8-1.0)
    assert g.status(28, 10, 30).alert_level == "warn"
    # 10日で$10 -> 着地$30 -> ok
    assert g.status(10, 10, 30).alert_level == "ok"


def test_budget_status_fields():
    g = BudgetGuard(budget_usd=100)
    s = g.status(40, 10, 30)
    assert s.projected_usd == 120.0
    assert s.over_budget is True
    assert s.remaining_usd == 60.0


def test_budget_routing_downgrades_to_cost_when_critical():
    g = BudgetGuard(budget_usd=100)
    # critical時は要求がqualityでもcostへ降格
    assert g.choose_strategy(40, 10, RoutingStrategy.QUALITY, 30) == RoutingStrategy.COST
    # ok時は要求どおり
    assert g.choose_strategy(10, 10, RoutingStrategy.QUALITY, 30) == RoutingStrategy.QUALITY


def test_cost_anomaly_detection():
    a = detect_cost_anomaly([1.0, 1.2, 0.9, 1.1], today=3.0, threshold=2.0)
    assert a.is_anomaly is True
    assert a.ratio >= 2.0
    normal = detect_cost_anomaly([1.0, 1.2, 0.9], today=1.1, threshold=2.0)
    assert normal.is_anomaly is False


def test_cost_anomaly_no_baseline():
    a = detect_cost_anomaly([], today=5.0)
    assert a.is_anomaly is False   # 基準なしでは異常判定しない
