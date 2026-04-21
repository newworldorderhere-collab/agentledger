# AgentLedger

> **Capability is scaling fast. But aligning identity, workflows, and cost into something that holds at scale is still hard. AgentLedger is the instrumentation layer that proves it — and starts to solve it.**

In a multi-agent system, a single user intent can spawn dozens of agent hops, LLM calls, and tool invocations before returning a result. There is no native way to know who is responsible for that chain of actions, bound the cost of a workflow run end-to-end, or enforce spend policies per identity without breaking orchestration. Without this layer, $0.38 in LLM spend looks like 47 unrelated API calls. Policy enforcement is impossible. Chargeback is a spreadsheet exercise.

AgentLedger is a proof-of-concept that builds the instrumentation layer from first principles: a signed propagating identity token (WIT), a LangGraph pipeline that makes identity propagation explicit by contract, and OpenTelemetry instrumentation that attributes every LLM call to the workflow, tenant, and initiator that caused it. Phase 2 routes that telemetry into ClickHouse for OLAP-speed cost attribution. Phase 3 adds a policy enforcement gate so governance becomes a runtime control plane — not an after-the-fact audit.

---

## Table of Contents

- [Why This Matters](#why-this-matters)
- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [Workflow Identity Token (WIT)](#workflow-identity-token-wit)
- [Three-Agent LangGraph Pipeline](#three-agent-langgraph-pipeline)
- [Design Decisions](#design-decisions)
- [Phase Roadmap](#phase-roadmap)
- [What's Next](#whats-next)

---

## Why This Matters

This section is written for hiring managers, CTOs, and technical interviewers who are not reading the code directly.

**The core problem is attribution, not capability.** Foundation model capability is a commodity problem — it improves predictably and is available to everyone. The hard problem is building the infrastructure layer that makes multi-agent systems governable at scale: who triggered a workflow, what it cost, whether it stayed within policy, and how to charge it back to the right team or customer.

Today, most enterprises running AI workflows have no answers to these questions. They have token dashboards. They do not have workflow-level cost attribution, identity-propagating traces, or inline policy enforcement. That gap matters more as agentic systems move from demos to production — and it matters even more in multi-tenant platforms where one tenant's unbounded workflow run should not become another tenant's bill.

AgentLedger is the instrumentation design that closes that gap. It is built at the level of abstraction where these problems live: above the LLM API, below the application, in the orchestration and identity layer where architecture decisions become irreversible.

The problem is real. I built the early version of this thinking while working on the Skylar AI platform at ScienceLogic, where the same questions — *which workflow caused this spend? whose identity is at the root of this agent chain? how do we enforce a spend cap without breaking orchestration?* — recurred across every production deployment conversation.

---

## Quick Start

```bash
# Requires Python 3.11+
git clone https://github.com/your-handle/agentledger
cd agentledger
pip install -e ".[dev]"
```

**Run the demo** (P1 incident triage workflow, full WIT causal chain + cost output):

```bash
python scripts/demo.py
```

**Run the test suite** (18 tests, no API keys required):

```bash
pytest tests/ -v
```

---

## Architecture Overview

```
User / Service
     │
     ▼
┌─────────────────────────────────────────────────────┐
│               Workflow Identity Token (WIT)         │  ← Phase 1 (complete)
│   Signed, propagating causal identity token        │
│   Carries: initiator · tenant · class · policy     │
└──────────────────┬──────────────────────────────────┘
                   │ spawn_child() at each agent hop
       ┌───────────▼───────────┐
       │      DataFetcher      │  depth=1  gpt-4o-mini
       └───────────┬───────────┘
       ┌───────────▼───────────┐
       │        Reasoner       │  depth=2  gpt-4o
       └───────────┬───────────┘
       ┌───────────▼───────────┐
       │    ActionExecutor     │  depth=3  gpt-4o-mini
       └───────────┬───────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│          Cost Attribution Engine (OTel → ClickHouse)│  ← Phase 2 (next)
│   Per-workflow token + cost rollup                  │
│   GROUP BY tenant_id  → chargeback                 │
│   GROUP BY workflow_class → budget tracking        │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│      Policy Enforcement Gate (OPA + FastAPI)        │  ← Phase 3 (planned)
│   Inline spend/identity policy enforcement          │
│   WIT policy_tags → Rego evaluation → allow/deny   │
└─────────────────────────────────────────────────────┘
```

---

## Workflow Identity Token (WIT)

The WIT is a signed, propagating identity token that flows through every agent in a multi-agent workflow. Think of it as a distributed trace ID that carries business context — the context that OpenTelemetry alone cannot provide.

### Field Reference

| Field | Type | Description |
|---|---|---|
| `workflow_id` | UUID | Stable identifier for the entire workflow run. Constant across all hops. |
| `span_id` | UUID | Identifier for this specific agent invocation. Unique per hop. |
| `parent_span_id` | UUID \| None | `span_id` of the calling agent. `None` for the root token. |
| `initiator` | string | Who or what started the workflow. E.g. `user:alice`, `service:orchestrator`. |
| `tenant_id` | string | Multi-tenant namespace. Used for cost isolation and policy scoping. |
| `workflow_class` | string | Logical workflow type for policy matching. E.g. `incident_triage`, `rca`. |
| `policy_tags` | dict | Arbitrary key-value metadata forwarded to the policy engine. E.g. `{"spend_cap_usd": "2.00", "environment": "production"}`. |
| `depth` | int | Hop count from root. `0` for the initiating context, increments with each `spawn_child()`. |
| `signature` | string | HMAC-SHA256 over the canonical fields. Tampered tokens are rejected at any depth. |

### How the Token Propagates

Every agent receives the calling agent's WIT and calls `spawn_child()` to create its own. The child inherits `workflow_id`, `initiator`, `tenant_id`, `workflow_class`, and `policy_tags`. It gets a new `span_id`, incremented `depth`, and sets `parent_span_id` to the calling agent's `span_id`. This forms a fully reconstructable causal tree.

```
workflow_id: a1b2c3d4 (stable across all hops)
─────────────────────────────────────────────────────────

Root WIT (depth=0)
  workflow_id    : a1b2c3d4-...
  span_id        : f0e1d2c3-...    ← created at workflow entry
  parent_span_id : None
  initiator      : user:priyanka@sciencelogic.com
  workflow_class : incident_triage
  depth          : 0
       │
       │  spawn_child()
       ▼
DataFetcher WIT (depth=1)
  workflow_id    : a1b2c3d4-...    ← same
  span_id        : aa11bb22-...    ← new
  parent_span_id : f0e1d2c3-...    ← root span_id
  initiator      : user:priyanka@sciencelogic.com  ← inherited
  depth          : 1
       │
       │  spawn_child()
       ▼
Reasoner WIT (depth=2)
  workflow_id    : a1b2c3d4-...    ← same
  span_id        : cc33dd44-...    ← new
  parent_span_id : aa11bb22-...    ← DataFetcher span_id
  initiator      : user:priyanka@sciencelogic.com  ← inherited
  depth          : 2
       │
       │  spawn_child()
       ▼
ActionExecutor WIT (depth=3)
  workflow_id    : a1b2c3d4-...    ← same
  span_id        : ee55ff66-...    ← new
  parent_span_id : cc33dd44-...    ← Reasoner span_id
  initiator      : user:priyanka@sciencelogic.com  ← inherited
  depth          : 3
```

### Creating and Propagating a WIT

**Create at workflow entry (once per workflow run):**

```python
from agentledger.wit import WorkflowIdentityToken, wit_context

root_wit = WorkflowIdentityToken.create(
    initiator="user:priyanka@sciencelogic.com",
    tenant_id="tenant:sciencelogic-prod",
    workflow_class="incident_triage",
    policy_tags={
        "spend_cap_usd": "2.00",
        "environment": "production",
        "priority": "P1",
    },
)
```

**Spawn a child inside each agent:**

```python
def data_fetcher_agent(state: PipelineState) -> PipelineState:
    parent_wit = state["wit"]
    child_wit = parent_wit.spawn_child()   # explicit, auditable

    with wit_context(child_wit):
        with agent_span("DataFetcher", "data_fetch", model="gpt-4o-mini") as span:
            response, t_in, t_out = call_llm(prompt)
            span.record_llm(tokens_in=t_in, tokens_out=t_out)

    return {**state, "wit": child_wit, "fetch_result": response}
```

**Transport over HTTP (for distributed agents):**

```python
# Sender
headers = {"X-WIT": child_wit.to_header()}

# Receiver — verifies HMAC before trusting
incoming_wit = WorkflowIdentityToken.from_header(request.headers["X-WIT"])
# Raises ValueError if signature is invalid
```

---

## Three-Agent LangGraph Pipeline

### Pipeline Structure

The Phase 1 pipeline wires three agents into a sequential DAG using LangGraph:

```
user_input
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  DataFetcher  (depth=1, model: gpt-4o-mini)         │
│  Fetches relevant data for the user request.        │
│  In production: vector store, database, API.        │
└──────────────────────┬───────────────────────────────┘
                       │  fetch_result + child WIT
                       ▼
┌──────────────────────────────────────────────────────┐
│  Reasoner  (depth=2, model: gpt-4o)                 │
│  Reasons over fetched data, produces structured     │
│  answer. More capable model justified by task.      │
└──────────────────────┬───────────────────────────────┘
                       │  reasoning_result + child WIT
                       ▼
┌──────────────────────────────────────────────────────┐
│  ActionExecutor  (depth=3, model: gpt-4o-mini)      │
│  Executes the action: API call, ticket, alert.      │
│  This is where Phase 3 policy gate intercepts.      │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
               cost_summary + OTel spans
```

**Why LangGraph:** LangGraph's state-based DAG model makes WIT propagation explicit — state is passed by value through each node, so the WIT is always part of the contract between agents. An agent cannot skip identity propagation by accident. This prevents the identity loss that commonly occurs in event-driven frameworks where context is carried in ambient thread locals or dropped on async boundaries.

### OTel Integration

Every agent hop emits an OpenTelemetry span tagged with the full WIT attribute set plus LLM token counts and estimated cost. In Phase 1, an in-memory exporter makes tests self-contained — no collector required. In production, set `OTEL_EXPORTER_OTLP_ENDPOINT` to route spans to Grafana, Honeycomb, or SigNoz.

Each span carries:

```
wit.workflow_id      → a1b2c3d4-...
wit.span_id          → cc33dd44-...
wit.parent_span_id   → aa11bb22-...
wit.depth            → 2
wit.initiator        → user:priyanka@sciencelogic.com
wit.tenant_id        → tenant:sciencelogic-prod
wit.workflow_class   → incident_triage
agent.name           → Reasoner
agent.role           → reasoning
llm.model            → gpt-4o
llm.tokens_in        → 67
llm.tokens_out       → 238
llm.estimated_cost_usd → 0.001525
agent.duration_ms    → 50.21
```

### Demo Output

Running `python scripts/demo.py` against a P1 incident triage workflow produces:

```
============================================================
AgentLedger — Workflow Run
============================================================
  workflow_id   : 7635f98e-3646-417b-9378-0e2932c7e78b
  initiator     : user:priyanka@sciencelogic.com
  tenant        : tenant:sciencelogic-prod
  class         : incident_triage
  input         : A P1 alert fired on host prod-db-07: CPU at 98% for 10 minutes.
────────────────────────────────────────────────────────────
  [DataFetcher] wit_depth=1  span=ea3acf30…  tokens=43in/238out
  [Reasoner]    wit_depth=2  span=570a3a9a…  tokens=67in/238out
  [ActionExec]  wit_depth=3  span=81d3f954…  tokens=35in/197out

────────────────────────────────────────────────────────────
Cost Summary
────────────────────────────────────────────────────────────
  Agent hops    : 3
  Total tokens  : 818 (145 in / 673 out)
  Estimated cost: $0.001602 USD
────────────────────────────────────────────────────────────
  DataFetcher        depth=1  tokens=43in/238out   $0.000042  (50.24ms)
  Reasoner           depth=2  tokens=67in/238out   $0.001525  (50.21ms)
  ActionExecutor     depth=3  tokens=35in/197out   $0.000035  (50.20ms)
============================================================
```

All spans for this run as they appear in a tracing backend (Grafana / Honeycomb):

```
span_id      : ea3acf30…
parent       : 73a111a3…        ← root WIT span
agent        : DataFetcher (data_fetch)
depth        : 1
initiator    : user:priyanka@sciencelogic.com
model        : gpt-4o-mini
tokens       : 43 in / 238 out
cost         : $0.000042 USD
duration     : 50.24 ms

span_id      : 570a3a9a…
parent       : ea3acf30…        ← DataFetcher span
agent        : Reasoner (reasoning)
depth        : 2
initiator    : user:priyanka@sciencelogic.com
model        : gpt-4o
tokens       : 67 in / 238 out
cost         : $0.001525 USD
duration     : 50.21 ms

span_id      : 81d3f954…
parent       : 570a3a9a…        ← Reasoner span
agent        : ActionExecutor (action_execution)
depth        : 3
initiator    : user:priyanka@sciencelogic.com
model        : gpt-4o-mini
tokens       : 35 in / 197 out
cost         : $0.000035 USD
duration     : 50.20 ms
```

Without AgentLedger, this looks like 3 unrelated LLM calls. With AgentLedger, it is one attributed, costed, identity-tagged workflow run traceable to a single initiator.

---

## Design Decisions

### 1. Why WIT and not just OpenTelemetry trace IDs?

OTel trace IDs propagate across spans and are excellent for distributed system observability. They do not carry business context. A trace ID does not know the initiating identity, the tenant namespace, the workflow class, or the spend policy. That context is required for chargeback — "which team caused this cost?" — and for policy enforcement — "is this workflow class allowed to spend more than $2.00?" WIT extends the OTel model with a business-semantic layer on top of the technical tracing layer. In production, both coexist: WIT attributes are emitted on OTel spans, so you get correlated technical and business observability in the same backend.

### 2. Why HMAC signing?

An agent three hops deep cannot be trusted to honestly report who initiated the workflow. Without signing, any intermediate agent could modify the `initiator` field to impersonate a higher-trust identity or to evade cost attribution. HMAC-SHA256 over the canonical fields — `workflow_id`, `span_id`, `initiator`, `tenant_id`, `workflow_class`, `issued_at`, `depth`, `parent_span_id` — ensures immutability. A tampered token is caught at any depth via `from_header()`. The current implementation uses a shared HMAC secret (sufficient for intra-cluster trust). Production deployments can swap to asymmetric signing (RS256) issued by an identity provider to eliminate shared-secret distribution.

### 3. Why is `spawn_child()` explicit?

The alternative — automatically propagating WIT via a global context variable and having `agent_span()` pick it up without any explicit call — would make identity propagation invisible. Invisible propagation means invisible failure modes: an agent that misconfigures its context manager silently orphans all downstream spans, and no one knows until the cost report is wrong. `spawn_child()` makes identity propagation a deliberate act. The agent author must consciously create a child WIT. This makes omissions visible in code review and auditable in the span tree: a span with no `parent_span_id` where one is expected stands out immediately.

### 4. Why Python `contextvars` for context propagation?

`contextvars.ContextVar` is correctly scoped to async tasks in Python's `asyncio` model. Thread-local storage would fail in concurrent async agent execution — two agents running concurrently in the same event loop would share a thread-local and corrupt each other's identity context. `ContextVar` gives each async task its own context, so `get_current_wit()` returns the correct WIT anywhere in the call stack — including nested tool calls, callbacks, and LangChain/LangGraph internals — without explicit parameter passing.

---

## Phase Roadmap

| Phase | Module | Status |
|---|---|---|
| 1 | WIT + LangGraph pipeline + OTel instrumentation + 18 tests | Complete |
| 2 | Cost Attribution Engine (ClickHouse sink) | In progress |
| 3 | Policy Enforcement Gate (OPA + FastAPI) | Planned |
| 4 | Dashboard (real-time cost / identity / violations) | Planned |

---

### Phase 1 — Workflow Identity + Instrumentation (Complete)

- `WorkflowIdentityToken`: signed, propagating identity token with full causal chain
- LangGraph three-agent pipeline: DataFetcher → Reasoner → ActionExecutor
- OTel instrumentation with in-memory exporter for test isolation
- Token/cost estimation per agent hop with per-model pricing table
- Header serialization for HTTP transport between distributed agents
- 18 passing tests covering WIT creation, child propagation, tamper detection, context isolation, multi-tenant span separation, and end-to-end pipeline cost attribution

---

### Phase 2 — Cost Attribution Engine (ClickHouse)

Every OTel span emitted in Phase 1 will be flushed to a `workflow_costs` table in ClickHouse via an OTLP-compatible exporter.

**Schema:**

```sql
CREATE TABLE workflow_costs (
    workflow_id       String,
    span_id           String,
    parent_span_id    Nullable(String),
    agent_name        String,
    tenant_id         String,
    workflow_class    String,
    tokens_in         UInt32,
    tokens_out        UInt32,
    model             String,
    cost_usd          Float64,
    duration_ms       Float64,
    timestamp         DateTime64(3)
) ENGINE = MergeTree()
ORDER BY (tenant_id, workflow_id, timestamp);
```

**Key queries:**

```sql
-- Per-run cost rollup: what did this workflow cost end-to-end?
SELECT workflow_id, sum(cost_usd) AS total_cost_usd, sum(tokens_in + tokens_out) AS total_tokens
FROM workflow_costs
WHERE workflow_id = 'a1b2c3d4-...'
GROUP BY workflow_id;

-- Chargeback by tenant: what did each tenant spend this month?
SELECT tenant_id, sum(cost_usd) AS monthly_cost_usd
FROM workflow_costs
WHERE timestamp >= toStartOfMonth(now())
GROUP BY tenant_id
ORDER BY monthly_cost_usd DESC;

-- Budget tracking by workflow class: which workflow types are expensive?
SELECT workflow_class, count() AS runs, avg(cost_usd) AS avg_cost, max(cost_usd) AS max_cost
FROM workflow_costs
GROUP BY workflow_class
ORDER BY avg_cost DESC;

-- Top 10 most expensive runs this week, by tenant
SELECT tenant_id, workflow_id, sum(cost_usd) AS run_cost
FROM workflow_costs
WHERE timestamp >= now() - INTERVAL 7 DAY
GROUP BY tenant_id, workflow_id
ORDER BY run_cost DESC
LIMIT 10;
```

**Why ClickHouse:** Columnar storage and vectorized aggregation make sub-second GROUP BY queries over billions of event rows practical. Time-series cost attribution at the scale of a production multi-tenant AI platform — thousands of workflow runs per hour, each with multiple spans — is exactly the workload ClickHouse is designed for. It is the same stack used for internal AI systems analytics at ScienceLogic.

**What this unlocks:** Chargeback becomes a SQL query instead of a spreadsheet. "Show me the 10 most expensive workflow runs this week, by tenant" is a 3-line query, not a manual reconciliation exercise. Engineering teams get per-class budget visibility before a workflow type starts burning through quota.

---

### Phase 3 — Policy Enforcement Gate (OPA + FastAPI)

An OPA (Open Policy Agent) sidecar evaluates spend and identity policies in Rego. A FastAPI gate sits inline before every expensive operation — the LLM call, the external API call — and requests an OPA decision before proceeding.

**Example policies in Rego:**

```rego
# workflow class incident_triage cannot exceed $2.00 per run
deny[msg] {
    input.workflow_class == "incident_triage"
    input.policy_tags.spend_cap_usd
    to_number(input.policy_tags.spend_cap_usd) < input.current_run_cost_usd
    msg := sprintf("spend cap exceeded: $%.4f > $%.4f", [input.current_run_cost_usd, to_number(input.policy_tags.spend_cap_usd)])
}

# agent role action_execution requires human approval outside business hours
deny[msg] {
    input.agent_role == "action_execution"
    not input.human_approved
    not is_business_hours
    msg := "action_execution requires human approval outside business hours"
}

# free-tier tenants capped at $0.10 per workflow
deny[msg] {
    input.tenant_tier == "free"
    input.current_run_cost_usd > 0.10
    msg := sprintf("free-tier spend cap exceeded: $%.4f", [input.current_run_cost_usd])
}
```

**Why inline enforcement matters:** The difference between governance as a dashboard and governance as a runtime control plane is enforcement timing. A dashboard tells you a workflow exceeded its budget after it spent the money. An inline gate prevents the spend before it happens. WIT `policy_tags` carry everything the policy engine needs to make a decision — the token is the policy input, and it was signed at workflow entry so it cannot be tampered with by the time it reaches the gate.

**Integration point:** The gate intercepts at `ActionExecutor` — the agent most likely to trigger expensive external operations. Phase 3 also adds a FastAPI `/authorize` endpoint that agents can call directly for pre-flight policy checks.

---

### Phase 4 — Dashboard

- Real-time cost, identity, and violation view per workflow run
- Per-tenant and per-workflow-class spend breakdown with budget utilization
- Policy violation feed: which runs were denied, which policies triggered, which tenants are approaching caps
- Exportable chargeback reports for finance/billing integration

---

## What's Next

Phase 2 is actively in development. The ClickHouse schema is finalized (see above). The next steps are:

1. Implement an OTLP span exporter that flushes WIT-tagged spans to ClickHouse on span end
2. Add a `CostAttributionClient` that the demo and tests can use to verify ClickHouse writes
3. Build the chargeback query library with the standard GROUP BY patterns documented above
4. Add Phase 2 tests: verify that a completed workflow run produces the correct row count and cost totals in ClickHouse

The goal is for Phase 2 to be independently deployable — a Docker Compose file that brings up ClickHouse, the AgentLedger pipeline, and a query interface — so the attribution layer is demonstrable without a production OTel collector or external infrastructure.

Phase 3 (OPA gate) follows Phase 2 because the policy engine needs real cost data from ClickHouse to enforce spend caps against actual accumulated spend, not just per-call estimates.

---

## Project Context

This is a portfolio proof-of-concept by a senior AI product and engineering leader working at the intersection of AI capability, workflow orchestration, and platform economics. The design patterns here — WIT, inline policy gates, OLAP-backed cost attribution — reflect real architecture decisions in production multi-tenant AI platforms, including work on the [Skylar AI platform](https://sciencelogic.com/product/skylar/) at ScienceLogic.

The codebase is intentionally lean. Phase 1 is ~600 lines of Python with no external dependencies beyond LangGraph and the OpenTelemetry SDK. Every design decision is documented and testable. The point is not to ship a product — it is to demonstrate that the hardest part of scaling multi-agent AI is not the capability layer. It is the identity, attribution, and governance layer underneath it.

---

## License

MIT
