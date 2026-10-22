"""Pipeline manifest schema and its command allowlist."""

from __future__ import annotations

import pytest
from forge_core.errors import ValidationError
from forge_core.manifest import CommandStep, PipelineManifest, parse_manifest

MINIMAL = b"""
version = 1
[service]
name = "demo"
"""


class TestParsing:
    def test_minimal_manifest_parses(self) -> None:
        manifest = parse_manifest(MINIMAL)
        assert manifest.service.name == "demo"
        assert manifest.service.port == 8080
        assert manifest.rollout.steps == [10, 25, 50, 100]

    def test_invalid_toml_is_reported_clearly(self) -> None:
        with pytest.raises(ValidationError, match="not valid TOML"):
            parse_manifest(b"this is [not toml")

    def test_json_manifest_is_supported(self) -> None:
        manifest = parse_manifest(
            b'{"version": 1, "service": {"name": "demo"}}', filename="forge.json"
        )
        assert manifest.service.name == "demo"

    def test_unknown_top_level_key_is_rejected(self) -> None:
        """extra='forbid' catches typos rather than silently ignoring them."""
        with pytest.raises(ValidationError):
            parse_manifest(MINIMAL + b'\nunexpected = "value"\n')

    def test_non_mapping_root_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="mapping"):
            parse_manifest(b"[1, 2, 3]", filename="forge.json")


class TestCommandAllowlist:
    """A manifest must not be able to run arbitrary commands."""

    @pytest.mark.parametrize(
        "executable",
        ["curl", "wget", "nc", "ssh", "rm", "chmod", "sudo", "docker", "kubectl"],
    )
    def test_non_allowlisted_executables_are_rejected(self, executable: str) -> None:
        with pytest.raises(ValueError, match="not permitted"):
            CommandStep(name="x", run=[executable, "whatever"])

    def test_absolute_paths_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="bare name"):
            CommandStep(name="x", run=["/bin/sh", "-c", "evil"])

    def test_relative_path_traversal_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="bare name"):
            CommandStep(name="x", run=["../../bin/sh"])

    def test_allowlisted_executable_is_accepted(self) -> None:
        step = CommandStep(name="unit", run=["pytest", "-q"])
        assert step.display == "pytest -q"

    def test_empty_command_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            CommandStep(name="x", run=[])

    def test_nul_byte_in_argument_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="NUL"):
            CommandStep(name="x", run=["pytest", "a\x00b"])

    def test_shell_metacharacters_are_inert_arguments(self) -> None:
        """No shell is used, so these are literal argv entries, not injection."""
        step = CommandStep(name="x", run=["echo", "; rm -rf /", "&& curl evil.com"])
        assert step.run[1] == "; rm -rf /"


class TestWorkingDirectory:
    @pytest.mark.parametrize("path", ["/etc", "../outside", "a/../../b"])
    def test_escaping_paths_are_rejected(self, path: str) -> None:
        with pytest.raises(ValueError, match="traverse upwards"):
            CommandStep(name="x", run=["pytest"], working_directory=path)

    def test_nested_relative_path_is_accepted(self) -> None:
        step = CommandStep(name="x", run=["pytest"], working_directory="services/api")
        assert step.working_directory == "services/api"

    def test_empty_working_directory_defaults_to_root(self) -> None:
        assert CommandStep(name="x", run=["pytest"], working_directory=" ").working_directory == "."


class TestBuildAndRollout:
    def test_build_args_rejecting_secrets(self) -> None:
        with pytest.raises(ValidationError, match="credential"):
            parse_manifest(MINIMAL + b'\n[build]\nbuild_args = {API_KEY = "x"}\n')

    def test_dockerfile_traversal_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_manifest(MINIMAL + b'\n[build]\ndockerfile = "../../Dockerfile"\n')

    def test_rollout_steps_must_ascend_to_100(self) -> None:
        with pytest.raises(ValidationError):
            parse_manifest(MINIMAL + b"\n[rollout]\nsteps = [50, 25, 100]\n")
        with pytest.raises(ValidationError):
            parse_manifest(MINIMAL + b"\n[rollout]\nsteps = [10, 50]\n")

    def test_error_rate_must_be_a_fraction(self) -> None:
        with pytest.raises(ValidationError):
            parse_manifest(MINIMAL + b"\n[rollout]\nmax_error_rate = 50\n")

    def test_service_name_must_be_dns_safe(self) -> None:
        with pytest.raises(ValidationError):
            parse_manifest(b'version = 1\n[service]\nname = "Not Valid!"\n')

    def test_probe_paths_must_be_absolute(self) -> None:
        with pytest.raises(ValidationError):
            parse_manifest(
                b'version = 1\n[service]\nname = "demo"\nhealth_path = "health"\n'
            )


def test_all_checks_orders_lint_before_tests() -> None:
    manifest = PipelineManifest.model_validate({
        "version": 1,
        "service": {"name": "demo"},
        "lint": [{"name": "ruff", "run": ["ruff", "check", "."]}],
        "test": [{"name": "unit", "run": ["pytest"]}],
    })
    assert [c.name for c in manifest.all_checks] == ["ruff", "unit"]
