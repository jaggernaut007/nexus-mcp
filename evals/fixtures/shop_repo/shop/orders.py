"""Order workflow."""

import uuid

from shop.db import Database
from shop.inventory import release_stock, reserve_stock
from shop.models import Customer, Order
from shop.notifications import send_order_email
from shop.payments.gateway import PaymentGateway
from shop.pricing import calculate_total


def create_order(db: Database, customer: Customer, lines: list, code: str = "") -> Order:
    """Reserve stock, price the order, charge the customer and send the email.

    lines holds (sku, price_cents, quantity) tuples.
    """
    for sku, _price, qty in lines:
        reserve_stock(db, sku, qty)
    total = calculate_total([(p, q) for _s, p, q in lines], customer.region, code)
    PaymentGateway().charge(customer.customer_id, total)
    order = Order(str(uuid.uuid4()), customer, lines, total, "paid")
    send_order_email(order)
    return order


def cancel_order(db: Database, order: Order, payment_id: str) -> None:
    """Refund the payment and return the stock."""
    PaymentGateway().refund(payment_id)
    for sku, _price, qty in order.lines:
        release_stock(db, sku, qty)
    order.status = "cancelled"
