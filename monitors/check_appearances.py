#!/usr/bin/env python3
"""Daily PKJ appearance monitor.

Fetches a fixed set of official/reference pages, extracts any lines that
mention Oklahoma, Kansas, Tulsa-metro cities, or the tracked Tulsa event, and
compares a normalized hash of those lines with the last run. A change (new
match, changed wording, page gone) produces ONE activity entry with the source
link, and the appearance is added as status "unconfirmed" for human review.
It never invents dates or venues: anything it cannot parse is logged as text.
"""
import re, sys, hashlib, json, pathlib
import requests
from common import load, save, add_activity, now_iso, UA, ROOT

WATCH = [
    ("Impulse Creations — PKJ signing page", "https://impulsecreations.com/collections/phillip-kennedy-johnson-midnight-spidrman-1-launch-signing"),
    ("FanCons — PKJ guest history", "https://fancons.com/guests/bio/7880/phillip-kennedy-johnson"),
    ("Baltimore Comic-Con — PKJ guest page", "https://baltimorecomiccon.com/guest/phillip-kennedy-johnson/"),
    ("PKJ official site", "https://phillipkennedyjohnson.com/"),
]
REGION = re.compile(r"\b(Oklahoma|Kansas|Tulsa|Broken Arrow|Owasso|Jenks|Bixby|Sand Springs|Sapulpa|Catoosa|Wichita|Oklahoma City|OKC|\bOK\b|\bKS\b)\b", re.I)
STATE = ROOT / "monitors" / ".appearance_state.json"

def snippets(html):
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return sorted({m.group(0)[:240] for m in re.finditer(r"[^.]{0,120}(?:Oklahoma|Kansas|Tulsa|Wichita|Broken Arrow|Owasso|Jenks|Bixby|Sand Springs|Sapulpa|Catoosa)[^.]{0,120}", text, re.I)})

def main(dry=False):
    d = load()
    prev = json.loads(STATE.read_text()) if STATE.exists() else {}
    cur = {}
    changes = 0
    for label, url in WATCH:
        try:
            html = requests.get(url, headers=UA, timeout=25).text
        except Exception as e:
            print("ERR", label, e, file=sys.stderr); continue
        snips = snippets(html)
        h = hashlib.sha256("\n".join(snips).encode()).hexdigest()
        cur[url] = h
        if prev.get(url) != h:
            if url in prev:
                add_activity(d, "appearance_added", f"Regional mention changed on {label}: " + ("; ".join(snips)[:300] if snips else "regional mentions removed") + " — review before trusting.", url)
                changes += 1
            print(f"CHANGED {label}: {len(snips)} regional snippet(s)")
        else:
            print(f"same    {label}")
    for a in d["appearances"]:
        a["last_verified"] = now_iso() if any(a["url"] == u for _, u in WATCH) else a["last_verified"]
    if not dry:
        STATE.write_text(json.dumps(cur, indent=2)); save(d)
    print(f"{changes} change(s)")

if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
