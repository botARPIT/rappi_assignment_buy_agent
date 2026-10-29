from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.agent.agent import PurchasingAgent
from backend.agent.policy import assess_purchase
from backend.agent.tools import PurchasingTools
from backend.store import ConflictError, Store


def draft(store, case_id="supplier-shortfall"):
    case = store.get_case(case_id)
    return store.create_draft(case_id, assess_purchase(case))


def test_evidence_gate_requires_all_four_reads(store):
    tools = PurchasingTools(store.get_case("supplier-shortfall"), store)
    available = {tool.name: tool for tool in tools.build()}
    assert available["evaluate_purchase_policy"].invoke({})["error"] == "evidence_gate"
    assert (
        available["prepare_purchase_draft"].invoke({"decision_id": "forged"})["error"] == "authorization_gate"
    )
    assert tools.draft is None


def test_model_has_no_execution_tool(store):
    available = {tool.name for tool in PurchasingTools(store.get_case("supplier-shortfall"), store).build()}
    assert "execute_order" not in available and "approve" not in available


def test_forged_quantity_or_cost_cannot_create_draft(store):
    decision = assess_purchase(store.get_case("supplier-shortfall"))
    decision.update(quantity=10000, total_cost_cents=1)
    with pytest.raises(ConflictError):
        store.create_draft("supplier-shortfall", decision)


def test_buyer_approval_must_be_explicit(store):
    with pytest.raises(ConflictError):
        store.approve(draft(store)["id"], approved=False)


def test_reanalysis_reuses_same_draft(store):
    assert draft(store)["id"] == draft(store)["id"]


@pytest.mark.parametrize("change", ["budget", "price", "lead", "demand", "revision_only"])
def test_changed_operational_state_blocks_old_authorization(store, change):
    old = draft(store)
    case = store.get_case("supplier-shortfall")
    if change == "budget":
        case["budget_cents"] = 1
    elif change == "price":
        case["suppliers"][0]["unit_cost_cents"] += 100
    elif change == "lead":
        case["suppliers"][0]["lead_days"] += 1
    elif change == "demand":
        case["demand"]["forecast_daily"] = 40
    store.update_case(case)
    with pytest.raises(ConflictError):
        store.approve(old["id"], approved=True)
    assert store.get_draft(old["id"])["status"] == "awaiting_approval"


def test_confirmation_updates_budget_and_incoming_once(store):
    order = draft(store)
    before = store.get_case(order["case_id"])
    approved = store.approve(order["id"], approved=True)
    repeated = store.approve(order["id"], approved=True)
    after = store.get_case(order["case_id"])
    assert approved["validation"]["valid"] is True and repeated["idempotent_replay"] is True
    assert approved["action"]["id"] == repeated["action"]["id"]
    assert after["budget_cents"] == before["budget_cents"] - 180000
    assert len(after["additional_incoming"]) == 1
    assert assess_purchase(after)["decision"] == "no_action"


def test_concurrent_approval_works_across_store_instances(store):
    order = draft(store)

    def approve(_):
        return Store(store.path).approve(order["id"], approved=True)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(approve, range(10)))
    assert len({r["action"]["id"] for r in results}) == 1
    assert len(store.get_case(order["case_id"])["additional_incoming"]) == 1
    assert sum(not r["idempotent_replay"] for r in results) == 1


@pytest.mark.parametrize("outcome,quantity,spent", [("rejected", 0, 0), ("partial", 75, 90000)])
def test_unexpected_supplier_response_is_reassessed(store, outcome, quantity, spent):
    order = draft(store)
    result = store.approve(order["id"], approved=True, outcome=outcome)
    assert result["validation"]["valid"] is False
    assert result["follow_up"]["status"] == "needs_buyer_review"
    assert result["action"]["confirmed_quantity"] == quantity
    assert store.get_case(order["case_id"])["budget_cents"] == 300000 - spent
    assert result["follow_up"]["remaining_gap"] == 110 - quantity


def test_buyer_rejection_prevents_execution(store):
    order = draft(store)
    store.reject(order["id"])
    with pytest.raises(ConflictError):
        store.approve(order["id"], approved=True)
    assert not store.get_case(order["case_id"])["additional_incoming"]


def test_unknown_draft_and_outcome(store):
    with pytest.raises(KeyError):
        store.approve("missing", approved=True)
    with pytest.raises(ValueError):
        store.approve(draft(store)["id"], approved=True, outcome="invented")


@pytest.mark.parametrize(
    "case_id,decision",
    [
        ("recommendation-review", "modify"),
        ("supplier-shortfall", "supplemental_purchase"),
        ("demand-spike", "adjust_plan"),
    ],
)
def test_actual_langchain_graph_for_three_features(store, case_id, decision):
    result = PurchasingAgent(store, provider="scripted").investigate(case_id)
    assert result["error"] is None
    assert result["decision"]["decision"] == decision
    assert result["draft"]["status"] == "awaiting_approval"
    assert len(result["trace"]) == 6
    assert result["metadata"]["framework"] == "langchain.create_agent"
    assert result["metadata"]["execution_mode"] == "scripted"


def test_declared_mock_fixture_does_not_override_live_store(store):
    case = store.get_case("supplier-shortfall")
    case["budget_cents"] = 1
    store.update_case(case)
    result = PurchasingAgent(store, provider="scripted").investigate("supplier-shortfall")
    assert result["decision"]["decision"] == "escalate"
    assert result["draft"] is None


def test_fresh_process_preserves_orders_and_audit(store):
    order = draft(store)
    store.approve(order["id"], approved=True)
    reopened = Store(store.path)
    assert reopened.get_draft(order["id"])["status"] == "executed"
    assert len(reopened.events(order["case_id"])) >= 4
    assert reopened.get_case(order["case_id"])["budget_cents"] == 120000
