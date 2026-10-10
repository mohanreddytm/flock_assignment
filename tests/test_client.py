import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.client import UrjaClient
from app.errors import PortalError

COOKIE = "__Secure-better-auth.session_token=test-session; Path=/; Secure; HttpOnly"
LISTING = {
    "data": [{"meterId": "J1", "serialNo": "00123"}],
    "total": 1,
    "page": 1,
    "pageSize": 20,
}


def login_response():
    return httpx.Response(
        200,
        json={"type": "redirect", "status": 303, "location": "/meters"},
        headers={"set-cookie": COOKIE},
    )


def test_lazy_login_and_cookie_reuse(settings):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/login":
            assert b"email=operator%40example.test" in request.content
            assert request.headers["origin"] == settings.base_url
            return login_response()
        assert "session_token=test-session" in request.headers["cookie"]
        return httpx.Response(200, json=LISTING)

    client = UrjaClient(settings, httpx.MockTransport(handle))
    assert client.meters().data[0].serial_number == "00123"
    client.meters()
    assert calls.count("/login") == 1
    client.close()


@pytest.mark.parametrize("kind", ["401", "redirect", "html"])
def test_expired_session_is_retried_once(settings, kind):
    logins = 0
    reads = 0

    def handle(request):
        nonlocal logins, reads
        if request.url.path == "/login":
            logins += 1
            return login_response()
        reads += 1
        if reads == 1:
            if kind == "redirect":
                return httpx.Response(302, headers={"location": "/login"})
            if kind == "html":
                return httpx.Response(
                    200,
                    text='<form><input type="password"></form>',
                    headers={"content-type": "text/html"},
                )
            return httpx.Response(401)
        return httpx.Response(200, json=LISTING)

    client = UrjaClient(settings, httpx.MockTransport(handle))
    assert client.meters().total == 1
    assert (logins, reads) == (2, 2)
    client.close()


def test_persistent_auth_failure_stops(settings):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        return login_response() if request.url.path == "/login" else httpx.Response(401)

    client = UrjaClient(settings, httpx.MockTransport(handle))
    with pytest.raises(PortalError) as error:
        client.meters()
    assert error.value.code == "upstream_authentication_failed"
    assert len(paths) == 4
    client.close()


def test_login_200_is_not_automatically_success(settings):
    client = UrjaClient(
        settings,
        httpx.MockTransport(
            lambda request: httpx.Response(200, json={"type": "failure"})
        ),
    )
    with pytest.raises(PortalError):
        client.login()
    assert not client.authenticated
    client.close()


@pytest.mark.parametrize(
    "status,expected", [(404, 404), (429, 503), (500, 502), (403, 502)]
)
def test_error_mapping(settings, status, expected):
    client = UrjaClient(
        settings,
        httpx.MockTransport(
            lambda request: (
                login_response()
                if request.url.path == "/login"
                else httpx.Response(status, text="private upstream error")
            )
        ),
    )
    with pytest.raises(PortalError) as error:
        client.meters()
    assert error.value.status == expected
    assert "private" not in error.value.message
    client.close()


def test_timeout_mapping(settings):
    def handle(request):
        raise httpx.ReadTimeout("secret details", request=request)

    client = UrjaClient(settings, httpx.MockTransport(handle))
    with pytest.raises(PortalError) as error:
        client.meters()
    assert error.value.status == 504
    client.close()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"data": "bad"},
        {"data": [], "total": 2, "page": 1, "pageSize": 20},
        {"data": [], "total": "0", "page": 1, "pageSize": 20},
    ],
)
def test_invalid_payload_does_not_return_empty_success(settings, payload):
    client = UrjaClient(
        settings,
        httpx.MockTransport(
            lambda request: (
                login_response()
                if request.url.path == "/login"
                else httpx.Response(200, json=payload)
            )
        ),
    )
    with pytest.raises(PortalError):
        client.meters()
    client.close()


def test_signed_export_cache_refresh_and_copy(settings):
    exports = 0

    def handle(request):
        nonlocal exports
        if request.url.path == "/login":
            return login_response()
        if request.url.path == "/portal/keys":
            return httpx.Response(200, json={"data": {"signingSecret": "test-key"}})
        exports += 1
        message = f"GET\n/portal/export\npage=1\n{request.headers['x-timestamp']}"
        assert (
            request.headers["x-signature"]
            == hmac.new(b"test-key", message.encode(), hashlib.sha256).hexdigest()
        )
        assert request.url.query == b"page=1"
        return httpx.Response(200, json={"data": [{"meterId": "J1"}], "total": 1})

    client = UrjaClient(settings, httpx.MockTransport(handle))
    first = client.export()
    first.data.clear()
    assert len(client.export().data) == 1
    assert exports == 1
    client.export(refresh=True)
    assert exports == 2
    client.close()


@pytest.mark.parametrize(
    "rows,total", [([], 1), ([{"meterId": "J1"}, {"meterId": "J1"}], 2)]
)
def test_incomplete_or_duplicate_export_fails(settings, rows, total):
    def handle(request):
        if request.url.path == "/login":
            return login_response()
        if request.url.path == "/portal/keys":
            return httpx.Response(200, json={"data": {"signingSecret": "test-key"}})
        return httpx.Response(200, json={"data": rows, "total": total})

    client = UrjaClient(settings, httpx.MockTransport(handle))
    with pytest.raises(PortalError):
        client.export()
    assert client.cached_export is None
    client.close()


def test_parallel_requests_share_one_login(settings):
    logins = []

    def handle(request):
        if request.url.path == "/login":
            logins.append(1)
            return login_response()
        return httpx.Response(200, json=LISTING)

    client = UrjaClient(settings, httpx.MockTransport(handle))
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: client.meters(), range(5)))
    assert len(logins) == 1
    assert all(result.total == 1 for result in results)
    client.close()
