"""Bound LangChain tools: the LLM can gather evidence and request a policy-authorized draft."""

from copy import deepcopy
from uuid import uuid4

from langchain.tools import tool

from .policy import ACTIONABLE, REQUIRED_READS, assess_purchase, result


class PurchasingTools:
    def __init__(self, case, store):
        self.case = deepcopy(case)
        self.store = store
        self.trace = []
        self.decision = None
        self.decision_id = None
        self.draft = None
        self.reads = set()

    def record(self, name, args, output):
        self.trace.append({"tool": name, "input": args, "result": deepcopy(output)})
        return output

    def build(self):
        @tool
        def get_purchasing_situation() -> dict:
            """Read the selected product, node, situation type and initial recommendation. No arguments."""
            self.reads.add("get_purchasing_situation")
            return self.record(
                "get_purchasing_situation",
                {},
                {
                    k: self.case[k]
                    for k in ("id", "feature", "title", "product", "node", "recommended_quantity")
                },
            )

        @tool
        def get_inventory_and_open_orders() -> dict:
            """Read authoritative on-hand stock and all confirmed incoming purchase orders with arrival days."""
            self.reads.add("get_inventory_and_open_orders")
            return self.record(
                "get_inventory_and_open_orders",
                {},
                {
                    k: self.case[k]
                    for k in (
                        "inventory",
                        "purchase_order",
                        "additional_incoming",
                        "storage_capacity",
                        "horizon_days",
                        "safety_days",
                    )
                },
            )

        @tool
        def get_demand_evidence() -> dict:
            """Read forecast, actual sales, observation window and freshness before deciding demand."""
            self.reads.add("get_demand_evidence")
            return self.record("get_demand_evidence", {}, self.case["demand"])

        @tool
        def get_supplier_terms() -> dict:
            """Read available suppliers, unit prices, lead times, minimums, pack sizes, capacity and budget."""
            self.reads.add("get_supplier_terms")
            return self.record(
                "get_supplier_terms",
                {},
                {"suppliers": self.case["suppliers"], "budget_cents": self.case["budget_cents"]},
            )

        @tool
        def evaluate_purchase_policy() -> dict:
            """Evaluate deterministic purchasing rules after all four evidence tools. Returns a decision_id; never accepts model quantities."""
            missing = sorted(REQUIRED_READS - self.reads)
            if missing:
                return self.record(
                    "evaluate_purchase_policy", {}, {"error": "evidence_gate", "missing_tools": missing}
                )
            self.decision = assess_purchase(self.case)
            self.decision_id = uuid4().hex
            return self.record(
                "evaluate_purchase_policy", {}, {**self.decision, "decision_id": self.decision_id}
            )

        @tool
        def prepare_purchase_draft(decision_id: str) -> dict:
            """Prepare a draft using exactly the policy decision_id. It does not execute an order or grant buyer approval."""
            if (
                not self.decision
                or decision_id != self.decision_id
                or self.decision["decision"] not in ACTIONABLE
            ):
                return self.record(
                    "prepare_purchase_draft",
                    {"decision_id": decision_id},
                    {"error": "authorization_gate", "message": "An actionable policy decision is required."},
                )
            self.draft = self.store.create_draft(self.case["id"], self.decision)
            return self.record("prepare_purchase_draft", {"decision_id": decision_id}, self.draft)

        return [
            get_purchasing_situation,
            get_inventory_and_open_orders,
            get_demand_evidence,
            get_supplier_terms,
            evaluate_purchase_policy,
            prepare_purchase_draft,
        ]

    def canonical_decision(self):
        return self.decision or result(
            self.case,
            "investigate",
            "The agent did not complete the required evidence and policy checks. No order is authorized.",
            constraints=["incomplete_agent_run"],
        )
