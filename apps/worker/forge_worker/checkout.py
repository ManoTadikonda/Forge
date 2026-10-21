"""Source acquisition.

Two code paths, deliberately separated:

* :func:`clone_https` handles user-registered repositories. The URL has already passed
  :func:`forge_core.urlguard.validate_repo_url`, and it is re-validated here
  immediately before the connection to narrow the DNS-rebinding window.
* :func:`clone_local` handles the bundled demo repository only. It accepts a
  ``file://`` path, which the API never permits for user input — a local path is
  reachable only through the operator-run seed command. This is what lets the
  rollback demonstration run with no network access.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dulwich import porcelain
from dulwich.repo import Repo
from forge_core.errors import ValidationError
from forge_core.logging import get_logger
from forge_core.urlguard import assert_resolved_addresses_allowed, validate_repo_url

logger = get_logger("forge.checkout")


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    workspace: Path
    commit_sha: str
    commit_message: str
    commit_author: str
    branch: str


def _head_details(repo_path: Path, branch: str) -> tuple[str, str, str]:
    with Repo(str(repo_path)) as repo:
        head = repo.head()
        commit = repo[head]
        message = commit.message.decode("utf-8", "replace").strip()
        author = commit.author.decode("utf-8", "replace")
        return head.decode("ascii"), message, author


async def clone_https(
    url: str, destination: Path, *, branch: str = "main", depth: int = 1
) -> CheckoutResult:
    """Clone a validated public HTTPS repository into ``destination``."""
    validated = validate_repo_url(url, resolve=True)
    # Re-check the resolved addresses right before connecting. This does not fully
    # close the TOCTOU window; the documented production control is a network-level
    # egress policy (docs/security.md).
    assert_resolved_addresses_allowed(validated.addresses, validated.host)

    def _clone() -> None:
        destination.mkdir(parents=True, exist_ok=True)
        porcelain.clone(
            url, target=str(destination), depth=depth, checkout=True,
            branch=branch.encode() if branch else None,
        )

    try:
        await asyncio.to_thread(_clone)
    except Exception as exc:
        raise ValidationError(
            f"Could not clone the repository on branch {branch!r}: "
            f"{type(exc).__name__}."
        ) from exc

    sha, message, author = await asyncio.to_thread(_head_details, destination, branch)
    _strip_vcs_metadata(destination)
    return CheckoutResult(destination, sha, message, author, branch)


async def clone_local(
    url: str, destination: Path, *, branch: str = "main"
) -> CheckoutResult:
    """Clone the bundled demo repository from a local path.

    Only reachable via the operator seed command; the API rejects non-HTTPS URLs.
    """
    parts = urlsplit(url)
    if parts.scheme != "file":
        raise ValidationError("clone_local only accepts file:// URLs.")
    source = Path(parts.path)

    def _clone() -> None:
        if not source.exists():
            raise ValidationError(f"Local repository {source} does not exist.")
        destination.mkdir(parents=True, exist_ok=True)
        porcelain.clone(str(source), target=str(destination), checkout=True)

    try:
        await asyncio.to_thread(_clone)
    except Exception as exc:
        raise ValidationError(
            f"Could not clone the local repository: {type(exc).__name__}: {exc}"
        ) from exc

    sha, message, author = await asyncio.to_thread(_head_details, destination, branch)
    _strip_vcs_metadata(destination)
    return CheckoutResult(destination, sha, message, author, branch)


async def checkout(
    url: str, destination: Path, *, branch: str = "main", allow_local: bool = False
) -> CheckoutResult:
    """Dispatch to the correct checkout strategy for ``url``."""
    scheme = urlsplit(url).scheme.lower()
    if scheme == "file":
        if not allow_local:
            raise ValidationError(
                "Local repository paths are not permitted for this repository."
            )
        return await clone_local(url, destination, branch=branch)
    return await clone_https(url, destination, branch=branch)


def _strip_vcs_metadata(workspace: Path) -> None:
    """Remove ``.git`` after checkout.

    Build and test commands run against repository contents only. Removing the VCS
    metadata means a malicious manifest cannot use git tooling to reach the network
    or read credentials from a git config the checkout dragged along.
    """
    shutil.rmtree(workspace / ".git", ignore_errors=True)


__all__ = ["CheckoutResult", "checkout", "clone_https", "clone_local"]
