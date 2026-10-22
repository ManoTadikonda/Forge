"""BuildKit container-image builder.

STATUS: implemented against the documented ``docker buildx`` contract but **never
executed**. The development environment has no container runtime. Do not describe
this adapter as working until it has actually built an image.

Isolation note: this adapter deliberately does *not* mount the host Docker socket into
the build. It talks to a BuildKit daemon over its own address, so repository code never
gains control of the host daemon. Docker daemon access is host-equivalent privilege;
see docs/security.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

from forge_core.config import Settings
from forge_core.errors import BuildError
from forge_core.ports import BuildRequest, BuildResult


class BuildKitBuilder:
    name = "buildkit"

    def __init__(self, settings: Settings, *, binary: str = "docker") -> None:
        self._settings = settings
        self._binary = binary
        self._buildkit_host = os.environ.get("BUILDKIT_HOST", "")

    async def available(self) -> tuple[bool, str]:
        if shutil.which(self._binary) is None:
            return False, (
                f"{self._binary!r} is not installed. Install Docker with BuildKit, or "
                f"use FORGE_BUILDER=process for local development."
            )
        process = await asyncio.create_subprocess_exec(
            self._binary,
            "buildx",
            "version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return False, "docker buildx is unavailable: " + stdout.decode(
                "utf-8", "replace"
            ).strip()
        return True, stdout.decode("utf-8", "replace").strip()

    async def build(self, request: BuildRequest) -> BuildResult:
        usable, reason = await self.available()
        if not usable:
            raise BuildError(f"BuildKit builder unavailable: {reason}")

        workspace = Path(request.workspace)
        metadata_file = workspace / ".forge-buildkit-metadata.json"
        tag = f"{request.image_name}:{request.version}"

        argv = [
            self._binary,
            "buildx",
            "build",
            "--file",
            str(workspace / request.dockerfile),
            "--tag",
            tag,
            "--metadata-file",
            str(metadata_file),
            # Load into the local image store so the deployment adapter can run it.
            "--load",
            "--progress",
            "plain",
        ]
        for key, value in request.build_args.items():
            argv += ["--build-arg", f"{key}={value}"]
        argv.append(str(workspace))

        env = dict(os.environ)
        if self._buildkit_host:
            env["BUILDKIT_HOST"] = self._buildkit_host

        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workspace),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=request.timeout_seconds
            )
        except TimeoutError as exc:
            process.kill()
            raise BuildError(f"Image build exceeded {request.timeout_seconds}s.") from exc

        logs = stdout.decode("utf-8", "replace")
        if process.returncode != 0:
            raise BuildError(f"Image build failed with exit code {process.returncode}.")

        if not metadata_file.is_file():
            raise BuildError("BuildKit produced no metadata file; cannot resolve digest.")
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        digest = metadata.get("containerimage.digest", "")
        if not digest.startswith("sha256:"):
            raise BuildError("BuildKit metadata did not contain an image digest.")

        return BuildResult(
            digest=digest,
            size_bytes=int(metadata.get("containerimage.size", 0) or 0),
            artifact_path=workspace,
            tags=(tag,),
            logs=logs,
            metadata={"builder": self.name, "commit_sha": request.commit_sha},
        )


__all__ = ["BuildKitBuilder"]
