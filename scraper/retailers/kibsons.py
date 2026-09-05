"""
Kibsons via its product-catalog JSON API + static product pages - no browser.

Search pages are JS-rendered, but the frontend's own catalog endpoint
(POST apinode.kibsons.com/product/productsv26) returns most products with
name/price/pack-size/origin, so find_url() scores that locally instead of
scraping the search page. Listings the API doesn't carry (navel/valencia
oranges) resolve via verified KNOWN_URLS; the stealthy browser is a last
resort only. Product pages themselves are server-rendered, so scrape()
stays plain HTTP. fetch_mode is "fast": the catalog + known-URL paths
never use a browser, which is what serves the hosted run.

The 14MB catalog is fetched at most once per CATALOG_TTL_SECONDS and shared
by every lookup, so an all-products job costs one heavy request, not 40.
"""

import gc
import re
import threading
import time
from datetime import datetime

from ..config import SITE_SEARCH_CONFIG
from ..utils import (
    fetch_fast,
    fetch_with_fallback,
    iter_links,
    rank_links,
    css_first_text,
    css_all_text,
    compute_per_kg,
)

SITE = "kibsons"

# Listings the catalog API doesn't carry (verified live 2026-09-03: zero
# hits for "navel"/"valencia" across all 10k catalog records, while the
# website lists both). Resolved before the stealthy fallback so the hosted
# run (no browsers on Render free) succeeds. Each URL is re-verified live
# via _verify_url before being returned.
KNOWN_URLS = {
    "orange navel": "https://www.kibsons.com/en/product/fruits/citrus/kibsons-navel-oranges-oranazakbpacs1",
    "navel orange": "https://www.kibsons.com/en/product/fruits/citrus/kibsons-navel-oranges-oranazakbpacs1",
    "orange valencia": "https://www.kibsons.com/en/product/fruits/citrus/kibsons-valencia-oranges-oravazakbpacs1",
    "valencia orange": "https://www.kibsons.com/en/product/fruits/citrus/kibsons-valencia-oranges-oravazakbpacs1",
}

CATALOG_URL = "https://apinode.kibsons.com/product/productsv26"
CATALOG_TTL_SECONDS = 30 * 60
CATALOG_MAX_PAGES = 6

# Only the fields find_url()/scrape() actually read - the raw records are
# huge and 5 pages x 2000 records would otherwise eat ~150MB on free Render.
_CATALOG_FIELDS = (
    "stockDesc", "stockRate", "stockShortDetail", "stockUnits", "stockOrigin",
    "stockCode", "brandDesc", "productkey", "sayt_stockdesc", "sayt_productdesc",
)

_catalog_cache = {"at": 0.0, "products": []}
# Single-flight: without this, N pool threads with a cold cache each fetch
# all catalog pages at once (N x 5 pages x ~14MB JSON parsed simultaneously),
# which OOM-kills the 512MB Render free container mid-job. The lock forces
# one fetch; the rest wait and share the result.
_catalog_lock = threading.Lock()


def _project_record(record):
    return {key: record.get(key) for key in _CATALOG_FIELDS}


def _get_catalog():
    now = time.time()
    if _catalog_cache["products"] and now - _catalog_cache["at"] < CATALOG_TTL_SECONDS:
        return _catalog_cache["products"]
    with _catalog_lock:
        # Double-check: another thread may have filled the cache while we waited.
        now = time.time()
        if _catalog_cache["products"] and now - _catalog_cache["at"] < CATALOG_TTL_SECONDS:
            return _catalog_cache["products"]
        products = _fetch_catalog_pages()
        _catalog_cache["at"] = time.time()
        _catalog_cache["products"] = products
        return products


def _fetch_catalog_pages():
    from scrapling.fetchers import Fetcher

    import json as _json

    merged = []
    seen_codes = set()
    total = None
    for page_no in range(1, CATALOG_MAX_PAGES + 1):
        page = Fetcher.post(
            CATALOG_URL,
            json={"page": page_no} if page_no > 1 else {"search": "x"},
            impersonate="chrome",
            stealthy_headers=True,
            timeout=60,
        )
        data = _json.loads(bytes(page.body or b"").decode("utf-8", "ignore"))
        del page
        node = data.get("data") or {}
        if total is None:
            try:
                total = int(node.get("totalcount") or 0)
            except (TypeError, ValueError):
                total = 0
        batch = node.get("products") or []
        del data, node
        if not batch:
            break
        for record in batch:
            code = (record.get("stockCode") or "").upper()
            if code and code not in seen_codes:
                seen_codes.add(code)
                merged.append(_project_record(record))
        del batch
        if total and len(merged) >= total:
            break
    if not merged:
        raise ValueError("Kibsons catalog API returned no products.")
    # Drop the transient per-page response trees promptly - peak RSS is what
    # kills the 512MB Render container, not the retained projection.
    gc.collect()
    return merged


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


