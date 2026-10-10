# Urja Meter API

A small FastAPI service that turns Urja Meter Ops into a read-only JSON API. It signs in to the portal, keeps its session cookie, and renews the session when it stops working. Clients do not need to handle the portal's login or response formats.

The core API covers meter search, individual meter details, and consumption readings. It also includes the full inventory export, transformer listings, and a network graph that keeps conflicting hierarchy relationships visible.

## Run locally

Use Python 3.10 or newer. This project was tested with Python 3.12.

### Windows PowerShell

```powershell
cd flock-energy-api
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### macOS or Linux

```bash
cd flock-energy-api
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Set `URJA_PASSWORD` in `.env` to the password supplied in the assignment email. Then run:

```bash
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for Swagger UI. Select an endpoint, click **Try it out**, and click **Execute**. There is no separate frontend build. The root URL redirects to the docs.

No database, paid service, or API subscription is required. Swagger's browser assets load from a CDN; the API and checked-in OpenAPI schema work without those assets.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `URJA_BASE_URL` | `https://urja-ops.flockenergy.tech` | HTTPS origin of the portal |
| `URJA_EMAIL` | Empty; example file supplies the assignment username | Portal login |
| `URJA_PASSWORD` | Empty | Set locally from the assignment email |
| `URJA_TIMEOUT` | `20` | HTTPX timeout in seconds for each network phase |
| `URJA_CACHE_SECONDS` | `60` | Lifetime of the inventory export cache; `0` disables reuse |
| `URJA_API_KEY` | Empty | Optional key required as `X-API-Key` on `/api/v1` routes |

The password, session cookie, and export signing secret are never returned to API clients. `.env` is ignored by Git and is not part of the source archive. This service uses one configured upstream operator account. `POST /auth/login` renews that account's session; it does not create accounts or accept credentials from callers.

The default setup is for a local reviewer. Before exposing it remotely, set an API key and put it behind TLS and request-rate controls. `/health`, `/docs`, and `/openapi.json` remain public and contain no meter records. `/health` is a process check, not a portal availability check.

## Endpoints

All data routes start with `/api/v1`.

| Method | Path | Behavior |
| --- | --- | --- |
| `POST` | `/auth/login` | Renew the server's portal session; normally automatic |
| `GET` | `/meters?q=&page=1` | Search by meter number or serial; portal pages contain up to 20 rows |
| `GET` | `/meters/{meter_id}` | Nameplate, location, hierarchy, and data-quality warnings |
| `GET` | `/meters/{meter_id}/consumption` | Available readings with numeric values and timestamp offsets |
| `GET` | `/meters/export?refresh=false` | Complete normalized inventory, including locations and hierarchy |
| `GET` | `/transformers?page=1` | Transformer listing, feeder code, and capacity |
| `GET` | `/hierarchy?refresh=false` | Network nodes, parent relationships, aliases, and meter membership |

The meter ID is a string; serial numbers remain strings to preserve leading zeros. No write routes are exposed for meter data.

### Sample request

Use `curl.exe` instead of `curl` in Windows PowerShell if `curl` is an alias.

```bash
curl "http://127.0.0.1:8000/api/v1/meters?q=J100000"
```

Observed response shape:

```json
{
  "data": [
    {
      "meter_id": "J100000",
      "serial_number": "SE33962",
      "make": "HPL",
      "phase": "single",
      "status": "Decommissioned",
      "transformer_code": "DT-001"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20,
  "has_next": false
}
```

```bash
curl "http://127.0.0.1:8000/api/v1/meters/J100000"
curl "http://127.0.0.1:8000/api/v1/meters/J100000/consumption?start=2026-06-24T00:00:00Z&end=2026-06-25T00:00:00Z"
curl "http://127.0.0.1:8000/api/v1/hierarchy"
curl "http://127.0.0.1:8000/api/v1/meters/export?refresh=true"
```

If an API key is configured, add `-H "X-API-Key: your-key"`. In Swagger UI, use **Authorize**.

### Reading consumption correctly

The portal sample contains increasing kWh and kVAh values. This implementation treats them as cumulative registers, but the portal does not explicitly define that meaning. Both the assumption and the inferred timezone appear in each consumption response.

- `kwh` and `kvah` preserve the numeric register values.
- `interval_kwh` is the difference from the preceding returned reading.
- The first reading has no interval. Missing values, decreasing registers, and duplicate timestamps do not produce an interval difference.
- `observed_usage_kwh` sums only valid adjacent differences. It is not a billing total. No interval means `null`, not a fabricated zero.
- Timezone-less timestamps are interpreted as `Asia/Kolkata`; callers must include an offset in `start` and `end`.
- Both filter boundaries are inclusive. Filtering only narrows the default history returned by the portal; it does not retrieve older data.
- Long time gaps are not filled or resampled. A valid difference across a gap represents that whole gap.

The sampled portal history was in June 2026 even though verification happened in October. An empty recent-date filter can be valid.

## Structure

| File | Responsibility |
| --- | --- |
| `app/main.py` | Routes, input validation, optional API key, error responses, application lifetime |
| `app/client.py` | HTTP session, authentication recovery, signing, endpoint calls, export cache |
| `app/normalize.py` | HTML parsing, numeric/date conversion, consumption differences, hierarchy graph |
| `app/models.py` | Response schemas |
| `app/config.py` | Environment settings |
| `app/errors.py` | Small shared error type |
| `tests/` | Offline tests and reduced HTML fixtures from both live detail formats |
| `scripts/` | OpenAPI export and opt-in live smoke check |

