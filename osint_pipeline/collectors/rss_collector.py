"""Collect public RSS/Atom feeds (news portals, blog syndication).

RSS is a consent-based publishing mechanism: feeds are explicitly offered for
machine consumption, which makes them an ethically clean OSINT source. Requires
the optional ``feedparser`` dependency; raises a clear error if unavailable.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any, Callable, Iterable, List, Optional
from urllib.parse import urlparse, urlunparse
from urllib import robotparser

from .base import BaseCollector, CollectedItem, EthicsPolicy

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class CollectorUnavailable(RuntimeError):
    """Raised when a collector's optional dependency is missing."""


def _clean(value: Any) -> str:
    if not value:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", str(value)))).strip()


def _jsonable(entry: Any) -> dict:
    out = {}
    for k, v in dict(entry).items():
        try:
            out[k] = v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v)
        except Exception:  # noqa: BLE001
            out[k] = str(v)
    return out


class RSSCollector(BaseCollector):
    name = "rss"

    def __init__(
        self,
        feed_urls: List[str],
        *,
        platform: str = "rss",
        policy: Optional[EthicsPolicy] = None,
        parser: Optional[Callable[[str], Any]] = None,
        robots_checker: Optional[Callable[[str], bool]] = None,
    ) -> None:
        super().__init__(policy)
        if isinstance(feed_urls, str):
            feed_urls = [feed_urls]
        self.feed_urls = [u for u in (feed_urls or []) if u and u.strip()]
        if not self.feed_urls:
            raise ValueError("RSSCollector requires at least one feed URL")
        self.platform = platform
        self._parser = parser          # injectable (tests / alternative parsers)
        self._robots_checker = robots_checker
        self._robots_cache: dict = {}
        self.errors: List[str] = []

    # -- helpers ------------------------------------------------------------
    def _get_parser(self) -> Callable[[str], Any]:
        if self._parser is not None:
            return self._parser
        try:
            import feedparser  # type: ignore
        except ImportError as exc:
            raise CollectorUnavailable(
                "RSSCollector requires 'feedparser' (pip install feedparser)."
            ) from exc
        return feedparser.parse

    def _allowed_by_robots(self, url: str) -> bool:
        if not self.policy.respect_robots:
            return True
        if self._robots_checker is not None:
            return bool(self._robots_checker(url))
        parts = urlparse(url)
        if parts.scheme not in ("http", "https"):
            return True  # local/file feeds have no robots.txt
        root = urlunparse((parts.scheme, parts.netloc, "/robots.txt", "", "", ""))
        rp = self._robots_cache.get(root)
        if rp is None:
            rp = robotparser.RobotFileParser()
            rp.set_url(root)
            try:
                rp.read()
            except Exception:  # noqa: BLE001 - unreachable robots.txt => allow
                rp = False
            self._robots_cache[root] = rp
        return True if rp is False else rp.can_fetch("*", url)

    # -- fetching -----------------------------------------------------------
    def _fetch(self) -> Iterable[CollectedItem]:
        parse = self._get_parser()
        for url in self.feed_urls:
            if not self._allowed_by_robots(url):
                msg = f"{url}: disallowed by robots.txt, skipped"
                log.warning(msg)
                self.errors.append(msg)
                continue
            self._throttle()
            parsed = parse(url)
            feed_meta = getattr(parsed, "feed", None) or {}
            entries = getattr(parsed, "entries", None) or []
            if getattr(parsed, "bozo", False) and not entries:
                msg = f"{url}: feed could not be parsed ({getattr(parsed, 'bozo_exception', 'unknown error')})"
                log.warning(msg)
                self.errors.append(msg)
                continue
            source = feed_meta.get("title", url) if hasattr(feed_meta, "get") else url
            for entry in entries:
                title = _clean(entry.get("title"))
                summary = _clean(entry.get("summary") or entry.get("description"))
                text = f"{title}. {summary}" if title and summary else (summary or title)
                yield CollectedItem(
                    text=text,
                    platform=self.platform,
                    source_url=entry.get("link"),
                    author=entry.get("author") or source,
                    item_id=entry.get("id") or entry.get("link"),
                    created_at=entry.get("published") or entry.get("updated"),
                    lang=entry.get("language") or (feed_meta.get("language") if hasattr(feed_meta, "get") else None),
                    raw=_jsonable(entry),
                )
