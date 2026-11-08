"""Content-Security-Policy header.

Feed content is sanitized server-side, but it is still third-party HTML in our
origin, so the CSP is a second wall: no inline scripts, no framing, scripts
only from ourselves and the pinned htmx CDN. Images and media may come from
anywhere because that's what feeds link to.
"""

POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://unpkg.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src * data: blob:",
        "media-src *",
        "connect-src 'self'",
        "frame-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith("/admin/"):
            response.headers.setdefault("Content-Security-Policy", POLICY)
        return response
