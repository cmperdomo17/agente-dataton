"""
Servicio de consultas rápidas a DynamoDB para el agente OmniRetail.

Este módulo se encarga de las búsquedas más frecuentes (clientes, productos,
pedidos, etc.) con tiempos de respuesta casi instantáneos (~10-50ms),
en lugar de usar Athena que puede tardar varios segundos.
"""

import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr
from strands import tool
from core.config import AWS_REGION, ATHENA_MAX_ROWS

DYNAMO_TABLE = "OmniRetailData"

_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
_table = _dynamodb.Table(DYNAMO_TABLE)


# ── Funciones auxiliares ───────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Convierte el texto a minúsculas y elimina tildes para facilitar las búsquedas."""
    trans = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return text.lower().translate(trans).strip()


def _fmt_value(v) -> str:
    if isinstance(v, Decimal):
        return str(int(v)) if v == v.to_integral_value() else str(v)
    return "" if v is None else str(v)


# Nombres de columnas traducidos a español para que el usuario los entienda
_COL_LABELS = {
    "customer_id": "id_cliente",
    "product_id": "id_producto",
    "order_id": "id_pedido",
    "order_date": "fecha_pedido",
    "item_id": "id_item",
    "ticket_id": "id_ticket",
    "promotion_id": "id_promo",
    "stock_id": "id_stock",
    "dni": "cedula",
    "name": "nombre",
    "last_name1": "apellido1",
    "last_name2": "apellido2",
    "phone": "telefono",
    "account_status": "estado_cuenta",
    "is_premium": "premium",
    "email": "correo",
    "email_type": "tipo_correo",
    "is_primary": "principal",
    "price": "precio",
    "active": "activo",
    "available_qty": "disponible",
    "stock_qty": "stock",
    "reserved_qty": "reservado",
    "restock_date": "fecha_restock",
    "brand_name": "marca",
    "category_name": "categoria",
    "warranty_months": "garantia_meses",
    "return_days": "dias_devolucion",
    "free_shipping": "envio_gratis",
    "is_final_sale": "venta_final",
    "status": "estado",
    "total_amount": "total",
    "subtotal": "subtotal",
    "payment_method": "metodo_pago",
    "delivery_method": "metodo_envio",
    "item_status": "estado_item",
    "qty": "cantidad",
    "unit_price": "precio_unitario",
    "discount_per_unit": "descuento_unidad",
    "warranty_expires_at": "vence_garantia",
    "return_deadline": "limite_devolucion",
    "carrier": "transportadora",
    "tracking_number": "guia",
    "shipment_status": "estado_envio",
    "estimated_delivery_date": "entrega_estimada",
    "address_line1": "direccion",
    "city": "ciudad",
    "department": "departamento",
    "address_type": "tipo_direccion",
    "is_default": "principal",
    "card_type": "tipo_tarjeta",
    "bank": "banco",
    "last_four": "ultimos_4",
    "is_primary": "principal",
    "subject": "asunto",
    "category": "categoria",
    "priority": "prioridad",
    "promotion_name": "nombre_promo",
    "promotion_type": "tipo_promo",
    "discount_value": "descuento",
    "start_date": "inicio",
    "end_date": "fin",
    "timestamp": "fecha_hora",
    "location": "ubicacion",
    "entity": "tipo",
    "specifications": "especificaciones",
    "description": "descripcion",
}

# Valores del sistema traducidos a español para mostrar al usuario
_VAL_TRANSLATIONS = {
    "true": "Sí", "false": "No",
    "active": "Activo", "inactive": "Inactivo", "suspended": "Suspendido",
    "pending": "Pendiente", "preparing": "En preparación",
    "shipped": "Enviado", "in_transit": "En tránsito",
    "out_for_delivery": "En camino", "delivered": "Entregado",
    "cancelled": "Cancelado", "returned": "Devuelto",
    "refunded": "Reembolsado", "replaced": "Reemplazado",
    "personal": "Personal", "work": "Trabajo", "other": "Otro",
    "home_delivery": "Domicilio", "store_pickup": "Recoge en tienda",
    "credit_card": "Tarjeta crédito", "debit_card": "Tarjeta débito",
    "cash_on_delivery": "Contra entrega", "bank_transfer": "Transferencia",
    "open": "Abierto", "closed": "Cerrado", "resolved": "Resuelto",
    "in_progress": "En progreso",
    "low": "Baja", "medium": "Media", "high": "Alta",
    "customer": "Cliente", "product": "Producto", "order": "Pedido",
    "email": "Correo", "address": "Dirección",
}


