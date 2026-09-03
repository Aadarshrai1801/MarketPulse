"""Push fresh prices for one retailer from THIS machine into MongoDB.

The use case is an IP-blocked retailer: Unioncoop's Fastly edge 405s every
request from hosting datacenter IPs (Render), but answers a residential IP
fine. So the hosted app sets DISABLED_RETAILERS=unioncoop and never attempts
it there - instead, this script runs on a machine whose egress is allowed
(e.g. your home PC) and upserts the exact same records (price_history +
latest_fetch) the hosted fetch would have written. The dashboard reads from
MongoDB, so Unioncoop rows show up there with no code or proxy changes.

Usage (from the repo root, home PC):
    python scripts/push_retailer.py --retailer unioncoop
    python scripts/push_retailer.py --retailer unioncoop --product tomato

Schedule it with Windows Task Scheduler, e.g. daily at 08:00:
    schtasks /create /tn "MarketPulse Unioncoop Push" /sc daily /st 08:00 ^
      /tr "'C:\\Users\\Aadarsh\\miniconda3\\envs\\marketpulse\\python.exe' ^
           'C:\\Users\\Aadarsh\\Desktop\\MarketPulse 2.0\\scripts\\push_retailer.py --retailer unioncoop'"

Requires MONGODB_URI in the environment or a local .env (same as app.py).
Exits 0 only when every lookup succeeds (so the scheduler flags partial runs).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.fetch import resolve_retailers, resolve_products, scrape_one


def main():
    parser = argparse.ArgumentParser(description="Scrape one retailer locally and push to MongoDB.")
    parser.add_argument("--retailer", default="unioncoop",
                        help="Retailer id (default: unioncoop).")
    parser.add_argument("--product", default="all",
                        help="Product id or 'all' (default: all).")
    args = parser.parse_args()

    retailers = resolve_retailers((args.retailer or "all").strip().lower())
    products = resolve_products((args.product or "all").strip().lower())

    if not retailers:
        print(f"Unknown or disabled retailer '{args.retailer}'.")
        return 2
    if not products:
        print(f"Unknown product '{args.product}'.")
        return 2

    ok = failed = 0
    for product in products:
        for retailer in retailers:
            result = scrape_one(product, retailer)
            if result.get("ok"):
                ok += 1
                print(f"OK   {product['id']} @ {retailer}: "
                      f"{result.get('product')} | {result.get('per_kg_price')} | "
                      f"{result.get('country_of_origin')}")
            else:
                failed += 1
                print(f"FAIL {product['id']} @ {retailer}: {result.get('error')}")

    print(f"\n{ok}/{ok + failed} rows pushed to MongoDB.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
