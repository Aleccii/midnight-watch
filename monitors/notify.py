#!/usr/bin/env python3
"""Send new activity-feed entries to Telegram after each scan.

Runs after build.py in the GitHub Action. Reads data.json, finds activity
entries newer than the last one it sent (tracked in .notify_state.json), and
posts each as a Telegram message. The activity feed only gets an entry for a
meaningful change (new preorder, restock, sold out, new listing, price or date
change, new appearance), so this never alerts for things already known. The
hourly Skottie Young watch (creator_watch.py) writes to the same feed, so its
new covers, drops, restocks and appearance changes arrive here too, prefixed
"🎨 Skottie Young".

Configuration — two GitHub repository secrets (Settings → Secrets and
variables → Actions):
  TELEGRAM_BOT_TOKEN   the token @BotFather gives you when you create a bot
  TELEGRAM_CHAT_ID     the chat to post to (your user id, or a group id)

If either is missing the script prints a note and exits 0, so a scan never
fails because alerts are not configured.

First run: sends a single "connected" message and records the current newest
entry as the baseline rather than replaying the whole feed.
"""
import json, os, pathlib, sys, html
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data.json"
STATE = pathlib.Path(__file__).with_name(".notify_state.json")
SITE = os.environ.get("SITE_URL", "").rstrip("/")

ICON = {"preorder_open": "🟦", "available": "🟩", "restock": "🟩", "sold_out": "⬜", "new_listing": "🆕",
        "new_issue": "🆕", "price_change": "💲", "date_change": "📅", "appearance": "📍", "appearance_added": "📍",
        "verified": "🔎", "watch_started": "👁"}
# Creator-watch entries carry creator_id; they are prefixed so a Skottie Young drop is
# distinguishable from a Midnight Spider-Man / Absolute Batman change at a glance.
CREATOR_TAG = {"skottie-young": "🎨 <b>Skottie Young</b> · "}

def send(token, chat_id, text):
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
                      timeout=30)
    if r.status_code != 200:
        print("Telegram error:", r.status_code, r.text[:200], file=sys.stderr)
    return r.status_code == 200

def main():
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("notify: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping alerts"); return
    d = json.loads(DATA.read_text())
    acts = sorted(d.get("activity", []), key=lambda a: a["detected_at"], reverse=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    last = state.get("last_sent_at")
    if not last:
        n = len(d.get("listings", []))
        send(token, chat_id, f"🛡 <b>KNIGHTWATCH BOT connected.</b>\nTracking {n} listings. You'll get a message here whenever something meaningful changes." + (f"\n{SITE}" if SITE else ""))
        STATE.write_text(json.dumps({"last_sent_at": acts[0]["detected_at"] if acts else ""}))
        return
    new = [a for a in reversed(acts) if a["detected_at"] > last]
    sent = 0
    for a in new:
        icon = ICON.get(a.get("type"), "•")
        text = f"{icon} {CREATOR_TAG.get(a.get('creator_id'), '')}{html.escape(a['text'])}"
        if a.get("source_url"):
            text += f"\n<a href=\"{html.escape(a['source_url'])}\">Source</a>"
        if send(token, chat_id, text):
            sent += 1
            state["last_sent_at"] = a["detected_at"]
            STATE.write_text(json.dumps(state))
    print(f"notify: {sent} alert(s) sent")

if __name__ == "__main__":
    main()
