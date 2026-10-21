"""Signed GitHub webhook ingestion.

Security properties:

* every delivery must carry a valid ``X-Hub-Signature-256`` HMAC; an unset secret
  disables the endpoint rather than accepting unsigned requests
* the signature is verified over the **raw** body, before any parsing
* ``X-GitHub-Delivery`` is recorded with a unique constraint, so a redelivery is
  acknowledged but never processed twice
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response, status
from forge_core.db.models import Repository, WebhookDelivery
from forge_core.errors import AuthenticationError
from forge_core.logging import get_logger
from forge_core.security.webhooks import verify_signature
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from forge_api.deps import DbSession, enforce_rate_limit
from forge_api.routers.pipelines import create_pipeline_run

logger = get_logger("forge.api.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

#: Events Forge acts on. Anything else is recorded and acknowledged without action.
HANDLED_EVENTS = frozenset({"push", "ping", "pull_request"})


@router.post("/github", summary="GitHub webhook receiver")
async def github_webhook(
    request: Request,
    session: DbSession,
    response: Response,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str, Header()] = "",
    x_github_delivery: Annotated[str, Header()] = "",
) -> dict[str, Any]:
    settings = request.app.state.settings
    await enforce_rate_limit(
        request, "webhook", limit=settings.rate_limit_webhook_per_minute
    )

    raw = await request.body()
    secret = settings.github_webhook_secret.get_secret_value()
    try:
        # Verified against the exact bytes received, before parsing.
        verify_signature(raw, x_hub_signature_256, secret)
    except AuthenticationError:
        logger.warning(
            "webhook_signature_rejected", github_event=x_github_event,
            delivery=x_github_delivery[:64],
        )
        # Deliberately unspecific: a sender must not learn whether the secret is
        # unset or merely mismatched.
        raise

    delivery_id = x_github_delivery or str(uuid.uuid4())
    payload_digest = "sha256:" + hashlib.sha256(raw).hexdigest()

    existing = (
        await session.execute(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Idempotent acknowledgement: GitHub retries, and a retry must not rebuild.
        logger.info("webhook_duplicate_ignored", delivery=delivery_id)
        return {
            "status": "duplicate",
            "delivery_id": delivery_id,
            "pipeline_run_id": str(existing.pipeline_run_id or ""),
            "detail": "This delivery was already processed.",
        }

    record = WebhookDelivery(
        id=uuid.uuid4(), delivery_id=delivery_id, event=x_github_event,
        signature_valid=True, payload_digest=payload_digest,
        created_at=datetime.now(UTC),
    )
    session.add(record)
    try:
        await session.flush()
    except IntegrityError:
        # Lost a race with a concurrent redelivery; the other one owns it.
        await session.rollback()
        return {"status": "duplicate", "delivery_id": delivery_id,
                "detail": "Concurrent redelivery was already recorded."}

    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        record.processed = True
        record.result = "Payload was not valid JSON."
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": "rejected", "detail": "Payload was not valid JSON."}

    if x_github_event == "ping":
        record.processed = True
        record.result = "ping acknowledged"
        return {"status": "pong", "delivery_id": delivery_id}

    if x_github_event not in HANDLED_EVENTS:
        record.processed = True
        record.result = f"Event {x_github_event!r} is not handled."
        return {"status": "ignored", "event": x_github_event}

    clone_url = (body.get("repository") or {}).get("clone_url", "")
    html_url = (body.get("repository") or {}).get("html_url", "")
    repository = None
    for candidate in (clone_url, html_url):
        if not candidate:
            continue
        repository = (
            await session.execute(
                select(Repository).where(
                    Repository.url == candidate, Repository.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if repository is not None:
            break

    if repository is None:
        record.processed = True
        record.result = "No matching registered repository."
        return {"status": "ignored", "detail": "Repository is not registered with Forge."}

    record.repository_id = repository.id

    branch = ""
    if x_github_event == "push":
        ref = str(body.get("ref", ""))
        branch = ref.split("refs/heads/", 1)[-1] if ref.startswith("refs/heads/") else ""
        if not branch or branch != repository.default_branch:
            record.processed = True
            record.result = f"Push to {branch or ref!r} is not the default branch."
            return {"status": "ignored", "detail": record.result}
    elif x_github_event == "pull_request":
        action = body.get("action", "")
        if action not in ("opened", "synchronize", "reopened"):
            record.processed = True
            record.result = f"Pull request action {action!r} needs no build."
            return {"status": "ignored", "detail": record.result}
        branch = ((body.get("pull_request") or {}).get("head") or {}).get("ref", "")

    run, created = await create_pipeline_run(
        session, repository=repository, branch=branch or repository.default_branch,
        trigger=f"webhook:{x_github_event}", triggered_by_id=None,
        # The delivery id makes the trigger idempotent even if dedup is bypassed.
        idempotency_key=f"delivery:{delivery_id}",
    )
    record.processed = True
    record.pipeline_run_id = run.id
    record.result = "Pipeline triggered." if created else "Existing run reused."
    logger.info(
        "webhook_processed", delivery=delivery_id, github_event=x_github_event,
        repository=repository.slug, pipeline_id=str(run.id), created=created,
    )
    return {
        "status": "accepted",
        "delivery_id": delivery_id,
        "pipeline_run_id": str(run.id),
        "created": created,
    }
