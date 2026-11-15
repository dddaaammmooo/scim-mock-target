from urllib.parse import quote

from conftest import AUTH

ENT = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"


def names(resp):
    return sorted(r["userName"] for r in resp.json()["Resources"])


# ---------------------------------------------------------------- auth / basics
def test_auth_required(client):
    r = client.get("/t/acme/scim/v2/Users")
    assert r.status_code == 401
    body = r.json()
    assert body["status"] == "401" and "Error" in body["schemas"][0]
    assert client.get("/t/acme/scim/v2/Users", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/t/acme/scim/v2/Users", headers=AUTH).status_code == 200


def test_discovery(api):
    assert api.get("/ServiceProviderConfig").json()["patch"]["supported"] is True
    assert api.get("/ResourceTypes").json()["totalResults"] == 2
    ids = [s["id"] for s in api.get("/Schemas").json()["Resources"]]
    assert any(i.endswith("core:2.0:User") for i in ids) and ENT in ids
    assert api.get("/ServiceProviderConfig").headers["content-type"].startswith("application/scim+json")


def test_create_get(api):
    r = api.post("/Users", {"userName": "Alice", "externalId": "e1", "emails": [{"value": "a@x.io", "type": "work"}],
                            "title": "Dev", "roles": [{"value": "user"}], ENT: {"department": "Eng"}})
    assert r.status_code == 201
    assert r.headers["location"].endswith(f"/t/acme/scim/v2/Users/{r.json()['id']}")
    assert r.headers["etag"] == 'W/"1"'
    u = r.json()
    assert u["meta"]["resourceType"] == "User" and u["meta"]["version"] == 'W/"1"'
    assert u["active"] is True and u["groups"] == []
    assert ENT in u["schemas"] and u["title"] == "Dev" and u[ENT]["department"] == "Eng"
    g = api.get(f"/Users/{u['id']}")
    assert g.status_code == 200 and g.json()["userName"] == "Alice"
    assert api.get("/Users/nope").status_code == 404
    assert api.get("/Users/nope").json()["status"] == "404"


def test_missing_username(api):
    r = api.post("/Users", {"displayName": "x"})
    assert r.status_code == 400 and r.json()["scimType"] == "invalidValue"
    assert api.post("/Groups", {}).status_code == 400


def test_uniqueness(api):
    api.user("Bob", externalId="x1")
    r = api.post("/Users", {"userName": "BOB"})
    assert r.status_code == 409 and r.json()["scimType"] == "uniqueness"
    r = api.post("/Users", {"userName": "other", "externalId": "x1"})
    assert r.status_code == 409
    api.group("Eng")
    assert api.post("/Groups", {"displayName": "eng"}).status_code == 409


# ---------------------------------------------------------------- filters
def test_filters(api):
    api.user("alice", name={"givenName": "Alice", "familyName": "Smith"}, emails=[{"value": "alice@corp.com", "type": "work"}],
             externalId="E1")
    api.user("bob", name={"givenName": "Bob", "familyName": "Jones"}, emails=[{"value": "bob@other.org", "type": "home"}],
             active=False)
    api.user("carol", emails=[{"value": "carol@corp.com", "type": "work"}])

    def f(expr):
        r = api.get("/Users?filter=" + quote(expr))
        assert r.status_code == 200, r.text
        return names(r)

    assert f('userName eq "ALICE"') == ["alice"]
    assert f('userName co "o"') == ["bob", "carol"]
    assert f('userName sw "ca" or userName ew "ob"') == ["bob", "carol"]
    assert f('emails.value co "corp.com" and active eq true') == ["alice", "carol"]
    assert f('emails.value eq "BOB@other.org"') == ["bob"]
    assert f('active eq false') == ["bob"]
    assert f('name.familyName eq "jones"') == ["bob"]
    assert f('externalId pr') == ["alice"]
    assert f('userName ne "alice"') == ["bob", "carol"]
    assert f('(userName eq "alice" or userName eq "bob") and active eq true') == ["alice"]
    assert f('emails[type eq "work" and value co "carol"]') == ["carol"]
    assert f('not (userName eq "alice")') == ["bob", "carol"]
    assert f('meta.lastModified gt "2000-01-01T00:00:00Z"') == ["alice", "bob", "carol"]
    bad = api.get("/Users?filter=" + quote("userName zz 1"))
    assert bad.status_code == 400 and bad.json()["scimType"] == "invalidFilter"
    assert api.get("/Users?filter=" + quote('userName eq')).status_code == 400


def test_pagination(api):
    for i in range(5):
        api.user(f"u{i}")
    r = api.get("/Users?startIndex=2&count=2").json()
    assert r["totalResults"] == 5 and r["startIndex"] == 2 and r["itemsPerPage"] == 2
    assert [u["userName"] for u in r["Resources"]] == ["u1", "u2"]
    assert api.get("/Users?startIndex=5&count=10").json()["itemsPerPage"] == 1
    assert api.get("/Users?count=0").json()["Resources"] == []
    assert api.get("/Users").json()["itemsPerPage"] == 5


def test_attributes(api):
    u = api.user("zed", title="T")
    r = api.get("/Users?attributes=userName").json()["Resources"][0]
    assert "userName" in r and "title" not in r and r["id"] == u["id"]
    g = api.group("G", [u["id"]])
    r = api.get("/Groups?excludedAttributes=members").json()["Resources"][0]
    assert "members" not in r and r["displayName"] == "G"


# ---------------------------------------------------------------- PUT / PATCH
def test_put(api):
    u = api.user("dave", title="old", externalId="d")
    r = api.put(f"/Users/{u['id']}", {"userName": "dave", "displayName": "Dave D"})
    assert r.status_code == 200
    j = r.json()
    assert j["displayName"] == "Dave D" and "title" not in j and "externalId" not in j
    assert j["id"] == u["id"] and r.headers["etag"] == 'W/"2"'
    assert api.put(f"/Users/{u['id']}", {"displayName": "x"}).status_code == 400
    assert api.put("/Users/nope", {"userName": "q"}).status_code == 404
    other = api.user("erin")
    assert api.put(f"/Users/{other['id']}", {"userName": "DAVE"}).status_code == 409


def test_patch_user(api):
    u = api.user("frank", emails=[{"value": "f@work.com", "type": "work"}, {"value": "f@home.com", "type": "home"}],
                 name={"givenName": "F", "familyName": "R"})
    uid = u["id"]
    r = api.patch(f"/Users/{uid}", [{"op": "replace", "path": "active", "value": False}])
    assert r.status_code == 200 and r.json()["active"] is False
    r = api.patch(f"/Users/{uid}", [{"op": "Replace", "path": "active", "value": "True"}])
    assert r.json()["active"] is True
    r = api.patch(f"/Users/{uid}", [{"op": "replace", "path": 'emails[type eq "work"].value', "value": "new@work.com"}])
    emails = {e["type"]: e["value"] for e in r.json()["emails"]}
    assert emails == {"work": "new@work.com", "home": "f@home.com"}
    r = api.patch(f"/Users/{uid}", [{"op": "replace", "path": "name.givenName", "value": "Frankie"}])
    assert r.json()["name"] == {"givenName": "Frankie", "familyName": "R"}
    r = api.patch(f"/Users/{uid}", [{"op": "add", "path": f"{ENT}:department", "value": "Sales"},
                                    {"op": "add", "path": f"{ENT}:costCenter", "value": "CC1"}])
    j = r.json()
    assert j[ENT] == {"department": "Sales", "costCenter": "CC1"} and ENT in j["schemas"]
    r = api.patch(f"/Users/{uid}", [{"op": "replace", "value": {"title": "VP", f"{ENT}:division": "D9",
                                                                  "name.familyName": "Z"}}])
    j = r.json()
    assert j["title"] == "VP" and j[ENT]["division"] == "D9" and j["name"]["familyName"] == "Z"
    r = api.patch(f"/Users/{uid}", [{"op": "remove", "path": 'emails[type eq "home"]'},
                                    {"op": "remove", "path": f"{ENT}:department"}])
    j = r.json()
    assert [e["type"] for e in j["emails"]] == ["work"] and "department" not in j[ENT]
    assert j["meta"]["version"] == 'W/"8"'
    # errors
    assert api.patch(f"/Users/{uid}", [{"op": "bogus", "path": "x", "value": 1}]).json()["scimType"] == "invalidSyntax"
    assert api.patch(f"/Users/{uid}", []).status_code == 400
    r = api.patch(f"/Users/{uid}", [{"op": "remove"}])
    assert r.status_code == 400 and r.json()["scimType"] == "noTarget"
    r = api.patch(f"/Users/{uid}", [{"op": "replace", "path": "emails[", "value": 1}])
    assert r.status_code == 400
    r = api.patch(f"/Users/{uid}", [{"op": "replace", "path": "groups", "value": []}])
    assert r.status_code == 400 and r.json()["scimType"] == "mutability"
    r = api.patch(f"/Users/{uid}", [{"op": "replace", "path": "userName", "value": ""}])
    assert r.status_code == 400
    assert api.patch("/Users/nope", [{"op": "replace", "path": "active", "value": False}]).status_code == 404


def test_patch_group_members(api):
    a, b, c = api.user("a"), api.user("b"), api.user("c")
    g = api.group("Team", [a["id"]])
    gid = g["id"]
    assert g["members"][0]["display"] == "a" and g["members"][0]["$ref"].endswith(f"/Users/{a['id']}")
    r = api.patch(f"/Groups/{gid}", [{"op": "add", "path": "members", "value": [{"value": b["id"]}, {"value": c["id"]}]}])
    assert sorted(m["display"] for m in r.json()["members"]) == ["a", "b", "c"]
    r = api.patch(f"/Groups/{gid}", [{"op": "add", "path": "members", "value": [{"value": b["id"]}]}])
    assert len(r.json()["members"]) == 3  # no duplicates
    r = api.patch(f"/Groups/{gid}", [{"op": "remove", "path": f'members[value eq "{b["id"]}"]'}])
    assert sorted(m["display"] for m in r.json()["members"]) == ["a", "c"]
    r = api.patch(f"/Groups/{gid}", [{"op": "remove", "path": "members", "value": [{"value": a["id"]}]}])
    assert [m["display"] for m in r.json()["members"]] == ["c"]
    r = api.patch(f"/Groups/{gid}", [{"op": "replace", "path": "members", "value": [{"value": a["id"]}]}])
    assert [m["display"] for m in r.json()["members"]] == ["a"]
    r = api.patch(f"/Groups/{gid}", [{"op": "replace", "path": "displayName", "value": "Team2"}])
    assert r.json()["displayName"] == "Team2"
    # users show computed groups
    ug = api.get(f"/Users/{a['id']}").json()["groups"]
    assert ug[0]["value"] == gid and ug[0]["display"] == "Team2" and ug[0]["$ref"].endswith(f"/Groups/{gid}")
    r = api.patch(f"/Groups/{gid}", [{"op": "remove", "path": "members"}])
    assert r.json()["members"] == []
    r = api.patch(f"/Groups/{gid}", [{"op": "add", "path": "members", "value": [{"value": "ghost"}]}])
    assert r.status_code == 400 and r.json()["scimType"] == "invalidValue"


def test_group_member_validation(api):
    r = api.post("/Groups", {"displayName": "Bad", "members": [{"value": "does-not-exist"}]})
    assert r.status_code == 400 and r.json()["scimType"] == "invalidValue"
    u = api.user("x")
    g = api.group("Ok", [u["id"]])
    r = api.put(f"/Groups/{g['id']}", {"displayName": "Ok", "members": [{"value": "nope"}]})
    assert r.status_code == 400
    assert api.get("/Groups?filter=" + quote('displayName eq "ok"')).json()["totalResults"] == 1


def test_delete_user_removes_from_groups(api):
    a, b = api.user("a"), api.user("b")
    g = api.group("G", [a["id"], b["id"]])
    assert api.delete(f"/Users/{a['id']}").status_code == 204
    assert api.get(f"/Users/{a['id']}").status_code == 404
    got = api.get(f"/Groups/{g['id']}").json()
    assert [m["value"] for m in got["members"]] == [b["id"]]
    assert got["meta"]["version"] == 'W/"2"'
    assert api.delete(f"/Users/{a['id']}").status_code == 404
    assert api.delete(f"/Groups/{g['id']}").status_code == 204
    assert api.user("a")["userName"] == "a"  # name freed up


# ---------------------------------------------------------------- tenants / log / faults / admin
def test_alias_shape_shares_tenant(api, alias, client):
    u = alias.user("shared")
    assert u["meta"]["location"].endswith(f"/scim/v2/enterprises/acme/Users/{u['id']}")
    assert api.get(f"/Users/{u['id']}").status_code == 200
    assert api.get(f"/Users/{u['id']}").json()["meta"]["location"].startswith("http://testserver/t/acme/scim/v2/")
    g = alias.group("G", [u["id"]])
    assert api.get("/Groups").json()["totalResults"] == 1
    other = client.get("/t/other/scim/v2/Users", headers=AUTH).json()
    assert other["totalResults"] == 0


def test_request_log(api, client):
    u = api.user("logged")
    api.get(f"/Users/{u['id']}")
    api.get("/Users/missing")
    client.get("/t/acme/scim/v2/Users")  # 401
    rows = client.get("/api/tenants/acme/requests").json()
    assert [r["status"] for r in rows] == [401, 404, 200, 201]
    created = rows[3]
    assert created["method"] == "POST" and created["resourceType"] == "Users" and created["resourceId"] == u["id"]
    assert '"logged"' in created["requestBody"] and created["authOk"] is True and created["durationMs"] >= 0
    assert rows[0]["authOk"] is False
    assert rows[1]["error"] and "not found" in rows[1]["error"]
    assert client.get("/api/tenants/acme/requests?method=get&status=4xx").json().__len__() == 2
    assert len(client.get(f"/api/tenants/acme/requests?resource={u['id']}").json()) == 2
    assert len(client.get("/api/tenants/acme/requests?limit=1").json()) == 1
    assert client.delete("/api/tenants/acme/requests").status_code == 204
    assert client.get("/api/tenants/acme/requests").json() == []


def test_log_truncates_and_prunes(api, client):
    api.user("big", title="x" * 10000)
    row = client.get("/api/tenants/acme/requests").json()[0]
    assert "truncated" in row["responseBody"] and len(row["responseBody"]) < 4300


def test_fault_rate_limit(api, client):
    r = client.put("/api/tenants/acme/faults", json={"rateLimitEvery": 3})
    assert r.status_code == 200 and r.json()["rateLimitEvery"] == 3
    codes = [api.get("/Users").status_code for _ in range(6)]
    assert codes == [200, 200, 429, 200, 200, 429]
    r = api.get("/Users")
    client.put("/api/tenants/acme/faults", json={"rateLimitEvery": 1})
    r = api.get("/Users")
    assert r.status_code == 429 and r.headers["retry-after"] == "1" and r.json()["status"] == "429"


def test_fault_error_rate_and_readonly_and_latency(api, client):
    client.put("/api/tenants/acme/faults", json={"errorRatePercent": 100})
    r = api.get("/Users")
    assert r.status_code == 500 and r.json()["status"] == "500"
    client.put("/api/tenants/acme/faults", json={"errorRatePercent": 0, "readOnly": True})
    assert api.get("/Users").status_code == 200
    assert api.post("/Users", {"userName": "no"}).status_code == 503
    assert api.delete("/Users/x").status_code == 503
    client.put("/api/tenants/acme/faults", json={"readOnly": False, "latencyMs": 150})
    import time
    t = time.perf_counter()
    assert api.get("/Users").status_code == 200
    assert time.perf_counter() - t >= 0.14
    f = client.get("/api/tenants/acme/faults").json()
    assert f == {"latencyMs": 150, "errorRatePercent": 0, "rateLimitEvery": 0, "readOnly": False}
    # faults are per-tenant
    assert client.get("/t/zzz/scim/v2/Users", headers=AUTH).status_code == 200


def test_admin_summary_and_reset(api, client):
    assert client.get("/api/health").json() == {"status": "ok"}
    a = api.user("alice", externalId="e-a", displayName="Alice A")
    b = api.user("bob", active=False)
    api.group("Eng", [a["id"], b["id"]], externalId="g1")
    s = client.get("/api/tenants/acme/summary").json()
    assert s["counts"]["users"] == 2 and s["counts"]["usersActive"] == 1 and s["counts"]["usersInactive"] == 1
    assert s["counts"]["groups"] == 1
    assert s["users"][0] == {"id": a["id"], "userName": "alice", "externalId": "e-a", "active": True,
                             "displayName": "Alice A"}
    assert s["groups"][0]["members"] == ["alice", "bob"] and s["groups"][0]["externalId"] == "g1"
    assert s["requestsByMethod"]["POST"] == 3
    ts = client.get("/api/tenants").json()
    assert ts[0]["tenant"] == "acme" and ts[0]["users"] == 2 and ts[0]["lastActivity"]
    assert len(client.get("/api/tenants/acme/users").json()) == 2
    assert client.get("/api/tenants/acme/groups").json()[0]["members"][0]["display"]
    assert client.delete("/api/tenants/acme").status_code == 204
    s = client.get("/api/tenants/acme/summary").json()
    assert s["counts"]["users"] == 0 and s["users"] == [] and s["counts"]["requests"] == 0
    assert client.get("/api/tenants").json() == []
    assert client.get("/", follow_redirects=False).headers["location"] == "/ui/"
