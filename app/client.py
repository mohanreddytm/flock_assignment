import hashlib
import hmac
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from app import normalize
from app.config import Settings
from app.errors import PortalError
from app.models import Export, Page


class UrjaClient:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.http = httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "UrjaAdapter/1.0", "Origin": settings.base_url},
        )
        self.lock = threading.RLock()
        self.authenticated = False
        self.cached_export = None
        self.cached_at = 0.0

    def close(self):
        self.http.close()

    def _send(self, method, path, **kwargs):
        try:
            return self.http.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise PortalError(
                "upstream_timeout", "The portal did not respond in time.", 504
            ) from None
        except httpx.RequestError:
            raise PortalError(
                "upstream_unavailable", "The portal could not be reached.", 502
            ) from None

    def login(self):
        with self.lock:
            self.authenticated = False
            self.cached_export = None
            if not self.settings.email or not self.settings.password.get_secret_value():
                raise PortalError(
                    "configuration_missing",
                    "Set URJA_EMAIL and URJA_PASSWORD before using the API.",
                    503,
                )
            self.http.cookies.clear()
            response = self._send(
                "POST",
                "/login",
                data={
                    "email": self.settings.email,
                    "password": self.settings.password.get_secret_value(),
                },
            )
            self._check_status(response, allow_redirect=True)
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            valid_redirect = (
                response.status_code in (302, 303)
                and response.headers.get("location") == "/meters"
            )
            valid_action = (
                isinstance(payload, dict)
                and payload.get("type") == "redirect"
                and payload.get("location") == "/meters"
            )
            session_cookie = any(
                cookie.name.endswith("better-auth.session_token")
                for cookie in self.http.cookies.jar
            )
            if not (valid_redirect or valid_action) or not session_cookie:
                raise PortalError(
                    "upstream_authentication_failed",
                    "The portal did not establish a session. Check the configured credentials.",
                    502,
                )
            self.authenticated = True

    @staticmethod
    def _expired(response):
        if response.status_code == 401:
            return True
        if response.is_redirect:
            return urlsplit(response.headers.get("location", "")).path == "/login"
        if "text/html" in response.headers.get("content-type", ""):
            soup = BeautifulSoup(response.text, "html.parser")
            return soup.select_one('form input[type="password"]') is not None
        return False

    @staticmethod
    def _check_status(response, allow_redirect=False):
        if response.status_code == 404:
            raise PortalError(
                "not_found", "The requested resource was not found in the portal.", 404
            )
        if response.status_code == 429:
            raise PortalError(
                "upstream_rate_limited", "The portal is busy. Try again later.", 503
            )
        if response.status_code >= 400 or (response.is_redirect and not allow_redirect):
            raise PortalError(
                "upstream_error",
                "The portal rejected the request or returned an unexpected response.",
                502,
            )

    def _get(self, path, params=None, signed=False):
        with self.lock:
            if not self.authenticated:
                self.login()
            for attempt in range(2):
                headers = {}
                if signed:
                    key_payload = self._json(self._get("/portal/keys"))
                    data = key_payload.get("data")
                    secret = (
                        data.get("signingSecret") if isinstance(data, dict) else None
                    )
                    if not isinstance(secret, str) or not secret:
                        raise PortalError(
                            "invalid_upstream_data",
                            "The portal export signing key was missing.",
                        )
                    query = str(httpx.QueryParams(params))
                    timestamp = str(int(time.time()))
                    message = f"GET\n{path}\n{query}\n{timestamp}"
                    headers = {
                        "x-timestamp": timestamp,
                        "x-signature": hmac.new(
                            secret.encode(), message.encode(), hashlib.sha256
                        ).hexdigest(),
                    }
                response = self._send("GET", path, params=params, headers=headers)
                if self._expired(response):
                    self.authenticated = False
                    if attempt == 0:
                        self.login()
                        continue
                    raise PortalError(
                        "upstream_authentication_failed",
                        "The portal rejected the refreshed session.",
                        502,
                    )
                self._check_status(response)
                return response

    @staticmethod
    def _json(response):
        try:
            payload = response.json()
        except ValueError:
            raise PortalError(
                "invalid_upstream_data", "The portal returned invalid JSON."
            ) from None
        if not isinstance(payload, dict):
            raise PortalError(
                "invalid_upstream_data",
                "The portal returned an unexpected JSON structure.",
            )
        return payload

    @staticmethod
    def _rows(payload):
        rows = payload.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise PortalError(
                "invalid_upstream_data", "The portal response has no valid data array."
            )
        return rows

    def _page(self, path, params, convert):
        payload = self._json(self._get(path, params))
        rows = self._rows(payload)
        total, page, size = (
            payload.get("total"),
            payload.get("page"),
            payload.get("pageSize"),
        )
        if (
            any(type(value) is not int for value in (total, page, size))
            or total < 0
            or page < 1
            or size < 1
        ):
            raise PortalError(
                "invalid_upstream_data", "The portal pagination metadata is invalid."
            )
        if (
            page != params["page"]
            or len(rows) > size
            or (not rows and (page - 1) * size < total)
        ):
            raise PortalError(
                "invalid_upstream_data", "The portal pagination data is inconsistent."
            )
        return Page(
            data=[convert(row) for row in rows],
            total=total,
            page=page,
            page_size=size,
            has_next=page * size < total,
        )

    def meters(self, query="", page=1):
        return self._page(
            "/portal/meters/search", {"q": query, "page": page}, normalize.meter
        )

    def transformers(self, page=1):
        return self._page("/portal/dts", {"page": page}, normalize.transformer)

    def meter(self, meter_id):
        html = self._get(f"/meters/{meter_id}").text
        geo = self._json(self._get(f"/portal/meters/{meter_id}/geo")).get("data")
        if not isinstance(geo, dict):
            raise PortalError(
                "invalid_upstream_data", "The portal location payload is invalid."
            )
        return normalize.detail(html, meter_id, geo)

    def consumption(self, meter_id, start=None, end=None):
        self._get(f"/meters/{meter_id}")
        payload = self._json(self._get(f"/portal/meters/{meter_id}/energy"))
        return normalize.consumption(meter_id, self._rows(payload), start, end)

    def export(self, refresh=False):
        with self.lock:
            if (
                not refresh
                and self.cached_export is not None
                and time.monotonic() - self.cached_at < self.settings.cache_seconds
            ):
                return self.cached_export.model_copy(deep=True)
            payload = self._json(self._get("/portal/export", {"page": 1}, signed=True))
            rows = self._rows(payload)
            total = payload.get("total")
            if type(total) is not int or total < 0 or len(rows) != total:
                raise PortalError(
                    "incomplete_export",
                    "The portal export was incomplete; no partial dataset was returned.",
                )
            identifiers = [normalize.meter(row).meter_id for row in rows]
            if len(set(identifiers)) != len(identifiers):
                raise PortalError(
                    "invalid_upstream_data",
                    "The portal export contains duplicate meter identifiers.",
                )
            result = Export(
                data=[normalize.export_meter(row) for row in rows],
                total=total,
                fetched_at=datetime.now(timezone.utc),
                warnings=[],
            )
            self.cached_export = result
            self.cached_at = time.monotonic()
            return result.model_copy(deep=True)
