"""hub: the connector-hub command line.

  hub init [DIR]                       copy the packs, profiles and config into a new project
  hub check [DIR]                      load everything and print packs, profiles and tools
  hub dev [DIR]                        run locally on fakes with dev auth and the dev page (HUB_ENV=dev)
  hub serve [DIR]                      run the service (HUB_AUTH must be set)
  hub tools PROFILE [DIR]              list a profile's tools
  hub call PROFILE TOOL --args JSON    call a tool in-process as --user (handy with fakes)
  hub conformance [PACK ...]           run the conformance checks offline
  hub record PACK --out FILE           run a pack's read calls against the configured system; save tokenised results
  hub upstream PACK                    list the tools of a pack's upstream MCP server (to map tool names)
  hub generate openapi SPEC --out DIR  write a pack from an OpenAPI spec
  hub new pack NAME                    scaffold a custom pack (tools, backend, fake, policies, conformance)
  hub deploy --print TARGET            print deployment steps: aws-ecs, cloud-run, agentcore-gateway, agent-registry
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_env_file


def _assets() -> Path:
    here = Path(__file__).resolve().parent
    installed = here / "packs"
    return here if installed.is_dir() else here.parent.parent


def cmd_init(a) -> int:
    dest = Path(a.dir)
    if dest.exists() and any(dest.iterdir()) and not a.force:
        print(f"{dest} is not empty (use --force)")
        return 2
    src = _assets()
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("packs", "profiles"):
        shutil.copytree(src / name, dest / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    for f, target in (("hub.yaml", "hub.yaml"), ("templates/env.example", ".env.example"), ("templates/gitignore", ".gitignore")):
        p = src / f
        if p.is_file():
            shutil.copy(p, dest / target)
    print(f"Created {dest}. Next:\n  cd {dest}\n  hub check\n  hub dev        # http://127.0.0.1:8000 on fakes")
    return 0


def _hub(path: str):
    from .engine import Hub
    load_env_file(Path(path) / ".env")
    return Hub.from_path(path)


def cmd_check(a) -> int:
    hub = _hub(a.dir)

    async def run():
        print(f"connector-hub {__version__}")
        for n, p in hub.packs.items():
            print(f"  pack {n}: system={p.spec.system} backend={p.spec.backend} identity={p.spec.auth.identity} "
                  f"tools={len(p.tools)}")
        for n in hub.profiles:
            ts = await hub.toolset(n)
            print(f"  profile {n}: {', '.join(ts)}")
    asyncio.run(run())
    return 0


def _serve(a, dev: bool) -> int:
    import uvicorn
    if dev:
        os.environ.setdefault("HUB_ENV", "dev")
        os.environ.setdefault("HUB_AUTH", "dev")
    load_env_file(Path(a.dir) / ".env")
    from .server import create_app
    app = create_app(a.dir)
    host = a.host or ("127.0.0.1" if dev else "0.0.0.0")  # noqa: S104 - serve binds all interfaces in containers
    if dev:
        print(f"connector-hub dev on http://{host}:{a.port}  (MCP: http://{host}:{a.port}/mcp/<profile>)")
    uvicorn.run(app, host=host, port=a.port, log_level="info" if dev else "warning")
    return 0


def _caller(a):
    from .tools import Caller, User
    claims = json.loads(a.claims) if a.claims else {}
    return Caller(User(a.user, a.tenant, [r for r in (a.roles or "").split(",") if r], claims))


def cmd_tools(a) -> int:
    hub = _hub(a.dir)
    for n, e in asyncio.run(hub.toolset(a.profile)).items():
        print(f"{n}{'  [writes]' if e.tool.writes else ''}  ({e.tool.pack})\n    {e.tool.description.splitlines()[0]}")
    return 0


def cmd_call(a) -> int:
    hub = _hub(a.dir)
    out = asyncio.run(hub.call(a.profile, a.tool, json.loads(a.args or "{}"), _caller(a)))
    print(json.dumps({"status": out.status, "result": out.payload(), "approval_id": out.approval_id}, indent=2, default=str))
    return 0 if out.status in ("ok", "pending_approval") else 1


def cmd_conformance(a) -> int:
    from .conformance import check_pack
    base = Path(a.dir).resolve()
    load_env_file(base / ".env")
    packs = a.packs or sorted(d.name for d in (base / "packs").iterdir() if (d / "pack.yaml").is_file())
    failed = 0
    for name in packs:
        rep = asyncio.run(check_pack(base, name))
        print(f"{'PASS' if rep.ok else 'FAIL'} {name}")
        for c in rep.checks:
            print(f"   {'ok ' if c.ok else 'X  '} {c.name:12} {c.detail}")
        failed += not rep.ok
    return 1 if failed else 0


def cmd_record(a) -> int:
    from .conformance import record
    n = asyncio.run(record(Path(a.dir).resolve(), a.pack, Path(a.out),
                           {"id": a.user, "tenant": a.tenant, "roles": [r for r in (a.roles or "").split(",") if r]}))
    print(f"recorded {n} calls to {a.out} (tokenised; review before committing)")
    return 0


def cmd_upstream(a) -> int:
    from . import upstream
    hub = _hub(a.dir)
    p = hub.packs[a.pack]
    url = p.spec.settings.get("mcp_url")
    if not url:
        print(f"{a.pack} has no mcp_url setting")
        return 2

    async def run():
        tok = await hub.broker.token(p.spec.auth, p.spec.system, _caller(a), p.spec.auth.scopes_for("*"))
        for t in await upstream.list_tools(url, tok):
            print(f"{t['name']}\n    {t['description'].splitlines()[0] if t['description'] else ''}\n"
                  f"    args: {', '.join(t['input_schema'].get('properties', {}))}")
    asyncio.run(run())
    return 0


def cmd_generate(a) -> int:
    from .openapi import generate_pack
    ops = [o for o in (a.ops or "").split(",") if o] or None
    out = generate_pack(Path(a.spec), Path(a.out), a.system, ops, a.base_url_env)
    print(f"wrote {out}. Writes are refused until listed in openapi.allow_writes. Next: hub conformance {out.name}")
    return 0


NEW_TOOLS = '''"""{name} tools."""
from __future__ import annotations

from typing import Annotated

from pydantic import Field

from connector_hub.sdk import ToolContext, ToolError, tool


@tool
async def get_record(ctx: ToolContext, record_id: Annotated[str, Field(pattern=r"^[A-Z0-9-]{{3,20}}$")]) -> dict:
    """Get one record by id. Describe what it returns: the model chooses tools by this text."""
    return await ctx.backend.get_record(ctx, record_id)


@tool(writes=True)
async def update_note(ctx: ToolContext, record_id: Annotated[str, Field(pattern=r"^[A-Z0-9-]{{3,20}}$")],
                      note: Annotated[str, Field(min_length=1, max_length=2000)]) -> dict:
    """Add a note to a record. Put business rules that need the system's data here, before the write."""
    rec = await ctx.backend.get_record(ctx, record_id)
    if rec.get("status") == "closed":
        raise ToolError(f"record {{record_id}} is closed")
    return await ctx.backend.add_note(ctx, record_id, note)
