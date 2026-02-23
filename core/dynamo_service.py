"""
Servicio de consultas rápidas a DynamoDB para el agente OmniRetail.

Utiliza 13 tablas independientes (prefijo omniretail_) con PK/SK y GSIs
optimizados para los patrones de acceso del agente.

Tablas:
  omniretail_customers          PK=customer_id        GSIs: phone-index, dni-index
  omniretail_customer_emails    PK=customer_id SK=email_id    GSI: email-index
  omniretail_addresses          PK=customer_id SK=address_id
  omniretail_cards              PK=customer_id SK=card_id
  omniretail_categories         PK=category_id
  omniretail_brands             PK=brand_id
  omniretail_products           PK=product_id         GSIs: category-index, brand-index
  omniretail_stock              PK=product_id
  omniretail_orders             PK=order_id           GSIs: customer-orders-index, status-index
  omniretail_order_items        PK=order_id SK=item_id GSI: product-items-index
  omniretail_tracking           PK=order_id SK=tracking_id
  omniretail_shipments          PK=order_id SK=shipment_id
  omniretail_promotions         PK=promotion_id       GSI: active-promos-index
"""

import re
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from strands import tool
from core.config import AWS_REGION, DYNAMO_PREFIX, MAX_ROWS

# ── Conexión y acceso a tablas ─────────────────────────────────────────

_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
_table_refs = {}


def _tbl(name: str):
    """Obtiene referencia lazy a una tabla DynamoDB."""
    if name not in _table_refs:
        _table_refs[name] = _dynamodb.Table(f"{DYNAMO_PREFIX}{name}")
    return _table_refs[name]


# ── Funciones auxiliares ───────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Minúsculas sin tildes para búsquedas fuzzy."""
    trans = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return text.lower().translate(trans).strip()


def _fmt(v) -> str:
    """Formatea un valor DynamoDB (Decimal → str legible)."""
    if isinstance(v, Decimal):
        return str(int(v)) if v == v.to_integral_value() else str(v)
    return "" if v is None else str(v)


# Nombres de columnas → español para el usuario
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
}

# Valores del sistema → español
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
    """Traduce un valor de sistema a español."""
    if not isinstance(v, str):
        return v
    s = str(v).strip()
    return _VAL_TR.get(s, _VAL_TR.get(s.lower(), s))


def _to_table(items: list[dict], columns: list[str]) -> str:
    """Formatea ítems como tabla texto pipe-separated para el agente."""
    if not items:
        return "Sin resultados (0 filas)."
    headers = [_COL_LABELS.get(c, c) for c in columns]
    lines = [" | ".join(headers)]
    for item in items[:MAX_ROWS]:
        lines.append(" | ".join(_tr(_fmt(item.get(c, ""))) for c in columns))
    return "\n".join(lines)


# ── Scan completo (para cachés pequeñas) ──────────────────────────────

def _scan_all(table_name: str) -> list[dict]:
    """Recorre toda una tabla DynamoDB."""
    table = _tbl(table_name)
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


# ── Cachés en memoria (se cargan una vez al iniciar) ──────────────────

def _load_caches():
    """Carga catálogos, productos (con stock), clientes y promociones."""
    # Catálogos pequeños
    cats = {str(int(c["category_id"])): c.get("name", "") for c in _scan_all("categories")}
    brnds = {str(int(b["brand_id"])): b.get("name", "") for b in _scan_all("brands")}
    stock_map = {str(int(s["product_id"])): s for s in _scan_all("stock")}

    # Productos enriquecidos con stock + categoría + marca
    products = _scan_all("products")
    for p in products:
        pid = str(int(p.get("product_id", 0)))
        p["category_name"] = cats.get(str(int(p.get("category_id", 0))), "")
        p["brand_name"] = brnds.get(str(int(p.get("brand_id", 0))), "")
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

    # Clientes
    customers = _scan_all("customers")
    for c in customers:
        full = f"{c.get('name', '')} {c.get('last_name1', '')} {c.get('last_name2', '')}".strip()
        c["name_normalized"] = _normalize(full)

    # Promociones
    promos = _scan_all("promotions")

    return cats, brnds, products, customers, promos


_cats_cache, _brands_cache, _products_cache, _customers_cache, _promotions_cache = _load_caches()

# Mapa product_id → producto enriquecido (para detalles de pedidos y promos)
_product_map = {str(int(p["product_id"])): p for p in _products_cache if "product_id" in p}


# ══════════════════════════════════════════════════════════════════════════
# OPERACIONES
# ══════════════════════════════════════════════════════════════════════════

def _buscar_producto(nombre: str) -> str:
    """Busca productos por nombre en la caché local."""
    term = _normalize(nombre)
    tokens = term.split()
    items = [p for p in _products_cache if all(t in p.get("name_normalized", "") for t in tokens)]
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
    """Busca un cliente por DNI en la caché (500 registros, instantáneo)."""
    d = dni.strip()
    items = [c for c in _customers_cache if str(c.get("dni", "")) == d]
    cols = [
        "customer_id", "tipo_id", "dni", "name", "last_name1", "last_name2",
        "phone", "account_status", "is_premium",
    ]
    return _to_table(items, cols)


