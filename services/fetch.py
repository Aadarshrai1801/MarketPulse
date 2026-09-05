"""One-off scrape orchestration: resolve selections, run lookups, persist.

This is the business logic behind ``POST /api/fetch`` and the async job
runner in :mod:`services.jobs`. The web layer (app.py) only resolves the
request params via :func:`resolve_retailers` / :func:`resolve_products`
and calls :func:`scrape_one` - it never touches retailer modules directly.
"""
import os
import re

from catalog import (
    RETAILER_PRODUCT_SKIPS,
    get_search_keyword,
    get_all_products,
    get_all_products_by_id,
)
from database.prices import (
    get_price_history,
    save_price_record,
    save_latest_fetch,
)
from scraper import SITE_SEARCH_CONFIG, find_product_url, get_product_details

RETAILERS = list(SITE_SEARCH_CONFIG.keys())


def disabled_retailers():
    """Retailer ids disabled via DISABLED_RETAILERS env (comma-separated).

    Escape hatch for hosts whose egress a site blocks: Unioncoop's WAF
    rejects requests from hosting datacenter IPs (search 405s AND product
    pages fail verification), so on Render set DISABLED_RETAILERS=unioncoop
    and jobs run 32/32 instead of 32 ok + 8 doomed rows. Local runs leave
    it unset and keep all 5 retailers.
    """
    return {
        r.strip().lower()
        for r in os.environ.get("DISABLED_RETAILERS", "").split(",")
        if r.strip()
    }


def available_retailers():
    """RETAILERS minus disabled ones - what the UI offers and jobs run."""
    return [r for r in RETAILERS if r not in disabled_retailers()]


def price_to_float(text):
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def get_previous_price(product_id, retailer):
    """
    Returns the previous saved price for the same product and retailer,
    ignoring the current price if it is already the newest entry.
    """
    history = get_price_history(product_id, retailer)

    if len(history) < 2:
        return None

    return history[-2]["price"]


def resolve_retailers(retailer_param):
    if not retailer_param or retailer_param == "all":
        return available_retailers()
    if retailer_param not in SITE_SEARCH_CONFIG:
        return []
    if retailer_param in disabled_retailers():
        return []
    return [retailer_param]


def resolve_products(product_param):
    if not product_param or product_param == "all":
        return get_all_products()
    p = get_all_products_by_id().get(product_param)
    return [p] if p else []


def scrape_one(product, retailer):
    """Run one product x retailer lookup, save it if it succeeds, and
    always return a plain-dict result (never raises).

    Known stockouts (catalog.RETAILER_PRODUCT_SKIPS, e.g. Barakat navel
    oranges delisted site-wide) short-circuit here without any network
    call: the result carries ``skipped=True`` so the UI / job runner can
    show it as info rather than a scary ⚠ failure, and nothing is written
    to MongoDB. scripts/push_retailer.py also checks the same set before
    calling this, so scheduled pushes keep logging SKIP there.
    """
    if (retailer, product["id"]) in RETAILER_PRODUCT_SKIPS:
        keyword = get_search_keyword(product, retailer)
        return {
            "ok": False,
            "skipped": True,
            "supermarket": retailer,
            "product_id": product["id"],
            "product_label": product["name"],
            "product_emoji": product["emoji"],
            "keyword_used": keyword,
            "error": f"Delisted on {retailer.capitalize()} (known stockout — skipped).",
        }

    keyword = get_search_keyword(product, retailer)

    try:
        url = find_product_url(retailer, keyword)
        data = get_product_details(url, retailer)

        # Use per_kg_price, not "price" - that's the field every retailer
        # module actually returns (Carrefour returns both, but Barakat,
        # Kibsons, LuLu, and Union Coop only ever set per_kg_price), and
        # it's also the exact column get_previous_price() reads history
        # from below, so this keeps current vs. previous comparing
        # like-for-like.
        current_price = price_to_float(data.get("per_kg_price"))

        previous_price = get_previous_price(product["id"], retailer)

        data["previous_price"] = previous_price

        # Guard against either side being None (e.g. this scrape failed to
        # find a price, or there's no usable history yet) - comparing None
        # to a float raises TypeError and used to crash the whole job once
        # enough history had built up.
        if previous_price is None or current_price is None:
            data["price_change"] = "same"
        elif current_price > previous_price:
            data["price_change"] = "up"
        elif current_price < previous_price:
            data["price_change"] = "down"
        else:
            data["price_change"] = "same"

        data["ok"] = True
        data["product_id"] = product["id"]
        data["product_label"] = product["name"]
        data["product_emoji"] = product["emoji"]

        save_price_record(
            data,
            product_id=product["id"],
            product_label=product["name"],
        )
        # Overwrites (rather than appends to) the one row this product+retailer
        # has in `latest_fetch` - this is what /api/latest and the "Latest Fetch
        # Results" table read from, so that table always shows only the newest
        # scrape per product+retailer, persisted server-side in MongoDB.
        save_latest_fetch(
            data,
            product_id=product["id"],
            product_label=product["name"],
        )
        return data
    except Exception as e:
        return {
            "ok": False,
            "supermarket": retailer,
            "product_id": product["id"],
            "product_label": product["name"],
            "product_emoji": product["emoji"],
            "keyword_used": keyword,
            "error": str(e),
        }
