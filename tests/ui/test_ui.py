"""Browser tests: drive the real UI against a live server. Any JS error fails the test (see `ui` fixture)."""
import json
import re
import time

from playwright.sync_api import expect


def sidebar(ui, label):
    ui.locator("aside.sidebar nav").get_by_text(label, exact=True).click()


def stat(ui, label):
    return ui.locator(".card.stat", has=ui.get_by_text(label, exact=True)).locator(".value")


# ---------------------------------------------------------------- tenants
def test_tenant_list_renders_cards(ui, server, demo):
    ui.go()
    expect(ui.get_by_role("heading", name="Tenants")).to_be_visible()
    card = ui.locator(".tenant-card", has_text="demo")
    expect(card).to_be_visible()
    expect(card.locator(".badge", has_text="3 users")).to_be_visible()
    expect(card.locator(".badge", has_text="2 active")).to_be_visible()
    expect(card.locator(".badge", has_text="1 suspended")).to_be_visible()
    expect(card.locator(".badge", has_text="2 groups")).to_be_visible()
    expect(card.get_by_role("button", name="Delete tenant demo")).to_be_visible()  # the x


def test_empty_state_and_new_tenant_entry(ui, server, fresh):
    ui.go()
    ui.get_by_placeholder("new-tenant-slug").fill("bad slug!")
    ui.get_by_role("button", name="Open tenant").click()
    expect(ui.locator(".toast", has_text="Use letters, digits")).to_be_visible()
    t = fresh(seed=False)["tenant"]
    ui.get_by_placeholder("new-tenant-slug").fill(t)
    ui.get_by_placeholder("new-tenant-slug").press("Enter")
    expect(ui.get_by_role("heading", name="Dashboard")).to_be_visible()
    assert ui.url.endswith(f"#/t/{t}/dashboard")
    expect(ui.get_by_text("Nothing yet")).to_be_visible()


def test_open_tenant_from_card(ui, demo):
    ui.go()
    ui.locator(".tenant-card", has_text="demo").locator("b", has_text="demo").click()
    expect(ui.get_by_role("heading", name="Dashboard")).to_be_visible()
    assert ui.url.endswith("#/t/demo/dashboard")
    expect(ui.locator(".tenant-box")).to_contain_text("demo")
    ui.get_by_role("link", name="Switch tenant").click()
    expect(ui.get_by_role("heading", name="Tenants")).to_be_visible()


# ---------------------------------------------------------------- dashboard
def test_dashboard_counts(ui, server, demo):
    ui.go("#/t/demo/dashboard")
    expect(ui.get_by_role("heading", name="Dashboard")).to_be_visible()
    summary = server.admin("GET", "/tenants/demo/summary").json()
    expect(stat(ui, "Users")).to_have_text("3")
    expect(stat(ui, "Groups")).to_have_text("2")
    expect(stat(ui, "Requests")).to_have_text(str(summary["counts"]["requests"]))
    expect(stat(ui, "Faults")).to_have_text("off")
    expect(ui.locator(".card", has_text="Active vs suspended").locator(".badge", has_text="2 active")).to_be_visible()
    expect(ui.locator(".card", has_text="Active vs suspended").locator(".badge", has_text="1 suspended")).to_be_visible()
    methods = ui.locator(".card", has_text="Requests by method")
    expect(methods).to_contain_text("POST")
    status = ui.locator(".card", has_text="Requests by status")
    for code in ("201", "404", "409"):
        expect(status).to_contain_text(code)
    recent = ui.locator(".card", has_text="Recent activity")
    expect(recent.locator("tbody tr").first).to_be_visible()
    expect(recent).to_contain_text("/t/demo/scim/v2/Users")
    expect(ui.locator(".card", has_text="Connection info")).to_be_visible()


