"""Tiny static server for the SuccessFactors-like fixture pages.

Usage in tests:   url, stop = start(); ...; stop()
Usage manually:   python -m tests.fixtures.sf_like.server   (prints the URL, Ctrl+C to stop)
"""

from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def start(host: str = "127.0.0.1", port: int = 0) -> tuple[str, callable]:
    handler = partial(_Handler, directory=str(ROOT))
    httpd = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://{host}:{httpd.server_address[1]}/"

    def stop() -> None:
        httpd.shutdown()
        httpd.server_close()

    return url, stop


if __name__ == "__main__":
    url, stop = start(port=int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(url, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        stop()
