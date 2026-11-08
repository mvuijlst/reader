"""Small HTTP layer: one GET with a size cap, redirect bookkeeping, sane errors."""
from dataclasses import dataclass, field

import requests
from django.conf import settings

DEFAULT_TIMEOUT = (10, 30)  # connect, read


class FetchError(Exception):
    pass


@dataclass
class HttpResult:
    status: int
    url: str  # final URL after redirects
    headers: dict = field(default_factory=dict)  # lower-cased keys
    content: bytes = b""
    permanent_url: str = ""  # set when every redirect hop was permanent (301/308)

    @property
    def ok(self):
        return 200 <= self.status < 300

    def text(self):
        ctype = self.headers.get("content-type", "")
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip().strip('"') or "utf-8"
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


# Bot-protection services answer automated requests with a challenge page that
# only a full browser can pass. They block feed readers too, and only the site
# can change that, so we recognise them and say so instead of "HTTP 403".
# Headers these services send only when they challenge or block a request, so
# they count even on a 200 (some challenges answer 200).
_CHALLENGE_HEADERS = [
    ("Cloudflare", "cf-mitigated", "challenge"),
    ("AWS WAF", "x-amzn-waf-action", ""),
    ("Vercel", "x-vercel-mitigated", ""),
    ("Sucuri", "x-sucuri-block", ""),
]
# Markers that only mean "challenge" on a refused request (403/429/503): some of
# them also appear on normal pages from the same service (Cloudflare injects its
# challenge-platform script into ordinary pages, Imperva sends x-iinfo always).
_REFUSAL_MARKERS = [
    ("Cloudflare", ("cf-ray",), ("just a moment...", "/cdn-cgi/challenge-platform", "cf-chl-")),
    ("BunnyCDN", (), ("establishing a secure connection",)),
    ("Sucuri", ("x-sucuri-id",), ("sucuri website firewall",)),
    ("Imperva", ("x-iinfo",), ("incapsula incident id", "_incapsula_resource")),
    ("DataDome", ("x-datadome",), ("captcha-delivery.com",)),
    ("HUMAN (PerimeterX)", (), ("px-captcha", "perimeterx")),
    ("AWS WAF", (), ("awswafintegration",)),
]


def bot_protection(result):
    """Name of the bot-protection service behind a challenge response, or None."""
    headers = result.headers
    for service, name, needle in _CHALLENGE_HEADERS:
        if name in headers and needle in headers[name].lower():
            return service
    if result.status not in (403, 429, 503):
        return None
    body = result.content[:30_000].decode("utf-8", errors="replace").lower()
    for service, header_names, body_markers in _REFUSAL_MARKERS:
        if any(marker in body for marker in body_markers):
            return service
    for service, header_names, body_markers in _REFUSAL_MARKERS:
        # e.g. a bare Cloudflare 403 with a cf-ray header but no challenge text
        if any(name in headers for name in header_names) and ("captcha" in body or "challenge" in body):
            return service
    if "captcha" in body or "verify you are human" in body:
        return "unknown"  # a challenge from a service we don't know by name
    return None


def blocked_message(service):
    who = "The site's bot protection" if service == "unknown" else f"The site's bot protection ({service})"
    return f"{who} blocks feed readers, not just bots. Only the site can let them through."


def get(url, *, headers=None, max_bytes=10 * 1024 * 1024, timeout=DEFAULT_TIMEOUT):
    all_headers = {"User-Agent": settings.READER_USER_AGENT}
    all_headers.update(headers or {})
    try:
        with requests.get(
            url, headers=all_headers, timeout=timeout, stream=True, allow_redirects=True
        ) as r:
            chunks, size = [], 0
            for chunk in r.iter_content(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError(f"Response larger than {max_bytes // 1024} KB")
                chunks.append(chunk)
            permanent = ""
            if r.history and all(h.status_code in (301, 308) for h in r.history):
                permanent = r.url
            return HttpResult(
                status=r.status_code,
                url=r.url,
                headers={k.lower(): v for k, v in r.headers.items()},
                content=b"".join(chunks),
                permanent_url=permanent,
            )
    except FetchError:
        raise
    except requests.exceptions.SSLError as e:
        raise FetchError(f"TLS error: {_short(e)}") from e
    except requests.exceptions.ConnectionError as e:
        raise FetchError(f"Could not connect: {_short(e)}") from e
    except requests.exceptions.Timeout as e:
        raise FetchError("Timed out") from e
    except requests.exceptions.RequestException as e:
        raise FetchError(_short(e)) from e


def _short(exc, limit=200):
    text = str(exc)
    return text if len(text) <= limit else text[: limit - 1] + "…"
