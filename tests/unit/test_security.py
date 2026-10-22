"""Passwords, tokens, webhook signatures and role permissions."""

from __future__ import annotations

import pytest
from forge_core.authz import (
    Permission,
    has_permission,
    permissions_for,
    require_permission,
)
from forge_core.enums import Role
from forge_core.errors import AuthenticationError, AuthorizationError
from forge_core.security.passwords import (
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
)
from forge_core.security.tokens import (
    constant_time_equals,
    create_access_token,
    decode_access_token,
    generate_csrf_token,
)
from forge_core.security.webhooks import compute_signature, verify_signature

SECRET = "a" * 48


class TestPasswords:
    def test_hash_is_salted_and_verifies(self) -> None:
        password = "correct horse battery staple"
        first, second = hash_password(password), hash_password(password)
        assert first != second, "identical passwords must not share a hash"
        assert verify_password(password, first)
        assert verify_password(password, second)

    def test_wrong_password_fails(self) -> None:
        assert not verify_password("wrong", hash_password("a-valid-password-123"))

    def test_hash_is_argon2id(self) -> None:
        assert hash_password("a-valid-password-123").startswith("$argon2id$")

    def test_plaintext_never_appears_in_the_hash(self) -> None:
        password = "uniquePlaintextMarker99"
        assert password not in hash_password(password)

    @pytest.mark.parametrize("password", ["", "short", "           "])
    def test_policy_rejects_weak_passwords(self, password: str) -> None:
        with pytest.raises(PasswordPolicyError):
            validate_password_policy(password)

    def test_policy_rejects_absurdly_long_passwords(self) -> None:
        """Unbounded input would make hashing itself a denial-of-service vector."""
        with pytest.raises(PasswordPolicyError):
            validate_password_policy("x" * 100_000)

    def test_verify_is_false_for_malformed_stored_hash(self) -> None:
        assert not verify_password("anything", "not-a-hash")
        assert not verify_password("anything", "")

    def test_verify_rejects_oversized_input_without_hashing(self) -> None:
        assert not verify_password("x" * 100_000, hash_password("a-valid-password-123"))

    def test_needs_rehash_for_invalid_hash(self) -> None:
        assert needs_rehash("garbage")
        assert not needs_rehash(hash_password("a-valid-password-123"))


class TestTokens:
    def test_roundtrip_preserves_claims(self) -> None:
        token, claims = create_access_token(
            subject="user-1", role="ADMIN", secret=SECRET, ttl_seconds=60
        )
        decoded = decode_access_token(token, secret=SECRET)
        assert decoded.subject == "user-1"
        assert decoded.role == "ADMIN"
        assert decoded.jti == claims.jti

    def test_token_signed_with_another_key_is_rejected(self) -> None:
        token, _ = create_access_token(subject="u", role="VIEWER", secret=SECRET, ttl_seconds=60)
        with pytest.raises(AuthenticationError):
            decode_access_token(token, secret="b" * 48)

    def test_expired_token_is_rejected(self) -> None:
        token, _ = create_access_token(subject="u", role="VIEWER", secret=SECRET, ttl_seconds=-1)
        with pytest.raises(AuthenticationError, match="expired"):
            decode_access_token(token, secret=SECRET)

    def test_tampered_token_is_rejected(self) -> None:
        token, _ = create_access_token(subject="u", role="VIEWER", secret=SECRET, ttl_seconds=60)
        head, payload, signature = token.split(".")
        with pytest.raises(AuthenticationError):
            decode_access_token(f"{head}.{payload}x.{signature}", secret=SECRET)

    def test_garbage_token_is_rejected(self) -> None:
        with pytest.raises(AuthenticationError):
            decode_access_token("not-a-token", secret=SECRET)

    def test_each_token_has_a_unique_jti(self) -> None:
        """Unique ids are what make individual sessions revocable."""
        ids = {
            create_access_token(subject="u", role="VIEWER", secret=SECRET, ttl_seconds=60)[1].jti
            for _ in range(20)
        }
        assert len(ids) == 20

    def test_error_message_does_not_disclose_the_reason(self) -> None:
        with pytest.raises(AuthenticationError) as caught:
            decode_access_token("a.b.c", secret=SECRET)
        assert caught.value.message == "Invalid authentication token."

    def test_csrf_tokens_are_unique_and_long(self) -> None:
        tokens = {generate_csrf_token() for _ in range(50)}
        assert len(tokens) == 50
        assert all(len(t) >= 32 for t in tokens)

    def test_constant_time_comparison(self) -> None:
        assert constant_time_equals("abc", "abc")
        assert not constant_time_equals("abc", "abd")
        assert not constant_time_equals("abc", "")


