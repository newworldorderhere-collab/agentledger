"""
Workflow Identity Token (WIT)
-----------------------------
A signed, propagating identity token that flows through every agent hop
in a multi-agent workflow. Carries:
  - workflow_id   : unique ID for the top-level workflow run
  - span_id       : ID for this specific agent invocation (child span)
  - parent_span_id: ID of the calling agent (None for root)
  - initiator     : who/what started the workflow (user, agent, service)
  - tenant_id     : multi-tenant namespace
  - workflow_class: logical workflow type (used for policy matching)
  - policy_tags   : arbitrary key-value metadata for policy enforcement
  - issued_at     : UTC timestamp of token creation
  - depth         : hop count from root (0 = initiating agent)
"""

import uuid
import time
import json
import hmac
import hashlib
import base64
from dataclasses import dataclass, field, asdict
from typing import Optional


# In production this comes from a secrets manager.
_SIGNING_SECRET = b"agentledger-dev-secret-change-in-prod"


@dataclass
class WorkflowIdentityToken:
    workflow_id: str
    span_id: str
    initiator: str
    tenant_id: str
    workflow_class: str
    issued_at: float
    depth: int = 0
    parent_span_id: Optional[str] = None
    policy_tags: dict = field(default_factory=dict)
    signature: Optional[str] = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        initiator: str,
        tenant_id: str,
        workflow_class: str,
        policy_tags: dict | None = None,
    ) -> "WorkflowIdentityToken":
        """Create a root WIT — call this once at workflow entry."""
        token = cls(
            workflow_id=str(uuid.uuid4()),
            span_id=str(uuid.uuid4()),
            initiator=initiator,
            tenant_id=tenant_id,
            workflow_class=workflow_class,
            issued_at=time.time(),
            depth=0,
            parent_span_id=None,
            policy_tags=policy_tags or {},
        )
        token.signature = token._sign()
        return token

    def spawn_child(self) -> "WorkflowIdentityToken":
        """
        Create a child WIT for a sub-agent invocation.
        Inherits workflow_id, initiator, tenant, class, and tags.
        Increments depth and sets parent_span_id.
        """
        child = WorkflowIdentityToken(
            workflow_id=self.workflow_id,
            span_id=str(uuid.uuid4()),
            initiator=self.initiator,
            tenant_id=self.tenant_id,
            workflow_class=self.workflow_class,
            issued_at=time.time(),
            depth=self.depth + 1,
            parent_span_id=self.span_id,
            policy_tags=dict(self.policy_tags),  # shallow copy — immutable in flight
        )
        child.signature = child._sign()
        return child

    # ------------------------------------------------------------------
    # Serialization (for HTTP headers, message queues, logs)
    # ------------------------------------------------------------------

    def to_header(self) -> str:
        """Encode WIT as a base64 JSON string suitable for an HTTP header."""
        payload = asdict(self)
        raw = json.dumps(payload, separators=(",", ":"))
        return base64.b64encode(raw.encode()).decode()

    @classmethod
    def from_header(cls, header_value: str) -> "WorkflowIdentityToken":
        """Decode and verify a WIT from a header string."""
        raw = base64.b64decode(header_value.encode()).decode()
        payload = json.loads(raw)
        token = cls(**payload)
        if not token._verify():
            raise ValueError(
                f"WIT signature verification failed for workflow_id={token.workflow_id}"
            )
        return token

    def to_dict(self) -> dict:
        return asdict(self)

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def _signable_payload(self) -> str:
        """Canonical string of fields that must not be tampered with."""
        return "|".join([
            self.workflow_id,
            self.span_id,
            self.initiator,
            self.tenant_id,
            self.workflow_class,
            str(self.issued_at),
            str(self.depth),
            self.parent_span_id or "",
        ])

    def _sign(self) -> str:
        payload = self._signable_payload().encode()
        mac = hmac.new(_SIGNING_SECRET, payload, hashlib.sha256)
        return mac.hexdigest()

    def _verify(self) -> bool:
        expected = self._sign()
        return hmac.compare_digest(expected, self.signature or "")

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"WIT(workflow={self.workflow_id[:8]}… "
            f"span={self.span_id[:8]}… "
            f"depth={self.depth} "
            f"initiator={self.initiator} "
            f"class={self.workflow_class})"
        )
