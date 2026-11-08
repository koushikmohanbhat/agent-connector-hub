"""Held calls (approvals), the audit log and linked accounts, in SQLite or Postgres (any SQLAlchemy URL).
Use `sqlite://` for an in-memory store in tests."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Column, Float, Integer, MetaData, String, Table, Text, create_engine, delete, insert, select, update
from sqlalchemy.pool import StaticPool

meta = MetaData()
approvals = Table(
    "hub_approvals", meta,
    Column("id", String(40), primary_key=True), Column("tenant", String(200), index=True), Column("profile", String(200)),
    Column("tool", String(200)), Column("args", JSON), Column("user", JSON), Column("status", String(20), index=True),
    Column("approver_role", String(200)), Column("message", Text), Column("approver", String(200)),
    Column("reason", Text), Column("result", JSON), Column("created", Float), Column("decided", Float))
audit = Table(
    "hub_audit", meta,
    Column("seq", Integer, primary_key=True, autoincrement=True), Column("id", String(40), index=True),
    Column("ts", Float, index=True), Column("tenant", String(200), index=True), Column("profile", String(200)),
    Column("client", String(200)), Column("user_id", String(200), index=True), Column("tool", String(200)),
    Column("pack", String(200)), Column("system", String(200)), Column("decision", String(40)),
    Column("approval_id", String(40)), Column("status", String(20)), Column("latency_ms", Float),
    Column("args", JSON), Column("error", Text), Column("trace_id", String(64)), Column("acting_for", String(200)))
links = Table(
    "hub_links", meta,
    Column("key", String(600), primary_key=True), Column("tenant", String(200)), Column("user_id", String(200)),
    Column("system", String(200)), Column("secret", Text), Column("updated", Float))


class Store:
    def __init__(self, url: str):
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kw: dict[str, Any] = {"future": True}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kw.update(connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.engine = create_engine(url, **kw)
        meta.create_all(self.engine)

    # approvals -----------------------------------------------------------------------------------------------------
    def hold(self, *, tenant: str, profile: str, tool: str, args: dict, user: dict, approver_role: str, message: str) -> str:
        aid = "apr_" + uuid.uuid4().hex[:20]
        with self.engine.begin() as c:
            c.execute(insert(approvals).values(id=aid, tenant=tenant, profile=profile, tool=tool, args=_jsonable(args), user=user,
                                               status="pending", approver_role=approver_role, message=message,
                                               created=time.time()))
        return aid

    def get_approval(self, aid: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(approvals).where(approvals.c.id == aid)).mappings().first()
        return dict(row) if row else None

    def list_approvals(self, tenant: str, status: str = "pending") -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(approvals).where(approvals.c.tenant == tenant, approvals.c.status == status)
                             .order_by(approvals.c.created)).mappings().all()
        return [dict(r) for r in rows]

    def decide(self, aid: str, status: str, approver: str, reason: str) -> bool:
        """Atomically move pending -> status. False if it was already decided."""
        with self.engine.begin() as c:
            n = c.execute(update(approvals).where(approvals.c.id == aid, approvals.c.status == "pending")
                          .values(status=status, approver=approver, reason=reason, decided=time.time())).rowcount
        return n == 1

    def set_result(self, aid: str, status: str, result: Any) -> None:
        with self.engine.begin() as c:
            c.execute(update(approvals).where(approvals.c.id == aid).values(status=status, result=_jsonable(result)))

    # audit ---------------------------------------------------------------------------------------------------------
    def record(self, **row: Any) -> str:
        row.setdefault("id", "aud_" + uuid.uuid4().hex[:20])
        row.setdefault("ts", time.time())
        row["args"] = _jsonable(row.get("args"))
        with self.engine.begin() as c:
            c.execute(insert(audit).values(**row))
        return row["id"]

    def audit(self, tenant: str, *, user_id: str | None = None, tool: str | None = None, limit: int = 100) -> list[dict]:
        q = select(audit).where(audit.c.tenant == tenant)
        if user_id:
            q = q.where(audit.c.user_id == user_id)
        if tool:
            q = q.where(audit.c.tool == tool)
        with self.engine.connect() as c:
            rows = c.execute(q.order_by(audit.c.seq.desc()).limit(limit)).mappings().all()
        return [{k: v for k, v in dict(r).items() if k != "seq"} for r in rows]

    # linked accounts (user_oauth) ----------------------------------------------------------------------------------
    def save_link(self, tenant: str, user_id: str, system: str, secret: str) -> None:
        key = f"{tenant}\x1f{user_id}\x1f{system}"
        with self.engine.begin() as c:
            c.execute(delete(links).where(links.c.key == key))
            c.execute(insert(links).values(key=key, tenant=tenant, user_id=user_id, system=system, secret=secret,
                                           updated=time.time()))

    def get_link(self, tenant: str, user_id: str, system: str) -> str | None:
        with self.engine.connect() as c:
            row = c.execute(select(links.c.secret).where(links.c.key == f"{tenant}\x1f{user_id}\x1f{system}")).first()
        return row[0] if row else None

    def delete_user(self, tenant: str, user_id: str) -> int:
        """Erasure: remove a user's linked accounts and pending held calls. Audit rows are kept (tokenised args)."""
        with self.engine.begin() as c:
            n = c.execute(delete(links).where(links.c.tenant == tenant, links.c.user_id == user_id)).rowcount
            pend = c.execute(select(approvals.c.id, approvals.c.user).where(approvals.c.tenant == tenant,
                                                                             approvals.c.status == "pending")).all()
            ids = [r[0] for r in pend if (r[1] or {}).get("id") == user_id]
            if ids:
                n += c.execute(delete(approvals).where(approvals.c.id.in_(ids))).rowcount
        return n


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str)) if v is not None else None