Routes call the adapter, and the adapter passes portal payloads into normalization functions. The parser never evaluates the portal's embedded JavaScript. A failure in the portal is returned as a bounded, readable error rather than a traceback or upstream HTML.

## Decisions and trade-offs

**Use JSON where available.** Search, coordinates, energy, transformers, and bulk export have internal JSON endpoints. Individual detail pages render a useful definition list, so that one path uses BeautifulSoup. Both portal detail formats become the same HTML layout; parsing it avoids coupling the adapter to Svelte's embedded data serialization.

**One session per process.** A reusable HTTPX client owns the cookie jar. A lock serializes upstream requests and authentication. This avoids simultaneous refreshes and is sufficient for a small local service, at the cost of throughput. Multiple worker processes have independent sessions and caches.

**Bounded recovery.** A `401`, login redirect, or HTML login form triggers one new login and one replay. The second failure stops. Generic `403` responses are not assumed to mean session expiry. Timeouts and server errors are surfaced without automatic retry storms.

**A small cache.** Only the signed inventory export is cached. Hierarchy requests reuse it. The default lifetime is 60 seconds, `refresh=true` bypasses it, and `fetched_at` remains the original retrieval time. Failed refreshes do not serve stale data silently. Meter search, details, and consumption go to the portal each time. Login invalidates the export cache.

**Keep ambiguity visible.** Hierarchy nodes use level plus code as their identity. Multiple names and multiple parents are retained, with warnings. A missing hierarchy code does not get an invented identity. The graph stops linking across that missing level. The graph can therefore be a forest or have multiple parents instead of pretending the data is one clean tree.

**Fail clearly on a changed protocol.** A missing nameplate, malformed measurement, wrong meter ID, incomplete export, or invalid pagination returns an error. Empty values become `null`; malformed values do not quietly become `null`. Export completeness is checked against `total`, and duplicate meter IDs are rejected.

**Keep the public API simple.** Status and make remain strings rather than rigid enums that could break on a new upstream value. Pagination follows the portal instead of inventing a second page-size system.

## Errors

```json
{
  "error": {
    "code": "upstream_timeout",
    "message": "The portal did not respond in time."
  }
}
```

| Status | Meaning |
| --- | --- |
| `401` | Missing or incorrect wrapper API key |
| `404` | Portal resource not found |
| `422` | Invalid path or query, missing timestamp offset, or reversed time range |
| `502` | Upstream connection/authentication/protocol failure |
| `503` | Portal rate limit or missing local credentials |
| `504` | Portal timeout |

A portal login failure is `502`, not `401`: it concerns this service's upstream credentials, not the caller's API key. Error bodies omit upstream response text and credentials.

## Tests and OpenAPI

After installing dependencies, use the Python executable from your virtual environment:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m scripts.export_openapi
python -m scripts.smoke_test
```

On Windows, replace `python` with `.venv\Scripts\python.exe` if the environment is not activated; on macOS/Linux use `.venv/bin/python`.

The offline suite does not need portal credentials. The smoke script is opt-in and uses the credentials in `.env`. It checks real data routes and simulates session loss by clearing its local cookie jar. It does not change meter data.

The checked-in [openapi.json](openapi.json) is OpenAPI 3.1 generated from the same FastAPI application served at `/openapi.json`. Run the export script after changing models or routes. The schema test detects drift. The optional API-key security requirement reflects whether `URJA_API_KEY` is set when the schema is generated.

See [VALIDATION.md](VALIDATION.md) for the actual verification results, [PROTOCOL.md](PROTOCOL.md) for portal findings, and [REFLECTION.md](REFLECTION.md) for implementation notes. `requirements-lock.txt` records the exact tested environment, including test dependencies; use it instead of the regular requirements if you want those exact transitive versions.

## Assumptions and omissions

The service assumes one operator account, a local reviewer, Indian local time for timestamps without offsets, and cumulative energy registers. The latter two need confirmation from the utility before using the data for billing or operational decisions.

A separate dashboard, database, geospatial search, background sync, multi-user credential storage, and deployment automation are intentionally left out. They would expand the scope without strengthening the core adapter. No undocumented date parameters are guessed. The signed export currently returns the whole inventory in one response; an incomplete response fails explicitly rather than returning a misleading partial inventory.

The in-memory export is appropriate for the observed 403 meters. Export storage is O(meters); graph construction and membership lists grow with meters times hierarchy depth. List-based membership checks also make grouping slower as groups grow. At tens or hundreds of thousands of meters, use a paged ingestion job, set-based grouping, a persistent indexed store, and snapshot/version metadata. There is no hard memory limit or streaming parser in this version.

With another day, the priorities would be confirming timezone/register semantics, inspecting more meters' energy formats, adding better freshness/latency visibility, and replacing synchronous refresh with controlled background snapshots. Production deployment would also need managed secrets and stronger caller authentication.

## Authorship and submission

This implementation, portal investigation, tests, and initial documentation were produced with substantial AI assistance in this session. It should not be represented as independently written or independently investigated by the candidate. The assignment limits AI to guidance, so the candidate needs to perform their own investigation and implementation work and follow the employer's rules before submitting. The reflection describes this implementation session, not personal experiences invented for the candidate.

To inspect or change the source, extract the archive and open the **flock-energy-api folder in VS Code**. All code is ordinary Python; there is no generated application bundle to edit.

This archive is not a published GitHub repository. After your own review, create a public repository and push the source. Keep `.env`, cookies, and exported datasets out of Git. The README and all required documentation are included; no email has been sent.
