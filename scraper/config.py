"""
Per-retailer search configuration.

- "search_url" must contain {query} where the URL-encoded search term goes.
- "result_selector" is a CSS selector matching the <a> tag (or something
  wrapping one) that links to a single product on the search results page.
- "result_index" (optional) picks which matching result to use, in case
  the first one usually isn't the real product (e.g. an ad slot).
  Defaults to 0.
- "base_url" is prefixed onto relative hrefs ("/p/12345") so callers
  always get a full URL back.

To point a retailer's scraper at a different result on the page, or fix a
broken lookup, this is the file to edit - none of the individual
`scraper/<retailer>.py` files hardcode these values, they all read from
here.

NOTE: barakat, kibsons, and unioncoop selectors below are best-effort
guesses based on each site's URL and product-page conventions - they
haven't been confirmed against the live, JS-rendered search results
pages. Carrefour and Lulu are confirmed working. If a lookup fails or
grabs the wrong link for those three, open that site's search results in
a real browser, right-click a product result -> Inspect, and update
"result_selector" (and "search_url" if the query parameter name differs)
to match what you actually see in the DOM.
"""

SITE_SEARCH_CONFIG = {
    "carrefour": {
        "base_url": "https://www.carrefouruae.com",
        "search_url": "https://www.carrefouruae.com/mafuae/en/search?keyword={query}",
        "result_selector": "a[href*='/p/']",
        # JS-heavy search; try fast HTTP first, use StealthyFetcher only if blocked.
        "fetch_mode": "auto",
    },
    "lulu": {
        "search_url": "https://gcc.luluhypermarket.com/en-ae/list/?search_text={query}",
        "result_selector": "a[href*='/p/']",
        "base_url": "https://gcc.luluhypermarket.com",
        "result_index": 0,
        "fetch_mode": "auto",
    },
    "barakat": {
        "search_url": "https://barakatfresh.ae/search?key={query}",
        "result_selector": "a[href$='.html']",
        "base_url": "https://barakatfresh.ae",
        # Slug-guess + static product pages work fine over plain HTTP.
        "fetch_mode": "fast",
    },
    "kibsons": {
        "search_url": "https://www.kibsons.com/en/search/{query}",
        "result_selector": "a[href*='/product/']",
        "base_url": "https://www.kibsons.com",
        "fetch_mode": "auto",
    },
    "unioncoop": {
        "search_url": "https://www.unioncoop.ae/catalogsearch/result/?q={query}",
        "result_selector": "a.result",
        "base_url": "https://www.unioncoop.ae",
        # Search listing is JS-rendered: try plain HTTP first, then the
        # stealthy browser only when it yields no product links.
        # Product pages themselves are server-rendered (span.base,
        # data-price-amount), so scrape() stays on plain HTTP.
        "fetch_mode": "auto",
    },
}
