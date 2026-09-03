"""
Persistence: upserts one scrape result as a document in the `price_history`
MongoDB collection.

One document per (product, retailer, day) - if a scrape already ran today
for this product+retailer, it's overwritten in place rather than duplicated.
"""

from db import get_db


def save_price_record(data, product_id=None, product_label=None):
    db = get_db()
    today = data["current_time"][:10]

    record = {
        "product_id": product_id,
        "product_label": product_label,
        "scraped_title": data["product"],
        "per_kg_price": data.get("per_kg_price"),
        "country_of_origin": data["country_of_origin"],
        "supermarket": data["supermarket"],
        "url": data["url"],
        "timestamp": data["current_time"],
        "date": today,
    }

    db.price_history.update_one(
        {"product_id": product_id, "supermarket": data["supermarket"], "date": today},
        {"$set": record},
        upsert=True,
    )


def save_latest_fetch(data, product_id=None, product_label=None):
    """
    Upserts one document per (product_id, supermarket) into the
    `latest_fetch` collection - unlike price_history (one row per day,
    kept forever), this collection only ever holds the single most recent
    scrape for each product+retailer. Every new successful scrape
    overwrites the previous entry in place (same product_id+supermarket ->
    same document, just updated), so the "Latest Fetch Results" table
    always reflects the newest data and never accumulates duplicates.
    """
    db = get_db()

    record = {
        "product_id": product_id,
        "product_label": product_label,
        "scraped_title": data["product"],
        "per_kg_price": data.get("per_kg_price"),
        "country_of_origin": data.get("country_of_origin"),
        "supermarket": data["supermarket"],
        "url": data.get("url"),
        "timestamp": data["current_time"],
        "date": data["current_time"][:10],
    }

    db.latest_fetch.update_one(
        {"product_id": product_id, "supermarket": data["supermarket"]},
        {"$set": record},
        upsert=True,
    )


def get_latest_fetch():
    """All rows currently in the latest-fetch snapshot, one per product+retailer."""
    db = get_db()
    return list(db.latest_fetch.find({}))


def get_price_history(product_id, retailer):
    """
    Ascending-by-timestamp list of {"price": float, "timestamp": str} for
    one product+retailer, skipping records with no parseable price. Used by
    app.py to work out the previous price vs. the one just scraped.
    """
    import re

    db = get_db()
    docs = db.price_history.find(
        {
            "product_id": product_id,
            "supermarket": {"$regex": f"^{re.escape(retailer)}$", "$options": "i"},
        }
    ).sort("timestamp", 1)

    history = []
    for d in docs:
        price = _price_to_float(d.get("per_kg_price"))
        if price is None:
            continue
        history.append({"price": price, "timestamp": d.get("timestamp")})
    return history


def _price_to_float(text):
    import re
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(text))
    return float(m.group(1)) if m else None