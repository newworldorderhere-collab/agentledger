"""
LangGraph Workflow Graph
------------------------
Wires the three agents into a sequential DAG:
  DataFetcher → Reasoner → ActionExecutor → END

The root WIT is created here at workflow entry and injected into
the initial state. Every node then spawns a child WIT, preserving
the full causal chain.
"""

from __future__ import annotations

from langgraph.graph import StateGraph, END

from agentledger.wit import WorkflowIdentityToken, wit_context
from agentledger.pipeline.agents import (
    PipelineState,
    data_fetcher_agent,
    reasoner_agent,
    action_executor_agent,
)
from agentledger.instrumentation import get_finished_spans, estimate_cost


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    graph = StateGraph(PipelineState)
    graph.add_node("data_fetcher", data_fetcher_agent)
    graph.add_node("reasoner", reasoner_agent)
    graph.add_node("action_executor", action_executor_agent)

    graph.set_entry_point("data_fetcher")
    graph.add_edge("data_fetcher", "reasoner")
    graph.add_edge("reasoner", "action_executor")
    graph.add_edge("action_executor", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Run helper
# ---------------------------------------------------------------------------

def run_workflow(
    user_input: str,
    initiator: str = "user:anonymous",
    tenant_id: str = "tenant:default",
    workflow_class: str = "standard",
    policy_tags: dict | None = None,
) -> dict:
    """
    Entry point for a full workflow run.
    Creates the root WIT, runs the graph, and returns a cost summary.
    """

    "1. creates root_wit and prints initial info"
    root_wit = WorkflowIdentityToken.create(
        initiator=initiator,
        tenant_id=tenant_id,
        workflow_class=workflow_class,
        policy_tags=policy_tags or {},
    )

    print(f"\n{'='*60}")
    print(f"AgentLedger — Workflow Run")
    print(f"{'='*60}")
    print(f"  workflow_id   : {root_wit.workflow_id}")
    print(f"  span_id   : {root_wit.span_id}")
    print(f"  initiator     : {root_wit.initiator}")
    print(f"  tenant        : {root_wit.tenant_id}")
    print(f"  class         : {root_wit.workflow_class}")
    print(f" token signature : {root_wit.signature[:8]}…")
    print(f"  input         : {user_input[:80]}")
    print(f"{'─'*60}")


    "2. builds graph and runs it with root_wit in context, which returns the final state"

    initial_state: PipelineState = {
        "user_input": user_input,
        "wit": root_wit,
        "fetch_result": None,
        "reasoning_result": None,
        "action_result": None,
        "cost_summary": None,
    }

    app = build_graph()

    with wit_context(root_wit):
        final_state = app.invoke(initial_state)

    "3. extracts spans for this workflow and builds a cost summary"

    # ------------------------------------------------------------------
    # Build cost summary from OTel spans
    # ------------------------------------------------------------------
    spans = get_finished_spans()
    workflow_spans = [
        s for s in spans
        "filter spans for this workflow using the wit.workflow_id attribute"
        if s.attributes.get("wit.workflow_id") == root_wit.workflow_id
    ]

    total_tokens_in = sum(s.attributes.get("llm.tokens_in", 0) for s in workflow_spans)
    total_tokens_out = sum(s.attributes.get("llm.tokens_out", 0) for s in workflow_spans)
    total_cost = sum(s.attributes.get("llm.estimated_cost_usd", 0.0) for s in workflow_spans)

    cost_summary = {
        "workflow_id": root_wit.workflow_id,
        "initiator": root_wit.initiator,
        "tenant_id": root_wit.tenant_id,
        "workflow_class": root_wit.workflow_class,
        "agent_hops": len(workflow_spans),
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_tokens": total_tokens_in + total_tokens_out,
        "estimated_cost_usd": round(total_cost, 6),
        "spans": [
            {
                "agent": s.attributes.get("agent.name"),
                "role": s.attributes.get("agent.role"),
                "model": s.attributes.get("llm.model"),
                "depth": s.attributes.get("wit.depth"),
                "span_id": s.attributes.get("wit.span_id", "")[:8],
                "parent_span_id": (s.attributes.get("wit.parent_span_id") or "")[:8] or "root",
                "tokens_in": s.attributes.get("llm.tokens_in", 0),
                "tokens_out": s.attributes.get("llm.tokens_out", 0),
                "cost_usd": s.attributes.get("llm.estimated_cost_usd", 0.0),
                "duration_ms": s.attributes.get("agent.duration_ms", 0),
            }
            print(s.attributes.get("agent.name"), s.attributes.get("wit.depth"))
            for s in workflow_spans
        ],
    }

    print(f"\n{'─'*60}")
    print(f"Cost Summary")
    print(f"{'─'*60}")
    print(f"  Agent hops    : {cost_summary['agent_hops']}")
    print(f"  Total tokens  : {cost_summary['total_tokens']} ({total_tokens_in} in / {total_tokens_out} out)")
    print(f"  Estimated cost: ${cost_summary['estimated_cost_usd']:.6f} USD")
    print(f"{'─'*60}")
    for s in cost_summary["spans"]:
        print(
            f"  {s['agent']:<18} depth={s['depth']} "
            f"tokens={s['tokens_in']}in/{s['tokens_out']}out  "
            f"${s['cost_usd']:.6f}  ({s['duration_ms']}ms)"
        )
    print(f"{'='*60}\n")

    return {**final_state, "cost_summary": cost_summary}
