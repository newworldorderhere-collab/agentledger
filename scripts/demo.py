"""
AgentLedger Phase 1 Demo
------------------------
Runs a single workflow and prints the full WIT causal chain + cost attribution.

Usage:
    python scripts/demo.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentledger.pipeline import run_workflow
from agentledger.instrumentation import get_finished_spans, clear_spans

clear_spans()

result = run_workflow(
    user_input=(
        "A P1 alert fired on host prod-db-07: CPU at 98% for 10 minutes. "
        "Fetch the last 24h metrics, identify root cause, and open a remediation ticket."
    ),
    initiator="user:priyanka@sciencelogic.com",
    tenant_id="tenant:sciencelogic-prod",
    workflow_class="incident_triage",
    policy_tags={
        "spend_cap_usd": "2.00",
        "environment": "production",
        "priority": "P1",
    },
)

print("Final action taken:")
print(f"  {result['action_result']}")
print()
print("All spans for this workflow run (what you'd see in Grafana/Honeycomb):")
print()

wid = result["cost_summary"]["workflow_id"]
spans = [s for s in get_finished_spans() if s.attributes.get("wit.workflow_id") == wid]

for s in sorted(spans, key=lambda x: x.attributes.get("wit.depth", 0)):
    attrs = s.attributes
    print(f"  span_id      : {attrs.get('wit.span_id','')[:8]}…")
    print(f"  parent       : {(attrs.get('wit.parent_span_id') or 'ROOT')[:8]}{'…' if attrs.get('wit.parent_span_id') else ''}")
    print(f"  agent        : {attrs.get('agent.name')} ({attrs.get('agent.role')})")
    print(f"  depth        : {attrs.get('wit.depth')}")
    print(f"  initiator    : {attrs.get('wit.initiator')}")
    print(f"  model        : {attrs.get('llm.model')}")
    print(f"  tokens       : {attrs.get('llm.tokens_in')} in / {attrs.get('llm.tokens_out')} out")
    print(f"  cost         : ${attrs.get('llm.estimated_cost_usd', 0):.6f} USD")
    print(f"  duration     : {attrs.get('agent.duration_ms')} ms")
    print()

summary = result["cost_summary"]
print(f"{'─'*50}")
print(f"TOTAL for workflow {wid[:8]}…")
print(f"  Hops    : {summary['agent_hops']}")
print(f"  Tokens  : {summary['total_tokens']} ({summary['total_tokens_in']} in / {summary['total_tokens_out']} out)")
print(f"  Cost    : ${summary['estimated_cost_usd']:.6f} USD")
print(f"  Tenant  : {summary['tenant_id']}")
print(f"  Class   : {summary['workflow_class']}")
print(f"{'─'*50}")
print()
print("Without AgentLedger, this looks like 3 unrelated LLM calls.")
print("With AgentLedger, it's one attributed, costed, identity-tagged workflow run.")
