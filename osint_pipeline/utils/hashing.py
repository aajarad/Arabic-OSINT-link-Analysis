"""Deterministic SHA-256 hashing helpers for content integrity / traceability."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Union


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Return the hex SHA-256 digest of text (UTF-8, NFC-independent bytes).

    We hash the exact UTF-8 encoding of the string as supplied. Callers that
    want normalization-insensitive hashes should normalize before hashing.
    """
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Union[str, Path], chunk_size: int = 65536) -> str:
    """Stream a file from disk and return its hex SHA-256 digest."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()