def test_dashboard_shows_fault_state_and_refreshes(ui, server, fresh):
    t = fresh()["tenant"]
    server.admin("PUT", f"/tenants/{t}/faults", json={"readOnly": True})
    ui.go(f"#/t/{t}/dashboard")
    expect(stat(ui, "Faults")).to_have_text("ON")
    before = int(stat(ui, "Requests").inner_text())
    server.admin("PUT", f"/tenants/{t}/faults", json={"readOnly": False})
    server.scim(t, "GET", "/Users")
    ui.get_by_role("button", name="Refresh").click()
    expect(stat(ui, "Faults")).to_have_text("off")
    expect(stat(ui, "Requests")).to_have_text(str(before + 1))


# ---------------------------------------------------------------- users
def test_users_list_search_and_detail(ui, server, demo):
    ui.go("#/t/demo/users")
    rows = ui.locator("tbody tr.clickable")
    expect(rows).to_have_count(3)
    expect(ui.get_by_text("3 of 3")).to_be_visible()
    expect(rows.filter(has_text="bob@example.com").locator(".badge")).to_have_text("suspended")
    expect(rows.filter(has_text="alice@example.com")).to_contain_text("Engineering")

    search = ui.get_by_placeholder("Search users...")
    search.fill("CAROL")
    expect(rows).to_have_count(1)
    expect(ui.get_by_text("1 of 3")).to_be_visible()
    search.fill("engineering")  # matches the enterprise department
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("alice@example.com")
    search.fill("nobody-matches-this")
    expect(ui.get_by_text("No users")).to_be_visible()
    search.fill("")
    expect(rows).to_have_count(3)

    rows.filter(has_text="alice@example.com").click()
    expect(ui.get_by_role("heading", name="alice@example.com")).to_be_visible()
    assert ui.url.endswith(f"/users/{demo['alice']}")
    summary = ui.locator(".card", has_text="Summary")
    expect(summary).to_contain_text(demo["alice"])
    expect(summary).to_contain_text("alice@example.com (work)")
    expect(summary).to_contain_text("Engineering")
    expect(summary.locator(".chip", has_text="Engineering")).to_be_visible()  # group membership chip
    doc = json.loads(ui.locator("pre.code").first.inner_text())
    assert doc["userName"] == "alice@example.com" and doc["id"] == demo["alice"] and doc["active"] is True
    assert doc["groups"][0]["display"] == "Engineering"

    # per-resource request history: exactly the requests that touched this user
    expected = server.requests("demo", resource=demo["alice"])
    expect(ui.get_by_role("heading", name=f"Request history ({len(expected)})")).to_be_visible()
    assert len(expected) >= 2
    hist = ui.locator(".card", has_text="Request history")
    expect(hist.locator("tr.logrow")).to_have_count(len(expected))
    hist.locator("tr.logrow").last.click()  # oldest = the POST that created it
    detail = hist.locator("tr.detail:not([hidden])")
    expect(detail).to_be_visible()
    expect(detail).to_contain_text("alice@example.com")

    ui.get_by_role("link", name="Back to users").click()
    expect(ui.get_by_role("heading", name="Users", exact=True)).to_be_visible()


def test_unknown_user_shows_notice(ui, demo):
    ui.go("#/t/demo/users/not-a-real-id")
    expect(ui.locator(".notice.warn")).to_contain_text("not found")


# ---------------------------------------------------------------- groups
def test_groups_expand_members(ui, demo):
    ui.go("#/t/demo/groups")
    expect(ui.get_by_text("2 groups - click a row to expand members")).to_be_visible()
    eng = ui.locator("tr.clickable", has_text="Engineering")
    detail = eng.locator("xpath=following-sibling::tr[1]")
    expect(detail).to_be_hidden()
    eng.click()
    expect(detail).to_be_visible()
    expect(detail.locator(".chip")).to_have_text(["alice@example.com", "bob@example.com"])
    detail.locator(".chip", has_text="bob@example.com").click()  # chip links to the user
    expect(ui.get_by_role("heading", name="bob@example.com")).to_be_visible()
    ui.go_back()
    ui.locator("tr.clickable", has_text="Engineering").click()
    ui.locator("tr.clickable", has_text="Engineering").click()  # toggles closed again
    expect(ui.locator("tr.clickable", has_text="Engineering").locator("xpath=following-sibling::tr[1]")).to_be_hidden()
    empty = ui.locator("tr.clickable", has_text="Empty")
    empty.click()
    expect(empty.locator("xpath=following-sibling::tr[1]")).to_contain_text("No members")


