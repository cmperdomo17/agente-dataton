"""
Servicio de consultas a DynamoDB para el agente OmniRetail.

Arquitectura:
  - Caché en memoria de tablas pequeñas (categorías, marcas, productos, clientes, promos).
  - Carga lazy: los datos se traen de DynamoDB la primera vez que se necesitan.
  - Una sola herramienta expuesta al agente: consultar_dynamo("OP:valor").

Tablas (prefijo omniretail_):
  customers, customer_emails, addresses, cards, categories, brands,
  products, stock, orders, order_items, tracking, shipments, promotions.
"""

import re
import time
import logging
import threading
from decimal import Decimal
from datetime import datetime

import boto3
from botocore.config import Config as BotoConfig
from boto3.dynamodb.conditions import Key
from strands import tool
from core.config import AWS_REGION, DYNAMO_PREFIX, MAX_ROWS, CURRENT_DATE

logger = logging.getLogger(__name__)

# ── Conexión DynamoDB ──────────────────────────────────────────────────

_boto_cfg = BotoConfig(
    retries={"max_attempts": 3, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=10,
)
_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION, config=_boto_cfg)
_table_refs: dict = {}


def _tbl(name: str):
    """Obtiene referencia lazy a una tabla DynamoDB (se cachea en memoria)."""
    if name not in _table_refs:
        _table_refs[name] = _dynamodb.Table(f"{DYNAMO_PREFIX}{name}")
    return _table_refs[name]


# ── Utilidades de formato ──────────────────────────────────────────────

_ACCENT_MAP = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")


def _normalize(text: str) -> str:
    """Convierte a minúsculas sin tildes para búsquedas tolerantes a errores."""
    return text.lower().translate(_ACCENT_MAP).strip()


def _fmt(v) -> str:
    """Formatea valores de DynamoDB: Decimal → entero o decimal legible."""
    if isinstance(v, Decimal):
        return str(int(v)) if v == v.to_integral_value() else str(v)
    return "" if v is None else str(v)


# Traducción de columnas técnicas → español para el usuario
_COL_LABELS = {
    "customer_id": "id_cliente", "product_id": "id_producto",
    "order_id": "id_pedido", "order_date": "fecha_pedido",
    "item_id": "id_item", "promotion_id": "id_promo",
    "stock_id": "id_stock", "card_id": "id_tarjeta",
    "email_id": "id_email", "address_id": "id_direccion",
    "shipment_id": "id_envio", "tracking_id": "id_tracking",
    "dni": "cedula", "tipo_id": "tipo_doc",
    "name": "nombre", "last_name1": "apellido1", "last_name2": "apellido2",
    "phone": "telefono", "birthday": "nacimiento",
    "account_status": "estado_cuenta", "is_premium": "premium",
    "registration_date": "fecha_registro",
    "email": "correo", "email_type": "tipo_correo",
    "is_primary": "principal", "is_verified": "verificado",
    "price": "precio", "active": "activo",
    "available_qty": "disponible", "stock_qty": "stock",
    "reserved_qty": "reservado", "restock_date": "fecha_restock",
    "brand_name": "marca", "category_name": "categoria",
    "warranty_months": "garantia_meses", "return_days": "dias_devolucion",
    "free_shipping": "envio_gratis", "is_final_sale": "venta_final",
    "shipping_days": "dias_envio", "weight_kg": "peso_kg",
    "specifications": "especificaciones", "description": "descripcion",
    "requires_installation": "requiere_instalacion",
    "installation_notes": "notas_instalacion",
    "status": "estado", "total_amount": "total", "subtotal": "subtotal",
    "shipping_cost": "costo_envio", "tax": "impuesto",
    "payment_method": "metodo_pago", "delivery_method": "metodo_envio",
    "payment_confirmed_at": "pago_confirmado",
    "shipped_at": "enviado_el", "delivered_at": "entregado_el",
    "cancelled_at": "cancelado_el", "cancellation_reason": "razon_cancelacion",
    "customer_notes": "notas_cliente", "internal_notes": "notas_internas",
    "item_status": "estado_item", "qty": "cantidad",
    "unit_price": "precio_unitario",
    "warranty_expires_at": "vence_garantia",
    "return_deadline": "limite_devolucion",
    "product_name": "producto",
    "carrier": "transportadora", "tracking_number": "guia",
    "tracking_url": "url_rastreo",
    "shipment_status": "estado_envio",
    "shipped_date": "fecha_envio",
    "estimated_delivery_date": "entrega_estimada",
    "actual_delivery_date": "entrega_real",
    "delivery_attempts": "intentos_entrega",
    "last_attempt_date": "ultimo_intento",
    "failed_delivery_reason": "razon_fallo",
    "address_line1": "direccion", "address_line2": "complemento",
    "city": "ciudad", "department": "departamento",
    "postal_code": "cod_postal", "country": "pais",
    "delivery_notes": "notas_entrega", "landmark": "referencia",
    "requires_appointment": "requiere_cita",
    "address_type": "tipo_direccion", "is_default": "predeterminada",
    "is_residential": "residencial",
    "card_type": "tipo_tarjeta", "bank": "banco",
    "last_four": "ultimos_4", "bin": "bin",
    "expiration_date": "vencimiento",
    "promotion_name": "nombre_promo",
    "discount_type": "tipo_descuento", "discount_value": "descuento",
    "min_purchase_amount": "compra_minima",
    "start_date": "inicio", "end_date": "fin",
    "applicable_category_ids": "categorias",
    "applicable_product_ids": "productos",
    "timestamp": "fecha_hora", "location": "ubicacion",
    "warehouse_location": "bodega",
    "low_stock_threshold": "umbral_bajo",
    "last_updated": "ultima_actualizacion",
    "is_return_eligible": "es_elegible_devolucion",
    "rejection_reason": "motivo_rechazo",
}

