"""Shared helpers for the Midnight Watch monitors."""
import json, hashlib, pathlib, datetime, zoneinfo
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data.json"
CT = zoneinfo.ZoneInfo("America/Chicago")
UA = {"User-Agent": "MidnightWatch/1.0 (hobby availability monitor; contact in README)"}

def now_iso():
    return datetime.datetime.now(CT).replace(microsecond=0).isoformat()

def load():
    return json.loads(DATA.read_text())

def save(d):
    d["meta"]["generated_at"] = now_iso()
    DATA.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")

def add_activity(d, type_, text, url, series_id=None, creator_id=None):
    """Append one activity-feed entry (newest first). creator_id tags entries that
    belong to a creator watch (e.g. "skottie-young") so the page and the Telegram
    notifier can label them; series rows leave it None."""
    stamp = now_iso()
    d["activity"].insert(0, {
        "id": f"act-{stamp}-{hashlib.md5((type_+text).encode()).hexdigest()[:6]}",
        "detected_at": stamp, "type": type_, "series_id": series_id, "creator_id": creator_id,
        "text": text, "source_url": url})