# ---------------------------------------------------------------- request log
def test_request_log_filters_and_bodies(ui, server, demo):
    ui.go("#/t/demo/requests")
    rows = ui.locator("tr.logrow")
    total = len(server.requests("demo"))
    expect(rows).to_have_count(total)
    expect(ui.get_by_text(f"{total} shown")).to_be_visible()

    ui.locator("select").first.select_option("POST")
    posts = len(server.requests("demo", method="POST"))
    expect(rows).to_have_count(posts)
    assert all(t.strip() == "POST" for t in rows.locator(".method").all_inner_texts())

    ui.locator("select").nth(1).select_option("409")
    expect(rows).to_have_count(1)
    row = rows.first
    expect(row).to_contain_text("409")
    row.click()
    detail = ui.locator("tr.detail:not([hidden])")
    expect(detail).to_be_visible()
    expect(detail.locator(".notice.warn")).to_contain_text("already exists")  # error text
    bodies = detail.locator("pre.code")
    assert json.loads(bodies.nth(0).inner_text())["userName"] == "ALICE@example.com"  # request body, pretty printed
    assert json.loads(bodies.nth(1).inner_text())["scimType"] == "uniqueness"  # response body
    expect(detail).to_contain_text("auth ok")
    row.click()
    expect(ui.locator("tr.detail:not([hidden])")).to_have_count(0)

    ui.locator("select").first.select_option("")
    ui.locator("select").nth(1).select_option("404")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("/Users/does-not-exist")
    ui.locator("select").nth(1).select_option("5xx")
    expect(ui.get_by_text("No requests recorded.")).to_be_visible()


def test_request_log_auto_refresh_toggle_and_clear(ui, server, fresh):
    t = fresh()["tenant"]
    ui.go(f"#/t/{t}/requests")
    rows = ui.locator("tr.logrow")
    n = len(server.requests(t))
    expect(rows).to_have_count(n)

    # an expanded row stays expanded across refreshes, and refreshes do not re-run the fade-in
    rows.first.click()
    expect(ui.locator("tr.detail:not([hidden])")).to_have_count(1)
    ui.get_by_label("Auto-refresh 2s").check()
    server.scim(t, "GET", "/Users")
    expect(rows).to_have_count(n + 1)  # appears without pressing Refresh
    expect(ui.locator("tr.detail:not([hidden])")).to_have_count(1)
    assert ui.evaluate("() => document.querySelector('.lazy').classList.contains('no-fade')")
    assert ui.evaluate("() => getComputedStyle(document.querySelector('.lazy > *')).animationName") == "none"

    ui.get_by_label("Auto-refresh 2s").uncheck()
    server.scim(t, "GET", "/Users")
    ui.wait_for_timeout(2600)  # longer than one refresh period
    expect(rows).to_have_count(n + 1)
    ui.get_by_role("button", name="Refresh").click()
    expect(rows).to_have_count(n + 2)

    ui.get_by_role("button", name="Clear log").click()
    expect(ui.get_by_text("No requests recorded.")).to_be_visible()
    assert server.requests(t) == []


# ---------------------------------------------------------------- fault injection
def _faults_page(ui, t):
    ui.go(f"#/t/{t}/faults")
    expect(ui.get_by_role("heading", name="Fault injection")).to_be_visible()


def test_fault_rate_limit_saved_from_ui_applies(ui, server, fresh):
    t = fresh(seed=False)["tenant"]
    _faults_page(ui, t)
    ui.get_by_label("Rate limit every N requests").fill("1")
    ui.get_by_role("button", name="Save").click()
    expect(ui.locator(".toast", has_text="Fault settings saved")).to_be_visible()
    r = server.scim(t, "GET", "/Users")
    assert r.status_code == 429 and r.headers["retry-after"] == "1"
    assert server.admin("GET", f"/tenants/{t}/faults").json()["rateLimitEvery"] == 1
    # the log shows it as a 429 with the injected error text
    ui.go(f"#/t/{t}/requests")
    expect(ui.locator("tr.logrow", has_text="429").first).to_be_visible()


