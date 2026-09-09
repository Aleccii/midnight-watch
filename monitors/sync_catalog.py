#!/usr/bin/env python3
"""Catalog sync — enumerate every Midnight Spider-Man / Absolute Batman product
on a Shopify retailer, then live-verify each one.

Runs before check_availability.py so newly listed covers, printings, specials
and signed editions are picked up on the same pass. Discovery walks the store's
public /collections/all/products.json (paged, 250 per page) — never Shop.app,
search snippets, or cached pages. Every product found is then verified with the
same two-signal rule used by the 30-minute scan (product JSON *and* the visible
add-to-cart control must agree).

Series membership is decided by the product title, not the URL handle: Impulse
handles are sometimes stale placeholders ("tbd-artist"), the titles are current.

  Midnight Spider-Man  -> titles starting "MIDNIGHT SPIDER-MAN"; the poster is
                          merchandise and is skipped.
  Absolute Batman      -> titles starting "ABSOLUTE BATMAN" that are issues,
                          printings, annuals, one-shots, Ark-M specials, or the
                          series' own collected editions. DC's "Absolute Edition"
                          hardcovers of classic Batman stories (Arkham Asylum,
                          Long Halloween, Court of Owls, ...) share the prefix but
                          are NOT this series and are excluded, as are statues
                          and parody books.
"""
import re, sys, json, time
import requests
from common import load, save, add_activity, now_iso, UA
from check_availability import shopify_state
import datetime, pathlib
CACHE = pathlib.Path(__file__).with_name(".catalog_cache.json")   # discovery cache (1 h) so an interrupted run can resume
FRESH_MIN = 60

TIMEOUT = 30
# Titles that share the "ABSOLUTE BATMAN" prefix but are not the Snyder/Dragotta series.
ABAT_EXCLUDE = re.compile(r"arkham asylum|dark victory|death of the family|haunted knight|incorporated|black mirror|court of owls|dark knight the master race|long halloween|three jokers|zero year|and son|statue|trumps", re.I)

# Cover letters Marvel assigned where Impulse's title omits the letter.
MSM_LETTER_FIX = {
    ("1", "1:25 JEEHYUNG LEE"): "E",
    ("1", "1:50 PEACH MOMOKO"): "G",
    ("1", "1:100 COVER J"): "J",
}
MSM_ARTIST_A = {"1": "Steve Beach"}   # Cover A artist per Marvel's solicit; Impulse's title omits it

def get(url, **kw):
    """GET with polite pacing and 429 backoff (Shopify throttles bursts)."""
    for attempt in range(6):
        r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
        if r.status_code == 429:
            time.sleep(8 * (attempt + 1)); continue
        return r
    return r

def walk(base, collections, queries):
    """Enumerate candidate products: every product in the named collections (paged
    250 at a time) plus Shopify's predictive-search API for each query. Union by
    handle. /collections/all is walked first (up to 150 pages of 250); the named
    collections and predictive search catch anything the store hides from it."""
    seen = {}
    for coll in ["all"] + list(collections):
        page = 1
        while page <= 150:
            r = get(f"{base}/collections/{coll}/products.json", params={"limit": 250, "page": page})
            if r.status_code != 200:
                break
            ps = r.json().get("products", [])
            if not ps:
                break
            for p in ps:
                seen.setdefault(p["handle"], p)
            page += 1
            time.sleep(0.25)
    for q in queries:
        r = get(f"{base}/search/suggest.json", params={"q": q, "resources[type]": "product", "resources[limit]": 50})
        if r.status_code != 200:
            continue
        for p in r.json().get("resources", {}).get("results", {}).get("products", []):
            if p["handle"] not in seen:
                pr = get(f"{base}/products/{p['handle']}.json")
                if pr.status_code == 200:
                    seen[p["handle"]] = pr.json()["product"]
                time.sleep(0.4)
    return list(seen.values())

def tag_date(tags, key):
    m = re.search(key + r"\s*(\d{1,2})-(\d{1,2})-(\d{2})", ", ".join(tags), re.I)
    return f"20{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None

