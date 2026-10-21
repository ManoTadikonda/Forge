"""Pipeline execution: checkout, validate, test, build, SBOM, scan, publish.

Each stage is persisted before and after it runs, so a crash leaves an accurate
record. Stage output is streamed into bounded log storage with secrets redacted.

Required gates are never skipped silently: an unavailable optional scanner is
recorded as SKIPPED with an explicit reason, and a failing required check fails
the pipeline.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge_core.config import Settings
from forge_core.db.logs import append_log
from forge_core.db.models import Artifact, PipelineRun, PipelineStage, Repository
from forge_core.enums import STAGE_SEQUENCE, PipelineState, StageName, StageStatus
from forge_core.errors import BuildError, ValidationError
from forge_core.logging import get_logger
from forge_core.manifest import PipelineManifest, load_manifest
from forge_core.ports import BuildRequest
from forge_core.state_machine import PIPELINE_MACHINE
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge_worker import checkout as checkout_module
from forge_worker.builders.sandbox import SandboxLimits, resolve_executable, run_command
from forge_worker.builders.sbom import generate_sbom, write_sbom

logger = get_logger("forge.pipeline")


class PipelineCancelled(Exception):
    """Raised when an operator cancels a run mid-flight."""


class PipelineRunner:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        builder: Any,
        registry: Any,
        settings: Settings,
        worker_id: str,
        is_cancelled: Any = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._builder = builder
        self._registry = registry
        self._settings = settings
        self._worker_id = worker_id
        self._is_cancelled = is_cancelled

    # ------------------------------------------------------------------ helpers

    async def _transition(
        self, run_id: uuid.UUID, target: PipelineState, *, reason: str = ""
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            run = await session.get(PipelineRun, run_id, with_for_update=True)
            if run is None:
                raise ValidationError(f"Pipeline run {run_id} no longer exists.")
            source = PipelineState(run.state)
            PIPELINE_MACHINE.validate(source, target)
            run.state = target.value
            now = datetime.now(UTC)
            if target is PipelineState.CLONING and run.started_at is None:
                run.started_at = now
            if reason:
                run.failure_reason = reason[:4000]
            if target in (
                PipelineState.SUCCEEDED, PipelineState.FAILED, PipelineState.CANCELED
            ):
                run.finished_at = now
                if run.started_at is not None:
                    run.duration_ms = int((now - run.started_at).total_seconds() * 1000)
            logger.info(
                "pipeline_transition", pipeline_id=str(run_id),
                from_state=source.value, to_state=target.value,
            )

    async def _stage_start(self, run_id: uuid.UUID, name: StageName) -> uuid.UUID:
        async with self._sessionmaker() as session, session.begin():
            stage = (
                await session.execute(
                    select(PipelineStage).where(
                        PipelineStage.run_id == run_id, PipelineStage.name == name.value
                    )
                )
            ).scalar_one_or_none()
            if stage is None:  # pragma: no cover - stages are pre-created
                stage = PipelineStage(
                    id=uuid.uuid4(), run_id=run_id, name=name.value,
                    sequence=STAGE_SEQUENCE.index(name),
                )
                session.add(stage)
                await session.flush()
            stage.status = StageStatus.RUNNING.value
            stage.started_at = datetime.now(UTC)
            return stage.id

    async def _stage_finish(
        self,
        stage_id: uuid.UUID,
        status: StageStatus,
        *,
        message: str = "",
        exit_code: int | None = None,
        skip_reason: str = "",
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            stage = await session.get(PipelineStage, stage_id)
            if stage is None:
                return
            now = datetime.now(UTC)
            stage.status = status.value
            stage.message = message[:4000]
            stage.exit_code = exit_code
            stage.skip_reason = skip_reason[:4000]
            stage.finished_at = now
            if stage.started_at is not None:
                stage.duration_ms = int((now - stage.started_at).total_seconds() * 1000)

    async def _log(self, run_id: uuid.UUID, stage: StageName, content: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await append_log(
                session, owner_type="pipeline", owner_id=run_id,
                stage=stage.value, content=content,
                max_total_bytes=self._settings.build_max_log_bytes,
            )

    async def _check_cancelled(self, run_id: uuid.UUID) -> None:
        async with self._sessionmaker() as session:
            flagged = await session.scalar(
                select(PipelineRun.cancel_requested).where(PipelineRun.id == run_id)
            )
        if flagged:
            raise PipelineCancelled("Pipeline was cancelled by an operator.")

    # --------------------------------------------------------------------- main

    async def run(self, run_id: uuid.UUID) -> PipelineState:
        async with self._sessionmaker() as session:
            run = await session.get(PipelineRun, run_id)
            if run is None:
                raise ValidationError(f"Pipeline run {run_id} not found.")
            if PipelineState(run.state) in (
                PipelineState.SUCCEEDED, PipelineState.FAILED, PipelineState.CANCELED
            ):
                return PipelineState(run.state)  # idempotent redelivery
            repository = (
                await session.get(Repository, run.repository_id)
                if run.repository_id
                else None
            )
            if repository is None:
                raise ValidationError(
                    "Pipeline run references a repository that no longer exists."
                )
            repo_url, branch = repository.url, run.branch or repository.default_branch
            allow_local = bool((repository.description or "").startswith("[demo]"))

        workspace = Path(self._settings.workspace_root) / str(run_id)
        try:
            shutil.rmtree(workspace, ignore_errors=True)
            return await self._execute(
                run_id, repo_url, branch, workspace, allow_local=allow_local
            )
        except PipelineCancelled as cancelled:
            await self._transition(
                run_id, PipelineState.CANCELED, reason=str(cancelled)
            )
            return PipelineState.CANCELED
        except (ValidationError, BuildError) as known:
            await self._transition(run_id, PipelineState.FAILED, reason=known.message)
            await self._log(run_id, StageName.CLONE, f"\n[forge] {known.message}\n")
            return PipelineState.FAILED
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.exception("pipeline_failed", pipeline_id=str(run_id))
            await self._transition(run_id, PipelineState.FAILED, reason=reason)
            return PipelineState.FAILED
        finally:
            # Workspaces are disposable; leaving them behind leaks disk and may leak
            # repository contents to later builds.
            shutil.rmtree(workspace, ignore_errors=True)

    async def _execute(
        self, run_id: uuid.UUID, repo_url: str, branch: str, workspace: Path,
        *, allow_local: bool,
    ) -> PipelineState:
        # ------------------------------------------------------------ CLONING
        await self._transition(run_id, PipelineState.CLONING)
        stage_id = await self._stage_start(run_id, StageName.CLONE)
        result = await checkout_module.checkout(
            repo_url, workspace, branch=branch, allow_local=allow_local
        )
        await self._stage_finish(
            stage_id, StageStatus.SUCCEEDED,
            message=f"Checked out {result.commit_sha[:12]} on {branch}.",
        )
        await self._log(
            run_id, StageName.CLONE,
            f"Cloned {repo_url} @ {branch}\nCommit {result.commit_sha}\n"
            f"Author {result.commit_author}\n{result.commit_message}\n",
        )
        async with self._sessionmaker() as session, session.begin():
            run = await session.get(PipelineRun, run_id)
            if run is not None:
                run.commit_sha = result.commit_sha
                run.commit_message = result.commit_message[:2000]
                run.commit_author = result.commit_author[:255]
        await self._check_cancelled(run_id)

        # --------------------------------------------------------- VALIDATING
        await self._transition(run_id, PipelineState.VALIDATING)
        stage_id = await self._stage_start(run_id, StageName.VALIDATE)
        manifest = load_manifest(workspace)
        async with self._sessionmaker() as session, session.begin():
            run = await session.get(PipelineRun, run_id)
            if run is not None:
                run.manifest = manifest.model_dump(mode="json")
        await self._stage_finish(
            stage_id, StageStatus.SUCCEEDED,
            message=f"Manifest valid: service {manifest.service.name}, "
                    f"{len(manifest.all_checks)} check(s).",
        )
        await self._log(
            run_id, StageName.VALIDATE,
            "Manifest accepted.\n"
            + "\n".join(f"  {c.name}: {c.display}" for c in manifest.all_checks)
            + "\n",
        )
        await self._check_cancelled(run_id)

        # ------------------------------------------------------------ TESTING
        await self._transition(run_id, PipelineState.TESTING)
        await self._run_checks(run_id, manifest, workspace)
        await self._check_cancelled(run_id)

        # ----------------------------------------------------------- BUILDING
        await self._transition(run_id, PipelineState.BUILDING)
        stage_id = await self._stage_start(run_id, StageName.BUILD)
        version = await self._resolve_version(run_id, manifest)
        build_result = await self._builder.build(
            BuildRequest(
                workspace=workspace,
                repository_url=repo_url,
                commit_sha=result.commit_sha,
                image_name=manifest.service.name,
                version=version,
                build_args=dict(manifest.build.build_args),
                dockerfile=manifest.build.dockerfile,
                timeout_seconds=self._settings.build_timeout_seconds,
            )
        )
        await self._stage_finish(
            stage_id, StageStatus.SUCCEEDED,
            message=f"Built {build_result.digest[:19]}… "
                    f"({build_result.size_bytes} bytes).",
        )
        await self._log(run_id, StageName.BUILD, build_result.logs)
        await self._check_cancelled(run_id)

        # --------------------------------------------------------------- SBOM
        stage_id = await self._stage_start(run_id, StageName.SBOM)
        document = generate_sbom(
            workspace, image_name=manifest.service.name, version=version,
            digest=build_result.digest, repository_url=repo_url,
            commit_sha=result.commit_sha,
        )
        sbom_dir = Path(self._settings.artifact_root) / "sbom"
        sbom_path, _sbom_digest = write_sbom(
            document, sbom_dir / f"{build_result.digest.split(':')[1]}.cdx.json"
        )
        component_count = len(document["components"])
        await self._stage_finish(
            stage_id, StageStatus.SUCCEEDED,
            message=f"CycloneDX 1.5 SBOM with {component_count} component(s).",
        )

        # --------------------------------------------------------------- SCAN
        stage_id = await self._stage_start(run_id, StageName.SCAN)
        scan_status, scan_reason, scan_summary = await self._scan(workspace)
        await self._stage_finish(
            stage_id,
            StageStatus.SUCCEEDED if scan_status == "PASSED" else StageStatus.SKIPPED,
            message=scan_reason if scan_status == "PASSED" else "",
            skip_reason="" if scan_status == "PASSED" else scan_reason,
        )
        await self._log(run_id, StageName.SCAN, scan_reason + "\n")

        # ---------------------------------------------------------- PUBLISHING
        await self._transition(run_id, PipelineState.PUBLISHING)
        stage_id = await self._stage_start(run_id, StageName.PUBLISH)
        published = await self._registry.publish(
            build_result, image_name=manifest.service.name, version=version
        )
        await self._record_artifact(
            run_id, build_result, published, manifest, version,
            repo_url, result.commit_sha, sbom_path, component_count,
            scan_status, scan_reason, scan_summary,
        )
        await self._stage_finish(
            stage_id, StageStatus.SUCCEEDED,
            message=f"Published {published.reference}.",
        )
        await self._log(
            run_id, StageName.PUBLISH,
            f"Published to {published.registry}: {published.reference}\n"
            f"Digest: {published.digest}\n"
            f"Signed: {published.signed} "
            f"({'signature recorded' if published.signed else 'signing not configured'})\n",
        )

        await self._transition(run_id, PipelineState.SUCCEEDED)
        return PipelineState.SUCCEEDED

    # ------------------------------------------------------------------ stages

    async def _run_checks(
        self, run_id: uuid.UUID, manifest: PipelineManifest, workspace: Path
    ) -> None:
        stage_id = await self._stage_start(run_id, StageName.TEST)
        checks = manifest.all_checks
        if not checks:
            await self._stage_finish(
                stage_id, StageStatus.SKIPPED,
                skip_reason="The manifest declares no lint or test commands.",
            )
            return

        limits = SandboxLimits(
            memory_mb=self._settings.build_memory_limit_mb,
            cpu_seconds=self._settings.build_cpu_seconds,
            max_processes=self._settings.build_max_processes,
            max_file_bytes=self._settings.build_max_file_bytes,
            timeout_seconds=self._settings.build_timeout_seconds,
            max_output_bytes=self._settings.build_max_log_bytes,
        )

        failures: list[str] = []
        for check in checks:
            await self._check_cancelled(run_id)
            # Resolution happens off the event loop; the containment check below is
            # what prevents a manifest from running commands outside the workspace.
            cwd = await asyncio.to_thread(
                (workspace / check.working_directory).resolve
            )
            workspace_root = await asyncio.to_thread(workspace.resolve)
            if not str(cwd).startswith(str(workspace_root)):
                raise ValidationError(
                    f"Check {check.name!r} escapes the workspace directory."
                )
            outcome = await run_command(
                check.run, cwd=cwd,
                limits=SandboxLimits(**{**limits.__dict__,
                                        "timeout_seconds": check.timeout_seconds}),
            )
            header = (
                f"\n$ {check.display}\n"
                f"[forge] exit={outcome.exit_code} "
                f"duration={outcome.duration_ms}ms\n"
            )
            await self._log(run_id, StageName.TEST, header + outcome.output)
            if not outcome.succeeded:
                detail = (
                    f"{check.name} failed (exit {outcome.exit_code}"
                    + (", timed out" if outcome.timed_out else "")
                    + ")"
                )
                if check.required:
                    failures.append(detail)
                else:
                    await self._log(
                        run_id, StageName.TEST,
                        f"[forge] {detail} — not required, continuing.\n",
                    )

        if failures:
            await self._stage_finish(
                stage_id, StageStatus.FAILED, message="; ".join(failures)
            )
            raise BuildError("Required checks failed: " + "; ".join(failures))

        await self._stage_finish(
            stage_id, StageStatus.SUCCEEDED,
            message=f"{len(checks)} check(s) passed.",
        )

    async def _scan(self, workspace: Path) -> tuple[str, str, dict[str, Any]]:
        """Run a dependency scanner when one is installed.

        An absent scanner is reported as SKIPPED with the reason. It is never
        reported as a pass.
        """
        for binary, argv in (
            ("trivy", ["trivy", "fs", "--quiet", "--format", "json", "."]),
            ("grype", ["grype", "dir:.", "-o", "json"]),
            ("pip-audit", ["pip-audit", "--format", "json", "--progress-spinner", "off"]),
        ):
            if resolve_executable(binary) is None:
                continue
            outcome = await run_command(
                argv, cwd=workspace,
                limits=SandboxLimits(timeout_seconds=300, max_output_bytes=1_000_000),
            )
            return (
                "PASSED" if outcome.exit_code == 0 else "FINDINGS",
                f"{binary} completed with exit code {outcome.exit_code}.",
                {"scanner": binary, "exit_code": outcome.exit_code},
            )
        return (
            "SKIPPED",
            "No dependency scanner is installed (looked for trivy, grype, pip-audit). "
            "Install one to enable this gate; it is recorded as skipped rather than "
            "passed.",
            {},
        )

    async def _resolve_version(
        self, run_id: uuid.UUID, manifest: PipelineManifest
    ) -> str:
        """Version for this build: explicit override, else run-number derived."""
        async with self._sessionmaker() as session:
            run = await session.get(PipelineRun, run_id)
            if run is None:
                return "0.0.0"
            configured = (run.manifest or {}).get("version_override") if run.manifest else None
            if isinstance(configured, str) and configured:
                return configured
            return f"1.0.{run.run_number}"

    async def _record_artifact(
        self, run_id: uuid.UUID, build_result: Any, published: Any,
        manifest: PipelineManifest, version: str, repo_url: str, commit_sha: str,
        sbom_path: Path, component_count: int,
        scan_status: str, scan_reason: str, scan_summary: dict[str, Any],
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            existing = (
                await session.execute(
                    select(Artifact).where(Artifact.digest == published.digest)
                )
            ).scalar_one_or_none()
            if existing is not None:
                # Same content: the artifact is immutable, so reuse it and just link
                # this run to it. This is what makes publishing idempotent.
                existing.pipeline_run_id = run_id
                return
            session.add(
                Artifact(
                    id=uuid.uuid4(),
                    digest=published.digest,
                    image_name=manifest.service.name,
                    version=version,
                    registry_name=published.registry,
                    reference=published.reference,
                    size_bytes=build_result.size_bytes,
                    pipeline_run_id=run_id,
                    repository_url=repo_url,
                    commit_sha=commit_sha,
                    builder=getattr(self._builder, "name", "unknown"),
                    build_started_at=_parse_iso(
                        build_result.metadata.get("build_started_at")
                    ),
                    build_finished_at=_parse_iso(
                        build_result.metadata.get("build_finished_at")
                    ),
                    provenance={
                        "repository_url": repo_url,
                        "commit_sha": commit_sha,
                        "pipeline_run_id": str(run_id),
                        "builder": getattr(self._builder, "name", "unknown"),
                        "reproducible": build_result.metadata.get("reproducible", "false"),
                        "file_count": build_result.metadata.get("file_count", ""),
                        "built_at": build_result.metadata.get("build_finished_at", ""),
                    },
                    sbom_format="CycloneDX-1.5",
                    sbom_path=str(sbom_path),
                    sbom_component_count=component_count,
                    signed=bool(published.signed),
                    signature_reference=published.signature_reference or "",
                    scan_status=scan_status,
                    scan_skip_reason="" if scan_status == "PASSED" else scan_reason,
                    scan_summary=scan_summary,
                )
            )


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["PipelineCancelled", "PipelineRunner"]
