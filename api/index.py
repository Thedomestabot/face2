from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, urljoin, parse_qs
import json, requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; FaceLocal/1.0)"
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff"}
MAX_BYTES = 15 * 1024 * 1024  # 15 MB cap for proxy

# ---------- helpers ----------
def extract_image_urls(html, base_url, formats, limit):
    soup = BeautifulSoup(html, "html.parser")
    urls = []

    def consider(src):
        if not src or src.startswith("data:"):
            return
        try:
            abs_url = urljoin(base_url, src)
            parsed = urlparse(abs_url)
        except Exception:
            return
        path = parsed.path.lower()
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        if ext in formats and abs_url not in urls:
            urls.append(abs_url)

    for tag in soup.find_all(["img", "source"]):
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            consider(tag.get(attr))
        for attr in ("srcset", "data-srcset"):
            val = tag.get(attr)
            if val:
                for part in val.split(","):
                    consider(part.strip().split(" ")[0])

    for a in soup.find_all("a", href=True):
        consider(a["href"])

    return urls[:limit]


def is_safe_proxy_url(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return False
    if host.startswith(("10.", "192.168.", "169.254.")) or host.endswith(".local"):
        return False
    if host.startswith("172."):
        try:
            if 16 <= int(host.split(".")[1]) <= 31:
                return False
        except Exception:
            pass
    return True


# ---------- single Vercel handler ----------
class handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_POST(self):
        # Route /api/scrape
        if self.path.startswith("/api/scrape"):
            self._handle_scrape()
        else:
            self._json(404, {"error": "Unknown POST route"})

    def do_GET(self):
        # Route /api/proxy
        if self.path.startswith("/api/proxy"):
            self._handle_proxy()
        else:
            self._json(404, {"error": "Unknown GET route"})

    # ---------- scraper logic ----------
    def _handle_scrape(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            url = payload.get("url", "").strip()
            if not url:
                return self._json(400, {"error": "Missing url"})

            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return self._json(400, {"error": "Only http(s) URLs allowed"})

            max_images = int(payload.get("maxImages", 100))
            formats = set(payload.get("formats") or ["jpg", "jpeg", "png", "webp"])
            formats &= IMAGE_EXTS

            resp = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            )
            resp.raise_for_status()

            images = extract_image_urls(resp.text, url, formats, max_images)
            return self._json(200, {"images": images})

        except requests.HTTPError as e:
            return self._json(502, {"error": f"Upstream returned {e.response.status_code}"})
        except Exception as e:
            return self._json(500, {"error": str(e)})

    # ---------- proxy logic ----------
    def _handle_proxy(self):
        qs = parse_qs(urlparse(self.path).query)
        target = (qs.get("url") or [""])[0]

        if not target or not is_safe_proxy_url(target):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid url")
            return

        try:
            r = requests.get(
                target,
                timeout=20,
                stream=True,
                headers={"User-Agent": USER_AGENT, "Accept": "image/*"},
            )
            ct = r.headers.get("Content-Type", "")
            if not ct.startswith("image/"):
                self.send_response(415)
                self.end_headers()
                self.wfile.write(b"Not an image")
                return

            if int(r.headers.get("Content-Length", 0)) > MAX_BYTES:
                self.send_response(413)
                self.end_headers()
                self.wfile.write(b"Too large")
                return

            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()

            total = 0
            for chunk in r.iter_content(16384):
                total += len(chunk)
                if total > MAX_BYTES:
                    break
                self.wfile.write(chunk)

        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode()[:500])