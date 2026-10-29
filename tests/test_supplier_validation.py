from copy import deepcopy

import pytest

from backend.agent.policy import assess_purchase
from backend.supplier import submit_order, validate_response


@pytest.mark.parametrize(
    "field,value",
    [
        ("unit_cost_cents", 99999),
        ("supplier_id", "unapproved"),
        ("confirmed_quantity", 10000),
        ("eta_days", 30),
        ("total_cost_cents", 1),
        ("confirmed_quantity", -1),
        ("confirmed_quantity", "150"),
    ],
)
def test_independent_supplier_validation_quarantines_unapproved_terms(store, field, value):
    authorization = assess_purchase(store.get_case("supplier-shortfall"))
    response = submit_order(authorization, "draft", "confirmed")
    response[field] = value
    validation = validate_response(authorization, response)
    assert not validation["valid"]
    assert not validation["commit_allowed"]


def test_unapproved_supplier_terms_do_not_spend_or_commit_inventory(store, monkeypatch):
    authorization = assess_purchase(store.get_case("supplier-shortfall"))
    draft = store.create_draft("supplier-shortfall", authorization)
    response = submit_order(authorization, draft["id"], "confirmed")
    response["unit_cost_cents"] += 500
    response["total_cost_cents"] = response["unit_cost_cents"] * response["confirmed_quantity"]
    monkeypatch.setattr("backend.store.submit_order", lambda *args: deepcopy(response))
    result = store.approve(draft["id"], approved=True)
    assert not result["validation"]["valid"]
    assert result["follow_up"] is not None
    case = store.get_case("supplier-shortfall")
    assert case["budget_cents"] == 300000 and not case["additional_incoming"]
