#!/usr/bin/env python3
"""Download each listing's retailer cover image into covers/ so the site serves
its own copies (no hot-linking, no referrer/CSP problems). Runs in the GitHub
Action after every scan. Fetches Midnight Spider-Man and Skottie Young watch covers by
default; pass --series=msm,sy,abat to also store Absolute Batman images. Only images already linked from a retailer's own
product listing are fetched; the source URL stays in data.json."""
import hashlib, pathlib, sys, re
import requests
from common import load, save, ROOT, UA
OUT = ROOT / "covers"; OUT.mkdir(exist_ok=True)

def main():
    d = load(); n = 0
    retailers = {r["id"]: r for r in d["retailers"]}
    series = [a.split("=")[1] for a in sys.argv[1:] if a.startswith("--series=")]
    series = series[0].split(",") if series else ["msm", "sy"]   # default: Midnight Spider-Man + Skottie Young watch covers
    for l in d["listings"]:
        src = l.get("image_url")
        if not src or l.get("image_local") or l.get("series_id") not in series:
            continue
        if "cdn.shopify.com" in src and l.get("retailer_id") == "skottie-shop":
            src = re.sub(r"(\.(?:jpe?g|png|webp))(\?|$)", r"_600x\1\2", src, count=1, flags=re.I)   # request a 600px rendition, not the multi-MB original
        ext = (re.search(r"\.(jpe?g|png|webp)(?:\?|$)", src, re.I) or [None, "jpg"])[1].lower()
        name = f"{l['id']}.{ext}"
        path = OUT / name
        try:
            ref = re.match(r"https?://[^/]+", (retailers.get(l.get("retailer_id")) or {}).get("website") or src).group(0) + "/"
            r = requests.get(src, headers={**UA, "Referer": ref}, timeout=30)
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
                print("skip", l["id"], r.status_code, file=sys.stderr); continue
            path.write_bytes(r.content); l["image_local"] = f"covers/{name}"; n += 1
            if l.get("series_id") == "sy":     # creator-watch copies are display-only: cap at 600px tall, JPEG
                try:
                    from PIL import Image
                    im = Image.open(path); im.load(); im = im.convert("RGB")
                    if im.size[1] > 600: im = im.resize((round(im.size[0] * 600 / im.size[1]), 600))
                    jp = path.with_suffix(".jpg"); im.save(jp, "JPEG", quality=82, optimize=True)
                    if jp != path: path.unlink()
                    l["image_local"] = f"covers/{jp.name}"
                except Exception as e:
                    print("resize skipped", l["id"], e, file=sys.stderr)
        except Exception as e:
            print("ERR", l["id"], e, file=sys.stderr)
    save(d); print(f"fetched {n} cover(s)")

if __name__ == "__main__":
    main()
