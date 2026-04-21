"""
WIT Context
-----------
Thread-local / contextvars-based carrier so that the active WIT is
automatically available anywhere in the call stack without explicit
parameter passing.

Usage:
    with wit_context(token):
        result = agent.run(...)
        # anywhere inside: get_current_wit()
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional
from .token import WorkflowIdentityToken


_current_wit: ContextVar[Optional[WorkflowIdentityToken]] = ContextVar(
    "current_wit", default=None
)


def get_current_wit() -> Optional[WorkflowIdentityToken]:
    """Return the WIT active in the current async/thread context."""
    return _current_wit.get()


def set_current_wit(token: WorkflowIdentityToken) -> None:
    _current_wit.set(token)


@contextmanager
def wit_context(token: WorkflowIdentityToken):
    """
    Context manager that sets the active WIT for the duration of a block.
    Automatically restores the previous WIT on exit (supports nesting).
    """
    previous = _current_wit.get()
    _current_wit.set(token)
    try:
        yield token
    finally:
        _current_wit.set(previous)
