"""Adapter registry: discovery and registration of source adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msrkit.adapters.base import BaseAdapter

# Global registry mapping adapter name → adapter class
_REGISTRY: dict[str, type[BaseAdapter]] = {}


def register(cls: type[BaseAdapter]) -> type[BaseAdapter]:
    """Register an adapter class in the global registry.

    Used as a class decorator on concrete adapters:

        @register
        class GitHubAdapter(BaseAdapter):
            name = "github"
            ...
    """
    _REGISTRY[cls.name] = cls
    return cls


def get_adapter(name: str) -> type[BaseAdapter]:
    """Get an adapter class by name.

    Raises:
        KeyError: If no adapter with the given name is registered.
    """
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown adapter '{name}'. Available: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]


def all_adapters() -> dict[str, type[BaseAdapter]]:
    """Return a copy of the full adapter registry."""
    return dict(_REGISTRY)


def discover_adapters() -> None:
    """Import all adapter modules to trigger registration.

    This must be called once at startup before accessing the registry.
    """
    # Importing each module triggers the @register decorator
    from msrkit.adapters import (  # noqa: F401
        bluesky,
        devto,
        discord,
        github,
        hackernews,
        huggingface,
        linkedin,
        reddit,
        rss,
        stackexchange,
        x_twitter,
    )
