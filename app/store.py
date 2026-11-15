"""SQLite persistence: tenants, SCIM resources (JSON documents) and the request log."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

LOG_CAP = 5000
DEFAULT_FAULTS = {"latencyMs": 0, "errorRatePercent": 0, "rateLimitEvery": 0, "readOnly": False}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
  slug TEXT PRIMARY KEY, created TEXT NOT NULL, faults TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS resources (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant TEXT NOT NULL, type TEXT NOT NULL, id TEXT NOT NULL,
  user_name TEXT, external_id TEXT, display_name TEXT, active INTEGER,
  version INTEGER NOT NULL, created TEXT NOT NULL, last_modified TEXT NOT NULL,
  body TEXT NOT NULL,
  UNIQUE (tenant, type, id)
);
CREATE INDEX IF NOT EXISTS ix_res_user ON resources (tenant, type, lower(user_name));
CREATE INDEX IF NOT EXISTS ix_res_ext ON resources (tenant, type, external_id);
CREATE INDEX IF NOT EXISTS ix_res_disp ON resources (tenant, type, lower(display_name));
CREATE TABLE IF NOT EXISTS request_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, tenant TEXT NOT NULL, method TEXT, path TEXT,
  resource_type TEXT, resource_id TEXT, status INTEGER, duration_ms REAL,
  auth_ok INTEGER, request_body TEXT, response_body TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS ix_log_tenant ON request_log (tenant, id);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "userName": r["user_name"], "externalId": r["external_id"],
        "displayName": r["display_name"], "active": None if r["active"] is None else bool(r["active"]),
        "version": r["version"], "created": r["created"], "lastModified": r["last_modified"],
        "body": json.loads(r["body"]),
    }


class Store:
    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "scim-target.db")
        self.lock = threading.RLock()
        self._depth = 0
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)

    # ---- transactions -------------------------------------------------
    @contextmanager
    def tx(self):
        with self.lock:
            outer = self._depth == 0
            if outer:
                self.db.execute("BEGIN")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outer:
                    self.db.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self.db.execute("COMMIT")

    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    # ---- tenants ------------------------------------------------------
    def ensure_tenant(self, slug: str) -> dict:
        """Create the tenant on first use; return its fault settings."""
        with self.lock:
            r = self.db.execute("SELECT faults FROM tenants WHERE slug=?", (slug,)).fetchone()
            if r is None:
                self.db.execute("INSERT INTO tenants (slug, created, faults) VALUES (?,?,?)",
                                (slug, now(), json.dumps(DEFAULT_FAULTS)))
                return dict(DEFAULT_FAULTS)
            return {**DEFAULT_FAULTS, **json.loads(r["faults"] or "{}")}

    def get_faults(self, slug: str) -> dict:
        r = self.q("SELECT faults FROM tenants WHERE slug=?", (slug,))
        return {**DEFAULT_FAULTS, **json.loads(r[0]["faults"] or "{}")} if r else dict(DEFAULT_FAULTS)

    def set_faults(self, slug: str, faults: dict) -> dict:
        self.ensure_tenant(slug)
        merged = {**DEFAULT_FAULTS, **faults}
        self.x("UPDATE tenants SET faults=? WHERE slug=?", (json.dumps(merged), slug))
        return merged

    def delete_tenant(self, slug: str) -> None:
        with self.tx():
            self.x("DELETE FROM resources WHERE tenant=?", (slug,))
            self.x("DELETE FROM request_log WHERE tenant=?", (slug,))
            self.x("DELETE FROM tenants WHERE slug=?", (slug,))

    def tenant_slugs(self) -> list[str]:
        return [r["slug"] for r in self.q("SELECT slug FROM tenants ORDER BY slug")]

    def tenant_exists(self, slug: str) -> bool:
        return bool(self.q("SELECT 1 FROM tenants WHERE slug=?", (slug,)))

    # ---- resources ----------------------------------------------------
    def list_resources(self, tenant: str, rtype: str) -> list[dict]:
        return [_row(r) for r in self.q(
            "SELECT * FROM resources WHERE tenant=? AND type=? ORDER BY seq", (tenant, rtype))]

    def get_resource(self, tenant: str, rtype: str, rid: str) -> dict | None:
        r = self.q("SELECT * FROM resources WHERE tenant=? AND type=? AND id=?", (tenant, rtype, rid))
        return _row(r[0]) if r else None

    def find_conflict(self, tenant: str, rtype: str, column: str, value: str, exclude_id: str | None,
                      ci: bool) -> str | None:
        assert column in ("user_name", "external_id", "display_name")
        expr = f"lower({column})" if ci else column
        val = value.lower() if ci else value
        rows = self.q(f"SELECT id FROM resources WHERE tenant=? AND type=? AND {expr}=? AND id IS NOT ?",
                      (tenant, rtype, val, exclude_id))
        return rows[0]["id"] if rows else None

    def insert_resource(self, tenant, rtype, rid, body, active=None):
        ts = now()
        self.x("INSERT INTO resources (tenant,type,id,user_name,external_id,display_name,active,version,"
               "created,last_modified,body) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (tenant, rtype, rid, body.get("userName"), body.get("externalId"), body.get("displayName"),
                None if active is None else int(active), 1, ts, ts, json.dumps(body)))

    def update_resource(self, tenant, rtype, rid, body, version, active=None):
        self.x("UPDATE resources SET user_name=?, external_id=?, display_name=?, active=?, version=?, "
               "last_modified=?, body=? WHERE tenant=? AND type=? AND id=?",
               (body.get("userName"), body.get("externalId"), body.get("displayName"),
                None if active is None else int(active), version, now(), json.dumps(body),
                tenant, rtype, rid))

    def delete_resource(self, tenant, rtype, rid):
        self.x("DELETE FROM resources WHERE tenant=? AND type=? AND id=?", (tenant, rtype, rid))

    def counts(self, tenant: str) -> dict:
        u = self.q("SELECT count(*) n, coalesce(sum(active),0) a FROM resources WHERE tenant=? AND type='User'",
                   (tenant,))[0]
        g = self.q("SELECT count(*) n FROM resources WHERE tenant=? AND type='Group'", (tenant,))[0]
        r = self.q("SELECT count(*) n FROM request_log WHERE tenant=?", (tenant,))[0]
        return {"users": u["n"], "usersActive": u["a"], "usersInactive": u["n"] - u["a"],
                "groups": g["n"], "requests": r["n"]}

    def last_activity(self, tenant: str) -> str | None:
        a = self.q("SELECT max(ts) m FROM request_log WHERE tenant=?", (tenant,))[0]["m"]
        b = self.q("SELECT max(last_modified) m FROM resources WHERE tenant=?", (tenant,))[0]["m"]
        vals = [v for v in (a, b) if v]
        return max(vals) if vals else None

    # ---- request log --------------------------------------------------
    def log_request(self, e: dict) -> None:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO request_log (ts,tenant,method,path,resource_type,resource_id,status,duration_ms,"
                "auth_ok,request_body,response_body,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (e["ts"], e["tenant"], e["method"], e["path"], e.get("resourceType"), e.get("resourceId"),
                 e["status"], e["durationMs"], int(e["authOk"]), e.get("requestBody"), e.get("responseBody"),
                 e.get("error")))
            self.db.execute("DELETE FROM request_log WHERE id <= ?", (cur.lastrowid - LOG_CAP,))

    def query_log(self, tenant, limit=100, method=None, status=None, resource=None) -> list[dict]:
        sql, args = "SELECT * FROM request_log WHERE tenant=?", [tenant]
        if method:
            sql += " AND method=?"
            args.append(method.upper())
        if status:
            s = str(status).lower()
            if len(s) == 3 and s.endswith("xx") and s[0].isdigit():
                sql += " AND status>=? AND status<?"
                args += [int(s[0]) * 100, int(s[0]) * 100 + 100]
            elif s.isdigit():
                sql += " AND status=?"
                args.append(int(s))
        if resource:
            sql += " AND (lower(resource_type)=lower(?) OR resource_id=?)"
            args += [resource, resource]
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit), LOG_CAP)))
        return [{
            "id": r["id"], "ts": r["ts"], "tenant": r["tenant"], "method": r["method"], "path": r["path"],
            "resourceType": r["resource_type"], "resourceId": r["resource_id"], "status": r["status"],
            "durationMs": r["duration_ms"], "authOk": bool(r["auth_ok"]), "requestBody": r["request_body"],
            "responseBody": r["response_body"], "error": r["error"],
        } for r in self.q(sql, args)]

    def log_stats(self, tenant: str) -> dict:
        by_m = {r["method"]: r["n"] for r in self.q(
            "SELECT method, count(*) n FROM request_log WHERE tenant=? GROUP BY method", (tenant,))}
        by_s = {str(r["status"]): r["n"] for r in self.q(
            "SELECT status, count(*) n FROM request_log WHERE tenant=? GROUP BY status", (tenant,))}
        return {"requestsByMethod": by_m, "requestsByStatus": by_s}

    def clear_log(self, tenant: str) -> None:
        self.x("DELETE FROM request_log WHERE tenant=?", (tenant,))
