"""Run deterministic scenario acceptance checks without an LLM or credentials."""

import json
import tempfile
from pathlib import Path

from backend.agent.agent import PurchasingAgent
from backend.agent.data import get_cases
from backend.store import Store


ROOT = Path(__file__).resolve().parents[1]


def run():
    dataset = json.loads((ROOT / "evaluations/dataset.json").read_text())
    results = []
    with tempfile.TemporaryDirectory() as temp_dir:
        agent = PurchasingAgent(get_cases(), Store(Path(temp_dir) / "eval.sqlite3"))
        for golden in dataset["cases"]:
            output = agent.investigate_shortfall(golden["id"])
            decision = output["decision"]
            tool_names = [step["tool"] for step in output["trace"]]
            case = agent.cases[golden["id"]]
            evidence = decision["evidence"]
            facts = {
                "minimum_order": evidence.get("proposed_units", 0) >= case["alternate_supplier"]["minimum_order"],
                "pack_multiple": evidence.get("proposed_units", 0) % case["alternate_supplier"]["pack_multiple"] == 0,
                "budget": evidence.get("estimated_cost", 0) <= case["budget_remaining"],
                "storage_capacity": evidence.get("projected_storage_units", 0) <= case["storage_capacity"],
            }
            constraints_ok = all(
                facts[name] if decision["decision"] == "supplemental_purchase"
                else name in decision["constraints"]
                for name in golden["must_respect"]
            )
            checks = {
                "decision_correct": decision["decision"] == golden["expected_decision"],
                "required_tools_used": all(name in tool_names for name in golden["expected_tools"]),
                "no_unexpected_action": not (decision["decision"] != "supplemental_purchase" and output["draft"]),
                "approval_required_for_order": decision.get("requires_human_approval", False) == golden["requires_approval"],
                "constraints_respected": constraints_ok,
            }
            results.append({"id": golden["id"], "checks": checks, "passed": all(checks.values()),
                            "decision": decision["decision"], "tools": tool_names})

        # Exercise the tool side effects and feedback loop, including a failed supplier response.
        success_agent = PurchasingAgent(get_cases(), Store(Path(temp_dir) / "success.sqlite3"))
        success = success_agent.investigate_shortfall("supplier-shortfall")
        approved = success_agent.store.execute_draft(success["draft"]["id"])
        validated = success_agent.store.validate_draft(success["draft"]["id"])
        success_check = {"approval_creates_confirmed_order": approved["ok"] and approved["status"] == "supplier_confirmed",
                         "confirmation_validated": validated["valid"]}

        failure_agent = PurchasingAgent(get_cases(), Store(Path(temp_dir) / "failure.sqlite3"))
        failed = failure_agent.investigate_shortfall("supplier-shortfall")
        rejected = failure_agent.store.execute_draft(failed["draft"]["id"], simulate_failure=True)
        follow_up = failure_agent.store.validate_draft(failed["draft"]["id"])
        failure_check = {"supplier_rejection_recorded": rejected["ok"] and rejected["status"] == "supplier_rejected",
                         "failed_action_not_reported_as_success": not follow_up["valid"],
                         "follow_up_guidance_present": "reassess" in follow_up["message"].lower()}

    feedback = [
        {"id": "supplier-confirmation-feedback", "checks": success_check, "passed": all(success_check.values()),
         "decision": "confirmed and validated", "tools": ["execute_order", "validate_order"]},
        {"id": "supplier-rejection-feedback", "checks": failure_check, "passed": all(failure_check.values()),
         "decision": "rejected and escalated", "tools": ["execute_order", "validate_order"]},
    ]
    all_results = results + feedback
    return {"dataset_version": dataset["version"], "passed": all(r["passed"] for r in all_results), "results": all_results}


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