class TestWebhookSignatures:
    def test_valid_signature_passes(self) -> None:
        payload = b'{"ref": "refs/heads/main"}'
        verify_signature(payload, compute_signature(payload, "s3cret"), "s3cret")

    def test_tampered_payload_is_rejected(self) -> None:
        signature = compute_signature(b'{"a": 1}', "s3cret")
        with pytest.raises(AuthenticationError, match="verification failed"):
            verify_signature(b'{"a": 2}', signature, "s3cret")

    def test_wrong_secret_is_rejected(self) -> None:
        payload = b"{}"
        with pytest.raises(AuthenticationError):
            verify_signature(payload, compute_signature(payload, "right"), "wrong")

    def test_unset_secret_disables_the_endpoint(self) -> None:
        """An unconfigured secret must fail closed, never accept everything."""
        payload = b"{}"
        with pytest.raises(AuthenticationError, match="disabled"):
            verify_signature(payload, compute_signature(payload, "x"), "")

    @pytest.mark.parametrize("header", [None, "", "deadbeef", "sha1=abc", "sha256="])
    def test_missing_or_malformed_headers_are_rejected(self, header: str | None) -> None:
        with pytest.raises(AuthenticationError):
            verify_signature(b"{}", header, "s3cret")


class TestAuthorization:
    def test_viewer_is_read_only(self) -> None:
        for permission in (
            Permission.REPO_WRITE,
            Permission.REPO_DELETE,
            Permission.PIPELINE_TRIGGER,
            Permission.DEPLOYMENT_CREATE,
            Permission.DEPLOYMENT_ROLLBACK,
            Permission.USER_MANAGE,
            Permission.SETTINGS_WRITE,
            Permission.AUDIT_READ,
        ):
            assert not has_permission(Role.VIEWER, permission)
        assert has_permission(Role.VIEWER, Permission.REPO_READ)

    def test_developer_can_deploy_but_not_administer(self) -> None:
        assert has_permission(Role.DEVELOPER, Permission.DEPLOYMENT_CREATE)
        assert has_permission(Role.DEVELOPER, Permission.PIPELINE_TRIGGER)
        assert not has_permission(Role.DEVELOPER, Permission.USER_MANAGE)
        assert not has_permission(Role.DEVELOPER, Permission.REPO_DELETE)

    def test_admin_has_every_permission(self) -> None:
        assert permissions_for(Role.ADMIN) == frozenset(Permission)

    def test_roles_are_strictly_nested(self) -> None:
        viewer = permissions_for(Role.VIEWER)
        developer = permissions_for(Role.DEVELOPER)
        admin = permissions_for(Role.ADMIN)
        assert viewer < developer < admin

    def test_require_permission_raises_for_denied(self) -> None:
        with pytest.raises(AuthorizationError) as caught:
            require_permission(Role.VIEWER, Permission.DEPLOYMENT_CREATE)
        assert caught.value.details["required_permission"] == "deployment:create"

    def test_require_permission_passes_for_granted(self) -> None:
        require_permission(Role.ADMIN, Permission.USER_MANAGE)
