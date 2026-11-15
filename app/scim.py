"""SCIM resource service layer and static discovery documents."""
import copy
import uuid

from . import filters
from .errors import ScimError
from .patch import apply_patch

USER_URN = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_URN = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENT_URN = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
LIST_URN = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
CORE = {"User": USER_URN, "Group": GROUP_URN}
ENDPOINT = {"User": "Users", "Group": "Groups"}
SPC_URN = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"


# --------------------------------------------------------------------------- discovery documents
def _a(name, typ="string", multi=False, required=False, mutability="readWrite", unique="none", sub=None,
       case_exact=False, returned="default", desc=""):
    d = {"name": name, "type": typ, "multiValued": multi, "description": desc or name, "required": required,
         "caseExact": case_exact, "mutability": mutability, "returned": returned, "uniqueness": unique}
    if sub:
        d["subAttributes"] = sub
    return d


def _multi(name, extra=()):
    subs = [_a("value"), _a("display"), _a("type"), _a("primary", "boolean")] + list(extra)
    return _a(name, "complex", True, sub=subs)


def schemas_doc(base):
    def meta(sid, name):
        return {"resourceType": "Schema", "location": f"{base}/Schemas/{sid}"}
    user = {"id": USER_URN, "name": "User", "description": "User Account", "attributes": [
        _a("userName", required=True, unique="server"),
        _a("externalId", case_exact=True),
        _a("displayName"), _a("nickName"), _a("profileUrl", "reference"), _a("title"), _a("userType"),
        _a("preferredLanguage"), _a("locale"), _a("timezone"),
        _a("active", "boolean"),
        _a("name", "complex", sub=[_a("formatted"), _a("familyName"), _a("givenName"), _a("middleName"),
                                   _a("honorificPrefix"), _a("honorificSuffix")]),
        _multi("emails"), _multi("phoneNumbers"), _multi("ims"), _multi("photos"),
        _multi("addresses", [_a("streetAddress"), _a("locality"), _a("region"), _a("postalCode"),
                             _a("country"), _a("formatted")]),
        _multi("roles"), _multi("entitlements"), _multi("x509Certificates"),
        _a("groups", "complex", True, mutability="readOnly",
           sub=[_a("value", mutability="readOnly"), _a("$ref", "reference", mutability="readOnly"),
                _a("display", mutability="readOnly"), _a("type", mutability="readOnly")]),
    ]}
    group = {"id": GROUP_URN, "name": "Group", "description": "Group", "attributes": [
        _a("displayName", required=True, unique="server"),
        _a("externalId", case_exact=True),
        _a("members", "complex", True, sub=[_a("value", mutability="immutable"),
                                            _a("$ref", "reference", mutability="immutable"),
                                            _a("display", mutability="immutable"),
                                            _a("type", mutability="immutable")]),
    ]}
    ent = {"id": ENT_URN, "name": "EnterpriseUser", "description": "Enterprise User", "attributes": [
        _a("employeeNumber"), _a("costCenter"), _a("organization"), _a("division"), _a("department"),
        _a("manager", "complex", sub=[_a("value"), _a("$ref", "reference"),
                                      _a("displayName", mutability="readOnly")]),
    ]}
    docs = [user, group, ent]
    for d in docs:
        d["schemas"] = ["urn:ietf:params:scim:schemas:core:2.0:Schema"]
        d["meta"] = meta(d["id"], d["name"])
    return docs


def resource_types_doc(base):
    return [
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "User", "name": "User",
         "endpoint": "/Users", "description": "User Account", "schema": USER_URN,
         "schemaExtensions": [{"schema": ENT_URN, "required": False}],
         "meta": {"resourceType": "ResourceType", "location": f"{base}/ResourceTypes/User"}},
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "Group", "name": "Group",
         "endpoint": "/Groups", "description": "Group", "schema": GROUP_URN,
         "meta": {"resourceType": "ResourceType", "location": f"{base}/ResourceTypes/Group"}},
    ]


def service_provider_config(base):
    return {
        "schemas": [SPC_URN],
        "documentationUri": "https://datatracker.ietf.org/doc/html/rfc7644",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 1000},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": True},
        "authenticationSchemes": [{
            "type": "oauthbearertoken", "name": "OAuth Bearer Token",
            "description": "Authentication scheme using the OAuth Bearer Token Standard",
            "specUri": "https://www.rfc-editor.org/info/rfc6750", "primary": True}],
        "meta": {"resourceType": "ServiceProviderConfig", "location": f"{base}/ServiceProviderConfig"},
    }