# Traducción de valores del sistema → español natural
_VAL_TR = {
    "true": "Sí", "false": "No",
    "active": "Activo", "inactive": "Inactivo", "suspended": "Suspendido",
    "pending": "Pendiente", "payment_confirmed": "Pago confirmado",
    "preparing": "En preparación",
    "shipped": "Enviado", "in_transit": "En tránsito",
    "out_for_delivery": "En camino", "delivered": "Entregado",
    "cancelled": "Cancelado", "returned": "Devuelto",
    "refunded": "Reembolsado", "replaced": "Reemplazado",
    "returned_to_sender": "Devuelto al remitente",
    "order_placed": "Pedido realizado",
    "ready_for_pickup": "Listo para recoger",
    "personal": "Personal", "work": "Trabajo", "other": "Otro",
    "home": "Casa",
    "home_delivery": "Domicilio", "pickup_point": "Punto de recogida",
    "tarjeta_credito": "Tarjeta crédito", "tarjeta_debito": "Tarjeta débito",
    "contraentrega": "Contra entrega",
    "PSE": "PSE", "nequi": "Nequi", "daviplata": "Daviplata",
    "percentage": "Porcentaje", "fixed_amount": "Monto fijo",
    "free_shipping": "Envío gratis",
    "CC": "Cédula", "CE": "Cédula Extranjería", "NIT": "NIT", "PA": "Pasaporte",
    "Visa": "Visa", "Mastercard": "Mastercard",
    "American Express": "American Express", "Diners Club": "Diners Club",
}


def _tr(v):
    """Traduce un valor del sistema a su equivalente en español."""
    if not isinstance(v, str):
        return v
    s = str(v).strip()
    return _VAL_TR.get(s, _VAL_TR.get(s.lower(), s))


def _to_table(items: list[dict], columns: list[str]) -> str:
    """Formatea una lista de ítems como tabla texto (pipe-separated) para el agente."""
    if not items:
        return "Sin resultados (0 filas)."
    headers = [_COL_LABELS.get(c, c) for c in columns]
    lines = [" | ".join(headers)]
    for item in items[:MAX_ROWS]:
        lines.append(" | ".join(_tr(_fmt(item.get(c, ""))) for c in columns))
    return "\n".join(lines)


