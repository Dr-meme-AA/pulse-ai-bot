"""Build one standalone preview Worker. No credentials or network are needed."""
import argparse
import base64
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("edition", choices=["robinhood", "binance"])
parser.add_argument("output", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parent
edition = root / args.edition
files = {}
for url, path, kind in [
    ("/", edition / "index.html", "text/html"),
    ("/assets/base.css", root / "shared/base.css", "text/css"),
    ("/assets/theme.css", edition / "theme.css", "text/css"),
    ("/assets/effects.js", root / "shared/effects.js", "application/javascript"),
]:
    files[url] = {"body": path.read_text(), "type": kind + "; charset=utf-8"}
files["/index.html"] = files["/"]
image_name = "hood-green.webp" if args.edition == "robinhood" else "hood-gold.webp"
image = {"path": "/assets/" + image_name, "base64": base64.b64encode((edition / "assets" / image_name).read_bytes()).decode()}
code = (root / "worker-template.js").read_text().replace("__FILES_PAYLOAD__", json.dumps(files)).replace("__IMAGE_PAYLOAD__", json.dumps(image))
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(code)
print(f"Built {args.edition}: {args.output.stat().st_size} bytes")
