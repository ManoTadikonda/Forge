"""Container deployment adapter.

STATUS: implemented against the documented Docker CLI contract but **never executed**.
The development environment has no container runtime. Do not describe this adapter as
working until it has actually run a container.

Privilege note: this adapter requires access to a container runtime socket, which is
host-equivalent privilege. It is therefore intended to run in a dedicated component
with a narrow API, never inside the build worker that executes untrusted repository
code. See docs/security.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import httpx
from forge_core.errors import DeploymentError
from forge_core.ports import ReleaseHandle, ReleaseSpec


class DockerDeploymentAdapter:
    name = "docker"

    def __init__(
        self,
        *,
        router_admin_url: str,
        binary: str = "docker",
        network: str = "forge",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._router_admin_url = router_admin_url.rstrip("/")
        self._binary = binary
        self._network = network
        self._client = http_client
        self._handles: dict[str, ReleaseHandle] = {}

    async def _run(self, *args: str) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            self._binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        return process.returncode or 0, stdout.decode("utf-8", "replace")

    async def available(self) -> tuple[bool, str]:
        if shutil.which(self._binary) is None:
            return False, (
                f"{self._binary!r} is not installed. Install Docker, or use "
                f"FORGE_DEPLOY_ADAPTER=process for a container-free local run."
            )
        code, output = await self._run("info", "--format", "{{.ServerVersion}}")
        if code != 0:
            return False, f"Container runtime is not responding: {output.strip()[:200]}"
        return True, f"Docker {output.strip()}"

    @staticmethod
    def _container_name(spec_or_handle: ReleaseSpec | ReleaseHandle) -> str:
        return f"forge-{spec_or_handle.release_id}"

    async def start_release(self, spec: ReleaseSpec) -> ReleaseHandle:
        usable, reason = await self.available()
        if not usable:
            raise DeploymentError(f"Docker adapter unavailable: {reason}")

        name = self._container_name(spec)
        # Idempotency: an already-running container is adopted, not duplicated.
        code, running = await self._run("ps", "--filter", f"name=^{name}$", "--format", "{{.ID}}")
        if code == 0 and running.strip():
            handle = await self._inspect(name, spec)
            self._handles[spec.release_id] = handle
            return handle

        await self._run("rm", "-f", name)

        argv = [
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            self._network,
            # Least privilege: no new privileges, read-only root, dropped caps.
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--read-only",
            # Container-internal tmpfs, not a host path: the root filesystem is
            # read-only so the app needs a small writable scratch area.
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108
            "--user",
            "10001:10001",
            # Resource limits so one release cannot starve the host.
            "--memory",
            "512m",
            "--cpus",
            "1.0",
            "--pids-limit",
            "256",
            "--publish",
            "0",  # ephemeral host port
            "--restart",
            "no",
            "--label",
            "forge.managed=true",
            "--label",
            f"forge.release={spec.release_id}",
            "--label",
            f"forge.service={spec.service_name}",
            "--label",
            f"forge.environment={spec.environment}",
        ]
        for key, value in {
            "SAMPLE_VERSION": spec.version,
            "SAMPLE_RELEASE_ID": spec.release_id,
            "SAMPLE_IMAGE_DIGEST": spec.image_digest,
            **dict(spec.env),
        }.items():
            argv += ["--env", f"{key}={value}"]

        # Deploy strictly by digest; a mutable tag is never used to resolve a release.
        argv.append(
            spec.image_digest
            if "@" in spec.image_digest
            else f"{spec.service_name}@{spec.image_digest}"
        )

        code, output = await self._run(*argv)
        if code != 0:
            raise DeploymentError(f"Failed to start container: {output.strip()[:300]}")

        handle = await self._inspect(name, spec)
        self._handles[spec.release_id] = handle
        return handle

    async def _inspect(self, name: str, spec: ReleaseSpec) -> ReleaseHandle:
        code, output = await self._run("inspect", name)
        if code != 0:
            raise DeploymentError(f"Could not inspect container {name}.")
        data = json.loads(output)[0]
        ports = data.get("NetworkSettings", {}).get("Ports", {}) or {}
        endpoint = ""
        for bindings in ports.values():
            if bindings:
                endpoint = f"http://127.0.0.1:{bindings[0]['HostPort']}"
                break
        if not endpoint:
            raise DeploymentError(f"Container {name} exposed no reachable port.")
        return ReleaseHandle(
            release_id=spec.release_id,
            version=spec.version,
            image_digest=spec.image_digest,
            endpoints=(endpoint,),
            instances=(data.get("Id", "")[:12],),
            started_at=datetime.now(UTC),
        )

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
        return self._client

    async def wait_ready(self, handle: ReleaseHandle, *, timeout_seconds: int) -> bool:
        client = await self._http()
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            try:
                if (
                    await client.get(f"{handle.primary_endpoint}/ready", timeout=3.0)
                ).status_code == 200:
                    return True
            except httpx.RequestError:
                pass
            await asyncio.sleep(0.5)
        return False

    async def probe(self, handle: ReleaseHandle) -> Mapping[str, object]:
        client = await self._http()
        observed_at = datetime.now(UTC)

        async def _get(path: str) -> httpx.Response | None:
            try:
                return await client.get(f"{handle.primary_endpoint}{path}", timeout=3.0)
            except httpx.RequestError:
                return None

        health, ready, stats_response = await asyncio.gather(
            _get("/health"), _get("/ready"), _get("/api/stats")
        )
        stats = {}
        if stats_response is not None and stats_response.status_code == 200:
            with contextlib.suppress(ValueError):
                stats = stats_response.json()
        return {
            "observed_at": observed_at,
            "healthy": health is not None and health.status_code == 200,
            "ready": ready is not None and ready.status_code == 200,
            "requests": int(stats.get("requests", 0) or 0),
            "errors": int(stats.get("errors", 0) or 0),
            "latency_p50_ms": stats.get("latency_p50_ms"),
            "latency_p95_ms": stats.get("latency_p95_ms"),
            "latency_p99_ms": stats.get("latency_p99_ms"),
            "version": stats.get("version", handle.version),
        }

    async def set_traffic_weights(
        self, service_name: str, environment: str, weights: Mapping[str, int]
    ) -> None:
        if sum(weights.values()) != 100:
            raise DeploymentError("Traffic weights must sum to 100.")
        client = await self._http()
        response = await client.post(
            f"{self._router_admin_url}/weights",
            json={"service": service_name, "environment": environment, "weights": dict(weights)},
        )
        if response.status_code >= 400:
            raise DeploymentError("Router rejected the weight change.")

    async def get_traffic_weights(self, service_name: str, environment: str) -> Mapping[str, int]:
        client = await self._http()
        response = await client.get(
            f"{self._router_admin_url}/stats",
            params={"service": service_name, "environment": environment},
        )
        return dict(response.json().get("weights", {})) if response.status_code == 200 else {}

    async def stop_release(self, handle: ReleaseHandle) -> None:
        name = self._container_name(handle)
        await self._run("rm", "-f", name)
        self._handles.pop(handle.release_id, None)

    async def list_releases(self, service_name: str, environment: str) -> Sequence[ReleaseHandle]:
        code, output = await self._run(
            "ps",
            "--filter",
            "label=forge.managed=true",
            "--filter",
            f"label=forge.service={service_name}",
            "--filter",
            f"label=forge.environment={environment}",
            "--format",
            "{{.Names}}",
        )
        if code != 0:
            return []
        names = {line.strip() for line in output.splitlines() if line.strip()}
        return [h for h in self._handles.values() if self._container_name(h) in names]


__all__ = ["DockerDeploymentAdapter"]
