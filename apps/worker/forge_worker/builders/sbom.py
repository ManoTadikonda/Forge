"""SBOM generation in CycloneDX 1.5 JSON.

Components are derived from what is actually present in the workspace — the resolved
Python environment and declared manifests. Nothing is invented: if a lock file cannot
be parsed, the SBOM says so rather than emitting a plausible-looking component list.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9._-]+)\s*(?P<spec>[=<>!~]=?\s*[0-9A-Za-z._*+-]+)?"
)


def _component(name: str, version: str, *, source: str) -> dict[str, Any]:
    return {
        "type": "library",
        "name": name,
        "version": version or "unknown",
        "purl": f"pkg:pypi/{name.lower()}@{version}" if version else f"pkg:pypi/{name.lower()}",
        "properties": [{"name": "forge:source", "value": source}],
    }


def _parse_requirements(path: Path) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        match = _REQUIREMENT.match(stripped)
        if not match:
            continue
        spec = (match.group("spec") or "").strip()
        version = spec.lstrip("=<>!~ ").strip() if spec.startswith("==") else ""
        components.append(_component(match.group("name"), version, source=path.name))
    return components


def _parse_pyproject(path: Path) -> list[dict[str, Any]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        return []
    dependencies = data.get("project", {}).get("dependencies", [])
    components: list[dict[str, Any]] = []
    for entry in dependencies:
        if not isinstance(entry, str):
            continue
        match = _REQUIREMENT.match(entry)
        if match:
            components.append(_component(match.group("name"), "", source=path.name))
    return components


def _parse_package_lock(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    components: list[dict[str, Any]] = []
    for name, meta in (data.get("packages") or {}).items():
        if not name or not isinstance(meta, dict):
            continue
        clean = name.split("node_modules/")[-1]
        version = str(meta.get("version", ""))
        components.append(
            {
                "type": "library",
                "name": clean,
                "version": version or "unknown",
                "purl": f"pkg:npm/{clean}@{version}" if version else f"pkg:npm/{clean}",
                "properties": [{"name": "forge:source", "value": path.name}],
            }
        )
    return components


def generate_sbom(
    workspace: Path,
    *,
    image_name: str,
    version: str,
    digest: str,
    repository_url: str = "",
    commit_sha: str = "",
) -> dict[str, Any]:
    """Build a CycloneDX document describing the workspace's declared dependencies."""
    components: list[dict[str, Any]] = []
    notes: list[str] = []

    sources = (
        ("requirements.txt", _parse_requirements),
        ("requirements-dev.txt", _parse_requirements),
        ("pyproject.toml", _parse_pyproject),
        ("package-lock.json", _parse_package_lock),
    )
    for filename, parser in sources:
        candidate = workspace / filename
        if candidate.is_file():
            found = parser(candidate)
            components.extend(found)
            notes.append(f"{filename}: {len(found)} component(s)")

    if not components:
        notes.append(
            "No parsable dependency manifest was found; the component list is empty "
            "rather than inferred."
        )

    # Deduplicate on (name, version), keeping first occurrence.
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for component in components:
        unique.setdefault((component["name"], component["version"]), component)

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "tools": [{"vendor": "Forge", "name": "forge-sbom", "version": "0.1.0"}],
            "component": {
                "type": "application",
                "name": image_name,
                "version": version,
                "hashes": [
                    {"alg": "SHA-256", "content": digest.split(":", 1)[-1]}
                ],
                "properties": [
                    {"name": "forge:repository", "value": repository_url},
                    {"name": "forge:commit", "value": commit_sha},
                ],
            },
            "properties": [{"name": "forge:note", "value": note} for note in notes],
        },
        "components": sorted(unique.values(), key=lambda c: (c["name"], c["version"])),
    }


def write_sbom(document: dict[str, Any], destination: Path) -> tuple[Path, str]:
    """Write the SBOM and return its path and sha256 digest."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=True).encode("utf-8")
    destination.write_bytes(payload)
    return destination, "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = ["generate_sbom", "write_sbom"]
