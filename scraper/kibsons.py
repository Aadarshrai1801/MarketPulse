"""
Kibsons via its product-catalog JSON API + static product pages - no browser.

Search pages are JS-rendered, but the frontend's own catalog endpoint
(POST apinode.kibsons.com/product/productsv26) returns all ~2000 products
with name/price/pack-size/origin, so find_url() scores that locally instead
of scraping the search page. Product pages themselves are server-rendered,
so scrape() stays plain HTTP. fetch_mode is "fast": a browser is never used.

The 14MB catalog is fetched at most once per CATALOG_TTL_SECONDS and shared
by every lookup, so an all-products job costs one heavy request, not 40.
"""

import re
import time
from datetime import datetime

from .config import SITE_SEARCH_CONFIG
from .utils import (
    fetch_fast,
    fetch_with_fallback,
    iter_links,
    css_first_text,
    css_all_text,
    compute_per_kg,
)

SITE = "kibsons"

CATALOG_URL = "https://apinode.kibsons.com/product/productsv26"
CATALOG_TTL_SECONDS = 30 * 60

_catalog_cache = {"at": 0.0, "products": []}


def _get_catalog():
    now = time.time()
    if _catalog_cache["products"] and now - _catalog_cache["at"] < CATALOG_TTL_SECONDS:
        return _catalog_cache["products"]
    from scrapling.fetchers import Fetcher

    page = Fetcher.post(
        CATALOG_URL,
        json={"search": "x"},
        impersonate="chrome",
        stealthy_headers=True,
        timeout=60,
    )
    import json as _json

    data = _json.loads(bytes(page.body or b"").decode("utf-8", "ignore"))
    products = (data.get("data") or {}).get("products") or []
    if not products:
        raise ValueError("Kibsons catalog API returned no products.")
    _catalog_cache["at"] = now
    _catalog_cache["products"] = products
    return products


def _tokens(text):
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _stem(token):
    # Lightweight plural handling so "potatoes" matches "potato-1kg".
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _score_record(keyword, record):
    """Higher = better match of the search keyword to a catalog record.

    ALL keyword tokens must be present (stemmed): a price tracker must
    never record a flower ("Orange Rose") as an orange or juice as a
    whole fruit. No full match -> no record (honest failure) rather than
    a wrong product.
    """
    key_tokens = [_stem(t) for t in _tokens(keyword)]
    if not key_tokens:
        return None
    hay_tokens = [_stem(t) for t in _tokens(record.get("stockDesc") or "")]
    hay_tokens += [_stem(t) for t in _tokens(record.get("sayt_stockdesc") or "")]
    hay_set = set(hay_tokens)
    if any(t not in hay_set for t in key_tokens):
        return None
    # Prefer the most generic name ("Tomatoes" over "Cherry Plum Tomatoes").
    extra = len(set(hay_tokens) - set(key_tokens))
    return -(extra * 0.5) - (len(record.get("stockDesc") or "") * 0.01)


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _candidate_urls(record):
    code = (record.get("stockCode") or "").lower()
    if not code:
        return []
    family = _slugify(record.get("productkey") or "vegetables") or "vegetables"
    brand = _slugify(record.get("brandDesc") or "")
    desc = _slugify(record.get("stockDesc") or "")
    slugs = []
    if brand and desc:
        slugs.append(f"{brand}-{desc}-{code}")
    if desc:
        slugs.append(f"{desc}-{code}")
    return [
        f"https://www.kibsons.com/en/product/vegetables/{family}/{slug}"
        for slug in slugs
    ]


def _product_name_words(record):
    return [t for t in _tokens(record.get("stockDesc") or "") if len(t) > 2]


def _verify_url(url, record):
    """Fetch candidate product page; accept it if its <h1> matches the record."""
    try:
        page = fetch_fast(url, timeout=30)
    except Exception:
        return False
    try:
        h1 = (page.css("h1::text").get() or "").strip().lower()  # type: ignore[attr-defined]
    except Exception:
        return False
    if not h1:
        return False
    words = _product_name_words(record)
    return bool(words) and any(w in h1 for w in words)


