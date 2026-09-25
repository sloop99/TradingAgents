"""Small content-addressed cache for immutable JSON research snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any


class EvidenceCache:
    """Store JSON snapshots behind hashed keys and content digests.

    The key index is mutable, but snapshots are immutable and addressed by the
    SHA-256 digest of their canonical UTF-8 JSON representation.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    @staticmethod
    def _key_digest(key: str) -> str:
        if not isinstance(key, str):
            raise TypeError("key must be a string")
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _serialize(payload: dict[str, Any] | list[Any]) -> bytes:
        if not isinstance(payload, (dict, list)):
            raise TypeError("payload must be a dict or list")
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def _index_path(self, key: str) -> Path:
        return self.root / "index" / f"{self._key_digest(key)}.json"

    def _snapshot_path(self, digest: str) -> Path:
        return self.root / "snapshots" / f"{digest}.json"

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise

    @staticmethod
    def _snapshot_matches(path: Path, content: bytes) -> bool:
        try:
            with path.open("rb") as stream:
                return stream.read() == content
        except FileNotFoundError:
            return False

    def put_json(self, key: str, payload: dict[str, Any] | list[Any]) -> str:
        """Write a snapshot and point ``key`` at it, returning its SHA-256 digest."""
        content = self._serialize(payload)
        digest = hashlib.sha256(content).hexdigest()
        snapshot = self._snapshot_path(digest)
        if not self._snapshot_matches(snapshot, content):
            self._atomic_write(snapshot, content)

        index = json.dumps(
            {"digest": digest, "wrote_at": time.time()},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self._atomic_write(self._index_path(key), index)
        return digest

    def get_json(self, key: str, max_age_seconds: float | None = None) -> dict[str, Any] | list[Any] | None:
        """Read the snapshot for ``key`` or return ``None`` on a cache miss."""
        index_path = self._index_path(key)
        try:
            with index_path.open("rb") as stream:
                index = json.load(stream)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeError):
            return None

        if not isinstance(index, dict):
            return None
        digest = index.get("digest")
        wrote_at = index.get("wrote_at")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return None
        try:
            timestamp = float(wrote_at)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(timestamp):
            return None
        if max_age_seconds is not None:
            if max_age_seconds < 0:
                raise ValueError("max_age_seconds must be non-negative or None")
            if time.time() - timestamp > max_age_seconds:
                return None

        try:
            with self._snapshot_path(digest).open("rb") as stream:
                payload = json.load(stream)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeError):
            return None
        if not isinstance(payload, (dict, list)):
            return None
        try:
            canonical = self._serialize(payload)
        except (TypeError, ValueError, OverflowError, UnicodeError):
            return None
        if hashlib.sha256(canonical).hexdigest() != digest:
            return None
        return json.loads(canonical.decode("utf-8"))
