"""Amazon ECR registry adapter.

STATUS: implemented against the documented ECR/Docker CLI contract but **never
executed**. The development environment has no container runtime, no AWS account and
no credentials. It must not be described as working until it has actually run.

It is included so the registry port demonstrably has a second implementation and the
abstraction is not shaped around a single adapter.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from forge_core.errors import BuildError
from forge_core.ports import BuildResult, PublishResult


class EcrRegistry:
    name = "ecr"

    def __init__(
        self,
        *,
        registry_url: str | None = None,
        region: str | None = None,
        docker_binary: str = "docker",
        aws_binary: str = "aws",
    ) -> None:
        self._registry_url = registry_url or os.environ.get("FORGE_ECR_REGISTRY", "")
        self._region = region or os.environ.get("AWS_REGION", "")
        self._docker = docker_binary
        self._aws = aws_binary

    async def available(self) -> tuple[bool, str]:
        if shutil.which(self._docker) is None:
            return False, f"{self._docker!r} is not installed; ECR publishing needs it."
        if shutil.which(self._aws) is None:
            return False, f"{self._aws!r} is not installed; ECR login needs it."
        if not self._registry_url:
            return False, "FORGE_ECR_REGISTRY is not set."
        if not self._region:
            return False, "AWS_REGION is not set."
        return True, f"ECR registry {self._registry_url} in {self._region}"

    async def _run(self, *args: str) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        stdout, _ = await process.communicate()
        return process.returncode or 0, stdout.decode("utf-8", "replace")

    async def publish(
        self, result: BuildResult, *, image_name: str, version: str
    ) -> PublishResult:
        usable, reason = await self.available()
        if not usable:
            raise BuildError(f"ECR registry is not usable: {reason}")

        # `aws ecr get-login-password | docker login --password-stdin` is the
        # documented flow; the password is never placed on a command line.
        code, output = await self._run(
            self._aws, "ecr", "get-login-password", "--region", self._region
        )
        if code != 0:
            raise BuildError("Failed to obtain an ECR authorization token.")
        password = output.strip()

        login = await asyncio.create_subprocess_exec(
            self._docker, "login", "--username", "AWS", "--password-stdin",
            self._registry_url,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await login.communicate(password.encode())
        if login.returncode != 0:
            raise BuildError("docker login against ECR failed.")

        target = f"{self._registry_url}/{image_name}:{version}"
        for args in (
            (self._docker, "tag", f"{image_name}:{version}", target),
            (self._docker, "push", target),
        ):
            code, output = await self._run(*args)
            if code != 0:
                raise BuildError(f"{args[1]} failed with exit code {code}.")

        code, inspect = await self._run(
            self._docker, "inspect", "--format={{index .RepoDigests 0}}", target
        )
        if code != 0 or "@" not in inspect:
            raise BuildError("Could not resolve the pushed image digest from ECR.")
        digest = inspect.strip().split("@", 1)[1]

        return PublishResult(
            reference=f"{self._registry_url}/{image_name}@{digest}",
            digest=digest,
            registry=self.name,
            signed=False,
        )

    async def resolve(self, digest: str) -> Path | None:
        # Remote registry: there is no local path for a digest.
        return None


__all__ = ["EcrRegistry"]
