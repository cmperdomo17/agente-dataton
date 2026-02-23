import os
from datetime import datetime

# --- AWS ---
AWS_PROFILE = "Mario"
AWS_REGION = "us-east-2"

# --- DynamoDB ---
MAX_ROWS = 20               # Máximo de filas a mostrar en la respuesta
DYNAMO_PREFIX = "omniretail_"

# --- Modelo ---
MODEL_ID = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
MODEL_TEMPERATURE = 0.0

# --- Rutas ---
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "data_dictionary.json")

# --- Fecha actual del sistema ---
CURRENT_DATE = datetime.now().strftime("%Y-%m-%d")

os.environ["AWS_PROFILE"] = AWS_PROFILE
