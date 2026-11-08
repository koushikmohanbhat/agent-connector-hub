"""CI check: an agent-starter-kit runtime binds tools from a connector hub over MCP and calls them as the kit's user.

    pip install -e . "agent-starter-kit[openai,connectors] @ git+https://github.com/koushikmohanbhat/agent-starter-kit"
    python examples/kit_integration.py
"""
import asyncio
import os
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
os.environ.update(HUB_STORE="sqlite://", HUB_AUTH="api_key", HUB_API_KEYS="kit-key", CONNECTOR_HUB_API_KEY="kit-key",
                  SESSION_STORE="memory://", CONNECTOR_HUB_PROFILE="claims-agent", CONNECTOR_HUB_PREFIX="hub_")

from connector_hub.server import create_app  # noqa: E402

s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
server = uvicorn.Server(uvicorn.Config(create_app(ROOT), host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
os.environ["CONNECTOR_HUB_URL"] = f"http://127.0.0.1:{port}"

from agent_kit.addons import add  # noqa: E402
from agent_kit.cli import kits_root  # noqa: E402
from agent_kit.runtime import AgentRuntime  # noqa: E402
from agent_kit.tools import BoundTool, RunState, ToolContext, User  # noqa: E402

kits = Path(kits_root()) / "support-agent"
kit = Path(tempfile.mkdtemp()) / "kit"
shutil.copytree(kits, kit)
add("connectors", kit)
rt = AgentRuntime.from_path(kit / "agent.yaml")
names = [t.name for t in rt.tool_specs]
assert {"lookup_order", "hub_get_policy", "hub_open_claim"} <= set(names), names


def call(name, args, user):
    spec = next(t for t in rt.tool_specs if t.name == name)
    return BoundTool(spec, ToolContext(user, "s1"), rt.policies, RunState("s1", user, 8, None)).call_sync(args)


me = User("cust-001", "default", [])
assert call("hub_get_policy", {"policy_number": "POL-1001"}, me)["status"] == "active"
assert call("hub_get_policy", {"policy_number": "POL-2001"}, me)["status"] == "error"          # someone else's
held = call("hub_open_claim", {"policy_number": "POL-1001", "loss_date": "2026-10-03", "loss_type": "flood",
                               "description": "Car submerged in basement parking", "estimated_amount": 350000}, me)
assert held["status"] == "pending_approval", held
reply = asyncio.run(rt.chat("Where is my order ORD-10001?", me))
assert "delivered" in reply.message.lower(), reply.message
print("ok:", ", ".join(n for n in names if n.startswith("hub_")))
