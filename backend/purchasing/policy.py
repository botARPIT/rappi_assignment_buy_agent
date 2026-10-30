"""Pure policy gates. The model cannot authorize quantities, spending, or execution."""

from math import ceil
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from backend.purchasing.feedback import supplier_requires_refresh

from .scenario_policies import (
    BASE_POLICY_ID,
    BASE_POLICY_VERSION,
    ScenarioPolicy,
    get_scenario_policy,
    validate_base_policy,
)

POLICY_VERSION = "2.2.0"
ACTIONABLE = {"accept", "modify", "supplemental_purchase", "adjust_plan"}
REQUIRED_READS = {
    "get_purchasing_situation",
    "get_inventory_and_open_orders",
    "get_demand_evidence",
    "get_supplier_terms",
}


def result(case, decision, reason, evidence=None, constraints=None, **kwargs):
    """Build the canonical policy result and derive whether buyer approval is required."""
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
        "rejected_suppliers": [],
        "case_revision": case.get("revision"),
        "policy_version": POLICY_VERSION,
        "requires_human_approval": decision in ACTIONABLE,
        **kwargs,
    }


def incoming(case):
    """Collect confirmed units from the original order and later commitments."""
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


def _calculate_coverage_evidence(case, daily):
    """Calculate the target, confirmed supply, gap, and baseline stockout risk."""
    demand = case["demand"]
    target = daily * (case["horizon_days"] + case["safety_days"])
    available = case["inventory"]["on_hand"] + sum(
        r["quantity"] for r in incoming(case) if r["eta_days"] <= case["horizon_days"]
    )
    gap = max(0, target - available)
    return {
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


def _apply_covered_inventory_policy(case, evidence, scenario: ScenarioPolicy):
    """Reject unnecessary buying, or escalate if confirmed supply arrives too late."""
    if evidence["baseline_first_stockout_day"] is not None:
        return result(
            case,
            "escalate",
            "Total supply is sufficient, but incoming deliveries arrive after a stockout. Expedite or transfer stock.",
            evidence,
            ["arrival_too_late"],
        )
    decision = scenario.covered_decision()
    if decision not in {"reject", "no_action"}:
        return result(
            case,
            "investigate",
            "Scenario policy returned an invalid covered-stock decision.",
            evidence,
            ["invalid_policy_result"],
        )
    return result(
        case,
        decision,
        "Existing inventory and confirmed orders cover the planning horizon and safety stock; additional buying is unnecessary.",
        evidence,
    )


def _supplier_availability_violation(supplier):
    """Enforce the supplier availability policy."""
    return None if supplier["available"] else "supplier_unavailable"


def _supplier_capacity_violation(supplier, quantity):
    """Enforce the supplier's maximum order quantity."""
    return "supplier_capacity" if quantity > supplier["max_quantity"] else None


def _purchase_budget_violation(case, cost):
    """Enforce the remaining purchase budget."""
    return "budget" if cost > case["budget_cents"] else None


def _storage_capacity_violation(case, projected_storage):
    """Enforce the storage limit after the proposed delivery."""
    return "storage_capacity" if projected_storage > case["storage_capacity"] else None


def _arrival_timing_violation(case, supplier, projection):
    """Enforce delivery within the planning horizon and before any stockout."""
    if supplier["lead_days"] > case["horizon_days"] or projection["first_stockout_day"] is not None:
        return "arrival_too_late"
    return None


def _safety_coverage_violation(case, daily, projection):
    """Enforce the safety-stock target at the end of the planning horizon."""
    return "insufficient_coverage" if projection["ending_stock"] < daily * case["safety_days"] else None


def _evaluate_supplier_plan(case, supplier, daily, gap):
    """Apply each supplier policy to one calculated order plan."""
    quantity = ceil(max(gap, supplier["minimum_order"]) / supplier["pack_multiple"]) * supplier["pack_multiple"]
    cost = quantity * supplier["unit_cost_cents"]
    projected_storage = case["inventory"]["on_hand"] + sum(r["quantity"] for r in incoming(case)) + quantity
    projection = timeline(case, daily, quantity, supplier["lead_days"])
    checks = (
        "supplier_feedback_requires_refresh"
        if supplier_requires_refresh(supplier, case["action_feedback"]) else None,
        _supplier_availability_violation(supplier),
        _supplier_capacity_violation(supplier, quantity),
        _purchase_budget_violation(case, cost),
        _storage_capacity_violation(case, projected_storage),
        _arrival_timing_violation(case, supplier, projection),
        _safety_coverage_violation(case, daily, projection),
    )
    violations = [violation for violation in checks if violation]
    plan = (cost, supplier["lead_days"], supplier["id"], supplier, quantity, projected_storage)
    return plan, violations


def _select_supplier_plan(case, daily, gap):
    """Select the cheapest feasible supplier and collect buyer-facing alternatives.

    Ties are resolved by lead time and supplier ID.
    """
    feasible, blocked, rejected = [], set(), []
    for supplier in case["suppliers"]:
        plan, violations = _evaluate_supplier_plan(case, supplier, daily, gap)
        if violations:
            blocked.update(violations)
            _, _, _, _, quantity, _ = plan
            cost = plan[0]
            rejected.append({
                "supplier_id": supplier["id"],
                "supplier_name": supplier["name"],
                "proposed_quantity": quantity,
                "estimated_cost": cost / 100,
                "eta_days": supplier["lead_days"],
                "constraints": violations,
            })
        else:
            feasible.append(plan)
    selected = min(feasible, key=lambda row: row[:3]) if feasible else None
    if selected:
        for alternative in feasible:
            if alternative is selected:
                continue
            cost, _, _, supplier, quantity, _ = alternative
            selected_cost, selected_eta, _ = selected[:3]
            if cost > selected_cost:
                reason = "higher_cost"
            elif supplier["lead_days"] > selected_eta:
                reason = "longer_lead_time"
            else:
                reason = "supplier_id_tiebreak"
            rejected.append({
                "supplier_id": supplier["id"],
                "supplier_name": supplier["name"],
                "proposed_quantity": quantity,
                "estimated_cost": cost / 100,
                "eta_days": supplier["lead_days"],
                "selection_reason": reason,
            })
    return selected, blocked, rejected


def _build_purchase_decision(case, evidence, supplier_plan, rejected_suppliers, scenario: ScenarioPolicy):
    """Build an actionable decision using the selected policy-approved supplier plan."""
    cost, _, _, supplier, quantity, storage = supplier_plan
    gap = evidence["uncovered_units"]
    decision = scenario.purchase_decision(case, quantity)
    if decision not in ACTIONABLE:
        return result(
            case,
            "investigate",
            "Scenario policy returned an invalid purchase decision.",
            evidence,
            ["invalid_policy_result"],
        )
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
        rejected_suppliers=rejected_suppliers,
    )


