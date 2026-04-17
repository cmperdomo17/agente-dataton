from .base_judge import BaseJudge
import json
import unicodedata
from typing import Any, Dict, List


class MemoryJudge(BaseJudge):
    """
    Judge for conversational memory scenarios.

    Evaluates whether the agent correctly stores and recalls user-provided
    facts (name, city, preferences) across conversation turns.
    """

    def evaluate(self, user_input, agent_response, tool_trace, expected_data=None, conversation_history=None):
        expected_data = expected_data or {}

        det = self._deterministic_checks(
            agent_response=agent_response,
            expected_data=expected_data,
            conversation_history=conversation_history or [],
        )

        llm_verdict = self._semantic_review(
            user_input=user_input,
            agent_response=agent_response,
            expected_data=expected_data,
            conversation_history=conversation_history or [],
            deterministic_issues=det["issues"],
            score_cap=det["score_cap"],
        )

        llm_score = self._safe_int(llm_verdict.get("score", 0))
        final_score = max(min(llm_score, det["score_cap"]), det.get("score_floor", 0))

        if det["hard_fail"]:
            final_score = min(final_score, 30)

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

    def _deterministic_checks(
        self,
        agent_response: str,
        expected_data: Dict[str, Any],
        conversation_history: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        issues: List[str] = []
        score_cap = 100
        hard_fail = False

        response_n = self._normalize_text(agent_response)
        goal = str(expected_data.get("goal", ""))

        must_answer_with_name = bool(expected_data.get("must_answer_with_name", False))
        must_not_request_identification = bool(expected_data.get("must_not_request_identification", False))
        name = expected_data.get("name", "")
        answer_contains = expected_data.get("answer_contains", "")
        facts = expected_data.get("facts", []) or []

        score_floor = 0
        if goal == "recall_name" and must_answer_with_name and name:
            if self._normalize_text(name) in response_n:
                score_floor = 75          # name present → always passes
            else:
                issues.append(f"No incluyó el nombre esperado '{name}' en la respuesta.")
                score_cap = min(score_cap, 30)
                hard_fail = True

        if goal == "recall_fact" and answer_contains:
            if self._normalize_text(str(answer_contains)) not in response_n:
                issues.append(f"No incluyó el dato esperado '{answer_contains}' en la respuesta.")
                score_cap = min(score_cap, 30)
                hard_fail = True

        if goal in ("store_name", "update_name_memory") and name:
            if self._normalize_text(name) not in response_n:
                issues.append(f"No usó ni reconoció el nombre '{name}' en su respuesta.")
                score_cap = min(score_cap, 70)

        acknowledgment_optional = bool(expected_data.get("acknowledgment_optional", False))
        if goal == "store_profile_fields" and facts and not acknowledgment_optional:
            missing = [f for f in facts if self._normalize_text(str(f)) not in response_n]
            if missing:
                issues.append(f"No reconoció estos datos del perfil: {', '.join(missing)}.")
                score_cap = min(score_cap, 65)

        if must_not_request_identification:
            id_keywords = ["cedula", "cédula", "dni", "documento", "identificacion", "identificación"]
            if any(self._normalize_text(k) in response_n for k in id_keywords):
                issues.append("Pidió identificación formal en un escenario de memoria conversacional.")
                score_cap = min(score_cap, 40)
                hard_fail = True

        return {
            "issues": issues,
            "score_cap": score_cap,
            "hard_fail": hard_fail,
            "score_floor": score_floor,
        }

    def _semantic_review(
        self,
        user_input: str,
        agent_response: str,
        expected_data: Dict[str, Any],
        conversation_history: List[Dict[str, str]],
        deterministic_issues: List[str],
        score_cap: int,
    ) -> Dict[str, Any]:
        history_text = self._format_history(conversation_history)

        allow_proactive_id = bool(expected_data.get("allow_proactive_identification", False))
        id_penalty_mode = expected_data.get("identification_penalty_mode", "hard")
        acknowledgment_optional = bool(expected_data.get("acknowledgment_optional", False))

        if allow_proactive_id and id_penalty_mode == "soft":
            criterion_3 = "3. El agente PUEDE pedir identificación de forma proactiva en este escenario. Solo penaliza si la solicitud de identificación BLOQUEÓ la respuesta principal (es decir, el agente no respondió lo que se le preguntó)."
        else:
            criterion_3 = "3. ¿Evita pedir identificación formal (cédula/DNI) cuando solo se trata de recordar datos conversacionales?"

        acknowledgment_note = ""
        if acknowledgment_optional:
            acknowledgment_note = "\n- IMPORTANTE: En este paso el agente solo necesita almacenar los datos recibidos — NO tiene que confirmarlos ni repetirlos en su respuesta. Si el agente saludó al usuario y respondió de forma natural sin repetir los datos, eso es CORRECTO. No penalices por no mencionar explícitamente los datos almacenados."

        prompt = f"""
Actúa como evaluador experto de memoria conversacional para agentes de atención al cliente.

Tu tarea es juzgar si el agente recuerda correctamente la información que el usuario compartió durante la conversación.

HISTORIAL DE LA CONVERSACIÓN (turnos anteriores):
{history_text}

TURNO ACTUAL:
- Pregunta del usuario: {json.dumps(user_input, ensure_ascii=False, default=str)}
- Respuesta final del agente: {json.dumps(agent_response, ensure_ascii=False, default=str)}
- Expected data: {json.dumps(expected_data, ensure_ascii=False, default=str)}
- Hallazgos determinísticos previos: {json.dumps(deterministic_issues, ensure_ascii=False, default=str)}
- Score cap máximo permitido: {score_cap}

CONFIGURACIÓN DEL ESCENARIO:
- allow_proactive_identification: {allow_proactive_id}
- identification_penalty_mode: {id_penalty_mode}
- acknowledgment_optional: {acknowledgment_optional}{acknowledgment_note}

CRITERIOS:
1. ¿El agente recuerda los datos que el usuario mencionó en turnos anteriores (nombre, ciudad, preferencias)?
2. ¿Responde con los datos correctos sin inventar información?
{criterion_3}
4. ¿Es natural y fluido al recordar, sin sonar mecánico?
5. Si el usuario corrigió un dato, ¿el agente usa el dato actualizado?

IMPORTANTE:
- Los datos que el usuario compartió en turnos anteriores NO son "inventados" por el agente al recordarlos.
- Si el historial muestra que el usuario dijo "vivo en Panamá" y luego pregunta "¿dónde vivo?", responder "Panamá" es CORRECTO.

IMPORTANTE SOBRE RECORDAR NOMBRES:
- Si el agente usa el nombre de forma natural en la conversación (ej. "¡Hola, Juan!", "Con gusto, Juan",
  "Claro que sí, Juan", "Lo siento Juan/Pedro") eso es CORRECTO. NO exijas que el agente diga explícitamente "te llamas Juan"
  o "tu nombre es Juan". Usar el nombre en el saludo o en la respuesta cuenta como recordarlo correctamente.
- Solo penaliza si el nombre está COMPLETAMENTE AUSENTE de la respuesta cuando debería estar presente.

Responde SOLO en JSON con esta forma exacta:
{{"score": int, "feedback": "str"}}
"""
        return self._call_llm(prompt)

    def _format_history(self, conversation_history: List[Dict[str, str]]) -> str:
        if not conversation_history:
            return "(Sin historial previo)"

        lines = []
        for turn in conversation_history:
            role = turn.get("role", "unknown")
            content = turn.get("content", "")
            emoji = "👤" if role == "user" else "🤖"
            lines.append(f"{emoji} {role}: {content}")
        return "\n".join(lines)

    def _normalize_text(self, value: str) -> str:
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        return value.lower().strip()

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0
