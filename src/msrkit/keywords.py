"""Keyword matching: term detection with context windows.

v0 uses simple case-insensitive matching with word boundaries.
No stemming, embeddings, or classification.
"""

from __future__ import annotations

import re
from typing import Literal

from msrkit.models import TermHit


def _build_pattern(term: str) -> re.Pattern[str]:
    """Build a regex pattern for a term with word boundaries.

    Multi-word terms are matched as exact phrases.
    """
    escaped = re.escape(term)
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


def _extract_context(text: str, match_start: int, match_end: int, window: int = 40) -> str:
    """Extract a context window around a match.

    Args:
        text: The full text.
        match_start: Start index of the match.
        match_end: End index of the match.
        window: Number of tokens (words) to include on each side.

    Returns:
        Context string with ±window tokens around the match.
    """
    # Split into tokens (words)
    # Find token boundaries around the match
    before = text[:match_start]
    after = text[match_end:]

    before_tokens = before.split()
    after_tokens = after.split()

    ctx_before = " ".join(before_tokens[-window:])
    matched_text = text[match_start:match_end]
    ctx_after = " ".join(after_tokens[:window])

    parts = []
    if ctx_before:
        parts.append(ctx_before)
    parts.append(matched_text)
    if ctx_after:
        parts.append(ctx_after)

    return " ".join(parts)


def match_terms(
    terms: list[str],
    *,
    title: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
    path: str | None = None,
    context_window: int = 40,
) -> list[TermHit]:
    """Match terms against item fields.

    Args:
        terms: List of search terms.
        title: Item title.
        body: Item body text.
        tags: Item tags.
        path: Item path (e.g., file path for code).
        context_window: Number of tokens for context.

    Returns:
        List of TermHit instances for all matches.
    """
    hits: list[TermHit] = []

    fields: list[tuple[Literal["title", "body", "tags", "path"], str | None]] = [
        ("title", title),
        ("body", body),
        ("tags", " ".join(tags) if tags else None),
        ("path", path),
    ]

    for term in terms:
        pattern = _build_pattern(term)

        for field_name, field_value in fields:
            if field_value is None:
                continue

            for match in pattern.finditer(field_value):
                context = _extract_context(
                    field_value,
                    match.start(),
                    match.end(),
                    context_window,
                )
                hits.append(
                    TermHit(
                        term=term,
                        field=field_name,
                        context=context,
                    )
                )

    return hits
