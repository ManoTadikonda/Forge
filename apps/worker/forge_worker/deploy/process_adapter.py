"""Local process deployment adapter.

Releases are real OS processes bound to real, dynamically allocated TCP ports.
Traffic is directed by the Forge router over its admin API. Health is measured by
issuing real HTTP requests to the release.

This is not a simulation of deployment; it is deployment without containers. The
orchestration above it — canary stepping, health analysis, rollback — is identical
whichever adapter is in use, which is the point of the port.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tarfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from forge_core.errors import DeploymentError
from forge_core.logging import get_logger
from forge_core.ports import ReleaseHandle, ReleaseSpec

logger = get_logger("forge.deploy.process")


def _free_port() -> int:
    """Reserve an ephemeral port by binding and immediately releasing it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ProcessDeploymentAdapter:
    """Runs releases as supervised local processes behind the Forge router."""

    name = "process"

    def __init__(
        self,
        *,
        router_admin_url: str,
        run_root: Path,
        host: str = "127.0.0.1",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._router_admin_url = router_admin_url.rstrip("/")
        self._run_root = Path(run_root)
        self._host = host
        self._client = http_client
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._handles: dict[str, ReleaseHandle] = {}
        self._specs: dict[str, ReleaseSpec] = {}

    # ----------------------------------------------------------------- plumbing

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def available(self) -> tuple[bool, str]:
        try:
            client = await self._http()
            response = await client.get(f"{self._router_admin_url}/health", timeout=3.0)
            if response.status_code != 200:
                return False, (
                    f"Router admin API at {self._router_admin_url} returned "
                    f"HTTP {response.status_code}."
                )
        except httpx.RequestError as exc:
            return False, (
                f"Router admin API at {self._router_admin_url} is unreachable "
                f"({type(exc).__name__}). Start it with 'make router' or "
                f"'scripts/run_stack.sh'."
            )
        if shutil.which(sys.executable) is None:  # pragma: no cover - defensive
            return False, "No Python interpreter is available to run releases."
        return True, f"Process adapter routing via {self._router_admin_url}"

    # ------------------------------------------------------------------ release

    def _release_dir(self, release_id: str) -> Path:
        return self._run_root / release_id

    def _unpack(self, spec: ReleaseSpec) -> Path:
        """Materialise the artifact for a release, addressed by digest."""
        target = self._release_dir(spec.release_id)
        if target.exists() and any(target.iterdir()):
            return target  # idempotent: already materialised
        target.mkdir(parents=True, exist_ok=True)
        if spec.artifact_path is None or not Path(spec.artifact_path).is_file():
            raise DeploymentError(
                f"Artifact for digest {spec.image_digest} is not present in the "
                f"registry; cannot start release {spec.release_id}."
            )
        with tarfile.open(spec.artifact_path, mode="r") as archive:
            # filter="data" rejects absolute paths, traversal and special files.
            archive.extractall(target, filter="data")
        return target

    async def start_release(self, spec: ReleaseSpec) -> ReleaseHandle:
        existing = self._handles.get(spec.release_id)
        if existing is not None:
            process = self._processes.get(spec.release_id)
            if process is not None and process.poll() is None:
                return existing  # idempotent restart

        workdir = await asyncio.to_thread(self._unpack, spec)
        port = _free_port()

        env = dict(os.environ)
        env.update(
            {
                "PORT": str(port),
                "SAMPLE_VERSION": spec.version,
                "SAMPLE_RELEASE_ID": spec.release_id,
                "SAMPLE_IMAGE_DIGEST": spec.image_digest,
                "PYTHONPATH": str(workdir),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        env.update({k: str(v) for k, v in spec.env.items()})

        log_path = workdir / "release.log"
        log_handle = log_path.open("ab")
        argv = [
            sys.executable,
            "-m",
            "uvicorn",
            "sample_service.main:app",
            "--host",
            self._host,
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
        # Popen (not asyncio.create_subprocess_exec) because the release must
        # outlive this coroutine and be supervised by PID across controller
        # restarts. Popen forks and returns immediately; it does not block.
        process = subprocess.Popen(  # noqa: S603, ASYNC220
            argv,
            cwd=str(workdir),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group, so cleanup is complete
        )
        self._processes[spec.release_id] = process

        handle = ReleaseHandle(
            release_id=spec.release_id,
            version=spec.version,
            image_digest=spec.image_digest,
            endpoints=(f"http://{self._host}:{port}",),
            instances=(str(process.pid),),
            started_at=datetime.now(UTC),
        )
        self._handles[spec.release_id] = handle
        self._specs[spec.release_id] = spec
        logger.info(
            "release_started",
            release_id=spec.release_id,
            version=spec.version,
            pid=process.pid,
            port=port,
            digest=spec.image_digest,
        )
        return handle

    async def wait_ready(self, handle: ReleaseHandle, *, timeout_seconds: int) -> bool:
        """Poll the readiness endpoint until it succeeds or the deadline passes."""
        spec = self._specs.get(handle.release_id)
        ready_path = spec.ready_path if spec else "/ready"
        client = await self._http()
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        url = f"{handle.primary_endpoint}{ready_path}"

        while asyncio.get_running_loop().time() < deadline:
            process = self._processes.get(handle.release_id)
            if process is not None and process.poll() is not None:
                logger.warning(
                    "release_exited_during_startup",
                    release_id=handle.release_id,
                    exit_code=process.returncode,
                )
                return False
            try:
                response = await client.get(url, timeout=3.0)
                if response.status_code == 200:
                    return True
            except httpx.RequestError:
                pass
            await asyncio.sleep(0.25)
        return False

    async def probe(self, handle: ReleaseHandle) -> Mapping[str, object]:
        """Scrape one observation: liveness, readiness and request counters."""
        spec = self._specs.get(handle.release_id)
        client = await self._http()
        base = handle.primary_endpoint
        observed_at = datetime.now(UTC)

        async def _get(path: str) -> httpx.Response | None:
            try:
                return await client.get(f"{base}{path}", timeout=3.0)
            except httpx.RequestError:
                return None

        health_response, ready_response, stats_response = await asyncio.gather(
            _get(spec.health_path if spec else "/health"),
            _get(spec.ready_path if spec else "/ready"),
            _get("/api/stats"),
        )

        stats: dict[str, Any] = {}
        if stats_response is not None and stats_response.status_code == 200:
            with contextlib.suppress(ValueError):
                stats = stats_response.json()

        return {
            "observed_at": observed_at,
            "healthy": health_response is not None and health_response.status_code == 200,
            "ready": ready_response is not None and ready_response.status_code == 200,
            "requests": int(stats.get("requests", 0) or 0),
            "errors": int(stats.get("errors", 0) or 0),
            "latency_p50_ms": stats.get("latency_p50_ms"),
            "latency_p95_ms": stats.get("latency_p95_ms"),
            "latency_p99_ms": stats.get("latency_p99_ms"),
            "version": stats.get("version", handle.version),
        }

    # ------------------------------------------------------------------ routing

    async def register_route(
        self,
        service_name: str,
        environment: str,
        handles: Sequence[ReleaseHandle],
        weights: Mapping[str, int],
    ) -> None:
        """Publish the set of routable releases and their initial weights."""
        client = await self._http()
        payload = {
            "service": service_name,
            "environment": environment,
            "targets": [
                {"release_id": h.release_id, "version": h.version, "url": h.primary_endpoint}
                for h in handles
            ],
            "weights": dict(weights),
        }
        response = await client.put(f"{self._router_admin_url}/routes", json=payload)
        if response.status_code >= 400:
            raise DeploymentError(
                f"Router rejected the route update (HTTP {response.status_code})."
            )

    async def set_traffic_weights(
        self, service_name: str, environment: str, weights: Mapping[str, int]
    ) -> None:
        total = sum(weights.values())
        if total != 100:
            raise DeploymentError(f"Traffic weights must sum to 100; received {total}.")
        client = await self._http()
        response = await client.post(
            f"{self._router_admin_url}/weights",
            json={"service": service_name, "environment": environment, "weights": dict(weights)},
        )
        if response.status_code >= 400:
            raise DeploymentError(
                f"Router rejected the weight change (HTTP {response.status_code}): "
                f"{response.text[:200]}"
            )
        logger.info(
            "traffic_weights_applied",
            service=service_name,
            environment=environment,
            weights=dict(weights),
        )

    async def get_traffic_weights(self, service_name: str, environment: str) -> Mapping[str, int]:
        client = await self._http()
        response = await client.get(
            f"{self._router_admin_url}/stats",
            params={"service": service_name, "environment": environment},
        )
        if response.status_code != 200:
            return {}
        return dict(response.json().get("weights", {}))

    async def router_stats(self, service_name: str, environment: str) -> dict[str, dict[str, int]]:
        """Router-side request counters — the second, independent traffic measurement."""
        client = await self._http()
        response = await client.get(
            f"{self._router_admin_url}/stats",
            params={"service": service_name, "environment": environment},
        )
        if response.status_code != 200:
            return {}
        return dict(response.json().get("stats", {}))

    async def reset_router_counters(self, service_name: str, environment: str) -> None:
        client = await self._http()
        with contextlib.suppress(httpx.RequestError):
            await client.post(
                f"{self._router_admin_url}/stats/reset",
                json={"service": service_name, "environment": environment},
            )

    # ----------------------------------------------------------------- teardown

    async def stop_release(self, handle: ReleaseHandle) -> None:
        """Stop a release. Succeeds when the release is already absent."""
        process = self._processes.pop(handle.release_id, None)
        self._handles.pop(handle.release_id, None)
        self._specs.pop(handle.release_id, None)
        if process is None or process.poll() is not None:
            return

        def _terminate() -> None:
            import signal

            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)

        await asyncio.to_thread(_terminate)
        logger.info("release_stopped", release_id=handle.release_id)

    async def list_releases(self, service_name: str, environment: str) -> Sequence[ReleaseHandle]:
        return [
            handle
            for release_id, handle in self._handles.items()
            if (process := self._processes.get(release_id)) is not None and process.poll() is None
        ]

    def is_running(self, release_id: str) -> bool:
        process = self._processes.get(release_id)
        return process is not None and process.poll() is None


__all__ = ["ProcessDeploymentAdapter"]
