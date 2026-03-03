from .base_judge import BaseJudge
import json
import unicodedata
from typing import Any, Dict, List


class BusinessJudge(BaseJudge):
    """
    Business / CX judge.

    Calibrated version:
    - FAQ/general questions: asking for identification is strongly penalized.
    - Transactional or personalized flows: early identification may be acceptable.
    - The judge should prioritize whether the agent moved the business flow forward
      in a reasonable way, not punish every request for identity.
    """

    IDENTIFICATION_KEYWORDS = [
        "cedula", "cédula", "dni", "documento", "identificacion", "identificación",
        "id", "numero de documento", "número de documento", "verificar tu identidad", "celular",
    ]

    def evaluate(self, user_input, agent_response, tool_trace, expected_data=None):
        expected_data = expected_data or {}

        det = self._deterministic_checks(
            user_input=user_input,
            agent_response=agent_response,
            expected_data=expected_data,
        )

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
            final_score = min(final_score, det["hard_fail_cap"])

        feedback_parts = []
        if det["issues"]:
            feedback_parts.append("Deterministic checks: " + " | ".join(det["issues"]))
        if llm_verdict.get("feedback"):
            feedback_parts.append("LLM review: " + str(llm_verdict["feedback"]))

        return {
            "score": final_score,
            "feedback": " || ".join(feedback_parts) if feedback_parts else "Sin feedback",
        }

    def _deterministic_checks(
        self,
        user_input: str,
        agent_response: str,
        expected_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        issues: List[str] = []
        score_cap = 100
        hard_fail = False
        hard_fail_cap = 40

        normalized_response = self._normalize_text(agent_response)
        asks_identification = any(k in normalized_response for k in self._normalized_id_keywords())

        must_not_request_identification = bool(expected_data.get("must_not_request_identification", False))
        should_answer_directly = bool(expected_data.get("should_answer_directly", False))
        may_require_identification = bool(expected_data.get("may_require_identification", False))
        required_checks = expected_data.get("required_checks", []) or []
        goal = str(expected_data.get("goal", "")).strip()
        interaction_type = str(expected_data.get("interaction_type", "")).strip() or (
            "general_faq" if must_not_request_identification else "transactional" if may_require_identification else "general"
        )

        if interaction_type == "general_faq" and must_not_request_identification and asks_identification:
            issues.append("Pidió identificación en una consulta general/FAQ donde no correspondía.")
            score_cap = min(score_cap, 35)
            hard_fail = True

        if should_answer_directly and self._looks_like_pure_deflection(normalized_response):
            issues.append("No respondió la pregunta de forma directa; generó fricción innecesaria.")
            score_cap = min(score_cap, 60)

        if goal == "validate_return_eligibility_before_approving":
            response_has_approval = any(
                phrase in normalized_response
                for phrase in ["aprob", "puedes devolver", "ya puedes devolver", "procede la devolucion", "proceder con la devolucion"]
            )
            if response_has_approval and required_checks:
                missing = [chk for chk in required_checks if self._normalize_text(str(chk)) not in normalized_response]
                if len(missing) == len(required_checks):
                    issues.append("Aprobó o encaminó una devolución sin mencionar ninguna validación relevante del caso.")
                    score_cap = min(score_cap, 65)
            # in transactional flows, requesting identification early can be acceptable
            if asks_identification and may_require_identification:
                score_cap = min(score_cap, 100)

        if interaction_type not in ["general_faq", "faq"] and asks_identification and not may_require_identification and goal not in ["validate_return_eligibility_before_approving"]:
            # only a light penalty outside hard FAQ cases
            issues.append("La identificación podría no ser necesaria para este tipo de consulta, pero no invalida por sí sola la respuesta.")
            score_cap = min(score_cap, 85)

        return {
            "issues": issues,
            "score_cap": score_cap,
            "hard_fail": hard_fail,
            "hard_fail_cap": hard_fail_cap,
        }

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
Actúa como evaluador experto de experiencia de cliente y lógica de negocio.

Tu tarea es juzgar si el agente respondió con el nivel correcto de ayuda, especificidad y fricción.

CONTEXTO:
- Pregunta del usuario: {json.dumps(user_input, ensure_ascii=False)}
- Respuesta final del agente: {json.dumps(agent_response, ensure_ascii=False)}
- Tool trace: {json.dumps(tool_trace, ensure_ascii=False)}
- Expected data: {json.dumps(expected_data, ensure_ascii=False)}
- Hallazgos determinísticos previos: {json.dumps(deterministic_issues, ensure_ascii=False)}
- Score cap máximo permitido: {score_cap}

CRITERIOS:
1. ¿El agente entiende el tipo de consulta (FAQ general vs trámite transaccional/sensible)?
2. ¿Evita pedir identificación cuando no es necesaria?
3. ¿Cuando la consulta sí es transaccional, la identificación temprana puede ser razonable y no debe castigarse de forma severa?
4. ¿Da una respuesta directa y útil cuando corresponde?
5. ¿Si el caso requiere más validaciones, evita prometer algo demasiado pronto?
6. ¿El tono es profesional y claro?

INSTRUCCIONES DE SCORING:
- Devuelve un score entero entre 0 y {score_cap}.
- NO asumas que pedir identificación siempre es incorrecto.
- Si el caso es una FAQ o consulta general y pide identificación sin necesidad, penaliza fuerte.
- Si el caso es un trámite transaccional, pedir identificación puede ser correcto e incluso útil.
- Prioriza el cumplimiento del objetivo del caso sobre reglas rígidas de fricción.

Responde SOLO en JSON con esta forma exacta:
{{"score": int, "feedback": "str"}}
"""
        return self._call_llm(prompt)

    def _normalized_id_keywords(self):
        return [self._normalize_text(k) for k in self.IDENTIFICATION_KEYWORDS]

    def _looks_like_pure_deflection(self, normalized_response: str) -> bool:
        patterns = [
            "para ayudarte necesito",
            "para poder ayudarte necesito",
            "necesito que me proporciones",
            "necesito tu cedula",
            "necesito tu cédula",
            "verificar tu identidad",
        ]
        return any(self._normalize_text(p) in normalized_response for p in patterns)

    def _normalize_text(self, value: str) -> str:
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        return value.lower().strip()

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0