def classify(title):
    """Return (series_id, fields) or (None, None)."""
    t = re.sub(r"\s+", " ", title).strip()
    up = t.upper()
    signed = re.search(r"\(signed by ([^)]+)\)", t, re.I)
    core = re.sub(r"\s*\(signed by [^)]+\)", "", t, flags=re.I)
    if up.startswith("MIDNIGHT SPIDER-MAN"):
        if "POSTER" in up:
            return None, None
        sid = "msm"
        m = re.match(r"MIDNIGHT SPIDER-MAN #(\d+)\s*(.*)", core, re.I)
        issue, rest = m.group(1), m.group(2).strip()
        letter = (re.search(r"\bCOVER ([A-Z])\b", rest, re.I) or [None, None])[1]
        for (iss, frag), L in MSM_LETTER_FIX.items():
            if iss == issue and frag in rest.upper():
                letter = L
        ratio = (re.search(r"\b1:(\d+)\b", rest) or [None, None])[1]
        virgin = bool(re.search(r"\bvirgin\b", rest, re.I))
        name = re.sub(r"^\s*(?:1:\d+\s*)?(?:COVER [A-Z]\s*)?(?:1:\d+\s*)?", "", rest, flags=re.I)
        name = re.sub(r"\bVARIANT\b", "", name, flags=re.I).strip(" -")
        artist = None
        am = re.match(r"([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){0,2}?)\s+(MIDNIGHT|3-PART|VIRGIN|$)", name)
        known = ["RYAN STEGMAN", "PEACH MOMOKO", "SKAN", "JEEHYUNG LEE", "CLAYTON CRAIN", "ERIC CANETE", "INHYUK LEE", "LUCAS WERNECK", "ITO", "SIMONE BIANCHI"]
        for k in known:
            if k in name.upper():
                artist = k.title().replace("Jeehyung", "JeeHyung").replace("Inhyuk", "InHyuk"); break
        if letter == "A" and not artist:
            artist = MSM_ARTIST_A.get(issue, "Not stated on listing")
        cover_name = "Regular" if letter == "A" else (
            "Midnight Bloodbath" if "BLOODBATH" in name.upper() else
            "Midnight Special" + (" (virgin)" if virgin else "") if "MIDNIGHT SPECIAL" in name.upper() else
            "Midnight Horror Homage" if "HORROR HOMAGE" in name.upper() else
            "Midnight Gallery" if "GALLERY" in name.upper() else
            "3-part connecting" if "CONNECTING" in name.upper() else
            ("Virgin" if virgin else "Variant"))
        kind = (f"1:{ratio} incentive" if ratio else "Trade dress") + (" · virgin" if virgin and ratio else "")
        vtype = "signed" if signed else ("incentive" if ratio else ("virgin" if virgin else ("regular" if letter == "A" else "variant")))
        variant = f"Cover {letter} — {artist or '?'} · {cover_name}" + (f" ({kind})" if ratio else "") + (", signed by PKJ" if signed else "")
        return sid, dict(issue=issue, title=f"Midnight Spider-Man #{issue}", variant=variant, variant_type=vtype,
                         cover_letter=letter, cover_name=cover_name, artist=artist, cover_kind=kind,
                         format="single issue" + (" (signed)" if signed else ""), signed_by=signed.group(1) if signed else None)
    if up.startswith("ABSOLUTE BATMAN"):
        if ABAT_EXCLUDE.search(core):
            return None, None
        sid = "abat"
        # collected editions
        cm = re.match(r"ABSOLUTE BATMAN (DELUXE EDITION HC|THE COVERS HC|HC|TP)\s*(VOL \d+)?\s*(.*)", core, re.I)
        if cm and not re.search(r"#\d", core):
            fmt = "collected edition (hardcover)" if "HC" in cm.group(1).upper() else "collected edition (trade paperback)"
            vol = (cm.group(2) or "").title()
            t2 = re.sub(r"\s+", " ", f"Absolute Batman {cm.group(1).title()} {vol}").strip().replace("Hc", "HC").replace("Tp", "TP")
            return sid, dict(issue=None, title=t2, variant=(cm.group(3) or "").title().strip() or "Standard edition" + (", signed by " + signed.group(1) if signed else ""),
                             variant_type="signed" if signed else "collected", format=fmt + (" (signed)" if signed else ""), signed_by=signed.group(1) if signed else None)
        m = re.match(r"ABSOLUTE BATMAN\s*((?:2025 ANNUAL|ARK-M SPECIAL|BEYOND ARK-M|NOIR EDITION)?)\s*#(\d+)\s*(\(ONE SHOT\))?\s*(.*)", core, re.I)
        if not m:
            return None, None
        sub, issue, rest = m.group(1).strip().title().replace("Ark-M", "Ark-M"), m.group(2), m.group(4).strip()
        title = "Absolute Batman" + (f" {sub}" if sub else "") + f" #{issue}"
        printing = re.search(r"\b(Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Eigth|Ninth|Tenth|Eleventh|Twelfth|\d+(?:st|nd|rd|th))\s+Print", rest, re.I)
        letter = (re.search(r"\bCVR ([A-Z])\b", rest, re.I) or [None, None])[1]
        ratio = (re.search(r"\b1:(\d+)\b", rest) or [None, None])[1]
        foil = bool(re.search(r"\bfoil\b", rest, re.I)); virgin = bool(re.search(r"\bvirgin\b", rest, re.I))
        blank = bool(re.search(r"\bblank\b", rest, re.I)); ashcan = "ASHCAN" in rest.upper()
        clean = re.sub(r"\((?:1st Print|1st print full print run flaw|\d+(?:st|nd|rd|th) Printing)\)", "", rest, flags=re.I)
        clean = re.sub(r"\b(Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Eigth|Ninth|Tenth|Eleventh|Twelfth) Printing\b", "", clean, flags=re.I).strip()
        variant = re.sub(r"\s+", " ", clean.title()).replace("Cvr ", "Cover ").replace("Inc ", "").replace(" Var", " variant").replace("Dc ", "DC ").replace("Nycc", "NYCC").strip() or "Cover A"
        if printing:
            variant = f"{printing.group(1).title().replace('Eigth','Eighth').replace('2Nd','Second')} printing — {variant}"
        variant += (", signed by " + signed.group(1)) if signed else ""
        vtype = ("signed" if signed else "special" if (sub or ashcan) and not printing else "reprint" if printing else
                 "incentive" if ratio else "virgin" if virgin else "foil" if foil else "blank" if blank else "regular" if (letter in (None, "A")) else "variant")
        return sid, dict(issue=issue, title=title, variant=variant, variant_type=vtype, cover_letter=letter,
                         artist=None, cover_kind=(f"1:{ratio} incentive" if ratio else "foil" if foil else "card stock" if "CARD STOCK" in rest.upper() else "regular"),
                         format=("one-shot" if m.group(3) else "single issue") + (" (signed)" if signed else ""), signed_by=signed.group(1) if signed else None,
                         printing=printing.group(1).title().replace("Eigth","Eighth").replace("2Nd","Second") if printing else "First")
    return None, None

