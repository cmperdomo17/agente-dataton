import sys
import io
import time

from core.config import ATHENA_DB
from core.agent import create_agent
from ui import console as ui

_EXIT_CMDS = {"salir", "exit", "quit"}
_CLEAR_CMDS = {"limpiar", "clear", "cls"}
_HELP_CMDS = {"ayuda", "help"}


def _invoke_agent(agent, query: str):
    """Ejecuta el agente sin mostrar los mensajes intermedios en consola."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        return agent(query)
    finally:
        sys.stdout = old_stdout


def main():
    agent = create_agent()

    ui.clear()
    ui.banner(ATHENA_DB)
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
                ui.banner(ATHENA_DB)
                ui.footer()
                continue

            if cmd in _HELP_CMDS:
                ui.help_panel()
                ui.footer()
                continue

            ui.loading()
            start = time.time()
            result = _invoke_agent(agent, user_input)
            elapsed = time.time() - start

            ui.done(elapsed)
            query_count += 1
            ui.response(result, elapsed)

        except KeyboardInterrupt:
            ui.goodbye(query_count)
            break
        except Exception as e:
            ui.error(str(e))


if __name__ == "__main__":
    main()
