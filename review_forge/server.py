"""Read-only local preview of the exact static directory intended for hosting."""
from __future__ import annotations

import mimetypes
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .publishing import build_site
from .util import ReviewError, read_json


def handler_for(directory: Path):
    directory = directory.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            path = unquote(urlsplit(self.path).path).lstrip('/') or 'index.html'
            manifest = read_json(directory / 'site-manifest.json', {})
            allowed = set(manifest.get('files', [])) | {'site-manifest.json'}
            target = directory / path
            if path not in allowed or not target.resolve().is_relative_to(directory) or not target.is_file():
                return self.send_error(404, 'Published file not found')
            body = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_error(405, 'Read-only site; generation is available only through the private CLI')

        do_PUT = do_POST
        do_DELETE = do_POST
        do_PATCH = do_POST
    return Handler


def serve(path: Path, port: int = 8765, open_browser=False):
    if not 0 <= port <= 65535:
        raise ReviewError('Порт должен быть от 0 до 65535.')
    directory = build_site(path, path.parent / 'site')
    server = ThreadingHTTPServer(('127.0.0.1', port), handler_for(directory))
    url = f'http://127.0.0.1:{server.server_address[1]}'
    print(f'Review Forge (read-only): {url}\nStatic files: {directory}\nCtrl+C stops preview.', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