def test_fault_error_rate_and_disable_all(ui, server, fresh):
    t = fresh(seed=False)["tenant"]
    _faults_page(ui, t)
    ui.get_by_label("Error rate").fill("100")
    ui.get_by_role("button", name="Save").click()
    expect(ui.locator(".toast", has_text="Fault settings saved")).to_be_visible()
    assert server.scim(t, "GET", "/Users").status_code == 500
    ui.get_by_role("button", name="Disable all").click()
    expect(ui.locator(".toast", has_text="All faults disabled")).to_be_visible()
    expect(ui.get_by_label("Error rate")).to_have_value("0")
    assert server.scim(t, "GET", "/Users").status_code == 200


def test_fault_read_only_returns_503_for_writes(ui, server, fresh):
    t = fresh(seed=False)["tenant"]
    _faults_page(ui, t)
    ui.get_by_label("Read-only mode").check()
    ui.get_by_role("button", name="Save").click()
    expect(ui.locator(".toast", has_text="Fault settings saved")).to_be_visible()
    assert server.scim(t, "POST", "/Users", json={"userName": "x"}).status_code == 503
    assert server.scim(t, "GET", "/Users").status_code == 200
    ui.go(f"#/t/{t}/dashboard")  # state survives reloads and shows on the dashboard
    expect(stat(ui, "Faults")).to_have_text("ON")
    _faults_page(ui, t)
    expect(ui.get_by_label("Read-only mode")).to_be_checked()


def test_fault_latency(ui, server, fresh):
    t = fresh(seed=False)["tenant"]
    _faults_page(ui, t)
    ui.get_by_label("Added latency").fill("400")
    ui.get_by_role("button", name="Save").click()
    expect(ui.locator(".toast", has_text="Fault settings saved")).to_be_visible()
    start = time.perf_counter()
    assert server.scim(t, "GET", "/Users").status_code == 200
    assert time.perf_counter() - start >= 0.38


# ---------------------------------------------------------------- connection
def test_connection_page(ui, server, demo):
    ui.go("#/t/demo/connection")
    expect(ui.get_by_role("heading", name="Connection", exact=True)).to_be_visible()
    texts = ui.locator(".copy code").all_inner_texts()
    port = server.base.rsplit(":", 1)[1]
    assert f"{server.base}/t/demo/scim/v2" in texts
    assert f"{server.base}/scim/v2/enterprises/demo" in texts
    assert f"http://host.docker.internal:{port}/t/demo/scim/v2" in texts
    assert f"http://host.docker.internal:{port}/scim/v2/enterprises/demo" in texts
    assert server.token in texts
    assert any(t.startswith("curl ") and server.token in t for t in texts)
    ui.locator(".copy", has_text=server.token).get_by_role("button", name="Copy").first.click()
    expect(ui.locator(".toast", has_text="Copied")).to_be_visible()
    assert ui.evaluate("() => navigator.clipboard.readText()") == server.token


def test_connection_curl_actually_works(ui, server, demo):
    ui.go("#/t/demo/connection")
    curl = [t for t in ui.locator(".copy code").all_inner_texts() if t.startswith("curl")][0]
    url = re.search(r"(http\S+)$", curl).group(1)
    r = server.http.get(url, headers={"Authorization": f"Bearer {server.token}"})
    assert r.status_code == 200 and r.json()["totalResults"] == 3


# ---------------------------------------------------------------- reset / delete
def test_reset_tenant(ui, server, fresh):
    t = fresh()["tenant"]
    server.admin("PUT", f"/tenants/{t}/faults", json={"latencyMs": 1})
    ui.go(f"#/t/{t}/dashboard")
    expect(stat(ui, "Users")).to_have_text("3")
    messages = []
    ui.once("dialog", lambda d: (messages.append(d.message), d.accept()))
    ui.get_by_role("button", name="Reset tenant").click()
    expect(ui.locator(".toast", has_text=f"Tenant {t} reset")).to_be_visible()
    assert t in messages[0] and "Delete ALL users" in messages[0]
    expect(stat(ui, "Users")).to_have_text("0")
    expect(stat(ui, "Groups")).to_have_text("0")
    assert server.admin("GET", f"/tenants/{t}/faults").json()["latencyMs"] == 0
    assert server.scim(t, "GET", "/Users").json()["totalResults"] == 0


