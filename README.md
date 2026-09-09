# Midnight Watch — Tulsa Release Tracker

Public dashboard + two scheduled monitors for **Midnight Spider-Man** (Marvel) and **Absolute Batman** (DC) availability across Tulsa-metro retailers, plus **Phillip Kennedy Johnson (PKJ)** appearances in Oklahoma and Kansas.

```
index.html                     built page (data baked in; also fetches data.json when served)
template.html                  page source with a __DATA__ placeholder
data.json                      canonical data model (series, retailers, listings, activity, appearances, sources)
build.py                       data.json + template.html -> index.html
monitors/sync_catalog.py       every 30 minutes: enumerate + live-verify every series product on Shopify retailers
monitors/check_availability.py every 30 minutes: re-check any other product-URL rows
monitors/check_appearances.py  daily PKJ scan
monitors/fetch_covers.py       saves Midnight Spider-Man cover images into covers/
covers/                        22 retailer cover images (Midnight Spider-Man #1 A–J + signed, #2 A and C)
monitors/common.py             shared helpers
.github/workflows/monitor.yml  every 30 minutes + daily cron, commit, deploy to GitHub Pages
```

## 1. What is real and what is not (as of Sep 9, 2026, ~2:45 PM CT)

Every listing and appearance carries a `verification` field and the page shows it as a tag on each row.

