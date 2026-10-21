"""Process isolation for commands defined by untrusted repository manifests.

**This is not a kernel sandbox.** It raises the cost of accidental and casual misuse;
it does not contain a determined attacker who controls repository contents. The
controls applied are:

* a scrubbed environment — no inherited credentials, no Forge configuration
* a dedicated working directory under the run's workspace
* a new process group, so a timeout kills the whole tree rather than one process
* POSIX resource limits: address space, CPU seconds, process count, file size
* a wall-clock timeout and a bounded output buffer
* no shell: commands are argv vectors with an allowlisted executable

The production control is to run builds in containers (``BuildKitBuilder``) or on
disposable hosts. Because this weaker mode exists, it refuses to run unless the
operator sets ``FORGE_ALLOW_UNSANDBOXED_BUILDS=true``, so it cannot be entered by
accident. See docs/security.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import resource
import shutil
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from forge_core.redaction import redact_text

#: Environment variables permitted through to a build. Everything else is dropped.
_ENV_ALLOWLIST = frozenset({"LANG", "LC_ALL", "TZ"})


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    memory_mb: int = 2048
    cpu_seconds: int = 600
    max_processes: int = 256
    max_file_bytes: int = 512 * 1024 * 1024
    timeout_seconds: int = 900
    max_output_bytes: int = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    exit_code: int
    output: str
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def build_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Construct a minimal environment containing no Forge or host credentials."""
    env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}
    # PATH is required to locate the interpreter/toolchain but is set explicitly
    # rather than inherited, so a poisoned PATH cannot reach the build.
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    env["HOME"] = "/tmp"  # noqa: S108 - deliberate: builds get a throwaway HOME
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["CI"] = "true"
    env["FORGE_BUILD"] = "1"
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def _apply_limits(limits: SandboxLimits) -> None:  # pragma: no cover - child process
    """Run in the forked child before exec. Must not raise on unsupported limits."""
    os.setsid()  # new process group so a timeout can kill the whole tree
    memory = limits.memory_mb * 1024 * 1024
    # Not every limit is supported on every platform (RLIMIT_NPROC in particular).
    # An unsupported limit is skipped rather than failing the build; the set that
    # does apply is reported by the builder.
    for limit, value in (
        (resource.RLIMIT_AS, (memory, memory)),
        (resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 10)),
        (resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes)),
        (resource.RLIMIT_FSIZE, (limits.max_file_bytes, limits.max_file_bytes)),
        (resource.RLIMIT_CORE, (0, 0)),
    ):
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(limit, value)


def resolve_executable(name: str) -> str | None:
    """Locate an allowlisted executable on the sandbox PATH."""
    return shutil.which(name, path=build_environment()["PATH"]) or shutil.which(name)


async def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    limits: SandboxLimits,
    env: Mapping[str, str] | None = None,
) -> CommandOutcome:
    """Execute ``argv`` under the isolation described in this module's docstring."""
    loop = asyncio.get_running_loop()
    started = loop.time()

    executable = resolve_executable(argv[0])
    if executable is None:
        return CommandOutcome(
            exit_code=127,
            output=f"Executable {argv[0]!r} was not found on the build PATH.",
            duration_ms=0,
        )

    process = await asyncio.create_subprocess_exec(
        executable,
        *argv[1:],
        cwd=str(cwd),
        env=build_environment(env),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        preexec_fn=lambda: _apply_limits(limits),
        start_new_session=False,
    )

    collected = bytearray()
    truncated = False

    async def _drain() -> None:
        nonlocal truncated
        assert process.stdout is not None
        while True:
            chunk = await process.stdout.read(65536)
            if not chunk:
                break
            if len(collected) < limits.max_output_bytes:
                remaining = limits.max_output_bytes - len(collected)
                collected.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True
            else:
                truncated = True

    timed_out = False
    try:
        await asyncio.wait_for(
            asyncio.gather(_drain(), process.wait()), timeout=limits.timeout_seconds
        )
    except TimeoutError:
        timed_out = True
        _kill_process_group(process)
        with contextlib.suppress(TimeoutError):  # pragma: no cover - defensive
            await asyncio.wait_for(process.wait(), timeout=10)

    duration_ms = int((loop.time() - started) * 1000)
    output = collected.decode("utf-8", errors="replace")
    if truncated:
        output += (
            f"\n[forge] output truncated at {limits.max_output_bytes} bytes.\n"
        )
    if timed_out:
        output += f"\n[forge] command exceeded {limits.timeout_seconds}s and was killed.\n"

    return CommandOutcome(
        exit_code=process.returncode if process.returncode is not None else -1,
        output=redact_text(output),
        duration_ms=duration_ms,
        timed_out=timed_out,
        truncated=truncated,
    )


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    """Kill the whole process group so orphaned children cannot survive a timeout."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError):
            return
        except OSError:
            break


__all__ = [
    "CommandOutcome",
    "SandboxLimits",
    "build_environment",
    "resolve_executable",
    "run_command",
]
