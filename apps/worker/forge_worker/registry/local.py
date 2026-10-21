"""Content-addressed local artifact registry.

Artifacts are stored under their own ``sha256:`` digest, so the store is immutable by
construction: writing the same content twice is a no-op, and a digest can never refer
to different bytes later. Deployments resolve releases by digest only — there is no
mutable-tag path to deploy through.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from forge_core.ports import BuildResult, PublishResult


class LocalContentAddressedRegistry:
    """Filesystem registry: ``<root>/blobs/sha256/<hex>`` plus a JSON index."""

    name = "local"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._blobs = self._root / "blobs" / "sha256"
        self._index = self._root / "index"

    async def available(self) -> tuple[bool, str]:
        try:
            self._blobs.mkdir(parents=True, exist_ok=True)
            self._index.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"Artifact root {self._root} is not writable: {exc}"
        return True, f"Local content-addressed registry at {self._root}"

    @staticmethod
    def _hex(digest: str) -> str:
        return digest.split(":", 1)[1] if ":" in digest else digest

    def blob_path(self, digest: str) -> Path:
        return self._blobs / self._hex(digest)

    async def publish(
        self, result: BuildResult, *, image_name: str, version: str
    ) -> PublishResult:
        await self.available()
        destination = self.blob_path(result.digest)

        def _store() -> None:
            if destination.exists():
                # Immutable store: identical content is already published.
                return
            temporary = destination.with_suffix(".tmp")
            shutil.copyfile(result.artifact_path, temporary)
            # Atomic publish so a reader never observes a partial blob.
            temporary.replace(destination)

        await asyncio.to_thread(_store)

        reference = f"forge.local/{image_name}@{result.digest}"
        metadata = {
            "reference": reference,
            "digest": result.digest,
            "image_name": image_name,
            "version": version,
            "size_bytes": result.size_bytes,
            "published_at": datetime.now(UTC).isoformat(),
            "metadata": dict(result.metadata),
        }
        index_file = self._index / f"{self._hex(result.digest)}.json"
        await asyncio.to_thread(
            index_file.write_text, json.dumps(metadata, indent=2), "utf-8"
        )

        return PublishResult(
            reference=reference,
            digest=result.digest,
            registry=self.name,
            # Signing is not configured in this environment. Reporting it honestly
            # matters more than showing a green badge.
            signed=False,
            signature_reference=None,
        )

    async def resolve(self, digest: str) -> Path | None:
        path = self.blob_path(digest)
        return path if await asyncio.to_thread(path.exists) else None


__all__ = ["LocalContentAddressedRegistry"]
