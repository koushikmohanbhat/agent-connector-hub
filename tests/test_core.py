from __future__ import annotations

from typing import Annotated, Literal

import pytest
from pydantic import Field

from connector_hub.config import ConfigError, PolicySpec, interpolate_env, reject_inline_secrets
from connector_hub.policy import PolicyError, PolicySet, compile_rule
from connector_hub.tokenize import tokenize
from connector_hub.tools import ToolContext, ToolError, _validate_schema, from_function, tool


def test_policy_rules_and_shadowing():
    ps = PolicySet([PolicySpec(tool="t", deny_if="len(args.items) > 2", message="too many"),
                    PolicySpec(tool="t", require_approval_if="args.amount > 100 and 'lead' not in user.roles",
                               approver_role="lead")])
    assert ps.evaluate("t", {"items": [1, 2, 3]}, {"id": "u", "roles": []}).action == "deny"
    assert ps.evaluate("t", {"items": [1], "amount": "150"}, {"id": "u", "roles": []}).action == "require_approval"
    assert ps.evaluate("t", {"items": [1], "amount": 150}, {"id": "u", "roles": ["lead"]}).action == "allow"
    assert ps.evaluate("t", {"items": []}, {"id": "u", "roles": []}).action == "allow"   # missing amount -> False
    assert ps.evaluate("other", {"items": [1, 2, 3]}, {"id": "u", "roles": []}).action == "allow"


@pytest.mark.parametrize("rule", ["__import__('os')", "args.__class__", "open('x')", "[x for x in args]", "lambda: 1",
                                  "os.system", "args._d"])
def test_policy_sandbox(rule):
    with pytest.raises(PolicyError):
        compile_rule(rule)


def test_rate_limit():
    ps = PolicySet([PolicySpec(tool="*", rate_limit={"per_user": "2/min"})])
    u = {"id": "u", "tenant": "t"}
    assert [ps.evaluate("x", {}, u).action for _ in range(3)] == ["allow", "allow", "rate_limited"]
    assert ps.evaluate("x", {}, {"id": "v", "tenant": "t"}).action == "allow"
    with pytest.raises(PolicyError):
        PolicySet([PolicySpec(tool="*", rate_limit={"per_user": "fast"})])


def test_env_and_secrets(monkeypatch):
    monkeypatch.setenv("A_URL", "https://x")
    assert interpolate_env({"u": "${A_URL}", "d": "${NOPE:-dflt}"}) == {"u": "https://x", "d": "dflt"}
    with pytest.raises(ConfigError):
        interpolate_env("${NOPE}")
    with pytest.raises(ConfigError, match="client_secret_env"):
        reject_inline_secrets({"auth": {"client_secret": "abc"}})
    reject_inline_secrets({"auth": {"client_secret_env": "X"}})


def test_tool_schema_and_validation():
    @tool(writes=True)
    async def do(ctx: ToolContext, n: Annotated[int, Field(ge=1, le=5)], mode: Literal["a", "b"] = "a") -> dict:
        """Does a thing with n."""
        return {"n": n}

    t = from_function(do, "p", "s")
    assert t.writes and "ctx" not in t.input_schema["properties"]
    assert t.input_schema["required"] == ["n"]
    assert t.validate({"n": 3}) == {"n": 3, "mode": "a"}
    for bad in ({"n": 9}, {}, {"n": 1, "x": 1}, {"n": 1, "mode": "c"}):
        with pytest.raises(ToolError):
            t.validate(bad)

    def nodoc(ctx: ToolContext, a: int):
        return a
    with pytest.raises(TypeError, match="docstring"):
        from_function(nodoc)

    def noctx(a: int):
        """x"""
    with pytest.raises(TypeError, match="ctx"):
        from_function(noctx)


def test_schema_validation_for_generated_tools():
    s = {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string", "enum": ["x"]}},
         "required": ["a"], "additionalProperties": False}
    assert _validate_schema(s, {"a": 1, "b": "x"})
    for bad in ({}, {"a": "1"}, {"a": True}, {"a": 1, "b": "y"}, {"a": 1, "c": 2}):
        with pytest.raises(ToolError):
            _validate_schema(s, bad)


def test_tokenize():
    out = tokenize({"note": "mail asha@example.com, PAN ABCDE1234F, call +91 9845012345, card 4111 1111 1111 1111, "
                            "aadhaar 2341 2341 2346", "n": 5})
    text = out["note"]
    assert "asha@example.com" not in text and "ABCDE1234F" not in text and "9845012345" not in text
    assert "4111 1111 1111 1111" not in text and "<EMAIL_" in text and "<CARD_" in text and out["n"] == 5
    assert tokenize("order 4500001 for 12000") == "order 4500001 for 12000"
