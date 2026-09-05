"""
Canonical product catalog shown in the frontend dropdown.

The frontend ALWAYS shows the same product names.
Each supermarket can use its own search keyword internally.

PRODUCTS below is the built-in, hardcoded catalog. On top of it, users can
add their own products from the "Products" tab in the UI - those are
persisted in the `products` collection in MongoDB (see db.py) rather than
in this file, so they survive restarts/redeploys without editing code.
get_all_products()/get_all_products_by_id() are what the rest of the app
(app.py's /api/meta and the scraper job resolver) should use - they merge
the built-in list below with whatever custom products are in MongoDB.
"""

import re

PRODUCTS = [
    {
        "id": "garlic",
        "name": "Garlic",
        "emoji": "🧄",
        "keywords": {
            "barakat": "garlic",
            "kibsons": "Garlic",
            "unioncoop": "Garlic",
            "carrefour": "Garlic",
            "lulu": "Ginger Garlic",
        },
    },
    {
        "id": "cucumber",
        "name": "Cucumber",
        "emoji": "🥒",
        "keywords": {
            "barakat": "Cucumber",
            "kibsons": "Cucumber",
            "unioncoop": "cucumber",
            "carrefour": "cucumber",
            "lulu": "Cucumber",
        },
    },
    {
        "id": "potato",
        "name": "Potato",
        "emoji": "🥔",
        "keywords": {
            "barakat": "Potatoes",
            "kibsons": " potato",
            "unioncoop": "potato",
            "carrefour": "Potato",
            "lulu": "Potato",
        },
    },
    {
        "id": "red_onion",
        "name": "Red Onion",
        "emoji": "🧅",
        "keywords": {
            "barakat": "onion red",
            "kibsons": "Red Onion",
            "unioncoop": "red onion",
            "carrefour": "onion",
            "lulu": "red onion",
        },
    },
    {
        "id": "tomato",
        "name": "Tomato",
        "emoji": "🍅",
        "keywords": {
            "barakat": "Tomato",
            "kibsons": "tomato",
            "unioncoop": "tomato",
            "carrefour": "Tomato",
            "lulu": "Tomato",
        },
    },
    {
        "id": "orange_valencia",
        "name": "Orange Valencia",
        "emoji": "🍊",
        "keywords": {
            "barakat": "Orange Valencia",
            "kibsons": "Valencia Orange",
            "unioncoop": "Orange valencia",
            "carrefour": "Valencia Orange",
            "lulu": "Valencia Orange",
        },
    },
    {
        "id": "orange_navel",
        "name": "Orange Navel",
        "emoji": "🍊",
        "keywords": {
            "barakat": "orange",
            "kibsons": "orange navel",
            "unioncoop": "navel orange",
            "carrefour": "Orange Navel",
            "lulu": "Navel Orange",
        },
    },
    {
        "id": "watermelon",
        "name": "Watermelon",
        "emoji": "🍉",
        "keywords": {
            "barakat": "Watermelon Juice",
            "kibsons": "watermelon",
            "unioncoop": "Watermelon",
            "carrefour": "watermelon",
            "lulu": "Watermelon saudi",
        },
    },
]

# Dictionary for quick lookup
PRODUCTS_BY_ID = {p["id"]: p for p in PRODUCTS}

# Retailer names shown in the frontend
RETAILER_LABELS = {
    "carrefour": "Carrefour",
    "lulu": "LuLu Hypermarket",
    "barakat": "Barakat",
    "kibsons": "Kibsons",
    "unioncoop": "Union Coop",
}


def get_search_keyword(product, retailer):
    """
    Returns the keyword that should be searched on a particular retailer.
    Falls back to the frontend product name if no retailer-specific keyword exists.
    """
    return product.get("keywords", {}).get(retailer, product["name"])


# ------------------------------------------------------------------
# Custom (user-added) products, persisted in MongoDB
# ------------------------------------------------------------------

def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "product"


def get_custom_products():
    """
    Returns user-added products from the `products` MongoDB collection.
    Returns an empty list (rather than raising) if MongoDB isn't configured
    or reachable, so /api/meta and the scraper still work with just the
    built-in catalog when Mongo is down.
    """
    from database import get_db
    try:
        db = get_db()
        return list(db.products.find({}, {"_id": 0}))
    except Exception:
        return []


def get_all_products():
    """Built-in PRODUCTS + whatever custom products are stored in MongoDB."""
    return PRODUCTS + get_custom_products()


def get_all_products_by_id():
    return {p["id"]: p for p in get_all_products()}


def add_custom_product(name, emoji=None):
    """
    Adds a new user-defined product. No per-retailer keyword mapping is
    stored - get_search_keyword() already falls back to the product name
    for any retailer without an explicit override, which is exactly what
    a freshly-added product needs to be scrapable immediately.
    """
    from database import get_db

    name = (name or "").strip()
    if not name:
        raise ValueError("Product name is required.")
    if len(name) > 60:
        raise ValueError("Product name is too long.")

    product_id = _slugify(name)
    existing_ids = set(PRODUCTS_BY_ID.keys()) | {p["id"] for p in get_custom_products()}
    if product_id in existing_ids:
        raise ValueError(f'"{name}" is already tracked.')

    doc = {
        "id": product_id,
        "name": name,
        "emoji": (emoji or "").strip() or "🛒",
        "keywords": {},
        "custom": True,
    }
    db = get_db()
    db.products.insert_one(dict(doc))
    return doc


def delete_custom_product(product_id):
    """Deletes a custom product. Built-in products can't be deleted this way."""
    from database import get_db

    if product_id in PRODUCTS_BY_ID:
        raise ValueError("Built-in products can't be deleted.")
    db = get_db()
    result = db.products.delete_one({"id": product_id, "custom": True})
    return result.deleted_count > 0


# ------------------------------------------------------------------
# Known stockouts: (retailer, product_id) pairs the site has delisted.
# scripts/push_retailer.py logs these as SKIP (not FAIL) so a scheduled
# run stays green while the gap stays visible in its log. The scraper
# itself still attempts them (an interactive fetch reports the honest
# failure) - the skip applies to scheduled pushes only, where a daily
# red run over a known-delisted product would just train everyone to
# ignore failures. Remove the entry when the site relists the product.
#
# Currently empty. History: ("barakat", "orange_navel") used to be here
# (Barakat delisted all navel oranges site-wide, verified 2026-09-03),
# but per request navel now falls back to Barakat's regular fresh orange
# via the pinned "navel orange" URL in scraper/retailers/barakat.py, so
# no skip is needed. services/fetch.py also understands the "skipped"
# result flag if entries are ever added back.
# ------------------------------------------------------------------
RETAILER_PRODUCT_SKIPS = set()