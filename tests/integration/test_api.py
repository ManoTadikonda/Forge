"""API behaviour against a real database: auth, authorization, validation, webhooks."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from forge_core.config import Settings
from forge_core.db.models import User, normalize_email
from forge_core.enums import Role
from forge_core.security.passwords import hash_password
from forge_core.security.webhooks import compute_signature
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

Maker = async_sessionmaker[AsyncSession]
WEBHOOK_SECRET = "test-webhook-secret"
PASSWORD = "test-password-at-least-12"


@pytest_asyncio.fixture
async def api(
    sessionmaker_fixture: Maker, engine: object, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client wired to the test database, with lifespan bypassed.

    The app's own lifespan would create a second engine against the real data
    directory; here the engine fixture is injected instead so each test is isolated.
    """
    import forge_core.db.session as session_module
    from fastapi import FastAPI
    from forge_api.errors import install_error_handlers
    from forge_api.main import API_PREFIX
    from forge_api.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
    from forge_api.ratelimit import InMemoryRateLimiter
    from forge_api.routers import (
        admin,
        artifacts,
        audit,
        auth,
        deployments,
        environments,
        health,
        metrics,
        pipelines,
        repositories,
        webhooks,
    )

    monkeypatch.setattr(session_module, "_engine", engine, raising=False)
    monkeypatch.setattr(
        session_module, "_sessionmaker", sessionmaker_fixture, raising=False
    )

    settings = Settings(
        environment="test",
        secret_key="test-secret-key-at-least-32-characters-long!!",
        github_webhook_secret=WEBHOOK_SECRET,
        rate_limit_enabled=False,
        csrf_protection_enabled=True,
    )
    app = FastAPI()
    app.state.settings = settings
    app.state.rate_limiter = InMemoryRateLimiter()
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(metrics.router)
    for module in (auth, repositories, pipelines, deployments, environments,
                   artifacts, audit, admin, webhooks):
        app.include_router(module.router, prefix=API_PREFIX)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def make_user(maker: Maker, email: str, role: Role) -> uuid.UUID:
    async with maker() as session, session.begin():
        user = User(
            id=uuid.uuid4(), email=normalize_email(email), display_name=email,
            password_hash=hash_password(PASSWORD), role=role.value, is_active=True,
        )
        session.add(user)
        await session.flush()
        return user.id


