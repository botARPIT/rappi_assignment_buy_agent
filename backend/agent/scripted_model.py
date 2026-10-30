"""Deterministic test double for exercising the real tool and approval workflow."""

import json
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from backend.action_feedback import supplier_requires_refresh


def _scripted_independent_review(tool_messages):
    """Provide reproducible test-mode output from the evidence, without calling policy code."""
    reads = {"get_purchasing_situation", "get_inventory_and_open_orders", "get_demand_evidence", "get_supplier_terms"}
    evidence = {message.name: json.loads(message.content) for message in tool_messages if message.name in reads}
    situation = evidence["get_purchasing_situation"]
    feedback_history = situation.get("action_feedback", [])
    stock = evidence["get_inventory_and_open_orders"]
    demand = evidence["get_demand_evidence"]
    suppliers = evidence["get_supplier_terms"]
    review = {"decision": "needs_review", "quantity": 0, "supplier_id": None, "estimated_cost_cents": 0,
              "reason": "The evidence or supplier constraints require buyer review."}

    def finish():
        if feedback_history:
            previous = feedback_history[-1]
            review["reason"] = (
                f"Previous supplier {previous['supplier_id']} outcome was {previous['outcome']}: "
                f"{previous['committed_quantity']}/{previous['requested_quantity']} units committed. "
                + review["reason"]
            )
        return review

    if demand["age_hours"] > 24 or (situation["feature"] == "demand_change" and demand["observed_days"] < 3):
        return finish()
    if situation["feature"] == "recommendation_review" and situation["recommended_quantity"] is None:
        return finish()
    daily = demand["forecast_daily"]
    if situation["feature"] == "demand_change":
        daily = max(daily, demand["recent_daily"])
    horizon = stock["horizon_days"]
    incoming = [{"quantity": stock["purchase_order"]["confirmed_quantity"],
                 "eta_days": stock["purchase_order"]["eta_days"]}, *stock["additional_incoming"]]
    gap = max(0, daily * (horizon + stock["safety_days"]) - stock["inventory"]["on_hand"]
              - sum(row["quantity"] for row in incoming if row["eta_days"] <= horizon))

    def projection(quantity=0, eta=0):
        balance = stock["inventory"]["on_hand"] + sum(row["quantity"] for row in incoming if row["eta_days"] == 0)
        if eta == 0:
            balance += quantity
        stockout = False
        for day in range(1, horizon + 1):
            balance += sum(row["quantity"] for row in incoming if row["eta_days"] == day)
            if eta == day:
                balance += quantity
            balance -= daily
            stockout |= balance < 0
        return stockout, balance

    if gap == 0:
        if not projection()[0]:
            review.update(decision="no_buy", reason="Existing stock and confirmed supply cover demand.")
        return finish()
    candidates = []
    for supplier in suppliers["suppliers"]:
        if supplier_requires_refresh(supplier, feedback_history):
            review["reason"] = "The failed supplier needs refreshed terms; consider an eligible alternative or request buyer review."
            continue
        multiple = supplier["pack_multiple"]
        required = max(gap, supplier["minimum_order"])
        quantity = ((required + multiple - 1) // multiple) * multiple
        cost = quantity * supplier["unit_cost_cents"]
        projected = stock["inventory"]["on_hand"] + sum(row["quantity"] for row in incoming) + quantity
        stockout, ending = projection(quantity, supplier["lead_days"])
        if (supplier["available"] and quantity <= supplier["max_quantity"] and cost <= suppliers["budget_cents"]
                and projected <= stock["storage_capacity"] and supplier["lead_days"] <= horizon
                and not stockout and ending >= daily * stock["safety_days"]):
            candidates.append((cost, supplier["lead_days"], supplier["id"], quantity))
    if candidates:
        cost, _, supplier_id, quantity = min(candidates)
        review.update(decision="buy", quantity=quantity, supplier_id=supplier_id, estimated_cost_cents=cost,
                      reason=f"The proposed order addresses a {gap}-unit coverage gap within the stated constraints.")
    return finish()


class ScriptedPurchasingModel(BaseChatModel):
    """Runs the real LangChain graph without a network model; only for reproducible checks."""

    @property
    def _llm_type(self):
        return "scripted-test-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        done = {m.name for m in tool_messages}
        reads = [
            "get_purchasing_situation",
            "get_inventory_and_open_orders",
            "get_demand_evidence",
            "get_supplier_terms",
        ]
        pending = next((name for name in reads if name not in done), None)
        args = {}
        if pending:
            msg = AIMessage(
                content="",
                tool_calls=[{"name": pending, "args": args, "id": uuid4().hex, "type": "tool_call"}],
            )
        else:
            msg = AIMessage(content=json.dumps(_scripted_independent_review(tool_messages)))
        return ChatResult(generations=[ChatGeneration(message=msg)])
