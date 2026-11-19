import json
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

def extract_images(html, base_url, max_images=50, min_width=100, min_height=100, formats=None):
    if formats is None:
        formats = {"jpg", "jpeg", "png", "webp"}
    soup = BeautifulSoup(html, "html.parser")
    candidates = set()

    for tag in soup.find_all(["img", "source"]):
        for attr in ("src", "data-src", "srcset", "data-srcset"):
            val = tag.get(attr)
            if not val:
                continue
            for part in val.split(","):
                src = part.strip().split(" ")[0]
                abs_url = urljoin(base_url, src)
                parsed = urlparse(abs_url)
                ext = parsed.path.lower().rsplit(".", 1)[-1]
                if ext in formats:
                    candidates.add(abs_url)

    # Further filter by attempting to load (optional, serverless-safe)
    results = []
    for url in candidates:
        if len(results) >= max_images:
            break
        try:
            r = requests.head(url, timeout=5, allow_redirects=True)
            width = int(r.headers.get("Content-Width", 0))
            height = int(r.headers.get("Content-Height", 0))
            # If headers don't provide dimensions, skip strict size check
            if width and height and (width < min_width or height < min_height):
                continue
            results.append(url)
        except Exception:
            results.append(url)  # keep if head request fails

    return results[:max_images]


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body)
            url = payload.get("url")
            if not url:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Missing url"}).encode())
                return

            max_images = payload.get("maxImages", 50)
            min_width = payload.get("minWidth", 100)
            min_height = payload.get("minHeight", 100)
            formats = set(payload.get("formats", ["jpg", "jpeg", "png", "webp"]))

            resp = requests.get(url, timeout=15)
            resp.raise_for_status()

            image_urls = extract_images(
                resp.text, url, max_images, min_width, min_height, formats
            )

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"images": image_urls}).encode())

        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())