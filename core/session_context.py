# core/session_context.py
from __future__ import annotations

import contextvars
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Optional, Dict, List


_session_customer_id = contextvars.ContextVar("session_customer_id", default=None)
_session_customer_name = contextvars.ContextVar("session_customer_name", default=None)
_tool_trace = contextvars.ContextVar("tool_trace", default=None)


@dataclass
class ToolTraceEvent:
    ts: str
    tool: str
    input: Dict[str, Any]
    output: Dict[str, Any]


def reset_session() -> None:
    """Resetea TODO el estado de sesión (identidad + trazas)."""
    _session_customer_id.set(None)
    _session_customer_name.set(None)
    _tool_trace.set([])


def get_session_customer_id() -> Optional[str]:
    return _session_customer_id.get()


def set_session_customer(customer_id: str, display_name: Optional[str] = None) -> None:
    _session_customer_id.set(customer_id)
    if display_name:
        _session_customer_name.set(display_name)


def get_tool_trace() -> List[Dict[str, Any]]:
    trace = _tool_trace.get()
    if trace is None:
        _tool_trace.set([])
        trace = _tool_trace.get()
    # devolver copia serializable
    return [asdict(e) for e in trace]


def add_tool_trace(tool: str, input_data: Dict[str, Any], output_data: Dict[str, Any]) -> None:
    trace = _tool_trace.get()
    if trace is None:
        _tool_trace.set([])
        trace = _tool_trace.get()

    trace.append(
        ToolTraceEvent(
            ts=datetime.utcnow().isoformat() + "Z",
            tool=tool,
            input=input_data,
            output=output_data,
        )
    )