def _check_url(url, record):
    """Live check with reason: fetch + h1 match. Returns (ok, reason)."""
    try:
        page = fetch_fast(url, timeout=30, site=SITE)
    except Exception as e:
        return False, f"product page fetch failed ({e})"
    try:
        h1 = (page.css("h1::text").get() or "").strip().lower()  # type: ignore[attr-defined]
    except Exception as e:
        return False, f"product page parse failed ({e})"
    if not h1:
        return False, "product page has no title"
    words = _product_name_words(record)
    if bool(words) and any(w in h1 for w in words):
        return True, "ok"
    return False, f"h1 mismatch (got '{h1[:80]}')"


def _verify_url(url, record):
    """Fetch candidate product page; accept it if its <h1> matches the record."""
    ok, _reason = _check_url(url, record)
    return ok


def _resolve(href, base_url):
    href = (href or "").strip()
    if href.startswith("/"):
        href = base_url.rstrip("/") + href
    return href


def find_url(product_name):
    config = SITE_SEARCH_CONFIG[SITE]

    # Primary (no browser): score the catalog API records locally.
    try:
        products = _get_catalog()
    except Exception:
        products = []
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
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        for _score, record in scored[:3]:
            for candidate in _candidate_urls(record):
                if _verify_url(candidate, record):
                    return candidate

    # Fallback 1 (no browser): pinned URLs for listings the catalog API
    # doesn't carry (navel/valencia oranges). Verified live via the same
    # h1 check - a renamed/delisted product fails loudly instead of
    # recording a stale price. This is what serves the hosted run.
    known = KNOWN_URLS.get(product_name.strip().lower())
    known_ok, known_reason = (
        _check_url(known, {"stockDesc": product_name}) if known else (False, "no known URL")
    )
    if known_ok:
        return known  # type: ignore[return-value]

    # Fallback 2 (browser): render the search page via fetch_with_fallback
    # (fast first, stealthy only if blocked). On slim hosts without
    # browsers fetch_with_fallback re-raises the FAST error (e.g. HTTP 403
    # / Cloudflare from a datacenter IP), which is the actionable signal -
    # never the Playwright "Executable doesn't exist" stack.
    query = product_name.strip().replace(" ", "%20")
    search_url = config["search_url"].format(query=query)
    try:
        page = fetch_with_fallback(
            search_url,
            mode=config.get("fetch_mode", "auto"),
            wait_selector="a[href*='/product/']",
            site=SITE,
        )
    except Exception as e:
        msg = str(e)
        if "Stealthy browser not installed" in msg:
            raise ValueError(
                f"Couldn't find '{product_name}' on {SITE}: host blocked fast HTTP"
                f" ({known_reason}) and has no stealthy browser (slim image)."
                f" Works locally; on Render set PROXY_{SITE.upper()}=http://user:pass@host:port"
                f" or skip this product."
            ) from None
        raise ValueError(
            f"Couldn't find '{product_name}' on {SITE}: search blocked ({e});"
            f" known URL unverified ({known_reason})."
        ) from None
    links = [(url, text) for url, text in iter_links(page, config["result_selector"])]
    for href in rank_links(links, product_name)[:5]:
        try:
            check = fetch_fast(_resolve(href, config["base_url"]), timeout=30, site=SITE)
            try:
                h1 = (check.css("h1::text").get() or "").strip().lower()  # type: ignore[attr-defined]
            except Exception:
                h1 = ""
            if h1 and product_name.lower().split()[0] in h1:
                return _resolve(href, config["base_url"])
        except Exception:
            continue

    raise ValueError(
        f"Couldn't find '{product_name}' on {SITE}"
        + (f" (known URL: {known_reason})" if known else "")
        + "."
    )


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
    from ..utils import parse_weight_to_kg

    config = SITE_SEARCH_CONFIG[SITE]
    mode = config.get("fetch_mode", "fast")

    title = None
    weight_kg = None
    price_value = None
    country = None
    catalog_record = None

    try:
        page = fetch_with_fallback(url, mode=mode, site=SITE)
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
