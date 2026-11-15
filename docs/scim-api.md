# SCIM API reference

MockSCIMTarget implements the parts of SCIM 2.0
([RFC 7643](https://www.rfc-editor.org/rfc/rfc7643), [RFC 7644](https://www.rfc-editor.org/rfc/rfc7644))
that provisioning clients use.

## Base URLs and tenants

Every resource belongs to a **tenant**: a slug matching `[A-Za-z0-9._-]{1,64}`. A tenant is
created automatically on the first **authenticated** SCIM request that names it. Two URL shapes
address the same tenant store:

| Shape | Base URL | Typical use |
|---|---|---|
| Generic | `/t/{tenant}/scim/v2` | Any SCIM client |
| GitHub EMU-shaped | `/scim/v2/enterprises/{tenant}` | Clients built for GitHub Enterprise Managed Users |

`meta.location` and the `Location` header echo whichever shape and host the client used.

## Authentication

Every SCIM request must send:

```
Authorization: Bearer <SCIM_TOKEN>        # default: target-token
```

A missing or wrong token gets a `401` SCIM error. A rejected request is still logged, but it
doesn't create the tenant.

## Content types

- Responses use `Content-Type: application/scim+json`.
- Requests may send `application/scim+json`, `application/json`, or no content type.

## Endpoints

All paths are relative to the base URL.

| Method | Path | Description |
|---|---|---|
| GET | `/ServiceProviderConfig` | Capabilities: patch and filter supported (maxResults 1000), etag supported; bulk, sort and changePassword not supported |
| GET | `/ResourceTypes`, `/ResourceTypes/{id}` | `User` and `Group` |
| GET | `/Schemas`, `/Schemas/{urn}` | Core User, core Group and Enterprise User schemas |
| GET | `/Users` | List or filter users |
| POST | `/Users` | Create a user: returns `201`, `Location` and `ETag` |
| GET | `/Users/{id}` | Get a user |
| PUT | `/Users/{id}` | Replace a user |
| PATCH | `/Users/{id}` | Modify a user (PatchOp) |
| DELETE | `/Users/{id}` | Delete a user: returns `204` and removes them from every group |
| GET, POST | `/Groups` | List or create groups |
| GET, PUT, PATCH, DELETE | `/Groups/{id}` | Group CRUD |

## Users

Required: `userName`. It is unique per tenant and compared case-insensitively. If `externalId`
is present it must also be unique.

Stored and returned as sent:
- the core attributes: `name`, `displayName`, `nickName`, `title`, `userType`, `active`,
  `emails`, `phoneNumbers`, `addresses`, `locale`, `timezone`, `preferredLanguage` and so on
- the **Enterprise User extension**,
  `urn:ietf:params:scim:schemas:extension:enterprise:2.0:User`, with its attributes
  `employeeNumber`, `costCenter`, `organization`, `division`, `department` and `manager`
- **any other attribute**, for example GitHub's `roles`

Server-managed attributes:
- `id`: a UUID.
- `meta`: `resourceType`, `created`, `lastModified`, `location`, and `version`, a weak ETag
  `W/"n"` that increments on every change and is also sent as the `ETag` header. **`If-Match` is
  not enforced.**
- `groups`: read-only and computed from group memberships, as `[{value, display, $ref}]`.

## Groups

Required: `displayName`, unique per tenant and case-insensitive. If `externalId` is present it
must also be unique.

`members` is a list of `{"value": "<user id>"}` entries. **Every member must be an existing
user**, otherwise the target returns `400 invalidValue`. Members are returned with `display`
(the user's displayName, or their userName if there is none) and `$ref`.

## Listing, filtering and pagination

| Parameter | Description |
|---|---|
| `filter` | SCIM filter expression (see below) |
| `startIndex` | 1-based start position (default 1) |
| `count` | Page size (default 100, max 1000) |
| `attributes` | Comma-separated attributes to return (`id` and `schemas` are always included) |
| `excludedAttributes` | Comma-separated attributes to omit, e.g. `members` on Groups |

**Filter syntax:**

| Element | Supported |
|---|---|
| Comparison | `eq`, `ne`, `co`, `sw`, `ew`, `gt`, `ge`, `lt`, `le`, `pr` |
| Logical | `and`, `or`, `not ( … )`, parentheses |
| Attribute paths | `userName`, `name.familyName`, `emails.value`, `meta.lastModified`, enterprise URN paths |
| Value paths | `emails[type eq "work" and value co "example.com"]` |

String comparisons are case-insensitive. A malformed filter returns `400 invalidFilter`.

```bash
TOKEN="Authorization: Bearer target-token"
B=http://localhost:8100/t/demo/scim/v2

curl -s -H "$TOKEN" "$B/Users?filter=userName%20eq%20%22ada.lovelace@example.com%22"
curl -s -H "$TOKEN" --get "$B/Users" --data-urlencode 'filter=emails[type eq "work" and value ew "example.com"]'
curl -s -H "$TOKEN" --get "$B/Users" --data-urlencode 'filter=active eq true and (title co "engineer" or userType eq "Contractor")'
curl -s -H "$TOKEN" "$B/Groups?excludedAttributes=members&count=50&startIndex=51"
```

The response is a `ListResponse` with `totalResults`, `itemsPerPage`, `startIndex` and
`Resources`.

## PATCH

The body must be a PatchOp document:

```json
{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
  "Operations": [ { "op": "replace", "path": "active", "value": false } ]
}
```

| Path form | Example |
|---|---|
| Simple attribute | `active`, `displayName`, `title` |
| Sub-attribute | `name.givenName` |
| Value filter | `emails[type eq "work"].value`. Creates the element if nothing matches. |
| Enterprise URN | `urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department` |
| Group member | `members[value eq "<user id>"]`, used with `remove` |
| No path | `{"op": "replace", "value": {"active": false, "title": "CTO"}}` |

`op` can be `add`, `replace` or `remove`. The target is lenient in a few ways:
- `remove` on `members` with a `value` array is accepted.
- The strings `"True"` and `"False"` are coerced to booleans for `active`.
- A `remove` whose filter matches nothing does nothing rather than failing.

Group membership examples:

```json
{ "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
  "Operations": [
    { "op": "add",    "path": "members", "value": [ { "value": "2819c223-7f76-453a-919d-413861904646" } ] },
    { "op": "remove", "path": "members[value eq \"902c246b-6245-4190-8e05-00816be7344a\"]" } ] }
```

## Errors

Errors use the SCIM error schema, with `status` as a string:

```json
{ "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
  "status": "409", "scimType": "uniqueness", "detail": "userName 'ada.lovelace@example.com' already exists" }
```

| Status | scimType | When |
|---|---|---|
| 400 | `invalidValue` | Missing `userName` or `displayName`; a group member that doesn't exist |
| 400 | `invalidFilter` | The filter can't be parsed |
| 400 | `invalidSyntax`, `invalidPath`, `noTarget`, `mutability` | Bad PatchOp document, path, or an attempt to change a read-only attribute |
| 401 | | Missing or wrong bearer token |
| 404 | | Unknown resource id |
| 409 | `uniqueness` | Duplicate `userName`, `externalId`, or group `displayName` |
| 429 / 500 / 503 | | Only when [fault injection](fault-injection.md) is enabled |

## Complete example: create, group, suspend, delete

```bash
TOKEN="Authorization: Bearer target-token"; CT="Content-Type: application/scim+json"
B=http://localhost:8100/t/demo/scim/v2

UID=$(curl -s -X POST "$B/Users" -H "$TOKEN" -H "$CT" -d '{"userName":"grace@example.com","active":true,
  "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User":{"department":"Engineering"}}' | jq -r .id)

GID=$(curl -s -X POST "$B/Groups" -H "$TOKEN" -H "$CT" \
  -d "{\"displayName\":\"Engineering\",\"members\":[{\"value\":\"$UID\"}]}" | jq -r .id)

curl -s -X PATCH "$B/Users/$UID" -H "$TOKEN" -H "$CT" \
  -d '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"replace","path":"active","value":false}]}'

curl -s -X DELETE "$B/Users/$UID" -H "$TOKEN" -o /dev/null -w "%{http_code}\n"   # 204, also leaves the group
```
