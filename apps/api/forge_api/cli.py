"""Operator command line: migrations, users, demo seeding and cleanup.

Run with ``python -m forge_api.cli <command>``.

The demo seed creates a *local* git repository from the bundled sample service and
registers it with a ``file://`` URL. The HTTP API never accepts such a URL — only
this operator-run command can create one — which is what lets the rollback demo run
with no network access while keeping the SSRF guard strict for user input.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from forge_core.config import get_settings
from forge_core.db.models import Repository, User, normalize_email
from forge_core.db.session import (
    dispose_engine,
    embedded_database_url,
    get_sessionmaker,
    init_engine,
)
from forge_core.enums import Role
from forge_core.logging import configure_logging, get_logger
from forge_core.security.passwords import PasswordPolicyError, hash_password
from sqlalchemy import select, text

from forge_api.bootstrap import ensure_environments

logger = get_logger("forge.cli")

DEMO_REPO_NAME = "forge-sample-service"
#: Marks a repository as the bundled demo so the pipeline runner will accept its
#: local path. See ``forge_worker.handlers.pipeline``.
DEMO_MARKER = "[demo] "


def _git_binary() -> str:
    """Absolute path to git, or a clear error naming the remediation."""
    resolved = shutil.which("git")
    if resolved is None:
        raise FileNotFoundError(
            "git is required to build the demo repository but was not found on "
            "PATH. Install git (for example 'xcode-select --install' on macOS, or "
            "'apt-get install git' on Debian/Ubuntu) and run this command again."
        )
    return resolved


def _git(*args: str, cwd: Path) -> None:
    """Run a git command as a fixed argv vector, with no shell."""
    subprocess.run(  # noqa: S603 - absolute binary, literal argv, no shell
        [_git_binary(), *args],
        cwd=str(cwd),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def build_demo_repository(root: Path, source: Path, *, version: str = "1.0.0") -> Path:
    """Create a git repository containing the sample service at ``version``."""
    work = root / "demo-src"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    for item in ("sample_service", "tests", "pyproject.toml", "Dockerfile", "forge.toml"):
        src = source / item
        if src.is_dir():
            shutil.copytree(src, work / item, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache"))
        elif src.is_file():
            shutil.copyfile(src, work / item)

    _set_version(work, version)

    _git("init", "-q", "-b", "main", cwd=work)
    _git("config", "user.email", "demo@forge.local", cwd=work)
    _git("config", "user.name", "Forge Demo", cwd=work)
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", f"Sample service {version}", cwd=work)
    return work


#: Matches a version assignment regardless of its current value, so setting the
#: demo version is idempotent and reversible. A literal replace of "1.0.0" would
#: only ever work in one direction.
_VERSION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'(__version__\s*=\s*")[^"]*(")'), r"\g<1>{version}\g<2>"),
    (re.compile(r'(version:\s*str\s*=\s*")[^"]*(")'), r"\g<1>{version}\g<2>"),
    (
        re.compile(r'(os\.environ\.get\("SAMPLE_VERSION",\s*")[^"]*(")'),
        r"\g<1>{version}\g<2>",
    ),
    (re.compile(r'(?m)^(version\s*=\s*")[^"]*(")'), r"\g<1>{version}\g<2>"),
)


def _set_version(work: Path, version: str) -> None:
    """Rewrite the demo service's declared version in place."""
    targets = (
        work / "sample_service" / "__init__.py",
        work / "sample_service" / "config.py",
        work / "pyproject.toml",
    )
    for path in targets:
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        updated = original
        for pattern, replacement in _VERSION_PATTERNS:
            updated = pattern.sub(replacement.format(version=version), updated)
        if updated != original:
            path.write_text(updated, encoding="utf-8")


async def _connect() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt="console", service="forge-cli")
    init_engine(settings.database_url or embedded_database_url())


