"""
Interfaz visual de la consola para el asistente OmniRetail.

Proporciona funciones para mostrar el banner, panel de ayuda,
mensajes de respuesta y errores con formato de colores ANSI.
"""

import os
import textwrap
from datetime import datetime

# Códigos ANSI para colores en la consola
_C = '\033[96m'   # Cyan
_G = '\033[92m'   # Green
_Y = '\033[93m'   # Yellow
_R = '\033[91m'   # Red
_B = '\033[1m'    # Bold
_D = '\033[2m'    # Dim
_W = '\033[97m'   # White
_0 = '\033[0m'    # Reset


def clear():
    """Limpia la pantalla de la consola."""
    os.system('cls' if os.name == 'nt' else 'clear')


def banner(db_name: str):
    """Muestra el banner de bienvenida con info de la sesión."""
    now = datetime.now().strftime('%d/%m/%Y %H:%M')
    print(f"""
{_C}{_B}╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║         ◆◆◆   O M N I R E T A I L   ◆◆◆                     ║
║         Asistente Inteligente de Datos Retail                 ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║  {_D}{_W}Motor: Claude 3.5 Haiku   │  DB: {db_name:<20}{_C}  ║
║  {_D}{_W}Sesión: {now}     │  Estado: {_G}● Conectado{_C}           ║
╚══════════════════════════════════════════════════════════════╝{_0}
""")


def help_panel():
    """Muestra las áreas de consulta disponibles y ejemplos."""
    sections = {
        "📦 Inventario":  "Stock · Agotados · Niveles",
        "🛒 Pedidos":     "Órdenes · Historial · Envíos",
        "👥 Clientes":    "Búsqueda · Soporte · Contacto",
        "💰 Promociones": "Descuentos · Cupones · Campañas",
        "📊 Productos":   "Catálogo · Specs · Precios",
    }
    print(f"  {_D}Áreas de consulta:{_0}")
    for cat, desc in sections.items():
        print(f"    {_B}{cat}{_0}  {_D}{desc}{_0}")

    for ex in ["¿Cuáles son las specs del iPhone 14?", "¿Qué productos están agotados?", "Top 5 productos más vendidos"]:
        print(f"    {_C}›{_0} {ex}")
    print()


def footer():
    """Muestra las teclas de comando disponibles."""
    print(f"  {_D}'salir' terminar  │  'limpiar' pantalla  │  'ayuda' opciones{_0}")


def prompt() -> str:
    """Muestra el prompt con timestamp y espera input del usuario."""
    ts = datetime.now().strftime('%H:%M:%S')
    return input(f"\n  {_Y}{_B}[{ts}] ❯{_0} ")


def loading():
    """Muestra indicador de carga mientras el agente procesa."""
    print(f"\n  {_D}⏳ Procesando...{_0}", end="", flush=True)


def done(elapsed: float):
    """Reemplaza el indicador de carga con el tiempo transcurrido."""
    print(f"\r  {_D}✓ Completado en {elapsed:.1f}s{_0}      ")


def response(text, elapsed: float = 0):
    """Muestra la respuesta del agente en un panel visual con bordes."""
    lines = str(text).strip().split('\n')
    wrapped = '\n'.join(textwrap.fill(l, width=58, subsequent_indent='    ') for l in lines)
    body = wrapped.replace('\n', f'\n  {_G}│{_0}  ')
    tag = f"  {_D}({elapsed:.1f}s){_0}" if elapsed else ""
    print(f"""
  {_G}{_B}╭─── OmniRetail IA ───{_0}{tag}
  {_G}│{_0}
  {_G}│{_0}  {body}
  {_G}│{_0}
  {_G}╰{'─' * 58}{_0}""")


def error(text):
    """Muestra un mensaje de error en panel rojo."""
    print(f"""
  {_R}{_B}╭─── Error ───{_0}
  {_R}│{_0}  {text}
  {_R}╰{'─' * 58}{_0}""")


def goodbye(count: int):
    """Muestra mensaje de despedida con el número de consultas realizadas."""
    print(f"\n  {_C}Sesión finalizada. Consultas: {count}{_0}")
    print(f"  {_D}{'─' * 58}{_0}\n")
