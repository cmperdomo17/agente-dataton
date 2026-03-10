# core/session_context.py — v2
#
# FIX: reemplaza contextvars.ContextVar por estado global con threading.Lock.
#
# El bug original: ContextVar aísla el estado por contexto de ejecución.
# Cuando el agente Strands/Bedrock corre add_tool_trace() en un thread
# separado (boto3 lo hace internamente), el evaluador llama get_tool_trace()
# desde el thread principal y ve una lista vacía — contextos distintos.
#
# El fix usa una lista global compartida + Lock para thread-safety.
# El evaluador y el agente ahora comparten el mismo objeto sin importar
# desde qué thread se llame.
from __future__ import annotations

import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


# ── Estado global compartido ──────────────────────────────────────────────────
_lock = threading.Lock()
_tool_trace: List["ToolTraceEvent"] = []
_session_customer_id: Optional[str] = None
_session_customer_name: Optional[str] = None


@dataclass
class ToolTraceEvent:
    ts: str
    tool: str
    input: Dict[str, Any]
    output: Dict[str, Any]


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset_session() -> None:
    """Reset session identity and tool trace. Llamar entre escenarios."""
    global _tool_trace, _session_customer_id, _session_customer_name
    with _lock:
        _tool_trace = []
        _session_customer_id = None
        _session_customer_name = None


# ── Customer identity ─────────────────────────────────────────────────────────

def set_session_customer(customer_id: str, display_name: Optional[str] = None) -> None:
    global _session_customer_id, _session_customer_name
    with _lock:
        _session_customer_id = customer_id
        if display_name is not None:
            _session_customer_name = display_name


def get_session_customer_id() -> Optional[str]:
    with _lock:
        return _session_customer_id


def get_session_customer_name() -> Optional[str]:
    with _lock:
        return _session_customer_name


def clear_session_customer() -> None:
    global _session_customer_id, _session_customer_name
    with _lock:
        _session_customer_id = None
        _session_customer_name = None


# ── Tool trace ────────────────────────────────────────────────────────────────

def add_tool_trace(tool: str, input_data: Dict[str, Any] = None, output_data: Dict[str, Any] = None) -> None:
    with _lock:
        _tool_trace.append(ToolTraceEvent(
            ts=datetime.utcnow().isoformat() + "Z",
            tool=tool,
            input=input_data or {},
            output=output_data or {},
        ))


def get_tool_trace() -> List[Dict[str, Any]]:
    with _lock:
        return [asdict(e) for e in _tool_trace]


def get_tool_trace_length() -> int:
    with _lock:
        return len(_tool_trace)


def get_tool_trace_since(start_idx: int) -> List[Dict[str, Any]]:
    with _lock:
        return [asdict(e) for e in _tool_trace[start_idx:]]


def clear_tool_trace() -> None:
    global _tool_trace
    with _lock:
        _tool_trace = []
