# MARKETPULSE — UAE Fresh Produce

A dashboard around your Playwright scraper: a Retailer dropdown and a
Product Filter dropdown (both default to "All"), a **Fetch latest
prices** button, and a table with retailers/products down the side and
dates across the top — a running price-history matrix in AED/kg, built
from every fetch you run. Includes login, role-based access (viewer /
editor / admin), and optional "Continue with Google" sign-in.

## Files

- `app.py` — thin Flask web layer: app setup, page + API routes only.
  Business logic lives in `services/`, storage in `database/`.
- `services/` — backend logic with no HTTP in it:
  - `fetch.py` — `resolve_retailers` / `resolve_products` /
    `scrape_one` (one product × retailer lookup + persist).
  - `jobs.py` — in-memory `JOBS` store + `run_fetch_job`
    (thread-pool fan-out). *(See Deployment notes if you run more
    than one worker.)*
- `scraper/` — one file per retailer so you can fix or
  extend one site without touching the others:
  - `config.py` — `SITE_SEARCH_CONFIG` plus per-retailer `fetch_mode`
    (`"fast"` = plain HTTP only, `"auto"` = HTTP first with a stealthy
    browser fallback if blocked). Edit search URLs/selectors here.
  - `utils.py` — shared helpers: `fetch_with_fallback` (Scrapling
    `Fetcher` by default, `StealthyFetcher` only when needed),
    `parse_weight_to_kg`, `parse_price_value`, `compute_per_kg`.
  - `retailers/carrefour.py`, `retailers/lulu.py`,
    `retailers/barakat.py`, `retailers/kibsons.py`,
    `retailers/unioncoop.py` — each has just two functions:
    `find_url(product_name)` and `scrape(url)`.
  - `__init__.py` — the only file anything outside `scraper/` imports
    from. Re-exports `SITE_SEARCH_CONFIG`, `find_product_url`,
    `get_product_details` (storage comes from `database/`).
  - **Adding a 6th retailer:** add its config to `config.py`, create
    `scraper/retailers/newsite.py` with `find_url()`/`scrape()`, then
    register it in the two dicts at the top of `scraper/__init__.py`.
- `database/` — all MongoDB access in one place:
  - `connection.py` — the single shared client (`get_db()`), plus
    `get_status()` (used by the admin "Database Settings" panel) and
    `ensure_indexes()` (called on every startup).
  - `prices.py` — `save_price_record` / `get_price_history` /
    `save_latest_fetch` / `get_latest_fetch`, including the "update
    today's record instead of duplicating it" logic (upsert on
    product+retailer+date).
- `catalog/` — the canonical product catalog (`PRODUCTS`,
  `RETAILER_LABELS`, `get_search_keyword`). The frontend dropdown always
  shows/uses `name` (e.g. "Red Onion") — the **same keyword for every
  retailer**. If a specific retailer's search only matches a different
  term, add a one-line override in that product's `keywords` dict; the
  frontend never sees it, only the scraper does. User-added products
  persist in the `products` collection via `add_custom_product` /
  `delete_custom_product`.
- `auth/` — session-based login, MongoDB-backed users (`users`
  collection), three roles (viewer / editor / admin), optional Google
  OAuth. Also seeds a default admin account on first run (see
  **Environment variables** below — set these before your first deploy).
- `ocr/` — receipt / price-list image scanning (`ocr.py`, PaddleOCR —
  optional on slim hosts). `scripts/ocr_demo.py` demos the pipeline on
  a synthesized sheet.
- `scripts/migrate_to_mongo.py` — one-time script that copies your
  existing `users.db` and `products.xlsx` into MongoDB. Safe to re-run.
- `scripts/push_retailer.py` — scrape retailers and push the rows
  into MongoDB. `--retailer` accepts one id, a comma-separated list,
  `green` (the daily cloud set: carrefour/lulu/barakat/kibsons —
  everything verified reachable from Actions egress), or `all`.
  Exits 0 unless a lookup FAILs; known stockouts
  (`catalog.RETAILER_PRODUCT_SKIPS`, e.g. Barakat navel oranges,
  delisted site-wide) log as SKIP and don't fail the run. The
  `Retailer cloud push` GitHub Action
  (`.github/workflows/retailer-cloud-push.yml`) runs `--retailer green`
  daily at 01:00 UTC (05:00 GST) — one ~5 min run, ~1–2% of the free
  Actions allowance. Manual "Run workflow" offers the same choices for
  ad-hoc pushes. Needs `MONGODB_URI` / `MONGO_DB_NAME` repo secrets
  matching Render's values.
- `.github/workflows/retailer-egress-probe.yml` — one-shot probe of
  every retailer from a fresh Actions runner; the log's HTTP codes tell
  you which retailers are cloud-friendly. Run this once before relying
  on the cloud push. Results 2026-09-03: carrefour/lulu/barakat/kibsons
  200, unioncoop 405 (blocked on Render AND Actions egress).
