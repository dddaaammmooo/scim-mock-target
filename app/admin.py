"""Admin JSON API under /api (no SCIM auth)."""
import os
import re

from fastapi import APIRouter, Body, HTTPException, Query, Request

from . import scim

router = APIRouter(prefix="/api")
SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _store(request):
    return request.app.state.store


def _base(request, tenant):
    return f"{str(request.base_url).rstrip('/')}/t/{tenant}/scim/v2"


def _slug(t):
    if not SLUG_RE.match(t):
        raise HTTPException(400, "Invalid tenant slug")
    return t


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/config")
def config(request: Request):
    return {"token": request.app.state.token, "genericPath": "/t/{tenant}/scim/v2",
            "aliasPath": "/scim/v2/enterprises/{tenant}"}


@router.get("/tenants")
def tenants(request: Request):
    s = _store(request)
    out = []
    for slug in s.tenant_slugs():
        out.append({"tenant": slug, **s.counts(slug), "lastActivity": s.last_activity(slug)})
    return out


@router.get("/tenants/{tenant}/users")
def users(request: Request, tenant: str):
    s = _store(request)
    ctx = scim.Ctx(s, tenant, _base(request, tenant))
    return [scim.render(ctx, "User", r) for r in s.list_resources(tenant, "User")]


@router.get("/tenants/{tenant}/groups")
def groups(request: Request, tenant: str):
    s = _store(request)
    ctx = scim.Ctx(s, tenant, _base(request, tenant))
    return [scim.render(ctx, "Group", r) for r in s.list_resources(tenant, "Group")]


@router.get("/tenants/{tenant}/requests")
def requests(request: Request, tenant: str, limit: int = Query(100, ge=1, le=5000), method: str | None = None,
             status: str | None = None, resource: str | None = None):
    return _store(request).query_log(tenant, limit, method, status, resource)


@router.delete("/tenants/{tenant}/requests", status_code=204)
def clear_requests(request: Request, tenant: str):
    _store(request).clear_log(tenant)


@router.get("/tenants/{tenant}/summary")
def summary(request: Request, tenant: str):
    s = _store(request)
    with s.lock:
        counts = s.counts(tenant)
        snap = scim.snapshot(s, tenant)
        stats = s.log_stats(tenant)
    return {"tenant": tenant, "exists": s.tenant_exists(tenant), "counts": counts, **snap, **stats,
            "faults": s.get_faults(tenant), "lastActivity": s.last_activity(tenant)}


@router.get("/tenants/{tenant}/faults")
def get_faults(request: Request, tenant: str):
    return _store(request).get_faults(tenant)


@router.put("/tenants/{tenant}/faults")
def put_faults(request: Request, tenant: str, body: dict = Body(...)):
    _slug(tenant)
    f = {}
    try:
        if "latencyMs" in body:
            f["latencyMs"] = max(0, min(int(body["latencyMs"]), 60000))
        if "errorRatePercent" in body:
            f["errorRatePercent"] = max(0.0, min(float(body["errorRatePercent"]), 100.0))
        if "rateLimitEvery" in body:
            f["rateLimitEvery"] = max(0, int(body["rateLimitEvery"]))
        if "readOnly" in body:
            f["readOnly"] = bool(body["readOnly"])
    except (TypeError, ValueError):
        raise HTTPException(400, "Fault values must be numeric / boolean")
    s = _store(request)
    current = s.get_faults(tenant)
    request.app.state.counters.pop(tenant, None)
    return s.set_faults(tenant, {**current, **f})


@router.delete("/tenants/{tenant}", status_code=204)
def delete_tenant(request: Request, tenant: str):
    _store(request).delete_tenant(tenant)
    request.app.state.counters.pop(tenant, None)