# ── Scan completo (para tablas pequeñas) ───────────────────────────────

def _scan_all(table_name: str) -> list[dict]:
    """Recorre toda una tabla DynamoDB paginando automáticamente."""
    table = _tbl(table_name)
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    logger.debug("Scan %s: %d items", table_name, len(items))
    return items


# ── Caché lazy (se carga una sola vez, con thread-safety) ──────────────

_cache_lock = threading.Lock()
_cache_loaded = False

_cats_cache: dict = {}
_brands_cache: dict = {}
_products_cache: list = []
_customers_cache: list = []
_promotions_cache: list = []
_product_map: dict = {}


def ensure_caches():
    """Carga los catálogos en memoria la primera vez que se necesitan.

    Usa un lock para evitar cargas duplicadas en entornos multi-hilo.
    Si DynamoDB falla, se registra el error y se re-lanza la excepción.
    """
    global _cache_loaded, _cats_cache, _brands_cache
    global _products_cache, _customers_cache, _promotions_cache, _product_map

    if _cache_loaded:
        return

    with _cache_lock:
        if _cache_loaded:  # Double-check dentro del lock
            return

        logger.info("Cargando catálogos desde DynamoDB...")
        start = time.time()

        try:
            # Catálogos pequeños
            _cats_cache = {
                str(int(c["category_id"])): c.get("name", "")
                for c in _scan_all("categories")
            }
            _brands_cache = {
                str(int(b["brand_id"])): b.get("name", "")
                for b in _scan_all("brands")
            }
            stock_map = {
                str(int(s["product_id"])): s
                for s in _scan_all("stock")
            }

            # Productos enriquecidos con stock, categoría y marca
            _products_cache = _scan_all("products")
            for p in _products_cache:
                pid = str(int(p.get("product_id", 0)))
                p["category_name"] = _cats_cache.get(str(int(p.get("category_id", 0))), "")
                p["brand_name"] = _brands_cache.get(str(int(p.get("brand_id", 0))), "")
                st = stock_map.get(pid, {})
                p["stock_qty"] = st.get("stock_qty", 0)
                p["reserved_qty"] = st.get("reserved_qty", 0)
                try:
                    p["available_qty"] = int(p["stock_qty"]) - int(p["reserved_qty"])
                except (ValueError, TypeError):
                    p["available_qty"] = 0
                p["warehouse_location"] = st.get("warehouse_location", "")
                p["restock_date"] = st.get("restock_date", "")
                p["name_normalized"] = _normalize(p.get("name", ""))

            # Clientes con nombre normalizado para búsquedas
            _customers_cache = _scan_all("customers")
            for c in _customers_cache:
                full = f"{c.get('name', '')} {c.get('last_name1', '')} {c.get('last_name2', '')}".strip()
                c["name_normalized"] = _normalize(full)

            # Promociones
            _promotions_cache = _scan_all("promotions")

            # Mapa rápido product_id → producto enriquecido
            _product_map = {
                str(int(p["product_id"])): p
                for p in _products_cache if "product_id" in p
            }

            _cache_loaded = True
            elapsed = time.time() - start
            logger.info(
                "Catálogos cargados: %d productos, %d clientes, %d promos (%.1fs)",
                len(_products_cache), len(_customers_cache),
                len(_promotions_cache), elapsed,
            )
        except Exception:
            logger.exception("Error cargando catálogos desde DynamoDB")
            raise


# ══════════════════════════════════════════════════════════════════════════
# OPERACIONES DE CONSULTA
# ══════════════════════════════════════════════════════════════════════════

def _buscar_producto(nombre: str) -> str:
    """Busca productos cuyo nombre contenga todos los términos indicados."""
    ensure_caches()
    term = _normalize(nombre)
    tokens = term.split()
    # Primero intenta coincidencia con todos los tokens
    items = [p for p in _products_cache if all(t in p.get("name_normalized", "") for t in tokens)]
    # Si no hay resultados y hay múltiples tokens, relaja la búsqueda
    if not items and len(tokens) > 1:
        items = [p for p in _products_cache if any(t in p.get("name_normalized", "") for t in tokens)]
    cols = [
        "product_id", "name", "price", "active", "available_qty",
        "stock_qty", "reserved_qty", "restock_date",
        "brand_name", "category_name", "warranty_months", "return_days",
        "free_shipping", "is_final_sale",
    ]
    return _to_table(items, cols)


