"""FinOps for LLM(差別化の核). LLM利用コストを「読める・守れる」化する.

  1. 月次コスト予測: 現時点の消費ペースから着地見込みを外挿
  2. 予算アラート: 着地見込みが予算を超える場合に警告(閾値段階)
  3. 予算内ルーティング: 残予算に応じて cost 戦略へ自動降格し、超過を防ぐ
  4. コスト異常検知: 直近平均に対する急変を検出
すべて標準ライブラリのみ。

外挿の限界(正直表記):
  線形外挿は「今日までの平均日次コストが月末まで続く」という仮定に立つ。
  月初 1-2 日のサンプルでは分散が大きく、着地見込みは信頼できない。
  `BudgetStatus.confidence` にサンプル日数由来の信頼度を持たせ、
  low の場合は UI 側でアラートを弱める判断ができるようにしている。
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from .llm_router import RoutingStrategy

# 何日分のサンプルがあれば外挿を信頼するか
MIN_DAYS_FOR_CONFIDENCE = 3
MIN_DAYS_FOR_HIGH_CONFIDENCE = 7


def days_in_month_of(d: date | None = None) -> int:
    """その月の実日数を返す(2月=28/29, 4月=30, 1月=31).

    旧実装は days_in_month=30 固定だった。1月(31日)では着地見込みを約3%過小評価し、
    2月(28日)では約7%過大評価する。予算アラートの閾値付近では判定が反転しうる。
    """
    d = d or date.today()
    return calendar.monthrange(d.year, d.month)[1]


@dataclass
class BudgetStatus:
    spent_usd: float
    budget_usd: float
    day_of_month: int
    days_in_month: int
    projected_usd: float          # 月末着地見込み
    over_budget: bool
    alert_level: str              # ok / warn / critical
    remaining_usd: float
    daily_avg_usd: float          # 日次平均消費
    confidence: str               # low / medium / high(サンプル日数由来)
    burn_rate_ratio: float        # 予算に対する消費ペース(1.0 で予算ぴったり着地)

    def as_dict(self):
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


def project_month_end(spent_usd: float, day_of_month: int,
                      days_in_month: int | None = None) -> float:
    """日割りペースで月末コストを外挿."""
    if spent_usd < 0:
        raise ValueError("spent_usd must be >= 0")
    days_in_month = days_in_month if days_in_month is not None else days_in_month_of()
    if day_of_month <= 0:
        return 0.0
    # 経過日数が月の日数を超える入力は不正(呼び出し側のバグ)。丸めて隠さない。
    if day_of_month > days_in_month:
        raise ValueError(f"day_of_month({day_of_month}) > days_in_month({days_in_month})")
    daily = spent_usd / day_of_month
    return daily * days_in_month


def _confidence(day_of_month: int) -> str:
    if day_of_month >= MIN_DAYS_FOR_HIGH_CONFIDENCE:
        return "high"
    if day_of_month >= MIN_DAYS_FOR_CONFIDENCE:
        return "medium"
    return "low"


class BudgetGuard:
    def __init__(self, budget_usd: float, warn_ratio: float = 0.8,
                 critical_ratio: float = 1.0) -> None:
        # 予算 0 / 負は設定ミス。黙って ok を返すと「守れている」と誤認させるため弾く。
        if budget_usd <= 0:
            raise ValueError("budget_usd must be > 0")
        if not 0 < warn_ratio <= critical_ratio:
            raise ValueError("require 0 < warn_ratio <= critical_ratio")
        self.budget_usd = float(budget_usd)
        self.warn_ratio = float(warn_ratio)
        self.critical_ratio = float(critical_ratio)

    def status(self, spent_usd: float, day_of_month: int | None = None,
               days_in_month: int | None = None) -> BudgetStatus:
        today = date.today()
        day_of_month = day_of_month if day_of_month is not None else today.day
        days_in_month = days_in_month if days_in_month is not None else days_in_month_of(today)
        projected = project_month_end(spent_usd, day_of_month, days_in_month)
        ratio = projected / self.budget_usd
        level = "ok"
        if ratio >= self.critical_ratio:
            level = "critical"
        elif ratio >= self.warn_ratio:
            level = "warn"
        daily_avg = spent_usd / day_of_month if day_of_month > 0 else 0.0
        return BudgetStatus(
            spent_usd=spent_usd, budget_usd=self.budget_usd, day_of_month=day_of_month,
            days_in_month=days_in_month, projected_usd=projected,
            # 既に実支出が予算を超えている場合も over_budget とする
            over_budget=projected > self.budget_usd or spent_usd > self.budget_usd,
            alert_level=level,
            remaining_usd=max(0.0, self.budget_usd - spent_usd),
            daily_avg_usd=daily_avg,
            confidence=_confidence(day_of_month),
            burn_rate_ratio=ratio,
        )

    def choose_strategy(self, spent_usd: float, day_of_month: int | None,
                        requested: RoutingStrategy,
                        days_in_month: int | None = None) -> RoutingStrategy:
        """予算内ルーティング: 着地見込みが予算超過なら cost 戦略へ自動降格.

        サンプル日数が少なく信頼度が low の場合は降格しない。月初 1 日目の
        スパイクで月中ずっと最安モデルに固定される事故を避けるため。
        """
        st = self.status(spent_usd, day_of_month, days_in_month)
        if st.alert_level == "critical" and st.confidence != "low":
            return RoutingStrategy.COST      # 超過見込み -> 最安固定で守る
        return requested


@dataclass
class CostAnomaly:
    is_anomaly: bool
    current: float
    baseline: float
    ratio: float
    note: str = ""


def detect_cost_anomaly(recent_daily: list[float], today: float,
                        threshold: float = 2.0,
                        absolute_floor_usd: float = 0.0) -> CostAnomaly:
    """直近平均に対し today のコストが threshold 倍以上なら異常.

    absolute_floor_usd: 金額がこの値未満なら比率が大きくても異常としない。
    ベースラインが $0.01 のとき $0.03 は「3倍」だが実害が無く、
    ノイズアラートで運用者がアラート全体を無視するようになるため。
    """
    if threshold <= 1:
        raise ValueError("threshold must be > 1")
    base = sum(recent_daily) / len(recent_daily) if recent_daily else 0.0
    ratio = (today / base) if base > 0 else 0.0
    is_anom = base > 0 and ratio >= threshold and today >= absolute_floor_usd
    if is_anom:
        note = f"直近平均の{ratio:.1f}倍"
    elif base <= 0:
        note = "ベースライン未確立(判定不能)"
    else:
        note = "正常範囲"
    return CostAnomaly(is_anomaly=is_anom, current=today, baseline=round(base, 6),
                       ratio=round(ratio, 2), note=note)
