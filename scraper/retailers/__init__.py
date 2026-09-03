"""One retailer scraper module per file - each exposes ``find_url()`` and
``scrape()``. Imported by :mod:`scraper`, which re-exports the public API.
"""
from . import barakat, carrefour, kibsons, lulu, unioncoop

__all__ = ["barakat", "carrefour", "kibsons", "lulu", "unioncoop"]