class PolicyState(TypedDict, total=False):
    raw_case: dict
    case: dict
    scenario: ScenarioPolicy
    components: list[dict]
    daily: int
    evidence: dict
    decision: dict
    steps: list[str]
    output: dict


def _validate_base_node(state: PolicyState) -> dict:
    case, issue = validate_base_policy(state["raw_case"])
    update = {"components": [{"id": BASE_POLICY_ID, "version": BASE_POLICY_VERSION}],
              "steps": ["validate_base"]}
    if issue is not None:
        details = {"validation_errors": list(issue.validation_errors)} if issue.validation_errors else {}
        update["decision"] = result(state["raw_case"], "investigate", issue.reason,
                                    constraints=[issue.constraint], **details)
    else:
        update["case"] = case
    return update


def _validate_scenario_node(state: PolicyState) -> dict:
    case = state["case"]
    scenario = get_scenario_policy(case["feature"])
    update = {"steps": [*state["steps"], "validate_scenario"]}
    if scenario is None:
        update["decision"] = result(case, "investigate", "No policy exists for this purchasing situation.",
                                    constraints=["unsupported_scenario"])
        return update
    update["scenario"] = scenario
    update["components"] = [*state["components"], {"id": scenario.policy_id, "version": scenario.version}]
    issue = scenario.validate(case)
    if issue is not None:
        update["decision"] = result(case, "investigate", issue.reason, constraints=[issue.constraint])
    return update


