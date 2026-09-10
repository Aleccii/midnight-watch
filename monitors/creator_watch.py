#!/usr/bin/env python3
"""Creator watch — runs hourly. Tracks one creator's cover variants and signed /
exclusive editions across three storefronts and writes them into data.json as
listings with series_id = the creator's watch id ("sy" for Skottie Young).

Sources (all read from the retailer's own US storefront, never Shop.app, cached
snippets or aggregators):

  Impulse Creations (Shopify, Tulsa)
      Discovery walks /collections/all/products.json plus every collection named
      in creators[].impulse_collections (250 per page; Shopify caps each
      collection at 100 pages, so the big publisher collections are walked
      individually). A product belongs to the watch when its TITLE contains the
      creator's name. Verification = the same two-signal rule as the series
      sync (product .js availability AND the submit button inside the
      /cart/add form must agree).

  Midtown Comics (custom storefront, New York — online US retailer)
      Discovery pages through /search?q=<creator>&pp=100&pj=N and parses each
      product card (title, publisher, release date, image, product URL).
      Only titles that credit the creator on the COVER are kept by default
      ("... Skottie Young Cover", "... Skottie Young Variant"); books he wrote
      but did not draw the cover for are skipped unless --include-writer.
      Verification POSTs /search-load-product-body for the product, which is the
      exact fragment Midtown renders for the purchase control: an enabled
      "ADD TO CART" button = purchasable (preorder_open when the release date is
      in the future), a wishlist-only fragment = sold_out, anything else =
      needs_verification.

  skottieyoung.com — "Stupid Fresh Mess" (Shopify, the creator's own store)
      Discovery walks /collections/all/products.json. Kept: comic books,
      artist exclusives, CGC Signature Series slabs, Big Marvel sets and
      signed comics in the warehouse sale. Skipped: original art, stickers,
      merch, prints, digital books, gift cards, graphic novels and auction
      lots. Each product's editions (Unsigned / Signed with COA / CGC 9.8 …)
      are recorded per variant with their own price and availability; the row
      status is the product-level purchase control.

Alerts: an activity entry (which notify.py forwards to Telegram) is written only
for a new listing, a sold-out -> purchasable restock, purchasable -> sold out,
a preorder opening, a price change > $0.01, an edition becoming purchasable, or
a retailer release-date change. Unchanged rows only refresh last_verified.

Hourly gate: the GitHub Action runs every 30 minutes; this script exits early
if its last full run finished less than 55 minutes ago (override with --force).

  python monitors/creator_watch.py --dry            # print, write nothing
  python monitors/creator_watch.py --source=midtown # one source only
  python monitors/creator_watch.py --budget=40      # verify at most 40 rows
"""
import re, sys, json, time, html as H, datetime, pathlib
import requests
from common import load, save, add_activity, now_iso, UA, ROOT, CT
from check_availability import shopify_state

CACHE = pathlib.Path(__file__).with_name(".creator_cache.json")   # discovery cache (55 min)
STATE = pathlib.Path(__file__).with_name(".creator_state.json")   # last full run
FRESH_MIN = 55
TIMEOUT = 40

# skottieyoung.com scope
SY_TYPES_KEEP = {"comic books", "cgc signature series", "big marvel", "warehouse sale"}
SY_TAGS_KEEP = {"exclusives", "big marvel", "warehouse sale"}
SY_EXCLUDE = re.compile(r"\bposter\b|mystery cgc|\bsocks?\b|\bkoozie\b|\btote\b|\bpatch\b|\bhat\b", re.I)


def get(url, **kw):
    for attempt in range(6):
        r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
        if r.status_code == 429:
            time.sleep(8 * (attempt + 1)); continue
        return r
    return r


def fresh(iso, minutes=FRESH_MIN):
    if not iso:
        return False
    try:
        t = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=CT)
    return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() < minutes * 60


def cache_get(key):
    c = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    e = c.get(key)
    return e["items"] if e and fresh(e["at"]) else None


