#!/usr/bin/env python3
"""Comic availability monitor — runs every 30 minutes.

Reads every listing in data.json, re-checks the retailer's US storefront, and
records an activity entry ONLY when something meaningful changed:
  became purchasable / preorder opened / restock / sold out / price change /
  release-date change. Unchanged rows just get a fresh last_verified.

Verification method by platform:
  shopify  -> GET <product_url>.js  (variants[].available + price) and
              GET the HTML to read the submit button inside the /cart/add form.
              Both must agree; if they disagree, status = needs_verification.
  other    -> HTML heuristics only; result is needs_verification unless an
              explicit enabled add-to-cart control is found.
Region rule: requests are made with no locale cookie so Shopify serves the
US (USD) storefront. Shop.app, foreign-currency, cached, or aggregator pages
are never used as evidence.
"""
import re, sys, json
import requests
from common import load, save, add_activity, now_iso, UA

TIMEOUT = 20

def shopify_state(url):
    """Return (status, price, evidence, image) for a Shopify product page.

    Signal 1 — storefront JS endpoint <product>.js: variants[].available and price.
               (The .json endpoint on some stores, Impulse included, omits `available`.)
    Signal 2 — the real purchase control: the submit button inside the
               <form action="/cart/add"> on the HTML page. Disabled / "Sold out" =>
               not purchasable; "Pre-order" / "Add to cart" without `disabled` =>
               purchasable. Theme-template buttons inside <script> blocks are ignored.
    Both signals must agree, otherwise status = needs_verification.
    """
    base = url.split("?")[0].rstrip("/")
    js = requests.get(base + ".js", headers=UA, timeout=TIMEOUT)
    if js.status_code == 404:
        return "unknown", None, "Product endpoint 404 — listing may have been removed", None
    js.raise_for_status()
    p = js.json()
    variants = p.get("variants", [])
    any_avail = bool(p.get("available")) or any(v.get("available") for v in variants)
    price = min(v["price"] for v in variants) / 100 if variants else None
    image = None
    imgs = p.get("images") or []
    if imgs:
        image = imgs[0] if imgs[0].startswith("http") else "https:" + imgs[0]

    html = requests.get(base, headers=UA, timeout=TIMEOUT).text
    i = html.find('action="/cart/add"')
    seg = re.sub(r"\s+", " ", html[i:i + 8000]) if i >= 0 else ""
    btn = re.search(r'<button[^>]*type="submit"[^>]*>(.{0,1500}?)<\/button>', seg, re.I | re.S)
    btn_tag = btn.group(0) if btn else ""
    btn_text = re.sub(r"<[^>]+>", " ", btn.group(1)).strip() if btn else ""
    disabled = bool(re.search(r"\bdisabled\b", btn_tag.split(">")[0], re.I))
    visible_sold_out = bool(re.search(r"sold out", btn_text, re.I))
    visible_add = bool(btn) and not disabled and bool(re.search(r"add to cart|pre-?order|buy", btn_text, re.I))
    preorder = bool(re.search(r"pre-?order", btn_text + btn_tag, re.I))

    if any_avail and visible_add:
        return ("preorder_open" if preorder else "available"), price, f"Storefront JS available=true and enabled '{btn_text}' button in the /cart/add form", image
    if not any_avail and (disabled or visible_sold_out or not btn):
        return "sold_out", price, f"Storefront JS available=false and {'disabled' if disabled else 'no enabled'} purchase button ('{btn_text or 'none'}')", image
    return "needs_verification", price, f"Contradictory signals (js_available={any_avail}, button='{btn_text}', disabled={disabled})", image

def generic_state(url):
    html = requests.get(url, headers=UA, timeout=TIMEOUT).text
    if re.search(r"sold\s*out|out of stock", html, re.I):
        return "sold_out", None, "Page text says sold out / out of stock", None
    if re.search(r'<button[^>]*add[- ]to[- ]cart[^>]*>', html, re.I) and not re.search(r'add[- ]to[- ]cart[^>]*disabled', html, re.I):
        return "available", None, "Enabled add-to-cart button found (heuristic)", None
    return "needs_verification", None, "No unambiguous purchase control found", None

