"""Deterministic purchasing policy: only this module may authorize an order."""

from math import ceil


def assess_shortfall(case, po, inventory, alternate, constraints):
    """Return a constrained, explainable recommendation for a supplier shortfall."""
    target = inventory["daily_demand"] * (
        inventory["supplier_lead_days"] + inventory["safety_days"]
    )
    available = inventory["on_hand"] + po["confirmed_quantity"]
    gap = max(0, target - available)
    evidence = {
        "target_units_through_replenishment": target,
        "available_units_on_hand_plus_confirmed": available,
        "uncovered_units": gap,
        "demand_window_days": inventory["supplier_lead_days"] + inventory["safety_days"],
    }
    if gap == 0:
        return {
            "decision": "no_action",
            "reason": "On-hand stock plus supplier-confirmed units cover forecast demand through lead time and safety stock.",
            "evidence": evidence,
            "quantity": 0,
            "estimated_cost": 0,
            "supplier": None,
            "constraints": [],
        }

    if not alternate["available"]:
        return _escalation("No alternate supplier has confirmed availability.", evidence, ["supplier_unavailable"])

    pack = max(1, alternate["pack_multiple"])
    order_floor = max(gap, alternate["minimum_order"])
    quantity = ceil(order_floor / pack) * pack
    cost = quantity * alternate["unit_cost"]
    blocked = []
    if quantity > alternate["max_quantity"]:
        blocked.append("alternate_supplier_capacity")
    if cost > constraints["budget_remaining"]:
        blocked.append("budget")
    projected_storage = inventory["on_hand"] + po["confirmed_quantity"] + quantity
    if projected_storage > constraints["storage_capacity"]:
        blocked.append("storage_capacity")
    evidence.update({
        "minimum_order_units": alternate["minimum_order"],
        "pack_multiple_units": pack,
        "proposed_units": quantity,
        "estimated_cost": cost,
        "budget_remaining": constraints["budget_remaining"],
        "projected_storage_units": projected_storage,
        "storage_capacity_units": constraints["storage_capacity"],
        "alternate_supplier_lead_days": alternate["lead_days"],
    })
    if blocked:
        return _escalation(
            "A top-up is needed, but the required quantity violates one or more purchasing constraints. No order was placed.",
            evidence,
            blocked,
        )
    return {
        "decision": "supplemental_purchase",
        "reason": f"Confirmed supply leaves a {gap}-unit coverage gap. A {quantity}-unit top-up from {alternate['name']} meets its minimum and pack size while staying within budget and storage limits.",
        "evidence": evidence,
        "quantity": quantity,
        "estimated_cost": cost,
        "supplier": alternate["name"],
        "constraints": [],
        "requires_human_approval": True,
    }


def _escalation(reason, evidence, constraints):
    return {
        "decision": "escalate",
        "reason": reason,
        "evidence": evidence,
        "quantity": 0,
        "estimated_cost": 0,
        "supplier": None,
        "constraints": constraints,
        "requires_human_approval": True,
    }
