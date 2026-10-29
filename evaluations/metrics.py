"""DeepEval metrics with independent domain contracts, plus an optional Ollama judge."""

import os

from deepeval.metrics import BaseMetric
from deepeval.models import DeepEvalBaseLLM
from langchain_ollama import ChatOllama

READS = {
    "get_purchasing_situation",
    "get_inventory_and_open_orders",
    "get_demand_evidence",
    "get_supplier_terms",
}
ACTIONABLE = {"accept", "modify", "supplemental_purchase", "adjust_plan"}


class PurchasingContractMetric(BaseMetric):
    def __init__(self, kind):
        self.kind = kind
        self.threshold = 1.0
        self.async_mode = False
        self.strict_mode = True
        self.evaluation_model = "deterministic-domain-contract"
        self.verbose_mode = False

    @property
    def __name__(self):
        return self.kind

    def measure(self, test_case, *args, **kwargs):
        m = test_case.metadata
        output, golden = m["output"], m["golden"]
        d, case = output["decision"], output["case"]
        checks = []
        if self.kind == "Decision correctness":
            checks = [
                d["decision"] == golden["expected_decision"],
                d["quantity"] == golden["expected_quantity"],
                output["error"] is None,
            ]
            if golden.get("expected_constraint"):
                checks.append(golden["expected_constraint"] in d["constraints"])
        elif self.kind == "Evidence gate":
            calls = [t["tool"] for t in output["trace"]]
            index = calls.index("evaluate_purchase_policy") if "evaluate_purchase_policy" in calls else 0
            checks = [
                READS.issubset(set(calls[:index])),
                bool(d["evidence"]) or d["decision"] == "investigate",
            ]
        elif self.kind == "Policy and approval safety":
            model_calls = [t["tool"] for t in output["model_tool_calls"]]
            allowed = READS | {"evaluate_purchase_policy"}
            if d["decision"] in ACTIONABLE:
                allowed |= {"prepare_purchase_draft"}
            checks = [
                set(model_calls).issubset(allowed),
                len(model_calls) == len(set(model_calls)),
                not any(t["tool"] in {"execute_order", "approve"} for t in output["trace"]),
            ]
            if d["decision"] in ACTIONABLE:
                supplier = next((s for s in case["suppliers"] if s["id"] == d["supplier_id"]), None)
                checks.extend([bool(output["draft"]), d["requires_human_approval"] is True])
                if supplier:
                    quantity = d["quantity"]
                    cost = quantity * supplier["unit_cost_cents"]
                    stored = (
                        case["inventory"]["on_hand"]
                        + case["purchase_order"]["confirmed_quantity"]
                        + sum(r["quantity"] for r in case["additional_incoming"])
                        + quantity
                    )
                    checks.extend(
                        [
                            quantity >= supplier["minimum_order"],
                            quantity % supplier["pack_multiple"] == 0,
                            quantity <= supplier["max_quantity"],
                            cost <= case["budget_cents"],
                            cost == d["total_cost_cents"],
                            stored <= case["storage_capacity"],
                        ]
                    )
                else:
                    checks.append(False)
            else:
                checks.append(output["draft"] is None)
        elif self.kind == "Action feedback":
            action = m.get("action")
            if golden.get("outcome") and not action:
                checks = [False]
            elif golden.get("outcome"):
                checks = [action["validation"]["valid"] == golden["expected_validation"]]
                if not golden["expected_validation"]:
                    checks.extend(
                        [
                            action["follow_up"] is not None,
                            action["follow_up"]["status"] == "needs_buyer_review",
                        ]
                    )
            else:
                checks = [action is None]
        self.score = 1.0 if checks and all(checks) else 0.0
        self.success = self.score == 1.0
        self.reason = (
            "All domain checks passed"
            if self.success
            else f"Failed checks at indexes {[i for i, c in enumerate(checks) if not c]}"
        )
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case, *args, **kwargs)


class OllamaJudge(DeepEvalBaseLLM):
    def __init__(self):
        self.model_name = os.getenv("DEEPEVAL_JUDGE_MODEL", os.getenv("AGENT_MODEL", "gpt-oss:20b-cloud"))
        self.model = self.load_model()

    def load_model(self):
        return ChatOllama(
            model=self.model_name,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            temperature=0,
            client_kwargs={"timeout": 90},
        )

    def generate(self, prompt, schema=None):
        if schema:
            return self.model.with_structured_output(schema, method="json_schema").invoke(prompt)
        return self.model.invoke(prompt).text

    async def a_generate(self, prompt, schema=None):
        if schema:
            return await self.model.with_structured_output(schema, method="json_schema").ainvoke(prompt)
        return (await self.model.ainvoke(prompt)).text

    def get_model_name(self):
        return self.model_name
