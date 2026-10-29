import pytest


def analyze(client, case_id="supplier-shortfall"):
    response = client.post("/api/agent/run", json={"case_id": case_id})
    assert response.status_code == 200
    return response.json()


def test_health_and_frontend_assets(client):
    assert client.get("/health").json()["agent_provider"] == "scripted"
    assert 'id="analyze-button"' in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert len(client.get("/api/cases").json()) == 6


def test_api_order_round_trip_and_repeat(client):
    investigation = analyze(client)
    path = f"/api/drafts/{investigation['draft']['id']}/approve"
    r = client.post(path, json={"approved": True})
    assert r.status_code == 200 and r.json()["validation"]["valid"] is True
    assert client.post(path, json={"approved": True}).json()["idempotent_replay"] is True
    assert analyze(client)["decision"]["decision"] == "no_action"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"approved": False},
        {"approved": "true"},
        {"approved": 1},
        {"approved": True, "quantity": 999},
        {"approved": True, "outcome": "fake"},
    ],
)
def test_approval_api_rejects_missing_or_forged_fields(client, body):
    draft = analyze(client)["draft"]
    assert client.post(f"/api/drafts/{draft['id']}/approve", json=body).status_code == 422
    assert client.app.state.store.get_draft(draft["id"])["status"] == "awaiting_approval"


def test_unknown_and_invalid_request(client):
    assert client.post("/api/agent/run", json={"case_id": "missing"}).status_code == 404
    assert (
        client.post("/api/agent/run", json={"case_id": "supplier-shortfall", "execute": True}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/agent/run", content="broken", headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )
    assert client.post("/api/drafts/missing/approve", json={"approved": True}).status_code == 404


def test_api_rejection_and_feedback(client):
    draft = analyze(client)["draft"]
    r = client.post(
        f"/api/drafts/{draft['id']}/approve", json={"approved": True, "outcome": "rejected"}
    ).json()
    assert not r["validation"]["valid"] and r["follow_up"]["remaining_gap"] == 110
    events = client.get("/api/events", params={"case_id": "supplier-shortfall"}).json()
    assert any(e["type"] == "buyer_review_required" for e in events)


def test_reset_invalidates_draft(client):
    draft = analyze(client)["draft"]
    assert client.post("/api/cases/supplier-shortfall/reset").status_code == 200
    assert client.post(f"/api/drafts/{draft['id']}/approve", json={"approved": True}).status_code == 409


def test_reject_draft_route(client):
    draft = analyze(client)["draft"]
    assert client.post(f"/api/drafts/{draft['id']}/reject").json()["status"] == "buyer_rejected"
    assert client.post(f"/api/drafts/{draft['id']}/approve", json={"approved": True}).status_code == 409
