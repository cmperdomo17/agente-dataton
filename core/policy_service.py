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
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config as BotoConfig
from strands import tool

from core.config import AWS_REGION, POLICY_S3_BUCKET, POLICY_S3_PREFIX, POLICY_LOCAL_DIR
from core.utils import normalize_text

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
                key = f"{doc_name}::{normalize_text(current_title)}"
                text = "\n".join(current_lines).strip()
                if text:
                    sections[key] = text

            current_title = header_match.group(1).strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    # Guardar última sección
    if current_lines:
        key = f"{doc_name}::{normalize_text(current_title)}"
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

# Caché de secciones: lista de dicts con {key, text, key_norm, text_norm}
_sections_cache: list[dict[str, str]] = []
_full_docs: dict[str, str] = {}


def _load_from_s3() -> dict[str, str]:
    """Lee los archivos de políticas desde S3 en paralelo.

    Returns:
        Diccionario {nombre_archivo: contenido}.
    """
    client = _get_s3_client()
    docs: dict[str, str] = {}

    def _fetch_file(filename: str) -> tuple[str, str | None]:
        key = f"{POLICY_S3_PREFIX}{filename}"
        try:
            resp = client.get_object(Bucket=POLICY_S3_BUCKET, Key=key)
            content = resp["Body"].read().decode("utf-8")
            logger.debug("S3: cargado %s (%d bytes)", key, len(content))
            return filename, content
        except Exception:
            logger.warning("No se pudo cargar %s desde S3", key)
            return filename, None

    with ThreadPoolExecutor(max_workers=min(len(_DOC_MAP), 5)) as executor:
        results = list(executor.map(_fetch_file, _DOC_MAP.keys()))

    for filename, content in results:
        if content:
            docs[filename] = content

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

        # Parsear cada documento en secciones y pre-normalizar
        all_sections = []
        for filename, content in raw_docs.items():
            doc_name = _DOC_MAP.get(filename, filename)
            _full_docs[doc_name] = content
            sections = _parse_sections(content, doc_name)
            
            for key, text in sections.items():
                all_sections.append({
                    "key": key,
                    "text": text,
                    "key_norm": normalize_text(key),
                    "text_norm": normalize_text(text)
                })

        _sections_cache = all_sections
        _policy_loaded = True
        logger.info(
            "Políticas cargadas: %d documentos, %d secciones pre-normalizadas",
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


def _score_section(section_data: dict[str, str], query_tokens: list[str],
                   preferred_doc: str | None) -> float:
    """Calcula un puntaje de relevancia usando datos pre-normalizados.

    Args:
        section_data: Dict con {key, text, key_norm, text_norm}.
        query_tokens: Lista de tokens normalizados de la consulta.
        preferred_doc: Documento preferido por las keywords (puede ser None).

    Returns:
        Puntaje numérico de relevancia.
    """
    key_norm = section_data["key_norm"]
    text_norm = section_data["text_norm"]
    section_key = section_data["key"]

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

    query_norm = normalize_text(query)
    tokens = [t for t in query_norm.split() if len(t) > 2]

    # Determinar documento preferido por palabras clave
    preferred_doc: str | None = None
    for keyword, doc in _KEYWORD_BOOST.items():
        if keyword in query_norm:
            preferred_doc = doc
            break

    # Puntuar todas las secciones usando el caché pre-normalizado
    scored: list[tuple[float, str, str]] = []
    for section_data in _sections_cache:
        score = _score_section(section_data, tokens, preferred_doc)
        if score > 0:
            scored.append((score, section_data["key"], section_data["text"]))

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
        start = time.time()
        result = _find_relevant_sections(consulta)
        elapsed_ms = (time.time() - start) * 1000
        
        # Emitir la métrica al trace estructurado para el evaluador
        from core.session_context import add_tool_trace
        result_snippet = result[:500] if result else ""
        add_tool_trace(
            "consultar_politica",
            {"consulta": consulta},
            {"elapsed_ms": elapsed_ms, "result_length": len(result), "result_snippet": result_snippet}
        )
        
        logger.debug("consultar_politica('%s') → %d caracteres", consulta, len(result))
        return result
    except Exception as e:
        logger.exception("Error en consultar_politica('%s')", consulta)
        return f"Error consultando políticas: {str(e)}"
