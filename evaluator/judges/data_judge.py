from .base_judge import BaseJudge
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple


class DataJudge(BaseJudge):
    DATA_BACKEND_GROUP = ["athena", "dynamo"]

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
            final_score = min(final_score, 40)

        feedback_parts = []
        if det["issues"]:
            feedback_parts.append("Deterministic checks: " + " | ".join(det["issues"]))
        if llm_verdict.get("feedback"):
            feedback_parts.append("LLM review: " + str(llm_verdict["feedback"]))

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
            issues.append("No usó un backend de datos válido (Athena o Dynamo).")
            score_cap = min(score_cap, 35)
            hard_fail = True

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
                issues.append(
                    f"No usó ninguna de las tools/backend aceptadas: {', '.join(required_any_of_tools)}."
                )
                score_cap = min(score_cap, 50)

        # Numeric correctness
        expected_values = expected_data.get("expected_values", {}) or {}
        tolerance = float(expected_data.get("numeric_tolerance", 0) or 0)

        numeric_issues = self._check_expected_values(agent_response, expected_values, tolerance)
        if numeric_issues:
            issues.extend(numeric_issues)
            score_cap = min(score_cap, 50)
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

        # We prioritize total_iva if present because it is often the final answer.
        for key, expected in expected_values.items():
            try:
                expected_num = float(expected)
            except Exception:
                continue

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
        prompt = f"""
Actúa como un Auditor de Datos y QA Engineer Senior.

Tu misión es evaluar la calidad técnica de la respuesta del agente, PERO respetando estas reglas:
- Athena y Dynamo son igualmente válidos como backend de datos en esta etapa.
- NO penalices al agente por haber usado Athena en vez de Dynamo o viceversa.
- Sí debes penalizar si no hay grounding cuando el caso lo exige.
- También debes penalizar si la respuesta final contradice los valores esperados.

CONTEXTO:
- Pregunta: {json.dumps(user_input, ensure_ascii=False)}
- Respuesta final del agente: {json.dumps(agent_response, ensure_ascii=False)}
- Tool trace: {json.dumps(tool_trace, ensure_ascii=False)}
- Expected data: {json.dumps(expected_data, ensure_ascii=False)}
- Hallazgos determinísticos previos: {json.dumps(deterministic_issues, ensure_ascii=False)}
- Score cap máximo permitido: {score_cap}

CRITERIOS:
1. Grounding: ¿la respuesta parece apoyarse en evidencia consultada?
2. Exactitud: ¿los números y hechos son coherentes con el expected data?
3. Claridad: ¿la respuesta explica bien el resultado o cálculo?
4. No alucinación: ¿evita inventar datos no soportados?

INSTRUCCIONES DE SCORING:
- Devuelve un score entero entre 0 y {score_cap}.
- Si los hallazgos determinísticos muestran falta de grounding o error numérico, no ignores esos problemas.
- Si el agente usó Athena o Dynamo correctamente, considéralo igual de válido.
- Si el agente acertó numéricamente pero no hay evidencia de consulta cuando era obligatoria, el score debe ser bajo.

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

        matches = re.findall(r'[-+]?\d[\d\.\,]*', text)
        numbers = []

        for match in matches:
            cleaned = match.strip()

            # Heuristic:
            # - if both '.' and ',' appear, assume one of them is thousands separator
            # - for this project, common forms are 119000 / 119.000 / 119,000
            if '.' in cleaned and ',' in cleaned:
                cleaned = cleaned.replace('.', '').replace(',', '.')
            else:
                # assume separator is thousands separator when there are 3-digit groups
                if re.match(r'^\d{1,3}(\.\d{3})+$', cleaned):
                    cleaned = cleaned.replace('.', '')
                elif re.match(r'^\d{1,3}(,\d{3})+$', cleaned):
                    cleaned = cleaned.replace(',', '')
                else:
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
