# Free-Render friendly: plain slim Python, no browser binaries.
# The old image (mcr.microsoft.com/playwright/python) + `playwright install`
# pulled ~1.5GB and needed far more RAM than Render free (512MB) provides.
# Scrapling's Fetcher path needs only pip packages, so this stays ~200-300MB.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# opencv-python-headless still needs these two shared libs at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# NOTE: intentionally NO `scrapling install` (browser download) here.
# Fetcher (default path) works without it. StealthyFetcher fallback only
# triggers for fetch_mode="auto" retailers when fast HTTP looks blocked;
# without browsers that row fails with a clear error instead of crashing.
# If you move to a bigger host and want the fallback live, add:
#   RUN python -m scrapling install  (or `scrapling install`)
# and rebuild.

COPY . .

# Render injects PORT at runtime; -w 1 matters because JOBS in
# app.py is an in-memory dict - multiple worker processes would each keep
# their own copy and job polling would break.
# --timeout 120: Scrapling Fetcher rows take seconds (not minutes like
# Playwright), but all/all (40 lookups) still needs headroom on 0.5 CPU.
CMD gunicorn -w 1 --timeout 120 -b 0.0.0.0:${PORT:-5000} app:app
