"""Contract tests for all adapters.

Parametrized over the registry: every adapter must implement the
SourceAdapter Protocol, declare a valid policy, and available()
must NOT make network requests.
"""

from __future__ import annotations

import pytest

from msrkit.models import AvailabilityStatus, SourcePolicy
from msrkit.registry import all_adapters, discover_adapters


@pytest.fixture(scope="module", autouse=True)
def _discover() -> None:
    """Discover all adapters before running tests."""
    discover_adapters()


def _adapter_names() -> list[str]:
    discover_adapters()
    return sorted(all_adapters().keys())


@pytest.mark.parametrize("name", _adapter_names())
class TestAdapterContract:
    """Contract tests that every adapter must satisfy."""

    def test_has_name(self, name: str) -> None:
        adapter_cls = all_adapters()[name]
        assert hasattr(adapter_cls, "name")
        assert isinstance(adapter_cls.name, str)
        assert len(adapter_cls.name) > 0

    def test_has_version(self, name: str) -> None:
        adapter_cls = all_adapters()[name]
        assert hasattr(adapter_cls, "version")
        assert isinstance(adapter_cls.version, str)

    def test_has_valid_policy(self, name: str) -> None:
        adapter_cls = all_adapters()[name]
        assert hasattr(adapter_cls, "policy")
        policy = adapter_cls.policy
        assert isinstance(policy, SourcePolicy)
        assert policy.rate_limit is not None
        assert policy.redistribution in (
            "metadata_only",
            "full_text_with_attribution",
            "full_text",
        )

    def test_available_returns_availability(self, name: str) -> None:
        """available() returns a valid Availability without network."""
        adapter_cls = all_adapters()[name]
        adapter = adapter_cls()
        avail = adapter.available()

        assert avail.status in (
            AvailabilityStatus.OK,
            AvailabilityStatus.DEGRADED,
            AvailabilityStatus.UNSUPPORTED,
        )
        assert isinstance(avail.reason, str)
        assert len(avail.reason) > 0

    def test_has_search_method(self, name: str) -> None:
        adapter_cls = all_adapters()[name]
        assert hasattr(adapter_cls, "search")
        assert callable(adapter_cls.search)

    def test_has_normalize_method(self, name: str) -> None:
        adapter_cls = all_adapters()[name]
        assert hasattr(adapter_cls, "normalize")
        assert callable(adapter_cls.normalize)

    def test_has_estimate_method(self, name: str) -> None:
        adapter_cls = all_adapters()[name]
        assert hasattr(adapter_cls, "estimate")
        assert callable(adapter_cls.estimate)

    def test_linkedin_always_unsupported(self, name: str) -> None:
        """LinkedIn must always return UNSUPPORTED (spec §7.11)."""
        if name != "linkedin":
            pytest.skip("Not LinkedIn")
        adapter_cls = all_adapters()[name]
        adapter = adapter_cls()
        avail = adapter.available()
        assert avail.status == AvailabilityStatus.UNSUPPORTED
        assert "public content-search API" in avail.reason
