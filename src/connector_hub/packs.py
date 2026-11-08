"""Loading connector packs. A pack folder holds pack.yaml, tools.py (typed tools), backend classes, fakes, policies,
conformance cases and a README. Pack modules are imported as a private package per pack, so every pack can have its
own `tools.py` and `backend.py` and use relative imports.

Refs like "backend:RestBackend" resolve inside the pack folder first; "connector_hub.emulators.salesforce:rest_app"
resolves from installed packages.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import fakes
from .config import ConfigError, PackSpec, read_yaml
from .tools import HubTool, from_function


@dataclass
class Pack:
    spec: PackSpec
    dir: Path
    tools: dict[str, HubTool] = field(default_factory=dict)
    backend: Any = None

    @property
    def name(self) -> str:
        return self.spec.name


def _pkg_name(pack_dir: Path) -> str:
    digest = hashlib.sha1(str(pack_dir.resolve()).encode()).hexdigest()[:8]  # noqa: S324 - a name, not security
    safe = re.sub(r"\W", "_", pack_dir.name)
    return f"hubpack_{safe}_{digest}"


def _ensure_package(pack_dir: Path) -> str:
    pkg = _pkg_name(pack_dir)
    if pkg not in sys.modules:
        spec = importlib.util.spec_from_loader(pkg, loader=None, is_package=True)
        mod = importlib.util.module_from_spec(spec)
        mod.__path__ = [str(pack_dir)]
        sys.modules[pkg] = mod
    return pkg


def import_ref(ref: str, pack_dir: Path | None = None) -> Any:
    mod_name, _, attr = ref.partition(":")
    if not attr:
        raise ConfigError(f"{ref!r}: expected module:attribute")
    module = None
    if pack_dir is not None and (pack_dir / (mod_name.replace(".", "/") + ".py")).is_file():
        module = importlib.import_module(f"{_ensure_package(pack_dir)}.{mod_name}")
    if module is None:
        module = importlib.import_module(mod_name)
    try:
        return getattr(module, attr)
    except AttributeError:
        raise ConfigError(f"{ref!r}: {mod_name} has no {attr}") from None


def load_pack(pack_dir: Path) -> Pack:
    data = read_yaml(pack_dir / "pack.yaml")
    pol = pack_dir / "policies.yaml"
    if pol.is_file() and "policies" not in data:
        data["policies"] = (read_yaml(pol).get("policies") or [])
    spec = PackSpec(**data)
    pack = Pack(spec, pack_dir)
    for host, ref in spec.fakes.items():
        target = import_ref(ref, pack_dir)
        fakes.register(host, target() if inspect.isfunction(target) else target)
    if spec.kind == "openapi":
        from .openapi import tools_from_spec
        for t in tools_from_spec(pack):
            pack.tools[t.name] = t
        return pack
    if spec.backend not in spec.backends:
        raise ConfigError(f"{spec.name}: backend {spec.backend!r} is not one of {', '.join(spec.backends) or 'none'}")
    cls = import_ref(spec.backends[spec.backend], pack_dir)
    pack.backend = cls(spec.settings, pack_dir)
    module = importlib.import_module(f"{_ensure_package(pack_dir)}.{spec.tools_module}")
    for obj in list(vars(module).values()):
        if callable(obj) and hasattr(obj, "__hub_tool__") and getattr(obj, "__module__", "") == module.__name__:
            t = from_function(obj, spec.name, spec.system)
            pack.tools[t.name] = t
    if not pack.tools:
        raise ConfigError(f"{spec.name}: {spec.tools_module}.py defines no @tool functions")
    return pack


def discover(packs_dir: Path, enabled: list[str] | None = None) -> dict[str, Pack]:
    if not packs_dir.is_dir():
        raise ConfigError(f"{packs_dir}: packs folder not found")
    dirs = sorted(d for d in packs_dir.iterdir() if (d / "pack.yaml").is_file())
    if enabled:
        missing = set(enabled) - {d.name for d in dirs}
        if missing:
            raise ConfigError(f"packs not found in {packs_dir}: {', '.join(sorted(missing))}")
        dirs = [d for d in dirs if d.name in enabled]
    packs = {}
    for d in dirs:
        p = load_pack(d)
        packs[p.name] = p
    return packs
