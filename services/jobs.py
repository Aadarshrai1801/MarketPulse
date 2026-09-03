"""Async fetch-job store and runner (used by ``POST /api/jobs``).

In-memory job dict - fine for a single gunicorn worker (see the "Single
worker" note in requirements.txt). With more than one worker/process a job
started on one worker won't be visible when another handles the poll
request - either run ``-w 1`` or move this state to Redis/a DB.

Free Render has 0.5 CPU / 512MB RAM: Scrapling Fetcher rows are I/O-bound
(~2-5s each), so a small thread pool cuts all/all (40 lookups) from
minutes to ~30s without the RAM spike a browser pool would cause.
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .fetch import scrape_one

FETCH_WORKERS = int(os.environ.get("FETCH_WORKERS", "5"))

JOBS = {}
JOBS_LOCK = threading.Lock()


def run_fetch_job(job_id, retailers, products):
    pairs = [(product, retailer) for product in products for retailer in retailers]
    if not pairs:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return

    workers = max(1, min(FETCH_WORKERS, len(pairs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_pair = {
            pool.submit(scrape_one, product, retailer): (product, retailer)
            for product, retailer in pairs
        }
        for future in as_completed(future_to_pair):
            try:
                result = future.result()
            except Exception as e:  # scrape_one never raises, but stay safe
                product, retailer = future_to_pair[future]
                result = {
                    "ok": False,
                    "supermarket": retailer,
                    "product_id": product["id"],
                    "product_label": product["name"],
                    "product_emoji": product.get("emoji", "🥬"),
                    "error": str(e),
                }

            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is None:
                    return  # job was cleared/removed while running
                job["results"].append(result)
                job["completed"] += 1

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job["status"] = "done"
            job["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
