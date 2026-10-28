"""Plain data models."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class Product:
    sku: str
    name: str
    price_cents: int
    stock: int = 0


@dataclass
class Customer:
    customer_id: str
    email: str
    region: str = "EU"
    is_admin: bool = False


@dataclass
class Order:
    order_id: str
    customer: Customer
    lines: List[tuple] = field(default_factory=list)
    total_cents: int = 0
    status: str = "new"