'''
NEW_BACKEND = '''"""How the tools reach {name}. Replace the paths with the real API's."""
from __future__ import annotations

from connector_hub.sdk import check_response, http


class RestBackend:
    def __init__(self, settings, pack_dir):
        self.s = settings

    async def get_record(self, ctx, record_id):
        async with http(ctx, self.s["base_url"]) as c:
            return check_response(await c.get(f"/records/{{record_id}}"), "{name}", f"record {{record_id}} was not found")

    async def add_note(self, ctx, record_id, note):
        async with http(ctx, self.s["base_url"]) as c:
            return check_response(await c.post(f"/records/{{record_id}}/notes", json={{"note": note}}), "{name}")
'''
NEW_FAKE = '''"""A fake of {name} for offline runs: same routes as the real API, per-user access from the hub's fake tokens."""
from __future__ import annotations

import copy

from fastapi import FastAPI, HTTPException, Request

from connector_hub.fakes import read_fake_token

SEED = {{"R-001": {{"id": "R-001", "owner": "user-1", "status": "open", "notes": []}}}}


def create_app():
    app = FastAPI()
    data = {{"r": copy.deepcopy(SEED)}}
    app.state.reset = lambda: data.update(r=copy.deepcopy(SEED))

    def record(request: Request, rid: str):
        c = read_fake_token(request.headers.get("authorization"))
        if c is None:
            raise HTTPException(401)
        rec = data["r"].get(rid)
        if rec is None or rec["owner"] != c["sub"]:
            raise HTTPException(404)
        return rec

    @app.get("/records/{{rid}}")
    def get(rid: str, request: Request):
        return record(request, rid)

    @app.post("/records/{{rid}}/notes")
    async def note(rid: str, request: Request):
        rec = record(request, rid)
        rec["notes"].append((await request.json())["note"])
        return {{"id": rid, "notes": len(rec["notes"])}}
    return app