def _buscar_cliente_dni(dni: str) -> str:
    """Busca un cliente por número de documento (cédula)."""
    ensure_caches()
    d = dni.strip()
    items = [c for c in _customers_cache if str(c.get("dni", "")) == d]
    cols = [
        "customer_id", "tipo_id", "dni", "name", "last_name1", "last_name2",
        "phone", "account_status", "is_premium",
    ]
    return _to_table(items, cols)
        "customer_id", "tipo_id", "dni", "name", "last_name1", "last_name2",
        "phone", "account_status", "is_premium",
    ]
    return _to_table(items, cols)


def _buscar_cliente_phone(phone: str) -> str:
    """Busca un cliente por número de celular (coincidencia parcial de dígitos)."""
    ensure_caches()
    digits = re.sub(r"[^\d+]", "", phone.strip())
    items = [
    """Busca un cliente por número de celular (coincidencia parcial de dígitos)."""
    ensure_caches()
    digits = re.sub(r"[^\d+]", "", phone.strip())
    items = [
        c for c in _customers_cache
        if digits in re.sub(r"[^\d+]", "", str(c.get("phone", "")))
        or phone.strip() in str(c.get("phone", ""))
    ]
    cols = [
        "customer_id", "tipo_id", "dni", "name", "last_name1", "last_name2",
        "phone", "account_status", "is_premium",
    ]
    return _to_table(items, cols)
        if digits in re.sub(r"[^\d+]", "", str(c.get("phone", "")))
        or phone.strip() in str(c.get("phone", ""))
    ]
    cols = [
        "customer_id", "tipo_id", "dni", "name", "last_name1", "last_name2",
        "phone", "account_status", "is_premium",
    ]
    return _to_table(items, cols)


def _buscar_cliente_nombre(nombre: str) -> str:
    """Busca clientes cuyo nombre completo contenga todos los términos."""
    ensure_caches()
    term = _normalize(nombre)
    tokens = term.split()
    items = [c for c in _customers_cache if all(t in c.get("name_normalized", "") for t in tokens)]
    # Relajar: aceptar si coinciden N-1 de N tokens
    if not items and len(tokens) > 1:
        items = [
            c for c in _customers_cache
            if sum(1 for t in tokens if t in c.get("name_normalized", "")) >= len(tokens) - 1
        ]
    cols = [
        "customer_id", "tipo_id", "dni", "name", "last_name1", "last_name2",
        "phone", "account_status", "is_premium",
    ]
    return _to_table(items, cols)


def _perfil_completo_cliente(customer_id: str) -> str:
    """Obtiene el perfil completo: datos personales, emails, direcciones y tarjetas."""
    cid = int(customer_id.strip())

    resp = _tbl("customers").get_item(Key={"customer_id": cid})
    profile = [resp["Item"]] if "Item" in resp else []

    resp = _tbl("customer_emails").query(KeyConditionExpression=Key("customer_id").eq(cid))
    emails = resp.get("Items", [])

    resp = _tbl("addresses").query(KeyConditionExpression=Key("customer_id").eq(cid))
    addresses = resp.get("Items", [])

    resp = _tbl("cards").query(KeyConditionExpression=Key("customer_id").eq(cid))
    cards = resp.get("Items", [])

    parts = []
    if profile:
        parts.append("CLIENTE:")
        parts.append(_to_table(profile, [
            "customer_id", "tipo_id", "dni", "name", "last_name1", "last_name2",
            "phone", "birthday", "account_status", "is_premium", "registration_date",
        ]))
    if emails:
        parts.append("\nEMAILS:")
        parts.append(_to_table(emails, ["email", "email_type", "is_primary", "is_verified"]))
    if addresses:
        parts.append("\nDIRECCIONES:")
        parts.append(_to_table(addresses, [
            "address_id", "address_line1", "city", "department", "address_type", "is_default",
        ]))
    if cards:
        parts.append("\nTARJETAS:")
        parts.append(_to_table(cards, ["card_id", "card_type", "bank", "last_four", "is_primary"]))

    return "\n".join(parts) if parts else "Sin resultados (0 filas)."