def build(p, r):
    sid, f = classify(p["title"])
    if not sid:
        return None
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    v = p["variants"][0]
    url = f"{r['website'].rstrip('/')}/products/{p['handle']}"
    return {"id": f"{r['id']}-{p['handle']}"[:90], "series_id": sid, "retailer_id": r["id"], "retailer_title": p["title"],
            "price_usd": float(v["price"]), "list_price_usd": float(v["compare_at_price"]) if v.get("compare_at_price") else None,
            "sku": v.get("sku"), "upc": v.get("barcode"),
            "release_date": tag_date(tags, "New Release"), "foc_date": tag_date(tags, "FOC"),
            "listing_url": url, "source_type": "retailer", "verification": "live",
            "image_url": (p.get("images") or [{}])[0].get("src"),
            "json_available": any(x.get("available") for x in p["variants"]),
            "tag_preorder": any(t.lower() == "preorder" for t in tags), **f}

def main(dry=False, budget=0, discover_only=False):
    d = load()
    by_url = {l["listing_url"].split("?")[0].rstrip("/"): l for l in d["listings"]}
    added = 0; found = []
    for r in d["retailers"]:
        if r.get("platform") != "shopify" or not r.get("website", "").startswith("http"):
            continue
        cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        c = cache.get(r["id"])
        if c and (datetime.datetime.now() - datetime.datetime.fromisoformat(c["at"])).total_seconds() < FRESH_MIN * 60:
            products = c["products"]
        else:
            try:
                products = walk(r["website"].rstrip("/"), r.get("catalog_collections") or [], r.get("catalog_queries") or ["midnight spider-man", "absolute batman"])
            except Exception as e:
                print("ERR walk", r["id"], e, file=sys.stderr); continue
            products = [p for p in products if classify(p["title"])[0]]
            cache[r["id"]] = {"at": datetime.datetime.now().isoformat(), "products": products}
            CACHE.write_text(json.dumps(cache))
        if discover_only:
            print(f"{r['name']}: {len(products)} series products cached"); continue
        print(f"{r['name']}: {len(products)} products in catalog")
        for p in products:
            row = build(p, r)
            if not row:
                continue
            found.append(row)
            old = by_url.get(row["listing_url"])
            if old is None:
                add_activity(d, "new_listing", f"New listing: {row['title']} {row['variant']} at {r['name']} (${row['price_usd']:.2f}).", row["listing_url"], row["series_id"])
                added += 1
            else:
                # keep the existing id so links/activity stay stable; drop stale snapshot fields
                row["id"] = old["id"]
                for k in ("status", "last_verified", "evidence", "image_local", "signed_price_usd"):
                    if k in old: row[k] = old[k]
                if old.get("release_date") and row["release_date"] and old["release_date"] != row["release_date"]:
                    add_activity(d, "date_change", f"{row['title']} {row['variant']} at {r['name']}: release date changed from {old['release_date']} to {row['release_date']} (retailer tag).", row["listing_url"], row["series_id"])
    # replace old rows for these retailers with the synced set; keep everything else
    synced_ret = {row["retailer_id"] for row in found}
    keep = [l for l in d["listings"] if l.get("retailer_id") not in synced_ret]
    d["listings"] = keep + found
    # live-verify every synced row (JSON + visible add-to-cart), sequentially with pacing
    done = 0
    for row in found:
        lv = row.get("last_verified")
        if lv and row.get("verification") == "live" and not str(row.get("evidence", "")).startswith("Live check failed") and \
           (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(lv)).total_seconds() < FRESH_MIN * 60:
            row.pop("json_available", None); row.pop("tag_preorder", None); continue   # verified within the hour (resume)
        if budget and done >= budget:
            row.pop("json_available", None); row.pop("tag_preorder", None); continue
        done += 1
        for attempt in range(4):
            try:
                status, price, evidence, image = shopify_state(row["listing_url"]); break
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 429 and attempt < 3:
                    time.sleep(10 * (attempt + 1)); continue
                status, price, evidence, image = "needs_verification", None, f"Live check failed: {e}", None; break
            except Exception as e:
                status, price, evidence, image = "needs_verification", None, f"Live check failed: {e}", None; break
        if status == "available" and row.get("tag_preorder"):
            status = "preorder_open"
        old_status = row.get("status")
        if old_status and old_status != status and status != "needs_verification" and row.get("verification") == "live":
            kind = "restock" if status in ("available", "preorder_open") and old_status == "sold_out" else status if status in ("available", "preorder_open", "sold_out") else "verified"
            add_activity(d, kind, f"{row['title']} {row['variant']} at Impulse Creations: {old_status} → {status}.", row["listing_url"], row["series_id"])
        row.update(status=status, evidence=evidence, last_verified=now_iso(),
                   preorder="open" if status == "preorder_open" else ("closed" if status == "sold_out" else "n/a"))
        if price is not None: row["price_usd"] = price
        if image and not row.get("image_url"): row["image_url"] = image
        row.pop("json_available", None); row.pop("tag_preorder", None)
        print(f"{row['id'][:70]:70} {status:>18} ${row['price_usd']:.2f}", flush=True)
        if not dry and done % 10 == 0:
            save(d)
        time.sleep(0.3)
    print(f"synced {len(found)} listing(s), {added} new")
    if not dry:
        save(d)

if __name__ == "__main__":
    b = [a for a in sys.argv[1:] if a.startswith("--budget=")]
    main(dry="--dry" in sys.argv, budget=int(b[0].split("=")[1]) if b else 0, discover_only="--discover" in sys.argv)
