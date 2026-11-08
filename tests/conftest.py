from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_PREFIXES = ("HUB_", "SALESFORCE_", "SERVICENOW_", "SAP_", "INSURANCE_", "WARRANTY_", "CLAIM_")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(_PREFIXES):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HUB_STORE", "sqlite://")
    from connector_hub.conformance import reset_fakes
    reset_fakes()
    yield
    reset_fakes()


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def project(tmp_path) -> Path:
    """A copy of the repo's hub project, safe to modify."""
    dest = tmp_path / "proj"
    dest.mkdir()
    for name in ("packs", "profiles"):
        shutil.copytree(ROOT / name, dest / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "hub.yaml", dest / "hub.yaml")
    return dest


@pytest.fixture
def hub(root):
    from connector_hub.engine import Hub
    return Hub.from_path(root)


def caller(uid="cust-001", roles=(), tenant="default", claims=None, token=None):
    from connector_hub.tools import Caller, User
    return Caller(User(uid, tenant, list(roles), dict(claims or {})), token)
