"""Local artifact viewer: python -m demo.serve --run /absolute/run --port 8765."""
import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .artifacts import open_artifact, snapshot

ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


def make_server(root, port=8765, replay=False):
    root = Path(root).resolve()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def respond_headers(self, code, content_type, length, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; media-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def send_bytes(self, code, body, content_type="text/plain; charset=utf-8"):
            self.respond_headers(code, content_type, len(body))
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            try:
                self.route()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (OSError, ValueError):
                self.send_bytes(404, b"Artifact not available.")

        def route(self):
            url = urlsplit(self.path)
            path = unquote(url.path)
            if path == "/favicon.ico":
                self.send_bytes(204, b"")
                return
            if path in ASSETS:
                filename, mime = ASSETS[path]
                self.send_bytes(200, Path(__file__).with_name(filename).read_bytes(), mime)
                return
            if path == "/api/snapshot":
                iteration = parse_qs(url.query).get("iteration", [None])[0]
                if iteration and not re.fullmatch(r"iteration-\d{3}", iteration):
                    self.send_bytes(400, b"Invalid iteration.")
                    return
                data = snapshot(root, iteration)
                data["replay"] = replay
                self.send_bytes(200, json.dumps(data, ensure_ascii=True, allow_nan=False).encode(), "application/json")
                return
            if not path.startswith("/artifacts/"):
                self.send_bytes(404, b"Not found.")
                return
            name = path[len("/artifacts/"):]
            with open_artifact(root, name) as stream:
                size = stream.seek(0, 2)
                start, end = 0, size - 1
                range_header = self.headers.get("Range")
                partial = range_header is not None
                if partial:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                    if not match or not any(match.groups()):
                        self.respond_headers(416, "text/plain", 0, {"Content-Range": f"bytes */{size}"})
                        return
                    left, right = match.groups()
                    if not left:
                        count = int(right)
                        start = max(0, size - count)
                    else:
                        start = int(left)
                        end = min(int(right), size - 1) if right else size - 1
                    if start > end or start >= size or end < 0:
                        self.respond_headers(416, "text/plain", 0, {"Content-Range": f"bytes */{size}"})
                        return
                mime = "video/mp4" if name.endswith(".mp4") else "image/png" if name.endswith(".png") else "text/plain; charset=utf-8"
                extra = {"Accept-Ranges": "bytes"}
                if partial:
                    extra["Content-Range"] = f"bytes {start}-{end}/{size}"
                remaining = max(0, end - start + 1)
                self.respond_headers(206 if partial else 200, mime, remaining, extra)
                if self.command == "HEAD":
                    return
                stream.seek(start)
                while remaining:
                    block = stream.read(min(65536, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--replay", action="store_true", help="Disable browser polling for a saved run.")
    args = parser.parse_args()
    if not args.run.is_dir():
        parser.error("--run must be an existing artifact directory")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    server = make_server(args.run, args.port, args.replay)
    print(f"Viewer: http://127.0.0.1:{server.server_port} | {'saved replay' if args.replay else 'live polling'} | {args.run.resolve()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
