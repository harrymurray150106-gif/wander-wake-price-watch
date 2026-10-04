# Wander & Wake Price Watch

An always-on price-tracking dashboard for **wanderandwakestore.com**, built to run entirely on your own GitHub account — nothing here depends on Claude to keep running. It checks prices on a schedule, logs every change it sees, and serves a small dashboard showing current prices and recent changes.

**What it watches:** 9 products from the "WOW Watersports" brand that Wander & Wake carries (water sports / boating gear — life vests, ropes, floats, pumps). Two of them have a confirmed matching listing at a competitor, **Up North Sports**, verified by matching SKU codes, so those two show a side-by-side comparison. The other seven are tracked for Wander & Wake's own price only, because I could not confirm an identical listing elsewhere without risking a false match (see "Why only 9 products" below).

## How it works (and why it's not "through Claude")

- **No scraping of rendered pages, no API keys.** Wander & Wake and Up North Sports are both built on Shopify, and every Shopify store publishes a free public JSON feed for each product at `/products/<handle>.json` — the exact data the storefront itself loads. The checker just reads that feed directly. This is far more reliable than parsing HTML, and much less likely to get blocked than hammering the normal web pages.
- **GitHub Actions runs the checks**, on a timer, inside your own GitHub account — not on any Anthropic/Claude infrastructure. `.github/workflows/update-prices.yml` runs `scripts/check_prices.py` every 15 minutes (configurable), and if any price changed, commits the updated data straight back into this repo.
- **GitHub Pages serves the dashboard**, a single static `index.html` that reads `data/snapshot.json` and `data/history.json` from the same repo. It also re-fetches that data every 60 seconds client-side, so a tab left open stays current without a manual reload.
- **Nothing is hosted by or routed through Claude.** Once this repo is pushed to your GitHub account, Claude is no longer involved at all — you could delete this chat and it would keep running.

### About "immediately"

True instant, push-based updates (the moment a price changes, with zero delay) would need Wander & Wake's own store admin to set up a webhook — not possible from the outside, since this is someone else's store. What this gives you instead is **frequent automated polling**: every 15 minutes by default, each check reads the live price directly from the store, so nothing is ever stale by more than one interval. You can tighten that in the workflow file (see below) — GitHub allows down to about every 5 minutes on free accounts before it starts silently throttling you.

## Set this up (10 minutes, no coding required)

1. **Create a new repository** on [github.com/new](https://github.com/new) — any name, e.g. `wander-wake-price-watch`. Keep it Public (GitHub Pages' free tier requires a public repo unless you're on a paid plan).
2. **Upload these files**: on the new repo's page, click "uploading an existing file", then drag in the entire contents of the folder you downloaded from this chat (keep the folder structure — `.github/workflows/update-prices.yml`, `data/`, `scripts/`, `index.html`, `README.md`). Commit directly to `main`.
   - If you're comfortable with git instead: `git init`, `git remote add origin <your repo URL>`, `git add .`, `git commit -m "Initial setup"`, `git push -u origin main`.
3. **Turn on GitHub Pages**: in the repo, go to *Settings → Pages*. Under "Build and deployment", set Source to "Deploy from a branch", branch `main`, folder `/ (root)`. Save. GitHub will give you a URL like `https://<your-username>.github.io/wander-wake-price-watch/` — that's your dashboard link.
4. **Turn on the scheduled checks**: go to the *Actions* tab. If prompted, click "I understand my workflows, go ahead and enable them". You should see "Update prices" listed. Click into it and press "Run workflow" once to do an immediate first check (otherwise it waits for the next 15-minute mark).
5. Done. Bookmark the Pages URL from step 3 — that's your live dashboard, and it'll keep itself updated for as long as the repo exists and Actions stays enabled (GitHub disables scheduled workflows automatically after 60 days of *zero* repo activity — any commit, including the bot's own price-update commits, resets that clock, so this effectively never happens on its own).

No secrets, API keys, or paid services are required anywhere in this setup.

## Adding more products or competitors later

Everything tracked lives in `data/config.json` — a plain list. Each entry looks like:

```json
{
  "id": "short-unique-id",
  "name": "Human-readable product name",
  "own": {
    "retailer": "Wander & Wake",
    "type": "shopify_json",
    "url": "https://wanderandwakestore.com/products/<handle>.json",
    "product_page": "https://wanderandwakestore.com/products/<handle>"
  },
  "competitors": [
    {
      "retailer": "Competitor Name",
      "type": "shopify_json",
      "url": "https://competitor-site.com/products/<handle>.json",
      "product_page": "https://competitor-site.com/products/<handle>",
      "match_note": "Why you're confident this is the same product (e.g. matching SKU)"
    }
  ]
}
```

To find a product's `<handle>`, open its page on the store and look at the URL — `wanderandwakestore.com/products/this-part-is-the-handle`. Appending `.json` to that page URL gives you the feed to check it actually returns data before adding it. This only works for stores built on Shopify (very common for small-to-mid retailers, but not universal — Amazon, Walmart, Target, and most big-box retailers use their own systems and actively block automated checking, so they're not realistic competitor sources for a free, self-run tool like this one).

Commit the change to `data/config.json` and the next scheduled run picks it up automatically.

### Why only 9 products, and why only 2 with competitor comparisons

Wander & Wake's catalogue has well over 100 products, most of them generic, unbranded drop-shipped goods (the same items are often resold under different names by hundreds of unrelated stores, with no reliable way to confirm "this exact item, at this exact spec, is the same one"). Rather than guess, this tool only tracks products and competitor matches I could actually verify — here, 9 products under the recognizable "WOW Watersports" brand, 2 of which I could confirm are identically sold at Up North Sports by matching their manufacturer SKU codes. The other 7 are tracked for Wander & Wake's own price history alone.

The checker also does a light sweep of Wander & Wake's product sitemap on every run and keeps a running list of product URLs it hasn't seen before in `data/discovered_products.json`, so if this site adds new products later you (or a future Claude session, or anyone) can see what's new and decide whether it's worth adding to `config.json` with a verified competitor match.

## Files in this repo

| Path | Purpose |
|---|---|
| `index.html` | The dashboard itself (served by GitHub Pages) |
| `data/config.json` | The list of tracked products and competitor URLs — edit this to add more |
| `data/snapshot.json` | Current prices, overwritten by every run |
| `data/history.json` | Every price change ever detected, most recent 2000 kept |
| `data/known_urls.json` / `data/discovered_products.json` | Bookkeeping for the new-product sweep |
| `data/last_run.json` | Status of the most recent check (for troubleshooting) |
| `scripts/check_prices.py` | The checker itself — plain-Python standard library only, no dependencies to install |
| `.github/workflows/update-prices.yml` | The schedule that runs the checker |

## Adjusting the check frequency

Open `.github/workflows/update-prices.yml` and change this line:

```yaml
  schedule:
    - cron: "*/15 * * * *"   # every 15 minutes
```

to whatever interval you want (standard 5-field cron syntax). Going much below every 5 minutes isn't recommended — GitHub's scheduler will start dropping runs under load, and it's also a more aggressive request rate against a store that isn't yours.

## If a check fails

Sites occasionally block an automated request, change their page structure, or just time out. When that happens for a given product/retailer, `check_prices.py` does **not** guess or carry forward a fabricated number — it leaves the last known price in place, marks it `"stale": true` with the error recorded, and the dashboard shows an amber "stale" tag on that listing so you know it's not been reconfirmed. Check the Actions tab's run logs, or `data/last_run.json`, for the specific error.