def cache_put(key, items):
    c = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    c[key] = {"at": now_iso(), "items": items}
    CACHE.write_text(json.dumps(c))


# ---------------------------------------------------------------------------
# Title parsing shared by Impulse and Midtown rows
# ---------------------------------------------------------------------------
def parse_cover(title, creator):
    """Return dict(series_title, issue, cover_letter, ratio, virgin, foil, signed,
    variant, variant_type, format) from a retailer product title."""
    t = re.sub(r"\s+", " ", title).strip()
    up = t.upper()
    signed = bool(re.search(r"\bsigned\b|\(signed", t, re.I))
    ratio = (re.search(r"\b1:(\d+)\b", t) or [None, None])[1]
    virgin = bool(re.search(r"\bvirgin\b", t, re.I))
    foil = bool(re.search(r"\bfoil\b", t, re.I))
    bw = bool(re.search(r"black (?:and|&) white|\bb&w\b", t, re.I))
    letter = (re.search(r"\b(?:COVER|CVR) ([A-Z])\b", t, re.I) or [None, None])[1]
    exclusive = bool(re.search(r"\bexclusive\b|display box", t, re.I))
    facsimile = "FACSIMILE" in up
    printing = re.search(r"\b(2ND|3RD|SECOND|THIRD)\s+(?:PTG|PRINT(?:ING)?)\b", up)
    incentive = bool(ratio) or "INCENTIVE" in up or "INC " in up
    m = re.match(r"(.*?)\s*#\s*(\d+)", t)
    series_title = (m.group(1) if m else re.split(r"\b(?:COVER|CVR|TP|HC)\b", t, 1, re.I)[0]).strip(" -:")
    series_title = re.sub(r"\s*\((?:2024|2025|2026|2022|2023)\)\s*$", "", series_title).strip()
    issue = m.group(2) if m else None
    fmt = "collected edition" if re.search(r"\b(TP|HC|GN)\b", up) else ("one-shot" if "ONE SHOT" in up or "ONE-SHOT" in up else "single issue")
    kind = []
    if ratio: kind.append(f"1:{ratio} incentive")
    elif incentive: kind.append("incentive")
    if virgin: kind.append("virgin")
    if bw: kind.append("black & white")
    if foil: kind.append("foil")
    if exclusive: kind.append("exclusive")
    if facsimile: kind.append("facsimile")
    if printing: kind.append(printing.group(0).title())
    vt = ("signed" if signed else "exclusive" if exclusive else "incentive" if incentive else
          "virgin" if virgin else "foil" if foil else "reprint" if printing else
          "collected" if fmt == "collected edition" else "variant")
    variant = f"Cover {letter} — {creator}" if letter else f"{creator} variant"
    if kind: variant += " (" + ", ".join(kind) + ")"
    return dict(series_title=series_title.title().replace("Dnx", "DNX").replace("G.O.D.S.", "G.O.D.S.").replace("X-Men", "X-Men"),
                issue=issue, cover_letter=letter, ratio=ratio, virgin=virgin, foil=foil, signed=signed,
                variant=variant, variant_type=vt, format=fmt + (" (signed)" if signed else ""),
                cover_kind=", ".join(kind) or ("regular" if letter == "A" else "trade dress"))


def tag_date(tags, key):
    m = re.search(key + r"\s*(\d{1,2})-(\d{1,2})-(\d{2})", ", ".join(tags), re.I)
    return f"20{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None


