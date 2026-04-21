"""
Three-Agent Pipeline: DataFetcher → Reasoner → ActionExecutor
--------------------------------------------------------------
Each agent:
  1. Receives the workflow state (which always carries the parent WIT)
  2. Spawns a child WIT for its own span
  3. Activates the child WIT in context (so any nested calls inherit it)
  4. Does its work (stubbed — replace with real LLM calls)
  5. Emits an OTel span with token/cost attribution

This file is intentionally LLM-agnostic in Phase 1 — the stubs return
realistic fake responses so the identity + cost machinery can be validated
without API keys. Swap stub_llm_call() for a real client in Phase 2.
"""

from __future__ import annotations

import random
import time
from typing import TypedDict, Optional

from agentledger.wit import WorkflowIdentityToken, wit_context
from agentledger.instrumentation import agent_span


# ---------------------------------------------------------------------------
# Pipeline state — the shared dict that flows through every node
# ---------------------------------------------------------------------------

class PipelineState(TypedDict):
    user_input: str
    wit: WorkflowIdentityToken        # always the *calling* agent's WIT
    fetch_result: Optional[str]
    reasoning_result: Optional[str]
    action_result: Optional[str]
    cost_summary: Optional[dict]


# ---------------------------------------------------------------------------
# Stub LLM call — replace with openai / anthropic / vllm in Phase 2
# ---------------------------------------------------------------------------

def _stub_llm_call(prompt: str, model: str = "gpt-4o-mini") -> tuple[str, int, int]:
    """
    Returns (response_text, tokens_in, tokens_out).
    Token counts are realistic approximations based on prompt length.
    """
    time.sleep(0.05)  # simulate network latency
    tokens_in = len(prompt.split()) + random.randint(10, 30)
    tokens_out = random.randint(80, 300)
    response = f"[{model}] Processed: {prompt[:60]}..."
    return response, tokens_in, tokens_out


# ---------------------------------------------------------------------------
# Agent 1: DataFetcher
# ---------------------------------------------------------------------------

def data_fetcher_agent(state: PipelineState) -> PipelineState:
    """
    Fetches relevant data for the user request.
    In production: queries a vector store, API, or database.
    """
    parent_wit: WorkflowIdentityToken = state["wit"]
    child_wit = parent_wit.spawn_child()

    with wit_context(child_wit):
        with agent_span("DataFetcher", "data_fetch", model="gpt-4o-mini") as span:
            prompt = (
                f"Fetch relevant data for the following request: {state['user_input']}"
            )
            response, t_in, t_out = _stub_llm_call(prompt, model="gpt-4o-mini")
            span.record_llm(tokens_in=t_in, tokens_out=t_out)

    print(
        f"  [DataFetcher] wit_depth={child_wit.depth} "
        f"span={child_wit.span_id[:8]}… "
        f"tokens={t_in}in/{t_out}out"
    )

    return {
        **state,
        "wit": child_wit,          # pass child WIT forward so next agent knows its parent
        "fetch_result": response,
    }


# ---------------------------------------------------------------------------
# Agent 2: Reasoner
# ---------------------------------------------------------------------------

def reasoner_agent(state: PipelineState) -> PipelineState:
    """
    Reasons over fetched data to produce a structured answer.
    In production: uses a more capable model (gpt-4o, claude-3-5-sonnet).
    """
    parent_wit: WorkflowIdentityToken = state["wit"]
    child_wit = parent_wit.spawn_child()

    with wit_context(child_wit):
        with agent_span("Reasoner", "reasoning", model="gpt-4o") as span:
            prompt = (
                f"Given this data: {state['fetch_result']}\n"
                f"Answer: {state['user_input']}"
            )
            response, t_in, t_out = _stub_llm_call(prompt, model="gpt-4o")
            span.record_llm(tokens_in=t_in, tokens_out=t_out)

    print(
        f"  [Reasoner]    wit_depth={child_wit.depth} "
        f"span={child_wit.span_id[:8]}… "
        f"tokens={t_in}in/{t_out}out"
    )

    return {
        **state,
        "wit": child_wit,
        "reasoning_result": response,
    }


# ---------------------------------------------------------------------------
# Agent 3: ActionExecutor
# ---------------------------------------------------------------------------

def action_executor_agent(state: PipelineState) -> PipelineState:
    """
    Takes the reasoning output and executes an action (API call, ticket, alert).
    In production: calls external APIs — this is where policy enforcement gates.
    """
    parent_wit: WorkflowIdentityToken = state["wit"]
    child_wit = parent_wit.spawn_child()

    with wit_context(child_wit):
        with agent_span("ActionExecutor", "action_execution", model="gpt-4o-mini") as span:
            prompt = (
                f"Execute action based on: {state['reasoning_result']}"
            )
            response, t_in, t_out = _stub_llm_call(prompt, model="gpt-4o-mini")
            span.record_llm(tokens_in=t_in, tokens_out=t_out)

    print(
        f"  [ActionExec]  wit_depth={child_wit.depth} "
        f"span={child_wit.span_id[:8]}… "
        f"tokens={t_in}in/{t_out}out"
    )

    return {
        **state,
        "wit": child_wit,
        "action_result": response,
    }
