"""Tests for query partitioning."""

from __future__ import annotations

from datetime import date

from msrkit.models import Query
from msrkit.partition import merge_date_ranges, partition, partition_by_date


class TestPartitionByDate:
    """Tests for date-based partitioning."""

    def test_split_wide_window(self) -> None:
        """A multi-day window is split in half."""
        q = Query(
            source="github",
            terms=["test"],
            since=date(2023, 1, 1),
            until=date(2023, 12, 31),
        )
        parts = partition_by_date(q)
        assert len(parts) == 2
        # Left half ends before right half starts
        assert parts[0].until is not None
        assert parts[1].since is not None
        assert parts[0].until < parts[1].since

    def test_two_day_window_split(self) -> None:
        """A 2-day window can be split into two single-day windows."""
        q = Query(
            source="github",
            terms=["test"],
            since=date(2023, 6, 15),
            until=date(2023, 6, 16),
        )
        parts = partition_by_date(q)
        assert len(parts) == 2
        assert parts[0].since == date(2023, 6, 15)
        assert parts[0].until == date(2023, 6, 15)
        assert parts[1].since == date(2023, 6, 16)
        assert parts[1].until == date(2023, 6, 16)

    def test_single_day_not_split(self) -> None:
        """A single-day window is not split."""
        q = Query(
            source="github",
            terms=["test"],
            since=date(2023, 6, 15),
            until=date(2023, 6, 15),
        )
        parts = partition_by_date(q)
        assert len(parts) == 1

    def test_no_dates(self) -> None:
        """Query without dates is returned unchanged."""
        q = Query(source="github", terms=["test"])
        parts = partition_by_date(q)
        assert len(parts) == 1


class TestPartition:
    """Tests for the recursive partition function."""

    def test_no_cap_single_partition(self) -> None:
        """No max_results → single partition, no truncation."""
        q = Query(source="test", terms=["x"])
        result = partition(q, max_results=None, estimate_fn=lambda _: 5000)
        assert len(result) == 1
        assert result[0][1] is False  # not truncated

    def test_within_cap(self) -> None:
        """Estimated results within cap → single partition."""
        q = Query(source="test", terms=["x"])
        result = partition(q, max_results=1000, estimate_fn=lambda _: 500)
        assert len(result) == 1
        assert result[0][1] is False

    def test_unknown_estimate(self) -> None:
        """Unknown estimate → single partition, not truncated."""
        q = Query(source="test", terms=["x"])
        result = partition(q, max_results=1000, estimate_fn=lambda _: None)
        assert len(result) == 1
        assert result[0][1] is False

    def test_date_split_when_over_cap(self) -> None:
        """Over cap with date range → splits by date."""
        q = Query(
            source="test",
            terms=["x"],
            since=date(2023, 1, 1),
            until=date(2023, 12, 31),
        )
        # Always return 2000 (over 1000 cap) to force splitting
        result = partition(q, max_results=1000, estimate_fn=lambda _: 2000)
        assert len(result) > 1

    def test_truncated_when_no_axis(self) -> None:
        """Single day, over cap, no secondary axis → truncated."""
        q = Query(
            source="test",
            terms=["x"],
            since=date(2023, 6, 15),
            until=date(2023, 6, 15),
        )
        result = partition(q, max_results=1000, estimate_fn=lambda _: 5000)
        assert len(result) == 1
        assert result[0][1] is True  # truncated!

    def test_secondary_axis(self) -> None:
        """Secondary axis splits when date can't."""
        q = Query(
            source="test",
            terms=["x"],
            since=date(2023, 6, 15),
            until=date(2023, 6, 15),
        )

        def split_by_lang(query: Query) -> list[Query]:
            return [
                query.model_copy(update={"extra": {"language": "Python"}}),
                query.model_copy(update={"extra": {"language": "JavaScript"}}),
            ]

        # Make estimate return small value for sub-queries to stop recursion
        call_count = 0

        def estimate(q: Query) -> int:
            nonlocal call_count
            call_count += 1
            # First call returns over-cap, subsequent calls return under-cap
            return 2000 if call_count == 1 else 500

        result = partition(
            q,
            max_results=1000,
            estimate_fn=estimate,
            secondary_axes=[split_by_lang],
        )
        assert len(result) == 2

    def test_partitions_cover_full_window(self) -> None:
        """Union of partition date ranges covers original window."""
        q = Query(
            source="test",
            terms=["x"],
            since=date(2023, 1, 1),
            until=date(2023, 12, 31),
        )

        result = partition(q, max_results=1000, estimate_fn=lambda _: 5000)
        queries = [r[0] for r in result]
        overall_since, overall_until = merge_date_ranges(queries)

        assert overall_since == date(2023, 1, 1)
        assert overall_until == date(2023, 12, 31)


class TestMergeDateRanges:
    """Tests for merge_date_ranges helper."""

    def test_empty_list(self) -> None:
        assert merge_date_ranges([]) == (None, None)

    def test_single_query(self) -> None:
        q = Query(
            source="test",
            terms=["x"],
            since=date(2023, 1, 1),
            until=date(2023, 6, 30),
        )
        assert merge_date_ranges([q]) == (date(2023, 1, 1), date(2023, 6, 30))
