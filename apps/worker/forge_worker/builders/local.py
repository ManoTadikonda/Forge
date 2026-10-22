"""Process builder: turns a workspace into a reproducible content-addressed artifact.

Reproducibility: the artifact is a tar stream written with normalised metadata —
sorted entries, fixed mtime, fixed uid/gid/mode. The same source tree therefore always
produces the same ``sha256`` digest, regardless of checkout time or host user. That is
what makes the digest a meaningful identity rather than an arbitrary label.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from forge_core.config import Settings
from forge_core.errors import BuildError
from forge_core.ports import BuildRequest, BuildResult

#: Directories never included in an artifact: caches, VCS metadata, local state.
_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        ".forge",
        ".idea",
        ".vscode",
        "dist",
        "build",
        ".eggs",
    }
)
_EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".so", ".log", ".tmp")
#: Fixed timestamp so archives are byte-identical across builds.
_FIXED_MTIME = 0


class LocalProcessBuilder:
    """Content-addresses a workspace into an immutable artifact.

    This does not produce an OCI image. It produces an immutable, digest-addressed
    bundle that the process deployment adapter can run. ``BuildKitBuilder`` is the
    adapter that produces real container images; it requires a container runtime.
    """

    name = "process"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def available(self) -> tuple[bool, str]:
        if not self._settings.allow_unsandboxed_builds:
            return (
                False,
                "The process builder does not provide kernel-level isolation and is "
                "disabled by default. Set FORGE_ALLOW_UNSANDBOXED_BUILDS=true to "
                "enable it for local development, or configure FORGE_BUILDER=buildkit "
                "with a container runtime.",
            )
        try:
            await asyncio.to_thread(
                Path(self._settings.artifact_root).mkdir, parents=True, exist_ok=True
            )
        except OSError as exc:
            return False, f"Artifact root is not writable: {exc}"
        return True, "Process builder (workspace isolation, not a kernel sandbox)"

    def _should_include(self, path: Path, workspace: Path) -> bool:
        relative = path.relative_to(workspace)
        if any(part in _EXCLUDED_DIRECTORIES for part in relative.parts):
            return False
        if path.name.startswith(".") and path.name not in (".forge.toml",):
            # Dotfiles other than the manifest are build noise, not source.
            return False
        return path.suffix not in _EXCLUDED_SUFFIXES

    def _collect(self, workspace: Path) -> list[Path]:
        files = [
            p
            for p in workspace.rglob("*")
            if p.is_file() and not p.is_symlink() and self._should_include(p, workspace)
        ]
        # Sorting is what makes the archive deterministic.
        return sorted(files, key=lambda p: str(p.relative_to(workspace)))

    def _pack(self, workspace: Path, files: list[Path]) -> tuple[bytes, int]:
        buffer = io.BytesIO()
        # Uncompressed tar: gzip embeds a timestamp, which would break reproducibility.
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for file_path in files:
                relative = file_path.relative_to(workspace)
                info = tarfile.TarInfo(name=str(relative))
                stat = file_path.stat()
                info.size = stat.st_size
                # Normalise every piece of variable metadata.
                info.mtime = _FIXED_MTIME
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mode = 0o755 if stat.st_mode & 0o111 else 0o644
                with file_path.open("rb") as handle:
                    archive.addfile(info, handle)
        payload = buffer.getvalue()
        return payload, len(payload)

    async def build(self, request: BuildRequest) -> BuildResult:
        usable, reason = await self.available()
        if not usable:
            raise BuildError(f"Builder unavailable: {reason}")

        workspace = Path(request.workspace)
        started = datetime.now(UTC)

        def _work() -> tuple[bytes, int, int]:
            if not workspace.is_dir():
                raise BuildError(f"Workspace {workspace} does not exist.")
            files = self._collect(workspace)
            if not files:
                raise BuildError("Workspace contains no files eligible for packaging.")
            payload, size = self._pack(workspace, files)
            return payload, size, len(files)

        payload, size, file_count = await asyncio.to_thread(_work)
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()

        artifact_root = Path(self._settings.artifact_root) / "staging"
        artifact_root.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_root / f"{digest.split(':')[1]}.tar"

        def _write() -> None:
            temporary = artifact_path.with_suffix(".tar.tmp")
            temporary.write_bytes(payload)
            temporary.replace(artifact_path)

        await asyncio.to_thread(_write)
        finished = datetime.now(UTC)

        return BuildResult(
            digest=digest,
            size_bytes=size,
            artifact_path=artifact_path,
            tags=(f"{request.image_name}:{request.version}",),
            logs=(
                f"Packaged {file_count} file(s) ({size} bytes) into a reproducible "
                f"archive.\nDigest: {digest}\n"
            ),
            metadata={
                "builder": self.name,
                "file_count": str(file_count),
                "reproducible": "true",
                "commit_sha": request.commit_sha,
                "repository_url": request.repository_url,
                "build_started_at": started.isoformat(),
                "build_finished_at": finished.isoformat(),
                "host_platform": os.uname().sysname,
            },
        )


__all__ = ["LocalProcessBuilder"]
