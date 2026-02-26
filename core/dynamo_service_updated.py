import os
import time
from decimal import Decimal
import re
from typing import Optional, Any

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from strands import tool

from core.config import AWS_REGION, ATHENA_MAX_ROWS
from core.session_context import (
    get_session_customer_id,
    set_session_customer,
    add_tool_trace,
)

# ---------------------------------------------------------------------
# Configuración Dynamo (nuevo esquema multi-tabla)
# ---------------------------------------------------------------------

DYNAMO_TABLES = {
    "products": os.getenv("DYNAMO_PRODUCTS_TABLE", "omniretail_products"),
    "stock": os.getenv("DYNAMO_STOCK_TABLE", "omniretail_stock"),
    "customers": os.getenv("DYNAMO_CUSTOMERS_TABLE", "omniretail_customers"),
    "customer_emails": os.getenv("DYNAMO_CUSTOMER_EMAILS_TABLE", "omniretail_customer_emails"),
    "addresses": os.getenv("DYNAMO_ADDRESSES_TABLE", "omniretail_addresses"),
    "cards": os.getenv("DYNAMO_CARDS_TABLE", "omniretail_cards"),
    "orders": os.getenv("DYNAMO_ORDERS_TABLE", "omniretail_orders"),
    "order_items": os.getenv("DYNAMO_ORDER_ITEMS_TABLE", "omniretail_order_items"),
    "shipments": os.getenv("DYNAMO_SHIPMENTS_TABLE", "omniretail_shipments"),
    "tracking": os.getenv("DYNAMO_TRACKING_TABLE", "omniretail_tracking"),
    "brands": os.getenv("DYNAMO_BRANDS_TABLE", "omniretail_brands"),
    "categories": os.getenv("DYNAMO_CATEGORIES_TABLE", "omniretail_categories"),
    "promotions": os.getenv("DYNAMO_PROMOTIONS_TABLE", "omniretail_promotions"),
    # opcional; en tu screenshot no aparece
    "tickets": os.getenv("DYNAMO_TICKETS_TABLE", "omniretail_tickets"),
}

_TABLE_KEYS = {
    "products": ("product_id", None),
    "stock": ("product_id", None),
    "customers": ("customer_id", None),
    "customer_emails": ("customer_id", "email_id"),
    "addresses": ("customer_id", "address_id"),
    "cards": ("customer_id", "card_id"),
    "orders": ("order_id", None),
    "order_items": ("order_id", "item_id"),
    "shipments": ("order_id", "shipment_id"),
    "tracking": ("order_id", "tracking_id"),
    "brands": ("brand_id", None),
    "categories": ("category_id", None),
    "promotions": ("promotion_id", None),
    "tickets": ("customer_id", "ticket_id"),
}

_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
_tables_cache: dict[str, Any] = {}

# caches lazy
_products_cache: Optional[list[dict]] = None
_customers_cache: Optional[list[dict]] = None
_cache_load_error: Optional[Exception] = None


# ── Funciones auxiliares ───────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Convierte el texto a minúsculas y elimina tildes para facilitar las búsquedas."""
    trans = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return (text or "").lower().translate(trans).strip()


def _fmt_value(v) -> str:
    if isinstance(v, Decimal):
        return str(int(v)) if v == v.to_integral_value() else str(v)
    return "" if v is None else str(v)


def _mask_value(v: str) -> str:
    """Evita loguear PII cruda en tool_trace."""
    if not v:
        return ""
    v = str(v).strip()
    if v.isdigit() and len(v) >= 6:
        return f"***{v[-4:]}(len={len(v)})"
    if "@" in v:
        parts = v.split("@", 1)
        left = parts[0]
        return (left[:2] + "***@" + parts[1]) if len(left) > 2 else "***@" + parts[1]
    return v[:40]


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


def _coerce_dynamo_key(v: Any):
    """Convierte ids numéricos string a int para tablas con PK tipo Number."""
    if isinstance(v, (int, float, Decimal)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"-?\d+", s):
            try:
                return int(s)
            except Exception:
                return Decimal(s)
        return s
    return v


