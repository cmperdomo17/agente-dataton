from .base_judge import BaseJudge
import json
import unicodedata
from typing import Any, Dict, List


class RagJudge(BaseJudge):

    # Español + inglés para detectar uso de herramienta de políticas
    RETRIEVAL_KEYWORDS = [
        # inglés
        "document", "documents", "doc", "docs", "retriev", "search",
        "knowledge", "kb", "policy", "policies", "file", "files",
        "rag", "guide", "manual", "faq",
        # español — nombres de herramientas comunes en el agente
        "politica", "política", "consultar_politica", "consultar_política",
        "terminos", "términos", "garantia", "garantía",
        "devolucion", "devolución", "cambio", "reglamento",
        "condiciones", "base_conocimiento", "base_de_conocimiento",
        "buscar_politica", "buscar_política", "knowledge_base",
        "retrieve", "buscar_documento", "consultar_documento",
    ]

    def evaluate(self, user_input, agent_response, tool_trace, expected_data=None, conversation_history=None):
        expected_data = expected_data or {}

        det = self._deterministic_checks(
            agent_response=agent_response,
            tool_trace=tool_trace or [],
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

    def _deterministic_checks(self, agent_response, tool_trace, expected_data):
        issues = []
        score_cap = 100
        hard_fail = False

        must_use_retrieval = bool(expected_data.get("must_use_retrieval", False))
        expected_fact = expected_data.get("expected_fact")
        expected_facts = expected_data.get("expected_facts", []) or (
            [] if not expected_fact else [expected_fact]
        )

        tool_names = self._extract_tool_names(tool_trace)
        retrieval_used = self._used_retrieval_tool(tool_names)

        required_tools = expected_data.get("required_tools", []) or []
        required_any_of_tools = expected_data.get("required_any_of_tools", []) or []

        # ── ¿Usó retrieval? ───────────────────────────────────────────
        if must_use_retrieval and not retrieval_used:
            issues.append("Se requería retrieval documental pero no hay evidencia en el tool trace.")
            score_cap = min(score_cap, 35)
            hard_fail = True

        if required_tools:
            missing = [req for req in required_tools
                       if not any(self._normalize_text(req) in t for t in tool_names)]
            if missing:
                issues.append(f"No usó tools documentales requeridas: {', '.join(missing)}.")
                score_cap = min(score_cap, 60)

        if required_any_of_tools:
            any_ok = any(
                any(self._normalize_text(opt) in used for used in tool_names)
                for opt in required_any_of_tools
            )
            if not any_ok:
                issues.append(f"No usó ninguna tool aceptada: {', '.join(required_any_of_tools)}.")
                score_cap = min(score_cap, 60)

        # Soft fact coverage check by normalized containment.
        normalized_response = self._normalize_text(agent_response)
        missing_facts = []
        for fact in expected_facts:
            normalized_fact = self._normalize_text(str(fact))
            # Split the expected fact into core keywords (words > 3 chars to ignore stop words)
            keywords = [w for w in normalized_fact.split() if len(w) > 3]
            if not keywords:
                if normalized_fact not in normalized_response:
                    missing_facts.append(fact)
                continue

            # Check if at least 45% of the significant keywords are present
            matches = sum(1 for k in keywords if k in normalized_response)
            if matches / len(keywords) < 0.45:
                missing_facts.append(fact)

        if missing_facts:
            issues.append(
                "Hizo retrieval pero la respuesta no incluye explícitamente la idea de: "
                + "; ".join(map(str, missing_facts))
            )
            score_cap = min(score_cap, 72)
        elif missing_facts and not retrieval_used:
            # Ya está penalizado por no hacer retrieval — no apilar penalidades
            pass

        return {"issues": issues, "score_cap": score_cap, "hard_fail": hard_fail}

    def _semantic_review(self, user_input, agent_response, tool_trace,
                         expected_data, deterministic_issues, score_cap):
        prompt = f"""
Actúa como evaluador experto en RAG y grounding documental para OmniRetail Colombia.

Tu tarea: juzgar si la respuesta del agente está bien sustentada en evidencia documental.

CONTEXTO:
- Usuario: {json.dumps(user_input, ensure_ascii=False)}
- Agente: {json.dumps(agent_response, ensure_ascii=False)}
- Tool trace: {json.dumps(tool_trace, ensure_ascii=False)}
- Expected data: {json.dumps(expected_data, ensure_ascii=False)}
- Hallazgos determinísticos: {json.dumps(deterministic_issues, ensure_ascii=False)}
- Score cap: {score_cap}

CRITERIOS PRIORITARIOS (en este orden):
1. RETRIEVAL: ¿Hay evidencia en el tool trace de que consultó documentación/política?
   Este es el criterio más importante. Sin retrieval = score máximo 35.
2. HECHO CLAVE: ¿La respuesta incluye el hecho esperado (expected_fact)?
3. GROUNDING: ¿La respuesta está alineada con la política consultada?

CRITERIOS SECUNDARIOS (NO son motivo de falla):
- Dar información adicional correcta que no estaba en expected_fact está BIEN.
- Dar más contexto o detalles relevantes sobre la política está BIEN.
- El tono o el nivel de detalle del lenguaje NO es motivo de penalización.

INSTRUCCIONES:
- Si hubo retrieval Y el hecho clave está presente: score alto (85+).
- Si hubo retrieval pero falta el hecho clave: score medio (60-75).
- Si NO hubo retrieval pero la respuesta es correcta: score bajo (30-40) — puede ser alucinación/conocimiento paramétrico.
- NO penalices por dar información extra que sea correcta y útil.
- Score entre 0 y {score_cap}.

Responde SOLO en JSON:
{{"score": int, "feedback": "str"}}
"""
        return self._call_llm(prompt)

    def _extract_tool_names(self, tool_trace):
        names = []
        for entry in tool_trace or []:
            raw = (entry.get("tool") or entry.get("tool_name") or
                   entry.get("name") or entry.get("function") or "")
            names.append(self._normalize_text(str(raw)))
        return names

    def _used_retrieval_tool(self, tool_names):
        for tool_name in tool_names:
            if any(keyword in tool_name for keyword in self.RETRIEVAL_KEYWORDS):
                return True
        return False

    def _normalize_text(self, value):
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        return value.lower().strip()

    def _safe_int(self, value):
        try:
            return int(value)
        except Exception:
            return 0
