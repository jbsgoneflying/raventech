"""Engine 2 v2 — advisor endpoint is always-on (not gated on multi-wing)."""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


def test_advisor_endpoint_exists_and_accepts_post(client, monkeypatch):
    """The advisor endpoint must be reachable. The v2 frontend no
    longer gates on `widthComparison.length > 1`, so POSTs should
    return either a 200 or a 4xx/5xx that's NOT a 404 (route absence)."""
    from backend.config import get_flags
    f = get_flags()
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.get_flags",
        lambda: replace(f, ENGINE2_ADVISOR_ENABLED=True, ENABLE_ENGINE2_SPX_IC=True),
    )
    r = client.post("/api/spx-ic/advisor", json={"scanPayload": {"underlying": "SPX"}})
    # Route must exist — anything except 404 is acceptable (500/502 from
    # missing mocks is fine; we're only asserting the gate is gone).
    assert r.status_code != 404, "advisor route must be registered"


def test_spx_routes_registered():
    from backend.app import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/spx-ic/wing-console" in paths
    assert "/api/spx-ic/wing-console/score-placement" in paths
    assert "/api/spx-ic/advisor" in paths
