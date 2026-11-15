import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="scimtarget-import-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Api:
    """Thin helper bound to one tenant and URL shape."""

    def __init__(self, client, prefix):
        self.c, self.p = client, prefix

    def req(self, method, path, **kw):
        kw.setdefault("headers", AUTH)
        return self.c.request(method, self.p + path, **kw)

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, body, **kw):
        return self.req("POST", path, json=body, **kw)

    def put(self, path, body, **kw):
        return self.req("PUT", path, json=body, **kw)

    def patch(self, path, ops, **kw):
        return self.req("PATCH", path, json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], "Operations": ops}, **kw)

    def delete(self, path, **kw):
        return self.req("DELETE", path, **kw)

    def user(self, uname, **extra):
        r = self.post("/Users", {"userName": uname, **extra})
        assert r.status_code == 201, r.text
        return r.json()

    def group(self, gname, members=(), **extra):
        r = self.post("/Groups", {"displayName": gname, "members": [{"value": m} for m in members], **extra})
        assert r.status_code == 201, r.text
        return r.json()


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "data"), TOKEN)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def api(client):
    return Api(client, "/t/acme/scim/v2")


@pytest.fixture()
def alias(client):
    return Api(client, "/scim/v2/enterprises/acme")
