"""Open a link from an email and bring back what is there.

Links in email are untrusted, so this is deliberately narrow:
- GET only, http/https only, no cookies or credentials, a generic browser name
- every hop must resolve to a public internet address (nothing on this machine or the local network)
- at most 5 redirects, 15 seconds, 8 MB
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 8 * 1024 * 1024
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; link-preview)",
    "Accept": "text/html,application/pdf,image/*,text/plain;q=0.9,*/*;q=0.5",
}


class FetchError(RuntimeError):
    pass


def is_public_host(host: str) -> bool:
    """True only when every address the name resolves to is on the public internet."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and all(ipaddress.ip_address(addr.split("%")[0]).is_global for addr in addresses)


def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Not a web link.")
    if parts.username or parts.password:
        raise FetchError("Links with embedded sign-in details are not opened.")
    if not is_public_host(parts.hostname):
        raise FetchError("Link does not point to a public website.")


def fetch(url: str) -> dict:
    """{'url', 'content_type', 'data'} for the page or file behind a link in an email.
    (Demo mode replaces this one with sample pages.)"""
    return fetch_public(url)


def fetch_public(url: str, max_bytes: int = MAX_BYTES, timeout: int = 15) -> dict:
    """The real download, with all the checks described at the top of this file."""
    import httpx

    current = url
    with httpx.Client(follow_redirects=False, timeout=timeout, headers=_HEADERS) as client:
        for _ in range(6):
            check_url(current)
            try:
                with client.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                        current = urljoin(current, resp.headers["location"])
                        continue
                    if resp.status_code in (401, 403):
                        raise FetchError("The site refused the request (it needs a sign-in or blocks automated visits).")
                    if resp.status_code >= 400:
                        raise FetchError(f"The site answered with error {resp.status_code}.")
                    data = bytearray()
                    for chunk in resp.iter_bytes():
                        data += chunk
                        if len(data) > max_bytes:
                            raise FetchError(f"The file is larger than {max_bytes // (1024 * 1024)} MB.")
                    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    return {"url": current, "content_type": content_type, "data": bytes(data)}
            except httpx.HTTPError as exc:
                raise FetchError(f"Could not reach the site ({type(exc).__name__}).") from exc
    raise FetchError("Too many redirects.")
