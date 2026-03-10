from .base_judge import BaseJudge
import json
import unicodedata
from typing import Any, Dict, List


class BusinessJudge(BaseJudge):

    IDENTIFICATION_KEYWORDS = [
        "cedula", "cédula", "dni", "documento", "identificacion", "identificación",
        "numero de documento", "número de documento", "verificar tu identidad",
        "numero de cedula", "número de cédula", "documento de identidad",
    ]

    def evaluate(self, user_input, agent_response, tool_trace, expected_data=None, conversation_history=None):
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
            conversation_history=conversation_history or [],
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

    def _deterministic_checks(self, user_input, agent_response, expected_data):
        issues = []
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
            "general_faq" if must_not_request_identification else
            "transactional" if may_require_identification else "general"
        )

        # ── FAQ: pedir ID en consulta general = fail duro ─────────────
        if interaction_type == "general_faq" and must_not_request_identification and asks_identification:
            issues.append("Pidió identificación en una consulta general/FAQ donde no correspondía.")
            score_cap = min(score_cap, 35)
            hard_fail = True

        # ── FAQ: deflexión pura sin responder ─────────────────────────
        if should_answer_directly and self._looks_like_pure_deflection(normalized_response):
            issues.append("No respondió la pregunta directamente; generó fricción innecesaria.")
            score_cap = min(score_cap, 60)

        # ── Devolución: no aprobar sin validar ────────────────────────
        if goal == "validate_return_eligibility_before_approving":
            response_has_approval = any(
                phrase in normalized_response for phrase in [
                    "aprob", "puedes devolver", "ya puedes devolver",
                    "procede la devolucion", "proceder con la devolucion"
                ]
            )
            if response_has_approval and required_checks:
                missing = [chk for chk in required_checks
                           if self._normalize_text(str(chk)) not in normalized_response]
                if len(missing) == len(required_checks):
                    issues.append("Aprobó una devolución sin mencionar ninguna validación requerida.")
                    score_cap = min(score_cap, 65)

            if asks_identification and may_require_identification:
                score_cap = min(score_cap, 100)  # pedir ID aquí es correcto

        # ── Retención: no confirmar cancelación sin intentar retener ──
        if goal == "retain_customer_with_empathy":
            confirmed_cancellation = any(p in normalized_response for p in [
                "he cancelado tu cuenta", "tu cuenta ha sido cancelada",
                "procedo a cancelar", "cancele tu cuenta",
            ])
            if confirmed_cancellation:
                issues.append("Confirmó la cancelación sin intentar retener al cliente.")
                score_cap = min(score_cap, 40)

        if may_require_identification is False and asks_identification and goal not in ["validate_return_eligibility_before_approving"]:
            # Solo penalizar si la identificación aparece en la respuesta final del agente,
            # no en el log completo de la sesión
            agent_final_response = agent_response  # solo el último mensaje emitido
            asks_identification_in_response = any(
                phrase in agent_final_response.lower() 
                for phrase in ["cédula", "celular", "verificar tu identidad", "número de documento", "cedula"]
            )
            if asks_identification_in_response:
                issues.append("La identificación parece innecesaria para este tipo de consulta.")
                score_cap = min(score_cap, 70)

        return {
            "issues": issues,
            "score_cap": score_cap,
            "hard_fail": hard_fail,
            "hard_fail_cap": hard_fail_cap,
        }

    def _semantic_review(self, user_input, agent_response, tool_trace, expected_data,
                         deterministic_issues, score_cap, conversation_history=None):
        goal = str(expected_data.get("goal", ""))
        interaction_type = str(expected_data.get("interaction_type", ""))
        history_text = self._format_history(conversation_history or [])

        # Instrucciones específicas por goal
        extra = ""
        if interaction_type == "general_faq":
            extra = """
INSTRUCCIÓN ESPECIAL — FAQ GENERAL:
- El agente solo necesita dar una respuesta directa y razonablemente informativa.
- NO castigues por no dar detalles extremadamente específicos (rangos de precios, condiciones exactas, etc.).
- NO castigues por no ofrecer un calculador de envíos o herramientas adicionales.
- Si respondió directamente la pregunta con información coherente, el score debe ser alto (85+).
- El único caso de score bajo en FAQ: pedir cédula/identificación sin motivo.
"""
        if goal == "recognize_escalation_need":
            extra = """
INSTRUCCIÓN ESPECIAL — ESCALAMIENTO:
- El agente pasa si: (1) reconoció la frustración del cliente, (2) no descartó el problema, 
  (3) ofreció escalamiento a humano O al menos reconoció que es un caso complejo.
- NO es obligatorio usar las palabras exactas "agente humano". Frases como "equipo especializado", 
  "te comunicamos con alguien", "escalar tu caso" son equivalentes.
- NO castigues por el orden en que dio las respuestas si el mensaje general fue de ayuda.
"""
        if goal == "redirect_to_appropriate_channel":
            extra = """
INSTRUCCIÓN ESPECIAL — REDIRECCIÓN FUERA DE ALCANCE:
- El agente pasa si: reconoció el problema, no dio consejo legal específico, y ofreció 
  algún canal de escalamiento (humano, banco, soporte especializado).
- NO castigues por asumir que el problema es de la empresa si el tono fue de ayuda.
- La redirección no tiene que ser perfecta, solo coherente y no dañina.
"""
        if goal == "retain_customer_with_empathy":
            extra = """
INSTRUCCIÓN ESPECIAL — RETENCIÓN:
- El agente pasa si: reconoció la frustración, no confirmó cancelación inmediatamente,
  y ofreció alguna alternativa (solución, escalar, hablar con alguien).
- NO castigues por no tener un script de retención perfecto.
"""

        prompt = f"""
Actúa como evaluador experto de experiencia de cliente y lógica de negocio para OmniRetail Colombia.

HISTORIAL (turnos anteriores):
{history_text}

TURNO ACTUAL:
- Usuario: {json.dumps(user_input, ensure_ascii=False)}
- Agente: {json.dumps(agent_response, ensure_ascii=False)}
- Tool trace: {json.dumps(tool_trace, ensure_ascii=False)}
- Expected data: {json.dumps(expected_data, ensure_ascii=False)}
- Hallazgos determinísticos: {json.dumps(deterministic_issues, ensure_ascii=False)}
- Score cap: {score_cap}
{extra}
CRITERIOS GENERALES:
1. ¿Entiende el tipo de consulta (FAQ vs transaccional)?
2. ¿Evita pedir ID cuando no es necesaria?
3. ¿Da una respuesta directa y útil cuando corresponde?
4. ¿Maneja bien el escalamiento/retención cuando aplica?
5. ¿Tono profesional y claro?

SCORING:
- Para FAQs: respuesta directa y coherente = 85+. No exigir perfección de detalles.
- Para transaccionales: validar elegibilidad antes de aprobar.
- Para escalamiento/retención: reconocer y ofrecer canal alternativo.
- Score entre 0 y {score_cap}.

Responde SOLO en JSON:
{{"score": int, "feedback": "str"}}
"""
        return self._call_llm(prompt)

    def _format_history(self, conversation_history):
        if not conversation_history:
            return "(Sin historial previo)"
        lines = []
        for turn in conversation_history:
            role = turn.get("role", "unknown")
            content = turn.get("content", "")
            emoji = "👤" if role == "user" else "🤖"
            lines.append(f"{emoji} {role}: {content}")
        return "\n".join(lines)

    def _normalized_id_keywords(self):
        return [self._normalize_text(k) for k in self.IDENTIFICATION_KEYWORDS]

    def _looks_like_pure_deflection(self, normalized_response):
        patterns = [
            "para ayudarte necesito",
            "para poder ayudarte necesito",
            "necesito que me proporciones",
            "necesito tu cedula",
            "necesito tu cedula",
            "verificar tu identidad",
        ]
        return any(self._normalize_text(p) in normalized_response for p in patterns)

    def _normalize_text(self, value):
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        return value.lower().strip()

    def _safe_int(self, value):
        try:
            return int(value)
        except Exception:
            return 0