async def login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class TestAuthentication:
    async def test_protected_routes_require_authentication(
        self, api: httpx.AsyncClient
    ) -> None:
        for path in ("/api/v1/repositories", "/api/v1/overview", "/api/v1/audit"):
            response = await api.get(path)
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "authentication_required"

    async def test_login_succeeds_with_valid_credentials(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        response = await api.post(
            "/api/v1/auth/login",
            json={"email": "dev@example.com", "password": PASSWORD},
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    async def test_email_lookup_is_case_insensitive(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        response = await api.post(
            "/api/v1/auth/login",
            json={"email": "DEV@Example.COM", "password": PASSWORD},
        )
        assert response.status_code == 200

    async def test_unknown_and_wrong_password_are_indistinguishable(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        """Different messages here would be an account-enumeration oracle."""
        await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        unknown = await api.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": PASSWORD},
        )
        wrong = await api.post(
            "/api/v1/auth/login",
            json={"email": "dev@example.com", "password": "wrong-password-x"},
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["error"] == wrong.json()["error"]

    async def test_logout_revokes_the_token_immediately(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        headers = await login(api, "dev@example.com")
        assert (await api.get("/api/v1/auth/me", headers=headers)).status_code == 200
        assert (await api.post("/api/v1/auth/logout", headers=headers)).status_code == 204
        assert (await api.get("/api/v1/auth/me", headers=headers)).status_code == 401

    async def test_inactive_account_cannot_authenticate(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        user_id = await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        headers = await login(api, "dev@example.com")
        async with sessionmaker_fixture() as session, session.begin():
            user = await session.get(User, user_id)
            assert user is not None
            user.is_active = False
        assert (await api.get("/api/v1/auth/me", headers=headers)).status_code == 401

    async def test_failed_login_is_recorded_in_the_audit_log(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        """Regression: the failure path must commit before raising.

        The request-scoped session rolls back on an exception, which previously
        discarded both the attempt counter and this audit record.
        """
        await make_user(sessionmaker_fixture, "admin@example.com", Role.ADMIN)
        await api.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "wrong-password-x"},
        )
        headers = await login(api, "admin@example.com")
        events = (await api.get(
            "/api/v1/audit", headers=headers, params={"outcome": "FAILURE"}
        )).json()
        assert events["total"] >= 1
        assert events["items"][0]["action"] == "login"

    async def test_repeated_failures_lock_the_account(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        for _ in range(11):
            await api.post(
                "/api/v1/auth/login",
                json={"email": "dev@example.com", "password": "wrong-password-x"},
            )
        response = await api.post(
            "/api/v1/auth/login",
            json={"email": "dev@example.com", "password": PASSWORD},
        )
        assert response.status_code == 401
        assert "locked" in response.json()["error"]["message"].lower()


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("POST", "/api/v1/repositories", {"url": "https://github.com/a/b"}),
            ("POST", "/api/v1/deployments", {"artifact_digest": "sha256:x"}),
            ("GET", "/api/v1/admin/users", None),
            ("GET", "/api/v1/audit", None),
        ],
    )
    async def test_viewer_is_denied_privileged_operations(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker,
        method: str, path: str, payload: dict[str, Any] | None,
    ) -> None:
        await make_user(sessionmaker_fixture, "viewer@example.com", Role.VIEWER)
        headers = await login(api, "viewer@example.com")
        response = await api.request(method, path, headers=headers, json=payload)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    async def test_viewer_may_read(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "viewer@example.com", Role.VIEWER)
        headers = await login(api, "viewer@example.com")
        assert (await api.get("/api/v1/repositories", headers=headers)).status_code == 200

    async def test_developer_cannot_manage_users(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        headers = await login(api, "dev@example.com")
        response = await api.post(
            "/api/v1/admin/users", headers=headers,
            json={"email": "x@example.com", "password": "a-password-123456"},
        )
        assert response.status_code == 403

    async def test_admin_may_manage_users(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "admin@example.com", Role.ADMIN)
        headers = await login(api, "admin@example.com")
        response = await api.post(
            "/api/v1/admin/users", headers=headers,
            json={"email": "new@example.com", "password": "a-password-123456",
                  "role": "DEVELOPER"},
        )
        assert response.status_code == 201

    async def test_admin_cannot_deactivate_themselves(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        """Locking the last admin out of their own platform is not a useful outcome."""
        user_id = await make_user(sessionmaker_fixture, "admin@example.com", Role.ADMIN)
        headers = await login(api, "admin@example.com")
        response = await api.post(
            f"/api/v1/admin/users/{user_id}/deactivate", headers=headers
        )
        assert response.status_code == 409


class TestRepositoryValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "http://github.com/a/b", "file:///etc/passwd", "https://127.0.0.1/x",
            "https://169.254.169.254/latest/meta-data/",
            "https://user:pass@github.com/a/b", "https://10.0.0.1/x",
            "ssh://git@github.com/a/b",
        ],
    )
    async def test_unsafe_urls_are_rejected(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker, url: str
    ) -> None:
        await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        headers = await login(api, "dev@example.com")
        response = await api.post(
            "/api/v1/repositories", headers=headers, json={"url": url}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsafe_url"

    async def test_duplicate_registration_conflicts(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        headers = await login(api, "dev@example.com")
        payload = {"url": "https://github.com/psf/requests"}
        first = await api.post("/api/v1/repositories", headers=headers, json=payload)
        assert first.status_code == 201
        second = await api.post("/api/v1/repositories", headers=headers, json=payload)
        assert second.status_code == 409

    async def test_deletion_retains_history(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        """Audit and pipeline history must survive repository removal."""
        await make_user(sessionmaker_fixture, "admin@example.com", Role.ADMIN)
        headers = await login(api, "admin@example.com")
        created = await api.post(
            "/api/v1/repositories", headers=headers,
            json={"url": "https://github.com/psf/requests"},
        )
        repository_id = created.json()["id"]
        assert (await api.delete(
            f"/api/v1/repositories/{repository_id}", headers=headers
        )).status_code == 204
        # The row still exists (deactivated), so historical references resolve.
        assert (await api.get(
            f"/api/v1/repositories/{repository_id}", headers=headers
        )).status_code == 200
        listed = await api.get("/api/v1/repositories", headers=headers)
        assert listed.json()["total"] == 0


class TestWebhooks:
    async def test_unsigned_delivery_is_rejected(self, api: httpx.AsyncClient) -> None:
        response = await api.post(
            "/api/v1/webhooks/github", content=b"{}",
            headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "d1"},
        )
        assert response.status_code == 401

    async def test_bad_signature_is_rejected(self, api: httpx.AsyncClient) -> None:
        response = await api.post(
            "/api/v1/webhooks/github", content=b"{}",
            headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "d1",
                     "X-Hub-Signature-256": "sha256=deadbeef"},
        )
        assert response.status_code == 401

    async def test_valid_ping_is_acknowledged(self, api: httpx.AsyncClient) -> None:
        payload = b'{"zen": "Keep it logically awesome."}'
        response = await api.post(
            "/api/v1/webhooks/github", content=payload,
            headers={
                "X-GitHub-Event": "ping", "X-GitHub-Delivery": "d-ping",
                "X-Hub-Signature-256": compute_signature(payload, WEBHOOK_SECRET),
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "pong"

    async def test_redelivery_is_deduplicated(self, api: httpx.AsyncClient) -> None:
        """GitHub retries; a retry must never trigger a second build."""
        payload = b'{"zen": "x"}'
        headers = {
            "X-GitHub-Event": "ping", "X-GitHub-Delivery": "d-dup",
            "X-Hub-Signature-256": compute_signature(payload, WEBHOOK_SECRET),
        }
        first = await api.post("/api/v1/webhooks/github", content=payload, headers=headers)
        second = await api.post("/api/v1/webhooks/github", content=payload, headers=headers)
        assert first.json()["status"] == "pong"
        assert second.json()["status"] == "duplicate"

    async def test_unregistered_repository_is_ignored(
        self, api: httpx.AsyncClient
    ) -> None:
        payload = (
            b'{"ref": "refs/heads/main", "repository": '
            b'{"clone_url": "https://github.com/unknown/repo.git"}}'
        )
        response = await api.post(
            "/api/v1/webhooks/github", content=payload,
            headers={
                "X-GitHub-Event": "push", "X-GitHub-Delivery": "d-unknown",
                "X-Hub-Signature-256": compute_signature(payload, WEBHOOK_SECRET),
            },
        )
        assert response.json()["status"] == "ignored"


class TestResponseHygiene:
    async def test_security_headers_are_present(self, api: httpx.AsyncClient) -> None:
        response = await api.get("/health")
        for header in (
            "X-Content-Type-Options", "X-Frame-Options",
            "Content-Security-Policy", "Referrer-Policy",
        ):
            assert header in response.headers

    async def test_every_response_carries_a_request_id(
        self, api: httpx.AsyncClient
    ) -> None:
        assert (await api.get("/health")).headers.get("X-Request-ID")

    async def test_errors_do_not_leak_internals(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        headers = await login(api, "dev@example.com")
        response = await api.get("/api/v1/pipelines/not-a-uuid", headers=headers)
        body = response.text.lower()
        for leak in ("traceback", "sqlalchemy", "select ", "/users/"):
            assert leak not in body

    async def test_user_listing_never_returns_password_hashes(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "admin@example.com", Role.ADMIN)
        headers = await login(api, "admin@example.com")
        response = await api.get("/api/v1/admin/users", headers=headers)
        assert "password" not in response.text.lower()
        assert "argon2" not in response.text.lower()

    async def test_settings_expose_no_secret_values(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        await make_user(sessionmaker_fixture, "admin@example.com", Role.ADMIN)
        headers = await login(api, "admin@example.com")
        response = await api.get("/api/v1/admin/settings", headers=headers)
        body = response.json()
        assert body["webhook_secret_configured"] is True
        assert WEBHOOK_SECRET not in response.text

    async def test_overview_reports_null_success_rate_when_empty(
        self, api: httpx.AsyncClient, sessionmaker_fixture: Maker
    ) -> None:
        """An empty platform must not claim a 0% success rate."""
        await make_user(sessionmaker_fixture, "dev@example.com", Role.DEVELOPER)
        headers = await login(api, "dev@example.com")
        stats = (await api.get("/api/v1/overview", headers=headers)).json()["stats"]
        assert stats["pipelines_total"] == 0
        assert stats["pipeline_success_rate"] is None
