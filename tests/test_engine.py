from __future__ import annotations

import pytest

from connector_hub.engine import ApprovalError, Hub
from connector_hub.tools import User

from .conftest import caller


async def test_pipeline_basics(hub):
    out = await hub.call("support-agent", "nope", {}, caller())
    assert out.status == "error" and "unknown tool" in out.message
    out = await hub.call("support-agent", "get_case", {"case_number": "1"}, caller())
    assert out.status == "error" and "invalid arguments" in out.message
    out = await hub.call("readonly-ops", "get_case", {"case_number": "00001001"}, caller())
    assert out.status == "ok"
    ts = await hub.toolset("readonly-ops")
    assert all(not e.tool.writes for e in ts.values())


async def test_read_only_profile_refuses_writes(project):
    (project / "profiles" / "ro.yaml").write_text("name: ro\nallow_writes: false\ntools: ['salesforce-service:*']\n")
    hub = Hub.from_path(project)
    out = await hub.call("ro", "add_case_comment", {"case_number": "00001001", "comment": "x"}, caller())
    assert out.status == "denied" and "read-only" in out.message


async def test_per_user_access_flows_through(hub):
    a = await hub.call("support-agent", "get_case", {"case_number": "00001002"}, caller("cust-001"))
    b = await hub.call("support-agent", "get_case", {"case_number": "00001002"}, caller("cust-002"))
    assert a.status == "error" and b.status == "ok"


async def test_approval_flow(hub):
    out = await hub.call("support-agent", "update_case_status", {"case_number": "00001001", "status": "Closed"},
                         caller("cust-001"))
    assert out.status == "pending_approval"
    aid = out.approval_id
    with pytest.raises(ApprovalError) as e:
        await hub.decide(aid, True, User("x", roles=["support_agent"]))
    assert e.value.status == 403
    with pytest.raises(ApprovalError) as e:
        await hub.decide(aid, True, User("lead", "other-tenant", ["support_lead"]))
    assert e.value.status == 404
    with pytest.raises(ApprovalError) as e:
        await hub.decide(aid, True, User("cust-001", roles=["support_lead"]))
    assert e.value.status == 403          # no self-approval
    res = await hub.decide(aid, True, User("lead", roles=["support_lead"]), "ok")
    assert res["status"] == "executed" and res["result"]["status"] == "Closed"
    with pytest.raises(ApprovalError) as e:
        await hub.decide(aid, False, User("lead", roles=["support_lead"]))
    assert e.value.status == 409
    case = await hub.call("support-agent", "get_case", {"case_number": "00001001"}, caller("cust-001"))
    assert case.value["status"] == "Closed"
    rows = hub.store.audit("default", tool="update_case_status")
    assert [r["status"] for r in rows] == ["ok", "held"] and rows[0]["acting_for"] == "lead"


async def test_reject(hub):
    out = await hub.call("helpdesk-agent", "create_incident",
                         {"short_description": "Payments are down", "urgency": 1, "impact": 1}, caller("emp-002"))
    res = await hub.decide(out.approval_id, False, User("lead", roles=["service_desk_lead"]), "not a P1")
    assert res["status"] == "rejected"
    assert hub.store.get_approval(out.approval_id)["status"] == "rejected"


async def test_tool_initiated_approval(hub):
    args = {"order_id": "4500001", "items": [{"item": "10", "quantity": 4}], "reason": "wrong_item"}
    out = await hub.call("support-agent", "create_return_request", args, caller("cust-001"))
    assert out.status == "pending_approval" and "sales ops" in out.message
    res = await hub.decide(out.approval_id, True, User("ops", roles=["sales_ops"]))
    assert res["status"] == "executed" and res["result"]["value"] == 64000.0


async def test_held_call_with_dates_round_trips(hub):
    args = {"policy_number": "POL-1001", "loss_date": "2026-10-03", "loss_type": "flood",
            "description": "Car submerged in basement parking", "estimated_amount": 350000}
    out = await hub.call("claims-agent", "open_claim", args, caller("cust-001"))
    assert out.status == "pending_approval"
    res = await hub.decide(out.approval_id, True, User("sup", roles=["claims_supervisor"]))
    assert res["status"] == "executed" and res["result"]["status"] == "registered"


async def test_sap_technical_user_fails_closed(project, monkeypatch):
    monkeypatch.setenv("SAP_IDENTITY", "service_account")
    monkeypatch.setenv("SAP_SVC_KEY", "k")
    p = project / "packs" / "sap-s4-orders" / "pack.yaml"
    extra = "client_id_env: SAP_CLIENT_ID\n  static_token_env: SAP_SVC_KEY"
    p.write_text(p.read_text().replace("client_id_env: SAP_CLIENT_ID", extra))
    hub = Hub.from_path(project)
    out = await hub.call("support-agent", "get_sales_order", {"order_id": "4500001"}, caller("cust-001"))
    assert out.status == "error" and "no SAP customer" in out.message


async def test_disable_and_audit_tokenises(hub):
    hub.disabled.add(("support-agent", "find_account"))
    out = await hub.call("support-agent", "find_account", {"name": "asha@example.com"}, caller())
    assert out.status == "denied"
    row = hub.store.audit("default", tool="find_account")[0]
    assert "asha@example.com" not in str(row["args"]) and row["decision"] == "disabled"


async def test_tokenize_results_and_hooks(project):
    prof = project / "profiles" / "support-agent.yaml"
    prof.write_text(prof.read_text() + "tokenize_results: true\n")
    (project / "myhooks.py").write_text("SEEN=[]\ndef on_result(v, meta):\n    return {**v, 'hooked': meta['tool']}\n"
                                        "def on_audit(row):\n    SEEN.append(row['tool'])\n")
    h = project / "hub.yaml"
    h.write_text(h.read_text().replace("hooks: {}", "hooks: {on_result: 'myhooks:on_result', on_audit: 'myhooks:on_audit'}"))
    hub = Hub.from_path(project)
    out = await hub.call("support-agent", "get_case", {"case_number": "00001001"}, caller())
    assert out.value["hooked"] == "get_case"
    import sys
    assert any("get_case" in getattr(m, "SEEN", []) for n, m in sys.modules.items() if n.endswith("myhooks"))


async def test_erasure(hub):
    out = await hub.call("support-agent", "update_case_status", {"case_number": "00001001", "status": "Closed"},
                         caller("cust-001"))
    assert hub.erase_user("default", "cust-001") == 1
    assert hub.store.get_approval(out.approval_id) is None


async def test_rate_limit_applies(hub):
    pol = hub._pack_policies["insurance-core"]
    pol.specs[-1].rate_limit = {"per_user": "2/min"}
    res = [(await hub.call("claims-agent", "get_policy", {"policy_number": "POL-1001"}, caller())).status for _ in range(3)]
    assert res == ["ok", "ok", "denied"]
