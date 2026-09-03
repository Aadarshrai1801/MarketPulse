"""
Small helpers shared by more than one retailer module. Nothing here is
retailer-specific - if it only applies to one site, it belongs in that
site's own file instead.

Free-Render design: Scrapling ``Fetcher`` (plain HTTP + TLS impersonation)
is the default and needs no browser binary. ``StealthyFetcher`` (headless
Chromium that solves Cloudflare Turnstile) is used ONLY as a fallback for
retailers whose ``fetch_mode`` is "auto"/"stealthy" AND whose fast request
looks blocked. On a slim image without ``scrapling install`` browsers, the
stealthy path raises a clear RuntimeError instead of crashing the job -
app.py turns that into an ``ok: False`` row.
"""

import re

STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

BROWSER_IMPERSONATE = "chrome"
FAST_TIMEOUT = 30
STEALTHY_TIMEOUT = 60

# Markers that almost always mean "bot wall / challenge page" rather than a
# real search/product page, even when HTTP status is 200.
_BLOCK_MARKERS = (
    "just a moment",
    "attention required",
    "verify you are human",
    "cloudflare",
    "datadome",
    "perimeterx",
    "press & hold",
)


def _page_text(page, limit=200000):
    """Best-effort visible text of a Scrapling Response/Selector."""
    # 1. Raw body decode (fastest, always available as bytes on Response).
    try:
        body = getattr(page, "body", b"")
        if isinstance(body, (bytes, bytearray)) and body:
            return bytes(body).decode(
                getattr(page, "encoding", None) or "utf-8", errors="ignore"
            )[:limit]
    except Exception:
        pass
    # 2. Selector text extraction fallback.
    try:
        parts = page.css("body ::text").getall()  # type: ignore[attr-defined]
        if parts:
            return " ".join(p.strip() for p in parts if p and p.strip())[:limit]
    except Exception:
        pass
    try:
        return str(page)[:limit]
    except Exception:
        return ""


def _page_title(page):
    try:
        title = page.css("title::text").get()  # type: ignore[attr-defined]
        return (title or "").strip()
    except Exception:
        return ""


def is_blocked_page(page):
    """Heuristic: True when the fetched page looks like a bot wall."""
    try:
        status = int(getattr(page, "status", 200) or 200)
    except Exception:
        status = 200
    if status in (403, 405, 429, 503):
        return True
    haystack = f"{_page_title(page)} {_page_text(page, limit=5000)}".lower()
    if len(haystack.strip()) < 200:
        # Real search/product pages are never this small; challenge stubs are.
        return True
    return any(marker in haystack for marker in _BLOCK_MARKERS)


def fetch_fast(url, timeout=FAST_TIMEOUT):
    """One Scrapling ``Fetcher`` GET with Chrome TLS impersonation.

    Needs only ``pip install "scrapling[fetchers]"`` - no browser binary,
    so it runs on Render free (512MB RAM).
    """
    from scrapling.fetchers import Fetcher

    page = Fetcher.get(
        url,
        impersonate=BROWSER_IMPERSONATE,
        stealthy_headers=True,
        timeout=timeout,
    )
    status = int(getattr(page, "status", 200) or 200)
    if status >= 400:
        raise RuntimeError(f"Fast fetch failed for {url}: HTTP {status}")
    return page


def fetch_stealthy(url, timeout=STEALTHY_TIMEOUT, wait_selector=None):
    """One Scrapling ``StealthyFetcher`` fetch (headless Chromium).

    ONLY called when fast HTTP can't do the job (JS-rendered listing with
    no usable API) and the retailer's ``fetch_mode`` allows it. Requires
    browsers from ``scrapling install``; on a slim free-Render image
    without them this raises a clear RuntimeError (caught per-row by
    app.py) instead of hanging.

    Speed settings matter: ``network_idle`` is deliberately OFF (analytics
    beacons poll forever and it would stall for the full timeout) -
    pass ``wait_selector`` so the fetch returns as soon as the product
    links attach, with trackers/images dropped for a further boost.
    """
    try:
        from scrapling.fetchers import StealthyFetcher
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"StealthyFetcher unavailable (import failed): {e}")

    kwargs = dict(
        headless=True,
        solve_cloudflare=True,
        timeout=timeout * 1000,
        disable_resources=True,
        block_ads=True,
    )
    if wait_selector:
        kwargs["wait_selector"] = wait_selector
    try:
        page = StealthyFetcher.fetch(url, **kwargs)
    except Exception as e:
        msg = str(e)
        if "browser" in msg.lower() or "executable" in msg.lower() or "not found" in msg.lower():
            raise RuntimeError(
                "Stealthy browser not installed on this host "
                "(skip `scrapling install` for slim free-Render image). "
                f"Original error: {e}"
            )
        raise RuntimeError(f"Stealthy fetch failed for {url}: {e}")
    return page


