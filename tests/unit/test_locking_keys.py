"""Lock resource key derivation (no database required)."""

from __future__ import annotations

import uuid

from forge_worker import locking


def test_resource_key_is_stable() -> None:
    service, environment = uuid.uuid4(), uuid.uuid4()
    assert locking.resource_key(service, environment) == locking.resource_key(service, environment)


def test_resource_key_is_scoped_per_environment() -> None:
    """The same service in two environments must not share one lock."""
    service = uuid.uuid4()
    assert locking.resource_key(service, uuid.uuid4()) != locking.resource_key(
        service, uuid.uuid4()
    )


def test_resource_key_is_scoped_per_service() -> None:
    environment = uuid.uuid4()
    assert locking.resource_key(uuid.uuid4(), environment) != locking.resource_key(
        uuid.uuid4(), environment
    )
