# Validation

Verified on 10 October 2026 using Python 3.12.

## Offline checks

`python -m pytest -q`: **43 passed**.

Coverage includes:

- Legacy and v2 nameplate HTML from the live portal, with scripts and comments removed from fixtures.
- Missing and malformed values, zero preservation, leading-zero serial numbers, timestamp sorting, time filtering, register decreases, and duplicates.
- Lazy login, cookie reuse, one-time recovery for 401/login redirect/login HTML, and stopping after repeated auth failure.
- Timeout/error mapping, incomplete exports, repeated meter identifiers, and malformed pagination.
- Signed export headers, cache reuse, forced refresh, and protection from callers mutating the cached result.
- Concurrent requests sharing a login, wrapper API-key enforcement, invalid request handling, and OpenAPI/schema agreement.

The installed Starlette release emits a deprecation warning about its HTTPX-backed TestClient. Tests pass; this warning does not come from the portal adapter. The runtime uses HTTPX as requested. The exact tested dependency versions are in `requirements-lock.txt`.

## Live checks

`python -m scripts.smoke_test` passed against the supplied portal account through the FastAPI routes.

| Check | Result |
| --- | --- |
| Meter listing | HTTP 200; total 403 |
| `J100000` detail | HTTP 200; parsed nameplate, hierarchy, and location |
| `J100000` consumption | HTTP 200; 337 readings |
| Transformer listing | HTTP 200; total 40 |
| Complete signed inventory export | HTTP 200; 403 unique meters |
| Hierarchy graph | HTTP 200; 119 nodes |
| Clear cookie jar, then request meters again | Automatic login and successful retry |

Additional direct inspection confirmed the v2 detail format for `J100004`, a 404 for an unknown meter, and the final search page containing three records. The unauthenticated internal search endpoint returned 401. The full export was read for hierarchy anomalies.

Live validation is a snapshot, not a guarantee that the portal remains unchanged. Actual one-hour expiry, upstream overload, invalid-password behavior, and unexpected payloads were not induced against the shared portal. Relevant recovery and failure cases use offline mock responses instead. No meter records were changed.

## Reproduce

Install `requirements-dev.txt`, fill `.env`, and run:

```bash
python -m pytest -q
python -m scripts.export_openapi
python -m scripts.smoke_test
```

The first two do not need a live session. The third makes authenticated read requests and a login request when needed. It deliberately removes only its own local session cookie to check recovery.
