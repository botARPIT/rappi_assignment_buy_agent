"""Integration checks across the agent, policy, persistence and evaluator."""

import pytest
from deepeval.models import DeepEvalBaseLLM

from backend.agent.agent import PurchasingAgent
from backend.agent.data import get_cases
from backend.agent.policy import assess_purchase
from backend.agent.tools import PurchasingTools
from backend.store import ConflictError
from evaluations.run_eval import run


def test_agent_reads_evidence_then_prepares_an_approval_draft(store):
    tools = PurchasingTools(store.get_case("supplier-shortfall"), store)
    available = {tool.name: tool for tool in tools.build()}
    assert available["evaluate_purchase_policy"].invoke({})["error"] == "evidence_gate"
    assert available["prepare_purchase_draft"].invoke({"decision_id": "forged"})["error"] == "authorization_gate"
    assert "execute_order" not in available

    result = PurchasingAgent(store, provider="scripted").investigate("supplier-shortfall")
    assert result["error"] is None
    assert result["decision"]["quantity"] == 150
    assert result["draft"]["status"] == "awaiting_approval"
    assert [step["tool"] for step in result["trace"]][-2:] == ["evaluate_purchase_policy", "prepare_purchase_draft"]


def test_approval_rechecks_state_and_executes_once(store):
    draft = store.create_draft("supplier-shortfall", assess_purchase(store.get_case("supplier-shortfall")))
    with pytest.raises(ConflictError, match="Explicit buyer approval"):
        store.approve(draft["id"], approved=False)
    changed = store.get_case("supplier-shortfall")
    changed["budget_cents"] = 1
    store.update_case(changed)
    with pytest.raises(ConflictError, match="stale"):
        store.approve(draft["id"], approved=True)

    restored = store.update_case(get_cases()["supplier-shortfall"])
    fresh = store.create_draft("supplier-shortfall", assess_purchase(restored))
    first = store.approve(fresh["id"], approved=True)
    second = store.approve(fresh["id"], approved=True)
    assert first["validation"]["valid"] and second["idempotent_replay"]
    assert len(store.get_case("supplier-shortfall")["additional_incoming"]) == 1


class FixedJudge(DeepEvalBaseLLM):
    """Test double for the external judge; it does not validate real grounding."""

    def __init__(self):
        self.prompts = []

    def load_model(self):
        return self

    def generate(self, prompt, schema=None):
        self.prompts.append(prompt)
        return schema(reason="Test judge", score=10) if schema else '{"reason":"Test judge","score":10}'

    async def a_generate(self, prompt, schema=None):
        return self.generate(prompt, schema)

    def get_model_name(self):
        return "fixed-test-judge"


def test_eval_runner_measures_initial_and_rejected_supplier_recovery():
    judge = FixedJudge()
    report = run(provider="scripted", case_ids=["supplier-rejection-feedback"], judge_model=judge)
    assert len(report["results"]) == 2
    initial, recovery = report["results"]
    assert recovery["id"] == "supplier-rejection-feedback/recovery"
    assert recovery["agent_recommendation"]["decision"] == "needs_review"
    assert "rejected" in recovery["agent_recommendation"]["reason"]
    assert set(initial["checks"]) == {"tool_efficiency", "groundedness", "answer_correctness"}
    assert initial["passed"] and recovery["passed"]
    assert judge.prompts and all("policy_decision" not in prompt for prompt in judge.prompts)
