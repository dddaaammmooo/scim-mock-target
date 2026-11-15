# End-to-end testing with scim-mock-hris

[scim-mock-hris](https://github.com/dddaaammmooo/scim-mock-hris) (MockHRIS) is a mock HR system
with a PingOne-style provisioning engine. It ships with an integration called **MockSCIMTarget**
that is already set up to provision every current employee to this server. Together the two
apps show the whole lifecycle: joiners, movers, leavers, rehires and drift.

```
MockHRIS (:8000) ── SCIM 2.0 (Bearer target-token) ──▶ scim-target (:8100, tenant "hris")
        ▲                                                       │
        └──── Verify: reads Users/Groups back and reconciles ◀──┘
```

## 1. Run both apps

```bash
git clone https://github.com/dddaaammmooo/scim-target.git
git clone https://github.com/dddaaammmooo/scim-mock-hris.git
(cd scim-target && docker compose up -d --build)
(cd scim-mock-hris && docker compose up -d --build)
```

They run as two separate compose projects. MockHRIS reaches the target through
`host.docker.internal`. Its compose file maps that to the host gateway, so this works on Linux
too.

| Setting in MockHRIS (*Integrations → MockSCIMTarget → Connection*) | Value |
|---|---|
| SCIM base URL | `http://host.docker.internal:8100/t/hris/scim/v2` |
| Auth | Bearer `target-token` |

If you run MockHRIS outside Docker, use `http://localhost:8100/t/hris/scim/v2` instead.

## 2. Provision

In MockHRIS, open **Integrations → MockSCIMTarget** and click **Sync now**. In a default
dataset this creates about 425 users and 18 groups. Then click **Verify**: it should report
*"Verified — the target matches the HRIS"*.

On this side:
- The **Dashboard** shows the counts.
- The **Users** page shows the core attributes plus the enterprise extension (department,
  division, cost centre, employee number, organisation, manager).
- The **Request log** shows each `POST /Users`, `POST /Groups` and the follow-up `PUT`s that set
  manager references.

## 3. Scenarios

| Scenario | In MockHRIS | Expected here | Verify before sync | Verify after sync |
|---|---|---|---|---|
| Leaver | Terminate a person | `PATCH active=false`; the user stays but shows as suspended | ✗ *should be disabled but still active* | ✓ |
| Rehire | Set lifecycle back to `employed` | `PUT` with `active: true` | ✗ | ✓ |
| Mover | Change team, title or site | `PUT` with updated attributes; group memberships change | ✗ *attribute mismatches* | ✓ |
| Drift | Edit a user **here**, e.g. PATCH `title` | n/a | ✗ shows the exact attribute, expected vs actual | ✓ after a *Full resync* |
| Target reset | Delete the `hris` tenant here (× on its card) | n/a | ✗ *missing on target* | Use **Forget state** in MockHRIS, then sync: everything is re-created |
| Flaky target | Set `errorRatePercent` here | Some 500s in the log | | Run is `partial`; the next sync fixes it |

## 4. Automate it

Both apps have JSON APIs, so an end-to-end check is only a few calls:

```python
import time, httpx
H, T = "http://localhost:8000", "http://localhost:8100"

run = httpx.post(f"{H}/api/integrations/scim-target/sync", json={}).json()
while run["status"] == "running":
    time.sleep(0.5)
    run = httpx.get(f"{H}/api/runs/{run['id']}").json()
assert run["status"] == "succeeded"

report = httpx.post(f"{H}/api/integrations/scim-target/verify").json()
assert report["ok"], report["users"]

summary = httpx.get(f"{T}/api/tenants/hris/summary").json()
assert summary["counts"]["usersActive"] == report["users"]["expectedActive"]
```
