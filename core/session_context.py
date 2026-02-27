# core/session_context.py
from __future__ import annotations

import contextvars
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


# Minimal contract:
# - store session identity (if the agent validates it)
# - store tool trace events
# - allow resetting state between tests
# - allow slicing trace by step for multi-turn evaluation

_session_customer_id = contextvars.ContextVar("session_customer_id", default=None)
_session_customer_name = contextvars.ContextVar("session_customer_name", default=None)
_tool_trace = contextvars.ContextVar("tool_trace", default=None)


@dataclass
class ToolTraceEvent:
    ts: str
    tool: str
    input: Dict[str, Any]
    output: Dict[str, Any]


def _ensure_trace() -> List[ToolTraceEvent]:
    trace = _tool_trace.get()
    if trace is None:
        trace = []
        _tool_trace.set(trace)
    return trace


def reset_session() -> None:
    """Reset session identity and tool trace."""
    _session_customer_id.set(None)
    _session_customer_name.set(None)
    _tool_trace.set([])


def get_session_customer_id() -> Optional[str]:
    return _session_customer_id.get()


def get_session_customer_name() -> Optional[str]:
    return _session_customer_name.get()


def set_session_customer(customer_id: str, display_name: Optional[str] = None) -> None:
    _session_customer_id.set(customer_id)
    if display_name is not None:
        _session_customer_name.set(display_name)


def clear_session_customer() -> None:
    _session_customer_id.set(None)
    _session_customer_name.set(None)


def get_tool_trace() -> List[Dict[str, Any]]:
    return [asdict(event) for event in _ensure_trace()]


def get_tool_trace_length() -> int:
    return len(_ensure_trace())


def get_tool_trace_since(start_idx: int) -> List[Dict[str, Any]]:
    trace = _ensure_trace()
    return [asdict(event) for event in trace[start_idx:]]


def clear_tool_trace() -> None:
    _tool_trace.set([])


def add_tool_trace(tool: str, input_data: Dict[str, Any], output_data: Dict[str, Any]) -> None:
    trace = _ensure_trace()
    trace.append(
        ToolTraceEvent(
            ts=datetime.utcnow().isoformat() + "Z",
            tool=tool,
            input=input_data or {},
            output=output_data or {},
        )
    )