# --------------------------------------------------------------------------- rendering
class Ctx:
    """Per-request rendering context with lazily loaded lookups."""

    def __init__(self, store, tenant, base):
        self.store, self.tenant, self.base = store, tenant, base
        self._users = self._memb = None

    @property
    def users(self):
        if self._users is None:
            self._users = {r["id"]: r for r in self.store.list_resources(self.tenant, "User")}
        return self._users

    @property
    def memberships(self):
        if self._memb is None:
            self._memb = {}
            for g in self.store.list_resources(self.tenant, "Group"):
                for m in g["body"].get("members", []):
                    self._memb.setdefault(m["value"], []).append(g)
        return self._memb


def render(ctx: Ctx, rtype: str, row: dict) -> dict:
    body = row["body"]
    out = {"schemas": body.get("schemas") or [CORE[rtype]], "id": row["id"]}
    for k, v in body.items():
        if k != "schemas":
            out[k] = copy.deepcopy(v)
    if rtype == "User":
        out["groups"] = [
            {"value": g["id"], "display": g["displayName"], "type": "direct",
             "$ref": f"{ctx.base}/Groups/{g['id']}"} for g in ctx.memberships.get(row["id"], [])]
    else:
        members = []
        for m in body.get("members", []):
            u = ctx.users.get(m["value"])
            members.append({"value": m["value"], "type": "User",
                            "$ref": f"{ctx.base}/Users/{m['value']}",
                            "display": (u["displayName"] or u["userName"]) if u else m.get("display")})
        out["members"] = members
    out["meta"] = {"resourceType": rtype, "created": row["created"], "lastModified": row["lastModified"],
                   "location": f"{ctx.base}/{ENDPOINT[rtype]}/{row['id']}", "version": f'W/"{row["version"]}"'}
    return out


def _project(res: dict, attributes: str | None, excluded: str | None) -> dict:
    if attributes:
        want = {a.strip().lower() for a in attributes.split(",") if a.strip()}
        tops = {w.split(".")[0] for w in want}
        return {k: v for k, v in res.items()
                if k in ("schemas", "id") or k.lower() in tops or k.lower() in want
                or any(w.startswith(k.lower() + ":") for w in want)}
    if excluded:
        out = dict(res)
        for e in (x.strip() for x in excluded.split(",") if x.strip()):
            if e.lower() in ("id", "schemas"):
                continue
            if e.lower().startswith("urn:"):  # a whole extension object
                k = filters.find_key(out, e)
                if k:
                    del out[k]
                continue
            head, _, tail = e.partition(".")
            k = filters.find_key(out, head)
            if k is None:
                continue
            if not tail:
                del out[k]
            elif isinstance(out[k], dict):
                out[k] = {a: b for a, b in out[k].items() if a.lower() != tail.lower()}
        return out
    return res


# --------------------------------------------------------------------------- validation
def _schemas(rtype, data, body):
    core = CORE[rtype]
    out = [core]
    for s in data.get("schemas") or []:
        if isinstance(s, str) and s not in out and (s in body or s.lower() == ENT_URN.lower()):
            out.append(s)
    for k in body:
        if k.startswith("urn:") and k not in out:
            out.append(k)
    return out


def _coerce_bool(v):
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


def _check_unique(store, tenant, rtype, body, exclude_id):
    if rtype == "User":
        un = body["userName"]
        if store.find_conflict(tenant, "User", "user_name", un, exclude_id, True):
            raise ScimError(409, f"userName '{un}' already exists", "uniqueness")
    else:
        dn = body["displayName"]
        if store.find_conflict(tenant, "Group", "display_name", dn, exclude_id, True):
            raise ScimError(409, f"displayName '{dn}' already exists", "uniqueness")
    ext = body.get("externalId")
    if ext and store.find_conflict(tenant, rtype, "external_id", ext, exclude_id, False):
        raise ScimError(409, f"externalId '{ext}' already exists", "uniqueness")