def _pedidos_cliente(customer_id: str) -> str:
    """Lista los pedidos de un cliente, ordenados por fecha más reciente."""
    cid = int(customer_id.strip())
    resp = _tbl("orders").query(
        IndexName="customer-orders-index",
        KeyConditionExpression=Key("customer_id").eq(cid),
        ScanIndexForward=False,
        Limit=20,
    )
    items = resp.get("Items", [])
    cols = ["order_id", "order_date", "status", "total_amount", "payment_method", "delivery_method"]
    return _to_table(items, cols)
def _pedidos_cliente(customer_id: str) -> str:
    """Lista los pedidos de un cliente, ordenados por fecha más reciente."""
    cid = int(customer_id.strip())
    resp = _tbl("orders").query(
        IndexName="customer-orders-index",
        KeyConditionExpression=Key("customer_id").eq(cid),
        ScanIndexForward=False,
        Limit=20,
    )
    items = resp.get("Items", [])
    cols = ["order_id", "order_date", "status", "total_amount", "payment_method", "delivery_method"]
    return _to_table(items, cols)


def _detalle_pedido(order_id: str) -> str:
    """Detalle completo de un pedido: resumen, ítems con producto, envíos y tracking."""
    oid = int(order_id.strip())

    resp = _tbl("orders").get_item(Key={"order_id": oid})
    order = resp.get("Item")

    # Ítems del pedido, enriquecidos con datos del producto desde caché
    ensure_caches()
    resp = _tbl("order_items").query(KeyConditionExpression=Key("order_id").eq(oid))
    items = resp.get("Items", [])
    for it in items:
        pid = str(int(it.get("product_id", 0)))
        prod = _product_map.get(pid, {})
        it["product_name"] = prod.get("name", "")
        it["warranty_months"] = prod.get("warranty_months", "")
        it["return_days"] = prod.get("return_days", "")
        it["is_final_sale"] = prod.get("is_final_sale", "")

    resp = _tbl("shipments").query(KeyConditionExpression=Key("order_id").eq(oid))
    shipments = resp.get("Items", [])

    resp = _tbl("tracking").query(KeyConditionExpression=Key("order_id").eq(oid))
    tracking = resp.get("Items", [])
