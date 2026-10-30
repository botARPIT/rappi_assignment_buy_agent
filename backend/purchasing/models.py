"""Validated operational inputs; quantities and money cannot be model-authored strings."""

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Inventory(StrictModel):
    on_hand: int = Field(ge=0)


class Demand(StrictModel):
    forecast_daily: int = Field(gt=0)
    recent_daily: int = Field(ge=0)
    observed_days: int = Field(ge=0)
    age_hours: int = Field(ge=0)


class OpenOrder(StrictModel):
    id: str
    ordered_quantity: int = Field(ge=0)
    confirmed_quantity: int = Field(ge=0)
    eta_days: int = Field(ge=0)
    supplier: str


class Incoming(StrictModel):
    id: str
    quantity: int = Field(ge=0)
    eta_days: int = Field(ge=0)


class Supplier(StrictModel):
    id: str
    name: str
    unit_cost_cents: int = Field(gt=0)
    lead_days: int = Field(ge=1)
    minimum_order: int = Field(ge=1)
    pack_multiple: int = Field(ge=1)
    max_quantity: int = Field(ge=0)
    available: bool
    terms_version: int = Field(default=1, ge=1)


class ActionFeedback(StrictModel):
    """Observed supplier outcome, distinct from the original purchasing plan."""

    order_id: str
    draft_id: str
    supplier_id: str
    supplier_terms_version: int = Field(ge=1)
    outcome: str
    requested_quantity: int = Field(ge=0)
    confirmed_quantity: int | None = Field(default=None, ge=0)
    committed_quantity: int = Field(ge=0)
    expected_unit_cost_cents: int = Field(gt=0)
    reported_unit_cost_cents: int | None = Field(default=None, ge=0)
    expected_eta_days: int = Field(ge=0)
    reported_eta_days: int | None = Field(default=None, ge=0)
    validation_passed: bool
    failed_checks: list[str]


class Situation(StrictModel):
    id: str
    feature: str = Field(min_length=1)
    title: str
    product: str
    sku: str
    node: str
    revision: int = Field(ge=1)
    inventory: Inventory
    demand: Demand
    horizon_days: int = Field(ge=1, le=90)
    safety_days: int = Field(ge=0, le=30)
    purchase_order: OpenOrder
    additional_incoming: list[Incoming]
    budget_cents: int = Field(ge=0)
    storage_capacity: int = Field(ge=0)
    suppliers: list[Supplier]
    recommended_quantity: int | None = Field(default=None, ge=0)
    action_feedback: list[ActionFeedback] = Field(default_factory=list)
