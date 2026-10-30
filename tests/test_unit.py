"""Focused unit tests for purchasing rules and evaluation metrics."""

import pytest
from deepeval.test_case import LLMTestCase
from pydantic import ValidationError

from backend.agent.data import get_cases
from backend.agent.policy import assess_purchase
from backend.agent.review import compare_recommendations, parse_recommendation
from backend.supplier import submit_order, validate_response
from evaluations.metrics import EVIDENCE_TOOLS, AnswerCorrectnessMetric, ToolEfficiencyMetric


def test_review_changes_recommendation_to_feasible_order():
    result = assess_purchase(get_cases()["recommendation-review"])
    assert (result["decision"], result["quantity"], result["total_cost_cents"]) == ("modify", 150, 150000)
    assert result["evidence"]["uncovered_units"] == 120


def test_only_confirmed_incoming_stock_counts():
    result = assess_purchase(get_cases()["supplier-shortfall"])
    assert result["evidence"]["available_units_on_hand_plus_confirmed"] == 370
    assert result["evidence"]["uncovered_units"] == 110


def test_demand_change_uses_recent_sales():
    result = assess_purchase(get_cases()["demand-spike"])
    assert (result["decision"], result["evidence"]["effective_daily_demand"]) == ("adjust_plan", 30)


def test_stale_demand_cannot_authorize_purchase():
    case = get_cases()["supplier-shortfall"]
    case["demand"]["age_hours"] = 25
    result = assess_purchase(case)
    assert result["decision"] == "investigate" and "stale_demand" in result["constraints"]


def test_budget_boundary_is_checked_in_cents():
    case = get_cases()["supplier-shortfall"]
    case["budget_cents"] = 180000
    assert assess_purchase(case)["decision"] == "supplemental_purchase"
    case["budget_cents"] = 179999
    assert "budget" in assess_purchase(case)["constraints"]


def test_storage_boundary_blocks_excess_inventory():
    case = get_cases()["supplier-shortfall"]
    case["storage_capacity"] = 519
    assert "storage_capacity" in assess_purchase(case)["constraints"]


def test_late_delivery_does_not_prevent_stockout():
    case = get_cases()["supplier-shortfall"]
    case["inventory"]["on_hand"] = 0
    assert "arrival_too_late" in assess_purchase(case)["constraints"]


def test_supplier_price_change_is_not_authorized():
    authorization = assess_purchase(get_cases()["supplier-shortfall"])
    response = submit_order(authorization, "draft", "confirmed")
    response["unit_cost_cents"] += 100
    response["total_cost_cents"] = response["unit_cost_cents"] * response["confirmed_quantity"]
    validation = validate_response(authorization, response)
    assert not validation["valid"] and not validation["commit_allowed"]


def test_nonpurchase_recommendation_cannot_include_order_terms():
    with pytest.raises(ValidationError):
        parse_recommendation('{"decision":"no_buy","quantity":5,"supplier_id":null,"estimated_cost_cents":0,"reason":"covered"}')


def test_comparison_reports_agent_quantity_disagreement():
    policy = assess_purchase(get_cases()["recommendation-review"])
    agent = {"decision": "buy", "quantity": 100, "supplier_id": "central", "estimated_cost_cents": 150000}
    assert compare_recommendations(policy, agent) == {"status": "disagree", "differences": ["quantity"]}


def test_tool_efficiency_penalizes_missing_and_repeated_calls():
    calls = [{"tool": name, "input": {}} for name in sorted(EVIDENCE_TOOLS)]
    case = LLMTestCase(input="case", actual_output="answer", metadata={"output": {"model_tool_calls": calls}})
    metric = ToolEfficiencyMetric()
    assert metric.measure(case) == 1.0
    calls.append(calls[0])
    assert metric.measure(case) == 0.8
    calls[:] = calls[:2]
    assert metric.measure(case) == 0.5


def test_answer_correctness_checks_the_agent_answer_not_policy():
    expected = {"decision": "buy", "quantity": 150, "supplier_id": "central", "estimated_cost_cents": 150000}
    output = {"agent_recommendation": dict(expected), "decision": {"quantity": 150}, "error": None, "review_error": None}
    case = LLMTestCase(input="case", actual_output="answer", metadata={"output": output, "expected_recommendation": expected})
    metric = AnswerCorrectnessMetric()
    assert metric.measure(case) == 1.0
    output["agent_recommendation"]["quantity"] = 100
    assert metric.measure(case) == 0.0
