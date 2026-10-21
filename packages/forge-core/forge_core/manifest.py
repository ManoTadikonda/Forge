"""The ``forge.yaml`` pipeline manifest.

Security property: a manifest may **not** supply a free-form shell string. Commands are
declared as argument vectors drawn from an allowlist of executables, and are executed
without a shell. This removes the "user submits an arbitrary shell command" class of
issue at the schema level rather than by escaping.
"""

from __future__ import annotations

import json
import shlex
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from forge_core.errors import ValidationError

MANIFEST_FILENAMES: tuple[str, ...] = ("forge.toml", ".forge.toml", "forge.json")

#: Executables a repository manifest is permitted to invoke. Anything else is rejected
#: at validation time. Extend deliberately; every entry widens the build's reach.
ALLOWED_EXECUTABLES: frozenset[str] = frozenset(
    {
        "python", "python3", "python3.12", "pytest", "ruff", "mypy", "pip",
        "node", "npm", "npx", "yarn", "pnpm", "tsc", "eslint", "vitest", "jest",
        "make", "bash", "sh", "echo", "true", "false",
        "go", "cargo", "rustc", "java", "mvn", "gradle",
    }
)

_MAX_STEPS = 25
_MAX_ARGS = 64


class CommandStep(BaseModel):
    """A single command, expressed as an argv vector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=120)]
    #: argv[0] must be in ALLOWED_EXECUTABLES; no shell is involved.
    run: Annotated[list[str], Field(min_length=1, max_length=_MAX_ARGS)]
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] = 600
    #: When False a failure is recorded but does not fail the pipeline.
    required: bool = True
    working_directory: Annotated[str, Field(max_length=256)] = "."

    @field_validator("run")
    @classmethod
    def _check_argv(cls, value: list[str]) -> list[str]:
        if not value or not value[0].strip():
            raise ValueError("run must contain at least an executable")
        executable = value[0]
        if "/" in executable or "\\" in executable:
            raise ValueError(
                f"Executable must be a bare name, not a path: {executable!r}"
            )
        if executable not in ALLOWED_EXECUTABLES:
            raise ValueError(
                f"Executable {executable!r} is not permitted. Allowed: "
                f"{', '.join(sorted(ALLOWED_EXECUTABLES))}"
            )
        for arg in value:
            if not isinstance(arg, str):
                raise ValueError("All command arguments must be strings")
            if "\x00" in arg:
                raise ValueError("Command arguments must not contain NUL bytes")
        return value

    @field_validator("working_directory")
    @classmethod
    def _check_workdir(cls, value: str) -> str:
        path = value.strip() or "."
        if path.startswith("/") or ".." in Path(path).parts:
            raise ValueError("working_directory must be a relative path inside the "
                             "workspace and must not traverse upwards")
        return path

    @property
    def display(self) -> str:
        return shlex.join(self.run)


class BuildSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Used by the container builder; ignored by the process builder, which
    #: content-addresses the workspace tree directly.
    dockerfile: Annotated[str, Field(max_length=256)] = "Dockerfile"
    context: Annotated[str, Field(max_length=256)] = "."
    build_args: dict[str, str] = Field(default_factory=dict)
    #: Files included in the artifact. Defaults to the whole workspace.
    include: list[str] = Field(default_factory=lambda: ["."])

    @field_validator("dockerfile", "context")
    @classmethod
    def _check_rel(cls, value: str) -> str:
        if value.startswith("/") or ".." in Path(value).parts:
            raise ValueError("Paths must be relative and must not traverse upwards")
        return value

    @field_validator("build_args")
    @classmethod
    def _check_args(cls, value: dict[str, str]) -> dict[str, str]:
        from forge_core.redaction import is_sensitive_key

        for key in value:
            if is_sensitive_key(key):
                raise ValueError(
                    f"build_args must not carry secrets; {key!r} looks like a credential"
                )
        return value


class ServiceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")]
    port: Annotated[int, Field(ge=1, le=65535)] = 8080
    health_path: Annotated[str, Field(max_length=256)] = "/health"
    ready_path: Annotated[str, Field(max_length=256)] = "/ready"
    metrics_path: Annotated[str, Field(max_length=256)] = "/metrics"
    #: argv used to start the service for the process deployment adapter.
    start: list[str] = Field(default_factory=list)

    @field_validator("health_path", "ready_path", "metrics_path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("Probe paths must start with '/'")
        return value


class RolloutSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: Literal["canary"] = "canary"
    steps: list[Annotated[int, Field(ge=1, le=100)]] = Field(default=[10, 25, 50, 100])
    step_seconds: Annotated[int, Field(ge=1, le=3600)] = 20
    max_error_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 0.05
    max_p95_latency_ms: Annotated[float, Field(gt=0)] = 750.0
    min_samples: Annotated[int, Field(ge=1)] = 20

    @field_validator("steps")
    @classmethod
    def _check_steps(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("rollout.steps must not be empty")
        if value != sorted(value):
            raise ValueError("rollout.steps must be ascending")
        if value[-1] != 100:
            raise ValueError("rollout.steps must finish at 100")
        return value


class PipelineManifest(BaseModel):
    """The validated contents of a repository's ``forge.toml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    service: ServiceSpec
    test: list[CommandStep] = Field(default_factory=list, max_length=_MAX_STEPS)
    lint: list[CommandStep] = Field(default_factory=list, max_length=_MAX_STEPS)
    build: BuildSpec = Field(default_factory=BuildSpec)
    rollout: RolloutSpec = Field(default_factory=RolloutSpec)

    @property
    def all_checks(self) -> list[CommandStep]:
        return [*self.lint, *self.test]


def parse_manifest(raw: str | bytes, *, filename: str = "forge.toml") -> PipelineManifest:
    """Parse and validate a manifest, raising :class:`ValidationError` on any problem."""
    data: Any
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        if filename.endswith(".json"):
            data = json.loads(text)
        else:
            data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            f"{filename} is not valid {'JSON' if filename.endswith('.json') else 'TOML'}: "
            f"{exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValidationError(f"{filename} must contain a mapping at the top level.")

    try:
        return PipelineManifest.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise ValidationError(
            f"{filename} failed schema validation: {exc}"
        ) from exc


def find_manifest(workspace: Path) -> Path | None:
    for name in MANIFEST_FILENAMES:
        candidate = workspace / name
        if candidate.is_file():
            return candidate
    return None


def load_manifest(workspace: Path) -> PipelineManifest:
    path = find_manifest(workspace)
    if path is None:
        raise ValidationError(
            "No pipeline manifest found. Expected one of: "
            + ", ".join(MANIFEST_FILENAMES)
        )
    return parse_manifest(path.read_text(encoding="utf-8"), filename=path.name)


__all__ = [
    "ALLOWED_EXECUTABLES",
    "MANIFEST_FILENAMES",
    "BuildSpec",
    "CommandStep",
    "PipelineManifest",
    "RolloutSpec",
    "ServiceSpec",
    "find_manifest",
    "load_manifest",
    "parse_manifest",
]
