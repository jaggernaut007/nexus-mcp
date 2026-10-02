"""Stock reservation."""

from shop.db import Database
from shop.errors import OutOfStock


def check_stock(db: Database, sku: str) -> int:
    """Return units on hand for a SKU."""
    row = db.execute("SELECT stock FROM products WHERE sku = ?", (sku,)).fetchone()
    return row[0] if row else 0


def reserve_stock(db: Database, sku: str, quantity: int) -> None:
    """Take units out of stock. Raise OutOfStock when there are too few."""
    if check_stock(db, sku) < quantity:
        raise OutOfStock(f"not enough {sku}")
    db.execute("UPDATE products SET stock = stock - ? WHERE sku = ?", (quantity, sku))


def release_stock(db: Database, sku: str, quantity: int) -> None:
    """Put units back, for example after a cancelled order."""
    db.execute("UPDATE products SET stock = stock + ? WHERE sku = ?", (quantity, sku))
