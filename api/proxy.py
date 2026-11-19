from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests

USER_AGENT = "Mozilla/5.0 (compatible; FaceLocalProxy/1.0)"
MAX_BYTES = 15 * 1024 * 1024  # 15 MB cap


def is_safe(url):
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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        target = (qs.get("url") or [""])[0]

        if not target or not is_safe(target):
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