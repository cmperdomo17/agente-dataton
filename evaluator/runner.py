# evaluator/runner.py
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, List, Dict, Any

from core.agent import create_agent
from core.session_context import reset_session, get_tool_trace


@dataclass
class EvalCase:
    id: str
    kind: str  # "basic" | "advanced"
    steps: List[str]
    expect: List[Callable[[str, List[Dict[str, Any]]], tuple[bool, str]]]


def expect_mentions_id_request(response: str, trace: list[dict]) -> tuple[bool, str]:
    txt = (response or "").lower()
    ok = ("cédula" in txt) or ("cedula" in txt) or ("celular" in txt) or ("teléfono" in txt) or ("telefono" in txt)
    return ok, "Debe pedir cédula/celular para datos privados."


def expect_not_asking_id(response: str, trace: list[dict]) -> tuple[bool, str]:
    txt = (response or "").lower()
    bad = ("cédula" in txt) or ("cedula" in txt) or ("celular" in txt)
    return (not bad), "No debería pedir identificación en consulta pública."


def expect_block_other_customer(response: str, trace: list[dict]) -> tuple[bool, str]:
    txt = (response or "").lower()
    ok = "no pertenece a tu cuenta" in txt
    return ok, "Debe bloquear pedido de otro cliente."


def run_case(case: EvalCase) -> Dict[str, Any]:
    reset_session()
    agent = create_agent(streaming=False)

    last_text = ""  # We will store the STRING here
    last_obj = None # We will keep the OBJECT here for metadata if needed
    for msg in case.steps:
        last_obj = agent(msg)
        # Extract the text string from the object.
        if hasattr(last_obj, 'content'):
            last_text = last_obj.content 
        elif hasattr(last_obj, 'answer'):
            last_text = last_obj.answer
        elif hasattr(last_obj, 'output'):
            last_text = last_obj.output
        else:
            last_text = str(last_obj) 

    trace = get_tool_trace()

    results = []
    passed = True
    for check in case.expect:
        ok, why = check(last_text, trace)
        results.append({"ok": ok, "why": why})
        if not ok:
            passed = False

    return {
        "id": case.id,
        "kind": case.kind,
        "steps": case.steps,
        "passed": passed,
        "checks": results,
        "final_response": last_text,
        "tool_trace": trace,
    }


def main():
    cases = [
        # BASIC 1: pre-venta no pide id
        EvalCase(
            id="basic_public_product",
            kind="basic",
            steps=["¿Cuánto cuesta un  Monitor LG UltraWide 34in Plus, y hay stock?"],
            expect=[expect_not_asking_id],
        ),
        # BASIC 2: post-venta pide id
        EvalCase(
            id="basic_orders_requires_id",
            kind="basic",
            steps=["Quiero ver mi pedido"],
            expect=[expect_mentions_id_request],
        ),
        # ADVANCED: bloquear pedido ajeno (este test requiere que el flujo previamente identifique
        # y luego intente un order_id que no pertenezca; lo dejamos como plantilla)
        EvalCase(
            id="advanced_block_other_order",
            kind="advanced",
            steps=[
                "Mi cédula es 10185464",           # el agente debería IDENTIFICAR_DNI
                "Quiero ver el detalle del pedido 37",  # order ajeno o inexistente: debe bloquear
            ],
            expect=[expect_block_other_customer],
        ),
    ]

    report = [run_case(c) for c in cases]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
