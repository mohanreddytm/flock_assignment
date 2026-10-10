# Portal protocol

Observed against `https://urja-ops.flockenergy.tech` on 10 October 2026. These are findings from direct HTTP requests and the frontend JavaScript served to ordinary portal users. This was not a browser DevTools capture. The investigation used the supplied operator account and read-only data paths; the only POST used was login.

## Discovery path

1. `GET /` without a cookie redirected to `/login`.
2. The login HTML contained a POST form with `email` and `password`, not `username`. There was no hidden CSRF field in the observed form.
3. After login, `/meters` initially rendered an empty table in the raw HTML. That was not evidence of an empty inventory: its frontend script calls `/portal/meters/search` on mount.
4. The meter page's script identified the `/geo` and `/energy` requests. The server-rendered HTML already contained nameplate and hierarchy data.
5. The Transformers frontend exposed `/portal/dts`, `/portal/keys`, and the signed `/portal/export` flow behind its normal “Export all meters” button.

Observed frontend build files included `5.C4dYcCRt.js` for the meter list, `6.l777LXKL.js` for details, and `4.2Bgc2kUI.js` for transformers. Those hashed names are evidence from this build, not paths the adapter relies on at runtime.

## Authentication

`POST /login` uses `application/x-www-form-urlencoded` fields:

```text
email=<configured operator email>
password=<configured password>
```

The requests sent `Origin: https://urja-ops.flockenergy.tech`. Whether Origin is strictly required was not tested. The adapter sends it to match normal form submission.

The observed successful response was HTTP **200** with this JSON action result:

```json
{"type":"redirect","status":303,"location":"/meters"}
```

The response set `__Secure-better-auth.session_token` with `Max-Age=3600`, `Path=/`, `HttpOnly`, `Secure`, and `SameSite=Lax`. Cookie values are deliberately omitted. The adapter requires both a success/redirect result and a session cookie; HTTP 200 by itself is not proof of successful login.

An unauthenticated request to `/portal/meters/search` returned HTTP 401:

```json
{"error":"unauthorized","message":"A valid session is required."}
```

The adapter also handles redirects to `/login` and a returned HTML login form. Those extra cases are tested with mock responses. Live recovery was checked by clearing the local cookie jar, not by waiting an hour for actual expiry. Invalid-password behavior was not deliberately exercised against the shared account.

## Read paths

| Method | Path | Parameters | Observed response |
| --- | --- | --- | --- |
| GET | `/meters` | None | HTML shell; initial table has no fetched rows |
| GET | `/portal/meters/search` | `q`, `page` | `{data, total, page, pageSize}` |
| GET | `/meters/{id}` | Meter ID in path | HTML nameplate and hierarchy; embedded Svelte data |
| GET | `/portal/meters/{id}/geo` | Meter ID in path | `{data: {latitude, longitude}}` with numeric strings |
| GET | `/portal/meters/{id}/energy` | No query used by the observed UI | `{data: [...]}` of timestamp, kwh, kvah, voltR |
| GET | `/portal/dts` | `page` | Transformer page with total and page size |
| GET | `/portal/keys` | Session cookie | `{data: {signingSecret}}` |
| GET | `/portal/export` | Exact query `page=1`, signed headers | Complete inventory in `{data, total}` |

Search returned **403** meters with a page size of **20**. Page 21 returned the final three, `J100400`, `J100401`, and `J100402`. The transformer listing reported **40** transformers. The normal search UI describes `q` as a meter-number or serial-number search; no arbitrary-attribute filtering is promised by this wrapper.

A nonexistent meter detail page returned HTTP 404. The consumption adapter checks the detail path first so a nonexistent meter is not accidentally represented as valid empty history.

## Detail formats

The observed legacy format in embedded page data was:

```json
{"data":[{"parameterName":"Meter ID","parameterValue":"J100000"}]}
```

The v2 format had a JSON string inside `classData`:

```json
{"classData":"{\"installed_meter\":{\"MeterId\":\"J100004\"}}"}
```

Both render a **Nameplate** section with a `<dl>` containing `<dt>` labels and `<dd>` values. The adapter uses this rendered definition list instead of evaluating JavaScript or implementing the framework's data protocol. Legacy labels include `Meter ID`, `Serial No`, and `Phase Type`; v2 labels include `MeterId`, `SerialNo`, and `PhaseType`. Removing spaces and punctuation for matching handles both. Original labels and values remain available in `nameplate`.

Hierarchy entries appear in the main content's breadcrumb `<nav>`. The adapter recognizes codes such as `Z-01`, `C-01`, `D-01`, `SD-01`, `SS-01`, `F-001`, and `DT-001`; it does not infer a level only from the entry's position. A missing entry therefore cannot shift every later level.

## Signed export

The normal Transformers UI fetches the signing secret using the authenticated session, then computes a lowercase hexadecimal HMAC-SHA256 signature over:

```text
GET\n/portal/export\npage=1\n<unix timestamp in seconds>
```

Those `\n` markers mean actual newline characters. It sends the exact query `page=1` and the headers `x-timestamp` and `x-signature`. The secret is obtained through the UI's ordinary endpoint; it is not a hardcoded or extracted server credential.

The live request returned all **403** records in one response, with `total: 403` and no `page` or `pageSize` metadata. The `page=1` argument is part of the observed signing contract; it is not evidence that the result needs to be paged. This adapter checks completeness and fails if a future response is partial. Other export query combinations, signature failure behavior, and clock-skew tolerance were not explored.

Export records include `meterId`, `serialNo`, `make`, `phaseType`, `installStatus`, `installType`, `build`, `dtCode`, a structured `hierarchy`, and numeric `geo.lat` / `geo.lng`. The observed build split was 238 legacy and 165 v2 records. The public API omits the portal's internal `build` field but keeps operational fields.

## Consumption and data quirks

- The sampled `J100000` energy response contained **337** readings, from `23/06/2026 23:30` to `30/06/2026 23:30`. The frontend did not supply dates. This is a fixed observed window, not a promise of “the last seven days” relative to today.
- Timestamps were `DD/MM/YYYY HH:MM` without offsets. `Asia/Kolkata` is an explicit wrapper assumption. Offset-bearing ISO timestamps are also supported by the parser through offline tests, but were not observed in that live sample.
- kWh, kVAh, voltage, latitude, and longitude were strings in the per-meter JSON paths. Bulk coordinates were numbers.
- kWh increased from `48438.74` to `48580.79` in the sample. That suggests cumulative registers. The difference is 142.05 kWh; summing those 337 register values would be misleading. Register meaning remains an assumption requiring confirmation.
- The UI uses truthiness for some energy display values, so numeric zero could display as a dash. The wrapper preserves zero and uses null only for recognized missing-value markers.
- The export contains empty hierarchy names and codes. Missing names fall back to the code; missing codes produce warnings and do not get invented IDs.
- `DT-007` occurs as both `Sanganer DT 7` and `Old Malviya Nagar Xfmr`. Some hierarchy codes also appear beneath different parents. The API keeps aliases and all observed parent links instead of choosing one arbitrarily.
- Optional numeric fields are nullable, but unrecognized non-numeric strings fail explicitly. Coordinates outside valid ranges become a missing location with a warning.

## Limits of these findings

Only a small sample of individual detail and energy pages was inspected; the full inventory was inspected through the bulk endpoint. No mutations, administrative routes, brute-force searches, or undocumented historical-date parameters were used. There is no evidence here for historical retention duration, register rollover semantics, maximum export size, or supported simultaneous-session count. The implementation and tests separate observed responses from defensive fallback handling.
