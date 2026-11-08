"""Optional OpenTelemetry. With the [otel] extra installed and an exporter configured the standard way
(OTEL_EXPORTER_OTLP_ENDPOINT, ADOT on AWS, the Telemetry API on Google Cloud), every tool call is an `execute_tool`
span with gen_ai.* and hub.* attributes. Arguments and results are never put on spans."""
from __future__ import annotations

import contextlib
import uuid
from typing import Any

try:  # pragma: no cover - depends on the extra
    from opentelemetry import trace as _trace
    _tracer = _trace.get_tracer("connector_hub")
except Exception:  # noqa: BLE001
    _trace = None
    _tracer = None


@contextlib.contextmanager
def span(name: str, attrs: dict[str, Any]):
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as s:  # pragma: no cover - depends on the extra
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v)
        yield s


def trace_id() -> str:
    if _trace is not None:  # pragma: no cover - depends on the extra
        ctx = _trace.get_current_span().get_span_context()
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    return uuid.uuid4().hex