- `.github/workflows/unioncoop-egress-probe.yml` — original narrower
  Unioncoop-only probe (kept for reference).
- `app.py` — Flask backend (routes only):
  - `GET /api/meta` — retailer + product lists for the dropdowns.
  - `POST /api/fetch` — **synchronous.** Runs the scraper for the
    selected retailer(s) × product(s) and blocks until done. Fine for a
    single retailer/product (a few seconds); avoid it for "all"/"all"
    since the request would sit open for several minutes.
  - `POST /api/jobs` — **asynchronous.** Same inputs as `/api/fetch`,
    returns immediately with a `job_id` while scraping runs in a
    background thread. Use this for bigger requests. *(In-memory job
    store — see Deployment notes if you run more than one worker.)*
  - `GET /api/jobs/<job_id>` — poll for
    `{status: "running"|"done", completed, total, results}`.
  - `GET /api/jobs` — list recent jobs (without full results).
  - `GET /api/history` — reads the `price_history` MongoDB collection,
    pivots it into `{dates, rows}`, filtered by whatever's selected in
    the dropdowns.
  - `POST /api/ocr` — accepts an uploaded receipt/price-list image and
    returns structured rows via the local `ocr/` module.
  - `/api/users*` — admin-only user management (create, change role,
    deactivate, reset password, delete).
  - `GET /api/admin/db/status` — admin-only MongoDB connection health
    check (used by the "Database Settings" panel).
- `templates/index.html` — the dashboard page. "Fetch latest prices"
  uses the async job API under the hood (submits a job, polls every
  ~1.2s, shows live "X/Y" progress). Admins also get a "Database
  Settings" panel from the user menu showing live connection status.
- `templates/login.html` — sign-in page (username/password + optional
  Google button).

## Local setup

```bash
python -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate

pip install -r requirements.txt
# No browser install needed - scraping uses Scrapling's plain-HTTP Fetcher.
```

## Environment variables

None of these are hardcoded — set them in your shell (local) or your
hosting platform's config/secrets panel (production). **Do this before
the first run on any server other machines can reach.**

