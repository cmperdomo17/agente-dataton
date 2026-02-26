import os
from datetime import datetime

# --- AWS ---
#AWS_PROFILE = "Mario"
AWS_REGION = "us-east-2"

# --- Athena ---
ATHENA_DB = "dataton-db"
ATHENA_OUTPUT = "s3://dataton-challenge-unicauca-athena-results/"
ATHENA_MAX_WAIT = 20        # Máximo de segundos esperando respuesta
ATHENA_POLL_INTERVAL = 0.5  # Tiempo entre cada revisión del estado
ATHENA_MAX_ROWS = 20        # Máximo de filas a mostrar en la respuesta

# --- DynamoDB ---
DYNAMO_TABLES = {
    "products": "omniretail_products",
    "stock": "omniretail_stock",
    "customers": "omniretail_customers",
    "customer_emails": "omniretail_customer_emails",
    "addresses": "omniretail_addresses",
    "cards": "omniretail_cards",
    "orders": "omniretail_orders",
    "order_items": "omniretail_order_items",
    "shipments": "omniretail_shipments",
    "tracking": "omniretail_tracking",
    "brands": "omniretail_brands",
    "categories": "omniretail_categories",
    "promotions": "omniretail_promotions",
}

# --- Modelo ---
MODEL_ID = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
MODEL_TEMPERATURE = 0.0

# --- Rutas ---
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "data_dictionary.json")

# --- Fecha actual del sistema ---
CURRENT_DATE = datetime.now().strftime("%Y-%m-%d")

#os.environ["AWS_PROFILE"] = AWS_PROFILE

# Agent
AGENT_STREAMING = os.getenv("AGENT_STREAMING", "true").lower() == "true"