def _table(logical_name: str):
    if logical_name not in _tables_cache:
        table_name = DYNAMO_TABLES[logical_name]
        _tables_cache[logical_name] = _dynamodb.Table(table_name)
    return _tables_cache[logical_name]


def _is_resource_not_found(exc: Exception) -> bool:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        return code == "ResourceNotFoundException"
    return False


def _wrap_dynamo_error(exc: Exception, logical_name: str) -> RuntimeError:
    table_name = DYNAMO_TABLES.get(logical_name, logical_name)
    if _is_resource_not_found(exc):
        return RuntimeError(
            f"No se encontró la tabla DynamoDB '{table_name}' en la región {AWS_REGION}. "
            "Verifica nombre de tabla, región y credenciales AWS."
        )
    return RuntimeError(f"Error DynamoDB ({table_name}): {str(exc)}")


def _scan_all(logical_name: str, filter_expression=None, limit: Optional[int] = None) -> list[dict]:
    """Escanea una tabla completa (o hasta limit) con paginación."""
    tbl = _table(logical_name)
    kwargs = {}
    if filter_expression is not None:
        kwargs["FilterExpression"] = filter_expression

    items: list[dict] = []
    while True:
        if limit is not None:
            remaining = max(limit - len(items), 0)
            if remaining == 0:
                break
            kwargs["Limit"] = min(remaining, 1000)

        try:
            resp = tbl.scan(**kwargs)
        except Exception as e:
            raise _wrap_dynamo_error(e, logical_name) from e

        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def _query_partition(logical_name: str, pk_value: Any, limit: Optional[int] = None, scan_index_forward: bool = True) -> list[dict]:
    """Query por partition key en tablas con PK simple o compuesta."""
    pk_name, _ = _TABLE_KEYS[logical_name]
    tbl = _table(logical_name)
    kwargs = {
        "KeyConditionExpression": Key(pk_name).eq(_coerce_dynamo_key(pk_value)),
        "ScanIndexForward": scan_index_forward,
    }
    if limit is not None:
        kwargs["Limit"] = limit
    try:
        resp = tbl.query(**kwargs)
        return resp.get("Items", [])
    except Exception as e:
        # fallback a scan si query falla por tipo de dato inesperado / schema distinto
        try:
            return _scan_all(logical_name, filter_expression=Attr(pk_name).eq(_coerce_dynamo_key(pk_value)), limit=limit)
        except Exception:
            raise _wrap_dynamo_error(e, logical_name) from e


def _get_by_id(logical_name: str, id_value: Any) -> Optional[dict]:
    """Obtiene un registro por PK simple. Si falla, hace scan por robustez."""
    pk_name, sk_name = _TABLE_KEYS[logical_name]
    if sk_name is not None:
        raise ValueError(f"_get_by_id solo aplica a tablas con PK simple: {logical_name}")

    key_val = _coerce_dynamo_key(id_value)
    tbl = _table(logical_name)
    try:
        resp = tbl.get_item(Key={pk_name: key_val})
        item = resp.get("Item")
        if item:
            return item
    except Exception as e:
        if _is_resource_not_found(e):
            raise _wrap_dynamo_error(e, logical_name) from e
        # fallback scan abajo

    rows = _scan_all(logical_name, filter_expression=Attr(pk_name).eq(key_val), limit=1)
    return rows[0] if rows else None


def _safe_scan_optional(logical_name: str) -> list[dict]:
    try:
        return _scan_all(logical_name)
    except RuntimeError as e:
        # tablas opcionales o no críticas (brands/categories/tickets)
        if "No se encontró la tabla" in str(e):
            return []
        raise


def _safe_sort(items: list[dict], key: str, reverse: bool = False) -> list[dict]:
    def _k(x):
        v = x.get(key)
        return "" if v is None else str(v)
    return sorted(items, key=_k, reverse=reverse)


# ── Caché en memoria (lazy load) ──────────────────────────────────────

