"""Content-addressed, tamper-evident archive.

Each collected item is written to a content-addressed blob (named by its
SHA-256) and recorded in an append-only JSONL manifest. Because the storage
key *is* the content hash, any later modification of a blob is detectable by
re-hashing, and the manifest preserves collection time and full provenance.

Semantics worth knowing:
    * One manifest line is appended per ``archive_item`` call, even when the
      content is identical to an existing blob (the blob is written once).
    * Repeated runs against the same archive directory keep appending.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ..collectors.base import CollectedItem
from ..utils import utc_now_iso
from ..utils.hashing import sha256_text


@dataclass
class ArchiveRecord:
    """One manifest line: the durable proof that an item was collected."""

    item_id: str
    content_sha256: str
    blob_path: str
    platform: str
    author: Optional[str]
    source_url: Optional[str]
    created_at: Optional[str]
    collected_at: Optional[str]
    archived_at: str
    collector: Optional[str]

    def to_dict(self) -> Dict:
        return asdict(self)


def _atomic_write_text(path: Path, data: str) -> None:
    """Write via a temp file + os.replace so a crash never leaves a torn blob."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class Archiver:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.blobs_dir = self.root / "blobs"
        self.manifest_path = self.root / "manifest.jsonl"
        self.blobs_dir.mkdir(parents=True, exist_ok=True)

    def _blob_path(self, digest: str) -> Path:
        # Shard by the first two hex chars to keep directories small.
        return self.blobs_dir / digest[:2] / f"{digest}.json"

    def archive_item(self, item: CollectedItem) -> ArchiveRecord:
        """Persist one item; blobs are de-duplicated by content hash."""
        digest = item.content_sha256 or sha256_text(item.text or "")
        item.content_sha256 = digest
        blob_path = self._blob_path(digest)

        # On-disk existence (not an in-memory set) decides whether to write, so
        # a deleted blob is healed and an existing one is never overwritten.
        if not blob_path.exists():
            payload = json.dumps(item.to_dict(), ensure_ascii=False, indent=2, default=str)
            _atomic_write_text(blob_path, payload)

        record = ArchiveRecord(
            item_id=item.item_id or digest,
            content_sha256=digest,
            blob_path=blob_path.relative_to(self.root).as_posix(),
            platform=item.platform,
            author=item.author,
            source_url=item.source_url,
            created_at=item.created_at,
            collected_at=item.collected_at,
            archived_at=utc_now_iso(),
            collector=item.collector,
        )
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record

    def archive_all(self, items: Iterable[CollectedItem]) -> List[ArchiveRecord]:
        return [self.archive_item(it) for it in items]

    # -- integrity ----------------------------------------------------------
    def _read_manifest(self) -> Tuple[List[Dict], List[int]]:
        """Return (valid records, 1-based line numbers of malformed lines)."""
        records: List[Dict] = []
        bad: List[int] = []
        if not self.manifest_path.exists():
            return records, bad
        text = self.manifest_path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad.append(n)
                continue
            if (not isinstance(rec, dict) or not rec.get("content_sha256")
                    or not rec.get("blob_path")):
                bad.append(n)
                continue
            records.append(rec)
        return records, bad

    def load_manifest(self) -> List[Dict]:
        """All well-formed manifest records (malformed lines are skipped;
        ``verify()`` reports them)."""
        return self._read_manifest()[0]

    def verify(self) -> Dict[str, object]:
        """Re-hash every blob and confirm it matches its manifest digest.

        Detects tampered, missing and unreadable blobs and malformed manifest
        lines. This is what turns the archive from 'a folder of files' into
        evidence.
        """
        records, bad_lines = self._read_manifest()
        problems: List[Dict[str, object]] = []
        for n in bad_lines:
            problems.append({"item_id": "", "issue": "malformed_manifest_line", "line": n})
        root = self.root.resolve()
        for rec in records:
            item_id = rec.get("item_id", "")
            blob = (self.root / rec["blob_path"]).resolve()
            if root not in blob.parents:
                problems.append({"item_id": item_id, "issue": "blob_path_outside_archive"})
                continue
            if not blob.is_file():
                problems.append({"item_id": item_id, "issue": "missing_blob"})
                continue
            try:
                stored = json.loads(blob.read_text(encoding="utf-8"))
                actual = sha256_text(stored.get("text") or "")
            except (ValueError, OSError, AttributeError):
                problems.append({"item_id": item_id, "issue": "unreadable_blob"})
                continue
            if actual != rec["content_sha256"] or blob.stem != rec["content_sha256"]:
                problems.append({"item_id": item_id, "issue": "hash_mismatch"})
        checked = len(records) + len(bad_lines)
        return {
            "checked": checked,
            "ok": checked - len(problems),
            "problems": problems,
            "verified_at": utc_now_iso(),
            "intact": not problems,
        }