def _resolve(href, base_url):
    href = (href or "").strip()
    if href.startswith("/"):
        href = base_url.rstrip("/") + href
    return href


def find_url(product_name):
    config = SITE_SEARCH_CONFIG[SITE]

    products = _get_catalog()
    scored = []
    for record in products:
        score = _score_record(product_name, record)
        if score is not None:
            try:
                rate = float(record.get("stockRate") or 0)
            except (TypeError, ValueError):
                rate = 0
            if rate > 0:
                scored.append((score, record))
    if not scored:
        raise ValueError(f"Couldn't find '{product_name}' on {SITE}.")
    scored.sort(key=lambda item: item[0], reverse=True)
    best = scored[0][1]

    for candidate in _candidate_urls(best):
        if _verify_url(candidate, best):
            return candidate

    # Couldn't verify a product page - return the search URL as the reference
    # link rather than failing; scrape() falls back to the catalog record.
    query = product_name.strip().replace(" ", "%20")
    return config["search_url"].format(query=query)


def _record_from_product_url(url):
    """Look up the catalog record whose stockCode is embedded in a product URL."""
    m = re.search(r"-([a-z0-9]{6,})\s*$", url.strip().rstrip("/"), re.IGNORECASE)
    if not m:
        return None
    code = m.group(1).upper()
    try:
        products = _get_catalog()
    except Exception:
        return None
    for record in products:
        if (record.get("stockCode") or "").upper() == code:
            return record
    return None


def scrape(url):
    from .utils import parse_weight_to_kg

    config = SITE_SEARCH_CONFIG[SITE]
    mode = config.get("fetch_mode", "fast")

    title = None
    weight_kg = None
    price_value = None
    country = None
    catalog_record = None

    try:
        page = fetch_with_fallback(url, mode=mode)
    except Exception:
        page = None

    if page is not None and "/product/" in url:
        # ---------------------------------------------------------
        # PRODUCT NAME
        # ---------------------------------------------------------
        title = css_first_text(page, ["h1"])

        # ---------------------------------------------------------
        # PRODUCT WEIGHT (Example: Approx 500g)
        # ---------------------------------------------------------
        for weight_text in css_all_text(page, "div.tw-text-primary.tw-py-1"):
            weight_kg, _ = parse_weight_to_kg(weight_text)
            if weight_kg is not None:
                break

        # ---------------------------------------------------------
        # PRODUCT PRICE
        # ---------------------------------------------------------
        for selector in ["p.tw-font-\\[600\\]", "p[class*='tw-font']", "[class*='price']"]:
            for price_text in css_all_text(page, selector):
                match = re.search(r"\d+(?:\.\d+)?", price_text)
                if match:
                    try:
                        price_value = float(match.group())
                        break
                    except ValueError:
                        continue
            if price_value is not None:
                break

        # ---------------------------------------------------------
        # COUNTRY OF ORIGIN
        # ---------------------------------------------------------
        country = css_first_text(page, ["div.tw-text-green.tw-uppercase"])

    # Catalog fallback: search pages / failed parses resolve via the API
    # record (the stockCode rides along in verified product URLs).
    if title is None or price_value is None:
        catalog_record = _record_from_product_url(url)
    if catalog_record is not None:
        title = title or catalog_record.get("stockDesc")
        country = country or catalog_record.get("stockOrigin")
        try:
            price_value = price_value or float(catalog_record.get("stockRate") or 0) or None
        except (TypeError, ValueError):
            pass
        if weight_kg is None:
            weight_kg, _ = parse_weight_to_kg(catalog_record.get("stockShortDetail") or "")

    # ---------------------------------------------------------
    # PER KG PRICE (always canonical "AED X.XX/kg" when a price exists)
    # ---------------------------------------------------------
    per_kg_price = compute_per_kg(price_value, weight_kg)

    return {
        "product": title,
        "per_kg_price": per_kg_price,
        "country_of_origin": country,
        "url": url,
        "supermarket": "kibsons",
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