def _load_caches():
    """Carga productos y clientes (enriquecidos) en memoria para búsquedas rápidas."""
    products = _scan_all("products")
    stock_rows = _scan_all("stock")
    customers = _scan_all("customers")
    brands = _safe_scan_optional("brands")
    categories = _safe_scan_optional("categories")

    brand_name_by_id = {}
    for b in brands:
        bid = _fmt_value(b.get("brand_id"))
        if bid:
            brand_name_by_id[bid] = b.get("brand_name") or b.get("name") or b.get("brand")

    category_name_by_id = {}
    for c in categories:
        cid = _fmt_value(c.get("category_id"))
        if cid:
            category_name_by_id[cid] = c.get("category_name") or c.get("name") or c.get("category")

    stock_by_product_id = {}
    for s in stock_rows:
        pid = _fmt_value(s.get("product_id"))
        if pid:
            stock_by_product_id[pid] = s

    for p in products:
        p["name_normalized"] = _normalize(p.get("name", ""))

        pid = _fmt_value(p.get("product_id"))
        stock = stock_by_product_id.get(pid, {})

        # merge no destructivo para no pisar datos ya enriquecidos
        for k in ["stock_qty", "reserved_qty", "restock_date", "active"]:
            if k not in p and k in stock:
                p[k] = stock.get(k)

        stock_qty = p.get("stock_qty", stock.get("stock_qty", 0))
        reserved_qty = p.get("reserved_qty", stock.get("reserved_qty", 0))
        try:
            p["available_qty"] = int(Decimal(str(stock_qty or 0))) - int(Decimal(str(reserved_qty or 0)))
        except Exception:
            p["available_qty"] = 0

        # Enriquecer marca/categoría si vienen por ID y no por nombre
        brand_id = _fmt_value(p.get("brand_id"))
        if not p.get("brand_name") and brand_id in brand_name_by_id:
            p["brand_name"] = brand_name_by_id[brand_id]

        category_id = _fmt_value(p.get("category_id"))
        if not p.get("category_name") and category_id in category_name_by_id:
            p["category_name"] = category_name_by_id[category_id]

    for c in customers:
        full_name = f"{c.get('name', '')} {c.get('last_name1', '')} {c.get('last_name2', '')}".strip()
        c["name_normalized"] = _normalize(full_name)

    return products, customers


def _ensure_caches_loaded() -> None:
    global _products_cache, _customers_cache, _cache_load_error

    if _products_cache is not None and _customers_cache is not None:
        return

    try:
        _products_cache, _customers_cache = _load_caches()
        _cache_load_error = None
    except Exception as e:
        _cache_load_error = e
        # fallback vacío para no romper el import / la app
        _products_cache = []
        _customers_cache = []
        raise


def _cache_guard():
    _ensure_caches_loaded()
    if _cache_load_error:
        raise RuntimeError(str(_cache_load_error))


# ── Helpers de sesión / seguridad ─────────────────────────────────────

def _require_session() -> Optional[str]:
    cid = get_session_customer_id()
    if not cid:
        return None
    return cid


def _get_order_meta(order_id: str) -> Optional[dict]:
    return _get_by_id("orders", order_id.strip())


def _ownership_check(order_id: str, session_customer_id: str) -> dict | None:
    """Devuelve meta del pedido si pertenece al cliente; si no, None."""
    meta = _get_order_meta(order_id)
    if not meta:
        return None
    if str(meta.get("customer_id", "")).strip() != str(session_customer_id).strip():
        return None
    return meta


def _identity_required_msg() -> str:
    return "Para verificar tu identidad necesito tu número de cédula o celular."


def _filter_equals_str(items: list[dict], field: str, value: Any) -> list[dict]:
    target = str(value).strip()
    return [r for r in items if str(r.get(field, "")).strip() == target]


def _one_or_none(items: list[dict]) -> Optional[dict]:
    return items[0] if items else None


# ── Operaciones de consulta ────────────────────────────────────────────

