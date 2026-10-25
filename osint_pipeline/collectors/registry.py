"""Collector registry so the CLI/pipeline can select a source by name."""

from __future__ import annotations

from typing import Dict, List, Type

from .base import BaseCollector
from .file_collector import FileCollector
from .rss_collector import RSSCollector

_REGISTRY: Dict[str, Type[BaseCollector]] = {
    FileCollector.name: FileCollector,
    RSSCollector.name: RSSCollector,
}


def available_collectors() -> List[str]:
    return sorted(_REGISTRY)


def get_collector(name: str) -> Type[BaseCollector]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown collector '{name}'. Available: {available_collectors()}"
        )
