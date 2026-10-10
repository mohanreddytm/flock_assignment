import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.client import UrjaClient
from app.config import Settings
from app.main import create_app


def offline(settings):
    def handle(request):
        if request.url.path == "/login":
            return httpx.Response(
                200,
                json={"type": "redirect", "location": "/meters"},
                headers={
                    "set-cookie": "__Secure-better-auth.session_token=test; Secure; Path=/"
                },
            )
        if request.url.path == "/portal/meters/search":
            return httpx.Response(
                200, json={"data": [], "total": 0, "page": 1, "pageSize": 20}
            )
        return httpx.Response(404)

    return create_app(settings, UrjaClient(settings, httpx.MockTransport(handle)))


def test_api_key_is_enforced(settings):
    settings.api_key = "wrapper-secret"
    settings = Settings(_env_file=None, **settings.model_dump())
    with TestClient(offline(settings)) as client:
        assert client.get("/health").status_code == 200
        denied = client.get("/api/v1/meters")
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "unauthorized"
        assert (
            client.get(
                "/api/v1/meters", headers={"X-API-Key": "wrapper-secret"}
            ).status_code
            == 200
        )


def test_invalid_parameters_do_not_reach_portal(settings):
    with TestClient(offline(settings)) as client:
        paths = [
            "/api/v1/meters?page=0",
            "/api/v1/meters/bad%20id",
            "/api/v1/meters/J1/consumption?start=2026-06-20T00:00:00",
            "/api/v1/meters/J1/consumption?start=2026-06-21T00:00:00Z&end=2026-06-20T00:00:00Z",
        ]
        for path in paths:
            response = client.get(path)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "invalid_request"
        assert not client.app.state.portal.authenticated


def test_not_found_and_login_routes(settings):
    with TestClient(offline(settings)) as client:
        assert client.post("/api/v1/auth/login").json() == {"authenticated": True}
        response = client.get("/api/v1/meters/UNKNOWN")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_missing_config_is_actionable():
    with TestClient(offline(Settings(_env_file=None))) as client:
        response = client.get("/api/v1/meters")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "configuration_missing"


def test_openapi_matches_export(settings):
    with TestClient(offline(settings)) as client:
        schema = client.get("/openapi.json").json()
        saved = json.loads((Path(__file__).parents[1] / "openapi.json").read_text())
        assert schema == saved
        assert schema["openapi"].startswith("3.")
        assert schema["paths"]["/api/v1/meters/{meter_id}/consumption"]["get"][
            "responses"
        ]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "ErrorResponse"
        )
