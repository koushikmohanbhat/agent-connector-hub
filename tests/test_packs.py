"""Every shipped pack passes conformance offline; generated and scaffolded packs do too; the CLI works."""
from __future__ import annotations

import pytest

from connector_hub.cli import main
from connector_hub.conformance import check_pack

PACKS = ["salesforce-service", "servicenow-itsm", "sap-s4-orders", "insurance-core", "warranty-api"]


@pytest.mark.parametrize("pack", PACKS)
async def test_pack_conformance(root, pack):
    rep = await check_pack(root, pack)
    assert rep.ok, [(c.name, c.detail) for c in rep.checks if not c.ok]
    if pack in ("salesforce-service", "servicenow-itsm"):
        assert any(c.name == "parity" for c in rep.checks)


async def test_generated_openapi_pack(project, root):
    assert main(["generate", "openapi", str(root / "examples/openapi/warranty.yaml"), "--out",
                 str(project / "packs" / "gen-warranty"), "--system", "warranty",
                 "--ops", "getWarranty,createWarrantyClaim"]) == 0
    rep = await check_pack(project, "gen-warranty")
    assert rep.ok, [(c.name, c.detail) for c in rep.checks if not c.ok]


async def test_openapi_writes_need_allow(hub):
    from connector_hub.config import ProfileSpec
    from connector_hub.tools import User

    from .conftest import caller
    hub.profiles["w"] = ProfileSpec(name="w", tools=["warranty-api:*"])
    hub._profile_policies["w"] = hub._profile_policies["claims-agent"]
    out = await hub.call("w", "createWarrantyClaim", {"serial": "SN-1", "body": {"fault": "x", "preferred_date": "2026-11-12"}},
                         caller())
    assert out.status == "pending_approval"
    res = await hub.decide(out.approval_id, True, User("ap", roles=["approver"]))
    assert res["status"] == "failed" and "allow_writes" in res["result"]["message"]


async def test_scaffolded_pack(project):
    assert main(["new", "pack", "fleet-api", "--dir", str(project)]) == 0
    rep = await check_pack(project, "fleet-api")
    assert rep.ok, [(c.name, c.detail) for c in rep.checks if not c.ok]


def test_cli(project, tmp_path, capsys):
    assert main(["check", str(project)]) == 0
    assert "profile support-agent" in capsys.readouterr().out
    assert main(["call", "claims-agent", "get_policy", "--args", '{"policy_number": "POL-1001"}', "--dir", str(project)]) == 0
    assert '"status": "ok"' in capsys.readouterr().out
    assert main(["tools", "helpdesk-agent", str(project)]) == 0
    assert "my_incidents" in capsys.readouterr().out
    assert main(["upstream", "servicenow-itsm", "--dir", str(project)]) == 0
    assert "get_records" in capsys.readouterr().out
    out = tmp_path / "rec.jsonl"
    assert main(["record", "insurance-core", "--out", str(out), "--dir", str(project)]) == 0
    assert out.read_text().count("\n") >= 4
    assert main(["init", str(tmp_path / "new")]) == 0
    assert (tmp_path / "new" / "packs" / "salesforce-service" / "pack.yaml").is_file()
    assert main(["conformance", "insurance-core", "--dir", str(tmp_path / "new")]) == 0
