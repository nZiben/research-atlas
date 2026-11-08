from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .util import ReviewError


def public_url(url: str):
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ReviewError("Source URL must be an HTTP(S) URL without embedded credentials.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as exc:
        raise ReviewError("Cannot resolve source host.") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ReviewError("Source URL resolves to a private or reserved address; use a local file explicitly.")


class SourceRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        if req.has_header("X-api-key") or req.has_header("Authorization"):
            raise ReviewError("Authenticated source redirected; authorization was not forwarded.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # API authorization must never be forwarded to a redirect target.
        raise ReviewError("API redirected. Configure its exact final endpoint URL.")


class HTTP:
    def __init__(self, timeout=60):
        self.timeout = timeout
        self.last: dict[str, float] = {}

    def get(self, url: str, headers=None, delay=0.0, max_bytes=40_000_000) -> bytes:
        public_url(url)
        host = urlsplit(url).hostname
        opener = urllib.request.build_opener(SourceRedirect())
        for attempt in range(3):
            time.sleep(max(0.0, delay - (time.monotonic() - self.last.get(host, 0))))
            self.last[host] = time.monotonic()
            request = urllib.request.Request(url, headers={"User-Agent": "ReviewForge/0.1 (local literature research)", **(headers or {})})
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ReviewError(f"Source exceeds the {max_bytes}-byte download limit.")
                return data
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise ReviewError(f"Source HTTP {exc.code} from {host}.") from exc
                retry = exc.headers.get("Retry-After", "")
                time.sleep(min(60, float(retry)) if retry.isdigit() else 2 ** (attempt + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == 2:
                    raise ReviewError(f"Source network error from {host}; retry later.") from exc
                time.sleep(2 ** (attempt + 1))
        raise ReviewError("Source request failed.")

    def post_json(self, url: str, payload: dict, key: str | None) -> dict:
        headers = {"Content-Type": "application/json", "User-Agent": "ReviewForge/0.1"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            # No automatic POST retries: an interrupted response can still be billed.
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(20_000_001)
            if len(raw) > 20_000_000:
                raise ReviewError("Model response exceeded 20 MB.")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ReviewError("Model API did not return a JSON object.")
            return result
        except urllib.error.HTTPError as exc:
            raise ReviewError(f"Model API returned HTTP {exc.code}. Check endpoint, key, model and parameters; response body omitted to protect secrets.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ReviewError("Model connection failed. The provider may have processed the request; check usage before retrying.") from exc
        except (ValueError, UnicodeError) as exc:
            raise ReviewError("Model API returned invalid JSON.") from exc
