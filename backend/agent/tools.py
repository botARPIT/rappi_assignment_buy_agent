"""Read and write tools. The agent calls these rather than touching storage directly."""

from copy import deepcopy


class PurchasingTools:
    def __init__(self, cases, store):
        self.cases = cases
        self.store = store
        self.trace = []

    def _record(self, name, args, result):
        self.trace.append({"tool": name, "input": args, "result": result})
        return result

    def get_situation(self, case_id):
        case = self.cases.get(case_id)
        result = deepcopy(case) if case else None
        return self._record("get_situation", {"case_id": case_id}, result)

    def inspect_open_purchase_order(self, case):
        po = deepcopy(case["purchase_order"])
        po["shortfall"] = po["ordered_quantity"] - po["confirmed_quantity"]
        return self._record("inspect_open_purchase_order", {"po_id": po["id"]}, po)

    def get_inventory_and_demand(self, case):
        result = {
            **deepcopy(case["inventory"]),
            "supplier_lead_days": case["supplier_lead_days"],
            "storage_capacity": case["storage_capacity"],
        }
        return self._record("get_inventory_and_demand", {"sku": case["sku"], "node": case["node"]}, result)

    def find_alternate_supplier(self, case):
        result = deepcopy(case["alternate_supplier"])
        return self._record("find_alternate_supplier", {"sku": case["sku"], "node": case["node"]}, result)

    def check_constraints(self, case):
        result = {"budget_remaining": case["budget_remaining"], "storage_capacity": case["storage_capacity"]}
        return self._record("check_constraints", {"sku": case["sku"], "node": case["node"]}, result)

    def create_draft_order(self, case_id, decision):
        draft = self.store.create_draft(case_id, decision)
        return self._record("create_draft_order", {"case_id": case_id, "quantity": decision["quantity"]}, draft)

    def execute_order(self, draft_id, simulate_failure=False):
        result = self.store.execute_draft(draft_id, simulate_failure)
        return self._record("execute_order", {"draft_id": draft_id, "simulate_failure": simulate_failure}, result)

    def validate_order(self, draft_id):
        result = self.store.validate_draft(draft_id)
        return self._record("validate_order", {"draft_id": draft_id}, result)
