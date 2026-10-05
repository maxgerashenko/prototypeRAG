"""Business identity from a URL (plan/01-crawler.md: "same website -> same business_id").

A business is identified by its main website domain, not by any particular sub-page or
by other locations it may have (a chain's second location is a different *page*, not a
different business). `domain_of` is the one place that normalization happens so the
crawler and the backfill migration agree on it.
"""

from urllib.parse import urlparse


def domain_of(url: str) -> str | None:
    """Host used for business identity: lowercased, no port, no trailing dot, leading
    "www." dropped as a literal prefix (never `lstrip`, which strips a character set and
    would silently mangle a domain like "wonderful.com"). Other subdomains are kept as
    part of the identity ("shop.example.com" stays "shop.example.com") -- splitting off a
    real registrable domain needs a public-suffix list, which this project doesn't carry
    (DEC-scope: good enough for stage 1's single-pilot-business crawl). IDNs are left
    as-is, not punycode-encoded. Returns None if `url` has no parseable host.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = parsed.hostname  # already lowercased by urlparse; stripped of port/brackets
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[len("www.") :]
    return host or None


def site_root(url: str) -> str | None:
    """The site root for `businesses.website`: scheme + host (+ non-default port) as
    given in `url` (not the www-stripped identity domain -- this is the actual address),
    path reset to "/". Returns None if `url` has no parseable host.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = parsed.hostname
    if not host:
        return None
    netloc = host.lower()
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    scheme = parsed.scheme or "https"
    return f"{scheme}://{netloc}/"
