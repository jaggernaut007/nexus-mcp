"""Runtime settings read from environment variables."""

import os
from dataclasses import dataclass


@dataclass
class Settings:
    database_url: str
    gateway_key: str
    tax_default: float
    max_retries: int


def load_settings() -> Settings:
    """Build Settings from SHOP_* environment variables."""
    return Settings(
        database_url=os.environ.get("SHOP_DB", "sqlite:///shop.db"),
        gateway_key=os.environ.get("SHOP_GATEWAY_KEY", ""),
        tax_default=float(os.environ.get("SHOP_TAX", "0.2")),
        max_retries=int(os.environ.get("SHOP_RETRIES", "3")),
    )
