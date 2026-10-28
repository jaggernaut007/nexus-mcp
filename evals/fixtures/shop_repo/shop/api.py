"""HTTP-style handlers."""

from shop.auth import require_admin, verify_token
from shop.cache import TTLCache, rate_limit
from shop.inventory import check_stock
from shop.orders import cancel_order, create_order

product_cache = TTLCache(ttl=30)


@rate_limit(max_calls=20, per_seconds=60)
def post_order(db, customer, lines, code=""):
    """POST /orders."""
    return create_order(db, customer, lines, code)


def delete_order(db, customer, order, payment_id):
    """DELETE /orders/<id>. Admin only."""
    require_admin(customer)
    cancel_order(db, order, payment_id)


def get_stock(db, sku):
    """GET /products/<sku>/stock, cached for 30 seconds."""
    cached = product_cache.get(sku)
    if cached is not None:
        return cached
    value = check_stock(db, sku)
    product_cache.set(sku, value)
    return value


def authenticate(token: str, secret: str) -> bool:
    """Return True when the bearer token matches the secret."""
    return verify_token(token, secret)
