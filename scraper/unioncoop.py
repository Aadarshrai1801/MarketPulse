"""
Union Coop via Scrapling - fast HTTP first, StealthyFetcher only if needed.

The search listing is JS-rendered (static fetch returns the page shell with
no product links), so find_url() retries via the stealthy browser when the
fast pass yields nothing. Product pages themselves are server-rendered
(span.base, data-price-amount), so scrape() stays on plain HTTP.
"""

import re
from datetime import datetime

from .config import SITE_SEARCH_CONFIG
from .utils import (
    fetch_with_fallback,
    fetch_stealthy,
    iter_links,
    rank_links,
    css_first_text,
    _page_text,
)

SITE = "unioncoop"

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


def _collect_hrefs(page, config, product_name):
    """(best_match_href, first_href) across the known result selectors.

    Best match = closest keyword match (fewest extra tokens), so a whole
    "Red Onion" beats "Red Onion Slices" and ranking decides instead of
    whatever the site slots first.
    """
    first_href = None
    candidates = []
    for selector in (config["result_selector"], "a.product-item-link"):
        for url, text in iter_links(page, selector):
            if not url:
                continue
            if first_href is None:
                first_href = url
            candidates.append((url, text))
        if first_href is not None:
            continue
        # Fallback: raw attr list if element iteration found nothing.
        try:
            raw = page.css(f"{selector}::attr(href)").get()  # type: ignore[attr-defined]
        except Exception:
            raw = None
        if raw and first_href is None:
            first_href = raw
    ranked = rank_links(candidates, product_name)
    return (ranked[0] if ranked else None), first_href


def find_url(product_name):
    config = SITE_SEARCH_CONFIG[SITE]
    mode = config.get("fetch_mode", "auto")

    query = product_name.strip().replace(" ", "%20")
    search_url = config["search_url"].format(query=query)

    page = fetch_with_fallback(search_url, mode=mode)
    href, first_href = _collect_hrefs(page, config, product_name)

    if href is None and first_href is None and mode != "fast":
        # Static fetch returned the JS shell with no product links - render
        # the listing once via the stealthy browser (only path that uses it
        # for this retailer). On slim hosts without browsers this raises a
        # clear RuntimeError, surfaced per-row by app.py.
        page = fetch_stealthy(search_url)
        href, first_href = _collect_hrefs(page, config, product_name)

    href = href or first_href
    if not href:
        print(f"[unioncoop] 0 results for '{product_name}' - url: {search_url}")
        raise ValueError(f"Couldn't find '{product_name}' on {SITE}.")

    return _resolve(href, config["base_url"])


def scrape(url):
    config = SITE_SEARCH_CONFIG[SITE]
    mode = config.get("fetch_mode", "fast")
    page = fetch_with_fallback(url, mode=mode)
    body_text = _page_text(page)

    # ---------------------------------------------------------
    # PRODUCT NAME (Magento 2 uses span.base on product pages)
    # ---------------------------------------------------------
    title = css_first_text(page, ["span.base", "h1"])
    if not title:
        m = re.search(r"<title>(.*?)</title>", body_text, re.IGNORECASE | re.DOTALL)
        title = m.group(1).strip()[:200] if m else None

    # ---------------------------------------------------------
    # COUNTRY OF ORIGIN (parsed from title; Magento rarely exposes it)
    # ---------------------------------------------------------
    country_of_origin = "United Arab Emirates"
    original_title = title
    if original_title:
        lower_title = original_title.lower()
        for country in KNOWN_COUNTRIES:
            if country.lower() in lower_title:
                country_of_origin = country
                break
        for country in KNOWN_COUNTRIES:
            title = re.sub(
                rf"\s*-\s*{re.escape(country)}", "", title,
                flags=re.IGNORECASE,
            )
        title = re.sub(
            r"\s*-\s*\d+(?:\.\d+)?\s*(kg|g)\b", "", title,
            flags=re.IGNORECASE,
        ).strip()

    # ---------------------------------------------------------
    # PER-KG PRICE (Magento price-box + data-price-amount)
    # ---------------------------------------------------------
    per_kg_price = None
    try:
        box_texts = page.css("div.price-box.price-final_price ::text").getall()  # type: ignore[attr-defined]
        box_text = " ".join(t.strip() for t in (box_texts or []) if t.strip())
    except Exception:
        box_text = ""
    if not box_text:
        box_text = body_text[:5000]

    price = None
    try:
        amount = page.css("div.price-box [data-price-amount]::attr(data-price-amount)").get()  # type: ignore[attr-defined]
        if amount:
            price = float(str(amount).strip())
    except Exception:
        price = None
    if price is None:
        m = re.search(r"([\d]+\.[\d]{2})", box_text)
        if m:
            try:
                price = float(m.group(1))
            except ValueError:
                price = None

    if price is not None:
        if re.search(r"/\s*kg\b", box_text, re.IGNORECASE):
            per_kg_price = f"AED {price:.2f}/kg"
        else:
            weight_match = re.search(
                r"/\s*(\d+(?:\.\d+)?)\s*(kg|g)\b", box_text, re.IGNORECASE,
            )
            if not weight_match and original_title:
                weight_match = re.search(
                    r"(\d+(?:\.\d+)?)\s*(kg|g)\b", original_title, re.IGNORECASE,
                )
            if weight_match:
                weight = float(weight_match.group(1))
                if weight_match.group(2).lower() == "g":
                    weight /= 1000
                if weight > 0:
                    per_kg_price = f"AED {price / weight:.2f}/kg"
            if per_kg_price is None:
                per_kg_price = f"AED {price:.2f}/kg"

    if per_kg_price is None:
        print(f"[unioncoop] Price Error: no price found at {url}")

    return {
        "product": title,
        "per_kg_price": per_kg_price,
        "country_of_origin": country_of_origin,
        "url": url,
        "supermarket": "unioncoop",
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