def main(dry=False):
    d = load()
    retailers = {r["id"]: r for r in d["retailers"]}
    changes = 0
    for l in d["listings"]:
        if l.get("verification") == "demo" or not l.get("listing_url") or not l.get("retailer_id"):
            continue  # demo rows and aggregator rows are not auto-checked
        r = retailers[l["retailer_id"]]
        if "/products/" not in l["listing_url"]:
            continue  # collection links are not product listings
        try:
            fn = shopify_state if r.get("platform") == "shopify" else generic_state
            status, price, evidence, image = fn(l["listing_url"])
        except Exception as e:
            print("ERR", l["id"], e, file=sys.stderr)
            continue

        old = l["status"]; old_price = l.get("price_usd")
        label = f"{l['title']} {l['variant']} at {r['name']}"
        if status != old:
            kind = {"available": "available", "preorder_open": "preorder_open", "sold_out": "sold_out"}.get(status, "verified")
            if status in ("available", "preorder_open") and old in ("sold_out",):
                kind = "restock"
            if status != "needs_verification":
                add_activity(d, kind, f"{label}: {old} → {status}.", l["listing_url"], l["series_id"]); changes += 1
        if price is not None and old_price is not None and abs(price - old_price) > 0.009:
            add_activity(d, "price_change", f"{label}: price changed from ${old_price:.2f} to ${price:.2f}.", l["listing_url"], l["series_id"]); changes += 1
        if image and not l.get("image_url"):
            l["image_url"] = image
        l.update(status=status, price_usd=price if price is not None else old_price,
                 last_verified=now_iso(), verification="live", evidence=evidence,
                 preorder="open" if status == "preorder_open" else ("closed" if status == "sold_out" else l.get("preorder")))
        print(f"{l['id']:28} {old:>18} -> {status}")
    if not dry:
        save(d)
    print(f"{changes} meaningful change(s)")

if __name__ == "__main__":
    main(dry="--dry" in sys.argv)


# ---------------------------------------------------------------------------
# Catalog discovery: find NEW product listings on retailers that expose a
# searchable web catalog (catalog_search_url). Anything matching the tracked
# series is added as a needs_verification row; the regular pass then verifies
# it on the same run. Never marks anything available by itself.
# ---------------------------------------------------------------------------
SERIES_PATTERNS = {"msm": re.compile(r"midnight[- ]spider[- ]man", re.I), "abat": re.compile(r"absolute[- ]batman", re.I)}

def discover(d):
    known = {l["listing_url"].split("?")[0].rstrip("/") for l in d["listings"]}
    added = 0
    for r in d["retailers"]:
        url = r.get("catalog_search_url")
        if not url:
            continue
        for sid, pat in SERIES_PATTERNS.items():
            q = url.replace("midnight+spider-man", pat.pattern.replace("[- ]", "+").replace("(?i)", "") if sid == "abat" else "midnight+spider-man")
            try:
                html = requests.get(q, headers=UA, timeout=TIMEOUT).text
            except Exception as e:
                print("ERR discover", r["id"], e, file=sys.stderr); continue
            base = re.match(r"https?://[^/]+", q).group(0)
            for m in re.finditer(r'href="([^"]*/products/([^"?#]+))"', html):
                handle = m.group(2)
                if not pat.search(handle.replace("-", " ")):
                    continue
                full = (m.group(1) if m.group(1).startswith("http") else base + m.group(1)).rstrip("/")
                if full in known:
                    continue
                known.add(full)
                d["listings"].append({
                    "id": f"{r['id']}-{handle[:40]}", "series_id": sid,
                    "issue": (re.search(r"-(\d+)(?:-|$)", handle) or [None, "?"])[1],
                    "title": handle.replace("-", " ").title(), "variant": "auto-discovered — verify cover details",
                    "variant_type": "variant", "retailer_id": r["id"], "format": "single issue",
                    "price_usd": None, "status": "needs_verification", "preorder": "unknown",
                    "release_date": None, "listing_url": full, "last_verified": None,
                    "verification": "live", "source_type": "retailer", "image_url": None,
                    "evidence": "Discovered by catalog search; awaiting first purchase-control check."})
                add_activity(d, "new_listing", f"New listing found at {r['name']}: {handle.replace('-', ' ')}", full, sid)
                added += 1
    print(f"discovery: {added} new listing(s)")

_main = main
def main(dry=False):
    d = load(); discover(d)
    if not dry: save(d)
    _main(dry)
