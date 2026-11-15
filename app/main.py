"""MockSCIMTarget: a SCIM 2.0 server for proving end-to-end provisioning."""
import asyncio
import hmac
import json
import os
import random
import re
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import admin, routes, store as store_mod
from .errors import ScimError, error_body

RE_GENERIC = re.compile(r"^/t/([^/]+)/scim/v2(/.*)?$")
RE_ALIAS = re.compile(r"^/scim/v2/enterprises/([^/]+)(/.*)?$")
RE_RES = re.compile(r"^/([^/]+)(?:/([^/]+))?")
SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
RESP_CAP, REQ_CAP = 4096, 16384
STATIC = Path(__file__).parent / "static"


def scim_resp(status, detail, scim_type=None, headers=None):
    return JSONResponse(error_body(status, detail, scim_type), status, headers,
                        media_type="application/scim+json")


def _trunc(b: bytes, cap: int):
    if not b:
        return None
    s = b.decode("utf-8", "replace")
    return s if len(s) <= cap else s[:cap] + f"... [truncated, {len(s)} chars]"


def create_app(data_dir: str | None = None, token: str | None = None) -> FastAPI:
    app = FastAPI(title="MockSCIMTarget", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.store = store_mod.Store(data_dir or os.environ.get("DATA_DIR", "./data"))
    app.state.token = token or os.environ.get("SCIM_TOKEN", "target-token")
    app.state.counters = {}

    @app.exception_handler(ScimError)
    async def _scim_error(request, exc: ScimError):
        return scim_resp(exc.status, exc.detail, exc.scim_type)

    @app.exception_handler(RequestValidationError)
    async def _validation(request, exc: RequestValidationError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": jsonable(exc.errors())}, 422)
        msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return scim_resp(400, f"Invalid request: {msg}", "invalidValue")

    @app.exception_handler(StarletteHTTPException)
    async def _http(request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/") or not (
                RE_GENERIC.match(request.url.path) or RE_ALIAS.match(request.url.path)):
            return JSONResponse({"detail": exc.detail}, exc.status_code)
        return scim_resp(exc.status_code, str(exc.detail))

    @app.middleware("http")
    async def scim_middleware(request: Request, call_next):
        m = RE_GENERIC.match(request.url.path) or RE_ALIAS.match(request.url.path)
        if not m:
            return await call_next(request)
        tenant, rest = m.group(1), m.group(2) or ""
        t0 = time.perf_counter()
        st = request.app.state
        body_bytes = await request.body()
        got = request.headers.get("authorization", "")
        auth_ok = got.lower().startswith("bearer ") and hmac.compare_digest(
            got[7:].strip().encode(), st.token.encode())
        resp = None
        if not SLUG_RE.match(tenant):
            resp = scim_resp(400, "Invalid tenant slug", "invalidValue")
        elif not auth_ok:
            resp = scim_resp(401, "Missing or invalid bearer token", None,
                             {"WWW-Authenticate": 'Bearer realm="scim"'})
        else:
            faults = await run_in_threadpool(st.store.ensure_tenant, tenant)
            n = st.counters[tenant] = st.counters.get(tenant, 0) + 1
            if faults["latencyMs"]:
                await asyncio.sleep(faults["latencyMs"] / 1000)
            if faults["rateLimitEvery"] and n % faults["rateLimitEvery"] == 0:
                resp = scim_resp(429, "Too many requests (injected)", "tooMany", {"Retry-After": "1"})
            elif faults["errorRatePercent"] and random.random() * 100 < faults["errorRatePercent"]:
                resp = scim_resp(500, "Injected server error")
            elif faults["readOnly"] and request.method in ("POST", "PUT", "PATCH", "DELETE"):
                resp = scim_resp(503, "Target is in read-only mode (injected)")
            else:
                try:
                    resp = await call_next(request)
                except Exception as exc:  # noqa: BLE001
                    resp = scim_resp(500, f"Internal error: {exc}")
        if hasattr(resp, "body_iterator"):
            chunks = [c async for c in resp.body_iterator]
            raw = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
            resp = Response(raw, resp.status_code, dict(resp.headers))
        else:
            raw = resp.body
        error = None
        if resp.status_code >= 400:
            try:
                error = json.loads(raw).get("detail")
            except Exception:  # noqa: BLE001
                error = raw[:200].decode("utf-8", "replace")
        rm = RE_RES.match(rest)
        rtype, rid = (rm.group(1), rm.group(2)) if rm else (None, None)
        if rid is None and rtype in ("Users", "Groups") and resp.status_code < 300 and raw:
            try:
                rid = json.loads(raw).get("id")
            except Exception:  # noqa: BLE001
                pass
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        entry = {"ts": store_mod.now(), "tenant": tenant, "method": request.method, "path": path,
                 "resourceType": rtype, "resourceId": rid, "status": resp.status_code,
                 "durationMs": round((time.perf_counter() - t0) * 1000, 1), "authOk": auth_ok,
                 "requestBody": _trunc(body_bytes, REQ_CAP), "responseBody": _trunc(raw, RESP_CAP),
                 "error": error}
        await run_in_threadpool(st.store.log_request, entry)
        return resp

    app.include_router(admin.router)
    app.include_router(routes.router, prefix="/t/{tenant}/scim/v2")
    app.include_router(routes.router, prefix="/scim/v2/enterprises/{tenant}")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/")

    app.mount("/ui", StaticFiles(directory=STATIC, html=True), name="ui")
    return app


def jsonable(errors):
    return json.loads(json.dumps(errors, default=str))


app = create_app()
