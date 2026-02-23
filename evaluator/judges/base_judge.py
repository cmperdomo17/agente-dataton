import boto3
import json
from core.config import MODEL_ID, AWS_REGION

class BaseJudge:
    def __init__(self):
        self.bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    def _call_llm(self, prompt):
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}]
        })
        try:
            response = self.bedrock.invoke_model(modelId=MODEL_ID, body=body)
            res_body = json.loads(response.get("body").read())
            text_response = res_body["content"][0]["text"]
            
            # Limpieza para extraer solo el bloque JSON
            start = text_response.find('{')
            end = text_response.rfind('}') + 1
            return json.loads(text_response[start:end])
        except Exception as e:
            return {"score": 0, "feedback": f"Error técnico en el juicio: {str(e)}"}