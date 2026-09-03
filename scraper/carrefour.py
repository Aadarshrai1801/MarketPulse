"""
Carrefour UAE via Scrapling - Fetcher first, StealthyFetcher if blocked.

The old Playwright flow waited ~40s for JS + probed split-span price DOM.
The same price/per-kg/pack-size strings exist in SSR HTML/body text, so
static fetch + regex fallbacks reproduce it without Chromium. fetch_mode is
"auto": browser only when the fast page looks like a bot wall.
"""

import re
from datetime import datetime

from .config import SITE_SEARCH_CONFIG
from .utils import (
    fetch_with_fallback,
    iter_links,
    rank_links,
    css_first_text,
    css_all_text,
    _page_text,
)

SITE = "carrefour"


def _parse_pack_size_to_kg(text):
    """
    Parses a pack-size string such as '500g', '1kg', '1.5 kg', '6x200g',
    '750ml', or '1L' into an equivalent weight in kilograms.

    Liquids (l/ml) are treated as 1:1 with kg (density ~1, e.g. water/milk) -
    this is an approximation and will be off for dense or light liquids
    (oil, honey, etc.), so treat converted liquid per-kg prices as indicative.

    Returns None if the text can't be confidently parsed.
    """
    if not text:
        return None

    cleaned = text.strip().lower().replace(' ', '')

    multipack_match = re.match(r'^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(kg|g|l|ml)$', cleaned)
    if multipack_match:
        count_str, amount_str, unit = multipack_match.groups()
        total = float(count_str) * float(amount_str)
    else:
        single_match = re.match(r'^(\d+(?:\.\d+)?)(kg|g|l|ml)$', cleaned)
        if not single_match:
            return None
        amount_str, unit = single_match.groups()
        total = float(amount_str)

    if unit == 'kg':
        return total
    if unit == 'g':
        return total / 1000
    if unit == 'l':
        return total  # approx: 1L ~= 1kg
    if unit == 'ml':
        return total / 1000
    return None


def _resolve(href, base_url):
    href = (href or "").strip()
    if href.startswith("/"):
        href = base_url.rstrip("/") + href
    return href


def find_url(product_name):
    config = SITE_SEARCH_CONFIG[SITE]
    mode = config.get("fetch_mode", "auto")
    result_index = config.get("result_index", 0)

    query = product_name.strip().replace(" ", "%20")
    search_url = config["search_url"].format(query=query)

    page = fetch_with_fallback(search_url, mode=mode)

    # Ranked: an unrelated first result (e.g. a sponsored slot) must not
    # win over a real keyword match further down the list.
    links = [(url, text) for url, text in iter_links(page, config["result_selector"])]
    ranked = rank_links(links, product_name)
    if not ranked:
        try:
            ranked = [
                h for h in page.css(f"{config['result_selector']}::attr(href)").getall()  # type: ignore[attr-defined]
                if h
            ]
        except Exception:
            ranked = []

    href = ranked[result_index] if len(ranked) > result_index else None

    if not href:
        raise ValueError(f"Couldn't find '{product_name}' on {SITE}.")

    return _resolve(href, config["base_url"])


def scrape(url):
    config = SITE_SEARCH_CONFIG[SITE]
    mode = config.get("fetch_mode", "auto")
    page = fetch_with_fallback(url, mode=mode)
    body_text = _page_text(page)

    # Note: the title sits nested (<h1><span>Tomato</span></h1>), so plain
    # h1::text (direct children only) comes back empty - read descendants.
    title = css_first_text(page, ["h1 span", "h1"])

    # ---- price extraction (same split-span DOM, read statically) ----
    main_price = None
    try:
        parts = [
            part.strip()
            for part in page.css("div.flex.items-baseline.force-ltr div::text").getall()  # type: ignore[attr-defined]
            if part.strip()
        ]
        if len(parts) >= 3:
            currency, integer_part = parts[0], parts[1]
            decimal_part = parts[2].lstrip('.')
            if integer_part and decimal_part:
                main_price = f"{currency} {integer_part}.{decimal_part}"
    except Exception:
        main_price = None
    if not main_price:
        prices_found = re.findall(r'AED\s?[\d]+\.\d{2}', body_text)
        main_price = prices_found[0] if prices_found else None

    # ---- per-kg price: prefer Carrefour's own explicit value ----
    per_kg_price = None
    for candidate in css_all_text(page, "div.text-gray-600"):
        match = re.search(r'AED\s?([\d]+\.\d{2})\s?/\s?(kg|g|l|ml)', candidate, re.IGNORECASE)
        if match:
            value, unit = float(match.group(1)), match.group(2).lower()
            if unit in ('kg', 'l'):
                per_kg_price = f"AED {value:.2f}"
            elif unit in ('g', 'ml'):
                per_kg_price = f"AED {value * 1000:.2f}"
            else:
                per_kg_price = main_price
            break
    if not per_kg_price:
        explicit_match = re.search(r'AED\s?([\d]+\.\d{2})\s?/\s?(kg|g|l|ml)', body_text, re.IGNORECASE)
        if explicit_match:
            value, unit = float(explicit_match.group(1)), explicit_match.group(2).lower()
            if unit in ('kg', 'l'):
                per_kg_price = f"AED {value:.2f}"
            elif unit in ('g', 'ml'):
                per_kg_price = f"AED {value * 1000:.2f}"

    # ---- pack size fallback (only needed if no explicit per-kg price) ----
    pack_size_raw = None
    if not per_kg_price:
        try:
            # Static equivalent of "find Pack Size label, read bold value".
            pack_size_raw = page.xpath(  # type: ignore[attr-defined]
                "//span[normalize-space()='Pack Size']/following::span[contains(@class,'font-bold')][1]/text()"
            ).get()
            if pack_size_raw:
                pack_size_raw = str(pack_size_raw).strip() or None
        except Exception:
            pack_size_raw = None
        if not pack_size_raw:
            pack_match = re.search(
                r'Pack Size\s*\n?\s*([\d.]+\s?(?:kg|g|l|ml)|[\d.]+\s?x\s?[\d.]+\s?(?:kg|g|l|ml))',
                body_text, re.IGNORECASE,
            )
            pack_size_raw = pack_match.group(1).strip() if pack_match else None

        pack_size_kg = _parse_pack_size_to_kg(pack_size_raw) if pack_size_raw else None
        price_value_match = re.search(r'[\d]+\.\d{2}', main_price) if main_price else None
        if price_value_match and pack_size_kg and pack_size_kg > 0:
            per_kg_price = f"AED {float(price_value_match.group()) / pack_size_kg:.2f}"
        elif not per_kg_price:
            per_kg_price = main_price

    # ---- country of origin ----
    country_of_origin = None
    try:
        alts = page.css('img[src*="countryimages/"]::attr(alt)').getall()  # type: ignore[attr-defined]
        for alt in alts:
            if alt and str(alt).strip() and str(alt).strip().lower() != "flag":
                country_of_origin = str(alt).strip()
                break
    except Exception:
        country_of_origin = None
    if not country_of_origin:
        origin_match = re.search(r'Origin\s*\n\s*([A-Za-z\s]+?)\s*\n', body_text)
        country_of_origin = origin_match.group(1).strip() if origin_match else "UAE"

    return {
        "product": title,
        "price": main_price,
        "pack_size": pack_size_raw,
        "per_kg_price": per_kg_price,
        "country_of_origin": country_of_origin,
        "url": url,
        "supermarket": "carrefour",
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
