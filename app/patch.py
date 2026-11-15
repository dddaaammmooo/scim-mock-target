"""SCIM PatchOp (RFC 7644 3.5.2) applied to a stored resource body."""
import copy
import re

from . import filters
from .errors import ScimError

MULTI = {"emails", "phonenumbers", "addresses", "members", "roles", "groups", "x509certificates", "ims",
         "photos", "entitlements"}
READONLY = {"id", "meta", "groups"}
PATH_RE = re.compile(r"^([^\[\.]+)(\[.*\])?(?:\.(.+))?$")


def _err(status, detail, t):
    return ScimError(status, detail, t)


def _parse_path(rest: str):
    m = PATH_RE.match(rest)
    if not m:
        raise _err(400, f"Invalid path '{rest}'", "invalidPath")
    attr, flt, sub = m.group(1), m.group(2), m.group(3)
    node = filters.parse(flt[1:-1]) if flt else None
    return attr, node, sub


def _same(a, b, attr):
    if attr.lower() == "members" and isinstance(a, dict) and isinstance(b, dict):
        return a.get("value") == b.get("value")
    return a == b


def _root(doc: dict, path: str, core_urn: str, create: bool):
    """Return (root_dict | None, remaining path | None, urn_key | None)."""
    m = filters.URN_RE.match(path)
    if not m:
        return doc, path, None
    urn, rest = m.group(1), m.group(2)
    if urn.lower() == core_urn.lower():
        return doc, rest, None
    key = filters.find_key(doc, urn) or urn
    if key not in doc:
        if not create:
            return None, rest, key
        doc[key] = {}
    if not isinstance(doc[key], dict):
        raise _err(400, f"'{urn}' is not a complex attribute", "invalidPath")
    return doc[key], rest, key


def _set(doc, op, path, value, core_urn, has_value=True, check_ro=True):
    root, rest, urn_key = _root(doc, path, core_urn, create=(op != "remove"))
    if root is None:
        return
    if not rest:  # whole extension object (or core urn)
        if op == "remove":
            if urn_key:
                doc.pop(urn_key, None)
        elif isinstance(value, dict):
            for k, v in value.items():
                # keys inside an extension object are not core attributes: skip the read-only guard
                _set(root, op, k, v, core_urn, check_ro=root is doc)
        else:
            raise _err(400, "Extension value must be an object", "invalidValue")
        return
    attr, node, sub = _parse_path(rest)
    if check_ro and attr.lower() in READONLY and root is doc:
        raise _err(400, f"Attribute '{attr}' is read-only", "mutability")
    key = filters.find_key(root, attr)
    cur = root.get(key) if key else None
    key = key or attr

    if node is None:
        if op == "remove":
            if sub:
                targets = [cur] if isinstance(cur, dict) else cur if isinstance(cur, list) else []
                for t in targets:
                    if isinstance(t, dict):
                        k = filters.find_key(t, sub)
                        if k:
                            del t[k]
            elif has_value and isinstance(cur, list) and isinstance(value, list):
                cur[:] = [e for e in cur if not any(_same(e, v, attr) for v in value)]
                if not cur:
                    del root[key]
            else:
                root.pop(key, None)
            return
        if sub:
            if isinstance(cur, list):
                for e in cur:
                    if isinstance(e, dict):
                        e[sub] = value
            elif isinstance(cur, dict):
                cur[filters.find_key(cur, sub) or sub] = value
            else:
                root[key] = {sub: value}
            return
        multi = attr.lower() in MULTI or isinstance(cur, list)
        if op == "add" and multi:
            items = value if isinstance(value, list) else [value]
            lst = cur if isinstance(cur, list) else []
            for it in items:
                if not any(_same(e, it, attr) for e in lst):
                    lst.append(copy.deepcopy(it))
            root[key] = lst
        elif isinstance(cur, dict) and isinstance(value, dict) and op == "add":
            cur.update(value)
        elif op == "replace" and multi:
            root[key] = copy.deepcopy(value) if isinstance(value, list) else [value]
        else:
            root[key] = value
        return

    # value-filter path, e.g. emails[type eq "work"].value
    if not isinstance(cur, list):
        if op == "remove":
            return
        cur = []
        root[key] = cur
    matches = [e for e in cur if isinstance(e, dict) and filters.match(node, e)]
    if op == "remove":
        if sub:
            for e in matches:
                k = filters.find_key(e, sub)
                if k:
                    del e[k]
        else:
            cur[:] = [e for e in cur if not any(e is m for m in matches)]
            if not cur:
                root.pop(key, None)
        return
    if not matches:
        if node[0] == "cmp" and node[2] == "eq" and "." not in node[1]:
            new = {node[1]: node[3]}
            if sub:
                new[sub] = value
            elif isinstance(value, dict):
                new.update(value)
            cur.append(new)
            return
        root_empty = not cur
        if root_empty:
            root.pop(key, None)
        raise _err(400, f"No target matches filter in path '{path}'", "noTarget")
    for e in matches:
        if sub:
            e[sub] = value
        elif isinstance(value, dict):
            if op == "replace":
                e.clear()
            e.update(value)
        else:
            raise _err(400, "Value must be an object when path ends at a filter", "invalidValue")


def apply_patch(doc: dict, request: dict, core_urn: str) -> dict:
    """Apply a PatchOp request to a deep copy of `doc` and return the result."""
    if not isinstance(request, dict) or not isinstance(request.get("Operations"), list) \
            or not request["Operations"]:
        raise _err(400, "PatchOp request requires a non-empty 'Operations' array", "invalidSyntax")
    doc = copy.deepcopy(doc)
    for o in request["Operations"]:
        if not isinstance(o, dict):
            raise _err(400, "Each operation must be an object", "invalidSyntax")
        op = str(o.get("op", "")).lower()
        if op not in ("add", "replace", "remove"):
            raise _err(400, f"Unsupported op '{o.get('op')}'", "invalidSyntax")
        path = o.get("path")
        if path is not None and not isinstance(path, str):
            raise _err(400, "path must be a string", "invalidPath")
        if path:
            if op != "remove" and "value" not in o:
                raise _err(400, f"'{op}' requires a value", "invalidSyntax")
            _set(doc, op, path, o.get("value"), core_urn, has_value="value" in o)
        else:
            if op == "remove":
                raise _err(400, "'remove' requires a path", "noTarget")
            val = o.get("value")
            if not isinstance(val, dict):
                raise _err(400, "Operation without path requires an object value", "invalidSyntax")
            for k, v in val.items():
                _set(doc, op, k, v, core_urn)
    for k in [k for k, v in doc.items() if k.startswith("urn:") and v == {}]:
        del doc[k]
    return doc
