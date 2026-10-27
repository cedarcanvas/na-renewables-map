"""Serve web/ locally, with HTTP Range requests.

The transmission lines are one PMTiles file, which the page reads a few kilobytes at a
time with Range requests; Python's own http.server ignores Range and sends the whole
file, which the pmtiles library refuses. Any real host (GitHub Pages, Netlify, S3,
Cloudflare) supports Range already, so this is for local use only.

    python3 scripts/serve.py            # http://localhost:8765
    python3 scripts/serve.py 9000
"""

import os
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not match or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last) if last else size - 1, size - 1)
        else:  # "bytes=-N": the last N bytes
            start, end = max(size - int(last or 0), 0), size - 1
        if start > end:
            self.send_error(416, "Range not satisfiable")
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(1 << 16, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)
        self._remaining = None

    def end_headers(self):
        # Data files change every hour; don't let the browser keep stale ones.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(RangeHandler, directory=str(WEB)))
    print(f"Serving {WEB} at http://localhost:{port}")
    server.serve_forever()
