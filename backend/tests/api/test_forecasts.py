"""
End-to-end forecast API tests, against the REAL trained artifact (not a
mock) -- proving the whole chain (auth -> RBAC -> tenancy -> feature store ->
model -> persistence) works together, the same chain manually verified via
curl during development.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, slug: str, email: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_name": slug,
            "tenant_slug": slug,
            "admin_email": email,
            "admin_password": "SuperSecret123!",
            "admin_full_name": "Admin",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _make_city_with_zone(client: AsyncClient, tokens: dict, zone_index: int = 0) -> str:
    city_resp = await client.post(
        "/api/v1/cities",
        json={"name": "Test City", "slug": "test-city", "timezone": "UTC"},
        headers=_auth(tokens),
    )
    assert city_resp.status_code == 201, city_resp.text
    city_id = city_resp.json()["id"]

    zone_resp = await client.post(
        f"/api/v1/cities/{city_id}/zones",
        json={
            "zone_index": zone_index,
            "zone_model_version": "test-v1",
            "name": f"Zone {zone_index}",
            "centroid_lat": 40.7,
            "centroid_lng": -74.0,
        },
        headers=_auth(tokens),
    )
    assert zone_resp.status_code == 201, zone_resp.text
    return city_id


async def test_forecast_for_unknown_zone_is_404(client: AsyncClient):
    tokens = await _register(client, "fc-404", "admin@fc-404.com")
    city_id = await _make_city_with_zone(client, tokens, zone_index=0)

    resp = await client.get(f"/api/v1/forecasts/cities/{city_id}/zones/99", headers=_auth(tokens))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_forecast_for_real_zone_returns_a_prediction(client: AsyncClient):
    """Exercises the real ModelRegistry + real LightGBM artifact. Since the
    test DB has no demand_observations seeded, this necessarily runs in
    degraded (calendar-only) mode -- which is itself the behaviour under test:
    a missing feature store must degrade, not error.
    """
    tokens = await _register(client, "fc-real", "admin@fc-real.com")
    city_id = await _make_city_with_zone(client, tokens, zone_index=3)

    resp = await client.get(f"/api/v1/forecasts/cities/{city_id}/zones/3", headers=_auth(tokens))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["zone_id"] == 3
    assert body["predicted_demand"] >= 0
    assert body["degraded"] is True  # no observation history in the test DB
    assert body["model_version"]
    assert "unit" in body


async def test_forecast_horizon_returns_requested_number_of_windows(client: AsyncClient):
    tokens = await _register(client, "fc-horizon", "admin@fc-horizon.com")
    city_id = await _make_city_with_zone(client, tokens, zone_index=1)

    resp = await client.get(
        f"/api/v1/forecasts/cities/{city_id}/zones/1/horizon?windows=3", headers=_auth(tokens)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    # Consecutive 15-minute windows, strictly increasing.
    starts = [w["window_start"] for w in body]
    assert starts == sorted(starts)
    assert len(set(starts)) == 3


async def test_forecast_horizon_caps_at_max_windows(client: AsyncClient):
    tokens = await _register(client, "fc-cap", "admin@fc-cap.com")
    city_id = await _make_city_with_zone(client, tokens, zone_index=2)

    resp = await client.get(
        f"/api/v1/forecasts/cities/{city_id}/zones/2/horizon?windows=999", headers=_auth(tokens)
    )
    # Pydantic's own Query(le=8) constraint should reject this before it ever
    # reaches the service-level cap.
    assert resp.status_code == 422


async def test_batch_forecast_scores_every_requested_zone(client: AsyncClient):
    tokens = await _register(client, "fc-batch", "admin@fc-batch.com")
    city_resp = await client.post(
        "/api/v1/cities", json={"name": "Batch City", "slug": "batch", "timezone": "UTC"}, headers=_auth(tokens)
    )
    city_id = city_resp.json()["id"]
    for zi in (0, 1, 2):
        await client.post(
            f"/api/v1/cities/{city_id}/zones",
            json={"zone_index": zi, "zone_model_version": "v1", "centroid_lat": 40.7, "centroid_lng": -74.0},
            headers=_auth(tokens),
        )

    resp = await client.post(
        f"/api/v1/forecasts/cities/{city_id}/batch", json={"zone_ids": [0, 1, 2]}, headers=_auth(tokens)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [r["zone_id"] for r in body] == [0, 1, 2]


async def test_forecast_persists_and_appears_in_history(client: AsyncClient):
    tokens = await _register(client, "fc-history", "admin@fc-history.com")
    city_id = await _make_city_with_zone(client, tokens, zone_index=7)

    predict_resp = await client.get(f"/api/v1/forecasts/cities/{city_id}/zones/7", headers=_auth(tokens))
    assert predict_resp.status_code == 200

    history_resp = await client.get(
        f"/api/v1/forecasts/cities/{city_id}/zones/7/history", headers=_auth(tokens)
    )
    assert history_resp.status_code == 200
    assert len(history_resp.json()) >= 1


async def test_viewer_can_read_forecast_but_not_trigger_batch(client: AsyncClient):
    tokens = await _register(client, "fc-viewer", "admin@fc-viewer.com")
    city_id = await _make_city_with_zone(client, tokens, zone_index=4)

    await client.post(
        "/api/v1/users",
        json={"email": "v@fc-viewer.com", "password": "ViewerPass123!", "full_name": "V", "role": "VIEWER"},
        headers=_auth(tokens),
    )
    viewer_tokens = (
        await client.post("/api/v1/auth/login", json={"email": "v@fc-viewer.com", "password": "ViewerPass123!"})
    ).json()

    read_resp = await client.get(f"/api/v1/forecasts/cities/{city_id}/zones/4", headers=_auth(viewer_tokens))
    assert read_resp.status_code == 200

    batch_resp = await client.post(
        f"/api/v1/forecasts/cities/{city_id}/batch", json={"zone_ids": [4]}, headers=_auth(viewer_tokens)
    )
    assert batch_resp.status_code == 403


async def test_model_current_reports_real_loaded_artifact(client: AsyncClient):
    tokens = await _register(client, "fc-model", "admin@fc-model.com")
    resp = await client.get("/api/v1/models/current", headers=_auth(tokens))
    assert resp.status_code == 200
    body = resp.json()
    assert body["loaded"] is True
    assert body["metadata"]["contract_version"] == "fr01-v1"
