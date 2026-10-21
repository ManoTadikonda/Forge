"""Tests for the sample service, including its deliberate fault modes."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    """Reimport the module so module-level CONFIG picks up the environment."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import sample_service.main as main

    importlib.reload(main)
    return TestClient(main.app)


def test_root_describes_service(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, SAMPLE_VERSION="1.0.0")
    body = client.get("/").json()
    assert body["service"] == "forge-sample-service"
    assert body["version"] == "1.0.0"
    assert body["faulty"] is False


def test_health_and_ready_ok_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, SAMPLE_VERSION="1.0.0")
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_unhealthy_mode_fails_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, SAMPLE_UNHEALTHY="true")
    assert client.get("/health").status_code == 503
    assert client.get("/ready").status_code == 503


def test_work_succeeds_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, SAMPLE_VERSION="1.0.0")
    for _ in range(20):
        assert client.get("/api/work").status_code == 200


def test_error_rate_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 20% error rate must produce exactly 1 failure in every 5 requests."""
    client = _client(monkeypatch, SAMPLE_ERROR_RATE="0.2")
    statuses = [client.get("/api/work").status_code for _ in range(50)]
    assert statuses.count(500) == 10
    assert statuses.count(200) == 40


def test_latency_mode_delays_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, SAMPLE_LATENCY_MS="60")
    body = client.get("/api/work").json()
    assert body["latency_applied_ms"] >= 60


def test_stats_track_requests_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, SAMPLE_ERROR_RATE="0.5")
    for _ in range(10):
        client.get("/api/work")
    stats = client.get("/api/stats").json()
    assert stats["requests"] >= 10
    assert stats["errors"] == 5
    assert 0.0 < stats["error_rate"] <= 1.0


def test_metrics_exposition_is_prometheus_formatted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, SAMPLE_VERSION="1.1.0")
    client.get("/api/work")
    text = client.get("/metrics").text
    assert "# TYPE sample_requests_total counter" in text
    assert 'sample_requests_total{version="1.1.0"' in text
    assert "sample_up" in text


def test_version_endpoint_reports_faulty_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, SAMPLE_VERSION="1.1.0", SAMPLE_ERROR_RATE="0.3")
    body = client.get("/version").json()
    assert body["version"] == "1.1.0"
    assert body["faulty"] is True
