#!/usr/bin/env python3
"""Download each listing's retailer cover image into covers/ so the site serves
its own copies (no hot-linking, no referrer/CSP problems). Runs in the GitHub
Action after every scan. Fetches Midnight Spider-Man covers by default;
pass --series=msm,abat to also store Absolute Batman images. Only images already linked from a retailer's own
product listing are fetched; the source URL stays in data.json."""
import hashlib, pathlib, sys, re
import requests
from common import load, save, ROOT, UA
OUT = ROOT / "covers"; OUT.mkdir(exist_ok=True)

def main():
    d = load(); n = 0
    series = [a.split("=")[1] for a in sys.argv[1:] if a.startswith("--series=")]
    series = series[0].split(",") if series else ["msm"]   # default: Midnight Spider-Man covers only
    for l in d["listings"]:
        src = l.get("image_url")
        if not src or l.get("image_local") or l.get("series_id") not in series:
            continue
        ext = (re.search(r"\.(jpe?g|png|webp)(?:\?|$)", src, re.I) or [None, "jpg"])[1].lower()
        name = f"{l['id']}.{ext}"
        path = OUT / name
        try:
            r = requests.get(src, headers={**UA, "Referer": "https://impulsecreations.com/"}, timeout=30)
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
                print("skip", l["id"], r.status_code, file=sys.stderr); continue
            path.write_bytes(r.content); l["image_local"] = f"covers/{name}"; n += 1
        except Exception as e:
            print("ERR", l["id"], e, file=sys.stderr)
    save(d); print(f"fetched {n} cover(s)")

if __name__ == "__main__":
    main()
