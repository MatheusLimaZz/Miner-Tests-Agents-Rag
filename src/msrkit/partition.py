"""Query partitioning: splits queries to stay within API result caps.

Generic algorithm that recursively subdivides queries by time window
and secondary axes until each partition fits within the source's
max_results_per_query limit.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from msrkit.models import Query

logger = logging.getLogger(__name__)


def partition_by_date(q: Query) -> list[Query]:
    """Split a query's date window in half.

    Returns two queries covering [since, mid) and [mid, until].
    If the window is a single day, returns [q] unchanged.
    """
    if q.since is None or q.until is None:
        return [q]

    delta = q.until - q.since
    if delta <= timedelta(days=1):
        return [q]

    mid = q.since + delta // 2

    left = q.model_copy(update={"until": mid})
    right = q.model_copy(update={"since": mid + timedelta(days=1)})
    return [left, right]


def partition(
    q: Query,
    max_results: int | None,
    estimate_fn: Callable[[Query], int | None],
    secondary_axes: list[Callable[[Query], list[Query]]] | None = None,
    depth: int = 0,
    max_depth: int = 20,
) -> list[tuple[Query, bool]]:
    """Recursively partition a query to stay within max_results.

    Args:
        q: The query to partition.
        max_results: Maximum results per query (from SourcePolicy).
        estimate_fn: Function to estimate result count for a query.
        secondary_axes: Optional list of functions that split by secondary
            axes (e.g., language, star range). Each returns a list of
            sub-queries.
        depth: Current recursion depth (internal).
        max_depth: Maximum recursion depth to prevent infinite loops.

    Returns:
        List of (query, truncated) tuples. truncated=True means the
        partition may be incomplete because no further splitting was possible.
    """
    # No cap → single partition, no truncation
    if max_results is None:
        return [(q, False)]

    # Estimate result count
    estimated = estimate_fn(q)

    # Unknown or within limit → single partition
    if estimated is None or estimated <= max_results:
        return [(q, False)]

    # Guard against infinite recursion
    if depth >= max_depth:
        logger.warning(
            "Partition depth %d reached for query %s, marking as truncated "
            "(estimated=%d, max=%d)",
            depth,
            q.terms,
            estimated,
            max_results,
        )
        return [(q, True)]

    # Try splitting by date window first
    if q.since is not None and q.until is not None:
        delta = q.until - q.since
        if delta > timedelta(days=1):
            sub_queries = partition_by_date(q)
            result: list[tuple[Query, bool]] = []
            for sq in sub_queries:
                result.extend(
                    partition(
                        sq,
                        max_results,
                        estimate_fn,
                        secondary_axes,
                        depth + 1,
                        max_depth,
                    )
                )
            return result

    # Try secondary axes
    if secondary_axes:
        for axis_fn in secondary_axes:
            sub_queries = axis_fn(q)
            if len(sub_queries) > 1:
                result = []
                for sq in sub_queries:
                    result.extend(
                        partition(
                            sq,
                            max_results,
                            estimate_fn,
                            secondary_axes,
                            depth + 1,
                            max_depth,
                        )
                    )
                return result

    # No further splitting possible — mark as truncated
    logger.warning(
        "Cannot partition query further: terms=%s, estimated=%d, max=%d. "
        "Marking as truncated (potential sampling bias).",
        q.terms,
        estimated,
        max_results,
    )
    return [(q, True)]


def merge_date_ranges(queries: list[Query]) -> tuple[date | None, date | None]:
    """Compute the overall date range covered by a list of queries.

    Used for validation: ensures partitions cover the full window.
    """
    since_dates = [q.since for q in queries if q.since is not None]
    until_dates = [q.until for q in queries if q.until is not None]

    return (
        min(since_dates) if since_dates else None,
        max(until_dates) if until_dates else None,
    )
