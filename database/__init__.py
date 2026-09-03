"""Database layer: connection plus price-history storage.

- :mod:`database.connection` - the single shared MongoDB client
  (``get_db()`` / ``get_status()`` / ``ensure_indexes()``).
- :mod:`database.prices` - price-history and latest-fetch upserts.
"""
from .connection import (
    get_client,
    get_db,
    is_configured,
    next_sequence,
    ensure_indexes,
    get_status,
)
from .prices import (
    save_price_record,
    save_latest_fetch,
    get_latest_fetch,
    get_price_history,
)

__all__ = [
    "get_client",
    "get_db",
    "is_configured",
    "next_sequence",
    "ensure_indexes",
    "get_status",
    "save_price_record",
    "save_latest_fetch",
    "get_latest_fetch",
    "get_price_history",
]
