"""Shortfall workflow orchestration; policy owns decisions, tools own side effects."""

from .policy import assess_shortfall
from .tools import PurchasingTools


class PurchasingAgent:
    def __init__(self, cases, store):
        self.cases = cases
        self.store = store

    def investigate_shortfall(self, case_id):
        tools = PurchasingTools(self.cases, self.store)
        case = tools.get_situation(case_id)
        if not case:
            raise KeyError(case_id)
        po = tools.inspect_open_purchase_order(case)
        inventory = tools.get_inventory_and_demand(case)
        alternate = tools.find_alternate_supplier(case)
        constraints = tools.check_constraints(case)
        decision = assess_shortfall(case, po, inventory, alternate, constraints)
        draft = None
        if decision["decision"] == "supplemental_purchase":
            draft = tools.create_draft_order(case_id, decision)
        return {
            "case": case,
            "decision": decision,
            "draft": draft,
            "trace": tools.trace,
            "message": self._message(decision),
        }

    @staticmethod
    def _message(decision):
        return {
            "supplemental_purchase": "A supplemental order is ready for buyer approval.",
            "no_action": "No additional purchase is needed based on the available evidence.",
            "escalate": "The situation needs buyer review. The agent did not place an order.",
        }[decision["decision"]]
