"""SCIM HTTP endpoints, mounted under both URL shapes."""
import json
import re

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from . import scim
from .errors import ScimError


class ScimResponse(JSONResponse):
    media_type = "application/scim+json"


def base_url(request: Request, tenant: str) -> str:
    root = str(request.base_url).rstrip("/")
    if request.url.path.startswith("/scim/v2/enterprises/"):
        return f"{root}/scim/v2/enterprises/{tenant}"
    return f"{root}/t/{tenant}/scim/v2"


async def _json(request: Request):
    raw = await request.body()
    try:
        return json.loads(raw.decode("utf-8") or "null")
    except ValueError:
        raise ScimError(400, "Request body is not valid JSON", "invalidSyntax")


def _etag(version):
    return {"ETag": f'W/"{version}"'}


router = APIRouter(default_response_class=ScimResponse)


@router.get("/ServiceProviderConfig")
async def spc(request: Request, tenant: str):
    return scim.service_provider_config(base_url(request, tenant))


@router.get("/ResourceTypes")
async def resource_types(request: Request, tenant: str):
    docs = scim.resource_types_doc(base_url(request, tenant))
    return {"schemas": [scim.LIST_URN], "totalResults": len(docs), "startIndex": 1,
            "itemsPerPage": len(docs), "Resources": docs}


@router.get("/ResourceTypes/{rid}")
async def resource_type(request: Request, tenant: str, rid: str):
    for d in scim.resource_types_doc(base_url(request, tenant)):
        if d["id"].lower() == rid.lower():
            return d
    raise ScimError(404, f"ResourceType {rid} not found")


@router.get("/Schemas")
async def schemas(request: Request, tenant: str):
    docs = scim.schemas_doc(base_url(request, tenant))
    return {"schemas": [scim.LIST_URN], "totalResults": len(docs), "startIndex": 1,
            "itemsPerPage": len(docs), "Resources": docs}


@router.get("/Schemas/{sid:path}")
async def schema(request: Request, tenant: str, sid: str):
    for d in scim.schemas_doc(base_url(request, tenant)):
        if d["id"].lower() == sid.lower():
            return d
    raise ScimError(404, f"Schema {sid} not found")


def _register(rtype: str):
    ep = scim.ENDPOINT[rtype]

    @router.get(f"/{ep}", name=f"list_{ep}")
    async def list_(request: Request, tenant: str, filter: str | None = None, startIndex: int | None = None,
                    count: int | None = None, attributes: str | None = None,
                    excludedAttributes: str | None = None):
        store = request.app.state.store
        return await run_in_threadpool(
            scim.list_resources, store, tenant, rtype, base_url(request, tenant), filter, startIndex, count,
            attributes, excludedAttributes)

    @router.post(f"/{ep}", status_code=201, name=f"create_{ep}")
    async def create_(request: Request, tenant: str):
        data = await _json(request)
        res, ver = await run_in_threadpool(scim.create, request.app.state.store, tenant, rtype,
                                           base_url(request, tenant), data)
        return ScimResponse(res, 201, {**_etag(ver), "Location": res["meta"]["location"]})

    @router.get(f"/{ep}/{{rid}}", name=f"get_{ep}")
    async def get_(request: Request, tenant: str, rid: str, attributes: str | None = None,
                   excludedAttributes: str | None = None):
        res, ver = await run_in_threadpool(scim.get_one, request.app.state.store, tenant, rtype, rid,
                                           base_url(request, tenant), attributes, excludedAttributes)
        return ScimResponse(res, 200, _etag(ver))

    @router.put(f"/{ep}/{{rid}}", name=f"put_{ep}")
    async def put_(request: Request, tenant: str, rid: str):
        data = await _json(request)
        res, ver = await run_in_threadpool(scim.replace, request.app.state.store, tenant, rtype, rid,
                                           base_url(request, tenant), data)
        return ScimResponse(res, 200, _etag(ver))

    @router.patch(f"/{ep}/{{rid}}", name=f"patch_{ep}")
    async def patch_(request: Request, tenant: str, rid: str):
        data = await _json(request)
        res, ver = await run_in_threadpool(scim.patch, request.app.state.store, tenant, rtype, rid,
                                           base_url(request, tenant), data)
        return ScimResponse(res, 200, _etag(ver))

    @router.delete(f"/{ep}/{{rid}}", status_code=204, name=f"delete_{ep}")
    async def delete_(request: Request, tenant: str, rid: str):
        await run_in_threadpool(scim.delete, request.app.state.store, tenant, rtype, rid)
        return Response(status_code=204)


_register("User")
_register("Group")
