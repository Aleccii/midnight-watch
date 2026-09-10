#!/usr/bin/env python3
"""Daily appearance monitor — PKJ (Oklahoma / Kansas) and Skottie Young.

Two kinds of watch target:

  PKJ (WATCH below) — fetches a fixed set of official/reference pages, extracts
  any sentences that mention Oklahoma, Kansas or a Tulsa-metro city, hashes
  them and diffs against the last run. Only regional mentions matter here.

  Creator watch (data.json creators[].appearance_watch) — for Skottie Young:
    mode "full"     hash the page's main text (his own Appearances page and the
                    Planet Comicon guest page): any edit is an alert.
    mode "upcoming" hash only lines that mention the current or next year
                    (FanCons history page — old rows must not alert).
    mode "name"     hash the presence/absence of the creator's name on a
                    convention guest list (Baltimore etc.).

A change produces ONE activity entry with the source link and the new snippet
(appearance_added, tagged with creator_id) for human review; it never invents a
dated appearance because it cannot verify dates or venues. State lives in
monitors/.appearance_state.json.
"""
import re, sys, hashlib, json, datetime
import requests
from common import load, save, add_activity, now_iso, UA, ROOT

WATCH = [
    ("Impulse Creations — PKJ signing page", "https://impulsecreations.com/collections/phillip-kennedy-johnson-midnight-spidrman-1-launch-signing"),
    ("FanCons — PKJ guest history", "https://fancons.com/guests/bio/7880/phillip-kennedy-johnson"),
    ("Baltimore Comic-Con — PKJ guest page", "https://baltimorecomiccon.com/guest/phillip-kennedy-johnson/"),
    ("PKJ official site", "https://phillipkennedyjohnson.com/"),
]
REGION_WORDS = r"Oklahoma|Kansas|Tulsa|Wichita|Broken Arrow|Owasso|Jenks|Bixby|Sand Springs|Sapulpa|Catoosa|Oklahoma City|OKC"
STATE = ROOT / "monitors" / ".appearance_state.json"
CHROME = re.compile(r"^(home|menu|cart|shop now|log in|search|newsletter|instagram|x|youtube|back to top|quick view|sold out|\+ add to cart|subtotal|check out|view cart|update cart|accessibility|contact|shop|follow us|skip to content|loading extension\.\.\.|©.*|\(|\)|0|items|your cart is currently empty|gift message|unit price:|/|per|previous panel|main menu|taxes and shipping calculated at checkout|−|\+|shopping cart|the skottie shop|books & graphic novels|cgc signature series|merch|original art|comics & exclusive variants|prints|stickers|digital books|gift cards|warehouse sale|auctions|my catalog|about|appearances|faq and shop policies|view the skottie shop|– stupid fresh mess|:|you may also like\.\.\.)$", re.I)


def text_of(html):
    body = re.search(r"<main.*?</main>", html, re.S)
    t = body.group(0) if body else html
    t = re.sub(r"<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "\n", t)
    t = re.sub(r"&nbsp;", " ", t)
    return re.sub(r"[ \t]+", " ", t)


def regional_snippets(html):
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    return sorted({m.group(0)[:240] for m in re.finditer(r"[^.]{0,120}(?:%s)[^.]{0,120}" % REGION_WORDS, text, re.I)})


def creator_snippets(html, mode, name):
    t = text_of(html)
    if mode == "full":
        lines = [x.strip() for x in t.split("\n") if x.strip()]
        return [l for l in lines if not CHROME.match(l)]
    if mode == "upcoming":
        y = datetime.date.today().year
        return sorted({l.strip()[:200] for l in t.split("\n") if str(y) in l or str(y + 1) in l})
    if mode == "name":
        return [f"{name} listed" if name.lower() in t.lower() else f"{name} not listed"]
    return regional_snippets(html)


def fetch(url):
    r = requests.get(url, headers=UA, timeout=25)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.text


def main(dry=False):
    d = load()
    prev = json.loads(STATE.read_text()) if STATE.exists() else {}
    cur = dict(prev)
    changes = 0
    targets = [(label, url, "regional", None, None) for label, url in WATCH]
    for cr in d.get("creators", []):
        for w in cr.get("appearance_watch", []):
            targets.append((w["label"], w["url"], w.get("mode", "full"), cr["id"], cr["name"]))
    for label, url, mode, creator_id, name in targets:
        try:
            html = fetch(url)
        except Exception as e:
            print("ERR", label, e, file=sys.stderr); continue
        snips = creator_snippets(html, mode, name) if mode != "regional" else regional_snippets(html)
        h = hashlib.sha256("\n".join(snips).encode()).hexdigest()
        cur[url] = h
        if prev.get(url) != h:
            if url in prev:
                who = f"{name}: " if name else "PKJ: "
                add_activity(d, "appearance_added", who + f"{label} changed — " + ("; ".join(snips)[:320] if snips else "watched text removed") + " — review before trusting.", url, None, creator_id)
                changes += 1
            print(f"CHANGED {label}: {len(snips)} watched snippet(s)")
        else:
            print(f"same    {label}")
    watched = {u for _, u, *_ in targets}
    for a in d["appearances"]:
        if a.get("url") in watched:
            a["last_verified"] = now_iso()
    if not dry:
        STATE.write_text(json.dumps(cur, indent=2)); save(d)
    print(f"{changes} change(s)")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
