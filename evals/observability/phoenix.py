"""Optional Phoenix/OpenTelemetry tracing for evaluation trials."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def get_span_context(self) -> Any:
        return None


class PhoenixTracer:
    """Small optional adapter around Phoenix tracing.

    The adapter is deliberately no-op by default so PR evaluation does not
    require Phoenix or OpenTelemetry dependencies.
    """

    def __init__(self) -> None:
        self.enabled = os.getenv("PHOENIX_ENABLED", "false").lower() in {"1", "true", "yes"}
        self.project_name = os.getenv("PHOENIX_PROJECT_NAME", "smart-eats-agent-eval")
        self.collector_endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
        self.app_url = os.getenv("PHOENIX_APP_URL") or os.getenv("PHOENIX_BASE_URL")
        self._tracer = None
        self._missing_dependency = False
        if self.enabled:
            self._init_tracer()

    def _init_tracer(self) -> None:
        try:
            if self.collector_endpoint:
                os.environ.setdefault("PHOENIX_COLLECTOR_ENDPOINT", self.collector_endpoint)
            from phoenix.otel import register  # type: ignore

            provider = register(protocol="http/protobuf", project_name=self.project_name)
            self._tracer = provider.get_tracer(__name__)
        except Exception:
            self._missing_dependency = True
            self.enabled = False

    @contextmanager
    def trial_span(self, name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
        if not self.enabled or self._tracer is None:
            yield _NoopSpan()
            return
        with self._tracer.start_as_current_span(name) as span:
            for key, value in (attributes or {}).items():
                if value is not None:
                    span.set_attribute(key, value)
            yield span

    @staticmethod
    def set_attributes(span: Any, attributes: dict[str, Any]) -> None:
        for key, value in attributes.items():
            if value is None:
                continue
            if isinstance(value, (list, dict)):
                value = str(value)
            span.set_attribute(key, value)

    def span_reference(self, span: Any) -> str | None:
        """Return a trace URL when possible, otherwise a stable trace id."""
        try:
            context = span.get_span_context()
        except Exception:
            return None
        if not context:
            return None
        trace_id = getattr(context, "trace_id", None)
        if not trace_id:
            return None
        trace_hex = f"{trace_id:032x}" if isinstance(trace_id, int) else str(trace_id)
        if self.app_url:
            return f"{self.app_url.rstrip('/')}/traces/{trace_hex}"
        return trace_hex
