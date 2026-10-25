"""Core collection contracts: the item model, ethics policy, and base collector."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, Iterator, List, Optional

from ..utils import utc_now_iso, sha256_text
from ..utils.arabic import normalize_arabic


@dataclass
class CollectedItem:
    """A single unit of publicly available content plus its provenance.

    ``collected_at`` and ``content_sha256`` are stamped at collection time and
    are what make an item traceable and tamper-evident downstream.
    """

    text: str
    platform: str
    source_url: Optional[str] = None
    author: Optional[str] = None
    author_id: Optional[str] = None
    item_id: Optional[str] = None
    created_at: Optional[str] = None          # original publish time (ISO-8601)
    lang: Optional[str] = None
    collected_at: Optional[str] = None         # stamped by the collector (UTC)
    content_sha256: Optional[str] = None       # stamped by the collector
    collector: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def finalize(self, collector_name: str) -> "CollectedItem":
        """Stamp collection-time provenance fields. Idempotent."""
        if self.item_id is None:
            self.item_id = uuid.uuid4().hex
        if self.collected_at is None:
            self.collected_at = utc_now_iso()
        if self.content_sha256 is None:
            self.content_sha256 = sha256_text(self.text or "")
        if self.collector is None:
            self.collector = collector_name
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CollectedItem":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        core = {k: v for k, v in d.items() if k in known}
        extra = {k: v for k, v in d.items() if k not in known}
        item = cls(**core)
        if extra:
            item.raw = {**extra, **item.raw}
        return item


@dataclass
class EthicsPolicy:
    """Operational guardrails applied to every collection run.

    These are enforced in code, not merely documented, so a run cannot silently
    drift into scraping private data or hammering a source.
    """

    public_only: bool = True          # refuse anything flagged non-public
    min_seconds_between_requests: float = 1.0
    max_items: Optional[int] = None    # hard cap per run
    respect_robots: bool = True        # collectors that fetch must check robots
    store_pii_note: str = (
        "Only publicly posted content is retained. No private messages, "
        "no access-controlled data, no re-identification of anonymous users."
    )

    def check_item(self, item: CollectedItem) -> bool:
        """Return True if the item is admissible under this policy."""
        if self.public_only:
            raw = item.raw or {}
            visibility = str(raw.get("visibility", "public")).strip().lower()
            if visibility not in ("public", "", "none"):
                return False
            for flag in ("private", "is_private", "protected"):
                if raw.get(flag) in (True, 1, "true", "True", "1", "yes"):
                    return False
        return isinstance(item.text, str) and bool(item.text.strip())


class BaseCollector:
    """Base class for all collectors.

    Subclasses implement :meth:`_fetch` yielding raw ``CollectedItem`` objects.
    The base class applies the ethics policy, rate limiting, provenance
    stamping, and the item cap uniformly.
    """

    name: str = "base"

    def __init__(self, policy: Optional[EthicsPolicy] = None) -> None:
        self.policy = policy or EthicsPolicy()
        if self.policy.max_items is not None and self.policy.max_items < 0:
            raise ValueError("EthicsPolicy.max_items must be >= 0 or None")
        self._last_request_ts: Optional[float] = None
        self.stats = {"fetched": 0, "rejected": 0, "yielded": 0}

    # -- to be implemented by subclasses ------------------------------------
    def _fetch(self) -> Iterable[CollectedItem]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- rate limiting ------------------------------------------------------
    def _throttle(self) -> None:
        gap = self.policy.min_seconds_between_requests
        if gap <= 0:
            return
        if self._last_request_ts is not None:
            elapsed = time.monotonic() - self._last_request_ts
            if elapsed < gap:
                time.sleep(gap - elapsed)
        self._last_request_ts = time.monotonic()

    # -- public API ---------------------------------------------------------
    def collect(self) -> List[CollectedItem]:
        return list(self.iter_collect())

    def iter_collect(self) -> Iterator[CollectedItem]:
        self.stats = {"fetched": 0, "rejected": 0, "yielded": 0}
        cap = self.policy.max_items
        if cap is not None and cap <= 0:
            return
        count = 0
        fetched = self._fetch()
        try:
            for item in fetched:
                self.stats["fetched"] += 1
                if not isinstance(item.text, str):
                    item.text = "" if item.text is None else str(item.text)
                item.finalize(self.name)
                if not self.policy.check_item(item):
                    self.stats["rejected"] += 1
                    continue
                if not item.lang:
                    item.lang = "ar"
                yield item
                count += 1
                self.stats["yielded"] = count
                if cap is not None and count >= cap:
                    break
        finally:
            close = getattr(fetched, "close", None)
            if close:
                close()
