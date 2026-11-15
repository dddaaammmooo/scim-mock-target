# Fault injection

Real SCIM targets are slow, rate-limited, flaky or temporarily read-only. A good provisioning
client retries, backs off, and reports partial failures clearly. Fault injection lets you test
that behaviour on purpose.

Faults are configured **per tenant**, on the UI's *Fault injection* page or through the admin
API, and are all off by default:

```json
{ "latencyMs": 0, "errorRatePercent": 0, "rateLimitEvery": 0, "readOnly": false }
```

| Setting | Effect |
|---|---|
| `latencyMs` | Adds this delay to every SCIM request |
| `rateLimitEvery` | Every Nth request gets `429 Too Many Requests` with `Retry-After: 1`. `0` turns it off. |
| `errorRatePercent` | This percentage of requests get a random `500` |
| `readOnly` | `POST`, `PUT`, `PATCH` and `DELETE` get `503 Service Unavailable` |

For each SCIM request the checks run in this order: latency, then rate limit, then random
error, then read-only. Injected responses are normal SCIM error documents and are recorded in
the request log. Saving fault settings resets the tenant's request counter, so
`rateLimitEvery` restarts from 1.

## Recipes

```bash
F=http://localhost:8100/api/tenants/hris/faults
J="Content-Type: application/json"

# Throttling: does the client honour Retry-After?
curl -X PUT $F -H "$J" -d '{"rateLimitEvery": 10}'

# A slow target: do timeouts and progress reporting behave?
curl -X PUT $F -H "$J" -d '{"latencyMs": 1500}'

# Flaky: are failures isolated per operation and retried on the next run?
curl -X PUT $F -H "$J" -d '{"errorRatePercent": 15}'

# Maintenance window: reads work, writes fail
curl -X PUT $F -H "$J" -d '{"readOnly": true}'

# Back to normal
curl -X PUT $F -H "$J" -d '{"latencyMs": 0, "errorRatePercent": 0, "rateLimitEvery": 0, "readOnly": false}'
```

## What to look for

With [scim-mock-hris](https://github.com/dddaaammmooo/scim-mock-hris) as the client:

- **429s:** retried after `Retry-After`, up to three times per call, so a sync still succeeds.
- **500s:** the run finishes as `partial`, and each failed operation shows its HTTP status. Its
  provisioning state isn't advanced, so the **next sync retries it**. **Verify** then reports
  whatever is still missing.
- **Read-only:** creates and updates fail with `503`, and Verify shows the drift until a sync
  runs after the window ends.