def _calculate_coverage_node(state: PolicyState) -> dict:
    daily = state["scenario"].daily_demand(state["case"])
    return {"daily": daily, "evidence": _calculate_coverage_evidence(state["case"], daily),
            "steps": [*state["steps"], "calculate_coverage"]}


def _covered_decision_node(state: PolicyState) -> dict:
    return {"decision": _apply_covered_inventory_policy(state["case"], state["evidence"], state["scenario"]),
            "steps": [*state["steps"], "covered_decision"]}


def _supplier_decision_node(state: PolicyState) -> dict:
    case, evidence = state["case"], state["evidence"]
    plan, blocked, rejected = _select_supplier_plan(case, state["daily"], evidence["uncovered_units"])
    if plan is None:
        reason = "Additional inventory is needed, but no supplier plan passes all constraints. Buyer intervention is required."
        if "supplier_feedback_requires_refresh" in blocked:
            reason += " Refresh failed supplier terms or provide a feasible alternative before another order."
        decision = result(
            case, "escalate",
            reason,
            evidence, sorted(blocked or {"no_supplier"}), rejected_suppliers=rejected,
        )
    else:
        decision = _build_purchase_decision(case, evidence, plan, rejected, state["scenario"])
    return {"decision": decision, "steps": [*state["steps"], "supplier_decision"]}


def _finalize_policy_node(state: PolicyState) -> dict:
    steps = [*state["steps"], "finalize_policy"]
    return {"output": {**state["decision"], "policy_components": state["components"], "policy_nodes": steps},
            "steps": steps}


def _route_validation(state: PolicyState) -> str:
    return "stop" if "decision" in state else "continue"


def _route_coverage(state: PolicyState) -> str:
    return "covered" if state["evidence"]["uncovered_units"] == 0 else "gap"


def _build_policy_graph():
    graph = StateGraph(PolicyState)
    graph.add_node("validate_base", _validate_base_node)
    graph.add_node("validate_scenario", _validate_scenario_node)
    graph.add_node("calculate_coverage", _calculate_coverage_node)
    graph.add_node("covered_decision", _covered_decision_node)
    graph.add_node("supplier_decision", _supplier_decision_node)
    graph.add_node("finalize_policy", _finalize_policy_node)
    graph.add_edge(START, "validate_base")
    graph.add_conditional_edges("validate_base", _route_validation,
                                {"stop": "finalize_policy", "continue": "validate_scenario"})
    graph.add_conditional_edges("validate_scenario", _route_validation,
                                {"stop": "finalize_policy", "continue": "calculate_coverage"})
    graph.add_conditional_edges("calculate_coverage", _route_coverage,
                                {"covered": "covered_decision", "gap": "supplier_decision"})
    graph.add_edge("covered_decision", "finalize_policy")
    graph.add_edge("supplier_decision", "finalize_policy")
    graph.add_edge("finalize_policy", END)
    return graph.compile()


POLICY_GRAPH = _build_policy_graph()


def assess_purchase(raw_case):
    """Run the deterministic base, scenario, coverage and supplier policy graph."""
    return POLICY_GRAPH.invoke({"raw_case": raw_case})["output"]


def matches_authorization(current, authorized):
    """Check that a fresh actionable decision still matches the approved terms."""
    fields = (
        "decision",
        "quantity",
        "supplier_id",
        "total_cost_cents",
        "unit_cost_cents",
        "eta_days",
        "case_revision",
        "policy_version",
        "policy_components",
    )
    return current["decision"] in ACTIONABLE and all(current.get(k) == authorized.get(k) for k in fields)