# ---------------------------------------------------------------------------
# Impulse Creations
# ---------------------------------------------------------------------------
def impulse_discover(r, cr):
    key = f"impulse:{cr['id']}"
    items = cache_get(key)
    if items is not None:
        return items
    base = r["website"].rstrip("/")
    match = cr["match"].lower()
    seen = {}
    for coll in ["all"] + list(cr.get("impulse_collections") or []):
        page = 1
        while page <= 100:
            resp = get(f"{base}/collections/{coll}/products.json", params={"limit": 250, "page": page})
            if resp.status_code != 200:
                break
            ps = resp.json().get("products", [])
            if not ps:
                break
            for p in ps:
                if match in p["title"].lower():
                    seen.setdefault(p["handle"], p)
            page += 1
            time.sleep(0.25)
    for q in cr.get("impulse_queries") or [cr["match"]]:
        resp = get(f"{base}/search/suggest.json", params={"q": q, "resources[type]": "product", "resources[limit]": 10})
        if resp.status_code != 200:
            continue
        for p in resp.json().get("resources", {}).get("results", {}).get("products", []):
            if p["handle"] not in seen and match in p["title"].lower():
                pr = get(f"{base}/products/{p['handle']}.json")
                if pr.status_code == 200:
                    seen[p["handle"]] = pr.json()["product"]
                time.sleep(0.4)
    items = [{k: p.get(k) for k in ("title", "handle", "tags", "variants", "images", "vendor", "product_type", "published_at")} for p in seen.values()]
    cache_put(key, items)
    return items


def impulse_row(p, r, cr):
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    v = p["variants"][0]
    f = parse_cover(p["title"], cr["name"])
    return {"id": f"{r['id']}-{p['handle']}"[:90], "series_id": cr["series_id"], "creator_id": cr["id"], "role": "cover",
            "retailer_id": r["id"], "retailer_title": p["title"], "title": f"{f['series_title']}" + (f" #{f['issue']}" if f["issue"] else ""),
            "publisher": p.get("vendor"), "price_usd": float(v["price"]),
            "list_price_usd": float(v["compare_at_price"]) if v.get("compare_at_price") else None,
            "sku": v.get("sku"), "upc": v.get("barcode"),
            "release_date": tag_date(tags, "New Release"), "foc_date": tag_date(tags, "FOC"),
            "listing_url": f"{r['website'].rstrip('/')}/products/{p['handle']}", "source_type": "retailer", "verification": "live",
            "image_url": (p.get("images") or [{}])[0].get("src"), "published_at": p.get("published_at"),
            "tag_preorder": any(t.lower() == "preorder" for t in tags),
            **{k: f[k] for k in ("issue", "variant", "variant_type", "cover_letter", "cover_kind", "format")}, "artist": cr["name"]}


# ---------------------------------------------------------------------------
# Midtown Comics
# ---------------------------------------------------------------------------
CARD = re.compile(r'id="main-record-(\d+)"')


def midtown_cards(base, q):
    """Parse every product card on the paginated search results."""
    s = requests.Session(); s.headers.update(UA)
    cards = []; pj = 1
    while pj <= 20:
        for attempt in range(4):
            r = s.get(f"{base}/search", params={"q": q, "pp": 100, "pj": pj}, timeout=TIMEOUT)
            if r.status_code == 200:
                break
            time.sleep(5 * (attempt + 1))
        t = r.text
        ids = CARD.findall(t)
        if not ids:
            break
        starts = [t.find(f'id="main-record-{i}"') for i in ids] + [len(t)]
        for k, pid in enumerate(ids):
            c = t[starts[k]:starts[k + 1]]
            g = lambda pat: (re.search(pat, c, re.S | re.I) or [None, None])[1]
            cards.append(dict(pid=pid, url=g(r'href="(https://www\.midtowncomics\.com/p/[^"]+)"'),
                              title=H.unescape(g(r"<h3>(.*?)</h3>") or "").strip(),
                              publisher=H.unescape(g(r'pc-publisher">(.*?)</span>') or ""),
                              release=g(r"search\?sd=([\d/]+)"),
                              writers=sorted({H.unescape(x) for x in re.findall(r'class="writer"><a[^>]*>(.*?)</a>', c, re.I)}),
                              artists=sorted({H.unescape(x) for x in re.findall(r'class="artist"><a[^>]*>(.*?)</a>', c, re.I)}),
                              image=g(r'<img src="(https://www\.midtowncomics\.com/images/PRODUCT/[^"]+)"')))
        if len(ids) < 100:
            break
        pj += 1; time.sleep(1)
    return s, cards


