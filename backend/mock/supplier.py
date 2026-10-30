"""Simulated supplier API for the demo purchasing workflow."""

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
