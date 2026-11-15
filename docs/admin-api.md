# Admin API reference

The admin API, under `/api`, serves the web UI and automated tests. It returns JSON and **needs
no authentication**, so keep the port local. For simple polling, reads of an unknown tenant
return empty results (`200`) instead of `404`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | `{"status": "ok"}` |
| GET | `/api/config` | The SCIM token and base-URL templates. The UI uses this for its connection panel. |
| GET | `/api/tenants` | All tenants with counts |
| GET | `/api/tenants/{t}/users` | Full SCIM user resources |
| GET | `/api/tenants/{t}/groups` | Full SCIM group resources |
| GET | `/api/tenants/{t}/requests` | Request log, newest first |
| DELETE | `/api/tenants/{t}/requests` | Clear the request log only (`204`) |
| GET | `/api/tenants/{t}/summary` | Counts plus a compact snapshot for assertions |
| GET | `/api/tenants/{t}/faults` | Fault-injection settings |
| PUT | `/api/tenants/{t}/faults` | Update fault settings. A partial object is accepted. |
| DELETE | `/api/tenants/{t}` | Delete the tenant: resources, request log and fault settings (`204`) |

## `GET /api/tenants`

```json
[ { "tenant": "hris", "users": 425, "usersActive": 425, "usersInactive": 0,
    "groups": 18, "requests": 1358, "lastActivity": "2026-11-14T11:45:13.413Z" } ]
```

## `GET /api/tenants/{t}/requests`

| Query | Description |
|---|---|
| `limit` | 1 to 5000, default 100 |
| `method` | `GET`, `POST`, `PUT`, `PATCH` or `DELETE` |
| `status` | An exact code (`404`) or a class (`4xx`) |
| `resource` | `Users`, `Groups`, or a resource id, giving the history of one user |

Each entry contains:
- timestamp, tenant, method, path with query, resource type and id
- status, duration in ms, and whether auth succeeded
- the request body (truncated at 16 KB), the response body (truncated at 4 KB) and any error
  detail

The latest **5000** entries are kept across all tenants.

## `GET /api/tenants/{t}/summary`

This endpoint is built for end-to-end assertions. It is small, stable and sorted:

```json
{
  "tenant": "hris",
  "counts": { "users": 425, "usersActive": 425, "usersInactive": 0, "groups": 18, "requests": 1358 },
  "requestsByMethod": { "GET": 30, "POST": 443, "PUT": 885 },
  "requestsByStatus": { "200": 915, "201": 443 },
  "faults": { "latencyMs": 0, "errorRatePercent": 0, "rateLimitEvery": 0, "readOnly": false },
  "users":  [ { "id": "…", "userName": "aarav.reddy@halcyon.example", "externalId": "3332…", "active": true, "displayName": "Aarav Reddy" } ],
  "groups": [ { "id": "…", "displayName": "Division: Engineering", "externalId": "div-engineering",
                "members": [ "aarav.reddy@halcyon.example", "…" ] } ]
}
```

Group `members` are listed as **userNames**, so you can compare them directly with what the
client intended to send.

Example: a pytest assertion.

```python
summary = httpx.get("http://localhost:8100/api/tenants/hris/summary").json()
assert summary["counts"]["usersActive"] == expected_active
assert {"alice@example.com", "bob@example.com"} <= set(next(
    g for g in summary["groups"] if g["displayName"] == "Engineering")["members"])
```

## Fault settings

See [fault-injection.md](fault-injection.md).

```bash
curl -X PUT http://localhost:8100/api/tenants/hris/faults -H "Content-Type: application/json" \
  -d '{"latencyMs": 250, "rateLimitEvery": 20}'
```

## Deleting a tenant

`DELETE /api/tenants/{t}` removes everything about the tenant. In the UI, click **×** on the
tenant's card on the Tenants screen, or use **Reset tenant** inside it. If a SCIM client calls
again later, the tenant is re-created empty.
