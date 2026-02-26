import json
from strands import Agent
from strands.models import BedrockModel

from core.config import MODEL_ID, MODEL_TEMPERATURE, AGENT_STREAMING
from core.athena_service import consultar_athena
from core.dynamo_service_updated import consultar_dynamo
from core.prompt import build_system_prompt


def create_agent(streaming: bool | None = None) -> Agent:
    prompt = build_system_prompt()

    if streaming is None:
        streaming = AGENT_STREAMING

    model = BedrockModel(
        model_id=MODEL_ID,
        temperature=MODEL_TEMPERATURE,
        streaming=bool(streaming),
    )

    return Agent(
        tools=[consultar_dynamo, consultar_athena],
        model=model,
        system_prompt=prompt,
    )
