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
## Credenciales estáticas (Vercel / producción): definir AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY
# en las variables de entorno del servicio de despliegue. boto3 las detecta automáticamente.
# Localmente se puede seguir usando AWS_PROFILE para desarrollo.
#AWS_PROFILE = os.getenv("AWS_PROFILE", "Mario")
AWS_REGION   = os.getenv("AWS_REGION", "us-east-2")

# --- DynamoDB ---
MAX_ROWS = int(os.getenv("MAX_ROWS", "20"))
DYNAMO_PREFIX = os.getenv("DYNAMO_PREFIX", "omniretail_")

# --- Modelo Bedrock ---
MODEL_ID = os.getenv("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
MODEL_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.0"))

# --- Rutas ---
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "data_dictionary.json")

# --- S3 Políticas ---
POLICY_S3_BUCKET = os.getenv("POLICY_S3_BUCKET", "omniretail-policies")
POLICY_S3_PREFIX = os.getenv("POLICY_S3_PREFIX", "politicas/")
POLICY_LOCAL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "politicas")

# --- Fecha actual (se evalúa al arrancar la app) ---
CURRENT_DATE = datetime.now().strftime("%Y-%m-%d")

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Silenciar librerías externas ruidosas (botocore, strands, urllib3)
for _noisy_lib in ("botocore", "boto3", "urllib3", "strands"):
    logging.getLogger(_noisy_lib).setLevel(logging.WARNING)

# Configurar perfil AWS para boto3 SOLO si no hay credenciales estáticas en el entorno.
# En Vercel (producción), AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY deben estar configuradas
# como variables de entorno del proyecto → boto3 las usa automáticamente sin necesidad de perfil.
_has_static_creds = bool(os.getenv("AWS_ACCESS_KEY_ID"))
_aws_profile = os.getenv("AWS_PROFILE", "")
if not _has_static_creds and _aws_profile and "AWS_PROFILE" not in os.environ:
    os.environ["AWS_PROFILE"] = _aws_profile

# Agent
AGENT_STREAMING = os.getenv("AGENT_STREAMING", "true").lower() == "true"