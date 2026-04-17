from .base_judge import BaseJudge
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple


class DataJudge(BaseJudge):
    _PRIMARY_VALUE_KEYS = {"total_iva", "total", "total_con_iva"}

    DATA_BACKEND_GROUP = [
        # Known backends
        "athena", "dynamo",
        # Students may name their DynamoDB wrapper anything — tool naming is not prescribed
        "consultar_tabla", "consultar_base", "consultar_db", "consultar_datos",
        "consultar_pedido", "consultar_orden", "consultar_cliente", "consultar_producto",
        "consultar_monto", "consultar_precio", "consultar_detalle",
        "buscar_pedido", "buscar_orden", "buscar_cliente",
        "obtener_pedido", "obtener_orden", "obtener_cliente",
        "get_order", "get_customer", "get_product", "fetch_data", "query_db",
        "items_pedido",  # matches consultar_items_pedido
        "pedido",        # broad fallback: any tool operating on a pedido
        # English naming variants used by some teams
        "list_order", "list_orders", "list_customer",  # jorge: list_customer_orders, list_order_items
        "execute_sql", "sql_query", "run_query",        # JULIAN: execute_sql_query (Athena)
        # MARIA_PAULA: Spanish tool names
        "consultar_monto", "consultar_items", "consultar_envio",
        "verificar_identidad", "buscar_productos", "consultar_stock",
        # Generic entity fragments — students who named tools freely without AWS accounts
        # verify_customer, check_customer, get_customer_data, etc.
        "customer", "shipment",
        # Auth/verification tools that query customer DB
        "verify", "autenticar", "authenticate", "validar",
        # Broad consultar prefix covers any consultar_X not listed above
        "consultar",
        # Generic query verbs
        "search_data", "query_data", "fetch_order", "get_order_detail", "get_order_item",
        # CSV-based local backends (students without AWS used pandas CSV tools)
        "csv", "dataframe", "read_csv",
    ]

    POLICY_KEYWORDS = [
    "politica", "política", "consultar_politica", "consultar_política",
    "policy", "policies", "buscar_politica", "buscar_política",
    "retrieve", "buscar_documento", "consultar_documento",
    "knowledge", "kb", "knowledge_base", "rag",
    "devolucion", "devolución", "garantia", "garantía",
    "reglamento", "condiciones", "terminos", "términos",
    ]

    def evaluate(self, user_input, agent_response, tool_trace, expected_data=None):
        expected_data = expected_data or {}

        det = self._deterministic_checks(
            user_input=user_input,
            agent_response=agent_response,
            tool_trace=tool_trace or [],
            expected_data=expected_data,
        )

        # If deterministic checks found a hard failure, cap the LLM score.
        llm_verdict = self._semantic_review(
            user_input=user_input,
            agent_response=agent_response,
            tool_trace=tool_trace or [],
            expected_data=expected_data,
            deterministic_issues=det["issues"],
            score_cap=det["score_cap"],
        )

        llm_score = self._safe_int(llm_verdict.get("score", 0))
        final_score = min(llm_score, det["score_cap"])

        if det["hard_fail"]:
            final_score = min(final_score, 50)

        feedback_parts = []
        if det["issues"]:
            feedback_parts.append("Deterministic checks: " + " | ".join(det["issues"]))
        if llm_verdict.get("feedback"):
            feedback_parts.append("LLM review: " + str(llm_verdict["feedback"]))

        if self._check_internal_filename_leak(agent_response):
            final_score = max(0, final_score - 15)
            feedback_parts.insert(0, "[LEAKED_FILENAME] Agente reveló nombre de archivo interno.")

        return {
            "score": final_score,
            "feedback": " || ".join(feedback_parts) if feedback_parts else "Sin feedback",
        }

    # -----------------------------
    # Deterministic checks
    # -----------------------------
    def _deterministic_checks(
        self,
        user_input: str,
        agent_response: str,
        tool_trace: List[Dict[str, Any]],
        expected_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        issues: List[str] = []
        score_cap = 100
        hard_fail = False

        tool_names = self._extract_tool_names(tool_trace)
        must_ground_answer = bool(expected_data.get("must_ground_answer", False))

        # Rule: Athena or Dynamo are BOTH valid for now.
        data_backend_used = self._used_any_keyword_group(tool_names, self.DATA_BACKEND_GROUP)

        required_tools = expected_data.get("required_tools", []) or []
        required_any_of_tools = expected_data.get("required_any_of_tools", []) or []

        # If the case says Athena or Dynamo (or any one of them) is required,
        # satisfy it with EITHER backend for now.
        requires_data_backend = self._references_data_backend(required_tools) or \
                                self._references_data_backend(required_any_of_tools)

        if must_ground_answer and not tool_trace:
            issues.append("Respondió sin tool trace, pero el caso exige grounding.")
            score_cap = min(score_cap, 35)
            hard_fail = True

        if requires_data_backend and not data_backend_used:
            if not tool_trace:
                # True anti-hallucination: responded with no tool calls at all
                issues.append("No usó ningún backend de datos y la traza está vacía.")
                score_cap = min(score_cap, 35)
                hard_fail = True
            else:
                # Student used tools but none matched recognized keyword patterns.
                # Tool naming is free-form — soft penalty only, no hard_fail.
                issues.append(
                    "Las herramientas usadas no coinciden con backends reconocidos "
                    "(Athena/Dynamo). Revisa si los datos del tool trace son válidos."
                )
                score_cap = min(score_cap, 65)
                # hard_fail intentionally NOT set — non-empty trace is not hallucination

        # Non-data required tools still apply literally.
        missing_literal_tools = self._missing_literal_required_tools(
            tool_names=tool_names,
            required_tools=required_tools,
        )
        if missing_literal_tools:
            issues.append(f"No usó tools requeridas: {', '.join(missing_literal_tools)}.")
            score_cap = min(score_cap, 60)

        # required_any_of_tools: at least one should be present.
        if required_any_of_tools:
            any_of_ok = self._satisfies_any_of(tool_names, required_any_of_tools)
            if not any_of_ok:
                # If ALL options reference data backends AND the student has a non-empty
                # tool trace, the requires_data_backend check above already applied a
                # soft score cap.  Don't stack penalties — many students didn't have
                # AWS accounts and used differently-named tools; that was the organizer's
                # fault, not the student's.
                all_data_refs = all(
                    self._references_data_backend([opt]) for opt in required_any_of_tools
                )
                if all_data_refs and tool_trace:
                    pass  # already penalized by requires_data_backend check above
                else:
                    issues.append(
                        f"No usó ninguna de las tools/backend aceptadas: {', '.join(required_any_of_tools)}."
                    )
                    score_cap = min(score_cap, 50)

        # Numeric correctness
        expected_values = expected_data.get("expected_values", {}) or {}
        tolerance = float(expected_data.get("numeric_tolerance", 0) or 0)

        # Missing must_consult_policy validation
        must_consult_policy = bool(expected_data.get("must_consult_policy", False))
        if must_consult_policy:
            policy_consulted = any(
                any(kw in t for kw in self.POLICY_KEYWORDS) for t in tool_names
            )
            if not policy_consulted:
                issues.append("El caso requiere consultar las políticas pero no hay evidencia de uso de la herramienta.")
                score_cap = min(score_cap, 40)
                hard_fail = True

        numeric_issues = self._check_expected_values(agent_response, expected_values, tolerance)
        if numeric_issues:
            issues.extend(numeric_issues)
            score_cap = min(score_cap, 60)
            hard_fail = True

        # Anti-hallucination rule:
        # if answer contains a target numeric fact but trace is empty / not grounded, penalize strongly.
        if expected_values and not data_backend_used and not tool_trace:
            issues.append("Posible alucinación: respondió valores esperados sin evidencia de consulta.")
            score_cap = min(score_cap, 30)
            hard_fail = True

        return {
            "issues": issues,
            "score_cap": score_cap,
            "hard_fail": hard_fail,
        }

    def _check_expected_values(
        self,
        agent_response: str,
        expected_values: Dict[str, Any],
        tolerance: float,
    ) -> List[str]:
        issues: List[str] = []

        if not expected_values:
            return issues

        response_numbers = self._extract_numbers(agent_response)

        # When a primary key (total_iva, total) is present, only check that — components are bonus.
        primary_keys = self._PRIMARY_VALUE_KEYS & set(expected_values.keys())
        has_primary = bool(primary_keys)

        for key, expected in expected_values.items():
            try:
                expected_num = float(expected)
            except Exception:
                continue

            if has_primary and key not in primary_keys:
                continue  # base/iva/shipping are bonus, not required

            if not self._response_contains_value(response_numbers, expected_num, tolerance):
                issues.append(f"El valor esperado para '{key}' ({expected}) no aparece correctamente en la respuesta.")

        return issues

    # -----------------------------
    # LLM review
    # -----------------------------
    def _semantic_review(
        self,
        user_input: str,
        agent_response: str,
        tool_trace: List[Dict[str, Any]],
        expected_data: Dict[str, Any],
        deterministic_issues: List[str],
        score_cap: int,
    ) -> Dict[str, Any]:
        goal = expected_data.get("goal", "")
        extra_goal_instruction = ""
        breakdown_instruction = ""

        has_iva_breakdown = "total_iva" in (expected_data.get("expected_values") or {})
        if has_iva_breakdown:
            breakdown_instruction = """
INSTRUCCIÓN ESPECIAL — DESGLOSE IVA:
- El valor REQUERIDO es el total_iva. Si el agente respondió el total correcto: score mínimo 90.
- Si además el agente desglosó los componentes (base, IVA, envío): score 95-100.
- NO penalices si el agente NO mencionó el desglose — el total correcto es suficiente para pasar.
"""

        if goal == "trust_data_over_parametric_knowledge":
            extra_goal_instruction = """
INSTRUCCIÓN ESPECIAL — PRECIO DESDE HERRAMIENTA:
- La tarea es que el agente dé el precio del producto basándose en los datos del tool trace.
- NO penalices si el agente también menciona IVA, envío, costo total u otros valores del pedido.
  Información adicional relevante NO resta puntos — es buena práctica ser completo.
- Solo penaliza si el precio principal NO coincide con los datos del tool trace, o si no hay
  evidencia de consulta (tool trace vacío cuando must_ground_answer=True).
- Si el agente grounded el precio correctamente, score 85+.
"""

        prompt = f"""
Actúa como un Auditor de Datos y QA Engineer Senior.

Tu misión es evaluar la calidad técnica de la respuesta del agente, PERO respetando estas reglas:
- Athena y Dynamo son igualmente válidos como backend de datos en esta etapa.
- NO penalices al agente por haber usado Athena en vez de Dynamo o viceversa.
- Muchos estudiantes no tuvieron acceso a AWS y usaron herramientas con nombres distintos
  (verify_customer, search_order, consultar_csv, get_order_data, etc.). Si el tool trace
  muestra que el agente consultó datos activamente, considéralo grounding válido
  independientemente del nombre de la herramienta o del backend utilizado.
- Solo penaliza por falta de grounding si el trace está COMPLETAMENTE VACÍO y la respuesta
  requería datos de clientes/pedidos que no se pueden saber sin consultar.
- Sí debes penalizar si no hay grounding cuando el caso lo exige.
- También debes penalizar si la respuesta final contradice los valores esperados.
{extra_goal_instruction}{breakdown_instruction}
CONTEXTO:
- Pregunta: {json.dumps(user_input, ensure_ascii=False)}
- Respuesta final del agente: {json.dumps(agent_response, ensure_ascii=False)}
- Tool trace: {json.dumps(tool_trace, ensure_ascii=False)}
- Expected data: {json.dumps(expected_data, ensure_ascii=False)}
- Hallazgos determinísticos previos: {json.dumps(deterministic_issues, ensure_ascii=False)}
- Score cap máximo permitido: {score_cap}

INSTRUCCIONES IMPORTANTES SOBRE GROUNDING:
- Los nombres de herramientas son libres — el estudiante puede llamar a su wrapper de DynamoDB
  de cualquier forma (consultar_pedido, get_order, buscar_datos, etc.). Si el tool trace tiene
  datos reales, ESO ES grounding válido independientemente del nombre de la herramienta.
- En el tool trace, cada entrada tiene un campo "output" que contiene "result_snippet".
- El campo "result_snippet" contiene los datos REALES devueltos por el backend (DynamoDB o políticas).
- Si el "result_snippet" contiene datos que coinciden con lo que el agente respondió, ESO ES EVIDENCIA
  DE GROUNDING VÁLIDA. El agente SÍ consultó la herramienta y obtuvo los datos.
- NO penalices por "falta de grounding" si el result_snippet muestra los datos que el agente usó.

CRITERIOS:
1. Grounding: ¿la respuesta parece apoyarse en evidencia consultada? Revisa el result_snippet.
2. Exactitud: ¿los números y hechos son coherentes con el expected data?
3. Claridad: ¿la respuesta explica bien el resultado o cálculo?
4. No alucinación: ¿evita inventar datos no soportados?

INSTRUCCIONES DE SCORING:
- Devuelve un score entero entre 0 y {score_cap}.
- Si los hallazgos determinísticos muestran falta de grounding o error numérico, no ignores esos problemas.
- Si el agente usó Athena o Dynamo correctamente, considéralo igual de válido.
- Si el result_snippet confirma los datos, el agente ESTÁ grounded — puntúa alto.
- Si el agente consultó datos correctamente pero el cálculo tiene un error menor, score 60-75.
- Solo score <50 si no hubo ningún intento de grounding o si los datos son completamente inventados.

Responde SOLO en JSON con esta forma exacta:
{{"score": int, "feedback": "str"}}
"""
        return self._call_llm(prompt)

    # -----------------------------
    # Helpers
    # -----------------------------
    def _extract_tool_names(self, tool_trace: List[Dict[str, Any]]) -> List[str]:
        names = []
        for entry in tool_trace or []:
            raw = (
                entry.get("tool")
                or entry.get("tool_name")
                or entry.get("name")
                or entry.get("function")
                or ""
            )
            names.append(self._normalize_text(str(raw)))
        return names

    def _used_any_keyword_group(self, tool_names: List[str], keywords: List[str]) -> bool:
        for tool_name in tool_names:
            for keyword in keywords:
                if keyword in tool_name:
                    return True
        return False

    def _references_data_backend(self, required_tools: List[str]) -> bool:
        normalized = [self._normalize_text(t) for t in required_tools]
        return any(any(db in t for db in self.DATA_BACKEND_GROUP) for t in normalized)

    def _missing_literal_required_tools(self, tool_names: List[str], required_tools: List[str]) -> List[str]:
        missing = []
        for req in required_tools:
            norm_req = self._normalize_text(str(req))

            # Skip literal enforcement for Athena/Dynamo because they are treated as equivalent.
            if any(db in norm_req for db in self.DATA_BACKEND_GROUP):
                continue

            if not any(norm_req in used for used in tool_names):
                missing.append(req)
        return missing

    def _satisfies_any_of(self, tool_names: List[str], options: List[str]) -> bool:
        normalized_options = [self._normalize_text(str(x)) for x in options]

        # If any option references data backend, either Athena or Dynamo is enough.
        if any(any(db in opt for db in self.DATA_BACKEND_GROUP) for opt in normalized_options):
            return self._used_any_keyword_group(tool_names, self.DATA_BACKEND_GROUP)

        return any(any(opt in used for used in tool_names) for opt in normalized_options for used in tool_names)

    def _extract_numbers(self, text: str) -> List[float]:
        if not text:
            return []

        # Mejorar el regex para no capturar puntuación final (puntos o comas sueltos al final)
        # Busca dígitos, opcionalmente seguidos de grupos de (punto/coma + dígitos)
        matches = re.finditer(r'[-+]?\d+(?:(?:[.,]\d+)+)?', text)
        numbers = []

        for match in matches:
            cleaned = match.group(0).strip()

            # Heuristic:
            # - if both '.' and ',' appear, check which one comes last
            # - for this project, common forms are 119000 / 119.000 / 119,000 / 5,940,956.86
            if '.' in cleaned and ',' in cleaned:
                if cleaned.rfind(',') < cleaned.rfind('.'):
                    # Comma is thousands separator, dot is decimal (US format)
                    cleaned = cleaned.replace(',', '')
                else:
                    # Dot is thousands separator, comma is decimal (EU/Spanish format)
                    cleaned = cleaned.replace('.', '').replace(',', '.')
            else:
                # assume separator is thousands separator when there are 3-digit groups
                # Note: This regex now strictly checks exactly 3 digits after the separator
                if re.match(r'^\d{1,3}(?:\.\d{3})+$', cleaned):
                    cleaned = cleaned.replace('.', '')
                elif re.match(r'^\d{1,3}(?:,\d{3})+$', cleaned):
                    cleaned = cleaned.replace(',', '')
                else:
                    # If just one separator and not matching 3-digit groups perfectly,
                    # assume it's a decimal point (whether it is comma or dot)
                    cleaned = cleaned.replace(',', '.')

            try:
                numbers.append(float(cleaned))
            except Exception:
                continue

        return numbers

    def _response_contains_value(self, numbers: List[float], expected: float, tolerance: float) -> bool:
        for number in numbers:
            if abs(number - expected) <= tolerance:
                return True
        return False

    def _normalize_text(self, value: str) -> str:
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        return value.lower().strip()

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0
