"""
AgentLedger Tracer
------------------
Wraps OpenTelemetry to emit spans that are WIT-aware.
Each span carries:
  - workflow_id, span_id, parent_span_id, depth  (from WIT)
  - agent_name, agent_role                        (from call site)
  - token_in, token_out, estimated_cost_usd       (from LLM response)

For Phase 1, we use a lightweight in-process exporter that accumulates
spans in memory so tests and demos work without an OTel collector.
In production, swap OTEL_EXPORTER_OTLP_ENDPOINT to point at your
collector (Grafana, Honeycomb, SigNoz, etc.).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional, List

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.resources import Resource

from agentledger.wit import get_current_wit, WorkflowIdentityToken


# ---------------------------------------------------------------------------
# Global in-memory exporter (Phase 1 — swap for OTLP in production)
# ---------------------------------------------------------------------------

_exporter = InMemorySpanExporter()
_provider = TracerProvider(
    resource=Resource.create({"service.name": "agentledger"})
)
_provider.add_span_processor(SimpleSpanProcessor(_exporter))
trace.set_tracer_provider(_provider)

_tracer = trace.get_tracer("agentledger.tracer")


def get_finished_spans():
    """Return all spans collected so far (for tests and demo reporting)."""
    return _exporter.get_finished_spans()


def clear_spans():
    _exporter.clear()


# ---------------------------------------------------------------------------
# Cost estimation (token-based, Phase 1 approximation)
# ---------------------------------------------------------------------------

# USD per 1M tokens — update to match your actual model pricing
_COST_PER_1M_TOKENS: dict[str, float] = {
    "gpt-4o": 5.00,
    "gpt-4o-mini": 0.15,
    "gpt-3.5-turbo": 0.50,
    "claude-3-5-sonnet": 3.00,
    "local-llm": 0.00,  # on-prem / vLLM
    "default": 1.00,
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    rate = _COST_PER_1M_TOKENS.get(model, _COST_PER_1M_TOKENS["default"])
    return ((tokens_in + tokens_out) / 1_000_000) * rate


# ---------------------------------------------------------------------------
# Span decorator / context manager
# ---------------------------------------------------------------------------

def agent_span(
    agent_name: str,
    agent_role: str,
    model: str = "default",
    wit: Optional[WorkflowIdentityToken] = None,
):
    """
    Context manager that wraps an agent invocation in an OTel span,
    automatically tagging it with WIT attributes.

    Usage:
        with agent_span("DataFetcher", "data_fetch", model="gpt-4o") as span:
            response = llm.invoke(prompt)
            span.record_llm(tokens_in=120, tokens_out=340)
    """
    active_wit = wit or get_current_wit()

    class _SpanCtx:
        def __enter__(self):
            self._span_ctx = _tracer.start_as_current_span(
                f"agent.{agent_name}",
                attributes=_build_wit_attributes(active_wit, agent_name, agent_role, model),
            )
            self._otel_span = self._span_ctx.__enter__()
            self.tokens_in = 0
            self.tokens_out = 0
            self._model = model
            self._start = time.perf_counter()
            return self

        def record_llm(self, tokens_in: int, tokens_out: int):
            self.tokens_in = tokens_in
            self.tokens_out = tokens_out
            cost = estimate_cost(self._model, tokens_in, tokens_out)
            self._otel_span.set_attributes({
                "llm.tokens_in": tokens_in,
                "llm.tokens_out": tokens_out,
                "llm.model": self._model,
                "llm.estimated_cost_usd": round(cost, 6),
            })

        def __exit__(self, *exc_info):
            elapsed = time.perf_counter() - self._start
            self._otel_span.set_attribute("agent.duration_ms", round(elapsed * 1000, 2))
            self._span_ctx.__exit__(*exc_info)

    return _SpanCtx()


def _build_wit_attributes(
    wit: Optional[WorkflowIdentityToken],
    agent_name: str,
    agent_role: str,
    model: str,
) -> dict:
    base = {
        "agent.name": agent_name,
        "agent.role": agent_role,
        "llm.model": model,
    }
    if wit:
        base.update({
            "wit.workflow_id": wit.workflow_id,
            "wit.span_id": wit.span_id,
            "wit.parent_span_id": wit.parent_span_id or "",
            "wit.depth": wit.depth,
            "wit.initiator": wit.initiator,
            "wit.tenant_id": wit.tenant_id,
            "wit.workflow_class": wit.workflow_class,
        })
    return base