def _translate_val(v: str) -> str:
    return _VAL_TRANSLATIONS.get(v.lower().strip(), v) if isinstance(v, str) else v


def _items_to_table(items: list[dict], columns: list[str]) -> str:
    if not items:
        return "Sin resultados (0 filas)."
    headers = [_COL_LABELS.get(c, c) for c in columns]
    lines = [" | ".join(headers)]
    for item in items[:ATHENA_MAX_ROWS]:
        lines.append(" | ".join(_translate_val(_fmt_value(item.get(c, ""))) for c in columns))
    return "\n".join(lines)


def _query_table(pk_value: str, sk_prefix: str = None, limit: int = 50) -> list[dict]:
    """Busca registros en la tabla principal usando la clave primaria."""
    key_condition = Key("pk").eq(pk_value)
    if sk_prefix:
        key_condition = key_condition & Key("sk").begins_with(sk_prefix)

    resp = _table.query(
        KeyConditionExpression=key_condition,
        Limit=limit,
    )
    return resp.get("Items", [])


def _query_gsi1(gsi1pk_value: str, gsi1sk_prefix: str = None, limit: int = 50) -> list[dict]:
    """Busca registros usando el índice secundario (útil para buscar por cédula, categoría, etc.)."""
    key_condition = Key("gsi1pk").eq(gsi1pk_value)
    if gsi1sk_prefix:
        key_condition = key_condition & Key("gsi1sk").begins_with(gsi1sk_prefix)

    resp = _table.query(
        IndexName="GSI1",
        KeyConditionExpression=key_condition,
        Limit=limit,
    )
    return resp.get("Items", [])


# ── Caché en memoria (se carga una sola vez al iniciar) ───────────────

def _full_scan(filter_expression=None) -> list[dict]:
    """Recorre toda la tabla y devuelve los registros que coincidan con el filtro."""
    kwargs = {}
    if filter_expression:
        kwargs["FilterExpression"] = filter_expression
    items = []
    while True:
        resp = _table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def _load_caches():
    """Carga todos los productos y clientes en memoria para que las búsquedas sean instantáneas."""
    products = _full_scan(filter_expression=Attr("entity").eq("product"))
    for p in products:
        p["name_normalized"] = _normalize(p.get("name", ""))
        stock_qty = p.get("stock_qty", 0)
        reserved_qty = p.get("reserved_qty", 0)
        try:
            p["available_qty"] = int(stock_qty) - int(reserved_qty)
        except (ValueError, TypeError):
            p["available_qty"] = 0

    customers = _full_scan(filter_expression=Attr("entity").eq("customer"))
    for c in customers:
        full_name = f"{c.get('name', '')} {c.get('last_name1', '')} {c.get('last_name2', '')}".strip()
        c["name_normalized"] = _normalize(full_name)

    return products, customers


_products_cache, _customers_cache = _load_caches()


# ── Operaciones de consulta ────────────────────────────────────────────

def _buscar_producto(nombre: str) -> str:
    """Busca productos por nombre en la caché local."""
    term = _normalize(nombre)
    tokens = term.split()
    items = [
        p for p in _products_cache
        if all(t in p.get("name_normalized", "") for t in tokens)
    ]
    if not items and len(tokens) > 1:
        # Si no encuentra con todas las palabras, busca con al menos una
        items = [
            p for p in _products_cache
            if any(t in p.get("name_normalized", "") for t in tokens)
        ]

    cols = [
        "product_id", "name", "price", "active", "available_qty",
        "stock_qty", "reserved_qty", "restock_date", "brand_name",
        "category_name", "warranty_months", "return_days", "free_shipping",
    ]
    return _items_to_table(items, cols)


def _buscar_cliente_dni(dni: str) -> str:
    """Busca un cliente por su número de cédula."""
    items = _query_gsi1(f"DNI#{dni.strip()}")
    if not items:
        return "Sin resultados (0 filas)."

    customer_pk = items[0].get("pk", "")
    profile = _query_table(customer_pk, "PROFILE")
    emails = _query_table(customer_pk, "EMAIL#")

    result_items = profile + emails

    cols = [
        "entity", "customer_id", "dni", "name", "last_name1", "last_name2",
        "phone", "account_status", "is_premium", "email", "email_type",
    ]
    return _items_to_table(result_items, cols)