def _buscar_producto(nombre: str) -> str:
    """Busca productos por nombre en la caché local."""
    _cache_guard()

    term = _normalize(nombre)
    tokens = term.split()
    items = [
        p for p in (_products_cache or [])
        if all(t in p.get("name_normalized", "") for t in tokens)
    ]
    if not items and len(tokens) > 1:
        items = [
            p for p in (_products_cache or [])
            if any(t in p.get("name_normalized", "") for t in tokens)
        ]

    items = _safe_sort(items, "name")

    cols = [
        "product_id", "name", "price", "active", "available_qty",
        "stock_qty", "reserved_qty", "restock_date", "brand_name",
        "category_name", "warranty_months", "return_days", "free_shipping",
    ]
    return _items_to_table(items, cols)


def _info_promocion(promotion_id: str) -> str:
    item = _get_by_id("promotions", promotion_id.strip())
    items = [item] if item else []

    cols = [
        "promotion_id", "promotion_name", "promotion_type",
        "discount_type", "discount_value", "min_purchase_amount",
        "start_date", "end_date", "active", "requires_premium",
    ]
    return _items_to_table(items, cols)


def _productos_categoria(category_id: str) -> str:
    _cache_guard()
    cid = category_id.strip()

    items = [
        p for p in (_products_cache or [])
        if str(p.get("category_id", "")).strip() == cid
    ]

    # fallback por nombre de categoría si pasaron texto (ej. "TVs")
    if not items:
        cid_norm = _normalize(cid)
        items = [
            p for p in (_products_cache or [])
            if cid_norm in _normalize(str(p.get("category_name", "")))
        ]

    cols = ["product_id", "name", "price", "brand_name", "available_qty", "active", "warranty_months"]
    return _items_to_table(items[:30], cols)


def _buscar_cliente_dni(dni: str) -> str:
    dni = dni.strip()
    # usamos customers directamente; evita depender de GSIs del diseño viejo
    rows = _scan_all("customers", filter_expression=Attr("dni").eq(_coerce_dynamo_key(dni)), limit=5)
    if not rows:
        # fallback string por si en Dynamo está como texto
        rows = [c for c in _safe_scan_optional("customers") if str(c.get("dni", "")).strip() == dni][:5]

    if not rows:
        return "No pude verificar tu identidad con ese número. ¿Podrías revisarlo e intentar de nuevo?"

    customer_id = rows[0].get("customer_id")
    if not customer_id:
        return "No pude verificar tu identidad con ese número. ¿Podrías revisarlo e intentar de nuevo?"

    set_session_customer(str(customer_id))
    return "Listo, identidad verificada. ¿En qué te puedo ayudar?"


def _buscar_cliente_phone(phone: str) -> str:
    _cache_guard()
    digits_only = re.sub(r"[^\d+]", "", phone.strip())
    matches = [
        c for c in (_customers_cache or [])
        if digits_only and digits_only in re.sub(r"[^\d+]", "", str(c.get("phone", "")))
        or phone.strip() in str(c.get("phone", ""))
    ]
    if not matches:
        return "No pude verificar tu identidad con ese número. ¿Podrías revisarlo e intentar de nuevo?"

    customer_id = matches[0].get("customer_id")
    if not customer_id:
        return "No pude verificar tu identidad con ese número. ¿Podrías revisarlo e intentar de nuevo?"

    set_session_customer(str(customer_id))
    return "Listo, identidad verificada. ¿En qué te puedo ayudar?"


def _buscar_cliente_nombre(nombre: str) -> str:
    """Busca un cliente por su nombre en la caché local."""
    _cache_guard()

    term = _normalize(nombre)
    tokens = term.split()
    items = [
        c for c in (_customers_cache or [])
        if all(t in c.get("name_normalized", "") for t in tokens)
    ]
    if not items and len(tokens) > 1:
        items = [
            c for c in (_customers_cache or [])
            if sum(1 for t in tokens if t in c.get("name_normalized", "")) >= max(len(tokens) - 1, 1)
        ]

    cols = [
        "customer_id", "dni", "name", "last_name1", "last_name2",
        "phone", "account_status", "is_premium",
    ]
    return _items_to_table(items, cols)


# ── Operaciones sensibles (requieren sesión) ──────────────────────────

