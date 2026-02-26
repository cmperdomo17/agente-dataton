from .base_judge import BaseJudge
import json

class DataJudge(BaseJudge):
    def evaluate(self, user_input, agent_response, tool_trace, expected_data=None):
        prompt = f"""
        Actúa como un Auditor de Datos y QA Engineer Senior.
        Tu misión es validar la precisión técnica y matemática del agente.

        CONTEXTO:
        - Pregunta: "{user_input}"
        - Respuesta Final: "{agent_response}"
        - Trazas de Herramientas (Telemetría): {json.dumps(tool_trace)}
        - Verdad Esperada (Ground Truth): {json.dumps(expected_data)}

        CRITERIOS DE EVALUACIÓN:
        1. PRECISIÓN: ¿El valor numérico final coincide con el esperado?
        2. ORIGEN: ¿Consultó la tabla correcta (Athena para históricos, Dynamo para estados)?
        3. CÁLCULO: Si hubo IVA o totales, ¿la operación matemática es correcta?

        REGLA: Si el agente da un dato correcto pero el 'tool_trace' muestra que NO usó herramientas, penaliza por alucinación.
        
        Responde en JSON: {{ "score": int, "feedback": "str" }}
        """
        return self._call_llm(prompt)