"""Evaluate the independent agent against versioned purchasing scenarios."""

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
from deepeval.test_case import LLMTestCase
from dotenv import load_dotenv

from backend.agent.agent import PurchasingAgent
from backend.agent.data import get_cases
from backend.agent.policy import POLICY_VERSION
from backend.store import Store
from evaluations.metrics import (
    EVIDENCE_TOOLS,
    AnswerCorrectnessMetric,
    OllamaJudge,
    ToolEfficiencyMetric,
    groundedness_metric,
)

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def merge_patch(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_patch(target[key], value)
        else:
            target[key] = deepcopy(value)


def make_test_case(name, case, output, expected):
    # Only actual evidence-tool results are shown to the groundedness judge.
    evidence = {step["tool"]: step["result"] for step in output["trace"] if step["tool"] in EVIDENCE_TOOLS}
    answer = output.get("agent_recommendation")
    return LLMTestCase(
        name=name,
        input=f"Investigate {case['feature']}: {case['title']}",
        actual_output=json.dumps(answer) if answer else "Independent recommendation unavailable.",
        expected_output=json.dumps(expected),
        context=[json.dumps(evidence)],
        metadata={"output": output, "expected_recommendation": expected},
    )


def run(provider="scripted", case_ids=None, output_path=None, judge_model=None):
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
            test_cases.append(make_test_case(golden["id"], case, output, golden["expected_recommendation"]))
            if golden.get("outcome") and golden.get("expected_recovery"):
                if output["draft"]:
                    store.approve(output["draft"]["id"], approved=True, outcome=golden["outcome"])
                    recovery = PurchasingAgent(store, provider=provider).investigate(case["id"])
                    test_cases.append(make_test_case(
                        golden["id"] + "/recovery", recovery["case"], recovery, golden["expected_recovery"]
                    ))
                else:
                    # A missing draft prevents the recovery scenario from running; it must not silently pass.
                    missing = deepcopy(output)
                    missing["agent_recommendation"] = None
                    missing["error"] = "Recovery unavailable: the initial investigation did not prepare a draft."
                    test_cases.append(make_test_case(golden["id"] + "/recovery", case, missing, golden["expected_recovery"]))

    metrics = [ToolEfficiencyMetric(), groundedness_metric(judge_model or OllamaJudge()), AnswerCorrectnessMetric()]
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
    for test_case, evaluated in zip(test_cases, measured.test_results, strict=True):
        checks = {metric.name: bool(metric.success) for metric in evaluated.metrics_data}
        results.append({
            "id": test_case.name,
            "decision": test_case.metadata["output"]["decision"]["decision"],
            "checks": checks,
            "passed": all(checks.values()),
            "metrics": [{"metric": m.name, "score": m.score, "threshold": m.threshold,
                         "passed": m.success, "reason": m.reason, "error": m.error}
                        for m in evaluated.metrics_data],
            "agent_recommendation": test_case.metadata["output"].get("agent_recommendation"),
            "expected_recommendation": test_case.metadata["expected_recommendation"],
            "model_tool_calls": test_case.metadata["output"]["model_tool_calls"],
            "trace": test_case.metadata["output"]["trace"],
            "run_metadata": test_case.metadata["output"]["metadata"],
        })
    report = {
        "framework": "deepeval",
        "dataset_version": dataset["version"],
        "policy_version": POLICY_VERSION,
        "provider": provider,
        "judge_model": (judge_model or metrics[1].model).get_model_name(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(result["passed"] for result in results),
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
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--output", default=".data/evaluation-report.json")
    args = parser.parse_args()
    report = run(args.provider, args.cases, args.output)
    print(json.dumps({"passed": report["passed"], "cases": len(report["results"]),
                      "provider": args.provider, "report": args.output}, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
