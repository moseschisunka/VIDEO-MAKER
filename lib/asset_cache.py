"""Content-addressed asset cache with integrity-checked resume semantics.

The provider/stock caches in OpenMontage intentionally operate below the
canonical asset manifest.  This module adds the missing production boundary:
an asset is reusable only when its request key, validation facts, and current
bytes all agree.  A corrupt or mismatched entry is removed and reported as a
miss so the caller can regenerate that one asset without invalidating a run.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from lib.media_ingestion import file_sha256, validate_media_file
from lib.media_contracts import AssetRequest, build_asset_request


class AssetCacheError(ValueError):
    """Raised when a cache operation cannot preserve asset integrity."""


@dataclass
class AssetCacheEntry:
    request_key: str
    asset_id: str
    file_name: str
    path: str
    sha256: str
    size_bytes: int
    media_type: str
    validation: dict[str, Any]
    metadata: dict[str, Any]
    created_at: float
    last_access_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_key": self.request_key,
            "asset_id": self.asset_id,
            "file_name": self.file_name,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "validation": dict(self.validation),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "last_access_at": self.last_access_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AssetCacheEntry":
        return cls(
            request_key=str(raw["request_key"]),
            asset_id=str(raw["asset_id"]),
            file_name=str(raw["file_name"]),
            path=str(raw.get("path") or raw["file_name"]),
            sha256=str(raw["sha256"]),
            size_bytes=int(raw["size_bytes"]),
            media_type=str(raw["media_type"]),
            validation=dict(raw.get("validation") or {}),
            metadata=dict(raw.get("metadata") or {}),
            created_at=float(raw.get("created_at", 0.0) or 0.0),
            last_access_at=float(raw.get("last_access_at", 0.0) or 0.0),
        )


def _copy_or_link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


class AssetCache:
    """Small filesystem cache keyed by the canonical asset request hash."""

    MANIFEST_NAME = "asset_cache.json"

    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.cache_dir / self.MANIFEST_NAME
        self.lock_path = self.cache_dir / "asset_cache.lock"

    @contextmanager
    def _locked(self):
        """Serialize manifest mutations when multiple runs resume together."""
        try:
            import filelock  # type: ignore

            with filelock.FileLock(str(self.lock_path), timeout=60):
                yield
        except ImportError:
            # Atomic replacement still protects readers; the fallback is only
            # used in minimal installations without the optional dependency.
            yield

    def _read(self) -> dict[str, AssetCacheEntry]:
        if not self.manifest_path.is_file():
            return {}
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        entries: dict[str, AssetCacheEntry] = {}
        for item in raw.get("entries", []) if isinstance(raw, Mapping) else []:
            try:
                entry = AssetCacheEntry.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            entries[entry.request_key] = entry
        return entries

    def _write(self, entries: Mapping[str, AssetCacheEntry]) -> None:
        fd, temp_name = tempfile.mkstemp(prefix="asset_cache.", suffix=".tmp", dir=str(self.cache_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": "1.0", "entries": [e.to_dict() for e in entries.values()]}, handle, indent=2)
                handle.write("\n")
            os.replace(temp_name, self.manifest_path)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def put(
        self,
        request: Mapping[str, Any] | AssetRequest,
        source_path: Path | str,
        *,
        asset_id: str,
        media_type: str | None = None,
        validation: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AssetCacheEntry:
        req = build_asset_request(request)
        source = Path(source_path).expanduser().resolve()
        if not source.is_file() or source.stat().st_size <= 0:
            raise AssetCacheError(f"cannot cache missing or empty asset: {source}")
        facts = dict(validation or {})
        if not facts.get("sha256") or not facts.get("mime_type"):
            facts = validate_media_file(source, media_type or req.media_type, strict_decode=True, min_bytes=128)
        digest = file_sha256(source)
        if str(facts.get("sha256")) != digest:
            raise AssetCacheError("asset validation hash differs from current bytes")
        suffix = source.suffix.lower() or ".bin"
        stable_name = f"{req.stable_key}{suffix}"
        target = self.cache_dir / stable_name
        part = target.with_name(target.name + ".part")
        part.unlink(missing_ok=True)
        try:
            shutil.copy2(source, part)
            if file_sha256(part) != digest:
                raise AssetCacheError("cache copy hash differs from source")
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)
        now = time.time()
        entry = AssetCacheEntry(
            request_key=req.stable_key,
            asset_id=str(asset_id),
            file_name=stable_name,
            path=stable_name,
            sha256=digest,
            size_bytes=target.stat().st_size,
            media_type=media_type or req.media_type,
            validation=facts,
            metadata=dict(metadata or {}),
            created_at=now,
            last_access_at=now,
        )
        with self._locked():
            entries = self._read()
            entries[req.stable_key] = entry
            self._write(entries)
        return entry

    def get(
        self,
        request: Mapping[str, Any] | AssetRequest,
        *,
        destination: Path | str | None = None,
        validate: bool = True,
    ) -> dict[str, Any] | None:
        req = build_asset_request(request)
        with self._locked():
            entries = self._read()
            entry = entries.get(req.stable_key)
            if entry is None:
                return None
            source = self.cache_dir / entry.file_name
            try:
                if not source.is_file() or source.stat().st_size != entry.size_bytes:
                    raise AssetCacheError("cached asset is missing or has changed size")
                if file_sha256(source) != entry.sha256:
                    raise AssetCacheError("cached asset hash mismatch")
                if validate:
                    facts = validate_media_file(source, entry.media_type, strict_decode=True, min_bytes=128)
                    if facts.get("sha256") != entry.sha256:
                        raise AssetCacheError("cached validation hash mismatch")
                else:
                    facts = dict(entry.validation)
            except Exception:
                entries.pop(req.stable_key, None)
                self._write(entries)
                source.unlink(missing_ok=True)
                return None
            entry.last_access_at = time.time()
            entries[req.stable_key] = entry
            self._write(entries)
            copied_path = source
            if destination is not None:
                target = Path(destination).expanduser().resolve()
                part = target.with_name(target.name + ".part")
                part.unlink(missing_ok=True)
                try:
                    _copy_or_link(source, part)
                    os.replace(part, target)
                    copied_path = target
                finally:
                    part.unlink(missing_ok=True)
            return {"hit": True, "path": str(copied_path), "asset_id": entry.asset_id, "request_key": entry.request_key, "sha256": entry.sha256, "size_bytes": entry.size_bytes, "validation": facts, "metadata": dict(entry.metadata)}


__all__ = ["AssetCache", "AssetCacheEntry", "AssetCacheError"]