def _pedidos_sesion(_: str = "") -> str:
    cid = _require_session()
    if not cid:
        return _identity_required_msg()

    items = _scan_all("orders", filter_expression=Attr("customer_id").eq(_coerce_dynamo_key(cid)), limit=100)
    items = _safe_sort(items, "order_date", reverse=True)
    cols = ["order_id", "order_date", "status", "total_amount", "payment_method"]
    return _items_to_table(items[:20], cols)


def _get_customer_related_rows(customer_id: str) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    cid = customer_id.strip()
    profile = []
    customer = _get_by_id("customers", cid)
    if customer:
        profile = [customer]

    # Tablas con PK customer_id: query es eficiente
    emails = _query_partition("customer_emails", cid, limit=50)
    addresses = _query_partition("addresses", cid, limit=50)
    cards = _query_partition("cards", cid, limit=50)
    return profile, emails, addresses, cards


def _render_customer_profile(customer_id: str) -> str:
    profile, emails, addresses, cards = _get_customer_related_rows(customer_id)
    parts = []

    if profile:
        parts.append("CLIENTE:")
        parts.append(_items_to_table(profile, [
            "customer_id", "dni", "name", "last_name1", "last_name2",
            "phone", "birthday", "account_status", "is_premium", "registration_date",
        ]))

    if emails:
        parts.append("\nEMAILS:")
        parts.append(_items_to_table(emails, ["email", "email_type", "is_primary", "is_verified"]))

    if addresses:
        parts.append("\nDIRECCIONES:")
        parts.append(_items_to_table(addresses, ["address_id", "address_line1", "city", "department", "address_type", "is_default"]))

    if cards:
        parts.append("\nTARJETAS:")
        parts.append(_items_to_table(cards, ["card_id", "card_type", "bank", "last_four", "is_primary"]))

    if not parts:
        return "Sin resultados (0 filas)."

    return "\n".join(parts)


def _perfil_sesion(_: str = "") -> str:
    cid = _require_session()
    if not cid:
        return _identity_required_msg()
    return _render_customer_profile(cid)


def _tickets_sesion(_: str = "") -> str:
    cid = _require_session()
    if not cid:
        return _identity_required_msg()

    # En el esquema nuevo del screenshot no aparece tabla de tickets.
    tickets_table = DYNAMO_TABLES.get("tickets")
    if not tickets_table:
        return "La funcionalidad de tickets no está configurada en este entorno."

    try:
        items = _query_partition("tickets", cid, limit=50)
    except RuntimeError as e:
        if "No se encontró la tabla" in str(e):
            return "La tabla de tickets no está disponible en el esquema actual de DynamoDB."
        raise

    cols = ["ticket_id", "order_id", "subject", "category", "status", "priority", "created_at"]
    return _items_to_table(items, cols)


