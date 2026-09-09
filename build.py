#!/usr/bin/env python3
"""Inject data.json into template.html -> index.html so the page works from disk and static hosts."""
import json, pathlib
root = pathlib.Path(__file__).parent
data = json.loads((root / "data.json").read_text())
html = (root / "template.html").read_text()
payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
(root / "index.html").write_text(html.replace("__DATA__", payload))
print("built index.html with", len(data["listings"]), "listings")
