"""Scrape one or more retailers from THIS machine and push rows to MongoDB.

Primary use: scheduled cloud pushes (see .github/workflows/retailer-cloud-push.yml)
from an egress network each site allows. Also handy manually on any machine:

    python scripts/push_retailer.py --retailer unioncoop
    python scripts/push_retailer.py --retailer carrefour,lulu
    python scripts/push_retailer.py --retailer green     # daily cloud set
    python scripts/push_retailer.py --retailer all       # everything

Retailer sets:
  all    - every configured retailer (local/full runs).
  green  - the daily cloud set: retailers verified reachable from GitHub
           Actions egress (see .github/workflows/retailer-egress-probe.yml).
           Edit GREEN_RETAILERS below when probe results change.

Known stockouts (catalog.RETAILER_PRODUCT_SKIPS) are logged as SKIP, not
FAIL - a delisted product must not turn every scheduled run red.

Requires MONGODB_URI in the environment or a local .env (same as app.py).
Exits 0 when every attempted lookup succeeds (skips don't count against it),
1 on any failure, 2 on bad arguments - so a scheduler flags real problems.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import RETAILER_PRODUCT_SKIPS
from services.fetch import resolve_retailers, resolve_products, scrape_one

# Retailers the daily cloud job covers: reachable from Actions egress AND
# cheap enough to run every day. Unioncoop is excluded (Fastly 405s both
# Render AND Actions egress - residential IPs only). Update when the
# retailer-egress-probe results change.
GREEN_RETAILERS = ("carrefour", "lulu", "barakat", "kibsons")


def resolve_retailer_arg(value):
    """Expand 'all' / 'green' / comma-separated ids, preserving order."""
    value = (value or "all").strip().lower()
    if value == "all":
        return resolve_retailers("all")
    if value == "green":
        retailers = []
        for retailer_id in GREEN_RETAILERS:
            retailers.extend(resolve_retailers(retailer_id))
        return retailers
    retailers = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        found = resolve_retailers(part)
        if not found:
            return []
        retailers.extend(r for r in found if r not in retailers)
    return retailers


def main():
    parser = argparse.ArgumentParser(description="Scrape retailers and push rows to MongoDB.")
    parser.add_argument("--retailer", default="green",
                        help="Retailer id, comma-separated ids, 'green' (daily cloud set), or 'all'.")
    parser.add_argument("--product", default="all",
                        help="Product id or 'all' (default: all).")
    args = parser.parse_args()

    retailers = resolve_retailer_arg(args.retailer)
    products = resolve_products((args.product or "all").strip().lower())

    if not retailers:
        print(f"Unknown or disabled retailer selection '{args.retailer}'.")
        return 2
    if not products:
        print(f"Unknown product '{args.product}'.")
        return 2

    ok = skipped = failed = 0
    for product in products:
        for retailer in retailers:
            if (retailer, product["id"]) in RETAILER_PRODUCT_SKIPS:
                skipped += 1
                print(f"SKIP {product['id']} @ {retailer}: known stockout "
                      f"(see catalog.RETAILER_PRODUCT_SKIPS).")
                continue
            result = scrape_one(product, retailer)
            if result.get("ok"):
                ok += 1
                print(f"OK   {product['id']} @ {retailer}: "
                      f"{result.get('product')} | {result.get('per_kg_price')} | "
                      f"{result.get('country_of_origin')}")
            else:
                failed += 1
                print(f"FAIL {product['id']} @ {retailer}: {result.get('error')}")

    total = ok + skipped + failed
    print(f"\n{ok} ok, {skipped} skipped, {failed} failed / {total} attempted.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
