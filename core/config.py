"""
Configuración centralizada del agente OmniRetail.

Lee valores desde variables de entorno con fallbacks sensatos.
Para configurar localmente, copiar .env.example → .env y ajustar valores.
"""

import os
import logging
from datetime import datetime

# Cargar .env si existe (opcional, no falla si python-dotenv no está instalado)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- AWS ---
AWS_PROFILE = os.getenv("AWS_PROFILE", "Mario")
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")

# --- DynamoDB ---
MAX_ROWS = int(os.getenv("MAX_ROWS", "20"))
DYNAMO_PREFIX = os.getenv("DYNAMO_PREFIX", "omniretail_")

# --- Modelo Bedrock ---
MODEL_ID = os.getenv("MODEL_ID", "us.anthropic.claude-3-5-haiku-20241022-v1:0")
MODEL_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.0"))

# --- Rutas ---
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "data_dictionary.json")

# --- Fecha actual (se evalúa al arrancar la app) ---
CURRENT_DATE = datetime.now().strftime("%Y-%m-%d")

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Configurar perfil AWS para boto3 (solo si no viene por otro mecanismo)
if AWS_PROFILE and "AWS_PROFILE" not in os.environ:
    os.environ["AWS_PROFILE"] = AWS_PROFILE
