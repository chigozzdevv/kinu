import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Status, StatusCode
from starlette.applications import Starlette

from telemetry.redact import REDACTED, redact

_ALLOWED_ATTRIBUTES = frozenset({"agent", "channel", "provider", "skill", "stage", "tool"})
_configured = False
_meter = metrics.get_meter("kinu.operations")
_operations = _meter.create_counter(
    "kinu.operations",
    unit="{operation}",
    description="Completed Kinu operations by bounded outcome.",
)
_duration = _meter.create_histogram(
    "kinu.operation.duration",
    unit="s",
    description="Kinu operation duration.",
)


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    service: str
    environment: str
    enabled: bool
    sample_ratio: float

    @classmethod
    def from_environment(cls, service: str) -> "TelemetryConfig":
        explicit = os.getenv("KINU_TELEMETRY_ENABLED")
        enabled = _boolean(explicit) if explicit is not None else False
        ratio = float(os.getenv("KINU_TRACE_SAMPLE_RATIO", "1"))
        if not 0 < ratio <= 1:
            raise ValueError("KINU_TRACE_SAMPLE_RATIO must be greater than zero and at most one")
        return cls(
            service=service,
            environment=os.getenv("KINU_ENVIRONMENT", "development"),
            enabled=enabled,
            sample_ratio=ratio,
        )


def instrument(app: Starlette, service: str) -> TelemetryConfig:
    global _configured
    config = TelemetryConfig.from_environment(service)
    if not config.enabled:
        return config
    if _configured:
        raise RuntimeError("process telemetry is already configured")

    resource = _resource(config)
    tracer_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(config.sample_ratio)),
    )
    meter_provider = MeterProvider(resource=resource)
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    if isinstance(app, FastAPI):
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            excluded_urls="/health/live",
            exclude_spans=["receive", "send"],
        )
    else:
        app.add_middleware(
            OpenTelemetryMiddleware,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            exclude_spans=["receive", "send"],
        )
    HTTPXClientInstrumentor().instrument(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )
    _configured = True
    return config


def _resource(config: TelemetryConfig) -> Resource:
    return Resource.create(
        {
            "service.name": config.service,
            "service.namespace": config.environment,
            "service.version": os.getenv("KINU_VERSION", "local"),
            "deployment.environment.name": config.environment,
        }
    )


@contextmanager
def operation(name: str, attributes: Mapping[str, str] | None = None) -> Iterator[None]:
    safe = _attributes(attributes or {})
    started = monotonic()
    tracer = trace.get_tracer("kinu.operations")
    with tracer.start_as_current_span(name, attributes=safe) as span:
        try:
            yield
        except Exception as error:
            span.set_status(Status(StatusCode.ERROR, type(error).__name__))
            _record(name, "failed", monotonic() - started, safe)
            raise
        span.set_status(Status(StatusCode.OK))
        _record(name, "succeeded", monotonic() - started, safe)


def record(name: str, outcome: str, duration_seconds: float, **attributes: str) -> None:
    if outcome not in {"failed", "paused", "succeeded"}:
        raise ValueError("telemetry outcome is invalid")
    _record(name, outcome, duration_seconds, _attributes(attributes))


def _record(
    name: str,
    outcome: str,
    duration_seconds: float,
    attributes: Mapping[str, str],
) -> None:
    labels = {"operation": name, "outcome": outcome, **attributes}
    _operations.add(1, labels)
    _duration.record(max(duration_seconds, 0), labels)


def _attributes(values: Mapping[str, str]) -> dict[str, str]:
    if not set(values).issubset(_ALLOWED_ATTRIBUTES):
        raise ValueError("telemetry contains an unbounded attribute")
    safe = redact(dict(values))
    if (
        not isinstance(safe, dict)
        or REDACTED in safe.values()
        or not all(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{0,127}", str(value)) for value in safe.values()
        )
    ):
        raise ValueError("telemetry attributes contain sensitive material")
    return {key: str(value)[:128] for key, value in safe.items()}


def _boolean(value: str) -> bool:
    normalised = value.strip().lower()
    if normalised in {"1", "true", "yes"}:
        return True
    if normalised in {"0", "false", "no"}:
        return False
    raise ValueError("KINU_TELEMETRY_ENABLED must be a boolean")
