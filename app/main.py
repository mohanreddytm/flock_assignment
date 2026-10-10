import hmac
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from pydantic import ValidationError

from app.client import UrjaClient
from app.config import Settings
from app.errors import PortalError
from app.models import (
    Consumption,
    ErrorResponse,
    Export,
    Health,
    Meter,
    MeterDetail,
    NetworkHierarchy,
    Page,
    SessionStatus,
    Transformer,
)
from app.normalize import network

MeterId = Annotated[
    str,
    Path(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
        description="Portal meter identifier, for example J100000.",
    ),
]
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def create_app(settings: Settings | None = None, portal: UrjaClient | None = None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application):
        application.state.portal = portal or UrjaClient(settings)
        try:
            yield
        finally:
            application.state.portal.close()

    app = FastAPI(
        title="Urja Meter API",
        version="1.0.0",
        description="Read-only access to the Urja Meter Ops portal. Portal sessions are managed by the server. Set URJA_API_KEY to require X-API-Key for /api/v1 routes. Consumption filters apply to the portal's default history window.",
        lifespan=lifespan,
    )

    @app.exception_handler(PortalError)
    async def portal_error(request, error):
        return JSONResponse(
            status_code=error.status,
            content={"error": {"code": error.code, "message": error.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def request_error(request, error):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Check the path and query values against the API documentation.",
                }
            },
        )

    @app.exception_handler(ValidationError)
    async def upstream_validation_error(request, error):
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "invalid_upstream_data",
                    "message": "The portal data did not match the expected schema.",
                }
            },
        )

    def authorize(value: Annotated[str | None, Depends(api_key_header)]):
        expected = settings.api_key.get_secret_value()
        if expected and not hmac.compare_digest(
            (value or "").encode(), expected.encode()
        ):
            raise PortalError("unauthorized", "Provide a valid X-API-Key header.", 401)

    def get_portal(request: Request):
        return request.app.state.portal

    Portal = Annotated[UrjaClient, Depends(get_portal)]
    responses = {
        status: {"model": ErrorResponse, "description": description}
        for status, description in {
            401: "Invalid wrapper API key",
            404: "Resource not found",
            422: "Invalid request",
            502: "Portal failure or unrecognized data",
            503: "Portal rate limit or missing configuration",
            504: "Portal timeout",
        }.items()
    }
    router = APIRouter(
        prefix="/api/v1", dependencies=[Depends(authorize)], responses=responses
    )

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/docs")

    @app.get(
        "/health",
        response_model=Health,
        tags=["Service"],
        summary="Check that the API process is running",
    )
    def health():
        return Health(status="ok")

    @router.post(
        "/auth/login",
        response_model=SessionStatus,
        tags=["Session"],
        summary="Renew the server's portal session",
    )
    def login(client: Portal):
        client.login()
        return SessionStatus(authenticated=True)

    @router.get(
        "/meters",
        response_model=Page[Meter],
        tags=["Meters"],
        summary="Search meters by identifier or serial number",
    )
    def meters(
        client: Portal,
        q: str = Query(default="", max_length=100),
        page: int = Query(default=1, ge=1, le=100000),
    ):
        return client.meters(q, page)

    @router.get(
        "/meters/export",
        response_model=Export,
        tags=["Meters"],
        summary="Fetch the complete meter inventory",
        description="Uses the portal's signed export. Cached for 60 seconds by default; refresh=true bypasses the cache. fetched_at records retrieval time, not source update time.",
    )
    def export(client: Portal, refresh: bool = False):
        return client.export(refresh)

    @router.get(
        "/meters/{meter_id}",
        response_model=MeterDetail,
        tags=["Meters"],
        summary="Read a nameplate, location, and hierarchy",
    )
    def meter(meter_id: MeterId, client: Portal):
        return client.meter(meter_id)

    @router.get(
        "/meters/{meter_id}/consumption",
        response_model=Consumption,
        tags=["Meters"],
        summary="Read the available consumption window",
        description="start and end are inclusive ISO 8601 timestamps with timezone offsets. Filtering is local and does not request older history. Measurements are cumulative registers by assumption; interval_kwh is their difference.",
    )
    def consumption(
        meter_id: MeterId,
        client: Portal,
        start: datetime | None = None,
        end: datetime | None = None,
    ):
        if any(value is not None and value.tzinfo is None for value in (start, end)):
            raise PortalError(
                "invalid_request", "start and end must include a timezone offset.", 422
            )
        if start is not None and end is not None and start > end:
            raise PortalError(
                "invalid_request", "start must be earlier than or equal to end.", 422
            )
        return client.consumption(meter_id, start, end)

    @router.get(
        "/transformers",
        response_model=Page[Transformer],
        tags=["Network"],
        summary="List distribution transformers",
    )
    def transformers(client: Portal, page: int = Query(default=1, ge=1, le=100000)):
        return client.transformers(page)

    @router.get(
        "/hierarchy",
        response_model=NetworkHierarchy,
        tags=["Network"],
        summary="Read the network graph, including conflicting relationships",
        description="Nodes are identified by level and code. Multiple names and parent relationships are retained and flagged rather than forced into a potentially incorrect tree. Shares the inventory export cache.",
    )
    def hierarchy(client: Portal, refresh: bool = False):
        return network(client.export(refresh))

    app.include_router(router)
    default_openapi = app.openapi

    def openapi():
        schema = default_openapi()
        if not settings.api_key.get_secret_value():
            for path in schema["paths"].values():
                for operation in path.values():
                    if (
                        isinstance(operation, dict)
                        and "security" in operation
                        and {} not in operation["security"]
                    ):
                        operation["security"].append({})
        return schema

    app.openapi = openapi
    return app


app = create_app()
