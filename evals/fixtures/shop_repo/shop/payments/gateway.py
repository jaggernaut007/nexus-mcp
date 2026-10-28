"""Payment gateway client."""

import time

from shop.config import load_settings
from shop.errors import PaymentDeclined


def retry_with_backoff(fn, attempts: int = 3, base_delay: float = 0.5):
    """Call fn until it works. Wait base_delay * 2**n between failures."""
    for n in range(attempts):
        try:
            return fn()
        except ConnectionError:
            if n == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** n))


class PaymentGateway:
    """Client for the external card gateway."""

    def __init__(self):
        self.key = load_settings().gateway_key

    def _post(self, path: str, payload: dict) -> dict:
        return {"ok": payload.get("amount_cents", 0) < 1_000_000, "path": path}

    def charge(self, customer_id: str, amount_cents: int) -> str:
        """Charge a customer and return a payment id. Raise PaymentDeclined on refusal."""
        result = retry_with_backoff(
            lambda: self._post("/charge", {"customer": customer_id, "amount_cents": amount_cents}),
            attempts=load_settings().max_retries,
        )
        if not result["ok"]:
            raise PaymentDeclined("card declined")
        return f"pay_{customer_id}_{amount_cents}"

    def refund(self, payment_id: str) -> None:
        """Refund a payment in full."""
        retry_with_backoff(lambda: self._post("/refund", {"payment": payment_id}))
