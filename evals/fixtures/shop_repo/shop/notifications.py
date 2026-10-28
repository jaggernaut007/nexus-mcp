"""Customer messages."""

from shop.models import Order


def send_order_email(order: Order) -> None:
    """Send the order confirmation email."""
    print(f"email to {order.customer.email}: order {order.order_id} total {order.total_cents}")


def send_sms(phone: str, text: str) -> None:
    """Send a short text message."""
    print(f"sms to {phone}: {text}")