def _buscar_cliente_phone(phone: str) -> str:
    """Busca un cliente por teléfono en la caché."""
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


def _buscar_cliente_nombre(nombre: str) -> str:
    """Busca un cliente por nombre en la caché."""
    term = _normalize(nombre)
    tokens = term.split()
    items = [c for c in _customers_cache if all(t in c.get("name_normalized", "") for t in tokens)]
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
    """Perfil completo: datos personales, emails, direcciones, tarjetas."""
    cid = int(customer_id.strip())

    # Datos del cliente
    resp = _tbl("customers").get_item(Key={"customer_id": cid})
    profile = [resp["Item"]] if "Item" in resp else []

    # Emails
    resp = _tbl("customer_emails").query(KeyConditionExpression=Key("customer_id").eq(cid))
    emails = resp.get("Items", [])

    # Direcciones
    resp = _tbl("addresses").query(KeyConditionExpression=Key("customer_id").eq(cid))
    addresses = resp.get("Items", [])

    # Tarjetas
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
    """Pedidos de un cliente, ordenados por fecha descendente."""
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
    """Detalle completo: resumen del pedido, ítems, envíos y tracking."""
    oid = int(order_id.strip())

    # Pedido
    resp = _tbl("orders").get_item(Key={"order_id": oid})
    order = resp.get("Item")

    # Ítems del pedido
    resp = _tbl("order_items").query(KeyConditionExpression=Key("order_id").eq(oid))
    items = resp.get("Items", [])
    # Enriquecer con datos del producto desde caché
    for it in items:
        pid = str(int(it.get("product_id", 0)))
        prod = _product_map.get(pid, {})
        it["product_name"] = prod.get("name", "")
        it["warranty_months"] = prod.get("warranty_months", "")
        it["return_days"] = prod.get("return_days", "")
        it["is_final_sale"] = prod.get("is_final_sale", "")

    # Envíos
    resp = _tbl("shipments").query(KeyConditionExpression=Key("order_id").eq(oid))
    shipments = resp.get("Items", [])

    # Tracking
    resp = _tbl("tracking").query(KeyConditionExpression=Key("order_id").eq(oid))
    tracking = resp.get("Items", [])

    parts = []
    if order:
        parts.append("PEDIDO:")
        parts.append(_to_table([order], [
            "order_id", "customer_id", "status", "order_date",
            "total_amount", "subtotal", "shipping_cost", "tax",
            "payment_method", "delivery_method",
        ]))
    if items:
        parts.append("\nITEMS:")
        parts.append(_to_table(items, [
            "product_name", "qty", "unit_price",
            "item_status", "return_deadline", "warranty_expires_at",
            "warranty_months", "return_days", "is_final_sale",
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
    """Dirección de entrega asociada a un pedido."""
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
    # Buscar dirección exacta
    resp = _tbl("addresses").query(
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
    """Detalle de una promoción por ID."""
    pid = int(promotion_id.strip())
    resp = _tbl("promotions").get_item(Key={"promotion_id": pid})
    item = resp.get("Item")
    if not item:
        return "Sin resultados (0 filas)."
    # Enriquecer con nombres de categorías
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


def _promos_activas(_: str) -> str:
    """Lista todas las promociones activas."""
    activas = [p for p in _promotions_cache if str(p.get("active", "")).lower() == "true"]
    cols = [
        "promotion_id", "promotion_name", "discount_type", "discount_value",
        "min_purchase_amount", "start_date", "end_date",
        "applicable_category_ids", "applicable_product_ids",
    ]
    return _to_table(activas, cols)


def _promos_producto(product_id: str) -> str:
    """Encuentra promociones activas que aplican a un producto (por ID o categoría)."""
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
    """Productos de una categoría (usa GSI category-index)."""
    cid = int(category_id.strip())
    resp = _tbl("products").query(
        IndexName="category-index",
        KeyConditionExpression=Key("category_id").eq(cid),
        Limit=30,
    )
    items = resp.get("Items", [])
    # Enriquecer con stock y marca desde caché
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
    """Stock detallado de un producto."""
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

    op_name, _, value = operacion.partition(":")
    op_name = op_name.strip().upper()
    value = value.strip()

    if not value:
        return "El valor no puede estar vacío."

    handler = _OPERATIONS.get(op_name)
    if not handler:
        return f"Operación desconocida: '{op_name}'. Disponibles: {', '.join(_OPERATIONS.keys())}"

    try:
        result = handler(value)
        elapsed_ms = (time.time() - start) * 1000
        return f"{result}\n\n[DynamoDB: {elapsed_ms:.0f}ms]"
    except Exception as e:
        return f"Error en consulta DynamoDB: {str(e)}"