'''


def cmd_new(a) -> int:
    base = Path(a.dir) / "packs" / a.name
    if base.exists():
        print(f"{base} exists")
        return 2
    env = a.name.replace("-", "_").upper()
    base.mkdir(parents=True)
    (base / "tools.py").write_text(NEW_TOOLS.format(name=a.name), encoding="utf-8")
    (base / "backend.py").write_text(NEW_BACKEND.format(name=a.name), encoding="utf-8")
    (base / "fake.py").write_text(NEW_FAKE.format(name=a.name), encoding="utf-8")
    (base / "pack.yaml").write_text(f"""name: {a.name}
version: 0.1.0
description: Describe the system and the job these tools do.
system: {a.name.replace('-', '_')}
backend: rest
backends:
  rest: backend:RestBackend
settings:
  base_url: "${{{env}_URL:-fake://{a.name}}}"
auth:
  identity: "${{{env}_IDENTITY:-fake}}"
  token_url: "${{{env}_TOKEN_URL:-}}"
  audience: "${{{env}_AUDIENCE:-}}"
  client_id_env: {env}_CLIENT_ID
  client_secret_env: {env}_CLIENT_SECRET
  scopes:
    "*": [read]
    update_note: [read, write]
fakes:
  {a.name}: fake:create_app
validation:
  rest: implemented
""", encoding="utf-8")
    (base / "policies.yaml").write_text("policies:\n  - tool: update_note\n    deny_if: \"len(args.note) > 1000\"\n"
                                        "    message: Notes are limited to 1,000 characters.\n", encoding="utf-8")
    (base / "conformance.yaml").write_text(
        "calls:\n  - {tool: get_record, user: {id: user-1}, args: {record_id: R-001}, expect: {status: ok}}\n"
        "  - {tool: get_record, user: {id: user-2}, args: {record_id: R-001}, expect: {status: error}}\n"
        "  - {tool: update_note, user: {id: user-1}, args: {record_id: R-001, note: hello}, expect: {status: ok}}\n",
        encoding="utf-8")
    (base / "README.md").write_text(f"# {a.name}\n\nWhat this pack does, the tools, and how to set up access to the system.\n",
                                    encoding="utf-8")
    print(f"wrote {base}. Next: hub conformance {a.name}")
    return 0


DEPLOY = {
    "aws-ecs": "deploy/terraform/aws-ecs/README.md",
    "cloud-run": "deploy/terraform/gcp-cloud-run/README.md",
    "agentcore-gateway": "deploy/agentcore-gateway/README.md",
    "agent-registry": "deploy/agent-registry/README.md",
}


def cmd_deploy(a) -> int:
    root = _assets()
    p = root / DEPLOY[a.target]
    if p.is_file():
        print(p.read_text(encoding="utf-8"))
    else:
        print(f"See {DEPLOY[a.target]} in https://github.com/koushikmohanbhat/agent-connector-hub")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hub", description="connector-hub: governed MCP tools for enterprise agents")
    ap.add_argument("--version", action="version", version=f"connector-hub {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def user_args(p):
        p.add_argument("--user", default="cust-001")
        p.add_argument("--roles", default="")
        p.add_argument("--tenant", default="default")
        p.add_argument("--claims", default="", help="JSON object of extra user claims")

    p = sub.add_parser("init")
    p.add_argument("dir", nargs="?", default="connector-hub-project")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("check")
    p.add_argument("dir", nargs="?", default=".")
    p.set_defaults(fn=cmd_check)
    for name, dev in (("dev", True), ("serve", False)):
        p = sub.add_parser(name)
        p.add_argument("dir", nargs="?", default=".")
        p.add_argument("--host", default="")
        p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
        p.set_defaults(fn=lambda a, d=dev: _serve(a, d))
    p = sub.add_parser("tools")
    p.add_argument("profile")
    p.add_argument("dir", nargs="?", default=".")
    p.set_defaults(fn=cmd_tools)
    p = sub.add_parser("call")
    p.add_argument("profile")
    p.add_argument("tool")
    p.add_argument("--args", default="{}")
    p.add_argument("--dir", default=".")
    user_args(p)
    p.set_defaults(fn=cmd_call)
    p = sub.add_parser("conformance")
    p.add_argument("packs", nargs="*")
    p.add_argument("--dir", default=".")
    p.set_defaults(fn=cmd_conformance)
    p = sub.add_parser("record")
    p.add_argument("pack")
    p.add_argument("--out", required=True)
    p.add_argument("--dir", default=".")
    user_args(p)
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("upstream")
    p.add_argument("pack")
    p.add_argument("--dir", default=".")
    user_args(p)
    p.set_defaults(fn=cmd_upstream)
    g = sub.add_parser("generate").add_subparsers(dest="what", required=True)
    p = g.add_parser("openapi")
    p.add_argument("spec")
    p.add_argument("--out", required=True)
    p.add_argument("--system", required=True)
    p.add_argument("--ops", default="", help="comma-separated operationIds (default: all GET operations)")
    p.add_argument("--base-url-env", default="", help="env var for the base URL (default <SYSTEM>_API_URL)")
    p.set_defaults(fn=cmd_generate)
    n = sub.add_parser("new").add_subparsers(dest="what", required=True)
    p = n.add_parser("pack")
    p.add_argument("name")
    p.add_argument("--dir", default=".")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("deploy")
    p.add_argument("--print", dest="target", choices=list(DEPLOY), required=True)
    p.set_defaults(fn=cmd_deploy)

    a = ap.parse_args(argv)
    if getattr(a, "what", "") == "openapi" and not a.base_url_env:
        a.base_url_env = a.system.replace("-", "_").upper() + "_API_URL"
    try:
        return a.fn(a)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())