| Tag | Meaning | Rows |
|---|---|---|
| `live` | Checked on Sep 9 against the source itself | **All 357 Impulse Creations listings** — 26 Midnight Spider-Man (#1 Covers A–J, the 10 PKJ-signed editions, #2 Covers A–F) and 331 Absolute Batman (issues #1–#25, all printings, 2025 Annual, Ark-M Special, Beyond Ark-M, Noir Edition, NYCC ashcan, Scott Snyder–signed copies, collected editions). Each was checked against the product page's storefront `.js` availability **and** the submit button inside the `/cart/add` form. **PKJ Tulsa signing** Sat Oct 17 (talk 1–2, signing 2–4, free) — Impulse's own event page. Baltimore Comic-Con Sep 25–27. Harrisonburg Jan 17 (past). |
| `publisher` | Publisher/press/aggregator fact; no retailer purchase path checked | MSM #1 credits, FOC Aug 31, $5.99 (Marvel.com). MSM #3 on Dec 16 (Marvel.com). Absolute Batman #24 → Sep 23 (DC via Bleeding Cool). NYCC/616 exclusive covers (League of Comic Geeks cover slate). |
| `directory` | Store facts confirmed via Google Places on Sep 9; inventory not surveyed | 7 of the 18 metro stores. |
| `snapshot` | Carried from the Sep 7 project screenshots and not re-checked | Inventory notes for the remaining non-Impulse stores. |

**No `demo` rows remain.** The two Absolute Batman placeholders were replaced by real Impulse product rows.

**What the Sep 9 live check found**
- Midnight Spider-Man #1: all 20 Impulse listings (A–J plus signed) are **sold out** — disabled *Sold out* button on every product page. $5.99 regular, $10.99 signed, $30 / $60 / $125 incentives.
- Midnight Spider-Man #2: Covers A–F are **preorder open** at $3.74 (list $4.99), E 1:25 $30, F 1:50 $60. Retailer tags: FOC **Oct 9**, on sale **Nov 18**. Impulse has not yet uploaded art for B, D, E, F; the page says so instead of showing a placeholder image.
- Absolute Batman: **56 listings buyable** (#17, #18, #22, #23 first prints, all eleven #25 covers on preorder with FOC Sep 25, the Beyond Ark-M one-shot, several 1:25s and recent reprints). Impulse's tag puts #25 on sale **Oct 28**, one week later than the July solicitation (Oct 21) — the page shows both.
- Totals on the page: **62 buyable listings**, 1 confirmed OK appearance, 0 Kansas.

**Retailer facts vs. publisher facts.** Release/FOC dates on listing rows come from Impulse's product tags (`New Release M-D-YY`, `FOC M-D-YY`); they are the store's expectation, not a publisher confirmation, and are labelled that way where they differ.

**Route 66 Comics** (Sapulpa) is behind Cloudflare bot protection and answered the monitor with 403/429, so it is marked manual. All other metro stores publish no per-title inventory; the page shows how to order from them but cannot see their shelves.

**Cover images.** `covers/` holds 22 Midnight Spider-Man images downloaded from Impulse's own product listings (`image_url` on each row records the source). Absolute Batman rows keep the retailer image URL but no local copy. `fetch_covers.py --series=msm,abat` would store those too.

**Not covered.** Any retailer other than Impulse for either series, and online US retailers. Add a Shopify store to `retailers` with `platform: "shopify"` and the sync enumerates it automatically.

## 2. Connecting the every 30 minutes and daily monitors

**Local test**

```bash
pip install requests
python monitors/check_availability.py --dry   # prints per-row status, writes nothing
python monitors/check_appearances.py --dry
python monitors/sync_catalog.py               # enumerate + verify every Impulse row (~10 min), writes data.json
python monitors/fetch_covers.py               # saves Midnight Spider-Man covers into covers/
python build.py                               # rebuilds index.html
```

**30-minute catalog sync** (`sync_catalog.py`) — for each Shopify retailer: walks `/collections/all/products.json` (paged, up to 150×250 — Impulse has ~19,600 products), the retailer's `catalog_collections`, and the predictive-search API; keeps products whose **title** is branded Midnight Spider-Man or Absolute Batman (URL handles are unreliable — Impulse still has `tbd-artist` handles on covers that now have artists); excludes DC's *Absolute Edition* hardcovers of classic Batman stories, statues, parodies and the MSM poster. New products create one `new_listing` activity; a changed `New Release` tag creates a `date_change`. Discovery is cached for an hour in `monitors/.catalog_cache.json` and verification saves every 10 rows, so an interrupted run resumes (`--budget=N` limits rows per run, `--discover` only refreshes the cache).

**Verification rule** (`check_availability.shopify_state`, used by both scripts): reads `<product-url>.js` for `available` and price — Impulse's `.json` endpoint omits `available`, which is why the earlier version could never see a buyable item — then the HTML page's submit button inside `<form action="/cart/add">`. `disabled`/*Sold out* → `sold_out`; enabled *Add to cart* → `available`; enabled *Pre-order* → `preorder_open`. Both signals must agree; disagreement → `needs_verification`. No locale cookie is sent, so Shopify serves the US/USD storefront. Shopify throttles bursts, so requests are paced (~1.5 s per product) and back off on 429.

**30-minute availability scan** (`check_availability.py`) — re-checks any remaining listing with a `/products/` URL that the sync did not cover (other platforms use HTML heuristics only and never mark `available` without an explicit enabled add-to-cart control).
- Other platforms: HTML heuristics only; never marks `available` without an explicit enabled add-to-cart control.
- Writes an activity entry only for: sold-out → purchasable (`restock`), purchasable → sold out, preorder opening, price change > $0.01. Otherwise it only refreshes `last_verified`.
- Rows with `verification: demo` or aggregator source links are skipped, so nothing demo can ever flip to "Available" by accident.

**Daily PKJ scan** (`check_appearances.py`) — fetches the pages in `WATCH` (Impulse event page, FanCons, Baltimore Comic-Con, PKJ's site), extracts sentences mentioning Oklahoma, Kansas, or Tulsa-metro cities, hashes them, and diffs against `monitors/.appearance_state.json`. A change writes one `appearance_added` activity with the snippet and link for **human review**; it does not auto-create a dated appearance because it cannot verify dates or venues.

**Adding a listing**: append an object to `listings` in `data.json` with `status: "needs_verification"`; the next every 30 minutes run sets the real state. Add convention guest lists (e.g. OKC Comic Con, Planet Comicon Kansas City, Wichita) to `WATCH` as URLs become known.

**Schedule**: `.github/workflows/monitor.yml` runs the catalog sync + availability scan at :07 and :37 every hour and the appearance scan at 13:00 UTC (8 AM CDT), commits `data.json` + `index.html`, and deploys. The page shows a live countdown to the next :07/:37 UTC run; GitHub sometimes starts scheduled jobs a few minutes late, so the countdown is the earliest the scan can begin, not a guarantee. Any cron host works instead: `7,37 * * * * cd /path && python monitors/sync_catalog.py && python monitors/fetch_covers.py && python build.py`.

## 3. Deploying publicly (no AI account needed by visitors)

The site is three static files; anyone with the URL can open it.

**GitHub Pages (free, includes the scheduler)**
1. Create a public repo, push this folder.
2. Settings → Pages → Source: **GitHub Actions**.
3. Actions → *Midnight Watch monitors* → **Run workflow** once to seed the state file.
4. Site is live at `https://<user>.github.io/<repo>/`. Every scan that changes something redeploys automatically.

**Netlify / Cloudflare Pages / Vercel**: drag-and-drop the folder or connect the repo; no build command needed (or `python build.py`). Keep the GitHub Action for the scans, or run the scripts from any cron host that can push to the repo.

**Anywhere else**: upload `index.html`, `data.json` (and nothing else) to any web server. Opening `index.html` straight from disk also works — it shows the baked-in data and a notice that live `data.json` refreshes need HTTP.

## 4. Data model (data.json)

- `meta` — timestamps (Central Time ISO-8601), scan cadence, `verification_levels`.
- `series[]` — id, publisher, creators, `issues[]` (number, release/FOC dates, price, source, verification).
- `retailers[]` — id, name, city, address, website, `platform` (`shopify` | `manage-comics` | `ebay` | `web` | `none`), ordering method, `auto_check`, and for Shopify stores `catalog_collections` / `catalog_queries` used by the sync.
- `listings[]` — one per variant per retailer: `series_id`, `issue`, `title`, `variant`, `variant_type` (`regular` | `variant` | `incentive` | `virgin` | `exclusive` | `signed` | `foil` | `blank` | `reprint` | `special` | `collected`), `retailer_id`, `format`, `price_usd`, `status` (`available` | `preorder_open` | `sold_out` | `coming_soon` | `needs_verification` | `unknown` | `demo`), `preorder`, `release_date`, `foc_date`, `listing_url`, `last_verified`, `verification`, `source_type` (`retailer` | `publisher` | `aggregator` | `marketplace`), `evidence`. Synced rows also carry `retailer_title` (the store's exact product name), `list_price_usd` (Shopify compare-at price), `sku`, `upc`, `image_url`, `image_local`, and for Midnight Spider-Man `cover_letter`, `cover_name`, `artist`, `cover_kind`; Absolute Batman rows carry `printing`.
- `activity[]` — `detected_at`, `type`, `series_id`, `text`, `source_url`. Newest first on the page.
- `appearances[]` — event, date/time, city/state, venue, type, entry requirement, status (`confirmed` | `unconfirmed` | `past` | `cancelled`), `in_region` (OK/KS), url, source, `last_verified`, verification, notes.
- `sources[]` — label, url, role.

Duplicates: the monitors key on `listing_url`; a second row with the same URL but a different `variant` is left in place and should be reviewed, never auto-merged.