| Variable | Required? | Purpose |
|---|---|---|
| `MONGODB_URI` | **Yes** | Full MongoDB connection string, e.g. `mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority`. Get it from Atlas: Database → Connect → Drivers → Python. Without it, the app raises a clear error on startup instead of coming up half-working. |
| `MONGO_DB_NAME` | Optional | Database name inside your cluster to use. Defaults to `intellicrop`. |
| `SECRET_KEY` | **Yes, in production** | Signs session cookies. Without it, a random key is generated and cached to a local `secret.key` file — fine on a machine with persistent disk, but if your host's filesystem is ephemeral (most PaaS/containers), a new key is generated on every restart and **all users get logged out**. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Recommended | Sets the first admin account's credentials on first run (instead of the `admin` / `admin123` default). Set these *before* the first request ever hits the app in production. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | Optional | Enables the "Continue with Google" button. Create credentials at the [Google Cloud Console](https://console.cloud.google.com/apis/credentials); `GOOGLE_REDIRECT_URI` must exactly match an authorized redirect URI there, e.g. `https://yourdomain.com/auth/google/callback`. |
| `GOOGLE_DEFAULT_ROLE` | Optional | Role assigned to new accounts created via Google sign-in. Defaults to `viewer`. |
| `DISABLED_RETAILERS` | Optional | Comma-separated retailer ids to hide from the UI and skip in jobs (e.g. `DISABLED_RETAILERS=unioncoop`). For hosts whose datacenter IPs a site's WAF blocks — Unioncoop rejects Render egress (search 405s and even product pages fail), so set this on Render and run 32/32 there; leave unset locally to keep all 5 retailers. |
| `PROXY_<SITE>` | Optional | Per-retailer egress proxy, e.g. `PROXY_UNIONCOOP=http://user:pass@host:port` routes only Unioncoop through it. The way back to Unioncoop data on hosting: a UAE-exit proxy (their Fastly edge allowlists by egress IP). Without one, keep the retailer disabled on that host. |
| `FETCH_WORKERS` | Optional | Job thread-pool size. Defaults to `3` (safe for 512MB Render free). Raise only on hosts with headroom. |

Keep these in a local `.env` (not committed — see `.env.example` for a
template) for development, and in your host's secret manager for
production — never commit real values. `.env` is loaded automatically
via `python-dotenv` (see `database/connection.py`).

## MongoDB setup

1. **Create a free cluster.** Sign up at
   [mongodb.com/cloud/atlas](https://www.mongodb.com/cloud/atlas/register),
   create a project, then "Build a Database" → the free **M0** tier is
   plenty for this app.
2. **Create a database user.** Database Access → Add New Database User
   (username/password auth). This is the `MONGODB_URI` username/password.
3. **Allow network access.** Network Access → Add IP Address. For local
   dev, "Allow Access from Anywhere" (`0.0.0.0/0`) is the easiest option;
   for production, restrict it to your host's IP range if it's static.
4. **Get the connection string.** Database → Connect → Drivers → Python,
   copy the `mongodb+srv://...` URI, fill in your database user's
   password, and put it in `MONGODB_URI` (in `.env` locally, or your
   host's environment/secrets panel in production).
5. **Migrate existing data (optional, one-time).** If you already have
   `users.db` / `products.xlsx` from before this migration, run:
    ```bash
    python scripts/migrate_to_mongo.py
    ```
   This copies both into MongoDB without touching or deleting the
   original files. Safe to re-run.
6. **Start the app as usual** (`python app.py`). On startup it connects,
   creates indexes, and — if the `users` collection is empty — seeds the
   default admin account described above.
7. **Check it from the UI.** Log in as an admin, open the user menu (top
   right) → **Database Settings** to see live connection status,
   database name, ping time, and record counts. There's also a "Test
   Connection" button there for troubleshooting.

## Run (development)

```bash
python app.py
```

Then open **<http://127.0.0.1:5000>**. This mode uses Flask's built-in
dev server with `debug=True` — reloads on file changes, but **must not**
be used for anything reachable outside your own machine (see
Deployment).

First run: the table starts empty. Pick "All Retailers" / "All
Configured Products" (or narrow it down) and click **Fetch latest
prices** — each successful lookup becomes a cell in the table, and
running it again on a different day adds a new date column, building
price history over time.

## Deployment

Before pointing a real domain at this app:

1. **Turn off debug mode.** `app.run(..., debug=True)` exposes an
   interactive debugger on any unhandled error — that's remote code
   execution for anyone who can trigger one. In production, don't call
   `app.run()` at all; run through a WSGI server instead:

   ```bash
   gunicorn -w 2 -b 0.0.0.0:$PORT app:app
   ```

2. **Set `SECRET_KEY` and `ADMIN_USERNAME`/`ADMIN_PASSWORD`** as real
   environment variables (see table above) before the first deploy.
3. **Enforce secure cookies** once you're serving over HTTPS, by adding
   to `app.py`:

   ```python
   app.config["SESSION_COOKIE_SECURE"] = True
   app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
   ```

4. **Persistent storage.** `users.db`, `products.xlsx`, `secret.key`,
   and `uploads/` are plain local files. Confirm your host gives you a
   persistent volume, or move `users.db`/`products.xlsx` to a proper
   hosted database — otherwise data disappears on redeploy/restart.
5. **Single worker, or externalize job state.** `JOBS` in `app.py` is
   an in-memory dict. With more than one gunicorn worker/process, a job
   started on one worker won't be visible when another worker handles
   the poll request. Either run `-w 1`, or move `JOBS` to Redis/a DB if
   you need to scale.
6. **No browser needed.** Scraping uses Scrapling's plain-HTTP
    `Fetcher` (Chrome TLS impersonation, no Chromium binary), so the
    slim `python:3.11-slim` image fits free-tier hosts. The
    `StealthyFetcher` browser fallback only triggers for
    `fetch_mode="auto"` retailers when plain HTTP can't do the job — and
    is skipped entirely on images without browsers.
7. **Unioncoop / Kibsons oranges on hosting IPs.** Unioncoop's WAF
    answers search requests from datacenter IPs (Render) with HTTP 405,
    and Kibsons' catalog API doesn't list navel/valencia oranges. Both
    cases resolve     via verified product-URL tables (`KNOWN_URLS` in
    `scraper/retailers/unioncoop.py` / `scraper/retailers/kibsons.py`) — each URL is
    re-verified live (status + title + price) before use, so a renamed
    product fails loudly instead of recording a stale price.
7. **Rate-limit `/api/auth/login`** (e.g. with `Flask-Limiter`) — it's
   currently unthrottled and brute-forceable.
8. Double-check `.gitignore` excludes `secret.key`, `users.db`,
   `uploads/`, and `products.xlsx` so none of them end up in a public
   repo.

## Notes

- Each lookup is a plain HTTP fetch (no browser), so one retailer × one
  product takes a couple of seconds, and jobs fan out over a small thread
  pool (`FETCH_WORKERS`, default 5) — "all"/"all" (5 × 8 = 40 lookups)
  finishes in well under a minute.
- Every successful row stores its price normalized to `AED X.XX/kg`
  (see `compute_per_kg` in `scraper/utils.py`): the retailer's own
  per-kg figure when shown, otherwise price ÷ pack weight. When a page
  shows a price but no parseable weight, the price itself is reported as
  the per-kg value (≈1kg-pack assumption) so no cell stays blank.
- Barakat, Kibsons, and Union Coop selectors in `scraper/config.py` are
  marked as best-effort guesses — if a lookup fails or grabs the wrong
  product for those three, inspect the live search results page and
  update `result_selector` in `scraper/config.py`.
