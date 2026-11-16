"""Deduplication: URL canonicalization and content hashing.

Three passes (Protocol E2 v2, §11):
1. Canonical URL — lowercase host, remove tracking params, trailing slash.
2. Content hash — sha256(title + normalized body).
3. Near-duplicates — MinHash over word 5-shingles with LSH banding; a candidate
   pair is merged only when its estimated Jaccard similarity reaches the
   threshold (calibrated in the pilot). Deterministic: fixed seeds, no Python
   `hash()`.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from pydantic import BaseModel

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

    - Normalize scheme to https (http -> https)
    - Lowercase the scheme and host, stripping 'www.'
    - Remove tracking parameters (utm_*, ref, source, etc.)
    - Remove trailing slash (except for root paths)
    - Strip '.git' from repository URLs (GitHub, GitLab, HuggingFace)
    - Sort remaining query parameters
    """
    parsed = urlparse(url)

    # Normalize scheme (http -> https) and lowercase host
    scheme = parsed.scheme.lower()
    if scheme == "http":
        scheme = "https"
    host = parsed.hostname or ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
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

    # Strip .git suffix and lowercase owner/repo for git forge repositories
    git_forges = ("github.com", "gitlab.com", "huggingface.co")
    if host in git_forges:
        if path.endswith(".git"):
            path = path[:-4]
        parts = path.split("/")
        if len(parts) >= 3:
            if host == "huggingface.co" and parts[1].lower() in ("datasets", "spaces", "models"):
                if len(parts) >= 4:
                    parts[2] = parts[2].lower()
                    parts[3] = parts[3].lower()
            else:
                parts[1] = parts[1].lower()
                parts[2] = parts[2].lower()
            path = "/".join(parts)

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


NUM_PERM = 128
BANDS = 32  # 32 bands x 4 rows: candidate pairs from ~0.42 similarity upwards
SHINGLE = 5
MIN_TOKENS = 30  # shorter bodies are too small for a meaningful similarity
_MERSENNE = (1 << 61) - 1
_rng = random.Random(20260928)
_PERMS = [(_rng.randrange(1, _MERSENNE), _rng.randrange(0, _MERSENNE)) for _ in range(NUM_PERM)]


def minhash_signature(text: str) -> list[int] | None:
    """MinHash signature of the word 5-shingles of a text (None if too short)."""
    tokens = re.findall(r"\w+", text.lower())
    if len(tokens) < MIN_TOKENS:
        return None
    shingles = {" ".join(tokens[i : i + SHINGLE]) for i in range(len(tokens) - SHINGLE + 1)}
    hashes = [
        int.from_bytes(hashlib.blake2b(sh.encode("utf-8"), digest_size=8).digest(), "big")
        for sh in shingles
    ]
    return [min((a * h + b) % _MERSENNE for h in hashes) for a, b in _PERMS]


def estimated_jaccard(sig_a: list[int], sig_b: list[int]) -> float:
    return sum(x == y for x, y in zip(sig_a, sig_b, strict=True)) / len(sig_a)


class DedupeReport(BaseModel):
    """Outcome of deduplication, with the reason each duplicate was removed."""

    model_config = {"arbitrary_types_allowed": True}

    unique: list[Any]
    duplicates: list[Any]
    # duplicate item id -> "url" | "content" | "near:<similarity>:<kept item id>"
    reasons: dict[str, str] = {}
    duplicate_reasons: list[str] = []

    def counts(self) -> dict[str, int]:
        out = {"url": 0, "content": 0, "near": 0}
        source = self.duplicate_reasons if self.duplicate_reasons else list(self.reasons.values())
        for reason in source:
            kind = reason.split(":", 1)[0]
            if kind in out:
                out[kind] += 1
            else:
                out[kind] = 1
        return out


def deduplicate_report(items: list[Item], near_threshold: float | None = 0.85) -> DedupeReport:
    """Three-pass deduplication; `near_threshold=None` disables pass 3."""
    unique, duplicates = _exact_passes(items)
    reasons: dict[str, str] = {}
    dup_reasons_list: list[str] = []
    dups: list[Item] = []
    for d, r in duplicates:
        dups.append(d)
        reasons[d.id] = r
        dup_reasons_list.append(r)

    if near_threshold is not None:
        unique, near = _near_pass(unique, near_threshold)
        for item, reason in near:
            dups.append(item)
            reasons[item.id] = reason
            dup_reasons_list.append(reason)

    logger.info(
        "Deduplication: %d input → %d unique + %d duplicates", len(items), len(unique), len(dups)
    )
    return DedupeReport(
        unique=unique,
        duplicates=dups,
        reasons=reasons,
        duplicate_reasons=dup_reasons_list,
    )


def _near_pass(items: list[Item], threshold: float) -> tuple[list[Item], list[tuple[Item, str]]]:
    rows = NUM_PERM // BANDS
    buckets: dict[tuple[int, tuple[int, ...]], list[int]] = {}
    sigs: list[list[int] | None] = []
    kept: list[Item] = []
    kept_ids: list[str] = []  # item id of each stored signature
    removed: list[tuple[Item, str]] = []
    for item in items:
        text = f"{item.title or ''} {item.body or ''}"
        sig = None if item.kind == ItemKind.CODE else minhash_signature(text)
        if sig is None:
            kept.append(item)
            continue
        best: tuple[float, int] | None = None
        candidates: set[int] = set()
        for band in range(BANDS):
            candidates.update(buckets.get((band, tuple(sig[band * rows : (band + 1) * rows])), []))
        for idx in sorted(candidates):
            other = sigs[idx]
            if other is None:
                continue
            sim = estimated_jaccard(sig, other)
            if sim >= threshold and (best is None or sim > best[0]):
                best = (sim, idx)
        if best is not None:
            removed.append((item, f"near:{best[0]:.2f}:{kept_ids[best[1]]}"))
            continue
        idx = len(sigs)
        sigs.append(sig)
        kept_ids.append(item.id)
        for band in range(BANDS):
            buckets.setdefault((band, tuple(sig[band * rows : (band + 1) * rows])), []).append(idx)
        kept.append(item)
    return kept, removed


def deduplicate(
    items: list[Item], near_threshold: float | None = None
) -> tuple[list[Item], list[Item]]:
    """Deduplicate items (URL, content hash and, if a threshold is given, near-duplicates)."""
    report = deduplicate_report(items, near_threshold)
    return report.unique, report.duplicates


def _exact_passes(items: list[Item]) -> tuple[list[Item], list[tuple[Item, str]]]:
    """Passes 1 and 2: canonical URL and content hash."""
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    unique: list[Item] = []
    duplicates: list[tuple[Item, str]] = []

    for item in items:
        # Pass 1: URL canonicalization
        canon_url = canonicalize_url(str(item.url))
        if canon_url in seen_urls:
            duplicates.append((item, "url"))
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
                duplicates.append((item, "content"))
                logger.debug("Duplicate content: hash=%s (item %s)", c_hash[:12], item.id)
                continue
            seen_hashes.add(c_hash)

        unique.append(item)

    return unique, duplicates
