from .base_judge import BaseJudge
import json
import unicodedata
from typing import Any, Dict, List


class MemoryJudge(BaseJudge):
    """
    Judge for lightweight conversational memory.

    Calibrated version:
    - The primary goal is memory correctness (store / update / recall).
    - Asking for identification is NOT automatically bad.
    - It is only penalized strongly when the case explicitly forbids it or when it
      blocks / replaces the memory task instead of solving it.
    """

    ID_REQUEST_KEYWORDS = [
        "cedula", "cédula", "dni", "documento", "identificacion", "identificación",
        "verificar tu identidad", "numero de documento", "número de documento", "celular",
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

    def _deterministic_checks(self, user_input: str, agent_response: str, expected_data: Dict[str, Any]) -> Dict[str, Any]:
        issues: List[str] = []
        score_cap = 100
        hard_fail = False
        hard_fail_cap = 35

        response_n = self._normalize_text(agent_response)
        goal = str(expected_data.get("goal", "")).strip()
        asks_identification = any(self._normalize_text(k) in response_n for k in self.ID_REQUEST_KEYWORDS)

        must_not_request_identification = bool(expected_data.get("must_not_request_identification", False))
        allow_proactive_identification = bool(expected_data.get("allow_proactive_identification", False))
        identification_penalty_mode = str(expected_data.get("identification_penalty_mode", "soft")).strip() or "soft"
        must_answer_with_name = bool(expected_data.get("must_answer_with_name", False))
        must_not_block_primary_goal = bool(expected_data.get("must_not_block_primary_goal", False))

        memory_ok = True

        if goal in ["recall_name", "update_name_memory"]:
            expected_name = str(expected_data.get("name", "")).strip()
            if expected_name and self._normalize_text(expected_name) not in response_n:
                issues.append(f"No recordó correctamente el nombre esperado: {expected_name}.")
                score_cap = min(score_cap, 25)
                hard_fail = True
                memory_ok = False
            elif must_answer_with_name and expected_name:
                # if it did mention it, memory objective is satisfied even if it adds extra friction
                memory_ok = True

        if goal == "recall_fact":
            expected = str(expected_data.get("answer_contains", "")).strip()
            if expected and self._normalize_text(expected) not in response_n:
                issues.append(f"No recordó el dato esperado: {expected}.")
                score_cap = min(score_cap, 30)
                hard_fail = True
                memory_ok = False

        if goal == "store_profile_fields":
            facts = expected_data.get("facts", []) or []
            if not any(word in response_n for word in ["hola", "encantado", "mucho gusto", "gracias", "ana", "juan", "pedro"]):
                issues.append("La respuesta no parece reconocer o integrar la información compartida por el usuario.")
                score_cap = min(score_cap, 80)

        if asks_identification:
            if must_not_request_identification:
                issues.append("Pidió identificación en una prueba donde el contrato del caso indicaba evitarla.")
                score_cap = min(score_cap, 45)
                if must_not_block_primary_goal and not memory_ok:
                    hard_fail = True
            elif allow_proactive_identification and memory_ok:
                # only a very light penalty, if any
                if identification_penalty_mode == "none":
                    pass
                else:
                    issues.append("Pidió identificación de forma proactiva, pero sin impedir cumplir el objetivo principal del caso.")
                    score_cap = min(score_cap, 95)
            elif memory_ok:
                # default soft penalty if it asks for ID but still solves the memory task
                issues.append("La respuesta agregó verificación de identidad, pero aun así cumplió el objetivo principal de memoria.")
                score_cap = min(score_cap, 90)
            else:
                issues.append("Pidió identificación y además no resolvió correctamente el objetivo principal de memoria.")
                score_cap = min(score_cap, 35)
                hard_fail = True

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
Actúa como evaluador experto en memoria conversacional básica.

Tu tarea es juzgar si el agente retuvo y reutilizó correctamente la información compartida por el usuario en turnos previos.

CONTEXTO:
- Input del usuario: {json.dumps(user_input, ensure_ascii=False)}
- Respuesta del agente: {json.dumps(agent_response, ensure_ascii=False)}
- Tool trace: {json.dumps(tool_trace, ensure_ascii=False)}
- Expected data: {json.dumps(expected_data, ensure_ascii=False)}
- Hallazgos determinísticos previos: {json.dumps(deterministic_issues, ensure_ascii=False)}
- Score cap máximo permitido: {score_cap}

CRITERIOS:
1. ¿Retuvo el nombre o dato previo correcto?
2. ¿Respondió al objetivo principal del caso de memoria?
3. Si pidió identificación, ¿bloqueó realmente la respuesta o fue solo una conducta proactiva secundaria?
4. ¿La respuesta es clara y coherente con la conversación?

INSTRUCCIONES:
- Devuelve un score entero entre 0 y {score_cap}.
- La prioridad es evaluar la memoria, no castigar automáticamente cualquier solicitud de identificación.
- Si el agente recordó correctamente el dato pedido, no lo penalices de forma severa solo por agregar una verificación proactiva.
- Penaliza fuerte solo si la identificación reemplaza, bloquea o contradice el objetivo principal del caso.

Responde SOLO en JSON con esta forma exacta:
{{"score": int, "feedback": "str"}}
"""
        return self._call_llm(prompt)

    def _normalize_text(self, value: str) -> str:
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        return value.lower().strip()

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0
