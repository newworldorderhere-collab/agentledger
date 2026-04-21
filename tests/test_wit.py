"""
Tests for Workflow Identity Token (WIT)

Validates:
  - Root WIT creation and signing
  - Child WIT inheritance across hops
  - Depth counter increments correctly
  - Causal chain (workflow_id stable, span_ids unique)
  - Header serialization round-trip
  - Tampered tokens are rejected
  - Context var propagation
"""

import pytest
from agentledger.wit import WorkflowIdentityToken, wit_context, get_current_wit


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def test_root_wit_creates_valid_token():
    token = WorkflowIdentityToken.create(
        initiator="user:priyanka",
        tenant_id="tenant:sciencelogic",
        workflow_class="incident_triage",
    )
    assert token.workflow_id
    assert token.span_id
    assert token.depth == 0
    assert token.parent_span_id is None
    assert token.initiator == "user:priyanka"
    assert token.tenant_id == "tenant:sciencelogic"
    assert token.workflow_class == "incident_triage"
    assert token.signature


def test_root_wit_signature_is_valid():
    token = WorkflowIdentityToken.create(
        initiator="user:test",
        tenant_id="tenant:x",
        workflow_class="standard",
    )
    assert token._verify()


# ---------------------------------------------------------------------------
# Child WIT propagation
# ---------------------------------------------------------------------------

def test_child_wit_inherits_workflow_id():
    root = WorkflowIdentityToken.create("user:a", "tenant:a", "class:a")
    child = root.spawn_child()
    assert child.workflow_id == root.workflow_id


def test_child_wit_has_new_span_id():
    root = WorkflowIdentityToken.create("user:a", "tenant:a", "class:a")
    child = root.spawn_child()
    assert child.span_id != root.span_id


def test_child_wit_parent_span_id_is_root_span_id():
    root = WorkflowIdentityToken.create("user:a", "tenant:a", "class:a")
    child = root.spawn_child()
    assert child.parent_span_id == root.span_id


def test_depth_increments_across_hops():
    root = WorkflowIdentityToken.create("user:a", "tenant:a", "class:a")
    child1 = root.spawn_child()
    child2 = child1.spawn_child()
    child3 = child2.spawn_child()
    assert root.depth == 0
    assert child1.depth == 1
    assert child2.depth == 2
    assert child3.depth == 3


def test_three_hop_causal_chain():
    """Simulates DataFetcher → Reasoner → ActionExecutor chain."""
    root = WorkflowIdentityToken.create("user:demo", "tenant:acme", "incident_triage")

    data_fetcher_wit = root.spawn_child()
    reasoner_wit = data_fetcher_wit.spawn_child()
    action_wit = reasoner_wit.spawn_child()

    # All share the same workflow_id
    wids = {root.workflow_id, data_fetcher_wit.workflow_id,
            reasoner_wit.workflow_id, action_wit.workflow_id}
    assert len(wids) == 1

    # All span_ids are unique
    sids = {root.span_id, data_fetcher_wit.span_id,
            reasoner_wit.span_id, action_wit.span_id}
    assert len(sids) == 4

    # Parent chain is intact
    assert data_fetcher_wit.parent_span_id == root.span_id
    assert reasoner_wit.parent_span_id == data_fetcher_wit.span_id
    assert action_wit.parent_span_id == reasoner_wit.span_id


def test_child_inherits_policy_tags():
    root = WorkflowIdentityToken.create(
        "user:a", "tenant:a", "class:a",
        policy_tags={"spend_cap_usd": "1.00", "environment": "prod"}
    )
    child = root.spawn_child()
    assert child.policy_tags == {"spend_cap_usd": "1.00", "environment": "prod"}


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_header_round_trip():
    original = WorkflowIdentityToken.create("user:b", "tenant:b", "class:b")
    header = original.to_header()
    recovered = WorkflowIdentityToken.from_header(header)
    assert recovered.workflow_id == original.workflow_id
    assert recovered.span_id == original.span_id
    assert recovered.initiator == original.initiator
    assert recovered.depth == original.depth
    assert recovered.signature == original.signature


def test_tampered_header_raises():
    token = WorkflowIdentityToken.create("user:c", "tenant:c", "class:c")
    header = token.to_header()

    # Decode, tamper, re-encode
    import base64, json
    raw = json.loads(base64.b64decode(header.encode()).decode())
    raw["initiator"] = "attacker:evil"
    tampered = base64.b64encode(json.dumps(raw).encode()).decode()

    with pytest.raises(ValueError, match="signature verification failed"):
        WorkflowIdentityToken.from_header(tampered)


# ---------------------------------------------------------------------------
# Context propagation
# ---------------------------------------------------------------------------

def test_wit_context_sets_and_restores():
    outer = WorkflowIdentityToken.create("user:outer", "tenant:t", "class:x")
    inner = outer.spawn_child()

    assert get_current_wit() is None

    with wit_context(outer):
        assert get_current_wit().span_id == outer.span_id

        with wit_context(inner):
            assert get_current_wit().span_id == inner.span_id

        # Restored after inner exits
        assert get_current_wit().span_id == outer.span_id

    # Restored after outer exits
    assert get_current_wit() is None


def test_wit_context_yields_token():
    token = WorkflowIdentityToken.create("user:y", "tenant:y", "class:y")
    with wit_context(token) as t:
        assert t is token
