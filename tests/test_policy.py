from copy import deepcopy

import pytest

from backend.agent.data import get_cases
from backend.agent.policy import assess_purchase


@pytest.fixture
def case():
    return get_cases()["supplier-shortfall"]


def test_assignment_recommendation_is_modified_not_blindly_accepted():
    decision = assess_purchase(get_cases()["recommendation-review"])
    assert (decision["decision"], decision["quantity"], decision["total_cost_cents"]) == (
        "modify",
        150,
        150000,
    )


def test_shortfall_counts_confirmed_units_not_original_order(case):
    decision = assess_purchase(case)
    assert decision["evidence"]["available_units_on_hand_plus_confirmed"] == 370
    assert decision["evidence"]["uncovered_units"] == 110
    assert decision["quantity"] == 150
    assert decision["evidence"]["budget_before_order"] == 3000
    assert decision["evidence"]["projected_budget_after_full_confirmation"] == 1200


def test_demand_change_uses_evidence_not_old_forecast():
    decision = assess_purchase(get_cases()["demand-spike"])
    assert (decision["decision"], decision["evidence"]["effective_daily_demand"]) == ("adjust_plan", 30)


@pytest.mark.parametrize(
    "field,value,reason",
    [("age_hours", 25, "stale_demand"), ("observed_days", 2, "insufficient_demand_evidence")],
)
def test_insufficient_demand_evidence_is_not_authorization(field, value, reason):
    case = get_cases()["demand-spike"]
    case["demand"][field] = value
    d = assess_purchase(case)
    assert d["decision"] == "investigate" and reason in d["constraints"] and d["quantity"] == 0


@pytest.mark.parametrize("budget,decision", [(180000, "supplemental_purchase"), (179999, "escalate")])
def test_budget_boundary_in_integer_cents(case, budget, decision):
    case["budget_cents"] = budget
    assert assess_purchase(case)["decision"] == decision


@pytest.mark.parametrize("capacity,decision", [(520, "supplemental_purchase"), (519, "escalate")])
def test_storage_boundary(case, capacity, decision):
    case["storage_capacity"] = capacity
    assert assess_purchase(case)["decision"] == decision


def test_minimum_then_pack_rounding(case):
    case["suppliers"][0].update(minimum_order=160, pack_multiple=50)
    assert assess_purchase(case)["quantity"] == 200


def test_no_supplier_capacity_is_not_ordered(case):
    case["suppliers"][0]["max_quantity"] = 149
    assert "supplier_capacity" in assess_purchase(case)["constraints"]


def test_timing_is_checked_even_when_total_units_suffice(case):
    case["inventory"]["on_hand"] = 30
    d = assess_purchase(case)
    assert d["decision"] == "escalate" and "arrival_too_late" in d["constraints"]


def test_covered_inventory_with_late_delivery_still_escalates(case):
    case["inventory"]["on_hand"] = 0
    case["purchase_order"]["confirmed_quantity"] = 600
    d = assess_purchase(case)
    assert d["decision"] == "escalate" and "arrival_too_late" in d["constraints"]


def test_incoming_after_horizon_is_not_coverage(case):
    case["purchase_order"]["eta_days"] = 30
    d = assess_purchase(case)
    assert d["evidence"]["available_units_on_hand_plus_confirmed"] == 120


def test_faster_feasible_supplier_wins_over_unusable_cheap_supplier(case):
    slow = deepcopy(case["suppliers"][0])
    slow.update(id="slow", name="Slow Supplier", lead_days=20, unit_cost_cents=100)
    case["suppliers"].append(slow)
    assert assess_purchase(case)["supplier_id"] == "central"


@pytest.mark.parametrize("invalid", ["negative_price", "negative_stock", "missing_budget", "string_money"])
def test_invalid_operational_data_fails_closed(case, invalid):
    if invalid == "negative_price":
        case["suppliers"][0]["unit_cost_cents"] = -1
    elif invalid == "negative_stock":
        case["inventory"]["on_hand"] = -1
    elif invalid == "missing_budget":
        del case["budget_cents"]
    else:
        case["budget_cents"] = "300000"
    assert assess_purchase(case)["decision"] == "investigate"


def test_no_available_suppliers(case):
    case["suppliers"][0]["available"] = False
    assert "supplier_unavailable" in assess_purchase(case)["constraints"]
