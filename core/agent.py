import json
from strands import Agent
from strands.models import BedrockModel

from core.config import MODEL_ID, MODEL_TEMPERATURE
from core.athena_service import consultar_athena
from core.dynamo_service import consultar_dynamo
from core.prompt import build_system_prompt


def create_agent() -> Agent:
    """Crea y devuelve el agente configurado con sus herramientas y modelo."""
    prompt = build_system_prompt()

    model = BedrockModel(
        model_id=MODEL_ID,
        temperature=MODEL_TEMPERATURE,
        streaming=True,
    )

    return Agent(
        tools=[consultar_dynamo, consultar_athena],
        model=model,
        system_prompt=prompt,
    )