def midtown_state(s, base, pid):
    """(status, price, list_price, evidence) from Midtown's own purchase-control fragment."""
    for attempt in range(3):
        r = s.post(f"{base}/search-load-product-body", data={"pr_parentid": pid, "pr_id": pid}, timeout=TIMEOUT)
        if r.status_code == 200:
            break
        time.sleep(5)
    if r.status_code != 200:
        return "needs_verification", None, None, f"Purchase-control endpoint answered HTTP {r.status_code}"
    x = re.sub(r"<select.*?</select>", "", r.text, flags=re.S)
    prices = [float(p.replace(",", "")) for p in re.findall(r"\$([\d,]+\.\d{2})", x)]
    strike = re.search(r'class="strike">\$([\d,]+\.\d{2})', x)
    disc = re.search(r'pc-discounted"\s*>\$([\d,]+\.\d{2})', x)
    price = float(disc.group(1).replace(",", "")) if disc else (prices[0] if prices else None)
    list_price = float(strike.group(1).replace(",", "")) if strike else None
    btns = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", b)).strip().upper() for b in re.findall(r"<button[^>]*>(.*?)</button>", x, re.S)]
    if any(b == "ADD TO CART" for b in btns):
        return "available", price, list_price, "Midtown purchase control renders an enabled 'ADD TO CART' button"
    if any("WISHLIST" in b for b in btns) and not any("CART" in b for b in btns):
        return "sold_out", price, list_price, "Midtown purchase control renders wishlist only — no add-to-cart button"
    return "needs_verification", price, list_price, f"Unrecognised purchase control ({', '.join(btns)[:80] or 'no buttons'})"


def midtown_row(c, r, cr, role):
    f = parse_cover(c["title"], cr["name"])
    rel = None
    if c["release"]:
        mm, dd, yy = c["release"].split("/")
        rel = f"{yy}-{int(mm):02d}-{int(dd):02d}"
    return {"id": f"midtown-{c['pid']}", "series_id": cr["series_id"], "creator_id": cr["id"], "role": role,
            "retailer_id": r["id"], "retailer_title": c["title"], "title": f"{f['series_title']}" + (f" #{f['issue']}" if f["issue"] else ""),
            "publisher": c["publisher"] or None, "release_date": rel, "foc_date": None,
            "listing_url": c["url"] or f"{r['website'].rstrip('/')}/p/{c['pid']}", "source_type": "retailer", "verification": "live",
            "image_url": c["image"], "midtown_id": c["pid"],
            **{k: f[k] for k in ("issue", "variant", "variant_type", "cover_letter", "cover_kind", "format")}, "artist": cr["name"]}


# ---------------------------------------------------------------------------
# skottieyoung.com (creator store)
# ---------------------------------------------------------------------------
def store_discover(r, cr):
    key = f"store:{cr['id']}"
    items = cache_get(key)
    if items is not None:
        return items
    base = r["website"].rstrip("/")
    prods = []; page = 1
    while page <= 100:
        resp = get(f"{base}/collections/all/products.json", params={"limit": 250, "page": page})
        if resp.status_code != 200:
            break
        ps = resp.json().get("products", [])
        if not ps:
            break
        prods += ps; page += 1; time.sleep(0.3)
    keep = []
    for p in prods:
        tags = [t.lower() for t in (p.get("tags") or [])]
        ptype = (p.get("product_type") or "").lower()
        if "auction" in tags or SY_EXCLUDE.search(p["title"]):
            continue
        if ptype in SY_TYPES_KEEP or (set(tags) & SY_TAGS_KEEP):
            keep.append({k: p.get(k) for k in ("title", "handle", "tags", "variants", "images", "vendor", "product_type", "published_at")})
    cache_put(key, keep)
    return keep


