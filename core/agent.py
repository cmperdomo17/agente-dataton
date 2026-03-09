"""
Fábrica del agente conversacional OmniRetail.

Crea el agente con el modelo Bedrock, el system prompt y las herramientas disponibles.
"""

import logging
from strands import Agent
from strands.models import BedrockModel
from strands.agent.conversation_manager import SlidingWindowConversationManager

from core.config import MODEL_ID, MODEL_TEMPERATURE, AGENT_STREAMING
from core.dynamo_service import consultar_dynamo
from core.policy_service import consultar_politica
from core.prompt import build_system_prompt

logger = logging.getLogger(__name__)


def create_agent(streaming: bool | None = None) -> Agent:
    prompt = build_system_prompt()

    if streaming is None:
        streaming = AGENT_STREAMING

    model = BedrockModel(
        model_id=MODEL_ID,
        temperature=MODEL_TEMPERATURE,
        streaming=bool(streaming),
    )

    logger.info("Agente creado con modelo=%s", MODEL_ID)

    return Agent(
        tools=[consultar_dynamo, consultar_politica],
        model=model,
        system_prompt=prompt,
        conversation_manager=SlidingWindowConversationManager(window_size=20),
    )