def _buscar_cliente_phone(phone: str) -> str:
    """Busca un cliente por su número de teléfono en la caché local."""
    import re
    digits_only = re.sub(r'[^\d+]', '', phone.strip())
    items = [
        c for c in _customers_cache
        if digits_only in re.sub(r'[^\d+]', '', c.get('phone', ''))
        or phone.strip() in c.get('phone', '')
    ]

    cols = [
        "customer_id", "dni", "name", "last_name1", "last_name2",
        "phone", "account_status", "is_premium",
    ]
    return _items_to_table(items, cols)


def _buscar_cliente_nombre(nombre: str) -> str:
    """Busca un cliente por su nombre en la caché local."""
    term = _normalize(nombre)
    tokens = term.split()
    items = [
        c for c in _customers_cache
        if all(t in c.get("name_normalized", "") for t in tokens)
    ]
    if not items and len(tokens) > 1:
        # Si no encuentra con el nombre completo, intenta con coincidencia parcial
        items = [
            c for c in _customers_cache
            if sum(1 for t in tokens if t in c.get("name_normalized", "")) >= len(tokens) - 1
        ]

    cols = [
        "customer_id", "dni", "name", "last_name1", "last_name2",
        "phone", "account_status", "is_premium",
    ]
    return _items_to_table(items, cols)


def _pedidos_cliente(customer_id: str) -> str:
    """Obtiene la lista de pedidos de un cliente."""
    items = _query_table(f"CUSTOMER#{customer_id.strip()}", "ORDER#", limit=20)
    # Los más recientes primero
    items.sort(key=lambda x: x.get("sk", ""), reverse=True)

    cols = [
        "order_id", "order_date", "status", "total_amount", "payment_method",
    ]
    return _items_to_table(items, cols)


def _detalle_pedido(order_id: str) -> str:
    """Obtiene toda la información de un pedido: resumen, productos, envíos y seguimiento."""
    items = _query_table(f"ORDER#{order_id.strip()}", limit=100)

    meta = [i for i in items if i.get("sk") == "META"]
    order_items = [i for i in items if i.get("entity") == "order_item"]
    shipments = [i for i in items if i.get("entity") == "shipment"]
    tracking = [i for i in items if i.get("entity") == "tracking"]

    parts = []

    if meta:
        m = meta[0]
        parts.append("PEDIDO:")
        parts.append(_items_to_table(meta, [
            "order_id", "customer_id", "status", "order_date", "total_amount",
            "subtotal", "shipping_cost", "tax", "total_discount_amount",
            "payment_method", "delivery_method",
        ]))

    if order_items:
        parts.append("\nITEMS:")
        parts.append(_items_to_table(order_items, [
            "product_name", "qty", "unit_price", "discount_per_unit",
            "item_status", "return_deadline", "warranty_expires_at",
            "warranty_months", "return_days", "is_final_sale",
        ]))

    if shipments:
        parts.append("\nENVÍOS:")
        parts.append(_items_to_table(shipments, [
            "shipment_id", "carrier", "tracking_number", "shipment_status",
            "shipped_date", "estimated_delivery_date", "actual_delivery_date",
            "delivery_attempts",
        ]))

    if tracking:
        tracking.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        parts.append("\nTRACKING:")
        parts.append(_items_to_table(tracking[:10], [
            "timestamp", "status", "location",
        ]))

    if not parts:
        return "Sin resultados (0 filas)."

    return "\n".join(parts)


def _direccion_pedido(order_id: str) -> str:
    """Obtiene la dirección de entrega asociada a un pedido."""
    meta = _query_table(f"ORDER#{order_id.strip()}", "META")
    if not meta:
        return "No se encontró el pedido."

    customer_id = meta[0].get("customer_id")
    address_id = meta[0].get("address_id")

    if not customer_id or not address_id:
        return "El pedido no tiene dirección de entrega asociada."

    items = _query_table(f"CUSTOMER#{customer_id}", f"ADDR#{address_id}")
    if not items:
        # Si no encuentra la dirección exacta, trae todas las del cliente
        items = _query_table(f"CUSTOMER#{customer_id}", "ADDR#")

    cols = [
        "address_line1", "address_line2", "city", "department",
        "postal_code", "country", "delivery_notes", "landmark",
        "address_type", "is_default",
    ]
    return _items_to_table(items, cols)


def _buscar_tickets(customer_id: str) -> str:
    """Obtiene los tickets de soporte de un cliente."""
    items = _query_table(f"CUSTOMER#{customer_id.strip()}", "TICKET#")

    cols = [
        "ticket_id", "order_id", "subject", "category",
        "status", "priority", "created_at",
    ]
    return _items_to_table(items, cols)


