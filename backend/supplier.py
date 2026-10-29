"""Replaceable mock supplier API and independent response validation."""

from uuid import uuid4


def submit_order(authorization, draft_id, outcome):
    quantity = (
        authorization["quantity"]
        if outcome == "confirmed"
        else authorization["quantity"] // 2
        if outcome == "partial"
        else 0
    )
    return {
        "id": "PO-" + uuid4().hex[:10].upper(),
        "draft_id": draft_id,
        "status": outcome,
        "requested_quantity": authorization["quantity"],
        "confirmed_quantity": quantity,
        "supplier_id": authorization["supplier_id"],
        "unit_cost_cents": authorization["unit_cost_cents"],
        "total_cost_cents": quantity * authorization["unit_cost_cents"],
        "eta_days": authorization["eta_days"],
    }


def validate_response(authorization, response):
    quantity = response.get("confirmed_quantity")
    price = response.get("unit_cost_cents")
    actual_cost = response.get("total_cost_cents")
    valid_numbers = all(type(n) is int and n >= 0 for n in (quantity, price, actual_cost))
    within_authorization = (
        valid_numbers
        and quantity <= authorization["quantity"]
        and actual_cost <= authorization["total_cost_cents"]
    )
    checks = {
        "quantity_matches": quantity == authorization["quantity"],
        "price_matches": price == authorization["unit_cost_cents"],
        "supplier_matches": response.get("supplier_id") == authorization["supplier_id"],
        "eta_matches": response.get("eta_days") == authorization["eta_days"],
        "cost_consistent": valid_numbers and actual_cost == quantity * price,
    }
    commit_allowed = within_authorization and all(
        checks[k] for k in ("price_matches", "supplier_matches", "eta_matches", "cost_consistent")
    )
    valid = commit_allowed and all(checks.values()) and response.get("status") == "confirmed"
    return {
        "valid": valid,
        "status": response.get("status", "unknown"),
        "confirmed_quantity": quantity,
        **checks,
        "commit_allowed": commit_allowed,
        "message": "Supplier quantity, price and identity match the approved order."
        if valid
        else "Supplier did not confirm the approved terms. Buyer review is required.",
    }
