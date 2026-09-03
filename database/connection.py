"""
MongoDB connection layer for MarketPulse / IntelliCrop.

Centralizes the Mongo client so every module (auth/, scraper/,
services/, app.py) shares one connection instead of opening its own. Configure via
environment variables - never hardcode credentials in source:

    MONGODB_URI    - full connection string, e.g.
                      mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
    MONGO_DB_NAME  - database name to use (default: "intellicrop")

Keep these in a local `.env` (not committed - see .gitignore) for
development, and in your host's secret manager for production.

If MONGODB_URI isn't set or the cluster can't be reached, get_client()/
get_db() raise a clear RuntimeError the first time they're used, and
get_status() reports it instead of raising - that's what the admin
"Database Settings" panel calls.
"""

import os
import time

from pymongo import MongoClient, ReturnDocument
from pymongo.server_api import ServerApi
from pymongo.errors import PyMongoError

try:
    from dotenv import load_dotenv
    load_dotenv()  # no-op if there's no .env file - safe to call unconditionally
except ImportError:
    pass

MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "marketpulse")

_client = None
_client_error = None


def _connect():
    global _client, _client_error
    if _client is not None or _client_error is not None:
        return
    if not MONGODB_URI:
        _client_error = (
            "MONGODB_URI is not set. Add it to your environment (or a local "
            ".env file) - see README's Environment variables table."
        )
        return
    try:
        client = MongoClient(
            MONGODB_URI,
            server_api=ServerApi("1"),
            serverSelectionTimeoutMS=8000,
        )
        client.admin.command("ping")  # forces an actual round-trip now, not on first query
        _client = client
    except PyMongoError as e:
        _client_error = f"Could not connect to MongoDB: {e}"


def get_client():
    _connect()
    if _client is None:
        raise RuntimeError(_client_error or "MongoDB client not initialized.")
    return _client


def get_db():
    return get_client()[MONGO_DB_NAME]


def is_configured():
    return bool(MONGODB_URI)


def next_sequence(name):
    """
    Atomic auto-increment counter - Mongo has no native autoincrement, so
    user ids (which the frontend/routes treat as plain ints, e.g.
    /api/users/<int:user_id>) come from here instead of an ObjectId.
    """
    db = get_db()
    doc = db.counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"] #type: ignore


def ensure_indexes():
    """Idempotent - safe to call on every startup (see init_auth in auth.py)."""
    db = get_db()
    db.users.create_index("id", unique=True)
    db.users.create_index("username", unique=True)
    db.users.create_index("google_id", unique=True, sparse=True)
    db.price_history.create_index(
        [("product_id", 1), ("supermarket", 1), ("date", 1)], unique=True
    )
    db.price_history.create_index([("product_id", 1), ("supermarket", 1), ("timestamp", 1)])
    db.latest_fetch.create_index([("product_id", 1), ("supermarket", 1)], unique=True)
    db.products.create_index("id", unique=True)


def get_status():
    """
    Health-check snapshot for the admin "Database Settings" panel. Never
    raises - always returns a dict describing the current state. Forces a
    fresh connection check each call (rather than reusing a cached client)
    since the whole point is to show the *current* state - a network blip,
    rotated credentials, or a paused cluster should show up immediately.
    """
    global _client, _client_error
    _client = None
    _client_error = None

    started = time.time()
    _connect()
    latency_ms = round((time.time() - started) * 1000)

    if _client is None:
        return {
            "configured": is_configured(),
            "connected": False,
            "error": _client_error,
            "db_name": MONGO_DB_NAME,
            "host": None,
            "latency_ms": None,
            "collections": {},
        }

    db = _client[MONGO_DB_NAME]

    def _safe_count(coll, query=None):
        try:
            return coll.count_documents(query or {})
        except PyMongoError:
            return None

    try:
        distinct_products = len(db.price_history.distinct("product_id"))
    except PyMongoError:
        distinct_products = None

    try:
        host_info = _client.address
        host_display = f"{host_info[0]}:{host_info[1]}" if host_info else None
    except Exception:
        host_display = None

    return {
        "configured": True,
        "connected": True,
        "error": None,
        "db_name": MONGO_DB_NAME,
        "host": host_display,
        "latency_ms": latency_ms,
        "collections": {
            "users": _safe_count(db.users),
            "price_records": _safe_count(db.price_history),
            "distinct_products": distinct_products,
        },
    }