def fetch_with_fallback(url, mode="auto", timeout=FAST_TIMEOUT, wait_selector=None):
    """Fetch ``url`` with Fetcher, falling back to StealthyFetcher if needed.

    mode: "fast" (never touch a browser), "stealthy" (go straight to the
    browser), "auto" (try fast, use browser only when blocked).

    In "auto" the fast stage is wrapped so that *any* fast failure -
    an HTTP error (403/405/... from a datacenter IP) or a 200 page that
    looks like a bot wall / JS shell - retries once via the stealthy
    browser. If the stealthy path is unavailable (slim image without
    browsers) the ORIGINAL fast error is re-raised, since that is the
    actionable diagnostic on Render free ("search blocked, HTTP 405"),
    not "browser not installed".
    """
    if mode == "stealthy":
        return fetch_stealthy(url, timeout=STEALTHY_TIMEOUT, wait_selector=wait_selector)
    if mode == "fast":
        return fetch_fast(url, timeout=timeout)
    # auto
    fast_error = None
    page = None
    try:
        page = fetch_fast(url, timeout=timeout)
    except Exception as e:
        fast_error = e
    if page is not None and not is_blocked_page(page):
        return page
    # Fast failed outright, or returned a block/JS shell: try the browser once.
    try:
        return fetch_stealthy(url, wait_selector=wait_selector)
    except RuntimeError as stealth_error:
        # No browsers on this host (Render free slim image): surface the
        # fast error when there is one - it tells the operator what the
        # site actually did (e.g. HTTP 405 for datacenter IPs).
        if fast_error is not None:
            raise fast_error from None
        raise


def iter_links(page, selector):
    """Yield (href, text) for each element matching ``selector``.

    Tolerant of Scrapling/Parsel API differences across versions.
    """
    try:
        elements = page.css(selector)  # type: ignore[attr-defined]
    except Exception:
        return
    if elements is None:
        return
    try:
        count = len(elements)
    except Exception:
        try:
            count = elements.length  # type: ignore[attr-defined]
        except Exception:
            return
    for i in range(count or 0):
        try:
            el = elements[i]
        except Exception:
            continue
        try:
            href = (el.attrib.get("href") if hasattr(el, "attrib") else None) or ""
        except Exception:
            href = ""
        if not href:
            try:
                href = (el.css("::attr(href)").get() or "")  # type: ignore[attr-defined]
            except Exception:
                href = ""
        try:
            texts = el.css("::text").getall()  # type: ignore[attr-defined]
            text = " ".join(t.strip() for t in (texts or []) if t and t.strip())
        except Exception:
            try:
                text = (getattr(el, "text", "") or "").strip()
            except Exception:
                text = ""
        yield href.strip(), text.strip().lower()


