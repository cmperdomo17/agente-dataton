"""
Servicio de consulta de políticas para el agente OmniRetail.

Arquitectura:
  - Carga los documentos Markdown de políticas desde S3 (o carpeta local como fallback).
  - Parsea cada documento en secciones indexadas por encabezados (##).
  - Caché lazy: se cargan una sola vez al primer uso, con thread-safety.
  - Búsqueda por relevancia de palabras clave para devolver solo las secciones pertinentes.
  - Una sola herramienta expuesta al agente: consultar_politica("pregunta del cliente").

Documentos:
  - Política de Devoluciones
  - Políticas de Envío
  - Política de Garantía
"""

import os
import re
import logging
import threading

import boto3
from botocore.config import Config as BotoConfig
from strands import tool

from core.config import AWS_REGION, POLICY_S3_BUCKET, POLICY_S3_PREFIX, POLICY_LOCAL_DIR

logger = logging.getLogger(__name__)

# ── Conexión S3 (lazy) ─────────────────────────────────────────────────

_s3_client = None


def _get_s3_client():
    """Obtiene el cliente S3 de forma lazy (se crea una sola vez)."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            region_name=AWS_REGION,
            config=BotoConfig(
                retries={"max_attempts": 3, "mode": "adaptive"},
                connect_timeout=5,
                read_timeout=10,
            ),
        )
    return _s3_client


# ── Normalización de texto ─────────────────────────────────────────────

_ACCENT_MAP = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")


def _normalize(text: str) -> str:
    """Convierte a minúsculas sin tildes para búsquedas tolerantes."""
    return text.lower().translate(_ACCENT_MAP).strip()


# ── Parseo de documentos Markdown ──────────────────────────────────────

def _parse_sections(content: str, doc_name: str) -> dict[str, str]:
    """Parsea un documento Markdown en secciones indexadas por encabezado.

    Cada sección se identifica por 'doc_name::titulo_seccion' normalizado.
    El contenido de nivel superior (antes del primer ##) se guarda como 'doc_name::general'.

    Args:
        content: Texto completo del documento Markdown.
        doc_name: Nombre corto del documento (ej: 'devoluciones').

    Returns:
        Diccionario {clave_seccion: contenido_seccion}.
    """
    sections: dict[str, str] = {}
    current_title = "general"
    current_lines: list[str] = []

    for line in content.splitlines():
        # Detectar encabezados ## (nivel 2) o ### (nivel 3)
        header_match = re.match(r"^#{2,3}\s+\**(.+?)\**\s*$", line)
        if header_match:
            # Guardar sección anterior si tiene contenido
            if current_lines:
                key = f"{doc_name}::{_normalize(current_title)}"
                text = "\n".join(current_lines).strip()
                if text:
                    sections[key] = text

            current_title = header_match.group(1).strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    # Guardar última sección
    if current_lines:
        key = f"{doc_name}::{_normalize(current_title)}"
        text = "\n".join(current_lines).strip()
        if text:
            sections[key] = text

    return sections


# ── Mapeo de archivos a nombres cortos ─────────────────────────────────

_DOC_MAP = {
    "Política de devoluciones.md": "devoluciones",
    "Políticas de envío.md": "envios",
    "Política de garantía.md": "garantia",
}


# ── Caché lazy (se carga una sola vez, con thread-safety) ──────────────

_policy_lock = threading.Lock()
_policy_loaded = False
_sections_cache: dict[str, str] = {}
_full_docs: dict[str, str] = {}


def _load_from_s3() -> dict[str, str]:
    """Lee los archivos de políticas desde S3.

    Returns:
        Diccionario {nombre_archivo: contenido}.
    """
    client = _get_s3_client()
    docs: dict[str, str] = {}

    for filename in _DOC_MAP:
        key = f"{POLICY_S3_PREFIX}{filename}"
        try:
            resp = client.get_object(Bucket=POLICY_S3_BUCKET, Key=key)
            content = resp["Body"].read().decode("utf-8")
            docs[filename] = content
            logger.debug("S3: cargado %s (%d bytes)", key, len(content))
        except Exception:
            logger.warning("No se pudo cargar %s desde S3", key)

    return docs


def _load_from_local() -> dict[str, str]:
    """Lee los archivos de políticas desde la carpeta local (fallback).

    Returns:
        Diccionario {nombre_archivo: contenido}.
    """
    docs: dict[str, str] = {}

    for filename in _DOC_MAP:
        filepath = os.path.join(POLICY_LOCAL_DIR, filename)
        if os.path.isfile(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                docs[filename] = f.read()
            logger.debug("Local: cargado %s", filepath)
        else:
            logger.warning("Archivo no encontrado: %s", filepath)

    return docs


def ensure_policies():
    """Carga y parsea los documentos de políticas la primera vez que se necesitan.

    Usa un lock para evitar cargas duplicadas en entornos multi-hilo.
    Intenta cargar desde S3 primero; si no hay bucket configurado o falla,
    usa la carpeta local como fallback.
    """
    global _policy_loaded, _sections_cache, _full_docs

    if _policy_loaded:
        return

    with _policy_lock:
        if _policy_loaded:  # Double-check dentro del lock
            return

        logger.info("Cargando documentos de políticas...")

        # Intentar S3 primero, fallback a local
        if POLICY_S3_BUCKET:
            raw_docs = _load_from_s3()
            if not raw_docs:
                logger.warning("S3 vacío o falló, usando fallback local")
                raw_docs = _load_from_local()
        else:
            logger.info("POLICY_S3_BUCKET no configurado, usando carpeta local")
            raw_docs = _load_from_local()

        # Parsear cada documento en secciones
        for filename, content in raw_docs.items():
            doc_name = _DOC_MAP.get(filename, filename)
            _full_docs[doc_name] = content
            sections = _parse_sections(content, doc_name)
            _sections_cache.update(sections)

        _policy_loaded = True
        logger.info(
            "Políticas cargadas: %d documentos, %d secciones",
            len(raw_docs), len(_sections_cache),
        )


# ══════════════════════════════════════════════════════════════════════════
# BÚSQUEDA DE SECCIONES RELEVANTES
# ══════════════════════════════════════════════════════════════════════════

# Palabras clave → secciones de política para mejorar la precisión de búsqueda
_KEYWORD_BOOST = {
    "devolucion": "devoluciones",
    "devolver": "devoluciones",
    "cambio": "devoluciones",
    "cambiar": "devoluciones",
    "reembolso": "devoluciones",
    "reembolsar": "devoluciones",
    "cancelar": "devoluciones",
    "cancelacion": "devoluciones",
    "envio": "envios",
    "envios": "envios",
    "entrega": "envios",
    "despacho": "envios",
    "tracking": "envios",
    "rastreo": "envios",
    "guia": "envios",
    "demora": "envios",
    "demorado": "envios",
    "factura": "envios",
    "facturacion": "envios",
    "garantia": "garantia",
    "reparacion": "garantia",
    "reparar": "garantia",
    "defecto": "garantia",
    "falla": "garantia",
    "servicio tecnico": "garantia",
    "instalacion": "garantia",
    "soporte": None,      # Busca en todos
    "contacto": None,
    "contactar": None,
    "direccion": None,
    "datos": None,
    "cuenta": None,
    "recibo": None,
    "comprobante": None,
}

# Máximo de secciones a devolver al agente
_MAX_SECTIONS = 5


def _score_section(section_key: str, section_text: str, query_tokens: list[str],
                   preferred_doc: str | None) -> float:
    """Calcula un puntaje de relevancia para una sección dado un conjunto de tokens.

    Args:
        section_key: Clave de la sección (ej: 'devoluciones::plazos').
        section_text: Contenido de la sección.
        query_tokens: Lista de tokens normalizados de la consulta.
        preferred_doc: Documento preferido por las keywords (puede ser None).

    Returns:
        Puntaje numérico de relevancia (mayor = más relevante).
    """
    key_norm = _normalize(section_key)
    text_norm = _normalize(section_text)

    score = 0.0

    # Coincidencia de tokens en el título de la sección (peso alto)
    for token in query_tokens:
        if token in key_norm:
            score += 3.0

    # Coincidencia de tokens en el contenido (peso normal)
    for token in query_tokens:
        count = text_norm.count(token)
        if count > 0:
            score += min(count, 5) * 0.5  # Cap para no sesgar por repetición

    # Boost si la sección pertenece al documento preferido
    if preferred_doc and section_key.startswith(preferred_doc + "::"):
        score += 2.0

    return score


def _find_relevant_sections(query: str) -> str:
    """Encuentra las secciones más relevantes para una consulta del usuario.

    Args:
        query: Pregunta del usuario en lenguaje natural.

    Returns:
        Texto con las secciones relevantes concatenadas, o un mensaje si no hay resultados.
    """
    ensure_policies()

    query_norm = _normalize(query)
    tokens = [t for t in query_norm.split() if len(t) > 2]

    # Determinar documento preferido por palabras clave
    preferred_doc: str | None = None
    for keyword, doc in _KEYWORD_BOOST.items():
        if keyword in query_norm:
            preferred_doc = doc
            break

    # Puntuar todas las secciones
    scored: list[tuple[float, str, str]] = []
    for key, text in _sections_cache.items():
        score = _score_section(key, text, tokens, preferred_doc)
        if score > 0:
            scored.append((score, key, text))

    # Ordenar por puntaje descendente
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return "No se encontró información relevante en las políticas."

    # Devolver las secciones más relevantes
    parts: list[str] = []
    for _score, key, text in scored[:_MAX_SECTIONS]:
        parts.append(text)

    return "\n\n---\n\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════
# HERRAMIENTA DEL AGENTE
# ══════════════════════════════════════════════════════════════════════════

@tool
def consultar_politica(consulta: str) -> str:
    """Consulta las políticas de OmniRetail sobre devoluciones, envíos y garantías.
    Usar para preguntas sobre: devoluciones, cambios, reembolsos, envíos, tiempos de entrega,
    tracking, facturación, garantías, reparaciones, contacto de soporte, datos de cuenta,
    cancelación de pedidos, dirección de envío, comprobantes y cualquier procedimiento.
    Pasar la pregunta del cliente como consulta.
    Ej: "plazos de devolución", "cómo contactar soporte", "pedido demorado opciones"
    """
    if not consulta or not consulta.strip():
        return "La consulta no puede estar vacía."

    try:
        result = _find_relevant_sections(consulta)
        logger.debug("consultar_politica('%s') → %d caracteres", consulta, len(result))
        return result
    except Exception as e:
        logger.exception("Error en consultar_politica('%s')", consulta)
        return f"Error consultando políticas: {str(e)}"
