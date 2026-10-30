"""Shared evidence validation and scenario-specific purchasing rules."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from .models import Situation

BASE_POLICY_ID = "purchasing_base"
BASE_POLICY_VERSION = "1.0.0"


@dataclass(frozen=True)
class PolicyIssue:
    reason: str
    constraint: str
    validation_errors: tuple[str, ...] = ()


def validate_base_policy(raw_case: dict) -> tuple[dict | None, PolicyIssue | None]:
    """Validate shared purchasing evidence before applying a scenario policy."""
    try:
        case = Situation.model_validate(raw_case).model_dump()
    except ValidationError as exc:
        return None, PolicyIssue(
            "Required purchasing evidence is missing or invalid.",
            "invalid_evidence",
            tuple(str(error["loc"]) for error in exc.errors()),
        )
    blank_fields = [name for name in ("id", "product", "sku", "node") if not case[name].strip()]
    if blank_fields:
        return None, PolicyIssue(
            "Situation identity, product, SKU and location must not be blank.",
            "invalid_evidence",
            tuple(blank_fields),
        )
    if case["demand"]["age_hours"] > 24:
        return None, PolicyIssue(
            "Demand evidence is older than 24 hours. Refresh it before buying.", "stale_demand"
        )
    return case, None


class ScenarioPolicy(Protocol):
    """Contract for the scenario rules composed with the shared purchasing policy."""

    policy_id: str
    version: str

    def validate(self, case: dict) -> PolicyIssue | None: ...

    def daily_demand(self, case: dict) -> int: ...

    def covered_decision(self) -> str: ...

    def purchase_decision(self, case: dict, quantity: int) -> str: ...


class RecommendationReviewPolicy:
    policy_id = "recommendation_review"
    version = "1.0.0"

    def validate(self, case: dict) -> PolicyIssue | None:
        if case["recommended_quantity"] is None:
            return PolicyIssue(
                "A recommended quantity is required to review the recommendation.",
                "missing_recommended_quantity",
            )
        return None

    def daily_demand(self, case: dict) -> int:
        return case["demand"]["forecast_daily"]

    def covered_decision(self) -> str:
        return "reject"

    def purchase_decision(self, case: dict, quantity: int) -> str:
        return "accept" if quantity == case["recommended_quantity"] else "modify"


class SupplierShortfallPolicy:
    policy_id = "supplier_shortfall"
    version = "1.0.0"

    def validate(self, case: dict) -> PolicyIssue | None:
        # The shared Situation schema requires the original order and confirmed quantity.
        return None

    def daily_demand(self, case: dict) -> int:
        return case["demand"]["forecast_daily"]

    def covered_decision(self) -> str:
        return "no_action"

    def purchase_decision(self, case: dict, quantity: int) -> str:
        return "supplemental_purchase"


class DemandChangePolicy:
    policy_id = "demand_change"
    version = "1.0.0"

    def validate(self, case: dict) -> PolicyIssue | None:
        if case["demand"]["observed_days"] < 3:
            return PolicyIssue(
                "At least three days of sales evidence are required before changing the forecast.",
                "insufficient_demand_evidence",
            )
        return None

    def daily_demand(self, case: dict) -> int:
        return max(case["demand"]["forecast_daily"], case["demand"]["recent_daily"])

    def covered_decision(self) -> str:
        return "no_action"

    def purchase_decision(self, case: dict, quantity: int) -> str:
        return "adjust_plan"


SCENARIO_POLICIES: dict[str, ScenarioPolicy] = {
    "recommendation_review": RecommendationReviewPolicy(),
    "supplier_shortfall": SupplierShortfallPolicy(),
    "demand_change": DemandChangePolicy(),
}


def get_scenario_policy(feature: str) -> ScenarioPolicy | None:
    return SCENARIO_POLICIES.get(feature)