def store_row(p, r, cr):
    t = p["title"]; up = t.upper(); tags = [x.lower() for x in (p.get("tags") or [])]; ptype = (p.get("product_type") or "")
    eds = [{"edition": v["title"], "price_usd": float(v["price"]), "available": bool(v.get("available")), "sku": v.get("sku")} for v in p["variants"]]
    cgc = "CGC" in up or ptype.lower() == "cgc signature series"
    signed = "SIGNED" in up or any("signed" in e["edition"].lower() for e in eds)
    excl = "EXCLUSIVE" in up or "exclusives" in tags
    pre = "PRE-ORDER" in up or "PREORDER" in up
    vt = "exclusive" if excl else "signed" if (cgc or signed) else "variant"
    kind = []
    if excl: kind.append("artist exclusive")
    if cgc: kind.append("CGC Signature Series")
    elif signed: kind.append("signed")
    if "big marvel" in tags: kind.append("Big Marvel")
    if "warehouse sale" in tags: kind.append("warehouse sale")
    m = re.match(r"(.*?)\s*#\s*(\d+)", t)
    title = re.sub(r"\s*(ARTIST EXCLUSIVE|EXCLUSIVE|PRE-ORDER|\(SIGNED\)|SIGNED SETS!?)\s*", " ", t, flags=re.I).strip(" -")
    editions = " · ".join(f"{e['edition']} {'✓' if e['available'] else '✗'}" for e in eds)
    buy = [e for e in eds if e["available"]]
    return {"id": f"{r['id']}-{p['handle']}"[:90], "series_id": cr["series_id"], "creator_id": cr["id"], "role": "store",
            "retailer_id": r["id"], "retailer_title": t, "title": title, "issue": m.group(2) if m else None,
            "publisher": p.get("vendor") if p.get("vendor") not in (None, "Stupid Fresh Mess", "skottieyoung.com") else None,
            "variant": (" · ".join(kind) or "Store listing") + (" · pre-order" if pre else ""), "variant_type": vt,
            "cover_kind": ", ".join(kind) or ptype, "cover_letter": None, "artist": cr["name"],
            "format": ("CGC slab" if cgc else "single issue") + (" (signed)" if signed and not cgc else ""),
            "price_usd": min([e["price_usd"] for e in buy] or [e["price_usd"] for e in eds]) if eds else None,
            "editions": eds, "editions_text": editions, "editions_available": len(buy),
            "release_date": None, "foc_date": None, "tag_preorder": pre,
            "listing_url": f"{r['website'].rstrip('/')}/products/{p['handle']}", "source_type": "retailer", "verification": "live",
            "image_url": (p.get("images") or [{}])[0].get("src"), "published_at": p.get("published_at")}


# ---------------------------------------------------------------------------
def label(l, retailers):
    return f"{l['title']} {l['variant']} at {retailers[l['retailer_id']]['name']}"


def merge(d, found, sources_done, cr, retailers):
    """Replace this creator's rows for the sources that ran; keep everything
    else; carry stable ids/status; write activity for new rows. Rows that vanished
    from a re-discovered source are kept with status unknown."""
    by_url = {l["listing_url"].split("?")[0].rstrip("/"): l for l in d["listings"] if l.get("series_id") == cr["series_id"]}
    out = []; added = 0
    for row in found:
        old = by_url.pop(row["listing_url"].split("?")[0].rstrip("/"), None)
        if old is None:
            add_activity(d, "new_listing", f"New {cr['name']} listing: {label(row, retailers)}" + (f" (${row['price_usd']:.2f})." if row.get('price_usd') is not None else "."), row["listing_url"], cr["series_id"], cr["id"])
            row["first_seen"] = now_iso(); added += 1
        else:
            row["id"] = old["id"]; row["first_seen"] = old.get("first_seen") or old.get("last_verified")
            for k in ("status", "last_verified", "evidence", "image_local", "preorder"):
                if k in old: row[k] = old[k]
            if old.get("editions") and row.get("editions"):
                row["_old_editions"] = old["editions"]
            if old.get("release_date") and row.get("release_date") and old["release_date"] != row["release_date"]:
                add_activity(d, "date_change", f"{label(row, retailers)}: release date changed from {old['release_date']} to {row['release_date']} (retailer listing).", row["listing_url"], cr["series_id"], cr["id"])
        out.append(row)
    for url, old in by_url.items():          # rows not found this pass
        if old.get("retailer_id") in sources_done:
            if old.get("status") != "unknown":
                old["status"] = "unknown"; old["evidence"] = "No longer returned by the retailer's catalog/search — listing may have been removed."; old["last_verified"] = now_iso()
        out.append(old)
    keep = [l for l in d["listings"] if l.get("series_id") != cr["series_id"]]
    d["listings"] = keep + out
    return added


