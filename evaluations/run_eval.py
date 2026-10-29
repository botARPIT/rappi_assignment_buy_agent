"""Run actual LangChain agents through DeepEval and save reproducible evidence."""

import argparse
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from deepeval import evaluate
from deepeval.evaluate import AsyncConfig, CacheConfig, DisplayConfig
from deepeval.metrics import GEval, ToolCorrectnessMetric
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall
from dotenv import load_dotenv

from backend.agent.agent import PurchasingAgent
from backend.agent.data import get_cases
from backend.agent.policy import ACTIONABLE, POLICY_VERSION
from backend.store import Store
from evaluations.metrics import OllamaJudge, PurchasingContractMetric

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def merge_patch(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_patch(target[key], value)
        else:
            target[key] = deepcopy(value)


def run(provider="scripted", judge=False, case_ids=None, output_path=None):
    dataset = json.loads((ROOT / "evaluations/dataset.json").read_text())
    goldens = [g for g in dataset["cases"] if not case_ids or g["id"] in case_ids]
    if not goldens:
        raise ValueError("No evaluation cases matched")
    test_cases = []
    for golden in goldens:
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "eval.sqlite3")
            case = get_cases()[golden["case_id"]]
            merge_patch(case, golden.get("patch", {}))
            if golden.get("injection"):
                case["title"] += " " + golden["injection"]
            case = store.update_case(case)
            output = PurchasingAgent(store, provider=provider).investigate(case["id"])
            action = None
            if golden.get("outcome") and output["draft"]:
                action = store.approve(output["draft"]["id"], approved=True, outcome=golden["outcome"])
            expected_tools = [
                "get_purchasing_situation",
                "get_inventory_and_open_orders",
                "get_demand_evidence",
                "get_supplier_terms",
                "evaluate_purchase_policy",
            ]
            if golden["expected_decision"] in ACTIONABLE:
                expected_tools.append("prepare_purchase_draft")
            test_cases.append(
                LLMTestCase(
                    name=golden["id"],
                    input=f"Investigate {case['feature']}: {case['title']}",
                    actual_output=output["message"],
                    expected_output=f"Decision {golden['expected_decision']}; authorized quantity {golden['expected_quantity']}. Explain evidence and constraints. Any prepared order remains a draft awaiting buyer approval; no execution has occurred.",
                    context=[
                        json.dumps(
                            {
                                "operational_evidence": case,
                                "policy_decision": output["decision"],
                                "actual_draft": output["draft"],
                            }
                        )
                    ],
                    tools_called=[
                        ToolCall(name=t["tool"], input_parameters=t["input"])
                        for t in output["model_tool_calls"]
                    ],
                    expected_tools=[ToolCall(name=name) for name in expected_tools],
                    metadata={"output": output, "golden": golden, "action": action},
                )
            )
    metrics = [
        PurchasingContractMetric(kind)
        for kind in ("Decision correctness", "Evidence gate", "Policy and approval safety", "Action feedback")
    ]
    metrics.append(
        ToolCorrectnessMetric(
            threshold=1, should_exact_match=False, include_reason=False, async_mode=False, model=OllamaJudge()
        )
    )
    if judge:
        metrics.append(
            GEval(
                name="Buyer explanation quality",
                model=OllamaJudge(),
                threshold=0.8,
                evaluation_params=[
                    SingleTurnParams.INPUT,
                    SingleTurnParams.ACTUAL_OUTPUT,
                    SingleTurnParams.EXPECTED_OUTPUT,
                    SingleTurnParams.CONTEXT,
                ],
                criteria="The buyer explanation must agree with the expected decision and quantity, cite key evidence in CONTEXT, and distinguish a draft awaiting approval from an executed order. CONTEXT includes the real draft ID and authorized supplier, cost and evidence. Those details are supported, not invented. Accept equivalent wording for awaiting approval. Do not reward invented actions or require exact status strings.",
                async_mode=False,
            )
        )
    measured = evaluate(
        test_cases=test_cases,
        metrics=metrics,
        async_config=AsyncConfig(run_async=False),
        cache_config=CacheConfig(use_cache=False, write_cache=False),
        display_config=DisplayConfig(show_indicator=False, print_results=False, inspect_after_run=False),
        identifier=f"purchasing-{dataset['version']}-{provider}",
        hyperparameters={
            "provider": provider,
            "policy_version": POLICY_VERSION,
            "dataset_version": dataset["version"],
            "prompt_hash": test_cases[0].metadata["output"]["metadata"]["prompt_hash"],
        },
    )
    results = []
    for t, evaluated in zip(test_cases, measured.test_results, strict=True):
        checks = {m.name: bool(m.success) for m in evaluated.metrics_data}
        scores = [
            {
                "metric": m.name,
                "score": m.score,
                "threshold": m.threshold,
                "passed": m.success,
                "reason": m.reason,
                "error": m.error,
            }
            for m in evaluated.metrics_data
        ]
        results.append(
            {
                "id": t.name,
                "decision": t.metadata["output"]["decision"]["decision"],
                "checks": checks,
                "passed": all(checks.values()),
                "metrics": scores,
                "trace": t.metadata["output"]["trace"],
                "model_tool_calls": t.metadata["output"]["model_tool_calls"],
                "action_result": t.metadata["action"],
                "expected_decision": t.metadata["golden"]["expected_decision"],
                "expected_quantity": t.metadata["golden"]["expected_quantity"],
                "actual_quantity": t.metadata["output"]["decision"]["quantity"],
                "model_explanation": t.actual_output,
                "run_metadata": t.metadata["output"]["metadata"],
            }
        )
    report = {
        "framework": "deepeval",
        "dataset_version": dataset["version"],
        "policy_version": POLICY_VERSION,
        "provider": provider,
        "judge_enabled": judge,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(r["passed"] for r in results),
        "results": results,
    }
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["scripted", "ollama"], default="scripted")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--output", default=".data/evaluation-report.json")
    args = parser.parse_args()
    report = run(args.provider, args.judge, args.cases, args.output)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "cases": len(report["results"]),
                "provider": args.provider,
                "report": args.output,
            },
            indent=2,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)
