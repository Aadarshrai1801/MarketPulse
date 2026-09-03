"""
LuLu Hypermarket via Scrapling - Fetcher first, StealthyFetcher if blocked.

The old Playwright flow waited up to 150s for JS-rendered results. The
static search URL usually returns the same product cards in SSR HTML, so
plain HTTP works; fetch_mode "auto" keeps the browser strictly as a
blocked-page fallback (never on free-Render fast path unless needed).
"""

import re
from datetime import datetime
from urllib.parse import quote

from .config import SITE_SEARCH_CONFIG
from .utils import (
    fetch_with_fallback,
    iter_links,
    rank_links,
    css_first_text,
    css_all_text,
    _page_text,
    parse_weight_to_kg,
    parse_price_value,
)

SITE = "lulu"

KNOWN_COUNTRIES = [
    "India", "China", "Pakistan", "Egypt", "Iran", "Turkey", "Jordan", "Oman", "UAE",
    "United Arab Emirates", "Saudi Arabia", "South Africa", "Netherlands", "USA",
    "United States", "Australia", "New Zealand", "Spain", "Italy", "France", "Mexico",
    "Lebanon", "Syria", "Morocco", "Sri Lanka", "Thailand", "Vietnam", "Philippines"
]


def _resolve(href, base_url):
    href = (href or "").strip()
    if href.startswith("/"):
        href = base_url.rstrip("/") + href
    return href


def find_url(product_name):
    config = SITE_SEARCH_CONFIG[SITE]
    mode = config.get("fetch_mode", "auto")
    result_index = config.get("result_index", 0)

    query = quote(product_name.strip())
    search_url = config["search_url"].format(query=query)

    page = fetch_with_fallback(search_url, mode=mode)

    # Ranked like carrefour: prefer the closest keyword match (fewest extra
    # tokens) over whatever the site slots first.
    links = [(url, _text) for url, _text in iter_links(page, config["result_selector"])]
    hrefs = rank_links(links, product_name)
    # Fallback: raw attr list if element iteration found nothing.
    if not hrefs:
        try:
            hrefs = [
                h for h in page.css(f"{config['result_selector']}::attr(href)").getall()  # type: ignore[attr-defined]
                if h
            ]
        except Exception:
            hrefs = []

    href = hrefs[result_index] if len(hrefs) > result_index else None

    if not href:
        raise ValueError(f"Couldn't find '{product_name}' on {SITE}.")

    return _resolve(href, config["base_url"])


def scrape(url):
    config = SITE_SEARCH_CONFIG[SITE]
    mode = config.get("fetch_mode", "auto")
    page = fetch_with_fallback(url, mode=mode)
    body_text = _page_text(page)

    # PRODUCT NAME
    title = css_first_text(
        page,
        [
            "h1",
            "[data-testid='product-title']",
            "[data-testid='product-name']",
            "[class*='product-title']",
            "[class*='ProductTitle']",
        ],
    )

    # PRICE
    price = None
    price_value = None
    for raw in css_all_text(page, "[data-testid='price']"):
        try:
            price_value = float(re.search(r"\d+(?:\.\d+)?", raw).group())  # type: ignore[union-attr]
            price = f"AED {price_value:.2f}"
            break
        except Exception:
            continue
    if price_value is None:
        match = re.search(r"\d+\.\d{2}", body_text)
        if match:
            try:
                price_value = float(match.group())
                price = f"AED {price_value:.2f}"
            except ValueError:
                pass

    # UNIT / PACK SIZE + PER-KG PRICE (LuLu shows size in title, not AED/kg)
    value, unit = parse_weight_to_kg(title or "")
    if value is None:
        value, unit = parse_weight_to_kg(body_text[:10000])
    if price_value is None:
        price_value = parse_price_value(price)

    per_kg_price = None
    if value and price_value is not None and value > 0:
        if unit == "kg":
            per_kg_price = f"AED {price_value / value:.2f}/kg"
        elif unit == "l":
            per_kg_price = f"AED {price_value / value:.2f}/L"

    # COUNTRY OF ORIGIN
    country_of_origin = "United Arab Emirates"
    if title:
        lower_title = title.lower()
        for country in KNOWN_COUNTRIES:
            if country.lower() in lower_title:
                country_of_origin = country
                break

    return {
        "product": title,
        "per_kg_price": per_kg_price,
        "country_of_origin": country_of_origin,
        "url": url,
        "supermarket": "lulu",
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
