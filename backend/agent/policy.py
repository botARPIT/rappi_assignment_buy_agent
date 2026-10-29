"""Pure policy gates. The model cannot authorize quantities, spending, or execution."""

from math import ceil

from pydantic import ValidationError

from .models import Situation

POLICY_VERSION = "2.0.0"
ACTIONABLE = {"accept", "modify", "supplemental_purchase", "adjust_plan"}
REQUIRED_READS = {
    "get_purchasing_situation",
    "get_inventory_and_open_orders",
    "get_demand_evidence",
    "get_supplier_terms",
}


def result(case, decision, reason, evidence=None, constraints=None, **kwargs):
    return {
        "decision": decision,
        "reason": reason,
        "evidence": evidence or {},
        "constraints": constraints or [],
        "quantity": 0,
        "estimated_cost": 0,
        "total_cost_cents": 0,
        "supplier": None,
        "supplier_id": None,
        "case_revision": case.get("revision"),
        "policy_version": POLICY_VERSION,
        "requires_human_approval": decision in ACTIONABLE,
        **kwargs,
    }


def incoming(case):
    po = case["purchase_order"]
    return [{"quantity": po["confirmed_quantity"], "eta_days": po["eta_days"]}, *case["additional_incoming"]]


def timeline(case, daily, extra_quantity=0, extra_eta=0):
    """Conservative day buckets: deliveries arrive at the start of their ETA day."""
    balance = case["inventory"]["on_hand"]
    rows = incoming(case) + [{"quantity": extra_quantity, "eta_days": extra_eta}]
    balance += sum(r["quantity"] for r in rows if r["eta_days"] == 0)
    first_stockout = None
    for day in range(1, case["horizon_days"] + 1):
        balance += sum(r["quantity"] for r in rows if r["eta_days"] == day)
        balance -= daily
        if balance < 0 and first_stockout is None:
            first_stockout = day
    return {"first_stockout_day": first_stockout, "ending_stock": balance}


def assess_purchase(raw_case):
    try:
        case = Situation.model_validate(raw_case).model_dump()
    except ValidationError as exc:
        return result(
            raw_case,
            "investigate",
            "Required purchasing evidence is missing or invalid.",
            constraints=["invalid_evidence"],
            validation_errors=[str(e["loc"]) for e in exc.errors()],
        )
    demand = case["demand"]
    if demand["age_hours"] > 24:
        return result(
            case,
            "investigate",
            "Demand evidence is older than 24 hours. Refresh it before buying.",
            constraints=["stale_demand"],
        )
    daily = demand["forecast_daily"]
    if case["feature"] == "demand_change":
        if demand["observed_days"] < 3:
            return result(
                case,
                "investigate",
                "At least three days of sales evidence are required before changing the forecast.",
                constraints=["insufficient_demand_evidence"],
            )
        daily = max(daily, demand["recent_daily"])
    target = daily * (case["horizon_days"] + case["safety_days"])
    available = case["inventory"]["on_hand"] + sum(
        r["quantity"] for r in incoming(case) if r["eta_days"] <= case["horizon_days"]
    )
    gap = max(0, target - available)
    evidence = {
        "target_units_through_replenishment": target,
        "available_units_on_hand_plus_confirmed": available,
        "uncovered_units": gap,
        "demand_window_days": case["horizon_days"],
        "effective_daily_demand": daily,
        "forecast_daily": demand["forecast_daily"],
        "observed_daily": demand["recent_daily"],
        "baseline_first_stockout_day": timeline(case, daily)["first_stockout_day"],
        "budget_remaining": case["budget_cents"] / 100,
        "storage_capacity_units": case["storage_capacity"],
    }
    if gap == 0:
        if timeline(case, daily)["first_stockout_day"] is not None:
            return result(
                case,
                "escalate",
                "Total supply is sufficient, but incoming deliveries arrive after a stockout. Expedite or transfer stock.",
                evidence,
                ["arrival_too_late"],
            )
        decision = "reject" if case["feature"] == "recommendation_review" else "no_action"
        return result(
            case,
            decision,
            "Existing inventory and confirmed orders cover the planning horizon and safety stock; additional buying is unnecessary.",
            evidence,
        )
    feasible, blocked = [], set()
    for supplier in case["suppliers"]:
        if not supplier["available"]:
            blocked.add("supplier_unavailable")
            continue
        quantity = (
            ceil(max(gap, supplier["minimum_order"]) / supplier["pack_multiple"]) * supplier["pack_multiple"]
        )
        cost = quantity * supplier["unit_cost_cents"]
        projected_storage = (
            case["inventory"]["on_hand"] + sum(r["quantity"] for r in incoming(case)) + quantity
        )
        violations = []
        if quantity > supplier["max_quantity"]:
            violations.append("supplier_capacity")
        if cost > case["budget_cents"]:
            violations.append("budget")
        if projected_storage > case["storage_capacity"]:
            violations.append("storage_capacity")
        projection = timeline(case, daily, quantity, supplier["lead_days"])
        if supplier["lead_days"] > case["horizon_days"] or projection["first_stockout_day"] is not None:
            violations.append("arrival_too_late")
        if projection["ending_stock"] < daily * case["safety_days"]:
            violations.append("insufficient_coverage")
        if violations:
            blocked.update(violations)
        else:
            feasible.append(
                (cost, supplier["lead_days"], supplier["id"], supplier, quantity, projected_storage)
            )
    if not feasible:
        return result(
            case,
            "escalate",
            "Additional inventory is needed, but no supplier plan passes all constraints. Buyer intervention is required.",
            evidence,
            sorted(blocked or {"no_supplier"}),
        )
    cost, _, _, supplier, quantity, storage = min(feasible, key=lambda row: row[:3])
    decision = "supplemental_purchase"
    if case["feature"] == "recommendation_review":
        decision = "accept" if quantity == case["recommended_quantity"] else "modify"
    elif case["feature"] == "demand_change":
        decision = "adjust_plan"
    evidence.update(
        {
            "proposed_units": quantity,
            "estimated_cost": cost / 100,
            "budget_before_order": case["budget_cents"] / 100,
            "projected_budget_after_full_confirmation": (case["budget_cents"] - cost) / 100,
            "minimum_order_units": supplier["minimum_order"],
            "pack_multiple_units": supplier["pack_multiple"],
            "projected_storage_units": storage,
            "alternate_supplier_lead_days": supplier["lead_days"],
            "recommended_quantity": case["recommended_quantity"],
        }
    )
    return result(
        case,
        decision,
        f"The {gap}-unit coverage gap requires {quantity} units after supplier minimum and pack rules. {supplier['name']} can deliver in {supplier['lead_days']} days within budget and storage limits.",
        evidence,
        quantity=quantity,
        estimated_cost=cost / 100,
        total_cost_cents=cost,
        unit_cost_cents=supplier["unit_cost_cents"],
        supplier=supplier["name"],
        supplier_id=supplier["id"],
        eta_days=supplier["lead_days"],
    )


def matches_authorization(current, authorized):
    fields = (
        "decision",
        "quantity",
        "supplier_id",
        "total_cost_cents",
        "unit_cost_cents",
        "eta_days",
        "case_revision",
        "policy_version",
    )
    return current["decision"] in ACTIONABLE and all(current.get(k) == authorized.get(k) for k in fields)
