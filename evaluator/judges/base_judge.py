"""
base_judge.py
─────────────
Lee MODEL_ID y AWS_REGION directamente desde variables de entorno,
sin importar core.config. Esto evita que el juez falle cuando se está
evaluando un ZIP de otro equipo cuyo core/ no tiene config.py.

IMPORTANTE: _call_llm lee las variables de entorno en cada llamada (no al
importar el módulo). Esto evita que el load_dotenv() de un equipo contamine
el modelo del juez cuando los jueces se importan dentro de MultiEngine.__init__
(que ocurre DESPUÉS de exec_module del equipo).
El modelo del juez se lee desde EVAL_JUDGE_MODEL_ID primero, luego MODEL_ID.
submission_loader._inject_aws_env() fuerza EVAL_JUDGE_MODEL_ID al modelo
correcto antes de cargar cualquier ZIP.
"""
import json
import os
import re

# Fallback por si EVAL_JUDGE_MODEL_ID no está definido y MODEL_ID tampoco
_JUDGE_MODEL_FALLBACK = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
_JUDGE_REGION_FALLBACK = "us-east-2"


class BaseJudge:
    def _call_llm(self, prompt: str) -> dict:
        try:
            import boto3
        except ImportError:
            return {"score": 0, "feedback": "Error: boto3 no instalado."}

        try:
            # Leer en cada llamada para no quedar atrapado con valores contaminados
            # por el load_dotenv() de algún equipo evaluado anteriormente.
            # EVAL_JUDGE_MODEL_ID es forzado por submission_loader antes de cualquier
            # carga de ZIP, por lo que tiene prioridad sobre MODEL_ID de equipos.
            model_id  = (os.getenv("EVAL_JUDGE_MODEL_ID")
                         or os.getenv("MODEL_ID")
                         or _JUDGE_MODEL_FALLBACK)
            aws_region = (os.getenv("EVAL_JUDGE_REGION")
                          or os.getenv("AWS_REGION")
                          or _JUDGE_REGION_FALLBACK)
            # EVAL_JUDGE_PROFILE is locked at submission_loader import time (before
            # AWS_PROFILE is overridden to Valen-Agentic for team DynamoDB calls).
            # This ensures the judge always uses the Bedrock-capable account.
            aws_profile = (os.getenv("EVAL_JUDGE_PROFILE")
                           or os.getenv("AWS_PROFILE", ""))

            # Si hay credenciales estáticas en env, boto3 las usa directamente.
            # Si no, usa el perfil configurado.
            session_kwargs = {}
            if aws_profile and not os.getenv("AWS_ACCESS_KEY_ID"):
                session_kwargs["profile_name"] = aws_profile

            session = boto3.Session(**session_kwargs)
            bedrock = session.client("bedrock-runtime", region_name=aws_region)

            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}]
            })

            response = bedrock.invoke_model(modelId=model_id, body=body)
            res_body = json.loads(response.get("body").read())
            text_response = res_body["content"][0]["text"]

            # Extraer solo el bloque JSON
            start = text_response.find('{')
            end = text_response.rfind('}') + 1
            if start == -1 or end == 0:
                return {"score": 0, "feedback": f"El LLM no devolvió JSON válido: {text_response[:200]}"}
            return json.loads(text_response[start:end])

        except Exception as e:
            return {"score": 0, "feedback": f"Error técnico en el juicio: {str(e)}"}

    # Detect responses that leak internal filenames (e.g. "según la politica.md")
    _INTERNAL_LEAK_RE = re.compile(r'\b[\w\-]+\.(md|txt|json|yaml|yml|csv|py)\b', re.IGNORECASE)

    def _check_internal_filename_leak(self, response: str) -> bool:
        """Return True if response contains an internal filename like 'politica.md'."""
        return bool(self._INTERNAL_LEAK_RE.search(response or ""))

    def _safe_int(self, value) -> int:
        try:
            return int(value)
        except Exception:
            return 0