# Re-check cadence by status. Buyable and preorder rows are the ones that can
# sell out under you, so they get the hourly pass; a sold-out back issue that
# restocks can wait a few hours to be noticed, and checking it hourly is what
# was tripping Impulse's rate limiter.
RECHECK_MIN = {"available": 55, "preorder_open": 55, "coming_soon": 55, "sold_out": 360, "unknown": 360}


def verify(d, rows, cr, retailers, budget, dry, midtown_session=None):
    done = 0
    for row in rows:
        due_in = RECHECK_MIN.get(row.get("status"), 55)
        if fresh(row.get("last_verified"), due_in) and row.get("verification") == "live" and not str(row.get("evidence", "")).startswith("Live check failed") and row.get("status") not in (None, "needs_verification"):
            continue
        if budget and done >= budget:
            continue
        done += 1
        r = retailers[row["retailer_id"]]
        old_status, old_price = row.get("status"), row.get("price_usd")
        try:
            if r.get("platform") == "midtown":
                status, price, list_price, evidence = midtown_state(midtown_session, r["website"].rstrip("/"), row["midtown_id"])
                if status == "available" and row.get("release_date") and row["release_date"] > now_iso()[:10]:
                    status = "preorder_open"
                if list_price: row["list_price_usd"] = list_price
            else:
                for attempt in range(5):   # Shopify throttles bursts: back off on 429 instead of recording a failure
                    try:
                        status, price, evidence, image = shopify_state(row["listing_url"]); break
                    except requests.HTTPError as e:
                        if e.response is not None and e.response.status_code == 429 and attempt < 4:
                            time.sleep(12 * (attempt + 1)); continue
                        raise
                if status == "available" and row.get("tag_preorder"):
                    status = "preorder_open"
                if image and not row.get("image_url"): row["image_url"] = image
        except Exception as e:
            status, price, evidence = "needs_verification", None, f"Live check failed: {e}"
        # a transition out of needs_verification is a first clean read, not a change — no alert
        if old_status and old_status not in ("needs_verification",) and old_status != status and status != "needs_verification" and row.get("verification") == "live":
            if status in ("available", "preorder_open") and old_status in ("sold_out", "unknown"):
                kind = "restock"
            elif status in ("available", "preorder_open", "sold_out"):
                kind = status
            else:
                kind = "verified"
            if kind != "verified":
                add_activity(d, kind, f"{label(row, retailers)}: {old_status} → {status}.", row["listing_url"], cr["series_id"], cr["id"])
        if price is not None and old_price is not None and abs(price - old_price) > 0.009 and old_status not in (None, "needs_verification"):
            add_activity(d, "price_change", f"{label(row, retailers)}: price changed from ${old_price:.2f} to ${price:.2f}.", row["listing_url"], cr["series_id"], cr["id"])
        if row.get("_old_editions") and row.get("editions"):
            was = {e["edition"]: e["available"] for e in row["_old_editions"]}
            for e in row["editions"]:
                if e["available"] and was.get(e["edition"]) is False:
                    add_activity(d, "restock", f"{row['title']} at {r['name']}: edition '{e['edition']}' is purchasable again (${e['price_usd']:.2f}).", row["listing_url"], cr["series_id"], cr["id"])
        row.pop("_old_editions", None)
        row.update(status=status, evidence=evidence, last_verified=now_iso(),
                   preorder="open" if status == "preorder_open" else ("closed" if status == "sold_out" else "n/a"))
        if price is not None: row["price_usd"] = price
        row.pop("tag_preorder", None)
        print(f"{row['id'][:60]:60} {status:>18} {('$%.2f' % row['price_usd']) if row.get('price_usd') is not None else '—':>8}", flush=True)
        if not dry and done % 10 == 0:
            save(d)
        time.sleep(0.8)
    return done