def normalize(store, tenant, rtype, data, exclude_id=None) -> dict:
    if not isinstance(data, dict):
        raise ScimError(400, "Request body must be a JSON object", "invalidSyntax")
    body = {k: copy.deepcopy(v) for k, v in data.items() if k not in ("id", "meta", "groups", "schemas")}
    if rtype == "User":
        un = body.get("userName")
        if not isinstance(un, str) or not un.strip():
            raise ScimError(400, "userName is required", "invalidValue")
        if "active" in body:
            body["active"] = _coerce_bool(body["active"])
            if not isinstance(body["active"], bool):
                raise ScimError(400, "active must be a boolean", "invalidValue")
        else:
            body["active"] = True
        for k in ("emails", "phoneNumbers", "addresses", "roles"):
            if k in body and not (isinstance(body[k], list) and all(isinstance(e, dict) for e in body[k])):
                raise ScimError(400, f"{k} must be an array of objects", "invalidValue")
        if "name" in body and not isinstance(body["name"], dict):
            raise ScimError(400, "name must be an object", "invalidValue")
    else:
        dn = body.get("displayName")
        if not isinstance(dn, str) or not dn.strip():
            raise ScimError(400, "displayName is required", "invalidValue")
        members, seen, missing = [], set(), []
        raw = body.get("members") or []
        if not isinstance(raw, list):
            raise ScimError(400, "members must be an array", "invalidValue")
        for m in raw:
            mid = m.get("value") if isinstance(m, dict) else None
            if not isinstance(mid, str) or not mid:
                raise ScimError(400, "Each member requires a 'value'", "invalidValue")
            if mid in seen:
                continue
            seen.add(mid)
            if not store.get_resource(tenant, "User", mid):
                missing.append(mid)
            members.append({"value": mid, "type": "User"})
        if missing:
            raise ScimError(400, "Member(s) not found: " + ", ".join(missing), "invalidValue")
        body["members"] = members
    body = {"schemas": _schemas(rtype, data, body), **body}
    _check_unique(store, tenant, rtype, body, exclude_id)
    return body


def _active(rtype, body):
    return body.get("active", True) if rtype == "User" else None


# --------------------------------------------------------------------------- operations
def list_resources(store, tenant, rtype, base, flt, start, count, attributes, excluded):
    node = filters.parse(flt) if flt and flt.strip() else None
    start = 1 if start is None or start < 1 else start
    count = 100 if count is None else max(0, min(count, 1000))
    ctx = Ctx(store, tenant, base)
    with store.lock:
        rows = store.list_resources(tenant, rtype)
        res = [render(ctx, rtype, r) for r in rows]
    if node:
        res = [r for r in res if filters.match(node, r)]
    page = res[start - 1:start - 1 + count]
    return {"schemas": [LIST_URN], "totalResults": len(res), "startIndex": start, "itemsPerPage": len(page),
            "Resources": [_project(r, attributes, excluded) for r in page]}


def get_one(store, tenant, rtype, rid, base, attributes=None, excluded=None):
    ctx = Ctx(store, tenant, base)
    with store.lock:
        row = store.get_resource(tenant, rtype, rid)
        if not row:
            raise ScimError(404, f"{rtype} {rid} not found")
        return _project(render(ctx, rtype, row), attributes, excluded), row["version"]


def create(store, tenant, rtype, base, data):
    with store.tx():
        body = normalize(store, tenant, rtype, data)
        rid = str(uuid.uuid4())
        store.insert_resource(tenant, rtype, rid, body, _active(rtype, body))
        return get_one(store, tenant, rtype, rid, base)


def _must(store, tenant, rtype, rid):
    row = store.get_resource(tenant, rtype, rid)
    if not row:
        raise ScimError(404, f"{rtype} {rid} not found")
    return row


def replace(store, tenant, rtype, rid, base, data):
    with store.tx():
        row = _must(store, tenant, rtype, rid)
        body = normalize(store, tenant, rtype, data, exclude_id=rid)
        store.update_resource(tenant, rtype, rid, body, row["version"] + 1, _active(rtype, body))
        return get_one(store, tenant, rtype, rid, base)


def patch(store, tenant, rtype, rid, base, request):
    with store.tx():
        row = _must(store, tenant, rtype, rid)
        patched = apply_patch(row["body"], request, CORE[rtype])
        body = normalize(store, tenant, rtype, patched, exclude_id=rid)
        store.update_resource(tenant, rtype, rid, body, row["version"] + 1, _active(rtype, body))
        return get_one(store, tenant, rtype, rid, base)


def delete(store, tenant, rtype, rid):
    with store.tx():
        _must(store, tenant, rtype, rid)
        store.delete_resource(tenant, rtype, rid)
        if rtype == "User":
            for g in store.list_resources(tenant, "Group"):
                members = g["body"].get("members", [])
                kept = [m for m in members if m["value"] != rid]
                if len(kept) != len(members):
                    store.update_resource(tenant, "Group", g["id"], {**g["body"], "members": kept},
                                          g["version"] + 1)


def snapshot(store, tenant):
    """Compact view used by the admin summary endpoint."""
    with store.lock:
        users = store.list_resources(tenant, "User")
        groups = store.list_resources(tenant, "Group")
    names = {u["id"]: u["userName"] for u in users}
    return {
        "users": [{"id": u["id"], "userName": u["userName"], "externalId": u["externalId"],
                   "active": u["active"], "displayName": u["displayName"]} for u in users],
        "groups": [{"id": g["id"], "displayName": g["displayName"], "externalId": g["externalId"],
                    "members": [names.get(m["value"], m["value"]) for m in g["body"].get("members", [])]}
                   for g in groups],
    }
