"""
Barakat Fresh via its XML sitemap + static product pages - no browser.

Barakat's search page is JS-rendered and its search API needs a browser
session, but robots.txt advertises a full static sitemap
(/pub/sitemap.xml, ~6k product URLs), so find_url() scores that locally.
The 6MB sitemap is fetched at most once per SITEMAP_TTL_SECONDS and shared
by every lookup. fetch_mode stays "fast": a browser is never used.
"""

import re
import threading
import time
from datetime import datetime

from ..config import SITE_SEARCH_CONFIG
from ..utils import (
    fetch_with_fallback,
    css_first_text,
    css_all_text,
    parse_weight_to_kg,
    compute_per_kg,
)

SITE = "barakat"

SITEMAP_URL = "https://barakatfresh.ae/pub/sitemap.xml"
SITEMAP_TTL_SECONDS = 12 * 60 * 60

# Keywords whose best sitemap match is ambiguous (e.g. "Watermelon Juice"
# would score the juice SKUs over the fruit) get a pinned canonical URL.
URL_OVERRIDES = {
    "watermelon": "https://barakatfresh.ae/en/watermelon-sliced-500g.html",
}

_sitemap_cache = {"at": 0.0, "urls": []}
# Single-flight (same OOM rationale as the Kibsons catalog lock): one
# thread fetches the 6MB sitemap, the rest wait and share it.
_sitemap_lock = threading.Lock()


def _get_sitemap_urls():
    now = time.time()
    if _sitemap_cache["urls"] and now - _sitemap_cache["at"] < SITEMAP_TTL_SECONDS:
        return _sitemap_cache["urls"]
    with _sitemap_lock:
        now = time.time()
        if _sitemap_cache["urls"] and now - _sitemap_cache["at"] < SITEMAP_TTL_SECONDS:
            return _sitemap_cache["urls"]
        from scrapling.fetchers import Fetcher

        page = Fetcher.get(
            SITEMAP_URL,
            impersonate="chrome",
            stealthy_headers=True,
            timeout=60,
        )
        body = bytes(page.body or b"").decode("utf-8-sig", "ignore")
        del page
        urls = re.findall(r"<loc>(.*?)</loc>", body)
        urls = [u.strip() for u in urls if u.strip().endswith(".html")]
        if not urls:
            raise ValueError("Barakat sitemap returned no product URLs.")
        _sitemap_cache["at"] = time.time()
        _sitemap_cache["urls"] = urls
        return urls


