"""HTTP-level behaviour: routing, middleware, validation, admin API and the store."""
import json

import pytest
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from app import scim, store as store_mod
from app.main import create_app
from conftest import AUTH, TOKEN

CORE_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
ENT = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"


# ---------------------------------------------------------------- discovery
def test_service_provider_config_uses_request_base(api, alias):
    a = api.get("/ServiceProviderConfig").json()
    assert a["meta"]["location"] == "http://testserver/t/acme/scim/v2/ServiceProviderConfig"
    b = alias.get("/ServiceProviderConfig").json()
    assert b["meta"]["location"] == "http://testserver/scim/v2/enterprises/acme/ServiceProviderConfig"
    assert a["filter"] == {"supported": True, "maxResults": 1000} and a["bulk"]["supported"] is False


def test_resource_types_list_and_by_id(api):
    lst = api.get("/ResourceTypes").json()
    assert [d["id"] for d in lst["Resources"]] == ["User", "Group"]
    assert lst["itemsPerPage"] == 2 and lst["startIndex"] == 1
    user = api.get("/ResourceTypes/User").json()
    assert user["endpoint"] == "/Users" and user["schemaExtensions"][0]["schema"] == ENT
    assert user["meta"]["location"].endswith("/t/acme/scim/v2/ResourceTypes/User")
    group = api.get("/ResourceTypes/group").json()  # case-insensitive
    assert group["name"] == "Group" and group["schema"].endswith("core:2.0:Group")
    r = api.get("/ResourceTypes/Nope")
    assert r.status_code == 404 and r.json()["status"] == "404" and "Nope" in r.json()["detail"]


def test_schemas_list_and_by_id(api):
    lst = api.get("/Schemas").json()
    assert lst["totalResults"] == 3 and {d["name"] for d in lst["Resources"]} == {"User", "Group", "EnterpriseUser"}
    for urn in (CORE_USER, ENT, "urn:ietf:params:scim:schemas:core:2.0:Group"):
        d = api.get(f"/Schemas/{urn}").json()  # ids contain colons
        assert d["id"] == urn and d["attributes"]
        assert d["meta"]["location"] == f"http://testserver/t/acme/scim/v2/Schemas/{urn}"
    assert api.get(f"/Schemas/{CORE_USER.lower()}").status_code == 200
    r = api.get("/Schemas/urn:nope")
    assert r.status_code == 404 and "urn:nope" in r.json()["detail"]
    user_attrs = {a["name"] for a in api.get(f"/Schemas/{CORE_USER}").json()["attributes"]}
    assert {"userName", "emails", "groups", "name"} <= user_attrs


# ---------------------------------------------------------------- routing and error mapping
def test_unknown_scim_path_returns_scim_404(api):
    r = api.get("/Nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/scim+json")
    assert r.json()["status"] == "404" and r.json()["schemas"][0].endswith("Error")


def test_method_not_allowed_is_scim_error(api):
    r = api.delete("/Users")
    assert r.status_code == 405 and r.json()["status"] == "405"


