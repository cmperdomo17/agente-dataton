"""
base_judge.py
─────────────
Lee MODEL_ID y AWS_REGION directamente desde variables de entorno,
sin importar core.config. Esto evita que el juez falle cuando se está
evaluando un ZIP de otro equipo cuyo core/ no tiene config.py.
"""
import json
import os


# Leer config directamente desde env — mismos defaults que core/config.py
_MODEL_ID   = os.getenv("MODEL_ID",   "us.anthropic.claude-sonnet-4-20250514-v1:0")
_AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
_AWS_PROFILE = os.getenv("AWS_PROFILE", "")


class BaseJudge:
    def _call_llm(self, prompt: str) -> dict:
        try:
            import boto3
        except ImportError:
            return {"score": 0, "feedback": "Error: boto3 no instalado."}

        try:
            # Si hay credenciales estáticas en env, boto3 las usa directamente.
            # Si no, usa el perfil configurado.
            session_kwargs = {}
            if _AWS_PROFILE and not os.getenv("AWS_ACCESS_KEY_ID"):
                session_kwargs["profile_name"] = _AWS_PROFILE

            session = boto3.Session(**session_kwargs)
            bedrock = session.client("bedrock-runtime", region_name=_AWS_REGION)

            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}]
            })

            response = bedrock.invoke_model(modelId=_MODEL_ID, body=body)
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

    def _safe_int(self, value) -> int:
        try:
            return int(value)
        except Exception:
            return 0
