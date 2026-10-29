"""LangChain create_agent runtime. The model selects tools; policy controls authorization."""

import hashlib
import os
import time
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
    after_model,
    wrap_model_call,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .llm import build_model
from .policy import ACTIONABLE, POLICY_VERSION, REQUIRED_READS
from .prompts import SYSTEM_PROMPT
from .tools import PurchasingTools


class PurchasingAgent:
    def __init__(self, store, provider=None, model=None):
        self.store = store
        self.provider = provider or os.getenv("AGENT_PROVIDER", "ollama")
        self.model = model

    def investigate(self, case_id, callbacks=None):
        case = self.store.get_case(case_id)
        tools = PurchasingTools(case, self.store)
        model = self.model or build_model(self.provider)

        @wrap_model_call
        def gated_tool_visibility(request, handler):
            # Gate capabilities before the model sees them; tool implementations also enforce authorization.
            if tools.draft or (tools.decision and tools.decision["decision"] not in ACTIONABLE):
                allowed = set()
                stage = "CURRENT STAGE: FINAL EXPLANATION. No tools are offered. Explain the policy result and whether buyer approval is needed. Stop."
            elif tools.decision:
                allowed = {"prepare_purchase_draft"}
                stage = f"CURRENT STAGE: PREPARE A DRAFT. Call the offered draft tool using decision_id {tools.decision_id}."
            elif REQUIRED_READS - tools.reads:
                allowed = REQUIRED_READS - tools.reads
                stage = "CURRENT STAGE: EVIDENCE COLLECTION. Read all currently offered evidence tools. Policy and action tools are not offered yet. Do not call them."
            else:
                allowed = {"evaluate_purchase_policy"}
                stage = "CURRENT STAGE: POLICY EVALUATION. All evidence has been read. Call the offered policy tool now."
            return handler(
                request.override(
                    tools=[t for t in request.tools if t.name in allowed],
                    system_message=SystemMessage(content=SYSTEM_PROMPT + "\n" + stage),
                )
            )

        completion_recoveries = 0

        @after_model(can_jump_to=["model"])
        def require_complete_investigation(state, runtime):
            nonlocal completion_recoveries
            last = state["messages"][-1]
            incomplete = tools.decision is None or (
                tools.decision["decision"] in ACTIONABLE and tools.draft is None
            )
            if (
                isinstance(last, AIMessage)
                and not last.tool_calls
                and incomplete
                and completion_recoveries < 2
            ):
                completion_recoveries += 1
                return {
                    "messages": [
                        HumanMessage(
                            content="[Workflow gate] Investigation is incomplete. Continue using the currently offered tools; do not give a final answer until the policy check and any required draft are complete."
                        )
                    ],
                    "jump_to": "model",
                }
            return None

        runtime = create_agent(
            model=model,
            tools=tools.build(),
            system_prompt=SYSTEM_PROMPT,
            middleware=[
                gated_tool_visibility,
                require_complete_investigation,
                ModelCallLimitMiddleware(run_limit=10, exit_behavior="end"),
                ToolCallLimitMiddleware(run_limit=16, exit_behavior="end"),
            ],
        )
        started = time.monotonic()
        error = None
        narrative = ""
        response = None
        try:
            response = runtime.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Investigate purchasing situation {case_id}. Use tools to decide what should happen next.",
                        }
                    ]
                },
                config={"recursion_limit": 80, "callbacks": callbacks or []},
            )
            last = next(
                (m for m in reversed(response["messages"]) if isinstance(m, AIMessage) and m.content), None
            )
            if last:
                narrative = last.text
        except Exception as exc:
            # An incomplete model invocation never becomes authorization by itself.
            error = f"{type(exc).__name__}: Agent run could not finish. Check model availability and retry."
        decision = tools.canonical_decision()
        if tools.decision is None or error:
            narrative = decision["reason"]
        output = {
            "run_id": uuid4().hex,
            "case": case,
            "decision": decision,
            "draft": tools.draft,
            "trace": tools.trace,
            "model_tool_calls": [
                {"tool": call["name"], "input": call["args"]}
                for message in (response or {}).get("messages", [])
                if isinstance(message, AIMessage)
                for call in message.tool_calls
            ],
            "message": narrative or decision["reason"],
            "error": error,
            "metadata": {
                "framework": "langchain.create_agent",
                "completion_recoveries": completion_recoveries,
                "provider": self.provider,
                "model": os.getenv("AGENT_MODEL", "gpt-oss:20b-cloud")
                if self.provider == "ollama"
                else "scripted-test-model",
                "policy_version": POLICY_VERSION,
                "prompt_hash": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "execution_mode": "live_model" if self.provider == "ollama" else "scripted",
            },
        }
        self.store.record_run(output)
        return output