def main(dry=False, budget=0, sources=None, force=False, include_writer=False):
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not force and not dry and fresh(st.get("last_run"), FRESH_MIN):
        print(f"creator_watch: last full run {st['last_run']} is under {FRESH_MIN} min old — hourly gate, skipping"); return
    d = load()
    retailers = {r["id"]: r for r in d["retailers"]}
    for cr in d.get("creators", []):
        found = []; done_sources = set(); midtown_session = None
        srcs = sources or ["impulse", "midtown", "store"]
        if "impulse" in srcs:
            r = retailers[cr["impulse_retailer_id"]]
            try:
                items = impulse_discover(r, cr)
                found += [impulse_row(p, r, cr) for p in items]; done_sources.add(r["id"])
                print(f"{r['name']}: {len(items)} {cr['name']} product(s)")
            except Exception as e:
                print("ERR impulse", e, file=sys.stderr)
        if "midtown" in srcs:
            r = retailers[cr["midtown_retailer_id"]]
            try:
                key = f"midtown:{cr['id']}"; cards = cache_get(key)
                if cards is None:
                    midtown_session, cards = midtown_cards(r["website"].rstrip("/"), cr["match"]); cache_put(key, cards)
                else:
                    midtown_session = requests.Session(); midtown_session.headers.update(UA)
                m = cr["match"].lower()
                for c in cards:
                    if m in c["title"].lower():
                        found.append(midtown_row(c, r, cr, "cover"))
                    elif include_writer and any(m in w.lower() for w in c["writers"]):
                        found.append(midtown_row(c, r, cr, "writer"))
                done_sources.add(r["id"])
                print(f"{r['name']}: {len(cards)} search hit(s), {sum(1 for x in found if x['retailer_id']==r['id'])} kept")
            except Exception as e:
                print("ERR midtown", e, file=sys.stderr)
        if "store" in srcs:
            r = retailers[cr["store_retailer_id"]]
            try:
                items = store_discover(r, cr)
                found += [store_row(p, r, cr) for p in items]; done_sources.add(r["id"])
                print(f"{r['name']}: {len(items)} in-scope product(s)")
            except Exception as e:
                print("ERR store", e, file=sys.stderr)
        added = merge(d, found, done_sources, cr, retailers)
        rows = [l for l in d["listings"] if l.get("series_id") == cr["series_id"] and l.get("retailer_id") in done_sources]
        n = verify(d, rows, cr, retailers, budget, dry, midtown_session)
        print(f"{cr['name']}: {len(rows)} row(s) tracked, {added} new, {n} verified this run")
    if not dry:
        save(d)
        if not budget and not sources:
            STATE.write_text(json.dumps({"last_run": now_iso()}))


if __name__ == "__main__":
    a = sys.argv[1:]
    b = [x for x in a if x.startswith("--budget=")]
    s = [x for x in a if x.startswith("--source=")]
    main(dry="--dry" in a, budget=int(b[0].split("=")[1]) if b else 0,
         sources=s[0].split("=")[1].split(",") if s else None, force="--force" in a, include_writer="--include-writer" in a)