def _info_promocion(promotion_id: str) -> str:
    """Obtiene los datos de una promoción."""
    items = _query_table(f"PROMO#{promotion_id.strip()}", "PROFILE")

    cols = [
        "promotion_id", "promotion_name", "promotion_type",
        "discount_type", "discount_value", "min_purchase_amount",
        "start_date", "end_date", "active", "requires_premium",
    ]
    return _items_to_table(items, cols)


def _productos_categoria(category_id: str) -> str:
    """Lista los productos que pertenecen a una categoría."""
    items = _query_gsi1(f"CAT#{category_id.strip()}", limit=30)

    cols = [
        "product_id", "name", "price", "brand_name",
        "available_qty", "active", "warranty_months",
    ]
    return _items_to_table(items, cols)


def _perfil_completo_cliente(customer_id: str) -> str:
    """Obtiene toda la información del cliente: datos personales, correos, direcciones y tarjetas."""
    items = _query_table(f"CUSTOMER#{customer_id.strip()}", limit=50)

    profile = [i for i in items if i.get("sk") == "PROFILE"]
    emails = [i for i in items if i.get("entity") == "email"]
    addresses = [i for i in items if i.get("entity") == "address"]
    cards = [i for i in items if i.get("entity") == "card"]

    parts = []

    if profile:
        parts.append("CLIENTE:")
        parts.append(_items_to_table(profile, [
            "customer_id", "dni", "name", "last_name1", "last_name2",
            "phone", "birthday", "account_status", "is_premium", "registration_date",
        ]))

    if emails:
        parts.append("\nEMAILS:")
        parts.append(_items_to_table(emails, [
            "email", "email_type", "is_primary", "is_verified",
        ]))

    if addresses:
        parts.append("\nDIRECCIONES:")
        parts.append(_items_to_table(addresses, [
            "address_id", "address_line1", "city", "department", "address_type", "is_default",
        ]))

    if cards:
        parts.append("\nTARJETAS:")
        parts.append(_items_to_table(cards, [
            "card_id", "card_type", "bank", "last_four", "is_primary",
        ]))

    if not parts:
        return "Sin resultados (0 filas)."

    return "\n".join(parts)

# ── Tabla de operaciones disponibles ──────────────────────────────────

_OPERATIONS = {
    "PRODUCTO":          _buscar_producto,
    "CLIENTE_DNI":       _buscar_cliente_dni,
    "CLIENTE_PHONE":     _buscar_cliente_phone,
    "CLIENTE_NOMBRE":    _buscar_cliente_nombre,
    "PEDIDOS":           _pedidos_cliente,
    "DETALLE_PEDIDO":    _detalle_pedido,
    "DIRECCION_PEDIDO":  _direccion_pedido,
    "PERFIL_CLIENTE":    _perfil_completo_cliente,
    "TICKETS":           _buscar_tickets,
    "PROMOCION":         _info_promocion,
    "PRODUCTOS_CAT":     _productos_categoria,
}


# ── Herramienta principal que usa el agente ──────────────────────────

@tool
def consultar_dynamo(operacion: str) -> str:
    """Consulta rápida a DynamoDB. Formato: OPERACION:valor.
    Ops: PRODUCTO:<nombre>, CLIENTE_DNI:<dni>, CLIENTE_PHONE:<tel>, CLIENTE_NOMBRE:<nombre>,
    PERFIL_CLIENTE:<cid>, PEDIDOS:<cid>, DETALLE_PEDIDO:<oid>, DIRECCION_PEDIDO:<oid>,
    TICKETS:<cid>, PROMOCION:<pid>, PRODUCTOS_CAT:<catid>.
    Ej: "PRODUCTO:monitor lg" o "CLIENTE_DNI:12345"
    """
    start = time.time()

    if ":" not in operacion:
        return f"❌ Formato inválido. Use OPERACION:valor. Operaciones: {', '.join(_OPERATIONS.keys())}"

    op_name, _, value = operacion.partition(":")
    op_name = op_name.strip().upper()
    value = value.strip()

    if not value:
        return "❌ El valor no puede estar vacío."

    handler = _OPERATIONS.get(op_name)
    if not handler:
        return f"❌ Operación desconocida: '{op_name}'. Disponibles: {', '.join(_OPERATIONS.keys())}"

    try:
        result = handler(value)
        elapsed_ms = (time.time() - start) * 1000
        return f"{result}\n\n[DynamoDB: {elapsed_ms:.0f}ms]"
    except Exception as e:
        return f"Error en consulta DynamoDB: {str(e)}"
