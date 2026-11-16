"""Keyword matching: term detection with context windows.

Two deterministic modes (no embeddings or ML classification):

- ``flexible`` (default): each word of a term tolerates inflection (a light
  suffix-stripping stem followed by ``\\w*``), and words may be separated by
  spaces, hyphens, underscores or slashes. "evaluate RAG" matches "Evaluating
  RAG"; "LLM as a judge" matches "LLM-as-a-judge". Words shorter than four
  characters (acronyms such as RAG, LLM) stay exact, allowing only a plural "s",
  so "rag" never matches "brag" or "courage".
- ``exact``: case-insensitive exact phrase with word boundaries (v0 behavior).

Terms containing symbols (C++, .NET, C#) always use exact matching.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Literal

from msrkit.models import TermHit

MatchMode = Literal["flexible", "exact"]

# Checked in order; the first suffix that leaves a stem of MIN_STEM chars wins.
_SUFFIXES = (
    # English
    "ations", "ation", "ings", "ing", "ers", "er", "ed",
    # Portuguese, accent-folded (avaliação/avaliações -> avali-, testes -> test-)
    "acoes", "acao", "coes", "cao",
    # shared
    "es", "s", "e",
)  # fmt: skip
MIN_STEM = 4
_WORD_SEP = re.compile(r"[\s\-_/]+")
_SEP_PATTERN = r"[\s\-_/]+"


def _fold(text: str) -> str:
    """Strip accents one character at a time, so string positions are preserved."""
    return "".join(unicodedata.normalize("NFD", ch)[0] for ch in text)


def _stem(word: str) -> str:
    """Strip one common English inflectional suffix, keeping at least MIN_STEM chars."""
    lower = word.lower()
    for suffix in _SUFFIXES:
        if lower.endswith(suffix) and len(lower) - len(suffix) >= MIN_STEM:
            return lower[: -len(suffix)]
    return lower


def _word_pattern(word: str) -> str:
    if len(word) < MIN_STEM:
        return re.escape(word) + r"s?\b"
    return re.escape(_stem(word)) + r"\w*"


def _exact_pattern(term: str) -> re.Pattern[str]:
    """Exact phrase with word boundaries, or lookarounds for symbol edges (C++, .NET)."""
    escaped = re.escape(term)
    left = r"\b" if re.match(r"^\w", term) else r"(?<!\w)"
    right = r"\b" if re.search(r"\w$", term) else r"(?!\w)"
    return re.compile(rf"{left}{escaped}{right}", re.IGNORECASE)


@lru_cache(maxsize=1024)
def _build_pattern(term: str, mode: MatchMode = "flexible") -> re.Pattern[str]:
    """Build the regex for a term in the given matching mode."""
    words = [w for w in _WORD_SEP.split(_fold(term.strip())) if w]
    if mode == "exact":
        return _exact_pattern(term)
    if not words or any(re.search(r"\W", w) for w in words):
        return _exact_pattern(_fold(term))
    body = _SEP_PATTERN.join(_word_pattern(w) for w in words)
    return re.compile(rf"\b{body}", re.IGNORECASE)


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
    mode: MatchMode = "flexible",
) -> list[TermHit]:
    """Match terms against item fields.

    Args:
        terms: List of search terms.
        title: Item title.
        body: Item body text.
        tags: Item tags.
        path: Item path (e.g., file path for code).
        context_window: Number of tokens for context.
        mode: "flexible" (inflections and hyphens tolerated) or "exact".

    Returns:
        List of TermHit instances for all matches.
    """
    hits: list[TermHit] = []

    clean_tags = " ".join(str(t) for t in tags if t is not None) if tags else None
    fields: list[tuple[Literal["title", "body", "tags", "path"], str | None]] = [
        ("title", title),
        ("body", body),
        ("tags", clean_tags if clean_tags else None),
        ("path", path),
    ]

    # Flexible mode matches on accent-folded text; _fold keeps positions, so the
    # context window is cut from the original text. Folded once per field, not per term.
    searchable_of = {
        name: _fold(value) if mode == "flexible" else value
        for name, value in fields
        if value is not None
    }

    for term in terms:
        if not term or not term.strip():
            continue
        pattern = _build_pattern(term, mode)

        for field_name, field_value in fields:
            if field_value is None:
                continue

            for match in pattern.finditer(searchable_of[field_name]):
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