def _detalle_pedido_sesion(order_id: str) -> str:
    cid = _require_session()
    if not cid:
        return _identity_required_msg()

    meta = _ownership_check(order_id, cid)
    if meta is None:
        return "Ese pedido no pertenece a tu cuenta."

    oid = order_id.strip()
    meta_items = [meta]
    order_items = _query_partition("order_items", oid, limit=200)
    shipments = _query_partition("shipments", oid, limit=100)
    tracking = _query_partition("tracking", oid, limit=200)

    parts = []

    if meta_items:
        parts.append("PEDIDO:")
        parts.append(_items_to_table(meta_items, [
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
        tracking = _safe_sort(tracking, "timestamp", reverse=True)
        parts.append("\nTRACKING:")
        parts.append(_items_to_table(tracking[:10], ["timestamp", "status", "location"]))

    if not parts:
        return "Sin resultados (0 filas)."

    return "\n".join(parts)


def _direccion_pedido_sesion(order_id: str) -> str:
    cid = _require_session()
    if not cid:
        return _identity_required_msg()

    meta = _ownership_check(order_id, cid)
    if meta is None:
        return "Ese pedido no pertenece a tu cuenta."

    address_id = meta.get("address_id")
    if not address_id:
        return "El pedido no tiene dirección de entrega asociada."

    # Tabla addresses tiene PK compuesta (customer_id, address_id). Intentamos query por customer y filtramos address_id.
    rows = _query_partition("addresses", cid, limit=100)
    items = [r for r in rows if str(r.get("address_id", "")).strip() == str(address_id).strip()]
    if not items:
        items = rows  # fallback: mostrar direcciones del cliente si no coincide exacto (dato inconsistente)

    cols = [
        "address_line1", "address_line2", "city", "department",
        "postal_code", "country", "delivery_notes", "landmark",
        "address_type", "is_default",
    ]
    return _items_to_table(items, cols)


def _perfil_completo_cliente(customer_id: str) -> str:
    """Obtiene toda la información del cliente por customer_id explícito."""
    return _render_customer_profile(customer_id)


def _perfil_cliente_dispatch(value: str) -> str:
    """PERFIL_CLIENTE puede usarse sin args (sesión) o con customer_id explícito."""
    if value and value.strip():
        return _perfil_completo_cliente(value)
    return _perfil_sesion("")


# ── Tabla de operaciones disponibles ──────────────────────────────────

_OPERATIONS = {
    "PRODUCTO": _buscar_producto,
    "CLIENTE_DNI": _buscar_cliente_dni,
    "CLIENTE_PHONE": _buscar_cliente_phone,
    "CLIENTE_NOMBRE": _buscar_cliente_nombre,
    "PEDIDOS": _pedidos_sesion,
    "PERFIL_CLIENTE": _perfil_cliente_dispatch,  # optional arg
    "PERFIL_CLIENTE_ID": _perfil_completo_cliente,
    "DETALLE_PEDIDO": _detalle_pedido_sesion,
    "DIRECCION_PEDIDO": _direccion_pedido_sesion,
    "TICKETS": _tickets_sesion,
    "PROMOCION": _info_promocion,
    "PRODUCTOS_CAT": _productos_categoria,
}

_NO_ARG_OPS = {"PEDIDOS", "PERFIL_CLIENTE", "TICKETS"}


# ── Herramienta principal que usa el agente ──────────────────────────

@tool
def consultar_dynamo(operacion: str) -> str:
    """Consulta rápida a DynamoDB. Formato: OPERACION:valor.
    Ops: PRODUCTO:<nombre>, CLIENTE_DNI:<dni>, CLIENTE_PHONE:<tel>, CLIENTE_NOMBRE:<nombre>,
    PERFIL_CLIENTE (o PERFIL_CLIENTE:<cid>), PEDIDOS, DETALLE_PEDIDO:<oid>, DIRECCION_PEDIDO:<oid>,
    TICKETS, PROMOCION:<pid>, PRODUCTOS_CAT:<catid>.
    Ej: "PRODUCTO:monitor lg" o "CLIENTE_DNI:12345"
    """
    start = time.time()

    raw = (operacion or "").strip()
    if not raw:
        return "❌ Operación vacía."

    if ":" in raw:
        op_name, _, value = raw.partition(":")
        op_name = op_name.strip().upper()
        value = value.strip()
        # PERFIL_CLIENTE admite valor opcional; otras requieren valor si usan ':'
        if not value and op_name not in _NO_ARG_OPS:
            return "❌ El valor no puede estar vacío."
    else:
        op_name = raw.upper()
        value = ""
        if op_name not in _NO_ARG_OPS:
            return f"❌ Formato inválido. Use OPERACION:valor. Operaciones: {', '.join(_OPERATIONS.keys())}"

    handler = _OPERATIONS.get(op_name)
    if not handler:
        return f"❌ Operación desconocida: '{op_name}'. Disponibles: {', '.join(_OPERATIONS.keys())}"

    try:
        result = handler(value)
        elapsed_ms = int((time.time() - start) * 1000)

        add_tool_trace(
            "consultar_dynamo",
            {"operacion": op_name, "value_masked": _mask_value(value)},
            {"elapsed_ms": elapsed_ms, "result_len": len(result)},
        )
        return result

    except Exception as e:
        add_tool_trace(
            "consultar_dynamo",
            {"operacion": op_name, "value_masked": _mask_value(value)},
            {"error": str(e)},
        )
        return f"Error en consulta DynamoDB: {str(e)}"
