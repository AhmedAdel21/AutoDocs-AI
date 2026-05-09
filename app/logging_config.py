"""Structured logging configuration.

Why structlog over stdlib logging:
- JSON output by default — machine-parseable, log-aggregator-friendly
- Context binding — once you bind a request_id, all logs in that scope have it
- Async-safe — bind context vars per request, not globally
- Composable processors — easy to add fields like trace_id from OpenTelemetry
"""

import logging
import sys

import structlog
from opentelemetry import trace as otel_trace

from app.config import get_settings


def add_otel_trace_id(logger, method_name, event_dict):
    """Inject the active span's trace_id and span_id into log entries.

    This is the bridge between logs and traces. When you see a log line in
    Grafana/Datadog, you can click trace_id to jump to the trace. Conversely,
    a trace's logs are findable by trace_id.
    """
    span = otel_trace.get_current_span()
    if span and span.is_recording():
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def setup_logging() -> None:
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,  # request-scoped context
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            add_otel_trace_id,  # custom — adds trace_id/span_id
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            (
                structlog.dev.ConsoleRenderer()  # human-readable in dev
                if settings.app_env == "development"
                else structlog.processors.JSONRenderer()  # JSON in prod
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# Module-level logger — import this anywhere
log = structlog.get_logger("autodocs")
