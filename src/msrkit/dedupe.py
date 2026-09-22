"""Deduplication: URL canonicalization and content hashing.

v0 uses two passes only:
1. Canonical URL — lowercase host, remove tracking params, trailing slash.
2. Content hash — sha256(title + normalized body).

Near-duplicate detection (MinHash/LSH) is deferred to v1.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from msrkit.models import ItemKind

if TYPE_CHECKING:
    from msrkit.models import Item

logger = logging.getLogger(__name__)

# Tracking parameters to strip from URLs
_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "source",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def canonicalize_url(url: str) -> str:
    """Canonicalize a URL for deduplication.

    - Lowercase the scheme and host
    - Remove tracking parameters (utm_*, ref, source, etc.)
    - Remove trailing slash (except for root paths)
    - Sort remaining query parameters
    """
    parsed = urlparse(url)

    # Lowercase scheme and host
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    host = host.lower()
    port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""

    # Clean query parameters
    params = parse_qs(parsed.query, keep_blank_values=True)
    clean_params = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
    sorted_query = urlencode(sorted(clean_params.items()), doseq=True) if clean_params else ""

    # Clean path — root path is normalized to '/', trailing slashes removed on non-root
    path = parsed.path
    if not path:
        path = "/"
    elif path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse((scheme, f"{host}{port}", path, "", sorted_query, ""))


def content_hash(title: str | None, body: str | None) -> str:
    """Compute content hash for deduplication.

    Uses sha256(title + normalized_body), where normalization
    collapses whitespace.
    """
    parts = []
    if title:
        parts.append(title.strip())
    if body:
        # Normalize whitespace
        normalized = re.sub(r"\s+", " ", body.strip())
        parts.append(normalized)

    combined = "\n".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def deduplicate(items: list[Item]) -> tuple[list[Item], list[Item]]:
    """Deduplicate items by URL and content hash.

    Args:
        items: List of items to deduplicate.

    Returns:
        Tuple of (unique_items, duplicate_items).
    """
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    unique: list[Item] = []
    duplicates: list[Item] = []

    for item in items:
        # Pass 1: URL canonicalization
        canon_url = canonicalize_url(str(item.url))
        if canon_url in seen_urls:
            duplicates.append(item)
            logger.debug("Duplicate URL: %s (item %s)", canon_url, item.id)
            continue
        seen_urls.add(canon_url)

        # Pass 2: Content hash (only when non-empty body content exists)
        # Items without body (e.g. code search files, title-only link posts) rely
        # strictly on URL canonicalization to prevent false-positive entity mergers (ADR-012).
        has_body = bool(item.body and item.body.strip())
        if has_body and item.kind != ItemKind.CODE:
            c_hash = content_hash(item.title, item.body)
            if c_hash in seen_hashes:
                duplicates.append(item)
                logger.debug("Duplicate content: hash=%s (item %s)", c_hash[:12], item.id)
                continue
            seen_hashes.add(c_hash)

        unique.append(item)

    logger.info(
        "Deduplication: %d input → %d unique + %d duplicates",
        len(items),
        len(unique),
        len(duplicates),
    )

    return unique, duplicates
