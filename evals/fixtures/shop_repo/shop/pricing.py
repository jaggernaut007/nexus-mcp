"""Price and tax rules."""

from shop.config import load_settings

REGION_TAX = {"EU": 0.21, "US": 0.07, "UK": 0.20}


def tax_for_region(region: str) -> float:
    """Return the tax rate for a region, or the configured default."""
    return REGION_TAX.get(region, load_settings().tax_default)


def apply_discount(subtotal_cents: int, code: str) -> int:
    """Return the subtotal after a discount code. Unknown codes change nothing."""
    if code == "WELCOME10":
        return int(subtotal_cents * 0.9)
    return subtotal_cents


def calculate_total(lines: list, region: str, code: str = "") -> int:
    """Total in cents for (price_cents, quantity) lines, with discount and tax."""
    subtotal = sum(price * qty for price, qty in lines)
    discounted = apply_discount(subtotal, code)
    return int(discounted * (1 + tax_for_region(region)))
