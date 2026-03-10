from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import unicodedata


_ACCENT_MAP = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")


def normalize_text(text: str) -> str:
    """Normaliza texto a minúsculas sin tildes y sin espacios extra."""
    if not isinstance(text, str):
        text = str(text)
    return text.lower().translate(_ACCENT_MAP).strip()


def normalize_ascii(text: str) -> str:
    """Normaliza texto a ASCII básico (útil para comparaciones tolerantes)."""
    value = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return value.lower().strip()


def fmt_value(value) -> str:
    """Formatea valores provenientes de Dynamo/BD a string legible."""
    if isinstance(value, Decimal):
        return str(int(value)) if value == value.to_integral_value() else str(value)
    return "" if value is None else str(value)


@dataclass
class DynamoCache:
    """Estructura de caché para datos de DynamoDB."""

    loaded: bool = False
    cats: dict = None
    brands: dict = None
    products: list = None
    customers: list = None
    promotions: list = None
    product_map: dict = None

    def __post_init__(self) -> None:
        # Evitar uso de mutables como default en la firma
        if self.cats is None:
            self.cats = {}
        if self.brands is None:
            self.brands = {}
        if self.products is None:
            self.products = []
        if self.customers is None:
            self.customers = []
        if self.promotions is None:
            self.promotions = []
        if self.product_map is None:
            self.product_map = {}

