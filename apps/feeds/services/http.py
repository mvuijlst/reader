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
