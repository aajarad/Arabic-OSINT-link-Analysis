"""Collect content from local JSON / JSONL exports.

This is the primary offline collector. It ingests platform exports or manually
curated datasets (the shape returned by most official research/export APIs),
which keeps collection reproducible and avoids unauthorized scraping.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Iterator, Union

from .base import BaseCollector, CollectedItem, EthicsPolicy

log = logging.getLogger(__name__)

# Common field aliases seen across platform exports mapped to CollectedItem.
_FIELD_ALIASES = {
    "text": ("text", "content", "body", "message", "full_text", "tweet", "caption"),
    "platform": ("platform", "source", "network", "site"),
    "source_url": ("source_url", "url", "link", "permalink"),
    "author": ("author", "user", "username", "screen_name", "handle", "name"),
    "author_id": ("author_id", "user_id", "uid"),
    "item_id": ("item_id", "id", "id_str", "post_id", "status_id"),
    "created_at": ("created_at", "date", "timestamp", "published", "time"),
    "lang": ("lang", "language", "lng"),
}


def _scalar(value):
    """Collapse nested platform objects (e.g. a Twitter ``user`` dict) to a string."""
    if isinstance(value, dict):
        for key in ("screen_name", "username", "name", "handle", "id_str", "id"):
            if value.get(key) not in (None, ""):
                return value[key]
        return None
    return value


def _pick(record: dict, field: str):
    for alias in _FIELD_ALIASES[field]:
        if alias in record:
            value = _scalar(record[alias]) if field != "text" else record[alias]
            if value not in (None, ""):
                return value
    return None


def _opt_str(value):
    return str(value) if value not in (None, "") else None


class FileCollector(BaseCollector):
    name = "file"

    def __init__(
        self,
        path: Union[str, Path],
        *,
        default_platform: str = "unknown",
        policy: EthicsPolicy | None = None,
    ) -> None:
        super().__init__(policy)
        self.path = Path(path)
        self.default_platform = default_platform
        self.malformed_lines = 0

    def _load_records(self) -> Iterator[dict]:
        # utf-8-sig transparently strips a BOM written by Windows tools.
        text = self.path.read_text(encoding="utf-8-sig")
        stripped = text.lstrip()
        if stripped.startswith("[") or stripped.startswith("{") and self._is_single_json(stripped):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed JSON in {self.path}: {exc}") from exc
            if isinstance(data, dict):
                for key in ("items", "data", "posts", "results", "tweets", "records"):
                    if isinstance(data.get(key), list):
                        data = data[key]
                        break
                else:
                    data = [data]
            for rec in data:
                if isinstance(rec, dict):
                    yield rec
                else:
                    self.malformed_lines += 1
            return
        # Otherwise JSONL: one JSON object per non-empty line; bad lines skipped.
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                log.warning("%s:%d: skipping malformed JSON line", self.path, lineno)
                self.malformed_lines += 1
                continue
            if isinstance(rec, dict):
                yield rec
            else:
                self.malformed_lines += 1

    @staticmethod
    def _is_single_json(stripped: str) -> bool:
        """A leading ``{`` is a whole-file JSON object only if it spans lines."""
        try:
            json.loads(stripped.splitlines()[0])
            return False  # first line is a complete object -> JSONL
        except (json.JSONDecodeError, IndexError):
            return True

    def _fetch(self) -> Iterable[CollectedItem]:
        if not self.path.exists():
            raise FileNotFoundError(f"Collection source not found: {self.path}")
        if not self.path.is_file():
            raise ValueError(f"Collection source is not a file: {self.path}")
        self._throttle()
        for record in self._load_records():
            text = _pick(record, "text")
            if not text:
                continue
            platform = _pick(record, "platform")
            if isinstance(platform, dict):
                platform = _scalar(platform)
            yield CollectedItem(
                text=str(text),
                platform=str(platform or self.default_platform),
                source_url=_opt_str(_pick(record, "source_url")),
                author=_opt_str(_pick(record, "author")),
                author_id=_opt_str(_pick(record, "author_id")),
                item_id=_opt_str(_pick(record, "item_id")),
                created_at=_opt_str(_pick(record, "created_at")),
                lang=_opt_str(_pick(record, "lang")),
                raw=record,
            )
