"""Collection layer.

All collectors are *ethics-first*: they only ingest content that is publicly
available, they record full provenance for every item, and they honour
per-source rate limits. Collectors never authenticate as another user, never
bypass access controls, and never fabricate content.
"""

from .base import CollectedItem, BaseCollector, EthicsPolicy
from .file_collector import FileCollector
from .rss_collector import RSSCollector, CollectorUnavailable
from .registry import get_collector, available_collectors

__all__ = [
    "CollectedItem",
    "BaseCollector",
    "EthicsPolicy",
    "FileCollector",
    "RSSCollector",
    "CollectorUnavailable",
    "get_collector",
    "available_collectors",
]
