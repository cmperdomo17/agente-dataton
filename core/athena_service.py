import time
import hashlib
from datetime import datetime, timedelta

import boto3
from strands import tool
from core.config import (
    ATHENA_DB,
    ATHENA_OUTPUT,
    ATHENA_MAX_WAIT,
    ATHENA_MAX_ROWS,
    AWS_REGION,
)

# Conexión reutilizable a Athena
_athena_client = boto3.client("athena", region_name=AWS_REGION)

# Caché en memoria para evitar repetir consultas recientes
_query_cache: dict[str, tuple[str, datetime]] = {}
_CACHE_TTL_MINUTES = 5


def _cache_key(sql: str) -> str:
    return hashlib.md5(sql.strip().lower().encode()).hexdigest()


def _get_cached(sql: str) -> str | None:
    """Devuelve el resultado guardado en caché si todavía es válido."""
    key = _cache_key(sql)
    if key in _query_cache:
        result, ts = _query_cache[key]
        if datetime.now() - ts < timedelta(minutes=_CACHE_TTL_MINUTES):
            return result
        del _query_cache[key]
    return None


def _set_cache(sql: str, result: str):
    """Guarda el resultado de una consulta en caché."""
    key = _cache_key(sql)
    _query_cache[key] = (result, datetime.now())


# Palabras prohibidas por seguridad (evita modificaciones a la base de datos)
_FORBIDDEN_KEYWORDS = [
    ";", "insert ", "update ", "delete ",
    "drop ", "create ", "alter ", "truncate ",
]


def _validate_query(sql: str) -> str | None:
    """Verifica que la consulta sea segura. Devuelve un mensaje de error si no lo es."""
    sql_lower = sql.strip().lower()

    if not sql_lower.startswith("select"):
        return "❌ Solo se permiten consultas SELECT."

    if any(kw in sql_lower for kw in _FORBIDDEN_KEYWORDS):
        return "❌ Consulta no permitida por razones de seguridad."

    return None


def _ensure_limit(sql: str) -> str:
    """Agrega un límite de 50 filas si la consulta no tiene uno."""
    if "limit" not in sql.strip().lower():
        return sql + " LIMIT 50"
    return sql


def _parse_results(results: dict) -> str:
    """Convierte los resultados de Athena en una tabla de texto legible."""
    headers = [col["Label"] for col in results["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
    rows = results["ResultSet"]["Rows"][1:]

    if not rows:
        return "La consulta se ejecutó correctamente pero no devolvió resultados (0 filas)."

    lines = [" | ".join(headers)]
    for row in rows:
        values = [
            col.get("VarCharValue", "") for col in row["Data"]
        ]
        lines.append(" | ".join(values))

    return "\n".join(lines[: ATHENA_MAX_ROWS + 1])

def _poll_execution(client, execution_id: str) -> dict:
    """Espera a que Athena termine de procesar la consulta, revisando periódicamente."""
    start = time.time()
    intervals = [0.15, 0.35, 0.7, 1.0, 1.5, 2.0, 3.0]
    idx = 0

    while True:
        stats = client.get_query_execution(QueryExecutionId=execution_id)
        status = stats["QueryExecution"]["Status"]["State"]

        if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return stats

        if time.time() - start > ATHENA_MAX_WAIT:
            raise TimeoutError("La consulta tardó demasiado tiempo.")

        wait = intervals[min(idx, len(intervals) - 1)]
        time.sleep(wait)
        idx += 1


@tool
def consultar_athena(sql_query: str) -> str:
    """Ejecuta SQL SELECT en Athena. SOLO para reportes/tops/estadísticas con GROUP BY, SUM, COUNT.
    Para consultas puntuales usa consultar_dynamo.
    CAST obligatorio en JOINs con products: CAST(x.product_id AS VARCHAR) = p.product_id.
    Siempre incluir LIMIT. Ej: "SELECT p.name, SUM(oi.qty) FROM order_items oi JOIN products p ON CAST(oi.product_id AS VARCHAR)=p.product_id GROUP BY p.name ORDER BY 2 DESC LIMIT 5"
    """
    # 1. Verificar que la consulta sea segura
    error = _validate_query(sql_query)
    if error:
        return error

    sql_query = _ensure_limit(sql_query)

    # 2. Revisar si ya tenemos el resultado en caché
    cached = _get_cached(sql_query)
    if cached is not None:
        return cached

    # 3. Enviar la consulta a Athena
    client = _athena_client

    try:
        response = client.start_query_execution(
            QueryString=sql_query,
            QueryExecutionContext={"Database": ATHENA_DB},
            ResultConfiguration={"OutputLocation": ATHENA_OUTPUT},
            ResultReuseConfiguration={
                "ResultReuseByAgeConfiguration": {
                    "Enabled": True,
                    "MaxAgeInMinutes": 60
                }
            },
        )
        execution_id = response["QueryExecutionId"]

        # 4. Esperar a que termine
        stats = _poll_execution(client, execution_id)
        status = stats["QueryExecution"]["Status"]["State"]

        # 5. Devolver el resultado
        if status == "SUCCEEDED":
            results = client.get_query_results(QueryExecutionId=execution_id)
            parsed = _parse_results(results)
            _set_cache(sql_query, parsed)
            return parsed

        reason = stats["QueryExecution"]["Status"].get("StateChangeReason", "Error desconocido")
        return f"Error SQL en Athena: {reason}"

    except TimeoutError:
        return "⏳ La consulta tardó demasiado tiempo. Intenta refinar la búsqueda."
    except Exception as e:
        return f"Excepción de conexión: {str(e)}"
