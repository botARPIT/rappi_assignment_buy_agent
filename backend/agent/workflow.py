"""LangGraph orchestration for an independent purchasing investigation."""

import hashlib
import os
import time
from typing import Any, TypedDict
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
    after_model,
    wrap_model_call,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from .llm import build_model
from .policy import ACTIONABLE, POLICY_VERSION, REQUIRED_READS
from .prompts import SYSTEM_PROMPT
from .review import compare_recommendations, parse_recommendation
from .tools import PurchasingTools


class PurchaseState(TypedDict, total=False):
    case_id: str
    case: dict
    tools: PurchasingTools
    tool_map: dict[str, Any]
    started: float
    steps: list[str]
    recommendation: dict | None
    review_error: str | None
    error: str | None
    policy_error: bool
    decision: dict
    comparison: dict
    model_tool_calls: list[dict]
    completion_recoveries: int
    output: dict


def summarize_decision(decision):
    """Make a short buyer summary using the deterministic policy result."""
    outcome = decision["decision"]
    if outcome in ACTIONABLE:
        evidence = decision["evidence"]
        return (
            f"Recommend ordering {decision['quantity']} units from {decision['supplier']} "
            f"for ${decision['estimated_cost']:,.2f}, pending buyer approval.\n"
            f"The coverage gap is {evidence['uncovered_units']} units; the proposed order "
            "meets budget and storage limits."
        )
    if outcome in {"reject", "no_action"}:
        return f"Recommend no additional purchase.\n{decision['reason']}"
    if outcome == "escalate":
        return f"Buyer review is needed before deciding on a purchase.\n{decision['reason']}"
    return f"Investigate before deciding on a purchase.\n{decision['reason']}"


