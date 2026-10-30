"""API-facing adapter for the purchasing investigation workflow."""

import os

from .workflow import PurchasingWorkflow


class PurchasingAgent:
    def __init__(self, store, provider=None, model=None):
        self.store = store
        self.provider = provider or os.getenv("AGENT_PROVIDER", "ollama")
        self.workflow = PurchasingWorkflow(store, self.provider, model)
        self.graph = self.workflow.graph

    @property
    def model(self):
        return self.workflow.model

    @model.setter
    def model(self, value):
        self.workflow.model = value

    def investigate(self, case_id, callbacks=None):
        state = self.graph.invoke(
            {"case_id": case_id},
            config={"recursion_limit": 80, "callbacks": callbacks or []},
        )
        return state["output"]
