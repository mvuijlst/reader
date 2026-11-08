"""Turn untrusted feed HTML into something safe to render in our origin."""
import html
import re

import nh3
from django.utils.html import escape, strip_tags

TAGS = (set(nh3.ALLOWED_TAGS) | {"picture", "source", "video", "audio", "track"}) - {"map", "area"}

ATTRIBUTES = {tag: set(attrs) for tag, attrs in nh3.ALLOWED_ATTRIBUTES.items()}
ATTRIBUTES.update(
    {
        "a": {"href", "hreflang", "title"},
        "img": {"src", "srcset", "sizes", "alt", "title", "width", "height"},
        "source": {"src", "srcset", "sizes", "type", "media"},
        "video": {"src", "poster", "width", "height"},
        "audio": {"src"},
        "track": {"src", "kind", "srclang", "label"},
        "abbr": {"title"},
        "time": {"datetime"},
    }
)

SET_ATTRIBUTES = {
    "a": {"target": "_blank"},
    "img": {"loading": "lazy", "decoding": "async"},
    "video": {"controls": "", "preload": "none"},
    "audio": {"controls": "", "preload": "none"},
}

_WS = re.compile(r"\s+")
_URL = re.compile(r"(https?://[^\s<>\"']+)")


def clean_html(fragment):
    if not fragment:
        return ""
    return nh3.clean(
        fragment,
        tags=TAGS,
        attributes=ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
        set_tag_attribute_values=SET_ATTRIBUTES,
        strip_comments=True,
    ).strip()


def clean_text(value):
    """Titles and authors: no markup, entities decoded, whitespace collapsed."""
    if not value:
        return ""
    return _WS.sub(" ", html.unescape(strip_tags(value))).strip()


_BREAKS = re.compile(r"<(?:br|hr|img)\b[^>]*>|</(?:p|div|li|h[1-6]|blockquote|tr|td|th|pre|figure|figcaption)>", re.I)


def text_snippet(fragment, length=280):
    # Block ends and <br> become spaces so words on either side don't fuse.
    text = clean_text(_BREAKS.sub(" ", fragment or ""))
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:-–— ") + "…"


_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r'\b(src|width|height)="([^"]*)"', re.I)
# Images that make poor thumbnails: tracking pixels, emoji, avatars, share buttons.
_NOT_A_PICTURE = re.compile(
    r"feedburner\.com/~|/pixel|tracking|stats\.wordpress\.com|/emoji/|gravatar\.com|"
    r"doubleclick\.net|feeds\.wordpress\.com/1\.0/|/share[-_]?button|/badge",
    re.I,
)


def first_image(fragment):
    """URL of the first image in sanitized HTML that could serve as a thumbnail."""
    for tag in _IMG_TAG.findall(fragment or ""):
        attrs = {k.lower(): html.unescape(v) for k, v in _ATTR.findall(tag)}
        src = attrs.get("src", "")
        if not src.startswith(("http://", "https://")) or _NOT_A_PICTURE.search(src):
            continue
        too_small = False
        for dim in ("width", "height"):
            value = attrs.get(dim, "").rstrip("px")
            if value.isdigit() and int(value) < 48:
                too_small = True
        if not too_small:
            return src
    return ""


def plain_to_html(text):
    """Plain-text bodies (YouTube descriptions, text/plain RSS) as paragraphs with links."""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    out = []
    for para in paragraphs:
        safe = escape(para)
        safe = _URL.sub(r'<a href="\1">\1</a>', safe)
        out.append("<p>" + safe.replace("\n", "<br>") + "</p>")
    return "\n".join(out)
