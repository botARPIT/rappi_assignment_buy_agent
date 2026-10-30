"""Build supplier-outcome evidence and prevent repeating failed purchasing terms."""

from backend.purchasing.models import ActionFeedback


def _nonnegative_integer(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def build_action_feedback(authorization: dict, response: dict, validation: dict, supplier: dict) -> dict:
    """Keep malformed supplier numbers out of authoritative operational evidence."""
    confirmed = _nonnegative_integer(response.get("confirmed_quantity"))
    checks = ("quantity_matches", "price_matches", "supplier_matches", "eta_matches", "cost_consistent")
    failed_checks = [check for check in checks if not validation[check]]
    if response.get("status") != "confirmed":
        failed_checks.append("status_matches")
    if not validation["commit_allowed"]:
        failed_checks.append("commit_allowed")
    return ActionFeedback(
        order_id=response["id"],
        draft_id=response["draft_id"],
        supplier_id=authorization["supplier_id"],
        supplier_terms_version=supplier.get("terms_version", 1),
        outcome=response.get("status", "unknown"),
        requested_quantity=authorization["quantity"],
        confirmed_quantity=confirmed,
        committed_quantity=confirmed if validation["commit_allowed"] and confirmed is not None else 0,
        expected_unit_cost_cents=authorization["unit_cost_cents"],
        reported_unit_cost_cents=_nonnegative_integer(response.get("unit_cost_cents")),
        expected_eta_days=authorization["eta_days"],
        reported_eta_days=_nonnegative_integer(response.get("eta_days")),
        validation_passed=validation["valid"],
        failed_checks=failed_checks,
    ).model_dump()


def supplier_requires_refresh(supplier: dict, feedback_history: list[dict]) -> bool:
    """Only a newer authoritative terms version permits retrying a failed supplier.

    Other suppliers remain eligible. The LLM cannot change supplier terms or
    bypass this rule, and a new investigation alone does not count as a refresh.
    """
    return any(
        feedback["supplier_id"] == supplier["id"]
        and not feedback["validation_passed"]
        and supplier.get("terms_version", 1) <= feedback["supplier_terms_version"]
        for feedback in feedback_history
    )