def _word_tokens(text):
    import re as _re

    return [t for t in _re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _stem_token(token):
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def rank_links(links, keyword):
    """Rank (href, text) pairs against a search keyword.

    Returns hrefs ordered by keyword-tokens-matched (desc) then extra
    tokens (asc), so "Red Onion - UAE" beats "Red Onion Slices" and an
    unrelated first result (e.g. a sponsored raspberry for a watermelon
    search) never wins. Links matching zero keyword tokens are dropped.

    Processed variants (sliced/paste/juice/...) are demoted as a tie-break
    so whole produce beats "Red Onion Slices" when both match equally -
    but match count still dominates, so an explicit "Ginger Garlic" style
    keyword keeps its intended product.
    """
    key_tokens = [_stem_token(t) for t in _word_tokens(keyword)]
    if not key_tokens:
        return []
    key_set = set(key_tokens)
    processed = {
        _stem_token(w) for w in (
            "slice", "slices", "sliced", "paste", "juice", "juices", "powder",
            "dice", "diced", "chop", "chopped", "mince", "minced", "puree",
            "pureed", "dry", "dried", "frozen", "pickle", "pickled",
        )
    }
    ranked = []
    for href, text in links:
        if not href:
            continue
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        cand_tokens = [_stem_token(t) for t in _word_tokens(slug + " " + (text or ""))]
        cand_set = set(cand_tokens)
        matched = len(key_set & cand_set)
        if matched == 0:
            continue
        # Numeric IDs, prices and hash-like slugs ("13513435",
        # "mdk1ndk...") carry no product meaning - ignore them so a
        # listing with more junk tokens doesn't lose to a worse product.
        meaningful = {
            t for t in cand_set
            if not t.isdigit() and len(t) <= 15
        }
        extra = len(meaningful - key_set)
        extra += sum(2 for t in cand_set & processed)
        ranked.append((matched, extra, href))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [href for _, _, href in ranked]


def css_first_text(page, selectors):
    """First non-empty ``::text`` across a list of CSS selectors."""
    for selector in selectors:
        try:
            value = page.css(f"{selector}::text").get()  # type: ignore[attr-defined]
        except Exception:
            continue
        if value and str(value).strip():
            return str(value).strip()
    return None


def css_all_text(page, selector):
    """All ``::text`` values for one selector, stripped."""
    try:
        values = page.css(f"{selector}::text").getall()  # type: ignore[attr-defined]
    except Exception:
        return []
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def parse_weight_to_kg(text):
    """
    Returns:
        (value, unit_type)

    unit_type is:
        "kg" -> value is in kilograms
        "l"  -> value is in litres

    Examples:
        "500g"          -> (0.5, "kg")
        "2 kg"          -> (2.0, "kg")
        "400ml"         -> (0.4, "l")
        "1.5L"          -> (1.5, "l")
        "2 x 400 ml"    -> (0.8, "l")
    """

    if not text:
        return None, None

    text = str(text)

    # -------------------------
    # Multipack
    # 2 x 400 ml
    # 3x250g
    # -------------------------

    multi = re.search(
        r'(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(kg|g|gm|gram|grams|l|lt|ltr|litre|liter|litres|liters|ml)\b',
        text,
        re.IGNORECASE
    )

    if multi:

        count = int(multi.group(1))
        value = float(multi.group(2))
        unit = multi.group(3).lower()

        total = count * value

    else:

        single = re.search(
            r'(\d+(?:\.\d+)?)\s*(kg|g|gm|gram|grams|l|lt|ltr|litre|liter|litres|liters|ml)\b',
            text,
            re.IGNORECASE
        )

        if not single:
            return None, None

        total = float(single.group(1))
        unit = single.group(2).lower()

    # -------------------------
    # Weight
    # -------------------------

    if unit == "kg":
        return total, "kg"

    if unit in ("g", "gm", "gram", "grams"):
        return total / 1000, "kg"

    # -------------------------
    # Volume
    # -------------------------

    if unit in ("l", "lt", "ltr", "litre", "liter", "litres", "liters"):
        return total, "l"

    if unit == "ml":
        return total / 1000, "l"

    return None, None


def parse_price_value(price_text):
    """Pull the numeric amount out of a price string like 'AED 3.99' -> 3.99."""
    if not price_text:
        return None
    match = re.search(r'\d+(?:\.\d+)?', price_text)
    return float(match.group()) if match else None


def format_per_kg(value):
    """Single canonical price format used everywhere: 'AED 12.95/kg'."""
    return f"AED {float(value):.2f}/kg"


def compute_per_kg(price_value, weight_kg):
    """Normalize any (price, pack-weight) pair into the canonical per-kg string.

    - weight_kg > 0 -> true per-kg value (price / weight).
    - weight unknown -> the price itself, formatted as /kg. This assumes a
      ~1kg pack and is only approximate, but keeps the invariant the
      dashboard relies on: every successful row carries a per-kg price.
      (Liquids use the same 1L ~= 1kg approximation as Carrefour's pack
      parser, so volume units also land here instead of a separate /L.)
    - price_value None -> None (no price found at all).
    """
    if price_value is None:
        return None
    try:
        price_value = float(price_value)
    except (TypeError, ValueError):
        return None
    try:
        weight_kg = float(weight_kg) if weight_kg else 0.0
    except (TypeError, ValueError):
        weight_kg = 0.0
    if weight_kg > 0:
        return format_per_kg(price_value / weight_kg)
    return format_per_kg(price_value)
