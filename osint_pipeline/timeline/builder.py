"""Build a chronological event timeline to support cross-source triangulation.

An event is anchored to a date (either a date *mentioned in the text* or, as a
fallback, the item's publish date) and carries the co-located entities and a
short snippet. Events sharing the same normalized date are grouped so an analyst
can see how many independent sources reported around the same moment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..nlp.ner import Entity
from ..nlp.dates import extract_dates
from ..utils.timeutil import parse_iso


@dataclass
class TimelineEvent:
    date: str                       # ISO-8601 (possibly partial)
    precision: str                  # day | month | year
    snippet: str
    entities: List[str] = field(default_factory=list)
    source: Optional[str] = None
    item_id: Optional[str] = None
    date_origin: str = "text"       # 'text' (mentioned) or 'published'

    def to_dict(self) -> Dict:
        return asdict(self)


_PRECISION_RANK = {"year": 0, "month": 1, "day": 2}


def _csv_safe(value: str) -> str:
    """Neutralize spreadsheet formula injection from untrusted post text."""
    if value and value[0] in "=+-@\t\r":
        return "'" + value
    return value


def _snippet(text: str, center: int, width: int = 80) -> str:
    start = max(0, center - width)
    end = min(len(text), center + width)
    frag = text[start:end].replace("\n", " ").strip()
    return ("…" if start > 0 else "") + frag + ("…" if end < len(text) else "")


class TimelineBuilder:
    def __init__(self) -> None:
        self._events: List[TimelineEvent] = []

    def add_document(
        self,
        text: str,
        *,
        entities: Optional[Iterable[Entity]] = None,
        source: Optional[str] = None,
        item_id: Optional[str] = None,
        published: Optional[str] = None,
    ) -> int:
        """Add all dated events found in a document. Returns events added."""
        ent_list = [e for e in (entities or []) if e.label in ("PERSON", "LOC", "ORG")]
        ent_names = sorted({e.text for e in ent_list})

        added = 0
        text = text or ""
        dates = extract_dates(text)
        for dm in dates:
            self._events.append(
                TimelineEvent(
                    date=dm.normalized,
                    precision=dm.precision,
                    snippet=_snippet(text, (dm.start + dm.end) // 2),
                    entities=ent_names,
                    source=source,
                    item_id=item_id,
                    date_origin="text",
                )
            )
            added += 1

        # Fallback: if the text carried no explicit date, anchor to publish time.
        if not dates and published:
            dt = parse_iso(published)
            if dt:
                self._events.append(
                    TimelineEvent(
                        date=dt.date().isoformat(),
                        precision="day",
                        snippet=_snippet(text, len(text) // 2),
                        entities=ent_names,
                        source=source,
                        item_id=item_id,
                        date_origin="published",
                    )
                )
                added += 1
        return added

    def events(self) -> List[TimelineEvent]:
        return sorted(self._events, key=lambda e: (e.date, _PRECISION_RANK.get(e.precision, 9)))

    def grouped(self) -> List[Dict]:
        """Group events by normalized date for a triangulation-friendly view."""
        buckets: Dict[str, List[TimelineEvent]] = {}
        for ev in self.events():
            buckets.setdefault(ev.date, []).append(ev)
        out = []
        for date in sorted(buckets):
            evs = buckets[date]
            out.append({
                "date": date,
                "event_count": len(evs),
                "source_count": len({e.source for e in evs if e.source}),
                "entities": sorted({name for e in evs for name in e.entities}),
                "events": [e.to_dict() for e in evs],
            })
        return out

    def to_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "event_count": len(self._events),
            "timeline": self.grouped(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def to_csv(self, path: str | Path) -> Path:
        import csv

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["date", "precision", "date_origin", "source",
                             "item_id", "entities", "snippet"])
            for ev in self.events():
                writer.writerow([ev.date, ev.precision, ev.date_origin, _csv_safe(ev.source or ""),
                                 _csv_safe(ev.item_id or ""),
                                 _csv_safe("; ".join(ev.entities)), _csv_safe(ev.snippet)])
        return path