def _tokens(text):
    import re as _re

    return [t for t in _re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _stem(token):
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _slug_tokens(url):
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    if slug.endswith(".html"):
        slug = slug[: -len(".html")]
    return _tokens(slug)


def _score_url(keyword, url):
    key_tokens = [_stem(t) for t in _tokens(keyword)]
    if not key_tokens:
        return None
    slug_stems = [_stem(t) for t in _slug_tokens(url)]
    slug_set = set(slug_stems)
    matched = sum(1 for t in key_tokens if t in slug_set)
    if matched == 0:
        return None
    extra = len(set(slug_stems) - set(key_tokens))
    weight_bonus = 0.5 if parse_weight_to_kg(" ".join(_slug_tokens(url)))[0] else 0.0
    return (matched * 10) - (extra * 0.4) + weight_bonus


def _h1_matches(page, product_name):
    """Token-subset match: every keyword token must appear in the <h1>
    (any order), so 'onion red' matches 'Onion Red India 1kg' while a
    plain substring check would miss it."""
    try:
        title = page.css("h1::text").get()  # type: ignore[attr-defined]
    except Exception:
        title = None
    if not title:
        return False
    title_tokens = set(_tokens(str(title).strip()))
    key_tokens = [_stem(t) for t in _tokens(product_name)]
    return bool(key_tokens) and all(t in title_tokens for t in key_tokens)


def _page_looks_like_product(page):
    """Status 200 + non-empty <h1> + a price on the page (used for pinned
    override URLs whose h1 legitimately differs from the search keyword)."""
    try:
        status = int(getattr(page, "status", 200) or 200)
    except Exception:
        status = 200
    if status != 200:
        return False
    try:
        title = page.css("h1::text").get()  # type: ignore[attr-defined]
    except Exception:
        title = None
    if not title or not str(title).strip():
        return False
    return any(
        css_all_text(page, selector)
        for selector in ["span.styles_price_value__4mAeb", "span.styles_price_full__opoLn"]
    )


def _verify_override(url):
    try:
        page = fetch_with_fallback(url, mode="fast")
    except Exception:
        return False
    return _page_looks_like_product(page)


def _verify_url(url, product_name, mode):
    try:
        page = fetch_with_fallback(url, mode="fast")
    except Exception:
        return False
    try:
        status = int(getattr(page, "status", 200) or 200)
    except Exception:
        status = 200
    return status == 200 and _h1_matches(page, product_name)


def _slug_candidates(product_name, base_url):
    """Legacy slug-guess fallback (kept for resilience if the sitemap fetch
    fails) - probes plain/product-weight/config URL variants."""
    slug = (
        product_name.lower()
        .strip()
        .replace("&", "and")
        .replace(",", "")
        .replace("/", "-")
        .replace("(", "")
        .replace(")", "")
        .replace(" ", "-")
    )
    plural_map = {
        "potato": "potatoes",
        "potatoes": "potatoes",
        "onion": "onions",
        "red-onion": "onions",
        "white-onion": "onions",
        "tomato": "tomatoes",
        "cucumber": "cucumbers",
    }
    if slug in plural_map:
        slug = plural_map[slug]

    paths = [
        f"/{slug}.html",
        f"/{slug}-250g.html",
        f"/{slug}-500g.html",
        f"/{slug}-750g.html",
        f"/{slug}-1kg.html",
        f"/{slug}-2kg.html",
    ]
    candidates = []
    for prefix in ("", "/en"):
        for path in paths:
            candidates.append(f"{base_url}{prefix}{path}")
    for config_id in ("84", "162", "187", "215", "537"):
        candidates.append(f"{base_url}/{slug}.html?config={config_id}")
    return candidates


def find_url(product_name):
    config = SITE_SEARCH_CONFIG[SITE]
    base_url = config["base_url"].rstrip("/")

    override = URL_OVERRIDES.get(product_name.strip().lower())
    if override and _verify_override(override):
        return override

    # Primary: score the static sitemap's canonical product URLs.
    try:
        urls = _get_sitemap_urls()
    except Exception:
        urls = []
    scored = []
    for url in urls:
        score = _score_url(product_name, url)
        if score is not None:
            scored.append((score, url))
    scored.sort(key=lambda item: item[0], reverse=True)
    for _score, url in scored[:5]:
        if _verify_url(url, product_name, "fast"):
            return url
    if override:
        # Pinned URL didn't verify (renamed?) - return it anyway rather than
        # failing outright; scrape() will surface whatever is there.
        return override

    # Fallback: legacy slug guessing.
    for candidate in _slug_candidates(product_name, base_url):
        if _verify_url(candidate, product_name, "fast"):
            return candidate

    raise ValueError(f"Couldn't find '{product_name}' on Barakat.")


def scrape(url):
    config = SITE_SEARCH_CONFIG[SITE]
    mode = config.get("fetch_mode", "fast")
    page = fetch_with_fallback(url, mode=mode)

    # ---------------------------------------------------------
    # PRODUCT NAME
    # ---------------------------------------------------------
    title = None
    try:
        title = page.css("h1::text").get()  # type: ignore[attr-defined]
        title = str(title).strip() if title else None
    except Exception:
        title = None

    # ---------------------------------------------------------
    # PRICE
    # ---------------------------------------------------------
    price_value = None
    for selector in ["span.styles_price_value__4mAeb", "span.styles_price_full__opoLn"]:
        for price_text in css_all_text(page, selector):
            m = re.search(r"(\d+(?:\.\d+)?)", price_text)
            if m:
                try:
                    price_value = float(m.group(1))
                    break
                except ValueError:
                    continue
        if price_value is not None:
            break

    # ---------------------------------------------------------
    # WEIGHT
    # ---------------------------------------------------------
    weight_kg = None
    for selector in ["span.styles_variations_value__7E9NH", "span.styles_configs_value_text__kjxvX"]:
        for weight_text in css_all_text(page, selector):
            m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g)", weight_text, re.I)
            if m:
                try:
                    weight = float(m.group(1))
                except ValueError:
                    continue
                unit = m.group(2).lower()
                weight_kg = weight / 1000 if unit == "g" else weight
                break
        if weight_kg is not None:
            break

    # ---------------------------------------------------------
    # PER KG PRICE (always canonical "AED X.XX/kg" when a price exists)
    # ---------------------------------------------------------
    per_kg_price = compute_per_kg(price_value, weight_kg)

    # ---------------------------------------------------------
    # COUNTRY OF ORIGIN
    # ---------------------------------------------------------
    country = css_first_text(
        page, ["span.styles_badges_text__WJmpL", "div.styles_details_origin__6Hu0I span"]
    ) or "United Arab Emirates"

    return {
        "product": title,
        "per_kg_price": per_kg_price,
        "country_of_origin": country,
        "url": url,
        "supermarket": SITE,
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