async def cmd_seed_demo(args: argparse.Namespace) -> int:
    """Create the local demo repository and register it."""
    await _connect()
    settings = get_settings()
    root = Path(settings.workspace_root).parent / "demo"

    def _prepare() -> Path:
        source = Path(args.source).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not (source / "sample_service").is_dir():
            raise FileNotFoundError(
                f"{source} does not look like the sample service directory"
            )
        return build_demo_repository(root, source, version=args.version)

    try:
        work = await asyncio.to_thread(_prepare)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # Absolute, so the checkout step resolves it the same way from any process.
    url = f"file://{work.resolve()}"

    maker = get_sessionmaker()
    async with maker() as session, session.begin():
        await ensure_environments(session)
        existing = (
            await session.execute(
                select(Repository).where(
                    Repository.slug == "local/demo/sample-service"
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.url = url
            existing.is_active = True
            repository_id = existing.id
            action = "updated"
        else:
            repository = Repository(
                id=uuid.uuid4(),
                name=DEMO_REPO_NAME,
                url=url,
                slug="local/demo/sample-service",
                default_branch="main",
                description=(
                    DEMO_MARKER
                    + "Bundled sample service used by the Forge rollback demo. "
                    "Registered from a local path by the operator seed command; the "
                    "HTTP API does not accept local URLs."
                ),
            )
            session.add(repository)
            await session.flush()
            repository_id = repository.id
            action = "created"

    print(json.dumps({
        "status": action,
        "repository_id": str(repository_id),
        "url": url,
        "version": args.version,
    }, indent=2))
    await dispose_engine()
    return 0


async def cmd_set_demo_version(args: argparse.Namespace) -> int:
    """Commit a new version of the demo source so the next pipeline builds it."""
    await _connect()
    settings = get_settings()
    work = Path(settings.workspace_root).parent / "demo" / "demo-src"

    def _commit() -> bool:
        """Returns True when a new commit was made, False when nothing changed."""
        if not (work / ".git").is_dir():
            raise FileNotFoundError(
                "demo repository not found; run 'seed-demo' first"
            )
        _set_version(work, args.version)
        _git("add", "-A", cwd=work)
        try:
            _git("commit", "-q", "-m", f"Sample service {args.version}", cwd=work)
        except subprocess.CalledProcessError as exc:
            # "nothing to commit" is the expected outcome when the demo is already
            # at this version; re-running the demo must stay idempotent.
            stderr = (exc.stderr or b"").decode("utf-8", "replace")
            if "nothing to commit" in stderr or exc.returncode == 1:
                return False
            raise
        return True

    try:
        committed = await asyncio.to_thread(_commit)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "committed" if committed else "unchanged",
        "version": args.version,
    }, indent=2))
    await dispose_engine()
    return 0


async def cmd_create_user(args: argparse.Namespace) -> int:
    await _connect()
    password = args.password or getpass.getpass("Password: ")
    email = normalize_email(args.email)
    maker = get_sessionmaker()
    async with maker() as session, session.begin():
        existing = await session.scalar(select(User.id).where(User.email == email))
        if existing is not None:
            print(f"error: a user with email {email} already exists", file=sys.stderr)
            return 1
        try:
            password_hash = hash_password(password)
        except PasswordPolicyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        session.add(
            User(
                id=uuid.uuid4(), email=email,
                display_name=args.display_name or email.split("@")[0],
                password_hash=password_hash, role=Role(args.role).value, is_active=True,
            )
        )
    print(json.dumps({"status": "created", "email": email, "role": args.role}, indent=2))
    await dispose_engine()
    return 0


async def cmd_reset_runtime(args: argparse.Namespace) -> int:
    """Delete pipeline/deployment history. Does **not** touch users or repositories."""
    if not args.yes:
        print("Refusing to delete runtime data without --yes.", file=sys.stderr)
        return 1
    await _connect()
    maker = get_sessionmaker()
    async with maker() as session, session.begin():
        # Order matters: children before parents.
        for table in (
            "health_samples", "deployment_events", "incidents", "releases",
            "deployments", "log_chunks", "pipeline_stages", "artifacts",
            "pipeline_runs", "jobs", "deployment_locks", "webhook_deliveries",
        ):
            await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608
        await session.execute(text("UPDATE services SET current_artifact_id = NULL"))
        await session.execute(text("DELETE FROM services"))
    print(json.dumps({
        "status": "runtime_reset",
        "preserved": ["users", "repositories", "environments", "audit_events"],
        "at": datetime.now(UTC).isoformat(),
    }, indent=2))
    await dispose_engine()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge", description="Forge operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed-demo", help="Create and register the demo repository")
    seed.add_argument("--source", default="apps/sample-service")
    seed.add_argument("--version", default="1.0.0")
    seed.set_defaults(func=cmd_seed_demo)

    bump = sub.add_parser("set-demo-version", help="Commit a new demo source version")
    bump.add_argument("--version", required=True)
    bump.set_defaults(func=cmd_set_demo_version)

    user = sub.add_parser("create-user", help="Create a user")
    user.add_argument("--email", required=True)
    user.add_argument("--password", default="")
    user.add_argument("--display-name", default="")
    user.add_argument("--role", default="DEVELOPER",
                      choices=[r.value for r in Role])
    user.set_defaults(func=cmd_create_user)

    reset = sub.add_parser(
        "reset-runtime",
        help="Delete pipeline and deployment history (keeps users and repositories)",
    )
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=cmd_reset_runtime)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(asyncio.run(args.func(args)))


if __name__ == "__main__":
    raise SystemExit(main())
