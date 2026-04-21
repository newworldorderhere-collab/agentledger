"""
Tests for the three-agent pipeline end-to-end

Validates:
  - All three agents run and produce output
  - WIT propagates with correct workflow_id across all agents
  - Depth increments correctly through the chain
  - OTel spans are emitted for each agent hop
  - Cost summary attributes are present and non-negative
  - Every span references the same workflow_id
"""

import pytest
from agentledger.pipeline import run_workflow
from agentledger.instrumentation import get_finished_spans, clear_spans


@pytest.fixture(autouse=True)
def reset_spans():
    """Clear the in-memory span store before each test."""
    clear_spans()
    yield
    clear_spans()


# ---------------------------------------------------------------------------
# Pipeline completion
# ---------------------------------------------------------------------------

def test_pipeline_runs_all_three_agents():
    result = run_workflow(
        user_input="What is the CPU utilization anomaly on host-42?",
        initiator="user:priyanka",
        tenant_id="tenant:sciencelogic",
        workflow_class="incident_triage",
    )
    assert result["fetch_result"] is not None
    assert result["reasoning_result"] is not None
    assert result["action_result"] is not None


def test_pipeline_produces_cost_summary():
    result = run_workflow(
        user_input="Summarize open P1 incidents for tenant ACME.",
        initiator="service:skylar-orchestrator",
        tenant_id="tenant:acme",
        workflow_class="incident_summary",
    )
    summary = result["cost_summary"]
    assert summary is not None
    assert summary["agent_hops"] == 3
    assert summary["total_tokens"] > 0
    assert summary["estimated_cost_usd"] >= 0.0


# ---------------------------------------------------------------------------
# WIT propagation across agent hops
# ---------------------------------------------------------------------------

def test_all_spans_share_workflow_id():
    result = run_workflow(
        user_input="Generate a root cause analysis for alert #8821.",
        initiator="user:demo",
        tenant_id="tenant:demo",
        workflow_class="rca",
    )
    workflow_id = result["cost_summary"]["workflow_id"]
    spans = get_finished_spans()
    pipeline_spans = [
        s for s in spans if s.attributes.get("wit.workflow_id") == workflow_id
    ]
    assert len(pipeline_spans) == 3
    for span in pipeline_spans:
        assert span.attributes["wit.workflow_id"] == workflow_id


def test_span_depths_are_sequential():
    result = run_workflow(
        user_input="Predict disk failure probability for array-7.",
        initiator="user:demo",
        tenant_id="tenant:demo",
        workflow_class="predictive",
    )
    workflow_id = result["cost_summary"]["workflow_id"]
    spans = get_finished_spans()
    pipeline_spans = sorted(
        [s for s in spans if s.attributes.get("wit.workflow_id") == workflow_id],
        key=lambda s: s.attributes.get("wit.depth", 0),
    )
    depths = [s.attributes["wit.depth"] for s in pipeline_spans]
    assert depths == [1, 2, 3], f"Expected [1,2,3], got {depths}"


def test_parent_span_chain_is_intact():
    """Verify depth-N span's parent_span_id == depth-(N-1) span's span_id."""
    result = run_workflow(
        user_input="Check SLA compliance for tenant Contoso.",
        initiator="user:demo",
        tenant_id="tenant:contoso",
        workflow_class="sla_check",
    )
    workflow_id = result["cost_summary"]["workflow_id"]
    spans = get_finished_spans()
    pipeline_spans = sorted(
        [s for s in spans if s.attributes.get("wit.workflow_id") == workflow_id],
        key=lambda s: s.attributes.get("wit.depth", 0),
    )

    for i in range(1, len(pipeline_spans)):
        parent = pipeline_spans[i - 1]
        child = pipeline_spans[i]
        assert child.attributes["wit.parent_span_id"] == parent.attributes["wit.span_id"], (
            f"Broken chain at depth {child.attributes['wit.depth']}: "
            f"expected parent_span_id={parent.attributes['wit.span_id'][:8]}…"
        )


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------

def test_two_concurrent_workflows_do_not_mix_spans():
    clear_spans()
    result_a = run_workflow("Query A", initiator="user:alice", tenant_id="tenant:alpha", workflow_class="qa")
    result_b = run_workflow("Query B", initiator="user:bob",   tenant_id="tenant:beta",  workflow_class="qa")

    wid_a = result_a["cost_summary"]["workflow_id"]
    wid_b = result_b["cost_summary"]["workflow_id"]
    assert wid_a != wid_b

    all_spans = get_finished_spans()
    spans_a = [s for s in all_spans if s.attributes.get("wit.workflow_id") == wid_a]
    spans_b = [s for s in all_spans if s.attributes.get("wit.workflow_id") == wid_b]

    assert len(spans_a) == 3
    assert len(spans_b) == 3
    # No span appears in both sets
    span_ids_a = {s.context.span_id for s in spans_a}
    span_ids_b = {s.context.span_id for s in spans_b}
    assert span_ids_a.isdisjoint(span_ids_b)
