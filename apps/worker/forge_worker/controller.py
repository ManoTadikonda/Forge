"""Deployment controller: canary rollout, health gating and verified rollback.

Invariants this module exists to uphold:

* Every state change is persisted with its reason before the next step begins, so a
  controller crash leaves an accurate record rather than a stuck row.
* No database transaction is held open across a health check, a sleep, or a process
  start. Long work happens outside transactions; each step commits on its own.
* A candidate is promoted only on an explicit HEALTHY verdict. UNKNOWN never promotes.
* ``ROLLED_BACK`` is written only after post-rollback HTTP verification confirms the
  previous release is actually serving. Otherwise the deployment is ``ROLLBACK_FAILED``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from forge_core.canary import CanaryAction, CanaryPlan, decide
from forge_core.config import Settings
from forge_core.db.models import (
    Artifact,
    Deployment,
    Environment,
    HealthSampleRow,
    Release,
    Service,
)
from forge_core.enums import DeploymentState, HealthVerdict, ReleaseRole
from forge_core.errors import DeploymentError, FencedError
from forge_core.health import HealthAssessment, HealthSample, HealthThresholds, analyze
from forge_core.logging import get_logger
from forge_core.ports import ReleaseSpec
from forge_core.state_machine import DEPLOYMENT_MACHINE
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge_worker import locking, traffic
from forge_worker.events import raise_incident, record_event
from forge_worker.registry.local import LocalContentAddressedRegistry

logger = get_logger("forge.controller")


class RolloutAborted(Exception):
    """Raised internally to unwind to the rollback path, carrying the reason."""

    def __init__(self, reason: str, assessment: HealthAssessment | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.assessment = assessment


@dataclass
class _ReleaseContext:
    """One release participating in this rollout."""

    release_id: str
    version: str
    digest: str
    handle: Any = None
    row_id: uuid.UUID | None = None
    #: Cumulative counters at the start of the current observation window.
    baseline_requests: int = 0
    baseline_errors: int = 0
    samples: list[HealthSample] = field(default_factory=list)


class DeploymentController:
    """Drives one deployment from PENDING to a terminal state."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        adapter: Any,
        settings: Settings,
        worker_id: str,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._adapter = adapter
        self._settings = settings
        self._worker_id = worker_id
        self._client = http_client
        self._sleep = sleep
        self._registry = LocalContentAddressedRegistry(Path(settings.artifact_root))

    # ------------------------------------------------------------------ helpers

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
        return self._client

    async def _transition(
        self,
        deployment_id: uuid.UUID,
        target: DeploymentState,
        *,
        message: str,
        kind: str = "state_change",
        payload: dict[str, Any] | None = None,
    ) -> DeploymentState:
        """Validate and persist a state transition together with its timeline event."""
        async with self._sessionmaker() as session, session.begin():
            deployment = await session.get(Deployment, deployment_id, with_for_update=True)
            if deployment is None:
                raise DeploymentError(f"Deployment {deployment_id} no longer exists.")
            source = DeploymentState(deployment.state)
            DEPLOYMENT_MACHINE.validate(source, target)
            deployment.state = target.value
            now = datetime.now(UTC)
            if target is DeploymentState.PREPARING and deployment.started_at is None:
                deployment.started_at = now
            if target in (
                DeploymentState.SUCCEEDED, DeploymentState.FAILED,
                DeploymentState.CANCELED, DeploymentState.ROLLED_BACK,
                DeploymentState.ROLLBACK_FAILED,
            ):
                deployment.finished_at = now
                if deployment.started_at is not None:
                    deployment.duration_ms = int(
                        (now - deployment.started_at).total_seconds() * 1000
                    )
            await record_event(
                session, deployment_id, kind=kind, message=message,
                from_state=source.value, to_state=target.value,
                actor=self._worker_id, payload=payload or {},
            )
            logger.info(
                "deployment_transition", deployment_id=str(deployment_id),
                from_state=source.value, to_state=target.value, message=message,
            )
            return target

    async def _event(
        self, deployment_id: uuid.UUID, kind: str, message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            await record_event(
                session, deployment_id, kind=kind, message=message,
                actor=self._worker_id, payload=payload or {},
            )

    async def _cancel_requested(self, deployment_id: uuid.UUID) -> bool:
        async with self._sessionmaker() as session:
            return bool(
                await session.scalar(
                    select(Deployment.cancel_requested).where(
                        Deployment.id == deployment_id
                    )
                )
            )

    # -------------------------------------------------------------- entry point

    async def run(self, deployment_id: uuid.UUID) -> DeploymentState:
        """Execute one deployment. Always leaves it in a terminal state."""
        async with self._sessionmaker() as session:
            deployment = await session.get(Deployment, deployment_id)
            if deployment is None:
                raise DeploymentError(f"Deployment {deployment_id} not found.")
            if DeploymentState(deployment.state) in (
                DeploymentState.SUCCEEDED, DeploymentState.FAILED,
                DeploymentState.CANCELED, DeploymentState.ROLLED_BACK,
                DeploymentState.ROLLBACK_FAILED,
            ):
                # Idempotency: at-least-once delivery may redeliver a finished job.
                return DeploymentState(deployment.state)
            service = await session.get(Service, deployment.service_id)
            environment = await session.get(Environment, deployment.environment_id)
            artifact = await session.get(Artifact, deployment.artifact_id)
            previous = (
                await session.get(Artifact, deployment.previous_artifact_id)
                if deployment.previous_artifact_id
                else None
            )
            if service is None or environment is None or artifact is None:
                raise DeploymentError("Deployment references missing service/artifact.")
            service_name, env_name = service.name, environment.name
            service_id, environment_id = service.id, environment.id
            canary_config = dict(deployment.canary_steps or {})

        resource = locking.resource_key(service_id, environment_id)
        async with self._sessionmaker() as session, session.begin():
            lock = await locking.acquire(
                session, resource, self._worker_id,
                ttl_seconds=self._settings.deployment_lock_ttl_seconds,
            )

        try:
            return await self._run_locked(
                deployment_id=deployment_id,
                lock=lock,
                service_name=service_name,
                service_id=service_id,
                env_name=env_name,
                artifact=artifact,
                previous=previous,
                canary_config=canary_config,
            )
        finally:
            with suppress(Exception):
                async with self._sessionmaker() as session, session.begin():
                    await locking.release(session, lock)

    # --------------------------------------------------------------- main flow

    async def _run_locked(
        self,
        *,
        deployment_id: uuid.UUID,
        lock: locking.DeploymentLock,
        service_name: str,
        service_id: uuid.UUID,
        env_name: str,
        artifact: Artifact,
        previous: Artifact | None,
        canary_config: dict[str, Any],
    ) -> DeploymentState:
        candidate = _ReleaseContext(
            release_id=f"{service_name}-{artifact.version}-{str(deployment_id)[:8]}",
            version=artifact.version,
            digest=artifact.digest,
        )
        stable: _ReleaseContext | None = None
        started_candidate = False

        try:
            # ---------------------------------------------------- PREPARING
            await self._transition(
                deployment_id, DeploymentState.PREPARING,
                message=f"Preparing release of {artifact.version} "
                        f"({artifact.digest[:19]}…) to {env_name}.",
                payload={"digest": artifact.digest, "version": artifact.version},
            )
            await locking.assert_held_in(self._sessionmaker, lock)

            artifact_path = await self._registry.resolve(artifact.digest)
            if artifact_path is None:
                raise DeploymentError(
                    f"Artifact {artifact.digest} is not present in the registry. "
                    f"The pipeline that produced it may not have completed publishing."
                )

            stable = await self._load_running_stable(service_id, service_name, env_name)

            # ---------------------------------------------------- DEPLOYING
            await self._transition(
                deployment_id, DeploymentState.DEPLOYING,
                message=f"Starting candidate release {candidate.release_id}.",
            )
            spec = ReleaseSpec(
                release_id=candidate.release_id,
                service_name=service_name,
                environment=env_name,
                version=candidate.version,
                image_digest=candidate.digest,
                artifact_path=artifact_path,
                env=self._release_env(canary_config),
            )
            candidate.handle = await self._adapter.start_release(spec)
            started_candidate = True
            candidate.row_id = await self._persist_release(
                deployment_id, service_id, artifact.id, candidate,
                role=ReleaseRole.CANDIDATE, weight=0,
            )
            await self._event(
                deployment_id, "candidate_started",
                f"Candidate {candidate.version} is running at "
                f"{candidate.handle.primary_endpoint}.",
                {"release_id": candidate.release_id,
                 "endpoint": candidate.handle.primary_endpoint,
                 "digest": candidate.digest},
            )

            # Register both releases on the router with all traffic still on stable.
            handles = [candidate.handle] + ([stable.handle] if stable else [])
            initial = (
                {stable.release_id: 100, candidate.release_id: 0}
                if stable
                else {candidate.release_id: 100}
            )
            await self._adapter.register_route(
                service_name, env_name, handles, initial
            )

            # ---------------------------------------------------- VERIFYING
            await self._transition(
                deployment_id, DeploymentState.VERIFYING,
                message="Waiting for the candidate to report ready.",
            )
            ready = await self._adapter.wait_ready(candidate.handle, timeout_seconds=60)
            if not ready:
                raise RolloutAborted(
                    "Candidate release never reported ready within 60s; it is not "
                    "safe to send it traffic."
                )
            probe = await self._adapter.probe(candidate.handle)
            if not probe.get("healthy") or not probe.get("ready"):
                raise RolloutAborted(
                    "Candidate failed its direct health check before receiving traffic."
                )
            await self._event(
                deployment_id, "candidate_verified",
                "Candidate passed its pre-traffic health and readiness checks.",
                {"release_id": candidate.release_id},
            )

            if stable is None:
                # First release of this service: there is no previous version to
                # shift traffic away from, so a canary would be meaningless.
                return await self._promote_initial(
                    deployment_id, service_id, artifact, candidate, service_name, env_name
                )

            # ------------------------------------------------------- CANARY
            await self._transition(
                deployment_id, DeploymentState.CANARY,
                message="Beginning progressive traffic shift to the candidate.",
            )
            await self._run_canary(
                deployment_id=deployment_id,
                lock=lock,
                service_name=service_name,
                env_name=env_name,
                candidate=candidate,
                stable=stable,
                canary_config=canary_config,
            )

            # ---------------------------------------------------- PROMOTING
            await self._transition(
                deployment_id, DeploymentState.PROMOTING,
                message="Health thresholds satisfied at every step; promoting.",
            )
            await self._adapter.set_traffic_weights(
                service_name, env_name,
                {candidate.release_id: 100, stable.release_id: 0},
            )
            confirmed = await self._verify_serving(
                service_name, env_name, candidate.version, expected_minimum=0.9
            )
            if not confirmed:
                raise RolloutAborted(
                    "Promotion could not be confirmed: the candidate is not serving "
                    "traffic after the final weight change."
                )

            await self._retire(stable, service_id)
            await self._mark_stable(deployment_id, service_id, artifact.id, candidate)

            await self._transition(
                deployment_id, DeploymentState.SUCCEEDED,
                message=f"Release {candidate.version} promoted and serving 100% "
                        f"of traffic.",
                kind="promoted",
                payload={"version": candidate.version, "digest": candidate.digest},
            )
            return DeploymentState.SUCCEEDED

        except RolloutAborted as abort:
            return await self._rollback(
                deployment_id=deployment_id,
                service_id=service_id,
                service_name=service_name,
                env_name=env_name,
                candidate=candidate if started_candidate else None,
                stable=stable,
                reason=abort.reason,
                assessment=abort.assessment,
            )
        except FencedError as fenced:
            # Another worker owns this deployment now. Stop without touching shared
            # infrastructure; the new owner is responsible for it.
            await self._event(
                deployment_id, "fenced",
                f"Stopped without acting: {fenced.message}",
            )
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.exception("deployment_failed", deployment_id=str(deployment_id))
            if started_candidate and stable is not None:
                return await self._rollback(
                    deployment_id=deployment_id, service_id=service_id,
                    service_name=service_name, env_name=env_name,
                    candidate=candidate, stable=stable,
                    reason=f"Deployment raised an unexpected error: {reason}",
                    assessment=None,
                )
            await self._fail(deployment_id, reason, candidate if started_candidate else None)
            return DeploymentState.FAILED

    # ------------------------------------------------------------------ canary

    def _plan(self, canary_config: dict[str, Any]) -> CanaryPlan:
        steps = canary_config.get("steps") or list(self._settings.canary_steps)
        seconds = int(canary_config.get("step_seconds") or self._settings.canary_step_seconds)
        return CanaryPlan(tuple(int(s) for s in steps), seconds)

    def _thresholds(self, canary_config: dict[str, Any]) -> HealthThresholds:
        return HealthThresholds(
            min_samples=int(
                canary_config.get("min_samples") or self._settings.health_min_samples
            ),
            max_error_rate=float(
                canary_config.get("max_error_rate")
                or self._settings.health_max_error_rate
            ),
            max_p95_latency_ms=float(
                canary_config.get("max_p95_latency_ms")
                or self._settings.health_max_p95_latency_ms
            ),
            staleness_seconds=self._settings.health_sample_staleness_seconds,
        )

    def _release_env(self, canary_config: dict[str, Any]) -> dict[str, str]:
        """Environment passed to the candidate process, including demo fault settings."""
        env = canary_config.get("release_env") or {}
        return {str(k): str(v) for k, v in env.items()}

    async def _run_canary(
        self,
        *,
        deployment_id: uuid.UUID,
        lock: locking.DeploymentLock,
        service_name: str,
        env_name: str,
        candidate: _ReleaseContext,
        stable: _ReleaseContext,
        canary_config: dict[str, Any],
    ) -> None:
        plan = self._plan(canary_config)
        thresholds = self._thresholds(canary_config)
        probe_requests = int(
            canary_config.get("probe_requests") or max(thresholds.min_samples * 2, 40)
        )
        url = (
            f"http://{self._settings.router_public_host}:"
            f"{canary_config.get('router_port', 8098)}/"
            f"{service_name}/{env_name}/api/work"
        )

        for index in range(plan.total_steps):
            weight = plan.weight_at(index)
            if await self._cancel_requested(deployment_id):
                raise RolloutAborted("Deployment was cancelled by an operator.")
            await locking.assert_held_in(self._sessionmaker, lock)

            await self._adapter.set_traffic_weights(
                service_name, env_name,
                {candidate.release_id: weight, stable.release_id: 100 - weight},
            )
            await self._update_step(deployment_id, index, weight)
            await self._event(
                deployment_id, "canary_step",
                f"Shifted {weight}% of traffic to the candidate.",
                {"step": index + 1, "of": plan.total_steps, "weight": weight},
            )

            # Baseline the counters so this window measures only its own traffic.
            await self._rebaseline(candidate)
            await self._rebaseline(stable)
            with suppress(Exception):
                await self._adapter.reset_router_counters(service_name, env_name)

            step_started = datetime.now(UTC)
            client = await self._http()
            sent = await traffic.generate(client, url, count=probe_requests)
            await self._sleep(min(2.0, plan.step_seconds / 4))

            candidate_assessment = await self._observe(
                deployment_id, candidate, ReleaseRole.CANDIDATE, thresholds
            )
            stable_assessment = await self._observe(
                deployment_id, stable, ReleaseRole.STABLE, thresholds
            )

            elapsed = (datetime.now(UTC) - step_started).total_seconds()
            decision = decide(
                plan, step_index=index,
                seconds_in_step=max(elapsed, plan.step_seconds),
                assessment=candidate_assessment,
            )

            await self._event(
                deployment_id, "health_evaluated",
                f"{candidate_assessment.verdict}: {candidate_assessment.reason}",
                {
                    "weight": weight,
                    "candidate": candidate_assessment.as_dict(),
                    "stable": stable_assessment.as_dict(),
                    "observed_split": sent.by_version,
                    "decision": str(decision.action),
                },
            )

            if decision.action is CanaryAction.ABORT:
                raise RolloutAborted(decision.reason, candidate_assessment)

            await self._store_health_summary(
                deployment_id, candidate_assessment, stable_assessment, weight
            )

    async def _rebaseline(self, release: _ReleaseContext) -> None:
        probe = await self._adapter.probe(release.handle)
        release.baseline_requests = int(probe.get("requests", 0) or 0)
        release.baseline_errors = int(probe.get("errors", 0) or 0)

    async def _observe(
        self,
        deployment_id: uuid.UUID,
        release: _ReleaseContext,
        role: ReleaseRole,
        thresholds: HealthThresholds,
    ) -> HealthAssessment:
        """Take one measurement of a release and persist it."""
        probe = await self._adapter.probe(release.handle)
        observed_at = probe.get("observed_at") or datetime.now(UTC)
        # Counters are cumulative; the window's traffic is the delta.
        requests = max(0, int(probe.get("requests", 0) or 0) - release.baseline_requests)
        errors = max(0, int(probe.get("errors", 0) or 0) - release.baseline_errors)

        sample = HealthSample(
            observed_at=observed_at if isinstance(observed_at, datetime) else datetime.now(UTC),
            requests=requests,
            errors=errors,
            latency_p50_ms=_as_float(probe.get("latency_p50_ms")),
            latency_p95_ms=_as_float(probe.get("latency_p95_ms")),
            latency_p99_ms=_as_float(probe.get("latency_p99_ms")),
            healthy_endpoint=bool(probe.get("healthy", False)),
            ready_endpoint=bool(probe.get("ready", False)),
        )
        release.samples = [sample]

        async with self._sessionmaker() as session, session.begin():
            deployment = await session.get(Deployment, deployment_id)
            session.add(
                HealthSampleRow(
                    id=uuid.uuid4(),
                    deployment_id=deployment_id,
                    service_id=deployment.service_id if deployment else None,
                    release_id=release.row_id,
                    release_role=role.value,
                    version=release.version,
                    observed_at=sample.observed_at,
                    requests=sample.requests,
                    errors=sample.errors,
                    latency_p50_ms=sample.latency_p50_ms,
                    latency_p95_ms=sample.latency_p95_ms,
                    latency_p99_ms=sample.latency_p99_ms,
                    healthy_endpoint=sample.healthy_endpoint,
                    ready_endpoint=sample.ready_endpoint,
                )
            )
        return analyze([sample], thresholds, now=datetime.now(UTC))

    # ---------------------------------------------------------------- rollback

    async def _rollback(
        self,
        *,
        deployment_id: uuid.UUID,
        service_id: uuid.UUID,
        service_name: str,
        env_name: str,
        candidate: _ReleaseContext | None,
        stable: _ReleaseContext | None,
        reason: str,
        assessment: HealthAssessment | None,
    ) -> DeploymentState:
        """Restore the previous release and verify it is actually serving."""
        await self._transition(
            deployment_id, DeploymentState.ROLLING_BACK,
            message=f"Rolling back: {reason}",
            kind="rollback_started",
            payload={"reason": reason,
                     "evidence": assessment.as_dict() if assessment else {}},
        )
        async with self._sessionmaker() as session, session.begin():
            deployment = await session.get(Deployment, deployment_id)
            if deployment is not None:
                deployment.rollback_reason = reason[:4000]

        if stable is None:
            # Nothing to roll back to: the service has no previously healthy release.
            await self._event(
                deployment_id, "rollback_impossible",
                "No previous healthy release exists for this service, so traffic "
                "cannot be restored to one.",
            )
            if candidate is not None:
                with suppress(Exception):
                    await self._adapter.stop_release(candidate.handle)
            return await self._finish_rollback(
                deployment_id, service_id, verified=False, reason=reason,
                assessment=assessment,
                detail="There was no previous healthy release to restore.",
            )

        verified = False
        detail = ""
        try:
            # 1. Shift all traffic back to the known-good release.
            await self._adapter.set_traffic_weights(
                service_name, env_name,
                {stable.release_id: 100, **({candidate.release_id: 0} if candidate else {})},
            )
            await self._event(
                deployment_id, "traffic_restored",
                f"All traffic routed back to the previous release "
                f"{stable.version}.",
                {"stable_release": stable.release_id, "weight": 100},
            )

            # 2. Stop the candidate so it cannot receive or consume anything further.
            if candidate is not None:
                with suppress(Exception):
                    await self._adapter.stop_release(candidate.handle)
                await self._mark_release_stopped(candidate)
                await self._event(
                    deployment_id, "candidate_stopped",
                    f"Candidate release {candidate.release_id} was stopped.",
                )

            # 3. Verify by issuing real traffic and checking the stable release's health.
            serving = await self._verify_serving(
                service_name, env_name, stable.version, expected_minimum=0.95
            )
            probe = await self._adapter.probe(stable.handle)
            healthy = bool(probe.get("healthy")) and bool(probe.get("ready"))
            verified = serving and healthy
            detail = (
                f"Post-rollback verification: traffic served by {stable.version} "
                f"= {serving}, stable health probe = {healthy}."
            )
            await self._event(
                deployment_id, "rollback_verification", detail,
                {"serving": serving, "healthy": healthy},
            )
        except Exception as exc:
            detail = f"Rollback verification raised {type(exc).__name__}: {exc}"
            logger.exception("rollback_verification_failed",
                             deployment_id=str(deployment_id))

        if verified:
            await self._restore_stable_record(service_id, stable)

        return await self._finish_rollback(
            deployment_id, service_id, verified=verified, reason=reason,
            assessment=assessment, detail=detail,
        )

    async def _finish_rollback(
        self,
        deployment_id: uuid.UUID,
        service_id: uuid.UUID,
        *,
        verified: bool,
        reason: str,
        assessment: HealthAssessment | None,
        detail: str,
    ) -> DeploymentState:
        target = (
            DeploymentState.ROLLED_BACK if verified else DeploymentState.ROLLBACK_FAILED
        )
        async with self._sessionmaker() as session, session.begin():
            deployment = await session.get(Deployment, deployment_id)
            if deployment is not None:
                deployment.rollback_verified = verified
            await raise_incident(
                session,
                deployment_id=deployment_id,
                service_id=service_id,
                title=(
                    "Automated rollback completed"
                    if verified
                    else "Automated rollback could not be verified"
                ),
                detail=f"{reason}\n\n{detail}",
                kind="ROLLBACK" if verified else "ROLLBACK_FAILED",
                severity="HIGH" if verified else "CRITICAL",
                verdict=str(assessment.verdict) if assessment else HealthVerdict.UNHEALTHY,
                evidence=assessment.as_dict() if assessment else {"reason": reason},
            )
        await self._transition(
            deployment_id, target,
            message=(
                "Traffic restored to the previous healthy release and verified."
                if verified
                else "Rollback could not be confirmed; manual intervention required."
            ),
            kind="rollback_finished",
            payload={"verified": verified, "reason": reason},
        )
        return target

    async def _verify_serving(
        self, service_name: str, env_name: str, expected_version: str,
        *, expected_minimum: float, requests: int = 20,
    ) -> bool:
        """Issue real requests through the router and confirm who answers them."""
        client = await self._http()
        url = (
            f"http://{self._settings.router_public_host}:8098/"
            f"{service_name}/{env_name}/api/work"
        )
        result = await traffic.generate(client, url, count=requests)
        served = result.by_version.get(expected_version, 0)
        ratio = served / requests if requests else 0.0
        logger.info(
            "serving_verification", expected_version=expected_version,
            observed=result.by_version, ratio=round(ratio, 3),
        )
        return ratio >= expected_minimum

    # ------------------------------------------------------------- persistence

    async def _load_running_stable(
        self, service_id: uuid.UUID, service_name: str, env_name: str
    ) -> _ReleaseContext | None:
        """Find the release currently serving stable traffic, if the adapter has it."""
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(Release)
                    .where(
                        Release.service_id == service_id,
                        Release.role == ReleaseRole.STABLE.value,
                        Release.is_running.is_(True),
                    )
                    .order_by(Release.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        handles = {
            h.release_id: h
            for h in await self._adapter.list_releases(service_name, env_name)
        }
        handle = handles.get(str(row.endpoints.get("release_id", "")))
        if handle is None:
            # The adapter has no live process for it (e.g. after a restart); it is
            # not a usable rollback target, and saying so is better than pretending.
            logger.warning(
                "stable_release_not_running", service=service_name,
                release=row.endpoints.get("release_id"),
            )
            return None
        return _ReleaseContext(
            release_id=handle.release_id, version=handle.version,
            digest=handle.image_digest, handle=handle, row_id=row.id,
        )

    async def _persist_release(
        self, deployment_id: uuid.UUID, service_id: uuid.UUID,
        artifact_id: uuid.UUID, release: _ReleaseContext,
        *, role: ReleaseRole, weight: int,
    ) -> uuid.UUID:
        async with self._sessionmaker() as session, session.begin():
            row = Release(
                id=uuid.uuid4(),
                service_id=service_id,
                deployment_id=deployment_id,
                artifact_id=artifact_id,
                version=release.version,
                role=role.value,
                weight=weight,
                endpoints={
                    "release_id": release.release_id,
                    "urls": list(release.handle.endpoints) if release.handle else [],
                },
                instances={"ids": list(release.handle.instances) if release.handle else []},
                is_running=True,
            )
            session.add(row)
            await session.flush()
            return row.id

    async def _update_step(
        self, deployment_id: uuid.UUID, index: int, weight: int
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            deployment = await session.get(Deployment, deployment_id)
            if deployment is not None:
                deployment.current_step_index = index
                deployment.current_weight = weight
                deployment.step_started_at = datetime.now(UTC)

    async def _store_health_summary(
        self, deployment_id: uuid.UUID, candidate: HealthAssessment,
        stable: HealthAssessment, weight: int,
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            deployment = await session.get(Deployment, deployment_id)
            if deployment is not None:
                deployment.health_summary = {
                    "weight": weight,
                    "candidate": candidate.as_dict(),
                    "stable": stable.as_dict(),
                    "evaluated_at": datetime.now(UTC).isoformat(),
                }

    async def _mark_release_stopped(self, release: _ReleaseContext) -> None:
        if release.row_id is None:
            return
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(Release, release.row_id)
            if row is not None:
                row.is_running = False
                row.weight = 0
                row.role = ReleaseRole.RETIRED.value
                row.stopped_at = datetime.now(UTC)

    async def _retire(self, stable: _ReleaseContext, service_id: uuid.UUID) -> None:
        with suppress(Exception):
            await self._adapter.stop_release(stable.handle)
        await self._mark_release_stopped(stable)

    async def _restore_stable_record(
        self, service_id: uuid.UUID, stable: _ReleaseContext
    ) -> None:
        if stable.row_id is None:
            return
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(Release, stable.row_id)
            if row is not None:
                row.role = ReleaseRole.STABLE.value
                row.weight = 100
                row.is_running = True

    async def _mark_stable(
        self, deployment_id: uuid.UUID, service_id: uuid.UUID,
        artifact_id: uuid.UUID, candidate: _ReleaseContext,
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            if candidate.row_id is not None:
                row = await session.get(Release, candidate.row_id)
                if row is not None:
                    row.role = ReleaseRole.STABLE.value
                    row.weight = 100
            service = await session.get(Service, service_id)
            if service is not None:
                service.current_artifact_id = artifact_id

    async def _promote_initial(
        self, deployment_id: uuid.UUID, service_id: uuid.UUID, artifact: Artifact,
        candidate: _ReleaseContext, service_name: str, env_name: str,
    ) -> DeploymentState:
        """First release of a service: no canary, because there is no baseline."""
        await self._event(
            deployment_id, "initial_release",
            "No previous release exists for this service, so traffic moves directly "
            "to the first version rather than through a canary.",
        )
        await self._transition(
            deployment_id, DeploymentState.CANARY,
            message="Initial release: routing 100% of traffic to the first version.",
        )
        await self._adapter.set_traffic_weights(
            service_name, env_name, {candidate.release_id: 100}
        )
        await self._update_step(deployment_id, 0, 100)
        await self._transition(
            deployment_id, DeploymentState.PROMOTING,
            message="Confirming the initial release is serving.",
        )
        if not await self._verify_serving(
            service_name, env_name, candidate.version, expected_minimum=0.9
        ):
            raise RolloutAborted("Initial release is not serving traffic.")
        await self._mark_stable(deployment_id, service_id, artifact.id, candidate)
        await self._transition(
            deployment_id, DeploymentState.SUCCEEDED,
            message=f"Initial release {candidate.version} is live.",
            kind="promoted",
        )
        return DeploymentState.SUCCEEDED

    async def _fail(
        self, deployment_id: uuid.UUID, reason: str, candidate: _ReleaseContext | None
    ) -> None:
        if candidate is not None and candidate.handle is not None:
            with suppress(Exception):
                await self._adapter.stop_release(candidate.handle)
            await self._mark_release_stopped(candidate)
        async with self._sessionmaker() as session, session.begin():
            deployment = await session.get(Deployment, deployment_id)
            if deployment is not None:
                deployment.failure_reason = reason[:4000]
        await self._transition(
            deployment_id, DeploymentState.FAILED,
            message=reason, kind="deployment_failed",
        )


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["DeploymentController", "RolloutAborted"]