def test_non_scim_404s_are_plain_json(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    r = client.get("/ui/missing.html")
    assert r.status_code == 404 and "detail" in r.json()


def test_query_validation_errors(api, client):
    r = api.get("/Users?startIndex=abc")
    assert r.status_code == 400 and r.json()["scimType"] == "invalidValue"
    assert "startIndex" in r.json()["detail"] and r.json()["detail"].startswith("Invalid request")
    r = client.get("/api/tenants/acme/requests?limit=0")
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "limit"
    assert client.get("/api/tenants/acme/requests?limit=99999").status_code == 422


def test_invalid_json_bodies(api):
    r = api.req("POST", "/Users", content=b"{not json", headers={**AUTH, "Content-Type": "application/scim+json"})
    assert r.status_code == 400 and r.json()["scimType"] == "invalidSyntax" and "not valid JSON" in r.json()["detail"]
    r = api.req("PATCH", "/Users/x", content=b"\xff\xfe", headers=AUTH)
    assert r.status_code == 400 and r.json()["scimType"] == "invalidSyntax"
    r = api.req("POST", "/Users", content=b"", headers=AUTH)  # empty body -> null
    assert r.status_code == 400 and "JSON object" in r.json()["detail"]
    for body in ([], "str", 5):
        r = api.post("/Users", body)
        assert r.status_code == 400 and r.json()["scimType"] == "invalidSyntax"
    assert api.post("/Groups", [1]).status_code == 400


# ---------------------------------------------------------------- auth and tenant slug middleware
def test_auth_header_variants(client):
    url = "/t/acme/scim/v2/Users"
    assert client.get(url, headers={"Authorization": "bearer " + TOKEN}).status_code == 200  # scheme is case-insensitive
    assert client.get(url, headers={"Authorization": f"Bearer   {TOKEN}  "}).status_code == 200
    assert client.get(url, headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get(url, headers={"Authorization": TOKEN}).status_code == 401
    r = client.get(url, headers={"Authorization": "Bearer "})
    assert r.status_code == 401 and r.headers["www-authenticate"] == 'Bearer realm="scim"'
    assert r.json()["detail"] == "Missing or invalid bearer token"


def test_unauthorised_calls_do_not_create_tenants(client):
    client.get("/t/ghost/scim/v2/Users")
    assert client.get("/api/tenants").json() == []
    assert client.get("/api/tenants/ghost/requests").json()[0]["authOk"] is False  # but they are logged


def test_invalid_tenant_slug(client):
    r = client.get("/t/bad slug/scim/v2/Users", headers=AUTH)
    assert r.status_code == 400 and r.json()["scimType"] == "invalidValue" and "slug" in r.json()["detail"]
    r = client.get("/t/" + "a" * 65 + "/scim/v2/Users", headers=AUTH)
    assert r.status_code == 400
    assert client.get("/api/tenants").json() == []
    assert client.get("/t/ok.name_1-x/scim/v2/Users", headers=AUTH).status_code == 200


def test_alias_and_generic_log_under_same_tenant(api, alias, client):
    alias.user("viaalias")
    api.get("/Users")
    rows = client.get("/api/tenants/acme/requests").json()
    assert rows[0]["path"] == "/t/acme/scim/v2/Users"
    assert rows[1]["path"] == "/scim/v2/enterprises/acme/Users" and rows[1]["resourceType"] == "Users"


# ---------------------------------------------------------------- middleware: errors, logging and faults
def _app_with_routes(tmp_path, *routes):
    app = create_app(str(tmp_path / "d"), TOKEN)
    for r in reversed(routes):
        app.router.routes.insert(0, r)
    return app


def test_unhandled_exception_becomes_scim_500(api, client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaput")
    monkeypatch.setattr(scim, "list_resources", boom)
    r = api.get("/Users")
    assert r.status_code == 500 and r.json()["detail"] == "Internal error: kaput"
    assert r.headers["content-type"].startswith("application/scim+json")
    row = client.get("/api/tenants/acme/requests").json()[0]
    assert row["status"] == 500 and row["error"] == "Internal error: kaput"


def test_log_handles_non_json_bodies(tmp_path):
    async def plain_groups(request):
        return PlainTextResponse("plain text, not json")

    async def teapot(request):
        return PlainTextResponse("x" * 500, status_code=418)

    async def list_error(request):
        return JSONResponse([1, 2, 3], status_code=500)

    app = _app_with_routes(
        tmp_path,
        Route("/t/{tenant}/scim/v2/Groups", plain_groups),
        Route("/t/{tenant}/scim/v2/Teapot", teapot),
        Route("/t/{tenant}/scim/v2/Broken", list_error))
    with TestClient(app) as c:
        h = AUTH
        assert c.get("/t/acme/scim/v2/Groups", headers=h).text == "plain text, not json"
        assert c.get("/t/acme/scim/v2/Teapot", headers=h).status_code == 418
        assert c.get("/t/acme/scim/v2/Broken", headers=h).status_code == 500
        rows = {r["resourceType"]: r for r in c.get("/api/tenants/acme/requests").json()}
        assert rows["Groups"]["resourceId"] is None and rows["Groups"]["status"] == 200  # id not parseable
        assert rows["Teapot"]["error"] == "x" * 200  # non-JSON error text is cut at 200 chars
        assert rows["Broken"]["error"] == "[1,2,3]"  # JSON list body: falls back to raw text


def test_log_records_ids_for_collection_and_item_requests(api, client):
    u = api.user("idlog")
    api.get("/Users")  # collection GET: no id
    api.get(f"/Users/{u['id']}")
    g = api.group("GL")
    rows = client.get("/api/tenants/acme/requests").json()
    assert rows[0]["resourceType"] == "Groups" and rows[0]["resourceId"] == g["id"]  # id lifted from the 201 body
    assert rows[1]["resourceId"] == u["id"]
    assert rows[2]["resourceType"] == "Users" and rows[2]["resourceId"] is None
    api.get("/ServiceProviderConfig")
    assert client.get("/api/tenants/acme/requests?limit=1").json()[0]["resourceType"] == "ServiceProviderConfig"


def test_request_body_truncation(api, client):
    api.user("trunc", title="y" * 20000)
    row = client.get("/api/tenants/acme/requests").json()[0]
    assert row["requestBody"].startswith("{") and "... [truncated, " in row["requestBody"]
    assert len(row["requestBody"]) < 16384 + 60
    assert "... [truncated, " in row["responseBody"] and len(row["responseBody"]) < 4096 + 60
    # small bodies are stored verbatim and GET requests have no request body
    api.get("/Users?count=0")
    row = client.get("/api/tenants/acme/requests").json()[0]
    assert row["requestBody"] is None and "truncated" not in row["responseBody"]


def test_request_body_is_valid_utf8_with_replacement(api, client):
    api.req("POST", "/Users", content=b'{"userName": "\xff\xfe"}', headers=AUTH)
    row = client.get("/api/tenants/acme/requests").json()[0]
    assert row["status"] == 400 and "�" in row["requestBody"]


def test_log_is_pruned_to_cap(api, client, monkeypatch):
    monkeypatch.setattr(store_mod, "LOG_CAP", 5)
    for _ in range(9):
        api.get("/Users")
    rows = client.get("/api/tenants/acme/requests?limit=100").json()
    assert len(rows) == 5
    assert rows[0]["id"] - rows[-1]["id"] == 4  # newest five survive


def test_fault_precedence_latency_then_429_then_500_then_503(api, client):
    put = lambda **f: client.put("/api/tenants/acme/faults", json=f)  # noqa: E731
    put(rateLimitEvery=1, errorRatePercent=100, readOnly=True)
    assert api.post("/Users", {"userName": "x"}).status_code == 429  # rate limit wins
    put(rateLimitEvery=0)
    assert api.post("/Users", {"userName": "x"}).status_code == 500  # then error rate
    put(errorRatePercent=0)
    assert api.post("/Users", {"userName": "x"}).status_code == 503  # then read-only
    assert api.get("/Users").status_code == 200  # reads still fine


def test_error_rate_is_probabilistic(api, client, monkeypatch):
    import random
    client.put("/api/tenants/acme/faults", json={"errorRatePercent": 30})
    monkeypatch.setattr(random, "random", lambda: 0.29)
    assert api.get("/Users").status_code == 500
    monkeypatch.setattr(random, "random", lambda: 0.31)
    assert api.get("/Users").status_code == 200


def test_readonly_blocks_every_write_method_but_not_reads(api, client):
    u = api.user("keep")
    client.put("/api/tenants/acme/faults", json={"readOnly": True})
    assert api.post("/Users", {"userName": "n"}).status_code == 503
    assert api.put(f"/Users/{u['id']}", {"userName": "keep"}).status_code == 503
    assert api.patch(f"/Users/{u['id']}", [{"op": "replace", "path": "title", "value": "t"}]).status_code == 503
    assert api.delete(f"/Users/{u['id']}").status_code == 503
    assert api.get(f"/Users/{u['id']}").status_code == 200
    assert api.get("/Users/" + u["id"]).json()["userName"] == "keep"


def test_injected_responses_are_logged_with_error_text(api, client):
    client.put("/api/tenants/acme/faults", json={"rateLimitEvery": 1})
    api.get("/Users")
    row = client.get("/api/tenants/acme/requests").json()[0]
    assert row["status"] == 429 and row["error"] == "Too many requests (injected)" and row["authOk"] is True


# ---------------------------------------------------------------- resource validation
def test_pagination_edges(api):
    for i in range(3):
        api.user(f"p{i}")
    assert api.get("/Users?startIndex=0").json()["startIndex"] == 1
    assert api.get("/Users?startIndex=-5").json()["startIndex"] == 1
    assert api.get("/Users?count=-1").json()["Resources"] == []
    r = api.get("/Users?count=5000").json()
    assert r["itemsPerPage"] == 3 and r["totalResults"] == 3
    r = api.get("/Users?startIndex=10").json()
    assert r["Resources"] == [] and r["totalResults"] == 3 and r["itemsPerPage"] == 0


def test_attributes_and_excluded_attributes(api):
    u = api.user("proj", title="T", name={"givenName": "G", "familyName": "F"},
                 emails=[{"value": "a@x.io"}], **{ENT: {"department": "Eng"}})
    got = api.get(f"/Users/{u['id']}?attributes=title").json()
    assert set(got) == {"schemas", "id", "title"}
    got = api.get(f"/Users/{u['id']}?attributes=NAME.givenName,userName").json()
    assert set(got) == {"schemas", "id", "name", "userName"}
    got = api.get(f"/Users/{u['id']}?attributes={ENT}:department").json()
    assert set(got) == {"schemas", "id", ENT}
    got = api.get(f"/Users/{u['id']}?attributes=,title,").json()  # empty fragments ignored
    assert set(got) == {"schemas", "id", "title"}

    full = api.get(f"/Users/{u['id']}").json()
    got = api.get(f"/Users/{u['id']}?excludedAttributes=title,emails").json()
    assert "title" not in got and "emails" not in got and got["userName"] == "proj"
    got = api.get(f"/Users/{u['id']}?excludedAttributes=name.givenName").json()
    assert got["name"] == {"familyName": "F"}
    got = api.get(f"/Users/{u['id']}?excludedAttributes=id,schemas").json()
    assert got["id"] == u["id"] and "schemas" in got  # never excludable
    got = api.get(f"/Users/{u['id']}?excludedAttributes=doesNotExist,title.sub").json()
    assert got == full  # unknown names and sub-attributes of scalars are ignored
    got = api.get(f"/Users/{u['id']}?excludedAttributes={ENT}").json()
    assert ENT not in got and got["title"] == "T"
    got = api.get(f"/Users/{u['id']}?excludedAttributes=urn:nope:2.0:X").json()
    assert got == full
    got = api.get(f"/Users/{u['id']}?excludedAttributes=,").json()
    assert got == full
    # attributes wins when both are given
    got = api.get(f"/Users/{u['id']}?attributes=title&excludedAttributes=title").json()
    assert got["title"] == "T"
    lst = api.get("/Users?attributes=userName").json()["Resources"][0]
    assert set(lst) == {"schemas", "id", "userName"}


def test_schemas_array_handling(api):
    u = api.post("/Users", {"userName": "s1", "schemas": [CORE_USER, ENT, "urn:unknown:1.0:X"]}).json()
    assert u["schemas"] == [CORE_USER, ENT]  # declared extension kept, unknown dropped
    u = api.post("/Users", {"userName": "s2", "schemas": ["not-a-urn", 5], ENT: {"department": "D"}}).json()
    assert u["schemas"] == [CORE_USER, ENT]
    u = api.post("/Users", {"userName": "s3", "urn:custom:1.0:X": {"a": 1}}).json()
    assert u["schemas"] == [CORE_USER, "urn:custom:1.0:X"] and u["urn:custom:1.0:X"] == {"a": 1}
    assert api.post("/Users", {"userName": "s4"}).json()["schemas"] == [CORE_USER]


def test_client_cannot_set_server_managed_fields(api):
    u = api.post("/Users", {"userName": "srv", "id": "mine", "meta": {"x": 1}, "groups": [{"value": "g"}]}).json()
    assert u["id"] != "mine" and u["meta"]["resourceType"] == "User" and u["groups"] == []


def test_user_validation_errors(api):
    cases = [
        ({"userName": "  "}, "userName is required"),
        ({"userName": 5}, "userName is required"),
        ({"userName": "a", "active": "maybe"}, "active must be a boolean"),
        ({"userName": "a", "active": 1}, "active must be a boolean"),
        ({"userName": "a", "emails": "x@y"}, "emails must be an array of objects"),
        ({"userName": "a", "emails": ["x@y"]}, "emails must be an array of objects"),
        ({"userName": "a", "phoneNumbers": {"value": "1"}}, "phoneNumbers must be an array of objects"),
        ({"userName": "a", "name": "Alice"}, "name must be an object"),
    ]
    for body, msg in cases:
        r = api.post("/Users", body)
        assert r.status_code == 400 and r.json()["scimType"] == "invalidValue", body
        assert msg in r.json()["detail"], body
    assert api.get("/Users").json()["totalResults"] == 0


def test_active_string_coercion(api):
    assert api.post("/Users", {"userName": "t", "active": "TRUE"}).json()["active"] is True
    assert api.post("/Users", {"userName": "f", "active": "false"}).json()["active"] is False
    assert api.post("/Users", {"userName": "d"}).json()["active"] is True  # defaults to true


def test_group_validation_errors(api):
    u = api.user("m")
    cases = [
        ({"displayName": ""}, "displayName is required"),
        ({"displayName": 7}, "displayName is required"),
        ({"displayName": "G", "members": "m"}, "members must be an array"),
        ({"displayName": "G", "members": {"value": u["id"]}}, "members must be an array"),
        ({"displayName": "G", "members": ["str"]}, "Each member requires a 'value'"),
        ({"displayName": "G", "members": [{"display": "x"}]}, "Each member requires a 'value'"),
        ({"displayName": "G", "members": [{"value": ""}]}, "Each member requires a 'value'"),
        ({"displayName": "G", "members": [{"value": 5}]}, "Each member requires a 'value'"),
        ({"displayName": "G", "members": [{"value": u["id"]}, {"value": "x1"}, {"value": "x2"}]}, "x1, x2"),
    ]
    for body, msg in cases:
        r = api.post("/Groups", body)
        assert r.status_code == 400 and r.json()["scimType"] == "invalidValue", body
        assert msg in r.json()["detail"], body


def test_group_members_dedupe_and_null(api):
    u = api.user("dm")
    g = api.post("/Groups", {"displayName": "D", "members": [{"value": u["id"]}, {"value": u["id"]}]}).json()
    assert len(g["members"]) == 1
    assert api.post("/Groups", {"displayName": "N", "members": None}).json()["members"] == []
    assert api.post("/Groups", {"displayName": "M"}).json()["members"] == []


def test_group_members_render_display_and_refs(api):
    named = api.user("nobody")
    shown = api.user("shown", displayName="Shown Person")
    g = api.group("R", [named["id"], shown["id"]])
    assert [m["display"] for m in g["members"]] == ["nobody", "Shown Person"]
    assert all(m["type"] == "User" and m["$ref"].endswith(f"/Users/{m['value']}") for m in g["members"])


def test_put_group_and_conflicts(api):
    u = api.user("gm")
    a = api.group("A", [u["id"]], externalId="ea")
    b = api.group("B", externalId="eb")
    r = api.put(f"/Groups/{a['id']}", {"displayName": "A", "externalId": "ea", "members": []})
    assert r.status_code == 200 and r.json()["members"] == []  # keeping own name/externalId is not a conflict
    assert api.put(f"/Groups/{b['id']}", {"displayName": "a"}).status_code == 409
    r = api.put(f"/Groups/{b['id']}", {"displayName": "B", "externalId": "ea"})
    assert r.status_code == 409 and "externalId" in r.json()["detail"]
    assert api.put("/Groups/ghost", {"displayName": "G"}).status_code == 404
    assert api.patch("/Groups/ghost", [{"op": "add", "path": "externalId", "value": "z"}]).status_code == 404
    assert api.delete("/Groups/ghost").status_code == 404


def test_patch_conflict_rolls_back(api):
    api.user("one")
    two = api.user("two")
    r = api.patch(f"/Users/{two['id']}", [{"op": "replace", "path": "userName", "value": "ONE"}])
    assert r.status_code == 409 and r.json()["scimType"] == "uniqueness"
    assert api.get(f"/Users/{two['id']}").json()["userName"] == "two"


def test_user_group_membership_is_computed_and_read_only(api):
    u = api.user("mem")
    g1, g2 = api.group("G1", [u["id"]]), api.group("G2", [u["id"]])
    groups = api.get(f"/Users/{u['id']}").json()["groups"]
    assert {x["display"] for x in groups} == {"G1", "G2"} and all(x["type"] == "direct" for x in groups)
    # PUT cannot rewrite computed groups
    r = api.put(f"/Users/{u['id']}", {"userName": "mem", "groups": []}).json()
    assert len(r["groups"]) == 2
    api.delete(f"/Groups/{g1['id']}")
    assert [x["display"] for x in api.get(f"/Users/{u['id']}").json()["groups"]] == ["G2"]


def test_delete_user_leaves_unrelated_groups_untouched(api):
    a, b = api.user("da"), api.user("db")
    mine = api.group("Mine", [a["id"]])
    other = api.group("Other", [b["id"]])
    empty = api.group("Empty")
    api.delete(f"/Users/{a['id']}")
    assert api.get(f"/Groups/{mine['id']}").json()["members"] == []
    assert api.get(f"/Groups/{mine['id']}").json()["meta"]["version"] == 'W/"2"'
    assert api.get(f"/Groups/{other['id']}").json()["meta"]["version"] == 'W/"1"'  # not rewritten
    assert api.get(f"/Groups/{empty['id']}").json()["meta"]["version"] == 'W/"1"'


def test_etag_and_location_headers(api):
    r = api.post("/Users", {"userName": "hdr"})
    uid = r.json()["id"]
    assert r.headers["etag"] == 'W/"1"' and r.headers["location"].endswith(f"/Users/{uid}")
    assert api.get(f"/Users/{uid}").headers["etag"] == 'W/"1"'
    assert api.patch(f"/Users/{uid}", [{"op": "add", "path": "title", "value": "t"}]).headers["etag"] == 'W/"2"'
    assert api.put(f"/Users/{uid}", {"userName": "hdr"}).headers["etag"] == 'W/"3"'
    assert api.delete(f"/Users/{uid}").content == b""


# ---------------------------------------------------------------- admin API
def test_admin_config_and_health(client):
    c = client.get("/api/config").json()
    assert c == {"token": TOKEN, "genericPath": "/t/{tenant}/scim/v2", "aliasPath": "/scim/v2/enterprises/{tenant}"}
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/openapi.json").status_code == 200


def test_admin_requires_no_scim_token(client):
    assert client.get("/api/tenants").status_code == 200


def test_admin_faults_validation_and_clamping(client):
    url = "/api/tenants/acme/faults"
    assert client.get(url).json() == {"latencyMs": 0, "errorRatePercent": 0, "rateLimitEvery": 0, "readOnly": False}
    assert client.put(url, json={}).json()["latencyMs"] == 0
    f = client.put(url, json={"latencyMs": 999999, "errorRatePercent": 250, "rateLimitEvery": 7}).json()
    assert f == {"latencyMs": 60000, "errorRatePercent": 100.0, "rateLimitEvery": 7, "readOnly": False}
    f = client.put(url, json={"latencyMs": -5, "errorRatePercent": -1, "rateLimitEvery": -3}).json()
    assert f["latencyMs"] == 0 and f["errorRatePercent"] == 0 and f["rateLimitEvery"] == 0
    f = client.put(url, json={"latencyMs": "250", "errorRatePercent": "12.5", "readOnly": 1}).json()
    assert f["latencyMs"] == 250 and f["errorRatePercent"] == 12.5 and f["readOnly"] is True
    # partial updates keep other fields; unknown keys are ignored
    f = client.put(url, json={"latencyMs": 10, "bogus": 1}).json()
    assert f["readOnly"] is True and f["errorRatePercent"] == 12.5 and "bogus" not in f
    for bad in ({"latencyMs": "fast"}, {"errorRatePercent": None}, {"rateLimitEvery": [1]}, {"latencyMs": {}}):
        r = client.put(url, json=bad)
        assert r.status_code == 400 and "numeric" in r.json()["detail"], bad
    assert client.put(url, json=["not", "an", "object"]).status_code == 422
    assert client.get(url).json()["latencyMs"] == 10  # rejected requests changed nothing


def test_admin_faults_rejects_invalid_slug_and_creates_tenant(client):
    r = client.put("/api/tenants/bad slug/faults", json={"latencyMs": 1})
    assert r.status_code == 400 and r.json() == {"detail": "Invalid tenant slug"}
    assert client.put("/api/tenants/newone/faults", json={"readOnly": True}).status_code == 200
    assert [t["tenant"] for t in client.get("/api/tenants").json()] == ["newone"]
    assert client.get("/api/tenants/unknown/faults").json()["latencyMs"] == 0  # defaults, tenant not created
    assert [t["tenant"] for t in client.get("/api/tenants").json()] == ["newone"]


def test_saving_faults_resets_request_counter(api, client):
    client.put("/api/tenants/acme/faults", json={"rateLimitEvery": 3})
    assert [api.get("/Users").status_code for _ in range(2)] == [200, 200]
    client.put("/api/tenants/acme/faults", json={"rateLimitEvery": 3})  # counter restarts
    assert [api.get("/Users").status_code for _ in range(3)] == [200, 200, 429]


def test_admin_summary_for_unknown_tenant(client):
    s = client.get("/api/tenants/nobody/summary").json()
    assert s["exists"] is False and s["counts"]["users"] == 0 and s["lastActivity"] is None
    assert s["users"] == [] and s["groups"] == [] and s["requestsByMethod"] == {} and s["faults"]["readOnly"] is False


def test_admin_summary_member_names_and_status_counts(api, client):
    a = api.user("sa")
    api.group("SG", [a["id"]])
    api.get("/Users/missing")
    s = client.get("/api/tenants/acme/summary").json()
    assert s["exists"] is True and s["requestsByStatus"] == {"201": 2, "404": 1}
    assert s["groups"][0]["members"] == ["sa"]
    assert s["lastActivity"]


def test_admin_request_filters(api, client):
    u = api.user("flt")
    api.get(f"/Users/{u['id']}")
    api.get("/Users/missing")
    api.get("/Groups/missing")
    api.delete(f"/Users/{u['id']}")
    q = lambda s: client.get(f"/api/tenants/acme/requests?{s}").json()  # noqa: E731
    assert len(q("")) == 5
    assert {r["status"] for r in q("status=404")} == {404} and len(q("status=404")) == 2
    assert len(q("status=2xx")) == 3 and len(q("status=4XX")) == 2 and q("status=5xx") == []
    assert len(q("status=garbage")) == 5  # unrecognised status filters are ignored
    assert q("status=20") == []  # a bare number is an exact status match
    assert [r["method"] for r in q("method=delete")] == ["DELETE"]
    assert len(q("resource=Groups")) == 1 and len(q("resource=groups")) == 1
    assert len(q(f"resource={u['id']}")) == 3
    assert len(q("method=GET&status=4xx&resource=Users")) == 1
    assert len(q("limit=2")) == 2
    assert q("limit=2")[0]["method"] == "DELETE"  # newest first


def test_admin_clear_requests_is_per_tenant(client):
    for t in ("acme", "beta"):
        client.get(f"/t/{t}/scim/v2/Users", headers=AUTH)
    assert client.delete("/api/tenants/acme/requests").status_code == 204
    assert client.get("/api/tenants/acme/requests").json() == []
    assert len(client.get("/api/tenants/beta/requests").json()) == 1


def test_admin_delete_tenant_removes_everything_but_only_that_tenant(client):
    for t in ("acme", "beta"):
        base = f"/t/{t}/scim/v2"
        uid = client.post(base + "/Users", json={"userName": "u"}, headers=AUTH).json()["id"]
        client.post(base + "/Groups", json={"displayName": "g", "members": [{"value": uid}]}, headers=AUTH)
        client.put(f"/api/tenants/{t}/faults", json={"latencyMs": 1})
    assert client.delete("/api/tenants/acme").status_code == 204
    assert client.delete("/api/tenants/never-existed").status_code == 204  # idempotent
    assert [t["tenant"] for t in client.get("/api/tenants").json()] == ["beta"]
    assert client.get("/api/tenants/acme/users").json() == [] and client.get("/api/tenants/acme/groups").json() == []
    assert client.get("/api/tenants/acme/requests").json() == []
    assert client.get("/api/tenants/acme/faults").json()["latencyMs"] == 0
    assert len(client.get("/api/tenants/beta/users").json()) == 1
    assert client.get("/api/tenants/beta/faults").json()["latencyMs"] == 1
    # the same names can be provisioned again afterwards
    r = client.post("/t/acme/scim/v2/Users", json={"userName": "u"}, headers=AUTH)
    assert r.status_code == 201


def test_admin_tenants_listing_counts(api, client):
    api.user("a1")
    api.user("a2", active=False)
    api.group("g")
    t = client.get("/api/tenants").json()[0]
    assert (t["users"], t["usersActive"], t["usersInactive"], t["groups"]) == (2, 1, 1, 1)
    assert t["requests"] == 3 and t["lastActivity"]


def test_tenants_are_isolated(client):
    a = client.post("/t/a/scim/v2/Users", json={"userName": "same"}, headers=AUTH)
    b = client.post("/t/b/scim/v2/Users", json={"userName": "same"}, headers=AUTH)
    assert a.status_code == b.status_code == 201 and a.json()["id"] != b.json()["id"]
    assert client.get(f"/t/b/scim/v2/Users/{a.json()['id']}", headers=AUTH).status_code == 404


def test_root_redirects_to_ui_and_ui_is_served(client):
    assert client.get("/", follow_redirects=False).status_code in (302, 307)
    page = client.get("/ui/")
    assert page.status_code == 200 and "MockSCIMTarget" in page.text
    assert client.get("/ui/app.js").status_code == 200 and client.get("/ui/app.css").status_code == 200


def test_app_reads_token_and_data_dir_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCIM_TOKEN", "from-env")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "envdata"))
    with TestClient(create_app()) as c:
        assert c.get("/t/x/scim/v2/Users", headers={"Authorization": "Bearer from-env"}).status_code == 200
        assert c.get("/t/x/scim/v2/Users", headers=AUTH).status_code == 401
    assert (tmp_path / "envdata" / "scim-target.db").exists()


def test_data_persists_across_app_restarts(tmp_path):
    d = str(tmp_path / "persist")
    with TestClient(create_app(d, TOKEN)) as c:
        c.post("/t/acme/scim/v2/Users", json={"userName": "durable"}, headers=AUTH)
        c.put("/api/tenants/acme/faults", json={"latencyMs": 5})
    with TestClient(create_app(d, TOKEN)) as c:
        assert c.get("/api/tenants/acme/users").json()[0]["userName"] == "durable"
        assert c.get("/api/tenants/acme/faults").json()["latencyMs"] == 5
        assert len(c.get("/api/tenants/acme/requests").json()) == 1


# ---------------------------------------------------------------- store (direct)
@pytest.fixture()
def store(tmp_path):
    return store_mod.Store(str(tmp_path / "s"))


def test_store_nested_transactions_commit_together(store):
    with store.tx():
        store.insert_resource("t", "User", "1", {"userName": "a"}, True)
        with store.tx():
            store.insert_resource("t", "User", "2", {"userName": "b"}, True)
    assert {r["id"] for r in store.list_resources("t", "User")} == {"1", "2"}


def test_store_outer_rollback_discards_inner_work(store):
    with pytest.raises(RuntimeError):
        with store.tx():
            store.insert_resource("t", "User", "1", {"userName": "a"}, True)
            with store.tx():
                store.insert_resource("t", "User", "2", {"userName": "b"}, True)
            raise RuntimeError("fail")
    assert store.list_resources("t", "User") == []
    assert store._depth == 0
    with store.tx():  # store is still usable
        store.insert_resource("t", "User", "3", {"userName": "c"}, True)
    assert len(store.list_resources("t", "User")) == 1


def test_store_inner_failure_propagates_and_rolls_back_all(store):
    with pytest.raises(ValueError):
        with store.tx():
            store.insert_resource("t", "User", "1", {"userName": "a"}, True)
            try:
                with store.tx():
                    raise ValueError("inner")
            except ValueError:
                raise
    assert store.list_resources("t", "User") == []


def test_store_inner_failure_can_be_swallowed_by_outer(store):
    with store.tx():
        store.insert_resource("t", "User", "1", {"userName": "a"}, True)
        try:
            with store.tx():
                raise ValueError("inner")
        except ValueError:
            pass
    assert len(store.list_resources("t", "User")) == 1  # outer still commits; depth stayed consistent
    assert store._depth == 0


def test_store_tenant_lifecycle_and_faults(store):
    assert store.tenant_slugs() == [] and not store.tenant_exists("t")
    assert store.get_faults("t") == store_mod.DEFAULT_FAULTS and not store.tenant_exists("t")
    assert store.ensure_tenant("t") == store_mod.DEFAULT_FAULTS
    assert store.ensure_tenant("t") == store_mod.DEFAULT_FAULTS and store.tenant_exists("t")
    merged = store.set_faults("t", {"latencyMs": 5})
    assert merged == {**store_mod.DEFAULT_FAULTS, "latencyMs": 5}
    assert store.ensure_tenant("t")["latencyMs"] == 5 and store.get_faults("t")["latencyMs"] == 5
    store.set_faults("fresh", {"readOnly": True})  # creates the tenant
    assert store.tenant_slugs() == ["fresh", "t"]
    store.x("UPDATE tenants SET faults='' WHERE slug='t'")  # blank column falls back to defaults
    assert store.get_faults("t") == store_mod.DEFAULT_FAULTS and store.ensure_tenant("t") == store_mod.DEFAULT_FAULTS
    store.delete_tenant("t")
    assert store.tenant_slugs() == ["fresh"]


def test_store_counts_and_last_activity(store):
    assert store.counts("t") == {"users": 0, "usersActive": 0, "usersInactive": 0, "groups": 0, "requests": 0}
    assert store.last_activity("t") is None
    store.insert_resource("t", "User", "1", {"userName": "a"}, True)
    store.insert_resource("t", "User", "2", {"userName": "b"}, False)
    store.insert_resource("t", "Group", "3", {"displayName": "g"})
    assert store.counts("t") == {"users": 2, "usersActive": 1, "usersInactive": 1, "groups": 1, "requests": 0}
    assert store.get_resource("t", "Group", "3")["active"] is None
    assert store.last_activity("t") == max(store.get_resource("t", "User", i)["lastModified"] for i in "12")
    store.log_request({"ts": "2999-01-01T00:00:00.000Z", "tenant": "t", "method": "GET", "path": "/x",
                       "status": 200, "durationMs": 1.0, "authOk": True})
    assert store.last_activity("t") == "2999-01-01T00:00:00.000Z"  # newest of log and resources


def test_store_find_conflict(store):
    store.insert_resource("t", "User", "1", {"userName": "Alice", "externalId": "Ex"}, True)
    assert store.find_conflict("t", "User", "user_name", "ALICE", None, True) == "1"
    assert store.find_conflict("t", "User", "user_name", "ALICE", "1", True) is None  # excluding self
    assert store.find_conflict("t", "User", "external_id", "Ex", None, False) == "1"
    assert store.find_conflict("t", "User", "external_id", "ex", None, False) is None  # case-sensitive
    assert store.find_conflict("other", "User", "user_name", "alice", None, True) is None  # other tenant
    with pytest.raises(KeyError):
        store.find_conflict("t", "User", "body; DROP TABLE resources", "x", None, True)


def test_store_query_log_filters(store):
    def log(status, method="GET", rtype="Users", rid=None):
        store.log_request({"ts": store_mod.now(), "tenant": "t", "method": method, "path": "/p", "status": status,
                           "durationMs": 0.5, "authOk": True, "resourceType": rtype, "resourceId": rid})
    for st in (200, 201, 404, 409, 500):
        log(st)
    log(204, "DELETE", "Groups", "gid")
    assert len(store.query_log("t")) == 6
    assert [r["status"] for r in store.query_log("t", status="4xx")] == [409, 404]
    assert [r["status"] for r in store.query_log("t", status=404)] == [404]
    assert [r["status"] for r in store.query_log("t", status="5XX")] == [500]
    assert len(store.query_log("t", status="bogus")) == 6 and len(store.query_log("t", status="")) == 6
    assert len(store.query_log("t", status="x2x")) == 6  # malformed class pattern ignored
    assert len(store.query_log("t", method="delete")) == 1
    assert len(store.query_log("t", resource="gid")) == 1 and len(store.query_log("t", resource="GROUPS")) == 1
    assert len(store.query_log("t", limit=0)) == 1  # limit is clamped to at least 1
    assert len(store.query_log("t", limit=10**9)) == 6  # ...and to LOG_CAP at most
    assert store.query_log("other") == []


def test_store_log_stats_and_clear(store):
    for st, m in ((200, "GET"), (200, "GET"), (201, "POST")):
        store.log_request({"ts": store_mod.now(), "tenant": "t", "method": m, "path": "/p", "status": st,
                           "durationMs": 0, "authOk": False})
    assert store.log_stats("t") == {"requestsByMethod": {"GET": 2, "POST": 1}, "requestsByStatus": {"200": 2, "201": 1}}
    assert store.query_log("t")[0]["authOk"] is False
    store.clear_log("t")
    assert store.log_stats("t") == {"requestsByMethod": {}, "requestsByStatus": {}}


def test_store_row_roundtrip_preserves_json(store):
    body = {"userName": "u", "nested": {"a": [1, 2, {"b": None}]}, "emoji": "☃"}
    store.insert_resource("t", "User", "1", body, True)
    row = store.get_resource("t", "User", "1")
    assert row["body"] == body and row["version"] == 1 and row["userName"] == "u"
    store.update_resource("t", "User", "1", {**body, "userName": "v"}, 2, False)
    row = store.get_resource("t", "User", "1")
    assert row["version"] == 2 and row["userName"] == "v" and row["active"] is False
    store.delete_resource("t", "User", "1")
    assert store.get_resource("t", "User", "1") is None


def test_now_format():
    ts = store_mod.now()
    assert len(ts) == 24 and ts.endswith("Z") and ts[10] == "T"
    json.dumps(ts)