def test_reset_can_be_cancelled(ui, server, fresh):
    t = fresh()["tenant"]
    ui.go(f"#/t/{t}/dashboard")
    ui.once("dialog", lambda d: d.dismiss())
    ui.get_by_role("button", name="Reset tenant").click()
    ui.wait_for_timeout(300)
    assert server.admin("GET", f"/tenants/{t}/summary").json()["counts"]["users"] == 3


def test_delete_tenant_from_card_x(ui, server, fresh):
    t = fresh()["tenant"]
    keep = fresh()["tenant"]
    ui.go()
    card = ui.locator(".tenant-card", has_text=t)
    expect(card).to_be_visible()
    messages = []
    ui.once("dialog", lambda d: (messages.append(d.message), d.accept()))
    card.get_by_role("button", name=f"Delete tenant {t}").click()
    expect(ui.locator(".tenant-card", has_text=t)).to_have_count(0)
    expect(ui.locator(".toast", has_text=f"Tenant {t} deleted")).to_be_visible()
    assert "3 users" in messages[0] and "2 groups" in messages[0]
    assert "/dashboard" not in ui.url  # clicking the x must not open the tenant
    expect(ui.locator(".tenant-card", has_text=keep)).to_be_visible()
    assert t not in [x["tenant"] for x in server.admin("GET", "/tenants").json()]


def test_delete_tenant_can_be_cancelled(ui, server, fresh):
    t = fresh()["tenant"]
    ui.go()
    ui.once("dialog", lambda d: d.dismiss())
    ui.locator(".tenant-card", has_text=t).get_by_role("button", name=f"Delete tenant {t}").click()
    ui.wait_for_timeout(300)
    expect(ui.locator(".tenant-card", has_text=t)).to_be_visible()
    assert "/dashboard" not in ui.url
    assert t in [x["tenant"] for x in server.admin("GET", "/tenants").json()]


# ---------------------------------------------------------------- navigation and fade-in
def test_sidebar_navigation_visits_every_page(ui, demo):
    ui.go("#/t/demo/dashboard")
    for label, heading in (("Users", "Users"), ("Groups", "Groups"), ("Request log", "Request log"),
                           ("Fault injection", "Fault injection"), ("Connection", "Connection"),
                           ("Dashboard", "Dashboard")):
        sidebar(ui, label)
        expect(ui.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        expect(ui.locator("aside.sidebar nav a.active")).to_contain_text(label)


def test_content_fades_in_on_navigation(ui, demo):
    ui.go("#/t/demo/users")
    expect(ui.get_by_role("heading", name="Users", exact=True)).to_be_visible()
    assert ui.evaluate("() => getComputedStyle(document.querySelector('#app > *')).animationName") in ("fade-in", "none")
    assert "fade-in" in ui.evaluate("() => [...document.styleSheets[0].cssRules].map(r => r.name || '').join()")
    expect(ui.locator("#app > *")).to_have_css("opacity", "1")  # settles fully visible


def test_dashboard_poll_does_not_refade(ui, demo):
    ui.go("#/t/demo/dashboard")
    expect(stat(ui, "Users")).to_have_text("3")
    ui.wait_for_function("() => document.querySelector('#app > *').classList.contains('no-fade')", timeout=9000)
    assert ui.evaluate("() => getComputedStyle(document.querySelector('#app > .shell main > *')).animationName") == "none"


def test_reduced_motion_disables_animation(browser, server, demo):
    ctx = browser.new_context(reduced_motion="reduce", viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server.base}/ui/#/t/demo/users")
    expect(page.get_by_role("heading", name="Users", exact=True)).to_be_visible()
    assert page.evaluate("() => getComputedStyle(document.querySelector('#app > *')).animationName") == "none"
    ctx.close()
    assert errors == []