class PurchasingWorkflow:
    """Build an explicit graph; each invocation carries its own case and tool state."""

    def __init__(self, store, provider: str, model=None):
        self.store = store
        self.provider = provider
        self.model = model
        builder = StateGraph(PurchaseState)
        builder.add_node("load_situation", self.load_situation)
        builder.add_node("independent_review", self.independent_review)
        builder.add_node("evaluate_policy", self.evaluate_policy)
        builder.add_node("incomplete_evidence", self.incomplete_evidence)
        builder.add_node("compare_recommendations", self.compare)
        builder.add_node("prepare_draft", self.prepare_draft)
        builder.add_node("finalize_result", self.finalize_result)
        builder.add_edge(START, "load_situation")
        builder.add_edge("load_situation", "independent_review")
        builder.add_conditional_edges(
            "independent_review",
            self.route_evidence,
            {"complete": "evaluate_policy", "incomplete": "incomplete_evidence"},
        )
        builder.add_edge("evaluate_policy", "compare_recommendations")
        builder.add_edge("incomplete_evidence", "compare_recommendations")
        builder.add_conditional_edges(
            "compare_recommendations",
            self.route_draft,
            {"prepare": "prepare_draft", "finish": "finalize_result"},
        )
        builder.add_edge("prepare_draft", "finalize_result")
        builder.add_edge("finalize_result", END)
        self.graph = builder.compile()

    @staticmethod
    def _steps(state: PurchaseState, node: str) -> list[str]:
        return [*state.get("steps", []), node]

    def load_situation(self, state: PurchaseState) -> dict:
        case = self.store.get_case(state["case_id"])
        tools = PurchasingTools(case, self.store)
        return {
            "case": case,
            "tools": tools,
            "tool_map": {tool.name: tool for tool in tools.build()},
            "started": time.monotonic(),
            "error": None,
            "policy_error": False,
            "steps": self._steps(state, "load_situation"),
        }

    def independent_review(self, state: PurchaseState) -> dict:
        """Run the evidence-gathering agent without exposing policy or draft tools."""
        tools, tool_map = state["tools"], state["tool_map"]
        completion_recoveries = 0

        @wrap_model_call
        def gated_tool_visibility(request, handler):
            if REQUIRED_READS - tools.reads:
                allowed = REQUIRED_READS - tools.reads
                stage = "CURRENT STAGE: EVIDENCE COLLECTION. Read all currently offered evidence tools."
            else:
                allowed = set()
                stage = "CURRENT STAGE: INDEPENDENT RECOMMENDATION. Use only the evidence already read and return the requested JSON."
            return handler(
                request.override(
                    tools=[tool for tool in request.tools if tool.name in allowed],
                    system_message=SystemMessage(content=SYSTEM_PROMPT + "\n" + stage),
                )
            )

        @after_model(can_jump_to=["model"])
        def require_complete_evidence(agent_state, runtime):
            nonlocal completion_recoveries
            last = agent_state["messages"][-1]
            if (
                isinstance(last, AIMessage)
                and not last.tool_calls
                and REQUIRED_READS - tools.reads
                and completion_recoveries < 2
            ):
                completion_recoveries += 1
                return {
                    "messages": [
                        HumanMessage(
                            content="[Workflow gate] Evidence collection is incomplete. Read the currently offered tools before returning your recommendation."
                        )
                    ],
                    "jump_to": "model",
                }
            return None

        recommendation = None
        review_error = None
        error = None
        response = None
        try:
            runtime = create_agent(
                model=self.model or build_model(self.provider),
                tools=[
                    tool_map[name]
                    for name in (
                        "get_purchasing_situation",
                        "get_inventory_and_open_orders",
                        "get_demand_evidence",
                        "get_supplier_terms",
                    )
                ],
                system_prompt=SYSTEM_PROMPT,
                middleware=[
                    gated_tool_visibility,
                    require_complete_evidence,
                    ModelCallLimitMiddleware(run_limit=10, exit_behavior="end"),
                    ToolCallLimitMiddleware(run_limit=16, exit_behavior="end"),
                ],
            )
            response = runtime.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Investigate purchasing situation {state['case_id']}. Use tools to decide what should happen next.",
                        }
                    ]
                },
                config={"recursion_limit": 80},
            )
            last = next(
                (
                    message
                    for message in reversed(response["messages"])
                    if isinstance(message, AIMessage) and message.content
                ),
                None,
            )
            if last and not (REQUIRED_READS - tools.reads):
                try:
                    recommendation = parse_recommendation(last.text)
                except (ValueError, TypeError, IndexError):
                    review_error = "The agent did not return a valid independent recommendation."
            else:
                review_error = "The agent did not complete the evidence review."
        except Exception as exc:
            error = f"{type(exc).__name__}: Agent run could not finish. Check model availability and retry."
            review_error = "The independent recommendation is unavailable."
        model_tool_calls = [
            {"tool": call["name"], "input": call["args"]}
            for message in (response or {}).get("messages", [])
            if isinstance(message, AIMessage)
            for call in message.tool_calls
        ]
        return {
            "recommendation": recommendation,
            "review_error": review_error,
            "error": error,
            "completion_recoveries": completion_recoveries,
            "model_tool_calls": model_tool_calls,
            "steps": self._steps(state, "independent_review"),
        }

    @staticmethod
    def route_evidence(state: PurchaseState) -> str:
        return "incomplete" if REQUIRED_READS - state["tools"].reads else "complete"

    def evaluate_policy(self, state: PurchaseState) -> dict:
        tools = state["tools"]
        error = state.get("error")
        policy_error = False
        try:
            state["tool_map"]["evaluate_purchase_policy"].invoke({})
        except Exception as exc:
            error = f"{type(exc).__name__}: Policy evaluation or draft preparation failed."
            policy_error = True
        return {
            "decision": tools.canonical_decision(),
            "error": error,
            "policy_error": policy_error,
            "steps": self._steps(state, "evaluate_policy"),
        }

    def incomplete_evidence(self, state: PurchaseState) -> dict:
        return {
            "decision": state["tools"].canonical_decision(),
            "policy_error": True,
            "steps": self._steps(state, "incomplete_evidence"),
        }

    def compare(self, state: PurchaseState) -> dict:
        return {
            "comparison": compare_recommendations(state["decision"], state.get("recommendation")),
            "steps": self._steps(state, "compare_recommendations"),
        }

    @staticmethod
    def route_draft(state: PurchaseState) -> str:
        return (
            "prepare"
            if not state["policy_error"] and state["decision"]["decision"] in ACTIONABLE
            else "finish"
        )

    def prepare_draft(self, state: PurchaseState) -> dict:
        error = state.get("error")
        try:
            state["tool_map"]["prepare_purchase_draft"].invoke({"decision_id": state["tools"].decision_id})
        except Exception as exc:
            error = f"{type(exc).__name__}: Policy evaluation or draft preparation failed."
        return {"error": error, "steps": self._steps(state, "prepare_draft")}

    def finalize_result(self, state: PurchaseState) -> dict:
        decision, recommendation, tools = state["decision"], state.get("recommendation"), state["tools"]
        steps = self._steps(state, "finalize_result")
        output = {
            "run_id": uuid4().hex,
            "case": state["case"],
            "decision": decision,
            "draft": tools.draft,
            "trace": tools.trace,
            "model_tool_calls": state["model_tool_calls"],
            "summary": summarize_decision(decision),
            "message": recommendation["reason"]
            if recommendation
            else state.get("review_error") or decision["reason"],
            "agent_recommendation": recommendation,
            "recommendation_comparison": state["comparison"],
            "review_error": state.get("review_error"),
            "error": state.get("error"),
            "metadata": {
                "framework": "langchain.create_agent",
                "workflow_framework": "langgraph.StateGraph",
                "workflow_nodes": steps,
                "completion_recoveries": state["completion_recoveries"],
                "provider": self.provider,
                "model": os.getenv("AGENT_MODEL", "gpt-oss:20b-cloud")
                if self.provider == "ollama"
                else "scripted-test-model",
                "policy_version": POLICY_VERSION,
                "prompt_hash": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
                "elapsed_seconds": round(time.monotonic() - state["started"], 3),
                "execution_mode": "live_model" if self.provider == "ollama" else "scripted",
            },
        }
        self.store.record_run(output)
        return {"output": output, "steps": steps}
