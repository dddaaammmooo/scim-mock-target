"""Fixtures for the browser tests: a real uvicorn server in a thread, seeded over the SCIM API."""
import os
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn

from app.main import create_app

TOKEN = "ui-test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/scim+json"}


def pytest_collection_modifyitems(items):
    here = str(Path(__file__).parent)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.ui)


class Server:
    def __init__(self, base: str):
        self.base = base
        self.token = TOKEN
        self.http = httpx.Client(base_url=base, headers=HEADERS, timeout=10)

    def scim(self, tenant: str, method: str, path: str, **kw):
        return self.http.request(method, f"/t/{tenant}/scim/v2{path}", **kw)

    def admin(self, method: str, path: str, **kw):
        return self.http.request(method, f"/api{path}", **kw)

    def seed(self, tenant: str) -> dict:
        """3 users (one suspended), 2 groups (one empty), plus a 404 and a 409 in the log."""
        ids = {}
        for name, extra in (
                ("alice@example.com", {"displayName": "Alice Anders", "externalId": "E1",
                                       "emails": [{"value": "alice@example.com", "type": "work", "primary": True}],
                                       "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User":
                                           {"department": "Engineering"}}),
                ("bob@example.com", {"displayName": "Bob Brown", "externalId": "E2", "active": False}),
                ("carol@example.com", {"displayName": "Carol Chen", "externalId": "E3"})):
            r = self.scim(tenant, "POST", "/Users", json={"userName": name, **extra})
            assert r.status_code == 201, r.text
            ids[name.split("@")[0]] = r.json()["id"]
        r = self.scim(tenant, "POST", "/Groups", json={
            "displayName": "Engineering", "members": [{"value": ids["alice"]}, {"value": ids["bob"]}]})
        assert r.status_code == 201, r.text
        ids["Engineering"] = r.json()["id"]
        assert self.scim(tenant, "POST", "/Groups", json={"displayName": "Empty"}).status_code == 201
        assert self.scim(tenant, "GET", "/Users/does-not-exist").status_code == 404
        assert self.scim(tenant, "POST", "/Users", json={"userName": "ALICE@example.com"}).status_code == 409
        assert self.scim(tenant, "GET", f"/Users/{ids['alice']}").status_code == 200
        return ids

    def requests(self, tenant: str, **params):
        return self.admin("GET", f"/tenants/{tenant}/requests", params={"limit": 500, **params}).json()


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("ui-data")
    app = create_app(str(data), TOKEN)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    srv = uvicorn.Server(cfg)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not srv.started:
        assert thread.is_alive() and time.time() < deadline, "uvicorn failed to start"
        time.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    s = Server(f"http://127.0.0.1:{port}")
    yield s
    srv.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="session")
def demo(server):
    """Read-only seeded tenant shared by tests that only look."""
    ids = server.seed("demo")
    return {"tenant": "demo", **ids}


@pytest.fixture()
def fresh(server):
    """Factory for throwaway tenants; everything created is deleted afterwards."""
    made = []

    def make(seed=True):
        t = "t" + uuid.uuid4().hex[:8]
        made.append(t)
        ids = server.seed(t) if seed else {}
        return {"tenant": t, **ids}

    yield make
    for t in made:
        server.admin("DELETE", f"/tenants/{t}")


# ---------------------------------------------------------------- browser configuration
@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """PLAYWRIGHT_CHANNEL=msedge uses the system Edge; unset uses Playwright's bundled chromium (CI)."""
    channel = os.environ.get("PLAYWRIGHT_CHANNEL")
    return {**browser_type_launch_args, **({"channel": channel} if channel else {})}


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1440, "height": 900}, "color_scheme": "dark",
            "permissions": ["clipboard-read", "clipboard-write"]}


@pytest.fixture()
def js_errors(page):
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console.error: {m.text}"))
    return errors


@pytest.fixture()
def ui(page, server, js_errors):
    """Page helper; fails the test if the page logged any JS error."""
    page.set_default_timeout(8000)

    def go(hash_=""):
        page.goto(f"{server.base}/ui/{hash_}")
    page.go = go
    yield page
    assert js_errors == [], js_errors
