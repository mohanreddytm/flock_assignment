import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def main():
    settings = Settings()
    headers = (
        {"X-API-Key": settings.api_key.get_secret_value()}
        if settings.api_key.get_secret_value()
        else {}
    )
    with TestClient(create_app(settings), headers=headers) as client:
        listing = client.get("/api/v1/meters")
        listing.raise_for_status()
        payload = listing.json()
        print(f"Meters: {payload['total']}", flush=True)
        if not payload["data"]:
            raise RuntimeError(
                "No live meter is available to verify detail and consumption."
            )
        identifier = payload["data"][0]["meter_id"]
        paths = [
            f"/api/v1/meters/{identifier}",
            f"/api/v1/meters/{identifier}/consumption",
            "/api/v1/transformers",
            "/api/v1/meters/export",
            "/api/v1/hierarchy",
        ]
        for path in paths:
            response = client.get(path)
            if response.status_code != 200:
                raise RuntimeError(f"{path}: {response.status_code} {response.text}")
            body = response.json()
            print(
                json.dumps(
                    {
                        "path": path,
                        "status": response.status_code,
                        "count": body.get(
                            "total", body.get("count", len(body.get("nodes", [])))
                        ),
                    }
                ),
                flush=True,
            )
        session = client.app.state.portal
        session.http.cookies.clear()
        response = client.get("/api/v1/meters")
        response.raise_for_status()
        print("Session recovery after clearing cookies: passed", flush=True)


if __name__ == "__main__":
    main()
