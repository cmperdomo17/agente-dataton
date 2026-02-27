"""
Punto de entrada del agente OmniRetail.

Ejecuta un loop interactivo (REPL) en la consola donde el usuario
hace preguntas y el agente responde consultando DynamoDB.
"""

import sys
import io
import time
import logging

from core.config import DYNAMO_PREFIX
from core.agent import create_agent
from core.dynamo_service import ensure_caches
from core.policy_service import ensure_policies
from ui import console as ui

logger = logging.getLogger(__name__)

# Comandos reconocidos por el REPL
_EXIT_CMDS = {"salir", "exit", "quit"}
_CLEAR_CMDS = {"limpiar", "clear", "cls"}
_HELP_CMDS = {"ayuda", "help"}


def _invoke_agent(agent, query: str):
    """Ejecuta el agente suprimiendo los mensajes intermedios de streaming.

    El framework strands imprime tokens a stdout durante el streaming.
    Redirigimos stdout temporalmente para que solo la respuesta final
    llegue al usuario a través de nuestra interfaz visual.
    """
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        return agent(query)
    finally:
        sys.stdout = old_stdout


def main():
    """Loop principal del agente: lee preguntas del usuario y muestra respuestas."""
    agent = create_agent()
    ensure_caches()
    ensure_policies()

    ui.clear()
    ui.banner(DYNAMO_PREFIX)
    ui.help_panel()
    ui.footer()

    query_count = 0

    while True:
        try:
            user_input = ui.prompt()

            if not user_input.strip():
                continue

            cmd = user_input.strip().lower()

            if cmd in _EXIT_CMDS:
                ui.goodbye(query_count)
                break

            if cmd in _CLEAR_CMDS:
                ui.clear()
                ui.banner(DYNAMO_PREFIX)
                ui.footer()
                continue

            if cmd in _HELP_CMDS:
                ui.help_panel()
                ui.footer()
                continue

            # Ejecutar consulta al agente
            ui.loading()
            start = time.time()
            result = _invoke_agent(agent, user_input)
            elapsed = time.time() - start

            ui.done(elapsed)
            query_count += 1
            ui.response(result, elapsed)
            logger.debug("Consulta #%d completada en %.1fs", query_count, elapsed)

        except KeyboardInterrupt:
            ui.goodbye(query_count)
            break
        except Exception as e:
            logger.exception("Error procesando consulta")
            ui.error(str(e))


if __name__ == "__main__":
    main()
