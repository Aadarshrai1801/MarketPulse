"""
Union Coop via Scrapling - fast HTTP first, stealthy browser if needed,
verified product-URL fallback for hosted datacenter IPs.

The search listing is JS-rendered (static fetch returns the page shell with
no product links), so find_url() retries via the stealthy browser when the
fast pass yields nothing. Unioncoop's WAF also rejects search requests from
hosting datacenter IPs (HTTP 405 on Render) - for that case a table of
verified product URLs (checked live, resolved per product) is used, so the
hosted run succeeds with zero browser. Product pages themselves are
server-rendered (span.base, data-price-amount), so scrape() stays on plain
HTTP.
"""

import re
from datetime import datetime

from .config import SITE_SEARCH_CONFIG
from .utils import (
    fetch_fast,
    fetch_with_fallback,
    iter_links,
    rank_links,
    css_first_text,
    _page_text,
    format_per_kg,
)

SITE = "unioncoop"

# Verified product URLs (checked live 2026-09-03). Used ONLY when the live
# search is unreachable - Unioncoop's WAF answers search requests from
# hosting datacenter IPs (Render) with HTTP 405. Each URL is re-verified
# live (status 200 + title + price) before being returned, so a renamed /
# delisted product fails loudly instead of recording a stale price.
# Keys are lowercase aliases; lookup matches on token subsets, so
# "Watermelon saudi" and "navel orange" resolve correctly.
KNOWN_URLS = {
    "garlic": "https://www.unioncoop.ae/garlic-loose-china-462760.html",
    "cucumber": "https://www.unioncoop.ae/cucumber-loose-uae-463632.html",
    "potato": "https://www.unioncoop.ae/potato-1700602554-mdawmdawmtm4mziwmq-mdawmdawmtm4mziwmv8xnzawnjayntu0.html",
    "potatoes": "https://www.unioncoop.ae/potato-1700602554-mdawmdawmtm4mziwmq-mdawmdawmtm4mziwmv8xnzawnjayntu0.html",
    "red onion": "https://www.unioncoop.ae/onion-red-organic-1728425908-mdawmdawmtqxndy4na-mdawmdawmtqxndy4nf8xnzi4ndi1ota4.html",
    "onion red": "https://www.unioncoop.ae/onion-red-organic-1728425908-mdawmdawmtqxndy4na-mdawmdawmtqxndy4nf8xnzi4ndi1ota4.html",
    "tomato": "https://www.unioncoop.ae/tomato-2405057.html",
    "valencia orange": "https://www.unioncoop.ae/armela-orange-val-1kg-1765580684-mdy2otu2oda4ote3mq-mdy2otu2oda4ote3mv8xnzy1ntgwnjg0.html",
    "orange valencia": "https://www.unioncoop.ae/armela-orange-val-1kg-1765580684-mdy2otu2oda4ote3mq-mdy2otu2oda4ote3mv8xnzy1ntgwnjg0.html",
    "navel orange": "https://www.unioncoop.ae/orange-naval-cambria-1755379961-mdywnze-mdywnzffmtc1ntm3otk2mq.html",
    "orange navel": "https://www.unioncoop.ae/orange-naval-cambria-1755379961-mdywnze-mdywnzffmtc1ntm3otk2mq.html",
    "orange naval": "https://www.unioncoop.ae/orange-naval-cambria-1755379961-mdywnze-mdywnzffmtc1ntm3otk2mq.html",
    "watermelon": "https://www.unioncoop.ae/water-melon-2373097.html",
}

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

    # Stage 1+2: live search - fast HTTP, then the stealthy browser on
    # block/JS-shell (fetch_with_fallback handles both; on a slim host
    # without browsers it re-raises the fast error, e.g. HTTP 405).
    search_error = None
    try:
        page = fetch_with_fallback(search_url, mode=mode, wait_selector="a.result")
    except Exception as e:
        page = None
        search_error = e
    if page is not None:
        href, first_href = _collect_hrefs(page, config, product_name)
        found = href or first_href
        if found:
            return _resolve(found, config["base_url"])

    # Stage 3: verified known-URL fallback (no browser needed) - this is
    # what saves the hosted run when the WAF blocks datacenter search.
    known = _known_url_for(product_name)
    if known and _verify_known_url(known):
        return known

    if search_error is not None:
        raise RuntimeError(
            f"Unioncoop search blocked ({search_error}); "
            f"and no verified product URL for '{product_name}'."
        ) from None
    print(f"[unioncoop] 0 results for '{product_name}' - url: {search_url}")
    raise ValueError(f"Couldn't find '{product_name}' on {SITE}.")


def _norm_tokens(text):
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _known_url_for(product_name):
    """Best KNOWN_URLS entry whose alias tokens are all in the query
    (so 'Watermelon saudi' -> 'watermelon'); falls back to best token
    overlap. Returns None when nothing shares a token."""
    want = set(_norm_tokens(product_name))
    if not want:
        return None
    best, best_key = None, None
    for alias, url in KNOWN_URLS.items():
        alias_tokens = set(_norm_tokens(alias))
        if alias_tokens <= want:
            if best is None or len(alias_tokens) > len(_norm_tokens(best_key)):
                best, best_key = url, alias
    if best is not None:
        return best
    for alias, url in KNOWN_URLS.items():
        if want & set(_norm_tokens(alias)):
            return url
    return None


def _verify_known_url(url):
    """Live check: status 200 + a title + a price on the product page."""
    try:
        page = fetch_fast(url, timeout=30)
    except Exception:
        return False
    try:
        title = page.css("span.base::text").get()  # type: ignore[attr-defined]
        if not title or not str(title).strip():
            title = page.css("h1::text").get()  # type: ignore[attr-defined]
        if not title or not str(title).strip():
            return False
        amount = page.css("[data-price-amount]::attr(data-price-amount)").get()  # type: ignore[attr-defined]
        if amount and float(str(amount).strip()) > 0:
            return True
        body = _page_text(page, limit=20000)
        return bool(re.search(r"\d+\.\d{2}", body))
    except Exception:
        return False


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
            per_kg_price = format_per_kg(price)
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
                    per_kg_price = format_per_kg(price / weight)
            if per_kg_price is None:
                # Price but no detectable weight: report it as per-kg
                # (assumes ~1kg pack) so every row stays comparable.
                per_kg_price = format_per_kg(price)

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
