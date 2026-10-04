#!/usr/bin/env python3
"""
Wander & Wake Price Watch — price checker.

Runs on a schedule via GitHub Actions (see .github/workflows/update-prices.yml).
Each run:
  1. Reads data/config.json (the list of tracked products + competitor URLs).
  2. Fetches the current price/stock for each product's own listing and any
     mapped competitor listings, using each store's public Shopify
     storefront JSON endpoint (the same data the store's own site uses —
     no scraping of rendered HTML, no API keys required).
  3. Compares against the last snapshot (data/snapshot.json). Any price
     that changed is appended to data/history.json as a dated event.
  4. Writes the new snapshot + a data/last_run.json status file.
  5. Also does a light "new product" sweep of Wander & Wake's product
     sitemap, so newly listed products get flagged for someone to review
     and (optionally) add to config.json later.

This script is deliberately dependency-light (just `requests`) and
tolerant of individual failures: if one URL fails (timeout, 404, site
blocks the request that run, etc.) the rest of the run still completes,
and the failure is recorded rather than silently dropped or guessed at.
Nothing here ever invents a price — a failed fetch leaves the previous
known value untouched and is flagged as stale instead.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from xml.etree import ElementTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
SNAPSHOT_PATH = os.path.join(DATA_DIR, "snapshot.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
LAST_RUN_PATH = os.path.join(DATA_DIR, "last_run.json")
KNOWN_URLS_PATH = os.path.join(DATA_DIR, "known_urls.json")
DISCOVERED_PATH = os.path.join(DATA_DIR, "discovered_products.json")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT_SECONDS = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
SITEMAP_URL = "https://wanderandwakestore.com/sitemap_products_1.xml"
NEW_PRODUCT_CAP_PER_RUN = 25


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=False)
        f.write("\n")


def fetch(url):
    """GET a URL with a browser-like User-Agent, retrying a few times.
    Returns (bytes_or_None, error_message_or_None)."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/xml, */*"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return resp.read(), None
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in (429, 503) and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            break
        except Exception as e:  # noqa: BLE001 - we want to record any failure, not crash the run
            last_err = str(e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            break
    return None, last_err


def fetch_shopify_json_price(url):
    """Fetch a Shopify /products/<handle>.json endpoint and return a
    normalised price record, or an error."""
    raw, err = fetch(url)
    if err:
        return None, err
    try:
        payload = json.loads(raw)
        product = payload["product"]
    except Exception as e:  # noqa: BLE001
        return None, f"unexpected response shape: {e}"

    variants = product.get("variants", [])
    if not variants:
        return None, "no variants in response"

    prices = []
    any_available = False
    for v in variants:
        try:
            prices.append(float(v["price"]))
        except (KeyError, TypeError, ValueError):
            continue
        if v.get("available") is True:
            any_available = True

    if not prices:
        return None, "could not parse any variant price"

    return {
        "title": product.get("title"),
        "price_min": min(prices),
        "price_max": max(prices),
        "currency": "USD",
        "available": any_available if any(("available" in v) for v in variants) else None,
        "variant_count": len(variants),
        "checked_at": now_iso(),
    }, None


FETCHERS = {
    "shopify_json": fetch_shopify_json_price,
}


def fetch_listing(listing):
    fetcher = FETCHERS.get(listing.get("type"))
    if fetcher is None:
        return None, f"unsupported source type: {listing.get('type')}"
    return fetcher(listing["url"])


def check_products(config):
    snapshot = load_json(SNAPSHOT_PATH, {"products": {}, "generated_at": None})
    history = load_json(HISTORY_PATH, [])
    run_log = {"checked": 0, "changed": 0, "errors": []}

    for product in config["products"]:
        pid = product["id"]
        prev = snapshot["products"].get(pid, {})
        entry = {
            "id": pid,
            "name": product["name"],
            "own": prev.get("own"),
            "competitors": dict(prev.get("competitors", {})),
        }

        # --- own price ---
        own_result, own_err = fetch_listing(product["own"])
        run_log["checked"] += 1
        if own_err:
            run_log["errors"].append({"product": pid, "source": product["own"]["retailer"], "error": own_err})
            if entry["own"]:
                entry["own"]["stale"] = True
                entry["own"]["last_error"] = own_err
        else:
            new_own = {
                "retailer": product["own"]["retailer"],
                "product_page": product["own"]["product_page"],
                "price_min": own_result["price_min"],
                "price_max": own_result["price_max"],
                "currency": own_result["currency"],
                "available": own_result["available"],
                "checked_at": own_result["checked_at"],
                "stale": False,
            }
            old_price = (prev.get("own") or {}).get("price_min")
            if old_price is not None and old_price != new_own["price_min"]:
                history.append({
                    "at": now_iso(),
                    "product_id": pid,
                    "product_name": product["name"],
                    "retailer": new_own["retailer"],
                    "kind": "own",
                    "old_price": old_price,
                    "new_price": new_own["price_min"],
                })
                run_log["changed"] += 1
            entry["own"] = new_own

        # --- competitors ---
        for comp in product.get("competitors", []):
            key = comp["retailer"]
            comp_result, comp_err = fetch_listing(comp)
            run_log["checked"] += 1
            prev_comp = (prev.get("competitors") or {}).get(key)
            if comp_err:
                run_log["errors"].append({"product": pid, "source": key, "error": comp_err})
                if prev_comp:
                    prev_comp["stale"] = True
                    prev_comp["last_error"] = comp_err
                    entry["competitors"][key] = prev_comp
                continue
            new_comp = {
                "retailer": key,
                "product_page": comp["product_page"],
                "price_min": comp_result["price_min"],
                "price_max": comp_result["price_max"],
                "currency": comp_result["currency"],
                "available": comp_result["available"],
                "checked_at": comp_result["checked_at"],
                "match_note": comp.get("match_note"),
                "stale": False,
            }
            old_price = (prev_comp or {}).get("price_min")
            if old_price is not None and old_price != new_comp["price_min"]:
                history.append({
                    "at": now_iso(),
                    "product_id": pid,
                    "product_name": product["name"],
                    "retailer": key,
                    "kind": "competitor",
                    "old_price": old_price,
                    "new_price": new_comp["price_min"],
                })
                run_log["changed"] += 1
            entry["competitors"][key] = new_comp

        snapshot["products"][pid] = entry

    snapshot["generated_at"] = now_iso()
    save_json(SNAPSHOT_PATH, snapshot)
    # Keep history from growing forever unbounded in a public repo; retain most recent 2000 events.
    save_json(HISTORY_PATH, history[-2000:])
    return run_log


def sweep_for_new_products(run_log):
    """Light-touch new-product detection: diff the store's product sitemap
    against the list of URLs we've already seen, and record anything new
    for a human (or a future run) to triage. Never auto-creates tracked
    products — we don't have confirmed competitor matches for anything we
    haven't researched."""
    is_first_run = not os.path.exists(KNOWN_URLS_PATH)
    known = load_json(KNOWN_URLS_PATH, {})
    discovered = load_json(DISCOVERED_PATH, [])
    discovered_urls = {d["url"] for d in discovered}

    raw, err = fetch(SITEMAP_URL)
    if err:
        run_log["errors"].append({"product": "(sitemap sweep)", "source": "wanderandwakestore.com", "error": err})
        return

    try:
        root = ElementTree.fromstring(raw)
    except Exception as e:  # noqa: BLE001
        run_log["errors"].append({"product": "(sitemap sweep)", "source": "wanderandwakestore.com", "error": f"could not parse sitemap XML: {e}"})
        return

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    found_urls = []
    for url_el in root.findall("sm:url", ns):
        loc_el = url_el.find("sm:loc", ns)
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()
        if "/products/" in loc:
            found_urls.append(loc)

    if is_first_run:
        # Baseline seed: everything currently in the sitemap is "already known",
        # not a fresh discovery. Only URLs that show up in *later* runs are new.
        for url in found_urls:
            known[url] = {"first_seen": now_iso()}
        save_json(KNOWN_URLS_PATH, known)
        run_log["new_products_found"] = 0
        run_log["baseline_seeded_urls"] = len(known)
        return

    new_count = 0
    for url in found_urls:
        if url in known or url in discovered_urls:
            continue
        if new_count >= NEW_PRODUCT_CAP_PER_RUN:
            break
        discovered.append({"url": url, "first_seen": now_iso()})
        known[url] = {"first_seen": now_iso()}
        new_count += 1

    if new_count:
        save_json(KNOWN_URLS_PATH, known)
        save_json(DISCOVERED_PATH, discovered)
    run_log["new_products_found"] = new_count


def main():
    config = load_json(CONFIG_PATH, None)
    if config is None:
        print("data/config.json not found", file=sys.stderr)
        sys.exit(1)

    run_log = check_products(config)
    sweep_for_new_products(run_log)
    run_log["run_at"] = now_iso()
    save_json(LAST_RUN_PATH, run_log)

    print(json.dumps(run_log, indent=2))


if __name__ == "__main__":
    main()
