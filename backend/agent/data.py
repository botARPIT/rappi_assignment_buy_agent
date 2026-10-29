"""Versioned purchasing fixtures. Each case has its own product, node and budget."""

from copy import deepcopy

DATASET_VERSION = "2.0.0"


def base_case(case_id, feature, title):
    return {
        "id": case_id,
        "feature": feature,
        "title": title,
        "product": "Arabica coffee beans · 1 kg",
        "sku": case_id.upper(),
        "node": "Bogotá North",
        "revision": 1,
        "inventory": {"on_hand": 120},
        "demand": {"forecast_daily": 30, "recent_daily": 30, "observed_days": 7, "age_hours": 2},
        "horizon_days": 14,
        "safety_days": 2,
        "purchase_order": {
            "id": "PO-" + case_id,
            "ordered_quantity": 500,
            "confirmed_quantity": 250,
            "eta_days": 5,
            "supplier": "Andes Foods",
        },
        "additional_incoming": [],
        "budget_cents": 300000,
        "storage_capacity": 700,
        "suppliers": [
            {
                "id": "central",
                "name": "Café Central",
                "unit_cost_cents": 1200,
                "lead_days": 3,
                "minimum_order": 100,
                "pack_multiple": 50,
                "max_quantity": 1000,
                "available": True,
            }
        ],
        "recommended_quantity": None,
    }


def get_cases():
    shortfall = base_case(
        "supplier-shortfall", "supplier_shortfall", "Supplier can fulfil only 250 of 500 units"
    )
    review = base_case(
        "recommendation-review", "recommendation_review", "Review a recommendation to buy 800 units"
    )
    review.update(product="Oat milk · 1 L", recommended_quantity=800)
    review["inventory"]["on_hand"] = 100
    review["purchase_order"].update(ordered_quantity=100, confirmed_quantity=100, eta_days=4)
    review["demand"].update(forecast_daily=20, recent_daily=20)
    review["suppliers"][0].update(lead_days=2, unit_cost_cents=1000)
    demand = base_case("demand-spike", "demand_change", "Sales have tripled while an order is already open")
    demand["product"] = "Sparkling water · 6 pack"
    demand["demand"].update(forecast_daily=10, recent_daily=30)
    covered = deepcopy(shortfall)
    covered.update(id="inventory-covers-demand", title="Existing supply already covers demand", sku="OAT-1L")
    covered["inventory"]["on_hand"] = 500
    covered["storage_capacity"] = 1000
    budget = deepcopy(shortfall)
    budget.update(id="budget-constrained", title="A top-up would exceed the budget", budget_cents=100000)
    storage = deepcopy(shortfall)
    storage.update(id="storage-constrained", title="A top-up would exceed storage", storage_capacity=450)
    return {c["id"]: c for c in (review, shortfall, demand, covered, budget, storage)}
