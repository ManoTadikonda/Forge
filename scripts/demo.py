"""Reproducible Forge demonstration.

Runs two releases end to end against the live local stack:

1. **Healthy release** — build v1.0.0 from source, deploy it, confirm it serves.
2. **Faulty release** — build v1.1.0, deploy it with an injected error rate, and
   show Forge detect the breach during the canary and roll back to v1.0.0.

Every assertion is made against observable state: HTTP responses from the running
service, the router's own counters, and the durable deployment events in PostgreSQL.
Nothing is inferred from a status field alone.

Usage:
    python scripts/demo.py            # full demo (healthy, then faulty)
    python scripts/demo.py --healthy  # healthy release only
    python scripts/demo.py --faulty   # faulty release only
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections import Counter
from typing import Any

import httpx

API = os.environ.get("FORGE_API_URL", "http://127.0.0.1:8000")
ROUTER = os.environ.get("FORGE_ROUTER_URL", "http://127.0.0.1:8098")
ROUTER_ADMIN = os.environ.get("FORGE_ROUTER_ADMIN_URL", "http://127.0.0.1:8099")
EMAIL = os.environ.get("FORGE_BOOTSTRAP_ADMIN_EMAIL", "admin@forge.local")
PASSWORD = os.environ.get("FORGE_BOOTSTRAP_ADMIN_PASSWORD", "")
SERVICE = "sample-service"
ENVIRONMENT = "development"
WORK_URL = f"{ROUTER}/{SERVICE}/{ENVIRONMENT}/api/work"

BOLD, DIM, GREEN, RED, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"
)


def step(message: str) -> None:
    print(f"\n{BOLD}▶ {message}{RESET}")


def info(message: str) -> None:
    print(f"  {message}")


def good(message: str) -> None:
    print(f"  {GREEN}✓{RESET} {message}")


def bad(message: str) -> None:
    print(f"  {RED}✗{RESET} {message}")


def note(message: str) -> None:
    print(f"  {DIM}{message}{RESET}")


class DemoError(RuntimeError):
    pass


class Forge:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API, timeout=60.0)
        self.headers: dict[str, str] = {}

    def login(self) -> None:
        if not PASSWORD:
            raise DemoError(
                "FORGE_BOOTSTRAP_ADMIN_PASSWORD is not set. Start the stack with "
                "scripts/run_stack.sh (which sets it) or export it yourself."
            )
        response = self.client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        if response.status_code != 200:
            raise DemoError(
                f"Could not sign in as {EMAIL} (HTTP {response.status_code}). "
                f"Is the stack running and the bootstrap admin configured?"
            )
        self.headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    def get(self, path: str, **kwargs: Any) -> Any:
        response = self.client.get(path, headers=self.headers, **kwargs)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        response = self.client.post(path, headers=self.headers, json=payload)
        if response.status_code >= 400:
            raise DemoError(f"POST {path} -> {response.status_code}: {response.text[:300]}")
        return response.json()

    # --------------------------------------------------------------- pipeline

    def repository(self) -> dict[str, Any]:
        for repo in self.get("/api/v1/repositories")["items"]:
            if repo["slug"] == "local/demo/sample-service":
                return repo
        raise DemoError(
            "The demo repository is not registered. Run:\n"
            "    python -m forge_api.cli seed-demo"
        )

    def run_pipeline(self, repository_id: str, *, timeout: int = 300) -> dict[str, Any]:
        run = self.post("/api/v1/pipelines", {"repository_id": repository_id})
        deadline = time.time() + timeout
        while time.time() < deadline:
            detail = self.get(f"/api/v1/pipelines/{run['id']}")
            if detail["state"] in ("SUCCEEDED", "FAILED", "CANCELED"):
                return detail
            time.sleep(1)
        raise DemoError(f"Pipeline {run['id']} did not finish within {timeout}s.")

    def deploy(self, payload: dict[str, Any], *, timeout: int = 420) -> dict[str, Any]:
        created = self.post("/api/v1/deployments", payload)
        deadline = time.time() + timeout
        terminal = {
            "SUCCEEDED", "FAILED", "CANCELED", "ROLLED_BACK", "ROLLBACK_FAILED",
        }
        seen = -1
        while time.time() < deadline:
            detail = self.get(f"/api/v1/deployments/{created['id']}")
            for event in detail["events"]:
                if event["sequence"] > seen:
                    seen = event["sequence"]
                    _print_event(event)
            if detail["state"] in terminal:
                return detail
            time.sleep(1)
        raise DemoError(f"Deployment {created['id']} did not finish within {timeout}s.")


def _print_event(event: dict[str, Any]) -> None:
    kind = event["kind"]
    colour = (
        RED if "rollback" in kind or kind == "deployment_failed"
        else GREEN if kind in ("promoted", "candidate_verified")
        else YELLOW if kind in ("canary_step", "health_evaluated")
        else ""
    )
    print(f"    {colour}{kind:22}{RESET} {event['message'][:92]}")


def sample_traffic(count: int = 60) -> Counter[str]:
    """Send real requests through the router and record which version answered."""
    seen: Counter[str] = Counter()
    with httpx.Client(timeout=10.0) as client:
        for _ in range(count):
            try:
                response = client.get(WORK_URL)
                version = response.headers.get("X-Forge-Version", "unknown")
                seen[f"{version}{'' if response.status_code < 500 else ' (5xx)'}"] += 1
            except httpx.RequestError:
                seen["unreachable"] += 1
    return seen


def router_weights() -> dict[str, int]:
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{ROUTER_ADMIN}/stats",
                params={"service": SERVICE, "environment": ENVIRONMENT},
            )
            if response.status_code == 200:
                return dict(response.json().get("weights", {}))
    except httpx.RequestError:
        pass
    return {}


def set_demo_version(version: str) -> None:
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "forge_api.cli", "set-demo-version",
         "--version", version],
        check=True, stdout=subprocess.DEVNULL,
    )


# ------------------------------------------------------------------- scenarios


def healthy_release(forge: Forge) -> dict[str, Any]:
    step("Scenario 1 — healthy release of v1.0.0")
    repository = forge.repository()
    info(f"repository: {repository['name']} ({repository['url']})")

    step("Building v1.0.0")
    run = forge.run_pipeline(repository["id"])
    if run["state"] != "SUCCEEDED":
        raise DemoError(f"Pipeline failed: {run['failure_reason'][:300]}")
    for stage in run["stages"]:
        marker = {"SUCCEEDED": "✓", "SKIPPED": "-", "FAILED": "✗"}.get(stage["status"], "·")
        detail = stage["message"] or stage["skip_reason"]
        print(f"    {marker} {stage['name']:9} {detail[:72]}")
    artifact = run["artifacts"][0]
    good(f"artifact {artifact['digest']}")
    note(f"version {artifact['version']}, {artifact['size_bytes']} bytes, "
         f"SBOM {artifact['sbom_component_count']} components")

    step("Deploying v1.0.0")
    deployment = forge.deploy({
        "artifact_digest": artifact["digest"],
        "environment": ENVIRONMENT,
        "service_name": SERVICE,
    })
    if deployment["state"] != "SUCCEEDED":
        raise DemoError(f"Deployment ended in {deployment['state']}.")
    good(f"deployment {deployment['state']}, weight {deployment['current_weight']}%")

    step("Verifying with real traffic through the router")
    observed = sample_traffic(40)
    info(f"responses by version: {dict(observed)}")
    if observed.get(artifact["version"], 0) != 40:
        raise DemoError(f"Expected all traffic on {artifact['version']}, saw {dict(observed)}")
    good(f"all 40 requests served by v{artifact['version']}")
    return artifact


def faulty_release(forge: Forge, stable: dict[str, Any]) -> dict[str, Any]:
    step("Scenario 2 — faulty release of v1.1.0 must be rolled back")
    repository = forge.repository()

    step("Committing v1.1.0 to the demo repository and building it")
    set_demo_version("1.1.0")
    run = forge.run_pipeline(repository["id"])
    if run["state"] != "SUCCEEDED":
        raise DemoError(f"Pipeline failed: {run['failure_reason'][:300]}")
    candidate = run["artifacts"][0]
    good(f"artifact {candidate['digest']}")
    note(f"version {candidate['version']} — builds and tests pass; the fault is "
         f"injected at deploy time")

    step("Deploying v1.1.0 with a 50% injected error rate")
    note("SAMPLE_ERROR_RATE=0.5 is explicit configuration, not random behaviour")
    deployment = forge.deploy({
        "artifact_digest": candidate["digest"],
        "environment": ENVIRONMENT,
        "service_name": SERVICE,
        "canary_steps": [10, 25, 50, 100],
        "step_seconds": 5,
        "release_env": {"SAMPLE_ERROR_RATE": "0.5"},
    })

    step("Outcome")
    if deployment["state"] != "ROLLED_BACK":
        bad(f"expected ROLLED_BACK, got {deployment['state']}")
        raise DemoError(
            f"Deployment ended in {deployment['state']}, not ROLLED_BACK. "
            f"reason={deployment['rollback_reason'][:200]}"
        )
    good(f"state: {deployment['state']}")
    good(f"rollback verified by post-rollback HTTP check: "
         f"{deployment['rollback_verified']}")
    info(f"reason: {deployment['rollback_reason'][:160]}")

    step("Verifying traffic was actually restored to the previous release")
    weights = router_weights()
    info(f"router weights: {weights}")
    observed = sample_traffic(40)
    info(f"responses by version: {dict(observed)}")
    stable_count = observed.get(stable["version"], 0)
    if stable_count != 40:
        raise DemoError(
            f"Expected all 40 requests served by the restored v{stable['version']}, "
            f"saw {dict(observed)}"
        )
    good(f"all 40 requests served by the restored v{stable['version']}")
    good("no requests reached the faulty candidate")

    incidents = forge.get("/api/v1/incidents")
    if incidents:
        incident = incidents[0]
        step("Incident record")
        info(f"{incident['severity']} — {incident['title']}")
        evidence = incident.get("evidence", {})
        if evidence:
            note(f"verdict={evidence.get('verdict')} "
                 f"requests={evidence.get('requests')} "
                 f"errors={evidence.get('errors')} "
                 f"error_rate={evidence.get('error_rate')}")
            note(f"breaches={evidence.get('breaches')}")
    return deployment


def main() -> int:
    parser = argparse.ArgumentParser(description="Forge end-to-end demonstration")
    parser.add_argument("--healthy", action="store_true", help="healthy release only")
    parser.add_argument("--faulty", action="store_true", help="faulty release only")
    args = parser.parse_args()

    forge = Forge()
    try:
        forge.login()
        print(f"{BOLD}Forge demonstration{RESET}")
        note(f"api={API} router={ROUTER}")

        stable: dict[str, Any] | None = None
        if not args.faulty:
            stable = healthy_release(forge)
        if not args.healthy:
            if stable is None:
                artifacts = forge.get("/api/v1/artifacts")["items"]
                if not artifacts:
                    raise DemoError("No artifacts exist; run the healthy scenario first.")
                stable = artifacts[-1]
            faulty_release(forge, stable)

        print(f"\n{GREEN}{BOLD}Demonstration complete.{RESET}")
        return 0
    except DemoError as exc:
        print(f"\n{RED}{BOLD}Demo failed:{RESET} {exc}")
        return 1
    except httpx.RequestError as exc:
        print(f"\n{RED}{BOLD}Cannot reach Forge:{RESET} {exc}")
        print("Start the stack first:  scripts/run_stack.sh start")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
