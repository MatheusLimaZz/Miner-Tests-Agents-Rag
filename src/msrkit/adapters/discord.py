"""Discord adapter — UNSUPPORTED by default.

No global search exists. The bot API only reads messages from servers
where the bot is installed. Requires the privileged message content
intent and explicit admin authorization.

Mining Discord communities requires:
1. Bot installed by server admin
2. Privileged message content intent approved
3. Ethical review before collection
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import ClassVar

from msrkit.adapters.base import BaseAdapter
from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Item,
    Query,
    RateLimit,
    RawItem,
    SourcePolicy,
    SourceUnsupportedError,
)
from msrkit.registry import register

logger = logging.getLogger(__name__)


@register
class DiscordAdapter(BaseAdapter):
    """Discord adapter — UNSUPPORTED by default.

    Requires bot token, guild IDs, and admin authorization.
    """

    name: ClassVar[str] = "discord"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=True,
        auth_env_vars=["DISCORD_BOT_TOKEN", "DISCORD_GUILD_IDS"],
        rate_limit=RateLimit(
            requests=5,
            per_seconds=60,
            burst=3,
        ),
        max_results_per_query=None,
        max_page_size=100,
        max_pages=None,
        supports_full_text_search=False,
        supports_date_filter=False,
        redistribution="metadata_only",
        tos_url="https://discord.com/developers/docs/policies-and-agreements/terms-of-service",
        docs_url="https://discord.com/developers/docs/intro",
        notes=(
            "No global search. Bot API only reads servers where installed. "
            "Requires privileged message content intent and explicit admin authorization. "
            "Must pass ethical review before collection."
        ),
    )

    def available(self) -> Availability:
        """Check for Discord credentials."""
        token = self._env("DISCORD_BOT_TOKEN")
        guild_ids = self._env("DISCORD_GUILD_IDS")

        if not token or not guild_ids:
            missing = []
            if not token:
                missing.append("DISCORD_BOT_TOKEN")
            if not guild_ids:
                missing.append("DISCORD_GUILD_IDS")
            return Availability(
                status=AvailabilityStatus.UNSUPPORTED,
                reason=(
                    "Discord bot credentials not configured. Mining Discord "
                    "communities requires a bot installed by server admins, "
                    "the privileged message content intent, and explicit "
                    "authorization. See README for ethical requirements."
                ),
                missing_env=missing,
            )
        return Availability(
            status=AvailabilityStatus.DEGRADED,
            reason=(
                "Discord bot token and guild IDs configured. Ensure admin "
                "authorization and ethical review have been completed before "
                "running collection."
            ),
        )

    def estimate(self, q: Query) -> int | None:
        return None

    def search(self, q: Query) -> Iterator[RawItem]:
        avail = self.available()
        if avail.status == AvailabilityStatus.UNSUPPORTED:
            raise SourceUnsupportedError(self.name, avail.reason)
        # Stub for v0
        return iter([])

    def normalize(self, raw: RawItem) -> Item:
        raise NotImplementedError("Discord normalization not implemented in v0.")
