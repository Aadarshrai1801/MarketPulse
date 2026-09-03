"""
Public API of the `scraper` package - this is the only file other code
(app.py, products_config.py) needs to import from, and its surface is
unchanged from when this used to be a single scraper.py:

    from scraper import SITE_SEARCH_CONFIG, find_product_url, get_product_details, save_price_record

Internally, each retailer now lives in its own module
(scraper/carrefour.py, scraper/lulu.py, etc.) with two functions:

    find_url(product_name) -> product page URL, or raises ValueError
    scrape(url)             -> dict of scraped product details

To add a new retailer:
  1. Add its entry to SITE_SEARCH_CONFIG in config.py.
  2. Create scraper/<retailer>.py with find_url() and scrape().
  3. Import it below and add one line each to _FINDERS and _SCRAPERS.
That's it - app.py and products_config.py don't need to change.

Price storage lives in MongoDB (see mongo_store.py / db.py).
"""

from .config import SITE_SEARCH_CONFIG
from .mongo_store import save_price_record, get_price_history, save_latest_fetch, get_latest_fetch
from .utils import parse_weight_to_kg, parse_price_value

from . import carrefour, lulu, barakat, kibsons, unioncoop

_FINDERS = {
    "carrefour": carrefour.find_url,
    "lulu": lulu.find_url,
    "barakat": barakat.find_url,
    "kibsons": kibsons.find_url,
    "unioncoop": unioncoop.find_url,
}

_SCRAPERS = {
    "carrefour": carrefour.scrape,
    "lulu": lulu.scrape,
    "barakat": barakat.scrape,
    "kibsons": kibsons.scrape,
    "unioncoop": unioncoop.scrape,
}


def find_product_url(site, product_name):
    """
    Search a supported supermarket for a product and return the product URL.
    """
    if site not in _FINDERS:
        raise ValueError(
            f"'{site}' isn't configured yet. Known sites: {list(SITE_SEARCH_CONFIG)}"
        )
    return _FINDERS[site](product_name)


def get_product_details(url, site):
    """
    Scrape the product page at `url` using the scraper registered for `site`.
    """
    if site not in _SCRAPERS:
        raise ValueError(f"No scraper implemented for {site}")
    return _SCRAPERS[site](url)


__all__ = [
    "SITE_SEARCH_CONFIG",
    "find_product_url",
    "get_product_details",
    "save_price_record",
    "get_price_history",
    "save_latest_fetch",
    "get_latest_fetch",
    "parse_weight_to_kg",
    "parse_price_value",
]