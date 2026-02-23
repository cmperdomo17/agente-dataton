from .base_judge import BaseJudge

class BusinessJudge(BaseJudge):
    def evaluate(self, user_input, agent_response, tool_trace):
        prompt = f"""
        Actúa como Gerente de Servicio al Cliente de OmniRetail.
        Evalúa si el agente sigue las políticas de la empresa y el flujo lógico.

        CRITERIOS:
        - ¿El tono es profesional?
        - ¿El agente guía al usuario (ej: pide ID antes de procesar una devolución)?
        - ¿Evita dar respuestas genéricas si puede ser específico?

        EVIDENCIA:
        - Pregunta del Cliente: "{user_input}"
        - Respuesta del Agente: "{agent_response}"

        Puntaje: 100 si el agente es servicial y sigue el protocolo de pedir identificación para trámites.
        
        Responde ÚNICAMENTE en JSON:
        {{ "score": int, "feedback": "str" }}
        """
        return self._call_llm(prompt)