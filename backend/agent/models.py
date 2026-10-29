"""Validated operational inputs; quantities and money cannot be model-authored strings."""

from typing import Literal

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


class Situation(StrictModel):
    id: str
    feature: Literal["recommendation_review", "supplier_shortfall", "demand_change"]
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
