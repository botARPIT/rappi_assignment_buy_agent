"""Production ChatOllama and an explicitly labelled scripted test model."""

import os
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_ollama import ChatOllama


class ScriptedPurchasingModel(BaseChatModel):
    """Runs the real LangChain graph without a network model; only for reproducible checks."""

    @property
    def _llm_type(self):
        return "scripted-test-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        import json

        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        done = {m.name for m in tool_messages}
        reads = [
            "get_purchasing_situation",
            "get_inventory_and_open_orders",
            "get_demand_evidence",
            "get_supplier_terms",
        ]
        pending = next((name for name in reads if name not in done), None)
        args = {}
        if not pending and "evaluate_purchase_policy" not in done:
            pending = "evaluate_purchase_policy"
        policy = next(
            (json.loads(m.content) for m in reversed(tool_messages) if m.name == "evaluate_purchase_policy"),
            None,
        )
        if (
            not pending
            and policy
            and policy.get("requires_human_approval")
            and "prepare_purchase_draft" not in done
        ):
            pending = "prepare_purchase_draft"
            args = {"decision_id": policy["decision_id"]}
        if pending:
            msg = AIMessage(
                content="",
                tool_calls=[{"name": pending, "args": args, "id": uuid4().hex, "type": "tool_call"}],
            )
        else:
            msg = AIMessage(content=(policy or {}).get("reason", "Further investigation is required."))
        return ChatResult(generations=[ChatGeneration(message=msg)])


def build_model(provider=None):
    provider = provider or os.getenv("AGENT_PROVIDER", "ollama")
    if provider == "scripted":
        return ScriptedPurchasingModel()
    if provider != "ollama":
        raise ValueError("AGENT_PROVIDER must be ollama or scripted")
    return ChatOllama(
        model=os.getenv("AGENT_MODEL", "gpt-oss:20b-cloud"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        temperature=0,
        client_kwargs={"timeout": 90},
    )
