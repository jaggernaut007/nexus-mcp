"""Domain errors."""


class ShopError(Exception):
    """Base class for all shop errors."""


class OutOfStock(ShopError):
    """Raised when a product has too few units to fill an order."""


class PaymentDeclined(ShopError):
    """Raised when the payment gateway refuses a charge."""


class Unauthorized(ShopError):
    """Raised when a caller lacks the needed role."""