def _detalle_pedido(order_id: str) -> str:
    """Detalle completo de un pedido: resumen, ítems con producto, envíos y tracking."""
    oid = int(order_id.strip())

    resp = _tbl("orders").get_item(Key={"order_id": oid})
    order = resp.get("Item")

    # Ítems del pedido, enriquecidos con datos del producto desde caché
    ensure_caches()
    resp = _tbl("order_items").query(KeyConditionExpression=Key("order_id").eq(oid))
    items = resp.get("Items", [])
    for it in items:
        pid = str(int(it.get("product_id", 0)))
        prod = _product_map.get(pid, {})
        it["product_name"] = prod.get("name", "")
        it["warranty_months"] = prod.get("warranty_months", "")
        it["return_days"] = prod.get("return_days", "")
        it["is_final_sale"] = prod.get("is_final_sale", "")

    resp = _tbl("shipments").query(KeyConditionExpression=Key("order_id").eq(oid))
    shipments = resp.get("Items", [])

    resp = _tbl("tracking").query(KeyConditionExpression=Key("order_id").eq(oid))
    tracking = resp.get("Items", [])

    parts = []
    if order:
    if order:
        parts.append("PEDIDO:")
        parts.append(_to_table([order], [
            "order_id", "customer_id", "status", "order_date",
            "total_amount", "subtotal", "shipping_cost", "tax",
        parts.append(_to_table([order], [
            "order_id", "customer_id", "status", "order_date",
            "total_amount", "subtotal", "shipping_cost", "tax",
            "payment_method", "delivery_method",
        ]))
    if items:
        # Calcular elegibilidad determinística en el backend
        order_status = (order.get("status") or "").strip().lower() if order else ""

        for it in items:
            raw_deadline = it.get("return_deadline")
            item_status = (it.get("item_status") or "").strip().lower()
            is_final = bool(it.get("is_final_sale") is True or str(it.get("is_final_sale")).lower() == "true")

            reasons = []

            # 1. Validar estado del pedido
            if order_status not in ("delivered", "entregado"):
                reasons.append(f"Pedido en estado '{_tr(order_status)}' (solo entregados)")

            # 2. Validar estado del item
            if item_status != "active":
                reasons.append(f"Item en estado '{_tr(item_status)}'")

            # 3. Validar venta final
            if is_final:
                reasons.append("Es venta final")

            # 4. Validar deadline correctamente
            if raw_deadline:
                try:
                    # CURRENT_DATE en config es string, comparamos contra string o convertimos ambos
                    if raw_deadline < CURRENT_DATE:
                        reasons.append(f"Plazo vencido el {raw_deadline}")
                except (ValueError, TypeError):
                    reasons.append("Formato de fecha inválido en return_deadline")
            else:
                # Deadline faltante solo es problema si el item podría ser elegible
                if not is_final and order_status in ("delivered", "entregado"):
                    reasons.append("Plazo no definido")

            # Resultado determinístico — string 'Sí'/'No' para que el agente lo interprete correctamente
            it["is_return_eligible"] = "No" if reasons else "Sí"
            it["rejection_reason"] = "; ".join(reasons) if reasons else ""

        # Flag agregado a nivel pedido
        order["has_returnable_items"] = any(it["is_return_eligible"] == "Sí" for it in items)

        parts.append("\nITEMS:")
        parts.append(_to_table(items, [
            "product_name",
            "qty",
            "unit_price",
            "item_status",
            "return_deadline",
            "is_return_eligible",
            "rejection_reason",
            "warranty_months",
            "is_final_sale",
        ]))
    if shipments:
        parts.append("\nENVÍOS:")
        parts.append(_to_table(shipments, [
            "shipment_id", "carrier", "tracking_number", "shipment_status",
            "shipped_date", "estimated_delivery_date", "actual_delivery_date",
            "delivery_attempts",
        ]))
    if tracking:
        tracking.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
        parts.append("\nTRACKING:")
        parts.append(_to_table(tracking[:10], ["timestamp", "status", "location"]))

    return "\n".join(parts) if parts else "Sin resultados (0 filas)."


def _direccion_pedido(order_id: str) -> str:
    """Obtiene la dirección de entrega asociada a un pedido."""
    oid = int(order_id.strip())
    resp = _tbl("orders").get_item(Key={"order_id": oid})
    order = resp.get("Item")
    if not order:
        return "No se encontró el pedido."

    cid = order.get("customer_id")
    aid = order.get("address_id")
    if not cid or not aid:
def _direccion_pedido(order_id: str) -> str:
    """Obtiene la dirección de entrega asociada a un pedido."""
    oid = int(order_id.strip())
    resp = _tbl("orders").get_item(Key={"order_id": oid})
    order = resp.get("Item")
    if not order:
        return "No se encontró el pedido."

    cid = order.get("customer_id")
    aid = order.get("address_id")
    if not cid or not aid:
        return "El pedido no tiene dirección de entrega asociada."

    cid, aid = int(cid), int(aid)
    resp = _tbl("addresses").query(
        KeyConditionExpression=Key("customer_id").eq(cid) & Key("address_id").eq(aid),
        KeyConditionExpression=Key("customer_id").eq(cid) & Key("address_id").eq(aid),
    )
    items = resp.get("Items", [])
    if not items:
        # Fallback: traer todas las direcciones del cliente
        resp = _tbl("addresses").query(KeyConditionExpression=Key("customer_id").eq(cid))
        items = resp.get("Items", [])

    cols = [
        "address_line1", "address_line2", "city", "department",
        "postal_code", "country", "delivery_notes", "landmark",
        "address_type", "is_default",
    ]
    return _to_table(items, cols)


def _info_promocion(promotion_id: str) -> str:
    """Obtiene los detalles de una promoción por su ID."""
    pid = int(promotion_id.strip())
    resp = _tbl("promotions").get_item(Key={"promotion_id": pid})
    item = resp.get("Item")
    if not item:
        return "Sin resultados (0 filas)."
    # Enriquecer con nombres de categorías desde caché
    ensure_caches()
    cats = str(item.get("applicable_category_ids", ""))
    if cats:
        names = [_cats_cache.get(c.strip(), c.strip()) for c in cats.split("|") if c.strip()]
        item["_cat_names"] = ", ".join(names)
    cols = [
        "promotion_id", "promotion_name", "description",
        "discount_type", "discount_value", "min_purchase_amount",
        "start_date", "end_date", "active",
        "applicable_category_ids", "applicable_product_ids",
    ]
    return _to_table([item], cols)


def _promos_activas(_value: str) -> str:
    """Lista todas las promociones marcadas como activas."""
    ensure_caches()
    activas = [p for p in _promotions_cache if str(p.get("active", "")).lower() == "true"]
    cols = [
        "promotion_id", "promotion_name", "discount_type", "discount_value",
        "min_purchase_amount", "start_date", "end_date",
        "applicable_category_ids", "applicable_product_ids",
    ]
    return _to_table(activas, cols)


def _promos_producto(product_id: str) -> str:
    """Encuentra promociones activas aplicables a un producto (por ID o categoría)."""
    ensure_caches()
    pid = product_id.strip()
    prod = _product_map.get(pid)
    if not prod:
        return f"Producto {pid} no encontrado."

    cat_id = str(int(prod.get("category_id", 0)))
    matching = []
    for p in _promotions_cache:
        if str(p.get("active", "")).lower() != "true":
            continue
        app_prods = str(p.get("applicable_product_ids", ""))
        app_cats = str(p.get("applicable_category_ids", ""))
        prod_ids = [x.strip() for x in app_prods.split("|") if x.strip()]
        cat_ids = [x.strip() for x in app_cats.split("|") if x.strip()]
        if pid in prod_ids or cat_id in cat_ids:
            matching.append(p)

    if not matching:
        return f"No hay promociones activas para el producto {prod.get('name', pid)}."

    cols = [
        "promotion_id", "promotion_name", "discount_type", "discount_value",
        "min_purchase_amount", "start_date", "end_date",
    ]
    return _to_table(matching, cols)


def _productos_categoria(category_id: str) -> str:
    """Lista productos de una categoría usando el GSI category-index."""
    cid = int(category_id.strip())
    ensure_caches()
    resp = _tbl("products").query(
        IndexName="category-index",
        KeyConditionExpression=Key("category_id").eq(cid),
        Limit=30,
    )
    items = resp.get("Items", [])
    for p in items:
        pid = str(int(p.get("product_id", 0)))
        cached = _product_map.get(pid, {})
        p["available_qty"] = cached.get("available_qty", "")
        p["brand_name"] = cached.get("brand_name", "")
    cols = [
        "product_id", "name", "price", "brand_name",
        "available_qty", "active", "warranty_months",
    ]
    return _to_table(items, cols)


def _consultar_stock(product_id: str) -> str:
    """Muestra el stock detallado de un producto específico."""
    ensure_caches()
    pid = product_id.strip()
    prod = _product_map.get(pid)
    if not prod:
        return f"Producto {pid} no encontrado."
    cols = [
        "product_id", "name", "price", "active",
        "stock_qty", "reserved_qty", "available_qty",
        "warehouse_location", "restock_date",
        "brand_name", "category_name",
    ]
    return _to_table([prod], cols)


# ══════════════════════════════════════════════════════════════════════════
# TABLA DE OPERACIONES DISPONIBLES
# ══════════════════════════════════════════════════════════════════════════

_OPERATIONS = {
    "PRODUCTO":         _buscar_producto,
    "CLIENTE_DNI":      _buscar_cliente_dni,
    "CLIENTE_PHONE":    _buscar_cliente_phone,
    "CLIENTE_NOMBRE":   _buscar_cliente_nombre,
    "PERFIL_CLIENTE":   _perfil_completo_cliente,
    "PEDIDOS":          _pedidos_cliente,
    "DETALLE_PEDIDO":   _detalle_pedido,
    "DIRECCION_PEDIDO": _direccion_pedido,
    "PROMOCION":        _info_promocion,
    "PROMOS_ACTIVAS":   _promos_activas,
    "PROMOS_PRODUCTO":  _promos_producto,
    "PRODUCTOS_CAT":    _productos_categoria,
    "STOCK":            _consultar_stock,
}


# ══════════════════════════════════════════════════════════════════════════
# HERRAMIENTA PRINCIPAL DEL AGENTE
# ══════════════════════════════════════════════════════════════════════════
    "PRODUCTO":         _buscar_producto,
    "CLIENTE_DNI":      _buscar_cliente_dni,
    "CLIENTE_PHONE":    _buscar_cliente_phone,
    "CLIENTE_NOMBRE":   _buscar_cliente_nombre,
    "PERFIL_CLIENTE":   _perfil_completo_cliente,
    "PEDIDOS":          _pedidos_cliente,
    "DETALLE_PEDIDO":   _detalle_pedido,
    "DIRECCION_PEDIDO": _direccion_pedido,
    "PROMOCION":        _info_promocion,
    "PROMOS_ACTIVAS":   _promos_activas,
    "PROMOS_PRODUCTO":  _promos_producto,
    "PRODUCTOS_CAT":    _productos_categoria,
    "STOCK":            _consultar_stock,
}


# ══════════════════════════════════════════════════════════════════════════
# HERRAMIENTA PRINCIPAL DEL AGENTE
# ══════════════════════════════════════════════════════════════════════════

@tool
def consultar_dynamo(operacion: str) -> str:
    """Consulta rápida a DynamoDB. Formato: OPERACION:valor.
    Ops: PRODUCTO:<nombre>, CLIENTE_DNI:<dni>, CLIENTE_PHONE:<tel>, CLIENTE_NOMBRE:<nombre>,
    PERFIL_CLIENTE:<cid>, PEDIDOS:<cid>, DETALLE_PEDIDO:<oid>, DIRECCION_PEDIDO:<oid>,
    PROMOCION:<pid>, PROMOS_ACTIVAS:1, PROMOS_PRODUCTO:<product_id>,
    PRODUCTOS_CAT:<catid>, STOCK:<product_id>.
    Ej: "PRODUCTO:monitor lg" o "CLIENTE_DNI:12345" o "PROMOS_ACTIVAS:1"
    """
    start = time.time()

    if ":" not in operacion:
        return f"Formato inválido. Use OPERACION:valor. Operaciones: {', '.join(_OPERATIONS.keys())}"
        return f"Formato inválido. Use OPERACION:valor. Operaciones: {', '.join(_OPERATIONS.keys())}"

    op_name, _, value = operacion.partition(":")
    op_name = op_name.strip().upper()
    value = value.strip()

    if not value:
        return "El valor no puede estar vacío."
        return "El valor no puede estar vacío."

    handler = _OPERATIONS.get(op_name)
    if not handler:
        return f"Operación desconocida: '{op_name}'. Disponibles: {', '.join(_OPERATIONS.keys())}"

    try:
        result = handler(value)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("consultar_dynamo(%s) → %.0fms", operacion, elapsed_ms)
        logger.debug("consultar_dynamo(%s) → %.0fms", operacion, elapsed_ms)
        return f"{result}\n\n[DynamoDB: {elapsed_ms:.0f}ms]"
    except Exception as e:
        logger.exception("Error en consultar_dynamo(%s)", operacion)
        logger.exception("Error en consultar_dynamo(%s)", operacion)
        return f"Error en consulta DynamoDB: {str(e)}"