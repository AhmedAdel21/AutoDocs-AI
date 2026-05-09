"""OpenTelemetry setup. Centralizes tracing config so main.py stays clean."""

import logging
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from fastapi import FastAPI

from app.config import get_settings
from app.db import engine


logger = logging.getLogger(__name__)


def setup_tracing(app: FastAPI) -> None:
    """Configure OpenTelemetry tracing.

    Three layers:
    1. Resource — tells the backend what service this is.
    2. TracerProvider — owns the spans.
    3. Exporters — where spans go (console in dev, OTLP in prod).
    4. Instrumentations — auto-create spans for FastAPI requests, SQLAlchemy queries, etc.
    """
    settings = get_settings()

    resource = Resource.create(
        {
            "service.name": "autodocs-api",
            "service.version": "0.1.0",
            "deployment.environment": settings.app_env,
        }
    )

    provider = TracerProvider(resource=resource)

    # In dev, dump spans to console so you can SEE them. In prod, you'd export to
    # an OTLP collector (Tempo, Jaeger, Honeycomb, Datadog, GCP Cloud Trace).
    if settings.app_env == "development":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        # Production: configure OTLP exporter via env vars
        # OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4317
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

    trace.set_tracer_provider(provider)

    # Auto-instrument FastAPI — every request becomes a span, every endpoint a child span
    FastAPIInstrumentor.instrument_app(app)

    # Auto-instrument SQLAlchemy — every query becomes a span
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)

    logger.info("OpenTelemetry tracing initialized")


# Module-level tracer — get this from any module that needs to create custom spans
tracer = trace.get_tracer("autodocs")
