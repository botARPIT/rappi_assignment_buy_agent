"""Validate and compare an independent recommendation with the policy result."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.purchasing.policy import ACTIONABLE


class IndependentRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["buy", "no_buy", "needs_review"]
    quantity: int = Field(ge=0)
    supplier_id: str | None
    estimated_cost_cents: int = Field(ge=0)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_terms(self):
        if not self.reason.strip():
            raise ValueError("A reason is required")
        if self.decision == "buy":
            if self.quantity <= 0 or not self.supplier_id or not self.supplier_id.strip():
                raise ValueError("A purchase needs a positive quantity and supplier ID")
        elif self.quantity != 0 or self.supplier_id is not None or self.estimated_cost_cents != 0:
            raise ValueError("A non-purchase cannot propose order terms")
        return self


def parse_recommendation(text: str) -> dict:
    content = text.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return IndependentRecommendation.model_validate(json.loads(content)).model_dump()


def compare_recommendations(policy: dict, recommendation: dict | None) -> dict:
    if recommendation is None:
        return {"status": "unavailable", "differences": []}
    policy_outcome = (
        "buy" if policy["decision"] in ACTIONABLE
        else "no_buy" if policy["decision"] in {"reject", "no_action"}
        else "needs_review"
    )
    differences = []
    if recommendation["decision"] != policy_outcome:
        differences.append("decision")
    elif policy_outcome == "buy":
        for agent_field, policy_field in (
            ("quantity", "quantity"),
            ("supplier_id", "supplier_id"),
            ("estimated_cost_cents", "total_cost_cents"),
        ):
            if recommendation[agent_field] != policy[policy_field]:
                differences.append(agent_field)
    return {"status": "disagree" if differences else "agree", "differences": differences}
